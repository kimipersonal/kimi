"""Tiered Approval — risk-based approval routing for CEO actions.

Routes approvals through tiers based on action category and estimated cost:
  - LOW risk → auto-approve (CEO proceeds autonomously)
  - MEDIUM risk → CEO decides (logs decision with reasoning)
  - HIGH risk → requires explicit owner approval via HITL

Thresholds are configurable per category and persist in Redis.
"""

import json
import logging
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

_REDIS_KEY = "tiered_approval:config"

# Default thresholds per category — [low_max_usd, medium_max_usd]
# Actions below low_max → auto-approve
# Between low_max and medium_max → CEO decides
# Above medium_max → owner approval required
DEFAULT_THRESHOLDS: dict[str, list[float]] = {
    "trade": [50.0, 500.0],
    "company_creation": [0.0, 0.0],  # always owner approval
    "hiring": [10.0, 100.0],
    "firing": [0.0, 50.0],
    "budget": [20.0, 200.0],
    "general": [10.0, 100.0],
    "tool_execution": [5.0, 50.0],
    "data_access": [0.0, 25.0],
}


@dataclass
class TierDecision:
    category: str
    estimated_cost_usd: float
    tier: str  # "low", "medium", "high"
    action: str  # "auto_approve", "ceo_decide", "owner_approval"
    thresholds: list[float]
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )


class TieredApprovalService:
    """Manages risk-tier routing for approval requests."""

    def __init__(self) -> None:
        self._thresholds: dict[str, list[float]] = dict(DEFAULT_THRESHOLDS)
        self._decisions: list[dict] = []
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
                data = json.loads(raw)
                self._thresholds = data.get("thresholds", dict(DEFAULT_THRESHOLDS))
                self._decisions = data.get("decisions", [])[-500:]
                logger.info("Tiered approval config loaded from Redis")
        except Exception as e:
            logger.debug(f"Could not load tiered approval config: {e}")

    async def _persist(self) -> None:
        try:
            import redis.asyncio as aioredis
            from app.config import get_settings

            r = aioredis.from_url(get_settings().redis_url, decode_responses=True)
            data = {
                "thresholds": self._thresholds,
                "decisions": self._decisions[-500:],
            }
            await r.set(_REDIS_KEY, json.dumps(data), ex=86400 * 30)
            await r.aclose()
        except Exception as e:
            logger.debug(f"Could not persist tiered approval config: {e}")

    def evaluate(self, category: str, estimated_cost_usd: float = 0.0) -> TierDecision:
        """Determine which approval tier an action falls into."""
        thresholds = self._thresholds.get(
            category, self._thresholds.get("general", [10.0, 100.0])
        )
        low_max, medium_max = thresholds[0], thresholds[1]

        # When both thresholds are 0, always require owner approval
        if low_max == 0 and medium_max == 0:
            tier = "high"
            action = "owner_approval"
        elif estimated_cost_usd <= low_max:
            tier = "low"
            action = "auto_approve"
        elif estimated_cost_usd <= medium_max:
            tier = "medium"
            action = "ceo_decide"
        else:
            tier = "high"
            action = "owner_approval"

        decision = TierDecision(
            category=category,
            estimated_cost_usd=estimated_cost_usd,
            tier=tier,
            action=action,
            thresholds=thresholds,
        )
        self._decisions.append(asdict(decision))
        return decision

    async def set_thresholds(
        self, category: str, low_max: float, medium_max: float
    ) -> dict:
        """Update tier thresholds for a category."""
        if low_max < 0 or medium_max < 0:
            return {"error": "Thresholds must be non-negative"}
        if low_max > medium_max:
            return {"error": "low_max must be <= medium_max"}

        self._thresholds[category] = [low_max, medium_max]
        await self._persist()
        return {
            "category": category,
            "low_max_usd": low_max,
            "medium_max_usd": medium_max,
            "updated": True,
        }

    def get_thresholds(self, category: str | None = None) -> dict:
        """Get current thresholds — all or for a specific category."""
        if category:
            t = self._thresholds.get(category)
            if not t:
                return {"error": f"No thresholds for category '{category}'"}
            return {
                "category": category,
                "low_max_usd": t[0],
                "medium_max_usd": t[1],
            }
        return {
            cat: {"low_max_usd": t[0], "medium_max_usd": t[1]}
            for cat, t in self._thresholds.items()
        }

    def get_analytics(self, limit: int = 100) -> dict:
        """Get analytics on tier routing decisions."""
        recent = self._decisions[-limit:]
        tier_counts = {"low": 0, "medium": 0, "high": 0}
        category_counts: dict[str, int] = {}
        total_cost = 0.0

        for d in recent:
            tier_counts[d["tier"]] = tier_counts.get(d["tier"], 0) + 1
            cat = d["category"]
            category_counts[cat] = category_counts.get(cat, 0) + 1
            total_cost += d.get("estimated_cost_usd", 0)

        return {
            "total_decisions": len(recent),
            "tier_distribution": tier_counts,
            "category_distribution": category_counts,
            "total_estimated_cost_usd": round(total_cost, 2),
            "auto_approve_rate": (
                round(tier_counts["low"] / len(recent) * 100, 1) if recent else 0
            ),
        }


# Global singleton
tiered_approval_service = TieredApprovalService()
