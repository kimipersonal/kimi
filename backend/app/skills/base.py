"""Skill base class — defines the contract for all agent skills."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum


class SkillCategory(str, Enum):
    """Skill categories for marketplace organization."""
    RESEARCH = "research"
    DATA = "data"
    COMMUNICATION = "communication"
    DEVELOPMENT = "development"
    FINANCE = "finance"
    PRODUCTIVITY = "productivity"
    UTILITY = "utility"


@dataclass
class SkillMetadata:
    """Metadata for marketplace display and filtering."""
    author: str = "system"
    tags: list[str] = field(default_factory=list)
    requires_config: list[str] = field(default_factory=list)  # env vars needed
    icon: str = "⚡"  # emoji for UI display


class Skill(ABC):
    """Base class for all agent skills.

    A skill packages one or more related tools that an agent can use.
    Skills are self-contained: they define their own schemas, execution
    logic, and metadata for the marketplace.
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Unique skill identifier (e.g., 'web_search')."""
        ...

    @property
    @abstractmethod
    def display_name(self) -> str:
        """Human-readable name (e.g., 'Web Search')."""
        ...

    @property
    @abstractmethod
    def description(self) -> str:
        """Description of what this skill does."""
        ...

    @property
    @abstractmethod
    def version(self) -> str:
        """Skill version string."""
        ...

    @property
    @abstractmethod
    def category(self) -> SkillCategory:
        """Skill category for marketplace grouping."""
        ...

    @property
    def metadata(self) -> SkillMetadata:
        """Optional metadata for marketplace display."""
        return SkillMetadata()

    @property
    def enabled(self) -> bool:
        """Whether this skill is currently enabled."""
        return True

    @abstractmethod
    def get_tools_schema(self) -> list[dict]:
        """Return OpenAI-format tool schemas for all tools in this skill."""
        ...

    @abstractmethod
    async def execute(self, tool_name: str, arguments: dict) -> str:
        """Execute a tool by name. Returns JSON result string."""
        ...

    def get_tool_names(self) -> list[str]:
        """Get list of tool names provided by this skill."""
        return [t["function"]["name"] for t in self.get_tools_schema()]

    async def initialize(self):
        """Called once when skill is loaded. Override for setup logic."""
        pass

    async def shutdown(self):
        """Called on application shutdown. Override for cleanup."""
        pass

    def is_configured(self) -> bool:
        """Check whether required config/env vars are set."""
        import os
        for var in self.metadata.requires_config:
            if not os.environ.get(var):
                return False
        return True

    def to_dict(self) -> dict:
        """Serialize skill info for API responses."""
        return {
            "name": self.name,
            "display_name": self.display_name,
            "description": self.description,
            "version": self.version,
            "category": self.category.value,
            "enabled": self.enabled,
            "configured": self.is_configured(),
            "tools": self.get_tool_names(),
            "tool_count": len(self.get_tool_names()),
            "metadata": {
                "author": self.metadata.author,
                "tags": self.metadata.tags,
                "requires_config": self.metadata.requires_config,
                "icon": self.metadata.icon,
            },
        }
