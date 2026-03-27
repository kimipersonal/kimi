"""Prompt Optimizer — Analyze agent prompt effectiveness and suggest improvements.

Correlates agent system prompts with performance scores to identify
high-performing prompt patterns and recommend changes for underperformers.
"""

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

_REDIS_KEY = "prompt_optimizer:history"


@dataclass
class PromptSnapshot:
    """Snapshot of an agent's prompt and performance at a point in time."""
    agent_id: str
    agent_name: str
    system_prompt: str
    score: int  # 0-100
    grade: str
    success_rate: float
    total_tasks: int
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class PromptOptimizer:
    """Tracks prompt→performance correlation and generates improvement suggestions.

    Stores periodic snapshots of agent prompts + scores. When asked for recommendations,
    compares high-performing vs low-performing agents to identify effective patterns.
    """

    MAX_SNAPSHOTS_PER_AGENT = 50

    def __init__(self) -> None:
        self._snapshots: dict[str, list[dict]] = {}  # agent_id → list of snapshots
        self._suggestions: list[dict] = []

    async def capture_snapshot(self, agent_id: str) -> dict | None:
        """Capture current prompt + performance for an agent. Returns snapshot dict or None."""
        try:
            from app.db.database import async_session
            from app.db.models import Agent as AgentModel
            from app.services.performance_tracker import performance_tracker

            async with async_session() as session:
                agent = await session.get(AgentModel, agent_id)
                if not agent:
                    return None

            score_data = await performance_tracker.get_score(agent_id)
            if score_data["total_calls"] == 0:
                return None

            snapshot = PromptSnapshot(
                agent_id=agent_id,
                agent_name=agent.name,
                system_prompt=agent.system_prompt or "",
                score=score_data["score"],
                grade=score_data["grade"],
                success_rate=score_data["success_rate"],
                total_tasks=score_data["total_calls"],
            )
            snap_dict = {
                "agent_id": snapshot.agent_id,
                "agent_name": snapshot.agent_name,
                "system_prompt": snapshot.system_prompt[:2000],  # cap for storage
                "score": snapshot.score,
                "grade": snapshot.grade,
                "success_rate": snapshot.success_rate,
                "total_tasks": snapshot.total_tasks,
                "timestamp": snapshot.timestamp,
            }

            if agent_id not in self._snapshots:
                self._snapshots[agent_id] = []
            self._snapshots[agent_id].append(snap_dict)
            # Trim to max
            if len(self._snapshots[agent_id]) > self.MAX_SNAPSHOTS_PER_AGENT:
                self._snapshots[agent_id] = self._snapshots[agent_id][-self.MAX_SNAPSHOTS_PER_AGENT:]

            await self._persist()
            return snap_dict

        except Exception as e:
            logger.error(f"Failed to capture prompt snapshot for {agent_id}: {e}")
            return None

    async def capture_all_snapshots(self) -> list[dict]:
        """Capture snapshots for all agents. Returns list of captured snapshots."""
        try:
            from app.db.database import async_session
            from app.db.models import Agent as AgentModel
            from sqlalchemy import select

            async with async_session() as session:
                result = await session.execute(
                    select(AgentModel.id).where(AgentModel.id != "ceo")
                )
                agent_ids = [row[0] for row in result.fetchall()]

            captured = []
            for aid in agent_ids:
                snap = await self.capture_snapshot(aid)
                if snap:
                    captured.append(snap)
            return captured

        except Exception as e:
            logger.error(f"Failed to capture all snapshots: {e}")
            return []

    async def analyze(self) -> dict:
        """Analyze prompt effectiveness across all agents.

        Returns analysis with:
        - top_performers: agents with consistently high scores
        - underperformers: agents with low/declining scores
        - recommendations: specific suggestions per agent
        - common_patterns: patterns found in high-performing prompts
        """
        all_agents = {}
        for agent_id, snaps in self._snapshots.items():
            if not snaps:
                continue
            latest = snaps[-1]
            avg_score = sum(s["score"] for s in snaps) / len(snaps)
            trend = 0.0
            if len(snaps) >= 2:
                recent_half = snaps[len(snaps) // 2:]
                old_half = snaps[:len(snaps) // 2]
                recent_avg = sum(s["score"] for s in recent_half) / len(recent_half)
                old_avg = sum(s["score"] for s in old_half) / len(old_half)
                trend = recent_avg - old_avg

            all_agents[agent_id] = {
                "agent_id": agent_id,
                "agent_name": latest["agent_name"],
                "current_score": latest["score"],
                "current_grade": latest["grade"],
                "avg_score": round(avg_score, 1),
                "trend": round(trend, 1),  # positive = improving
                "snapshots": len(snaps),
                "success_rate": latest["success_rate"],
                "prompt_length": len(latest["system_prompt"]),
            }

        sorted_agents = sorted(all_agents.values(), key=lambda x: x["current_score"], reverse=True)
        top = [a for a in sorted_agents if a["current_score"] >= 80]
        under = [a for a in sorted_agents if a["current_score"] < 60]
        declining = [a for a in sorted_agents if a["trend"] < -10]

        recommendations = []
        for agent in under:
            rec = {
                "agent_id": agent["agent_id"],
                "agent_name": agent["agent_name"],
                "current_grade": agent["current_grade"],
                "issues": [],
                "suggestions": [],
            }
            if agent["success_rate"] < 50:
                rec["issues"].append(f"Low success rate: {agent['success_rate']}%")
                rec["suggestions"].append("Consider simplifying the agent's instructions or reducing scope")
            if agent["current_score"] < 40:
                rec["issues"].append(f"Very low score: {agent['current_score']}")
                rec["suggestions"].append("Consider restarting with upgraded model tier or revised prompt")
            if agent["prompt_length"] > 1500:
                rec["issues"].append(f"Long prompt ({agent['prompt_length']} chars)")
                rec["suggestions"].append("Consider shortening the prompt — focus on core instructions only")
            if agent["prompt_length"] < 100:
                rec["issues"].append(f"Very short prompt ({agent['prompt_length']} chars)")
                rec["suggestions"].append("Add more specific instructions about the agent's role and expected behaviors")
            if agent["trend"] < -10:
                rec["issues"].append(f"Declining performance (trend: {agent['trend']})")
                rec["suggestions"].append("Recent changes may have degraded performance — consider reverting")
            recommendations.append(rec)

        for agent in declining:
            if agent["agent_id"] not in [r["agent_id"] for r in recommendations]:
                recommendations.append({
                    "agent_id": agent["agent_id"],
                    "agent_name": agent["agent_name"],
                    "current_grade": agent["current_grade"],
                    "issues": [f"Performance declining (trend: {agent['trend']})"],
                    "suggestions": ["Investigate recent changes; monitor closely"],
                })

        # Common patterns in top performers
        patterns = []
        if top:
            avg_prompt_len = sum(a["prompt_length"] for a in top) / len(top)
            patterns.append(f"Top performers average {avg_prompt_len:.0f} chars in system prompt")
            avg_success = sum(a["success_rate"] for a in top) / len(top)
            patterns.append(f"Top performers average {avg_success:.0f}% success rate")

        return {
            "total_agents_tracked": len(all_agents),
            "top_performers": top,
            "underperformers": under,
            "declining": declining,
            "recommendations": recommendations,
            "common_patterns": patterns,
        }

    async def get_agent_history(self, agent_id: str) -> list[dict]:
        """Get prompt performance history for a specific agent."""
        return self._snapshots.get(agent_id, [])

    async def _persist(self) -> None:
        try:
            import redis.asyncio as aioredis
            from app.config import get_settings
            r = aioredis.from_url(get_settings().redis_url, decode_responses=True)
            await r.set(_REDIS_KEY, json.dumps(self._snapshots), ex=86400 * 30)
            await r.aclose()
        except Exception as e:
            logger.debug(f"Could not persist prompt optimizer: {e}")

    async def load_from_redis(self) -> None:
        try:
            import redis.asyncio as aioredis
            from app.config import get_settings
            r = aioredis.from_url(get_settings().redis_url, decode_responses=True)
            raw = await r.get(_REDIS_KEY)
            await r.aclose()
            if raw:
                self._snapshots = json.loads(raw)
                total = sum(len(v) for v in self._snapshots.values())
                logger.info(f"Loaded prompt optimizer: {len(self._snapshots)} agents, {total} snapshots")
        except Exception as e:
            logger.debug(f"Could not load prompt optimizer: {e}")


# Singleton
prompt_optimizer = PromptOptimizer()
