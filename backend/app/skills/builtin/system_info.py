"""System Info skill — query system status, agent info, and holding stats."""

import json
import logging

from app.skills.base import Skill, SkillCategory, SkillMetadata

logger = logging.getLogger(__name__)


class SystemInfoSkill(Skill):
    """Query system status, list agents, and get holding company information."""

    @property
    def name(self) -> str:
        return "system_info"

    @property
    def display_name(self) -> str:
        return "System Info"

    @property
    def description(self) -> str:
        return "Query system status, list registered agents, check plugin status, and get holding company stats."

    @property
    def version(self) -> str:
        return "1.0.0"

    @property
    def category(self) -> SkillCategory:
        return SkillCategory.UTILITY

    @property
    def metadata(self) -> SkillMetadata:
        return SkillMetadata(
            author="system",
            tags=["system", "agents", "info", "status"],
            icon="ℹ️",
        )

    def get_tools_schema(self) -> list[dict]:
        return [
            {
                "type": "function",
                "function": {
                    "name": "get_system_status",
                    "description": (
                        "Get current system status including agent count, plugin status, "
                        "registered skills, and company overview."
                    ),
                    "parameters": {"type": "object", "properties": {}},
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "list_agents",
                    "description": "List all registered agents with their status and roles.",
                    "parameters": {"type": "object", "properties": {}},
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "list_skills",
                    "description": "List all available skills and their tools.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "category": {
                                "type": "string",
                                "description": "Filter by category (research, data, utility, etc.)",
                            },
                        },
                    },
                },
            },
        ]

    async def execute(self, tool_name: str, arguments: dict) -> str:
        match tool_name:
            case "get_system_status":
                return await self._get_status()
            case "list_agents":
                return self._list_agents()
            case "list_skills":
                return self._list_skills(arguments.get("category"))
            case _:
                return json.dumps({"error": f"Unknown tool: {tool_name}"})

    async def _get_status(self) -> str:
        from app.agents.registry import registry
        from app.plugins.registry import plugin_registry
        from app.skills.registry import skill_registry

        return json.dumps({
            "agents": {
                "total": registry.count,
                "list": [
                    {"id": a.agent_id, "name": a.name, "status": a.status.value}
                    for a in registry.get_all()
                ],
            },
            "plugins": plugin_registry.get_status(),
            "skills": {
                "total": skill_registry.count,
                "enabled": len(skill_registry.get_enabled()),
            },
        }, default=str)

    def _list_agents(self) -> str:
        from app.agents.registry import registry

        agents = []
        for a in registry.get_all():
            agents.append({
                "id": a.agent_id,
                "name": a.name,
                "role": a.role,
                "status": a.status.value,
                "model_tier": a.model_tier,
            })
        return json.dumps({"agents": agents, "count": len(agents)})

    def _list_skills(self, category: str | None = None) -> str:
        from app.skills.registry import skill_registry

        if category:
            from app.skills.base import SkillCategory
            try:
                cat = SkillCategory(category)
                skills = skill_registry.get_by_category(cat)
            except ValueError:
                return json.dumps({"error": f"Unknown category: {category}"})
        else:
            skills = skill_registry.get_all()

        return json.dumps({
            "skills": [s.to_dict() for s in skills],
            "count": len(skills),
        })


def register(registry):
    """Register this skill with the skill registry."""
    registry.register(SystemInfoSkill())
