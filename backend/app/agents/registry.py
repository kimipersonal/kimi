"""Agent Registry — manages lifecycle of all agent instances."""

import logging

from app.agents.base import BaseAgent

logger = logging.getLogger(__name__)


class AgentRegistry:
    """Central registry for all active agent instances.

    Manages creation, destruction, and lookup of agents.
    Agent state is persisted in DB; this holds the live runtime instances.
    """

    def __init__(self):
        self._agents: dict[str, BaseAgent] = {}

    def register(self, agent: BaseAgent):
        """Register a live agent instance."""
        self._agents[agent.agent_id] = agent
        logger.info(f"Registered agent: {agent.name} ({agent.agent_id})")

    def unregister(self, agent_id: str):
        """Remove an agent instance from the registry."""
        agent = self._agents.pop(agent_id, None)
        if agent:
            logger.info(f"Unregistered agent: {agent.name} ({agent_id})")

    def get(self, agent_id: str) -> BaseAgent | None:
        """Get a live agent instance by ID."""
        return self._agents.get(agent_id)

    def get_all(self) -> list[BaseAgent]:
        """Get all registered agent instances."""
        return list(self._agents.values())

    def get_by_company(self, company_id: str) -> list[BaseAgent]:
        """Get all agents belonging to a specific company."""
        return [
            a for a in self._agents.values()
            if getattr(a, "company_id", None) == company_id
        ]

    async def stop_all(self):
        """Stop all agents gracefully."""
        for agent in self._agents.values():
            await agent.stop()
        logger.info("All agents stopped")

    @property
    def count(self) -> int:
        return len(self._agents)

    @property
    def active_count(self) -> int:
        from app.db.models import AgentStatus

        return sum(
            1
            for a in self._agents.values()
            if a.status not in (AgentStatus.STOPPED, AgentStatus.ERROR)
        )


# Global singleton
registry = AgentRegistry()
