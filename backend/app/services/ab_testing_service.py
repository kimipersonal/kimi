"""A/B Testing Service — Compare agent strategies side by side.

The CEO can create A/B tests where two agents with different configurations
(prompts, models, tools) compete on the same tasks. Results are tracked and
compared to pick the winner.
"""

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from uuid import uuid4

logger = logging.getLogger(__name__)

_REDIS_KEY = "ab_testing:experiments"


class ExperimentStatus:
    SETUP = "setup"
    RUNNING = "running"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


@dataclass
class Variant:
    """One side of an A/B test."""
    variant_id: str
    agent_id: str
    agent_name: str
    description: str
    tasks_completed: int = 0
    tasks_failed: int = 0
    total_cost_usd: float = 0.0
    total_tokens: int = 0
    total_time_s: float = 0.0
    scores: list[float] = field(default_factory=list)


class ABTestingService:
    """Manage A/B test experiments between agent variants.

    Flow:
    1. CEO creates experiment: defines hypothesis, assigns 2 agents as A and B.
    2. Tasks are assigned to both agents (same task, different agents).
    3. Results are recorded for each variant.
    4. CEO requests comparison to see which variant performs better.
    5. CEO decides winner (requires owner approval for permanent changes).
    """

    MAX_EXPERIMENTS = 50

    def __init__(self) -> None:
        self._experiments: dict[str, dict] = {}  # experiment_id → experiment

    async def create_experiment(
        self,
        name: str,
        hypothesis: str,
        agent_a_id: str,
        agent_a_desc: str,
        agent_b_id: str,
        agent_b_desc: str,
        max_tasks: int = 20,
    ) -> dict:
        """Create a new A/B test experiment.

        Args:
            name: Experiment name (e.g. "Flash vs Pro for analysis")
            hypothesis: What you're testing (e.g. "Pro model produces better trading signals")
            agent_a_id: Agent ID for variant A
            agent_a_desc: Description of what makes variant A different
            agent_b_id: Agent ID for variant B
            agent_b_desc: Description of what makes variant B different
            max_tasks: How many tasks each variant should complete before conclusion

        Returns:
            dict with experiment details
        """
        if len(self._experiments) >= self.MAX_EXPERIMENTS:
            # Remove oldest completed experiments
            completed = [
                (eid, exp) for eid, exp in self._experiments.items()
                if exp["status"] == ExperimentStatus.COMPLETED
            ]
            if completed:
                completed.sort(key=lambda x: x[1].get("created_at", ""))
                for eid, _ in completed[:10]:
                    del self._experiments[eid]

        # Look up agent names
        agent_a_name = await self._get_agent_name(agent_a_id)
        agent_b_name = await self._get_agent_name(agent_b_id)

        experiment_id = f"exp-{uuid4().hex[:8]}"
        experiment = {
            "experiment_id": experiment_id,
            "name": name,
            "hypothesis": hypothesis,
            "status": ExperimentStatus.RUNNING,
            "max_tasks": max_tasks,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "completed_at": None,
            "variant_a": {
                "variant_id": "A",
                "agent_id": agent_a_id,
                "agent_name": agent_a_name,
                "description": agent_a_desc,
                "tasks_completed": 0,
                "tasks_failed": 0,
                "total_cost_usd": 0.0,
                "total_tokens": 0,
                "total_time_s": 0.0,
                "scores": [],
            },
            "variant_b": {
                "variant_id": "B",
                "agent_id": agent_b_id,
                "agent_name": agent_b_name,
                "description": agent_b_desc,
                "tasks_completed": 0,
                "tasks_failed": 0,
                "total_cost_usd": 0.0,
                "total_tokens": 0,
                "total_time_s": 0.0,
                "scores": [],
            },
            "winner": None,
            "conclusion": None,
        }

        self._experiments[experiment_id] = experiment
        await self._persist()
        return experiment

    async def record_result(
        self,
        experiment_id: str,
        variant: str,  # "A" or "B"
        success: bool,
        score: float = 0.0,
        cost_usd: float = 0.0,
        tokens: int = 0,
        time_s: float = 0.0,
    ) -> dict:
        """Record a task result for a variant in an experiment.

        Args:
            experiment_id: The experiment ID
            variant: "A" or "B"
            success: Whether the task was completed successfully
            score: Quality score 0-100
            cost_usd: Cost of this task
            tokens: Tokens used
            time_s: Time taken in seconds

        Returns:
            Updated variant stats
        """
        exp = self._experiments.get(experiment_id)
        if not exp:
            return {"error": f"Experiment {experiment_id} not found"}
        if exp["status"] != ExperimentStatus.RUNNING:
            return {"error": f"Experiment is {exp['status']}, not running"}

        key = f"variant_{variant.lower()}"
        if key not in exp:
            return {"error": f"Invalid variant '{variant}'. Must be 'A' or 'B'."}

        v = exp[key]
        if success:
            v["tasks_completed"] += 1
        else:
            v["tasks_failed"] += 1
        v["total_cost_usd"] += cost_usd
        v["total_tokens"] += tokens
        v["total_time_s"] += time_s
        if score > 0:
            v["scores"].append(score)

        # Auto-complete if both variants hit max_tasks
        total_a = exp["variant_a"]["tasks_completed"] + exp["variant_a"]["tasks_failed"]
        total_b = exp["variant_b"]["tasks_completed"] + exp["variant_b"]["tasks_failed"]
        if total_a >= exp["max_tasks"] and total_b >= exp["max_tasks"]:
            exp["status"] = ExperimentStatus.COMPLETED
            exp["completed_at"] = datetime.now(timezone.utc).isoformat()
            self._determine_winner(exp)

        await self._persist()
        return v

    def _determine_winner(self, exp: dict) -> None:
        """Determine the winner based on composite scoring."""
        a = exp["variant_a"]
        b = exp["variant_b"]

        score_a = self._calculate_variant_score(a)
        score_b = self._calculate_variant_score(b)

        if score_a > score_b:
            exp["winner"] = "A"
            exp["conclusion"] = (
                f"Variant A ({a['agent_name']}: {a['description']}) wins with composite score "
                f"{score_a:.1f} vs {score_b:.1f}."
            )
        elif score_b > score_a:
            exp["winner"] = "B"
            exp["conclusion"] = (
                f"Variant B ({b['agent_name']}: {b['description']}) wins with composite score "
                f"{score_b:.1f} vs {score_a:.1f}."
            )
        else:
            exp["winner"] = "tie"
            exp["conclusion"] = f"Tie: both variants scored {score_a:.1f}."

    def _calculate_variant_score(self, v: dict) -> float:
        """Calculate composite score (0-100) for a variant.

        Weights: success_rate 50%, quality 30%, cost_efficiency 20%
        """
        total = v["tasks_completed"] + v["tasks_failed"]
        if total == 0:
            return 0.0

        success_rate = v["tasks_completed"] / total * 100
        avg_score = sum(v["scores"]) / len(v["scores"]) if v["scores"] else 50
        # Cost efficiency: lower cost = better (normalize: <$0.001/task = 100, >$0.10 = 0)
        avg_cost = v["total_cost_usd"] / total if total else 0
        cost_score = max(0, min(100, (0.10 - avg_cost) / 0.10 * 100))

        return success_rate * 0.5 + avg_score * 0.3 + cost_score * 0.2

    async def get_results(self, experiment_id: str) -> dict:
        """Get detailed results for an experiment."""
        exp = self._experiments.get(experiment_id)
        if not exp:
            return {"error": f"Experiment {experiment_id} not found"}

        a = exp["variant_a"]
        b = exp["variant_b"]

        total_a = a["tasks_completed"] + a["tasks_failed"]
        total_b = b["tasks_completed"] + b["tasks_failed"]

        return {
            "experiment_id": exp["experiment_id"],
            "name": exp["name"],
            "hypothesis": exp["hypothesis"],
            "status": exp["status"],
            "created_at": exp["created_at"],
            "completed_at": exp.get("completed_at"),
            "variant_a": {
                **a,
                "total_tasks": total_a,
                "success_rate": round(a["tasks_completed"] / total_a * 100, 1) if total_a else 0,
                "avg_score": round(sum(a["scores"]) / len(a["scores"]), 1) if a["scores"] else 0,
                "avg_cost": round(a["total_cost_usd"] / total_a, 6) if total_a else 0,
                "composite_score": round(self._calculate_variant_score(a), 1),
            },
            "variant_b": {
                **b,
                "total_tasks": total_b,
                "success_rate": round(b["tasks_completed"] / total_b * 100, 1) if total_b else 0,
                "avg_score": round(sum(b["scores"]) / len(b["scores"]), 1) if b["scores"] else 0,
                "avg_cost": round(b["total_cost_usd"] / total_b, 6) if total_b else 0,
                "composite_score": round(self._calculate_variant_score(b), 1),
            },
            "winner": exp.get("winner"),
            "conclusion": exp.get("conclusion"),
        }

    async def list_experiments(
        self,
        status: str | None = None,
        limit: int = 20,
    ) -> list[dict]:
        """List all experiments, optionally filtered by status."""
        experiments = list(self._experiments.values())
        if status:
            experiments = [e for e in experiments if e["status"] == status]
        experiments.sort(key=lambda x: x["created_at"], reverse=True)
        # Return summary without full score arrays
        result = []
        for exp in experiments[:limit]:
            total_a = exp["variant_a"]["tasks_completed"] + exp["variant_a"]["tasks_failed"]
            total_b = exp["variant_b"]["tasks_completed"] + exp["variant_b"]["tasks_failed"]
            result.append({
                "experiment_id": exp["experiment_id"],
                "name": exp["name"],
                "status": exp["status"],
                "created_at": exp["created_at"],
                "variant_a": f"{exp['variant_a']['agent_name']} ({total_a}/{exp['max_tasks']} tasks)",
                "variant_b": f"{exp['variant_b']['agent_name']} ({total_b}/{exp['max_tasks']} tasks)",
                "winner": exp.get("winner"),
            })
        return result

    async def cancel_experiment(self, experiment_id: str) -> dict:
        """Cancel a running experiment."""
        exp = self._experiments.get(experiment_id)
        if not exp:
            return {"error": f"Experiment {experiment_id} not found"}
        if exp["status"] != ExperimentStatus.RUNNING:
            return {"error": f"Experiment is {exp['status']}, cannot cancel"}
        exp["status"] = ExperimentStatus.CANCELLED
        exp["completed_at"] = datetime.now(timezone.utc).isoformat()
        await self._persist()
        return {"success": True, "experiment_id": experiment_id, "status": "cancelled"}

    async def _get_agent_name(self, agent_id: str) -> str:
        """Look up agent name from DB."""
        try:
            from app.db.database import async_session
            from app.db.models import Agent as AgentModel
            async with async_session() as session:
                agent = await session.get(AgentModel, agent_id)
                return agent.name if agent else agent_id
        except Exception:
            return agent_id

    async def _persist(self) -> None:
        try:
            import redis.asyncio as aioredis
            from app.config import get_settings
            r = aioredis.from_url(get_settings().redis_url, decode_responses=True)
            await r.set(_REDIS_KEY, json.dumps(self._experiments), ex=86400 * 30)
            await r.aclose()
        except Exception as e:
            logger.debug(f"Could not persist A/B experiments: {e}")

    async def load_from_redis(self) -> None:
        try:
            import redis.asyncio as aioredis
            from app.config import get_settings
            r = aioredis.from_url(get_settings().redis_url, decode_responses=True)
            raw = await r.get(_REDIS_KEY)
            await r.aclose()
            if raw:
                self._experiments = json.loads(raw)
                logger.info(f"Loaded A/B testing: {len(self._experiments)} experiments")
        except Exception as e:
            logger.debug(f"Could not load A/B experiments: {e}")


# Singleton
ab_testing_service = ABTestingService()
