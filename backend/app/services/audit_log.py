"""Audit Log — persistent log of all CEO actions and system events.

Stores entries in Redis with optional DB persistence. Each entry captures
who did what, when, and the result.
"""

from app.db.database import redis_pool
import json
import logging
import time
from collections import deque
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

_REDIS_KEY = "audit_log:entries"
_MAX_IN_MEMORY = 1000
_MAX_REDIS_ENTRIES = 5000


@dataclass
class AuditEntry:
    timestamp: str
    agent_id: str
    agent_name: str
    action: str
    arguments: dict
    result_summary: str
    success: bool
    source: str = "tool_call"  # tool_call, telegram, webhook, scheduler
    id: str = field(default_factory=lambda: f"audit-{int(time.time() * 1000)}")


class AuditLog:
    """Thread-safe audit log with Redis persistence."""

    def __init__(self) -> None:
        self._entries: deque[dict] = deque(maxlen=_MAX_IN_MEMORY)
        self._loaded = False

    async def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        try:
            raw = await redis_pool.get(_REDIS_KEY)
            if raw:
                entries = json.loads(raw)
                for e in entries[-_MAX_IN_MEMORY:]:
                    self._entries.append(e)
                logger.info(f"Loaded {len(self._entries)} audit entries from Redis")
        except Exception as e:
            logger.debug(f"Could not load audit log: {e}")

    async def log(
        self,
        agent_id: str,
        agent_name: str,
        action: str,
        arguments: dict,
        result_summary: str,
        success: bool,
        source: str = "tool_call",
    ) -> None:
        """Record an audit entry."""
        await self._ensure_loaded()
        entry = AuditEntry(
            timestamp=datetime.now(timezone.utc).isoformat(),
            agent_id=agent_id,
            agent_name=agent_name,
            action=action,
            arguments=arguments,
            result_summary=result_summary[:500],  # cap summary length
            success=success,
            source=source,
        )
        self._entries.append(asdict(entry))
        await self._persist()

        # Persist to PostgreSQL (permanent record)
        try:
            from app.db.database import async_session
            from app.db.models import AuditLogEntry

            async with async_session() as session:
                db_entry = AuditLogEntry(
                    agent_id=entry.agent_id,
                    agent_name=entry.agent_name,
                    action=entry.action,
                    arguments=entry.arguments,
                    result_summary=entry.result_summary,
                    success=entry.success,
                    source=entry.source,
                )
                session.add(db_entry)
                await session.commit()
        except Exception as e:
            logger.debug(f"Could not persist audit entry to DB: {e}")

    async def _persist(self) -> None:
        """Save entries to Redis."""
        try:
            data = list(self._entries)[-_MAX_REDIS_ENTRIES:]
            await redis_pool.set(_REDIS_KEY, json.dumps(data), ex=86400 * 30)  # 30 day TTL
        except Exception as e:
            logger.debug(f"Could not persist audit log: {e}")

    async def get_entries(
        self,
        agent_id: str | None = None,
        action: str | None = None,
        source: str | None = None,
        limit: int = 100,
    ) -> list[dict]:
        """Query audit entries with optional filters."""
        await self._ensure_loaded()
        entries = list(self._entries)
        if agent_id:
            entries = [e for e in entries if e["agent_id"] == agent_id]
        if action:
            entries = [e for e in entries if e["action"] == action]
        if source:
            entries = [e for e in entries if e["source"] == source]
        return entries[-limit:]

    async def get_stats(self) -> dict:
        """Return summary statistics."""
        await self._ensure_loaded()
        entries = list(self._entries)
        actions: dict[str, int] = {}
        successes = 0
        failures = 0
        for e in entries:
            actions[e["action"]] = actions.get(e["action"], 0) + 1
            if e["success"]:
                successes += 1
            else:
                failures += 1
        return {
            "total_entries": len(entries),
            "actions": actions,
            "successes": successes,
            "failures": failures,
        }


# Global singleton
audit_log = AuditLog()
