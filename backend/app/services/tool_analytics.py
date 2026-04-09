"""Tool Usage Analytics — track tool calls, success/fail rates, and timing per agent."""

import asyncio
import json
import logging
import time
from collections import defaultdict
from dataclasses import dataclass

from app.db.database import redis_pool

logger = logging.getLogger(__name__)

_REDIS_KEY = "tool_analytics:counters"


@dataclass
class ToolCallRecord:
    """Single tool call record."""
    tool_name: str
    agent_id: str
    agent_name: str
    success: bool
    duration_ms: float
    timestamp: float  # time.time()
    error: str | None = None


class ToolAnalytics:
    """In-memory analytics for tool usage across all agents.

    Thread-safe singleton — call ``tool_analytics.record()`` after each tool call,
    and ``tool_analytics.get_stats()`` for the dashboard.
    """

    MAX_RECORDS = 10_000  # ring buffer size

    def __init__(self) -> None:
        self._records: list[ToolCallRecord] = []
        # Pre-aggregated counters (faster than scanning records)
        self._tool_counts: dict[str, int] = defaultdict(int)
        self._tool_successes: dict[str, int] = defaultdict(int)
        self._tool_failures: dict[str, int] = defaultdict(int)
        self._tool_total_ms: dict[str, float] = defaultdict(float)
        self._agent_tool_counts: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))

    def record(
        self,
        tool_name: str,
        agent_id: str,
        agent_name: str,
        success: bool,
        duration_ms: float,
        error: str | None = None,
    ) -> None:
        """Record a tool call."""
        rec = ToolCallRecord(
            tool_name=tool_name,
            agent_id=agent_id,
            agent_name=agent_name,
            success=success,
            duration_ms=duration_ms,
            timestamp=time.time(),
            error=error,
        )

        # Ring buffer
        if len(self._records) >= self.MAX_RECORDS:
            self._records = self._records[-(self.MAX_RECORDS // 2):]

        self._records.append(rec)

        # Update counters
        self._tool_counts[tool_name] += 1
        if success:
            self._tool_successes[tool_name] += 1
        else:
            self._tool_failures[tool_name] += 1
        self._tool_total_ms[tool_name] += duration_ms
        self._agent_tool_counts[agent_id][tool_name] += 1

        # Periodically persist counters to Redis (every 50 calls)
        total = sum(self._tool_counts.values())
        if total % 50 == 0:
            try:
                asyncio.get_running_loop().create_task(self._save_to_redis())
            except RuntimeError:
                pass  # No event loop

    def get_stats(self) -> dict:
        """Get aggregated tool usage statistics."""
        tool_stats = {}
        for tool_name in self._tool_counts:
            total = self._tool_counts[tool_name]
            successes = self._tool_successes[tool_name]
            failures = self._tool_failures[tool_name]
            avg_ms = self._tool_total_ms[tool_name] / total if total else 0
            tool_stats[tool_name] = {
                "total_calls": total,
                "successes": successes,
                "failures": failures,
                "success_rate": round(successes / total * 100, 1) if total else 0,
                "avg_duration_ms": round(avg_ms, 1),
            }

        return {
            "total_calls": sum(self._tool_counts.values()),
            "unique_tools_used": len(self._tool_counts),
            "tools": tool_stats,
        }

    def get_agent_stats(self, agent_id: str) -> dict:
        """Get tool usage stats for a specific agent."""
        agent_tools = self._agent_tool_counts.get(agent_id, {})
        return {
            "agent_id": agent_id,
            "total_calls": sum(agent_tools.values()),
            "tools": dict(agent_tools),
        }

    def get_recent(self, limit: int = 50) -> list[dict]:
        """Get the most recent tool call records."""
        records = self._records[-limit:]
        return [
            {
                "tool_name": r.tool_name,
                "agent_id": r.agent_id,
                "agent_name": r.agent_name,
                "success": r.success,
                "duration_ms": round(r.duration_ms, 1),
                "timestamp": r.timestamp,
                "error": r.error,
            }
            for r in reversed(records)
        ]

    def reset(self) -> None:
        """Clear all analytics data."""
        self._records.clear()
        self._tool_counts.clear()
        self._tool_successes.clear()
        self._tool_failures.clear()
        self._tool_total_ms.clear()
        self._agent_tool_counts.clear()

    async def _save_to_redis(self) -> None:
        """Persist aggregated counters to Redis."""
        try:
            data = {
                "tool_counts": dict(self._tool_counts),
                "tool_successes": dict(self._tool_successes),
                "tool_failures": dict(self._tool_failures),
                "tool_total_ms": dict(self._tool_total_ms),
                "agent_tool_counts": {
                    aid: dict(tc) for aid, tc in self._agent_tool_counts.items()
                },
            }
            await redis_pool.set(_REDIS_KEY, json.dumps(data), ex=86400 * 30)  # 30d TTL
        except Exception as e:
            logger.debug(f"Could not save tool analytics to Redis: {e}")

    async def load_from_redis(self) -> None:
        """Load aggregated counters from Redis on startup."""
        try:
            raw = await redis_pool.get(_REDIS_KEY)
            if not raw:
                return
            data = json.loads(raw)
            for k, v in data.get("tool_counts", {}).items():
                self._tool_counts[k] = v
            for k, v in data.get("tool_successes", {}).items():
                self._tool_successes[k] = v
            for k, v in data.get("tool_failures", {}).items():
                self._tool_failures[k] = v
            for k, v in data.get("tool_total_ms", {}).items():
                self._tool_total_ms[k] = v
            for aid, tc in data.get("agent_tool_counts", {}).items():
                for k, v in tc.items():
                    self._agent_tool_counts[aid][k] = v
            logger.info(f"Loaded tool analytics from Redis ({sum(self._tool_counts.values())} total calls)")
        except Exception as e:
            logger.debug(f"Could not load tool analytics from Redis: {e}")


# Singleton
tool_analytics = ToolAnalytics()
