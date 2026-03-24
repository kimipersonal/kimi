"""Tool Usage Analytics — track tool calls, success/fail rates, and timing per agent."""

import logging
import time
from collections import defaultdict
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


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


# Singleton
tool_analytics = ToolAnalytics()
