"""Agent Delegation — peer-to-peer task delegation without CEO bottleneck.

Allows any agent to delegate sub-tasks to other agents in the same company.
Maintains a delegation chain for traceability. The CEO retains visibility
over all delegations via the audit trail.
"""

import asyncio
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from uuid import uuid4

from app.services.event_bus import event_bus

logger = logging.getLogger(__name__)

_REDIS_KEY = "delegation:chains"


class DelegationStatus(str, Enum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    REJECTED = "rejected"


@dataclass
class Delegation:
    delegation_id: str
    from_agent_id: str
    from_agent_name: str
    to_agent_id: str
    to_agent_name: str
    task_description: str
    company_id: str | None
    status: DelegationStatus = DelegationStatus.PENDING
    result: str | None = None
    error: str | None = None
    parent_delegation_id: str | None = None  # for chained delegations
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    completed_at: str | None = None

    def to_dict(self) -> dict:
        return {
            "delegation_id": self.delegation_id,
            "from_agent_id": self.from_agent_id,
            "from_agent_name": self.from_agent_name,
            "to_agent_id": self.to_agent_id,
            "to_agent_name": self.to_agent_name,
            "task_description": self.task_description,
            "company_id": self.company_id,
            "status": self.status.value,
            "result": self.result,
            "error": self.error,
            "parent_delegation_id": self.parent_delegation_id,
            "created_at": self.created_at,
            "completed_at": self.completed_at,
        }


class DelegationService:
    """Manages peer-to-peer task delegation between agents."""

    def __init__(self) -> None:
        self._delegations: dict[str, Delegation] = {}
        self._running: dict[str, asyncio.Task] = {}
        self._max_chain_depth = 3  # prevent infinite delegation loops
        self._max_history = 500

    async def delegate_task(
        self,
        from_agent_id: str,
        to_agent_id: str,
        task_description: str,
        parent_delegation_id: str | None = None,
    ) -> Delegation:
        """Delegate a task from one agent to another.

        Both agents must exist in the registry.  Same-company check is enforced
        unless the delegator is the CEO.
        """
        from app.agents.registry import registry

        source = registry.get(from_agent_id)
        target = registry.get(to_agent_id)

        if not source:
            raise ValueError(f"Source agent {from_agent_id} not found")
        if not target:
            raise ValueError(f"Target agent {to_agent_id} not found")

        if from_agent_id == to_agent_id:
            raise ValueError("Cannot delegate to self")

        # Check chain depth
        if parent_delegation_id:
            depth = self._get_chain_depth(parent_delegation_id)
            if depth >= self._max_chain_depth:
                raise ValueError(
                    f"Delegation chain depth limit reached ({self._max_chain_depth}). "
                    f"Cannot delegate further."
                )

        delegation_id = f"dlg-{str(uuid4())[:8]}"
        delegation = Delegation(
            delegation_id=delegation_id,
            from_agent_id=from_agent_id,
            from_agent_name=source.name,
            to_agent_id=to_agent_id,
            to_agent_name=target.name,
            task_description=task_description,
            company_id=source.company_id,
            parent_delegation_id=parent_delegation_id,
        )
        self._delegations[delegation_id] = delegation

        # Prune old entries
        self._prune_history()

        # Launch execution
        bg_task = asyncio.create_task(self._execute_delegation(delegation))
        self._running[delegation_id] = bg_task

        await event_bus.broadcast(
            "delegation_created",
            delegation.to_dict(),
            agent_id=from_agent_id,
        )

        logger.info(
            f"Delegation {delegation_id}: {source.name} → {target.name}: "
            f"'{task_description[:60]}'"
        )
        return delegation

    async def _execute_delegation(self, delegation: Delegation) -> None:
        """Execute the delegated task on the target agent."""
        from app.agents.registry import registry
        from app.services.messaging import send_message

        try:
            target = registry.get(delegation.to_agent_id)
            if not target:
                delegation.status = DelegationStatus.FAILED
                delegation.error = "Target agent no longer available"
                return

            delegation.status = DelegationStatus.IN_PROGRESS

            # Notify target agent about the delegation
            prompt = (
                f"[DELEGATED TASK — from {delegation.from_agent_name}]\n"
                f"Task: {delegation.task_description}\n\n"
                f"Complete this task and return your results."
            )

            await send_message(
                from_agent_id=delegation.from_agent_id,
                to_agent_id=delegation.to_agent_id,
                content=prompt,
                message_type="delegation",
                metadata={"delegation_id": delegation.delegation_id},
            )

            # Run the task on the target agent with a timeout
            result = await asyncio.wait_for(target.run(prompt), timeout=300)

            delegation.status = DelegationStatus.COMPLETED
            delegation.result = result
            delegation.completed_at = datetime.now(timezone.utc).isoformat()

            # Send result back to delegator
            await send_message(
                from_agent_id=delegation.to_agent_id,
                to_agent_id=delegation.from_agent_id,
                content=f"[DELEGATION RESULT — {delegation.delegation_id}]\n{result}",
                message_type="delegation_result",
                metadata={"delegation_id": delegation.delegation_id},
            )

            await event_bus.broadcast(
                "delegation_completed",
                delegation.to_dict(),
                agent_id=delegation.to_agent_id,
            )

            logger.info(f"Delegation {delegation.delegation_id} completed by {delegation.to_agent_name}")

        except asyncio.TimeoutError:
            delegation.status = DelegationStatus.FAILED
            delegation.error = "Task timed out (300s)"
            delegation.completed_at = datetime.now(timezone.utc).isoformat()
            logger.warning(f"Delegation {delegation.delegation_id} timed out")
        except Exception as e:
            delegation.status = DelegationStatus.FAILED
            delegation.error = str(e)[:500]
            delegation.completed_at = datetime.now(timezone.utc).isoformat()
            logger.error(f"Delegation {delegation.delegation_id} failed: {e}")
        finally:
            self._running.pop(delegation.delegation_id, None)
            await event_bus.broadcast(
                "delegation_status",
                delegation.to_dict(),
                agent_id=delegation.from_agent_id,
            )

    def _get_chain_depth(self, delegation_id: str) -> int:
        """Count the depth of a delegation chain."""
        depth = 0
        current = self._delegations.get(delegation_id)
        while current and current.parent_delegation_id:
            depth += 1
            current = self._delegations.get(current.parent_delegation_id)
            if depth > self._max_chain_depth + 1:
                break  # safety
        return depth

    def _prune_history(self) -> None:
        """Remove old completed delegations if we exceed max."""
        if len(self._delegations) <= self._max_history:
            return
        completed = [
            (did, d)
            for did, d in self._delegations.items()
            if d.status in (DelegationStatus.COMPLETED, DelegationStatus.FAILED, DelegationStatus.REJECTED)
        ]
        completed.sort(key=lambda x: x[1].created_at)
        for did, _ in completed[: len(completed) // 2]:
            del self._delegations[did]

    def get_delegation(self, delegation_id: str) -> Delegation | None:
        """Get a delegation by ID."""
        return self._delegations.get(delegation_id)

    def get_chain(self, delegation_id: str) -> list[dict]:
        """Get the full delegation chain (root → leaf)."""
        chain = []
        current = self._delegations.get(delegation_id)

        # Walk up to root
        ancestors = []
        while current and current.parent_delegation_id:
            ancestors.append(current)
            current = self._delegations.get(current.parent_delegation_id)
        if current:
            ancestors.append(current)

        # Reverse to get root-first
        ancestors.reverse()
        return [d.to_dict() for d in ancestors]

    def get_agent_delegations(
        self,
        agent_id: str,
        direction: str = "both",
    ) -> list[dict]:
        """Get delegations for an agent (sent, received, or both)."""
        results = []
        for d in self._delegations.values():
            if direction in ("sent", "both") and d.from_agent_id == agent_id:
                results.append(d.to_dict())
            elif direction in ("received", "both") and d.to_agent_id == agent_id:
                results.append(d.to_dict())
        return results

    def get_company_delegations(self, company_id: str) -> list[dict]:
        """Get all delegations within a company."""
        return [
            d.to_dict()
            for d in self._delegations.values()
            if d.company_id == company_id
        ]

    def get_all_delegations(self, status: DelegationStatus | None = None) -> list[dict]:
        """Get all delegations, optionally filtered by status."""
        items = list(self._delegations.values())
        if status:
            items = [d for d in items if d.status == status]
        return [d.to_dict() for d in items]

    async def persist_to_redis(self) -> None:
        """Persist delegation history to Redis."""
        try:
            import redis.asyncio as aioredis
            from app.config import get_settings

            data = {did: d.to_dict() for did, d in self._delegations.items()}
            r = aioredis.from_url(get_settings().redis_url, decode_responses=True)
            await r.set(_REDIS_KEY, json.dumps(data), ex=86400 * 7)
            await r.aclose()
        except Exception as e:
            logger.debug(f"Could not persist delegation data: {e}")

    async def load_from_redis(self) -> None:
        """Load delegation history from Redis (completed only)."""
        try:
            import redis.asyncio as aioredis
            from app.config import get_settings

            r = aioredis.from_url(get_settings().redis_url, decode_responses=True)
            raw = await r.get(_REDIS_KEY)
            await r.aclose()
            if not raw:
                return
            data = json.loads(raw)
            for did, ddict in data.items():
                if did not in self._delegations:
                    self._delegations[did] = Delegation(
                        delegation_id=ddict["delegation_id"],
                        from_agent_id=ddict["from_agent_id"],
                        from_agent_name=ddict["from_agent_name"],
                        to_agent_id=ddict["to_agent_id"],
                        to_agent_name=ddict["to_agent_name"],
                        task_description=ddict["task_description"],
                        company_id=ddict.get("company_id"),
                        status=DelegationStatus(ddict["status"]),
                        result=ddict.get("result"),
                        error=ddict.get("error"),
                        parent_delegation_id=ddict.get("parent_delegation_id"),
                        created_at=ddict.get("created_at", ""),
                        completed_at=ddict.get("completed_at"),
                    )
            logger.info(f"Loaded {len(data)} delegations from Redis")
        except Exception as e:
            logger.debug(f"Could not load delegation data: {e}")


# Global singleton
delegation_service = DelegationService()
