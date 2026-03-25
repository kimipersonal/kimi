"""Cost Tracker — Track token usage and costs per agent.

Records every LLM call with input/output tokens, model used, and agent ID.
Provides aggregated cost views for the dashboard and budget alert thresholds.
Persists data to Redis so nothing is lost on restart.
"""

import asyncio
import json
import logging
from collections import defaultdict
from dataclasses import dataclass, asdict
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

# Approximate costs per 1M tokens (input, output) by model pattern
# These are rough estimates for Vertex AI models
_COST_PER_M_TOKENS: dict[str, tuple[float, float]] = {
    "gemini-2.5-flash": (0.15, 0.60),
    "gemini-2.5-flash-lite": (0.075, 0.30),
    "gemini-2.5-pro": (1.25, 10.0),
    "deepseek": (0.30, 0.90),
    "kimi": (0.60, 2.00),
    "glm": (0.30, 0.90),
    "minimax": (0.30, 0.90),
    "qwen": (0.15, 0.60),
    "gpt-oss": (0.15, 0.60),
    "llama": (0.15, 0.60),
    "meta": (0.15, 0.60),
}

# Default cost if model not matched
_DEFAULT_COST = (0.30, 0.90)


def _estimate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    """Estimate cost in USD for a single LLM call."""
    model_lower = model.lower()
    cost_rates = _DEFAULT_COST
    for pattern, rates in _COST_PER_M_TOKENS.items():
        if pattern in model_lower:
            cost_rates = rates
            break
    input_cost = (input_tokens / 1_000_000) * cost_rates[0]
    output_cost = (output_tokens / 1_000_000) * cost_rates[1]
    return input_cost + output_cost


@dataclass
class LLMCallRecord:
    """Single LLM call record."""
    agent_id: str
    model: str
    input_tokens: int
    output_tokens: int
    cost_usd: float
    timestamp: datetime


@dataclass
class AgentCostSummary:
    """Aggregated cost data for one agent."""
    agent_id: str
    total_calls: int = 0
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_cost_usd: float = 0.0
    calls_today: int = 0
    cost_today_usd: float = 0.0


class CostTracker:
    """Tracks LLM token usage and costs per agent."""

    def __init__(self, daily_budget_usd: float = 5.0, alert_threshold: float = 0.8):
        self._records: list[LLMCallRecord] = []
        self._agent_totals: dict[str, AgentCostSummary] = defaultdict(
            lambda: AgentCostSummary(agent_id="unknown")
        )
        self._daily_budget = daily_budget_usd
        self._alert_threshold = alert_threshold  # Alert at 80% of budget
        self._budget_alert_sent = False
        self._lock = asyncio.Lock()
        self._on_budget_alert: list = []  # callbacks
        self._loaded = False

    def on_budget_alert(self, callback):
        """Register a callback for budget threshold alerts."""
        self._on_budget_alert.append(callback)

    _REDIS_KEY = "cost_tracker:records"
    _LIFETIME_COST_KEY = "cost_tracker:lifetime_cost"
    _LIFETIME_CALLS_KEY = "cost_tracker:lifetime_calls"
    _LIFETIME_TOKENS_KEY = "cost_tracker:lifetime_tokens"

    async def _increment_lifetime(self, cost: float, tokens: int) -> None:
        """Atomically increment lifetime counters in Redis (never reset)."""
        try:
            import redis.asyncio as aioredis
            from app.config import get_settings
            r = aioredis.from_url(get_settings().redis_url, decode_responses=True)
            pipe = r.pipeline()
            pipe.incrbyfloat(self._LIFETIME_COST_KEY, cost)
            pipe.incr(self._LIFETIME_CALLS_KEY)
            pipe.incrby(self._LIFETIME_TOKENS_KEY, tokens)
            await pipe.execute()
            await r.aclose()
        except Exception as e:
            logger.debug(f"Could not update lifetime counters: {e}")

    async def get_lifetime_stats(self) -> dict:
        """Get all-time lifetime cost/calls/tokens from Redis."""
        try:
            import redis.asyncio as aioredis
            from app.config import get_settings
            r = aioredis.from_url(get_settings().redis_url, decode_responses=True)
            pipe = r.pipeline()
            pipe.get(self._LIFETIME_COST_KEY)
            pipe.get(self._LIFETIME_CALLS_KEY)
            pipe.get(self._LIFETIME_TOKENS_KEY)
            results = await pipe.execute()
            await r.aclose()
            return {
                "lifetime_cost_usd": round(float(results[0] or 0), 6),
                "lifetime_calls": int(results[1] or 0),
                "lifetime_tokens": int(results[2] or 0),
            }
        except Exception as e:
            logger.debug(f"Could not read lifetime counters: {e}")
            return {"lifetime_cost_usd": 0, "lifetime_calls": 0, "lifetime_tokens": 0}

    async def _load_from_redis(self) -> None:
        """Load persisted cost records from Redis on first access."""
        if self._loaded:
            return
        self._loaded = True
        try:
            import redis.asyncio as aioredis
            from app.config import get_settings
            settings = get_settings()
            r = aioredis.from_url(settings.redis_url, decode_responses=True)
            raw = await r.get(self._REDIS_KEY)
            await r.aclose()
            if raw:
                data = json.loads(raw)
                for rec in data:
                    record = LLMCallRecord(
                        agent_id=rec["agent_id"],
                        model=rec["model"],
                        input_tokens=rec["input_tokens"],
                        output_tokens=rec["output_tokens"],
                        cost_usd=rec["cost_usd"],
                        timestamp=datetime.fromisoformat(rec["timestamp"]),
                    )
                    self._records.append(record)
                    # Rebuild agent totals
                    summary = self._agent_totals[record.agent_id]
                    summary.agent_id = record.agent_id
                    summary.total_calls += 1
                    summary.total_input_tokens += record.input_tokens
                    summary.total_output_tokens += record.output_tokens
                    summary.total_cost_usd += record.cost_usd
                    today = datetime.now(timezone.utc).date()
                    if self._is_today(record.timestamp, today):
                        summary.calls_today += 1
                        summary.cost_today_usd += record.cost_usd
                logger.info(f"Loaded {len(data)} cost records from Redis")
        except Exception as e:
            logger.debug(f"Could not load cost records from Redis: {e}")

    async def _save_to_redis(self) -> None:
        """Persist recent cost records to Redis (last 7 days)."""
        try:
            import redis.asyncio as aioredis
            from app.config import get_settings
            settings = get_settings()
            # Only keep last 7 days of records
            cutoff = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
            from datetime import timedelta
            cutoff = cutoff - timedelta(days=7)
            recent = [r for r in self._records if r.timestamp >= cutoff]
            self._records = recent
            data = [
                {
                    "agent_id": r.agent_id,
                    "model": r.model,
                    "input_tokens": r.input_tokens,
                    "output_tokens": r.output_tokens,
                    "cost_usd": r.cost_usd,
                    "timestamp": r.timestamp.isoformat(),
                }
                for r in recent
            ]
            r = aioredis.from_url(settings.redis_url, decode_responses=True)
            await r.set(self._REDIS_KEY, json.dumps(data), ex=86400 * 8)
            await r.aclose()
        except Exception as e:
            logger.debug(f"Could not save cost records to Redis: {e}")

    async def record(
        self,
        agent_id: str,
        model: str,
        input_tokens: int,
        output_tokens: int,
    ):
        """Record a single LLM call."""
        cost = _estimate_cost(model, input_tokens, output_tokens)
        now = datetime.now(timezone.utc)
        record = LLMCallRecord(
            agent_id=agent_id,
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=cost,
            timestamp=now,
        )

        async with self._lock:
            self._records.append(record)

            # Update agent summary
            summary = self._agent_totals[agent_id]
            summary.agent_id = agent_id
            summary.total_calls += 1
            summary.total_input_tokens += input_tokens
            summary.total_output_tokens += output_tokens
            summary.total_cost_usd += cost

            # Update today counts
            today = now.date()
            if self._is_today(record.timestamp, today):
                summary.calls_today += 1
                summary.cost_today_usd += cost

            # Check budget alert
            total_today = self.get_total_cost_today()
            if (
                not self._budget_alert_sent
                and total_today >= self._daily_budget * self._alert_threshold
            ):
                self._budget_alert_sent = True
                logger.warning(
                    f"Budget alert: ${total_today:.4f} / ${self._daily_budget:.2f} "
                    f"({total_today / self._daily_budget * 100:.0f}%)"
                )
                for cb in self._on_budget_alert:
                    try:
                        await cb(total_today, self._daily_budget)
                    except Exception as e:
                        logger.error(f"Budget alert callback error: {e}")

        # Persist to Redis + update lifetime counters
        await self._save_to_redis()
        await self._increment_lifetime(cost, input_tokens + output_tokens)

    @staticmethod
    def _is_today(ts: datetime, today) -> bool:
        return ts.date() == today

    def get_total_cost_today(self) -> float:
        today = datetime.now(timezone.utc).date()
        return sum(
            r.cost_usd for r in self._records if self._is_today(r.timestamp, today)
        )

    def get_agent_summary(self, agent_id: str) -> dict:
        summary = self._agent_totals.get(agent_id)
        if not summary:
            return {"agent_id": agent_id, "total_calls": 0, "total_cost_usd": 0}
        return {
            "agent_id": summary.agent_id,
            "total_calls": summary.total_calls,
            "total_input_tokens": summary.total_input_tokens,
            "total_output_tokens": summary.total_output_tokens,
            "total_cost_usd": round(summary.total_cost_usd, 6),
            "calls_today": summary.calls_today,
            "cost_today_usd": round(summary.cost_today_usd, 6),
        }

    def get_all_summaries(self) -> list[dict]:
        return [self.get_agent_summary(aid) for aid in self._agent_totals]

    def get_overview(self) -> dict:
        today = datetime.now(timezone.utc).date()
        total_cost = sum(r.cost_usd for r in self._records)
        cost_today = sum(
            r.cost_usd for r in self._records if self._is_today(r.timestamp, today)
        )
        calls_today = sum(
            1 for r in self._records if self._is_today(r.timestamp, today)
        )
        return {
            "total_cost_usd": round(total_cost, 6),
            "cost_today_usd": round(cost_today, 6),
            "daily_budget_usd": self._daily_budget,
            "budget_used_pct": round(cost_today / self._daily_budget * 100, 1)
            if self._daily_budget > 0
            else 0,
            "total_calls": len(self._records),
            "calls_today": calls_today,
            "agents": self.get_all_summaries(),
        }

    async def get_overview_with_lifetime(self) -> dict:
        """Get overview including lifetime all-time stats."""
        overview = self.get_overview()
        lifetime = await self.get_lifetime_stats()
        overview.update(lifetime)
        return overview

    def reset_daily(self):
        """Reset daily counters (call at midnight)."""
        self._budget_alert_sent = False
        for summary in self._agent_totals.values():
            summary.calls_today = 0
            summary.cost_today_usd = 0.0


# Global singleton
def _create_cost_tracker() -> CostTracker:
    try:
        from app.config import get_settings
        s = get_settings()
        return CostTracker(daily_budget_usd=s.daily_budget_usd, alert_threshold=s.budget_alert_threshold)
    except Exception:
        return CostTracker()


cost_tracker = _create_cost_tracker()
