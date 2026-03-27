"""Rollback Service — track and revert CEO autonomous actions.

Maintains a log of reversible actions with their inverse operations.
Supports auto-rollback on bad outcomes and manual rollback requests.
"""

import json
import logging
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum

logger = logging.getLogger(__name__)

_REDIS_KEY = "rollback:actions"


class RollbackStatus(str, Enum):
    AVAILABLE = "available"
    ROLLED_BACK = "rolled_back"
    EXPIRED = "expired"
    FAILED = "failed"


@dataclass
class RollbackAction:
    id: str
    original_action: str
    original_args: dict
    inverse_action: str
    inverse_args: dict
    agent_id: str
    description: str
    status: str = RollbackStatus.AVAILABLE.value
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    rolled_back_at: str | None = None
    rollback_result: str | None = None


# Map of actions to their inverse operations
INVERSE_ACTIONS: dict[str, str] = {
    "hire_agent": "fire_agent",
    "fire_agent": "rehire_agent",
    "create_company": "dissolve_company",
    "set_auto_trade_mode": "set_auto_trade_mode",
    "update_risk_limits": "update_risk_limits",
    "set_approval_thresholds": "set_approval_thresholds",
    "update_kpi_value": "update_kpi_value",
}


class RollbackService:
    """Tracks reversible actions and enables rollback."""

    def __init__(self) -> None:
        self._actions: list[dict] = []
        self._loaded = False

    async def load_from_redis(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        try:
            import redis.asyncio as aioredis
            from app.config import get_settings

            r = aioredis.from_url(get_settings().redis_url, decode_responses=True)
            raw = await r.get(_REDIS_KEY)
            await r.aclose()
            if raw:
                self._actions = json.loads(raw)[-500:]
                logger.info(f"Loaded {len(self._actions)} rollback actions from Redis")
        except Exception as e:
            logger.debug(f"Could not load rollback actions: {e}")

    async def _persist(self) -> None:
        try:
            import redis.asyncio as aioredis
            from app.config import get_settings

            r = aioredis.from_url(get_settings().redis_url, decode_responses=True)
            await r.set(
                _REDIS_KEY, json.dumps(self._actions[-500:]), ex=86400 * 30
            )
            await r.aclose()
        except Exception as e:
            logger.debug(f"Could not persist rollback actions: {e}")

    async def record_action(
        self,
        action: str,
        args: dict,
        agent_id: str,
        description: str,
    ) -> dict | None:
        """Record a reversible action. Returns the rollback entry or None if not reversible."""
        inverse = INVERSE_ACTIONS.get(action)
        if not inverse:
            return None

        inverse_args = self._build_inverse_args(action, args)

        entry = RollbackAction(
            id=f"rb-{int(time.time() * 1000)}",
            original_action=action,
            original_args=args,
            inverse_action=inverse,
            inverse_args=inverse_args,
            agent_id=agent_id,
            description=description,
        )
        self._actions.append(asdict(entry))
        await self._persist()
        return asdict(entry)

    def _build_inverse_args(self, action: str, args: dict) -> dict:
        """Build the arguments needed for the inverse action."""
        if action == "hire_agent":
            return {"agent_id": args.get("agent_id", ""), "reason": "Rollback of hire"}
        elif action == "fire_agent":
            return {
                "name": args.get("name", "unknown"),
                "role": args.get("role", "unknown"),
                "reason": "Rollback of firing",
            }
        elif action == "create_company":
            return {
                "company_id": args.get("company_id", ""),
                "reason": "Rollback of company creation",
            }
        elif action == "set_auto_trade_mode":
            # Toggle back
            return {"enabled": not args.get("enabled", False)}
        elif action == "update_risk_limits":
            return args.get("previous_limits", args)
        elif action == "set_approval_thresholds":
            return args.get("previous_thresholds", args)
        elif action == "update_kpi_value":
            return {
                "kpi_id": args.get("kpi_id", ""),
                "value": args.get("previous_value", 0),
            }
        return args

    async def execute_rollback(self, rollback_id: str) -> dict:
        """Execute a rollback. Returns the result."""
        entry = None
        for a in self._actions:
            if a["id"] == rollback_id:
                entry = a
                break

        if not entry:
            return {"error": f"Rollback action {rollback_id} not found"}

        if entry["status"] != RollbackStatus.AVAILABLE.value:
            return {
                "error": f"Action already in status: {entry['status']}",
                "id": rollback_id,
            }

        # Mark as rolled back
        entry["status"] = RollbackStatus.ROLLED_BACK.value
        entry["rolled_back_at"] = datetime.now(timezone.utc).isoformat()
        entry["rollback_result"] = (
            f"Rollback requested for {entry['original_action']}. "
            f"Inverse action: {entry['inverse_action']} with args: {entry['inverse_args']}"
        )
        await self._persist()

        return {
            "id": rollback_id,
            "status": "rolled_back",
            "original_action": entry["original_action"],
            "inverse_action": entry["inverse_action"],
            "inverse_args": entry["inverse_args"],
            "rolled_back_at": entry["rolled_back_at"],
        }

    def list_actions(
        self, status: str | None = None, limit: int = 50
    ) -> list[dict]:
        """List rollback-eligible actions."""
        actions = self._actions
        if status:
            actions = [a for a in actions if a.get("status") == status]
        return actions[-limit:]

    def get_history(self) -> dict:
        """Get rollback history summary."""
        total = len(self._actions)
        available = sum(
            1 for a in self._actions if a["status"] == RollbackStatus.AVAILABLE.value
        )
        rolled_back = sum(
            1 for a in self._actions if a["status"] == RollbackStatus.ROLLED_BACK.value
        )
        return {
            "total_tracked": total,
            "available": available,
            "rolled_back": rolled_back,
            "actions_by_type": self._count_by_field("original_action"),
        }

    def _count_by_field(self, field: str) -> dict[str, int]:
        counts: dict[str, int] = {}
        for a in self._actions:
            val = a.get(field, "unknown")
            counts[val] = counts.get(val, 0) + 1
        return counts


# Global singleton
rollback_service = RollbackService()
