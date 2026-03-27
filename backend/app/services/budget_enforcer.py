"""Budget Enforcer — per-agent and per-company daily budget limits.

When an agent exceeds its budget, it gets auto-paused. The CEO is notified.
Budget config is stored in Redis so it survives restarts.
"""

import asyncio
import json
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

_REDIS_KEY = "budget_enforcer:config"


class BudgetEnforcer:
    """Enforce daily spending limits per agent and per company."""

    def __init__(self, global_daily_budget: float = 5.0):
        self._global_daily_budget = global_daily_budget
        # {agent_id: max_daily_usd}
        self._agent_budgets: dict[str, float] = {}
        # {company_id: max_daily_usd}
        self._company_budgets: dict[str, float] = {}
        # Track which agents/companies have been paused today
        self._paused_agents: set[str] = set()
        self._paused_companies: set[str] = set()
        self._loaded = False
        self._callbacks: list = []  # (agent_id, reason) callbacks

    def on_budget_exceeded(self, callback):
        """Register callback for budget exceeded events."""
        self._callbacks.append(callback)

    async def _ensure_loaded(self):
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
                self._agent_budgets = data.get("agent_budgets", {})
                self._company_budgets = data.get("company_budgets", {})
                logger.info(
                    f"Loaded budget config: {len(self._agent_budgets)} agent, "
                    f"{len(self._company_budgets)} company budgets"
                )
        except Exception as e:
            logger.debug(f"Could not load budget config: {e}")

    async def _persist(self):
        try:
            import redis.asyncio as aioredis
            from app.config import get_settings

            r = aioredis.from_url(get_settings().redis_url, decode_responses=True)
            data = {
                "agent_budgets": self._agent_budgets,
                "company_budgets": self._company_budgets,
            }
            await r.set(_REDIS_KEY, json.dumps(data))
            await r.aclose()
        except Exception as e:
            logger.debug(f"Could not persist budget config: {e}")

    async def set_agent_budget(self, agent_id: str, daily_usd: float) -> None:
        """Set daily budget for a specific agent."""
        await self._ensure_loaded()
        self._agent_budgets[agent_id] = daily_usd
        await self._persist()
        logger.info(f"Agent {agent_id} daily budget set to ${daily_usd:.2f}")

    async def set_company_budget(self, company_id: str, daily_usd: float) -> None:
        """Set daily budget for a specific company."""
        await self._ensure_loaded()
        self._company_budgets[company_id] = daily_usd
        await self._persist()
        logger.info(f"Company {company_id} daily budget set to ${daily_usd:.2f}")

    async def remove_agent_budget(self, agent_id: str) -> bool:
        """Remove budget limit for an agent."""
        await self._ensure_loaded()
        removed = self._agent_budgets.pop(agent_id, None) is not None
        if removed:
            await self._persist()
        return removed

    async def remove_company_budget(self, company_id: str) -> bool:
        """Remove budget limit for a company."""
        await self._ensure_loaded()
        removed = self._company_budgets.pop(company_id, None) is not None
        if removed:
            await self._persist()
        return removed

    async def get_budgets(self) -> dict:
        """Return all configured budgets."""
        await self._ensure_loaded()
        return {
            "global_daily_budget_usd": self._global_daily_budget,
            "agent_budgets": dict(self._agent_budgets),
            "company_budgets": dict(self._company_budgets),
            "paused_agents_today": list(self._paused_agents),
            "paused_companies_today": list(self._paused_companies),
        }

    async def check_and_enforce(self, agent_id: str, company_id: str | None = None) -> dict:
        """Check if agent/company is within budget. Returns enforcement result.

        Call this AFTER recording a cost. If budget is exceeded, the agent gets paused.
        Returns: {"allowed": True/False, "reason": str, "action_taken": str}
        """
        await self._ensure_loaded()
        from app.services.cost_tracker import cost_tracker

        result = {"allowed": True, "reason": "", "action_taken": ""}

        # --- Check agent budget ---
        agent_budget = self._agent_budgets.get(agent_id)
        if agent_budget is not None and agent_id not in self._paused_agents:
            agent_summary = cost_tracker.get_agent_summary(agent_id)
            agent_cost_today = agent_summary.get("cost_today_usd", 0)
            if agent_cost_today >= agent_budget:
                result = {
                    "allowed": False,
                    "reason": f"Agent {agent_id} exceeded daily budget: "
                              f"${agent_cost_today:.4f} >= ${agent_budget:.2f}",
                    "action_taken": "agent_paused",
                }
                await self._pause_agent(agent_id, result["reason"])
                return result

        # --- Check company budget ---
        if company_id and company_id not in self._paused_companies:
            company_budget = self._company_budgets.get(company_id)
            if company_budget is not None:
                company_cost = self._get_company_cost_today(company_id)
                if company_cost >= company_budget:
                    result = {
                        "allowed": False,
                        "reason": f"Company {company_id} exceeded daily budget: "
                                  f"${company_cost:.4f} >= ${company_budget:.2f}",
                        "action_taken": "company_agents_paused",
                    }
                    await self._pause_company(company_id, result["reason"])
                    return result

        # --- Check global budget ---
        total_today = cost_tracker.get_total_cost_today()
        if total_today >= self._global_daily_budget:
            result = {
                "allowed": False,
                "reason": f"Global daily budget exceeded: "
                          f"${total_today:.4f} >= ${self._global_daily_budget:.2f}",
                "action_taken": "warning_only",
            }

        return result

    def _get_company_cost_today(self, company_id: str) -> float:
        """Sum today's costs for all agents in a company."""
        from app.services.cost_tracker import cost_tracker
        from app.agents.registry import registry

        total = 0.0
        for agent_id, agent in registry._agents.items():
            if getattr(agent, "company_id", None) == company_id:
                summary = cost_tracker.get_agent_summary(agent_id)
                total += summary.get("cost_today_usd", 0)
        return total

    async def _pause_agent(self, agent_id: str, reason: str):
        """Pause an agent due to budget exceeded."""
        self._paused_agents.add(agent_id)
        try:
            from app.agents.registry import registry

            agent = registry.get(agent_id)
            if agent:
                await agent.pause()
                logger.warning(f"Budget enforcer paused agent {agent_id}: {reason}")
        except Exception as e:
            logger.error(f"Failed to pause agent {agent_id}: {e}")

        # Notify via callbacks
        for cb in self._callbacks:
            try:
                await cb(agent_id, reason)
            except Exception as e:
                logger.error(f"Budget callback error: {e}")

    async def _pause_company(self, company_id: str, reason: str):
        """Pause all agents in a company due to budget exceeded."""
        self._paused_companies.add(company_id)
        from app.agents.registry import registry

        for agent_id, agent in registry._agents.items():
            if getattr(agent, "company_id", None) == company_id:
                try:
                    await agent.pause()
                    self._paused_agents.add(agent_id)
                    logger.warning(
                        f"Budget enforcer paused agent {agent_id} "
                        f"(company {company_id}): {reason}"
                    )
                except Exception as e:
                    logger.error(f"Failed to pause agent {agent_id}: {e}")

        for cb in self._callbacks:
            try:
                await cb(company_id, reason)
            except Exception as e:
                logger.error(f"Budget callback error: {e}")

    def reset_daily(self):
        """Reset daily enforcement state. Call at midnight UTC."""
        self._paused_agents.clear()
        self._paused_companies.clear()
        logger.info("Budget enforcer daily state reset")


# Global singleton
def _create_budget_enforcer() -> BudgetEnforcer:
    try:
        from app.config import get_settings
        return BudgetEnforcer(global_daily_budget=get_settings().daily_budget_usd)
    except Exception:
        return BudgetEnforcer()


budget_enforcer = _create_budget_enforcer()
