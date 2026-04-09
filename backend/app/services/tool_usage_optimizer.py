"""Tool Usage Optimizer — Analyze tool usage patterns and recommend optimizations.

Builds on ToolAnalytics data to identify:
- Unused or rarely used tools (candidates for removal)
- High-failure-rate tools (need fixing or better docs)
- Slow tools (optimization targets)
- Missing capabilities (based on failed tool calls and patterns)
"""

from app.db.database import redis_pool
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

_REDIS_KEY = "tool_optimizer:recommendations"


@dataclass
class ToolRecommendation:
    """A recommendation about a tool."""
    tool_name: str
    action: str  # "disable", "investigate", "optimize", "keep", "promote"
    reason: str
    priority: str  # "high", "medium", "low"
    metrics: dict


class ToolUsageOptimizer:
    """Analyzes tool usage data from ToolAnalytics and generates actionable recommendations.

    Works with existing tool_analytics singleton to derive insights without duplicating data.
    """

    # Thresholds for recommendations
    MIN_CALLS_FOR_ANALYSIS = 5  # need at least this many calls to judge
    FAILURE_RATE_HIGH = 30.0  # >30% failure rate = investigate
    FAILURE_RATE_CRITICAL = 50.0  # >50% failure rate = disable candidate
    SLOW_THRESHOLD_MS = 5000  # >5s average = slow
    UNUSED_THRESHOLD_CALLS = 2  # <2 calls in tracked history = unused

    def __init__(self) -> None:
        self._last_analysis: dict | None = None
        self._last_analysis_time: str | None = None

    async def analyze(self) -> dict:
        """Run full tool usage analysis. Returns categorized recommendations.

        Categories:
        - disable_candidates: tools with high failure rate or never used
        - investigate: tools with moderate issues
        - optimize: tools that work but are slow
        - top_tools: most valuable tools (high usage, high success)
        - agent_insights: per-agent tool usage patterns
        """
        from app.services.tool_analytics import tool_analytics

        stats = tool_analytics.get_stats()
        tool_stats = stats.get("tools", {})
        total_calls = stats.get("total_calls", 0)

        if total_calls == 0:
            return {
                "status": "insufficient_data",
                "message": "No tool usage data available yet. Tools need to be used before analysis.",
                "total_calls": 0,
            }

        disable_candidates = []
        investigate = []
        optimize = []
        top_tools = []
        healthy = []

        for tool_name, ts in tool_stats.items():
            total = ts["total_calls"]
            success_rate = ts["success_rate"]
            avg_ms = ts["avg_duration_ms"]

            rec_metrics = {
                "total_calls": total,
                "success_rate": success_rate,
                "failures": ts["failures"],
                "avg_duration_ms": avg_ms,
                "usage_share": round(total / total_calls * 100, 1) if total_calls else 0,
            }

            if total < self.MIN_CALLS_FOR_ANALYSIS:
                if total <= self.UNUSED_THRESHOLD_CALLS:
                    disable_candidates.append({
                        "tool_name": tool_name,
                        "action": "disable",
                        "reason": f"Rarely used ({total} calls). Consider removing to reduce LLM context size.",
                        "priority": "low",
                        "metrics": rec_metrics,
                    })
                continue

            if success_rate < (100 - self.FAILURE_RATE_CRITICAL):
                disable_candidates.append({
                    "tool_name": tool_name,
                    "action": "disable",
                    "reason": f"Critical failure rate: {100 - success_rate:.0f}%. Tool is unreliable.",
                    "priority": "high",
                    "metrics": rec_metrics,
                })
            elif success_rate < (100 - self.FAILURE_RATE_HIGH):
                investigate.append({
                    "tool_name": tool_name,
                    "action": "investigate",
                    "reason": f"High failure rate: {100 - success_rate:.0f}%. Needs attention.",
                    "priority": "medium",
                    "metrics": rec_metrics,
                })
            elif avg_ms > self.SLOW_THRESHOLD_MS:
                optimize.append({
                    "tool_name": tool_name,
                    "action": "optimize",
                    "reason": f"Slow average execution: {avg_ms:.0f}ms ({avg_ms / 1000:.1f}s).",
                    "priority": "medium",
                    "metrics": rec_metrics,
                })
            elif total >= 10 and success_rate >= 95:
                top_tools.append({
                    "tool_name": tool_name,
                    "action": "keep",
                    "reason": f"Top performer: {total} calls, {success_rate}% success, {avg_ms:.0f}ms avg.",
                    "priority": "low",
                    "metrics": rec_metrics,
                })
            else:
                healthy.append({
                    "tool_name": tool_name,
                    "action": "keep",
                    "reason": "Operating within normal parameters.",
                    "priority": "low",
                    "metrics": rec_metrics,
                })

        # Sort by priority/impact
        disable_candidates.sort(key=lambda x: x["metrics"]["total_calls"], reverse=True)
        top_tools.sort(key=lambda x: x["metrics"]["total_calls"], reverse=True)

        result = {
            "status": "analyzed",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "total_calls": total_calls,
            "unique_tools": stats.get("unique_tools_used", 0),
            "disable_candidates": disable_candidates,
            "investigate": investigate,
            "optimize": optimize,
            "top_tools": top_tools[:10],
            "healthy": len(healthy),
            "summary": self._build_summary(disable_candidates, investigate, optimize, top_tools, total_calls),
        }

        self._last_analysis = result
        self._last_analysis_time = result["timestamp"]
        await self._persist()
        return result

    async def get_agent_tool_profile(self, agent_id: str) -> dict:
        """Get tool usage profile for a specific agent.

        Shows which tools the agent uses most/least and identifies potential issues.
        """
        from app.services.tool_analytics import tool_analytics

        agent_stats = tool_analytics.get_agent_stats(agent_id)
        tools_used = agent_stats.get("tools", {})
        total = agent_stats.get("total_calls", 0)

        if total == 0:
            return {"agent_id": agent_id, "status": "no_data", "total_calls": 0}

        sorted_tools = sorted(tools_used.items(), key=lambda x: x[1], reverse=True)

        return {
            "agent_id": agent_id,
            "total_calls": total,
            "unique_tools": len(tools_used),
            "most_used": [{"tool": t, "calls": c} for t, c in sorted_tools[:10]],
            "least_used": [{"tool": t, "calls": c} for t, c in sorted_tools[-5:]] if len(sorted_tools) > 5 else [],
            "tool_diversity": round(len(tools_used) / max(total, 1) * 100, 1),
        }

    def _build_summary(self, disable, investigate, optimize, top, total_calls) -> str:
        parts = [f"Analyzed {total_calls} total tool calls."]
        if disable:
            parts.append(f"{len(disable)} tool(s) recommended for disabling/review.")
        if investigate:
            parts.append(f"{len(investigate)} tool(s) need investigation (high failure rate).")
        if optimize:
            parts.append(f"{len(optimize)} tool(s) could be optimized (slow execution).")
        if top:
            parts.append(f"Top tool: {top[0]['tool_name']} ({top[0]['metrics']['total_calls']} calls, "
                        f"{top[0]['metrics']['success_rate']}% success).")
        return " ".join(parts)

    async def _persist(self) -> None:
        if not self._last_analysis:
            return
        try:
            await redis_pool.set(_REDIS_KEY, json.dumps(self._last_analysis), ex=86400 * 7)
        except Exception as e:
            logger.debug(f"Could not persist tool optimizer: {e}")

    async def load_from_redis(self) -> None:
        try:
            raw = await redis_pool.get(_REDIS_KEY)
            if raw:
                self._last_analysis = json.loads(raw)
                self._last_analysis_time = self._last_analysis.get("timestamp")
                logger.info(f"Loaded tool optimizer last analysis from {self._last_analysis_time}")
        except Exception as e:
            logger.debug(f"Could not load tool optimizer: {e}")


# Singleton
tool_usage_optimizer = ToolUsageOptimizer()
