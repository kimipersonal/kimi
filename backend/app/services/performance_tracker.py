"""Performance Tracker — score agents based on task outcomes and efficiency.

Tracks per-agent: task completions, failures, avg response time, cost efficiency.
Persists to Redis.
"""

from app.db.database import redis_pool
import json
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

_REDIS_KEY = "performance:agents"


class PerformanceTracker:
    """Track and score agent performance."""

    def __init__(self) -> None:
        self._data: dict[str, dict] = {}
        self._loaded = False

    async def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        try:
            raw = await redis_pool.get(_REDIS_KEY)
            if raw:
                self._data = json.loads(raw)
                logger.info(f"Loaded performance data for {len(self._data)} agents")
        except Exception as e:
            logger.debug(f"Could not load performance data: {e}")

    async def _persist(self) -> None:
        try:
            await redis_pool.set(_REDIS_KEY, json.dumps(self._data), ex=86400 * 30)
        except Exception as e:
            logger.debug(f"Could not persist performance data: {e}")

    def _ensure_agent(self, agent_id: str) -> dict:
        if agent_id not in self._data:
            self._data[agent_id] = {
                "agent_id": agent_id,
                "tasks_completed": 0,
                "tasks_failed": 0,
                "total_response_time_s": 0.0,
                "total_cost_usd": 0.0,
                "total_tokens": 0,
                "total_calls": 0,
                "last_activity": None,
            }
        return self._data[agent_id]

    async def record_task(
        self,
        agent_id: str,
        success: bool,
        response_time_s: float,
        cost_usd: float = 0.0,
        tokens: int = 0,
    ) -> None:
        """Record a task completion/failure for an agent."""
        await self._ensure_loaded()
        d = self._ensure_agent(agent_id)
        if success:
            d["tasks_completed"] += 1
        else:
            d["tasks_failed"] += 1
        d["total_response_time_s"] += response_time_s
        d["total_cost_usd"] += cost_usd
        d["total_tokens"] += tokens
        d["total_calls"] += 1
        d["last_activity"] = datetime.now(timezone.utc).isoformat()
        await self._persist()

    async def get_score(self, agent_id: str) -> dict:
        """Calculate performance score for an agent.

        Score is 0-100 based on success rate, response time, and cost efficiency.
        """
        await self._ensure_loaded()
        d = self._data.get(agent_id)
        if not d or d["total_calls"] == 0:
            return {
                "agent_id": agent_id,
                "score": 0,
                "grade": "N/A",
                "tasks_completed": 0,
                "tasks_failed": 0,
                "success_rate": 0,
                "avg_response_time_s": 0,
                "total_cost_usd": 0,
            }

        total = d["tasks_completed"] + d["tasks_failed"]
        success_rate = d["tasks_completed"] / total if total > 0 else 0
        avg_time = d["total_response_time_s"] / d["total_calls"]

        # Score components (0-100 each)
        # Success rate: 50% weight
        success_score = success_rate * 100
        # Speed: 30% weight (sub-10s = 100, 60s+ = 0)
        speed_score = max(0, min(100, (60 - avg_time) / 60 * 100))
        # Experience: 20% weight (more tasks = better, cap at 50)
        experience_score = min(100, total * 2)

        score = int(success_score * 0.5 + speed_score * 0.3 + experience_score * 0.2)

        # Letter grade
        if score >= 90:
            grade = "A"
        elif score >= 80:
            grade = "B"
        elif score >= 70:
            grade = "C"
        elif score >= 60:
            grade = "D"
        else:
            grade = "F"

        return {
            "agent_id": agent_id,
            "score": score,
            "grade": grade,
            "success_rate": round(success_rate * 100, 1),
            "tasks_completed": d["tasks_completed"],
            "tasks_failed": d["tasks_failed"],
            "avg_response_time_s": round(avg_time, 2),
            "total_cost_usd": round(d["total_cost_usd"], 6),
            "total_tokens": d["total_tokens"],
            "total_calls": d["total_calls"],
            "last_activity": d.get("last_activity"),
        }

    async def get_all_scores(self) -> list[dict]:
        """Get performance scores for all tracked agents, sorted by score."""
        await self._ensure_loaded()
        scores = []
        for agent_id in self._data:
            scores.append(await self.get_score(agent_id))
        scores.sort(key=lambda x: x["score"], reverse=True)
        return scores

    async def get_leaderboard(self, limit: int = 10) -> list[dict]:
        """Get top agents by performance score."""
        all_scores = await self.get_all_scores()
        return all_scores[:limit]


# Global singleton
performance_tracker = PerformanceTracker()
