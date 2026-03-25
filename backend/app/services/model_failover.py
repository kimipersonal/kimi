"""Model Failover Service — Enhanced multi-model failover with cooldowns and health tracking.

Extends the existing circuit breaker with:
- Per-model cooldown tracking (avoid hammering failing models)
- Latency-weighted model selection
- Cross-provider support (ready for Anthropic/OpenAI/Groq when keys are added)
- Automatic tier rotation within same tier_hint
- Per-call timeout protection (prevents agents from hanging forever)
"""

import asyncio
import logging
import time
from dataclasses import dataclass, field

from app.services import llm_router
from app.services.circuit_breaker import circuit_registry, CircuitOpenError

logger = logging.getLogger(__name__)

# Cooldown period after a model failure (seconds)
MODEL_COOLDOWN_SECONDS = 120
# Max consecutive failures before extended cooldown
MAX_CONSECUTIVE_FAILURES = 3
EXTENDED_COOLDOWN_SECONDS = 600
# Timeout for a single LLM call (seconds) — prevents infinite hangs
LLM_CALL_TIMEOUT = 300  # 5 minutes max per call


@dataclass
class ModelHealth:
    """Track health metrics for a single model."""
    model_id: str
    consecutive_failures: int = 0
    total_failures: int = 0
    total_successes: int = 0
    last_failure_time: float = 0.0
    last_success_time: float = 0.0
    avg_latency_ms: float = 0.0
    _latency_samples: list[float] = field(default_factory=list)

    @property
    def is_cooled_down(self) -> bool:
        """Check if the model has waited long enough after failure."""
        if self.consecutive_failures == 0:
            return True
        cooldown = (
            EXTENDED_COOLDOWN_SECONDS
            if self.consecutive_failures >= MAX_CONSECUTIVE_FAILURES
            else MODEL_COOLDOWN_SECONDS
        )
        return (time.time() - self.last_failure_time) >= cooldown

    @property
    def success_rate(self) -> float:
        total = self.total_successes + self.total_failures
        return self.total_successes / total if total > 0 else 1.0

    def record_success(self, latency_ms: float):
        self.consecutive_failures = 0
        self.total_successes += 1
        self.last_success_time = time.time()
        self._latency_samples.append(latency_ms)
        if len(self._latency_samples) > 20:
            self._latency_samples = self._latency_samples[-20:]
        self.avg_latency_ms = sum(self._latency_samples) / len(self._latency_samples)

    def record_failure(self):
        self.consecutive_failures += 1
        self.total_failures += 1
        self.last_failure_time = time.time()


class ModelFailoverService:
    """Enhanced model failover with health tracking and tier-aware rotation."""

    def __init__(self):
        self._health: dict[str, ModelHealth] = {}

    def _get_health(self, model_id: str) -> ModelHealth:
        if model_id not in self._health:
            self._health[model_id] = ModelHealth(model_id=model_id)
        return self._health[model_id]

    def get_tier_models(self, tier: str) -> list[str]:
        """Get all available models for a given tier, ordered by health."""
        models = []
        for m in llm_router.AVAILABLE_MODELS:
            if m["tier_hint"] == tier:
                models.append(m["id"])

        # Sort by health: cooled-down first, then by success rate, then by latency
        def sort_key(model_id: str):
            h = self._get_health(model_id)
            cooled = 0 if h.is_cooled_down else 1
            # Primary model (currently assigned to tier) gets priority
            is_primary = 0 if model_id == llm_router.MODEL_TIERS.get(tier) else 1
            return (cooled, is_primary, -h.success_rate, h.avg_latency_ms)

        models.sort(key=sort_key)
        return models

    async def chat_with_failover(
        self,
        messages: list[dict],
        tier: str = "smart",
        temperature: float = 0.7,
        max_tokens: int = 4096,
        tools: list[dict] | None = None,
        model_override: str | None = None,
        agent_id: str | None = None,
    ) -> dict:
        """Enhanced chat with tier-aware failover across all models in the tier.

        Falls through: same-tier models → fallback tier models.
        """

        if model_override:
            # Direct model call, no failover
            return await self._try_model(
                model_override, messages, temperature, max_tokens, tools, agent_id
            )

        # Build ordered candidate list
        candidates = []

        # 1. Current tier models
        for model_id in self.get_tier_models(tier):
            h = self._get_health(model_id)
            if h.is_cooled_down:
                candidates.append((model_id, tier))

        # 2. Fallback tier models
        for fallback_tier in llm_router.FALLBACK_CHAIN.get(tier, []):
            for model_id in self.get_tier_models(fallback_tier):
                h = self._get_health(model_id)
                if h.is_cooled_down and (model_id, fallback_tier) not in candidates:
                    candidates.append((model_id, fallback_tier))

        if not candidates:
            # All models are in cooldown — try the primary model anyway
            primary = llm_router.MODEL_TIERS.get(tier, llm_router.MODEL_TIERS["smart"])
            candidates = [(primary, tier)]

        errors = []
        for model_id, attempt_tier in candidates:
            try:
                result = await self._try_model(
                    model_id, messages, temperature, max_tokens, tools, agent_id
                )
                if attempt_tier != tier:
                    logger.info(f"Failover success: {tier}→{attempt_tier} ({model_id})")
                return result
            except Exception as e:
                errors.append(f"{model_id}: {e}")
                continue

        raise RuntimeError(
            f"All models failed for tier '{tier}'. Errors: {'; '.join(errors)}"
        )

    async def _try_model(
        self,
        model_id: str,
        messages: list[dict],
        temperature: float,
        max_tokens: int,
        tools: list[dict] | None,
        agent_id: str | None,
    ) -> dict:
        """Try a single model call with health tracking."""
        from app.services.cost_tracker import cost_tracker

        health = self._get_health(model_id)
        cb = circuit_registry.get_or_create(model_id)
        kwargs = llm_router._build_kwargs(model_id, messages, temperature, max_tokens, tools)

        start = time.time()
        try:
            from litellm import acompletion
            response = await asyncio.wait_for(
                cb.call(acompletion, **kwargs),
                timeout=LLM_CALL_TIMEOUT,
            )
            latency_ms = (time.time() - start) * 1000
            health.record_success(latency_ms)

            result = llm_router._parse_response(response, model_id)
            usage = result.get("usage", {})
            await cost_tracker.record(
                agent_id=agent_id or "unknown",
                model=model_id,
                input_tokens=usage.get("input_tokens", 0),
                output_tokens=usage.get("output_tokens", 0),
            )
            return result
        except asyncio.TimeoutError:
            health.record_failure()
            logger.error(f"Model {model_id} timed out after {LLM_CALL_TIMEOUT}s")
            raise TimeoutError(f"Model {model_id} timed out after {LLM_CALL_TIMEOUT}s")
        except CircuitOpenError:
            health.record_failure()
            raise
        except Exception as e:
            health.record_failure()
            logger.warning(f"Model {model_id} failed (failures={health.consecutive_failures}): {e}")
            raise

    def get_health_report(self) -> list[dict]:
        """Get health status of all tracked models."""
        return [
            {
                "model_id": h.model_id,
                "success_rate": round(h.success_rate, 3),
                "consecutive_failures": h.consecutive_failures,
                "avg_latency_ms": round(h.avg_latency_ms, 1),
                "is_available": h.is_cooled_down,
                "total_calls": h.total_successes + h.total_failures,
            }
            for h in self._health.values()
        ]


# Singleton
failover_service = ModelFailoverService()
