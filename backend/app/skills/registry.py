"""Skill Registry — discovers, loads, and manages skills."""

import importlib
import logging
from pathlib import Path

from app.skills.base import Skill, SkillCategory

logger = logging.getLogger(__name__)


class SkillRegistry:
    """Central registry for all agent skills."""

    def __init__(self):
        self._skills: dict[str, Skill] = {}
        # Maps tool_name → skill_name for fast dispatch
        self._tool_index: dict[str, str] = {}

    def register(self, skill: Skill):
        """Register a skill and index its tools."""
        name = skill.name
        if name in self._skills:
            logger.warning(f"Skill '{name}' already registered, replacing")
            self._unindex_tools(name)

        self._skills[name] = skill

        # Index tool names → skill
        for tool_name in skill.get_tool_names():
            if tool_name in self._tool_index:
                logger.warning(
                    f"Tool '{tool_name}' already registered by '{self._tool_index[tool_name]}', "
                    f"overriding with '{name}'"
                )
            self._tool_index[tool_name] = name

        logger.info(
            f"Registered skill: {name} v{skill.version} "
            f"({len(skill.get_tool_names())} tools)"
        )

    def unregister(self, name: str):
        """Remove a skill by name."""
        if name not in self._skills:
            logger.warning(f"Skill '{name}' not found")
            return
        self._unindex_tools(name)
        del self._skills[name]
        logger.info(f"Unregistered skill: {name}")

    def _unindex_tools(self, skill_name: str):
        """Remove tool index entries for a skill."""
        to_remove = [k for k, v in self._tool_index.items() if v == skill_name]
        for k in to_remove:
            del self._tool_index[k]

    def get(self, name: str) -> Skill | None:
        """Get a skill by name."""
        return self._skills.get(name)

    def get_all(self) -> list[Skill]:
        """Get all registered skills."""
        return list(self._skills.values())

    def get_by_category(self, category: SkillCategory) -> list[Skill]:
        """Get skills filtered by category."""
        return [s for s in self._skills.values() if s.category == category]

    def get_enabled(self) -> list[Skill]:
        """Get only enabled skills."""
        return [s for s in self._skills.values() if s.enabled]

    def get_tool_schemas(self, skill_names: list[str] | None = None) -> list[dict]:
        """Get tool schemas from specified skills (or all enabled skills)."""
        schemas = []
        skills = (
            [self._skills[n] for n in skill_names if n in self._skills]
            if skill_names is not None
            else self.get_enabled()
        )
        for skill in skills:
            if skill.enabled:
                schemas.extend(skill.get_tools_schema())
        return schemas

    async def execute_tool(self, tool_name: str, arguments: dict) -> str | None:
        """Execute a tool by name, dispatching to the correct skill.

        Returns result string or None if no skill handles this tool.
        """
        skill_name = self._tool_index.get(tool_name)
        if not skill_name:
            return None

        skill = self._skills.get(skill_name)
        if not skill or not skill.enabled:
            return None

        return await skill.execute(tool_name, arguments)

    async def discover_and_load(self):
        """Discover and load skills from the builtin directory."""
        builtin_dir = Path(__file__).parent / "builtin"
        if not builtin_dir.exists():
            return

        for py_file in sorted(builtin_dir.glob("*.py")):
            if py_file.name.startswith("_"):
                continue

            module_name = py_file.stem
            try:
                module_path = f"app.skills.builtin.{module_name}"
                module = importlib.import_module(module_path)

                if hasattr(module, "register"):
                    module.register(self)
                    logger.info(f"Loaded skill from: {py_file.name}")
                else:
                    logger.warning(f"Skill {py_file.name} has no register() function")
            except Exception as e:
                logger.error(f"Failed to load skill {py_file.name}: {e}")

    async def initialize_all(self):
        """Initialize all registered skills."""
        for skill in self._skills.values():
            try:
                await skill.initialize()
            except Exception as e:
                logger.error(f"Failed to initialize skill '{skill.name}': {e}")

    async def shutdown_all(self):
        """Shutdown all registered skills."""
        for skill in self._skills.values():
            try:
                await skill.shutdown()
            except Exception as e:
                logger.error(f"Error shutting down skill '{skill.name}': {e}")

    async def reload_skill(self, skill_name: str) -> dict:
        """Hot-reload a skill by re-importing its module.

        Returns {"success": bool, "message": str}.
        """
        # Unregister existing
        if skill_name in self._skills:
            old_skill = self._skills[skill_name]
            try:
                await old_skill.shutdown()
            except Exception:
                pass
            self.unregister(skill_name)

        # Find the module — check builtin directory
        builtin_dir = Path(__file__).parent / "builtin"
        candidates = list(builtin_dir.glob("*.py"))
        reloaded = False
        for py_file in candidates:
            if py_file.name.startswith("_"):
                continue
            module_path = f"app.skills.builtin.{py_file.stem}"
            try:
                module = importlib.import_module(module_path)
                module = importlib.reload(module)
                if hasattr(module, "register"):
                    module.register(self)
                    if skill_name in self._skills:
                        await self._skills[skill_name].initialize()
                        reloaded = True
                        break
            except Exception as e:
                logger.error(f"Error reloading {py_file.name}: {e}")

        # Also check plugins directory
        if not reloaded:
            plugin_dir = Path(__file__).parent.parent / "plugins"
            if plugin_dir.exists():
                for py_file in plugin_dir.glob("*.py"):
                    if py_file.name.startswith("_"):
                        continue
                    module_path = f"app.plugins.{py_file.stem}"
                    try:
                        module = importlib.import_module(module_path)
                        module = importlib.reload(module)
                        if hasattr(module, "register"):
                            module.register(self)
                            if skill_name in self._skills:
                                await self._skills[skill_name].initialize()
                                reloaded = True
                                break
                    except Exception as e:
                        logger.error(f"Error reloading plugin {py_file.name}: {e}")

        if reloaded:
            logger.info(f"Hot-reloaded skill: {skill_name}")
            return {"success": True, "message": f"Skill '{skill_name}' reloaded successfully."}
        return {"success": False, "message": f"Skill '{skill_name}' not found for reload."}

    def get_status(self) -> dict:
        """Get status of all skills for dashboard/API."""
        return {
            "total": len(self._skills),
            "enabled": len(self.get_enabled()),
            "categories": list({s.category.value for s in self._skills.values()}),
            "skills": [s.to_dict() for s in self._skills.values()],
        }

    @property
    def count(self) -> int:
        return len(self._skills)


# Singleton
skill_registry = SkillRegistry()
