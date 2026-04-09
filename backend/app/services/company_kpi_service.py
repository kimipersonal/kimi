"""Company KPIs — measurable key performance indicators per company.

Tracks company-level metrics: revenue/profit, task success rate, cost efficiency,
agent utilization, and custom KPIs.  The CEO reviews KPIs to make data-driven
decisions about resource allocation.
"""

from app.db.database import redis_pool
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

_REDIS_KEY = "kpis:companies"


@dataclass
class KPITarget:
    """A single KPI target for a company."""
    name: str
    metric: str  # what to measure (e.g., "profit_usd", "task_success_rate")
    target_value: float
    current_value: float = 0.0
    unit: str = ""  # e.g., "USD", "%", "count"
    direction: str = "higher_is_better"  # or "lower_is_better"
    updated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    @property
    def achievement_pct(self) -> float:
        if self.target_value == 0:
            return 100.0 if self.current_value >= 0 else 0.0
        pct = (self.current_value / self.target_value) * 100
        return round(min(pct, 999.9), 1)

    @property
    def on_track(self) -> bool:
        if self.direction == "higher_is_better":
            return self.current_value >= self.target_value * 0.8  # 80% threshold
        else:
            return self.current_value <= self.target_value * 1.2

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "metric": self.metric,
            "target_value": self.target_value,
            "current_value": self.current_value,
            "unit": self.unit,
            "direction": self.direction,
            "achievement_pct": self.achievement_pct,
            "on_track": self.on_track,
            "updated_at": self.updated_at,
        }


@dataclass
class CompanyKPIs:
    """KPI collection for a single company."""
    company_id: str
    company_name: str
    kpis: dict[str, KPITarget] = field(default_factory=dict)
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> dict:
        on_track_count = sum(1 for k in self.kpis.values() if k.on_track)
        total = len(self.kpis)
        return {
            "company_id": self.company_id,
            "company_name": self.company_name,
            "kpis": {name: kpi.to_dict() for name, kpi in self.kpis.items()},
            "summary": {
                "total_kpis": total,
                "on_track": on_track_count,
                "off_track": total - on_track_count,
                "health_pct": round((on_track_count / total * 100) if total else 0, 1),
            },
            "created_at": self.created_at,
        }


class CompanyKPIService:
    """Manages KPI targets and tracking for all companies."""

    def __init__(self) -> None:
        self._companies: dict[str, CompanyKPIs] = {}

    async def set_kpi(
        self,
        company_id: str,
        company_name: str,
        kpi_name: str,
        metric: str,
        target_value: float,
        unit: str = "",
        direction: str = "higher_is_better",
    ) -> KPITarget:
        """Set or update a KPI target for a company."""
        if company_id not in self._companies:
            self._companies[company_id] = CompanyKPIs(
                company_id=company_id,
                company_name=company_name,
            )

        kpi = KPITarget(
            name=kpi_name,
            metric=metric,
            target_value=target_value,
            unit=unit,
            direction=direction,
        )
        self._companies[company_id].kpis[kpi_name] = kpi
        await self._persist()

        logger.info(f"KPI set: {company_name}/{kpi_name} = target {target_value}{unit}")
        return kpi

    async def update_kpi_value(
        self,
        company_id: str,
        kpi_name: str,
        current_value: float,
    ) -> KPITarget | None:
        """Update the current value of a KPI."""
        company = self._companies.get(company_id)
        if not company:
            return None
        kpi = company.kpis.get(kpi_name)
        if not kpi:
            return None

        kpi.current_value = current_value
        kpi.updated_at = datetime.now(timezone.utc).isoformat()
        await self._persist()
        return kpi

    async def remove_kpi(self, company_id: str, kpi_name: str) -> bool:
        """Remove a KPI from a company."""
        company = self._companies.get(company_id)
        if not company or kpi_name not in company.kpis:
            return False
        del company.kpis[kpi_name]
        await self._persist()
        return True

    def get_company_kpis(self, company_id: str) -> dict | None:
        """Get all KPIs for a company."""
        company = self._companies.get(company_id)
        return company.to_dict() if company else None

    def get_all_kpis(self) -> list[dict]:
        """Get KPIs for all companies."""
        return [c.to_dict() for c in self._companies.values()]

    async def auto_update_from_metrics(self, company_id: str) -> dict:
        """Auto-compute KPI values from real metrics (costs, performance, tasks).

        Maps standard metric names to actual data sources.
        """
        company = self._companies.get(company_id)
        if not company:
            return {"updated": 0}

        updated = 0
        for kpi_name, kpi in company.kpis.items():
            value = await self._fetch_metric(company_id, kpi.metric)
            if value is not None:
                kpi.current_value = value
                kpi.updated_at = datetime.now(timezone.utc).isoformat()
                updated += 1

        if updated:
            await self._persist()

        return {"company_id": company_id, "updated": updated, "total": len(company.kpis)}

    async def _fetch_metric(self, company_id: str, metric: str) -> float | None:
        """Fetch a real metric value from the system."""
        try:
            if metric == "daily_cost_usd":
                from app.services.cost_tracker import cost_tracker
                summary = await cost_tracker.get_daily_summary()
                # Sum costs for agents in this company
                from app.agents.registry import registry
                total = 0.0
                for agent in registry.get_all():
                    if agent.company_id == company_id:
                        agent_costs = summary.get("per_agent", {}).get(agent.agent_id, {})
                        total += agent_costs.get("cost_usd", 0.0)
                return round(total, 4)

            elif metric == "task_success_rate":
                from app.services.performance_tracker import performance_tracker
                from app.agents.registry import registry
                scores = []
                for agent in registry.get_all():
                    if agent.company_id == company_id:
                        perf = await performance_tracker.get_score(agent.agent_id)
                        if perf.get("success_rate", 0) > 0:
                            scores.append(perf["success_rate"])
                return round(sum(scores) / len(scores), 1) if scores else 0.0

            elif metric == "agent_count":
                from app.agents.registry import registry
                return float(sum(
                    1 for a in registry.get_all() if a.company_id == company_id
                ))

            elif metric == "avg_response_time_s":
                from app.services.performance_tracker import performance_tracker
                from app.agents.registry import registry
                times = []
                for agent in registry.get_all():
                    if agent.company_id == company_id:
                        perf = await performance_tracker.get_score(agent.agent_id)
                        avg_time = perf.get("avg_response_time_s", 0)
                        if avg_time > 0:
                            times.append(avg_time)
                return round(sum(times) / len(times), 2) if times else 0.0

        except Exception as e:
            logger.debug(f"Could not fetch metric {metric} for {company_id}: {e}")
        return None

    async def _persist(self) -> None:
        """Persist KPI data to Redis."""
        try:

            data = {cid: c.to_dict() for cid, c in self._companies.items()}
            await redis_pool.set(_REDIS_KEY, json.dumps(data), ex=86400 * 30)
        except Exception as e:
            logger.debug(f"Could not persist KPI data: {e}")

    async def load_from_redis(self) -> None:
        """Load KPI data from Redis."""
        try:

            raw = await redis_pool.get(_REDIS_KEY)
            if not raw:
                return
            data = json.loads(raw)
            for cid, cdict in data.items():
                if cid not in self._companies:
                    company = CompanyKPIs(
                        company_id=cid,
                        company_name=cdict.get("company_name", ""),
                        created_at=cdict.get("created_at", ""),
                    )
                    for kpi_name, kpi_dict in cdict.get("kpis", {}).items():
                        company.kpis[kpi_name] = KPITarget(
                            name=kpi_dict["name"],
                            metric=kpi_dict["metric"],
                            target_value=kpi_dict["target_value"],
                            current_value=kpi_dict.get("current_value", 0.0),
                            unit=kpi_dict.get("unit", ""),
                            direction=kpi_dict.get("direction", "higher_is_better"),
                            updated_at=kpi_dict.get("updated_at", ""),
                        )
                    self._companies[cid] = company
            logger.info(f"Loaded KPIs for {len(data)} companies from Redis")
        except Exception as e:
            logger.debug(f"Could not load KPI data: {e}")


# Global singleton
company_kpi_service = CompanyKPIService()
