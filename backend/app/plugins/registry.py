"""Plugin Registry — discovers, loads, and manages plugins."""

import importlib
import logging
from pathlib import Path

from app.plugins.base import PluginBase, ProviderPlugin, ChannelPlugin, ToolPlugin

logger = logging.getLogger(__name__)


class PluginRegistry:
    """Central registry for all plugins."""

    def __init__(self):
        self._providers: dict[str, ProviderPlugin] = {}
        self._channels: dict[str, ChannelPlugin] = {}
        self._tools: dict[str, ToolPlugin] = {}

    def register(self, plugin: PluginBase):
        """Register a plugin by its type."""
        name = plugin.name

        if isinstance(plugin, ProviderPlugin):
            self._providers[name] = plugin
            logger.info(f"Registered provider plugin: {name} v{plugin.version}")
        elif isinstance(plugin, ChannelPlugin):
            self._channels[name] = plugin
            logger.info(f"Registered channel plugin: {name} v{plugin.version}")
        elif isinstance(plugin, ToolPlugin):
            self._tools[name] = plugin
            logger.info(f"Registered tool plugin: {name} v{plugin.version}")
        else:
            logger.warning(f"Unknown plugin type for '{name}': {type(plugin)}")

    def unregister(self, name: str):
        """Remove a plugin by name."""
        for registry in (self._providers, self._channels, self._tools):
            if name in registry:
                del registry[name]
                logger.info(f"Unregistered plugin: {name}")
                return
        logger.warning(f"Plugin '{name}' not found in any registry")

    # --- Getters ---

    def get_provider(self, name: str) -> ProviderPlugin | None:
        return self._providers.get(name)

    def get_channel(self, name: str) -> ChannelPlugin | None:
        return self._channels.get(name)

    def get_tool(self, name: str) -> ToolPlugin | None:
        return self._tools.get(name)

    @property
    def providers(self) -> dict[str, ProviderPlugin]:
        return dict(self._providers)

    @property
    def channels(self) -> dict[str, ChannelPlugin]:
        return dict(self._channels)

    @property
    def tools(self) -> dict[str, ToolPlugin]:
        return dict(self._tools)

    def get_all_models(self) -> list[dict]:
        """Get models from all registered providers."""
        models = []
        for provider in self._providers.values():
            if provider.enabled:
                for model in provider.get_models():
                    model["provider"] = provider.name
                    models.append(model)
        return models

    def get_all_tool_schemas(self) -> list[dict]:
        """Get tool schemas from all registered tool plugins."""
        schemas = []
        for tool_plugin in self._tools.values():
            if tool_plugin.enabled:
                schemas.extend(tool_plugin.get_tools_schema())
        return schemas

    async def execute_tool(self, tool_name: str, arguments: dict) -> str | None:
        """Try to execute a tool across all registered tool plugins.

        Returns result string or None if no plugin handles this tool.
        """
        for tool_plugin in self._tools.values():
            if tool_plugin.enabled and tool_name in tool_plugin.get_tool_names():
                return await tool_plugin.execute(tool_name, arguments)
        return None

    # --- Discovery ---

    async def discover_and_load(self, enabled_plugins: list[str] | None = None):
        """Discover and load plugins from builtin and external directories.

        If enabled_plugins is provided, only load matching plugins.
        """
        plugin_dirs = [
            Path(__file__).parent / "builtin",
        ]

        # Also check for external plugins directory
        external_dir = Path(__file__).parent / "external"
        if external_dir.exists():
            plugin_dirs.append(external_dir)

        for plugin_dir in plugin_dirs:
            if not plugin_dir.exists():
                continue

            for py_file in sorted(plugin_dir.glob("*.py")):
                if py_file.name.startswith("_"):
                    continue

                module_name = py_file.stem
                if enabled_plugins and module_name not in enabled_plugins:
                    logger.debug(f"Skipping disabled plugin: {module_name}")
                    continue

                try:
                    # Determine the import path
                    if "builtin" in str(py_file):
                        module_path = f"app.plugins.builtin.{module_name}"
                    else:
                        module_path = f"app.plugins.external.{module_name}"

                    module = importlib.import_module(module_path)

                    # Call the register() function if it exists
                    if hasattr(module, "register"):
                        module.register(self)
                        logger.info(f"Loaded plugin from: {py_file.name}")
                    else:
                        logger.warning(f"Plugin {py_file.name} has no register() function")

                except Exception as e:
                    logger.error(f"Failed to load plugin {py_file.name}: {e}")

    async def initialize_all(self):
        """Initialize all registered plugins."""
        for registries in (self._providers, self._channels, self._tools):
            for plugin in registries.values():
                try:
                    await plugin.initialize()
                except Exception as e:
                    logger.error(f"Failed to initialize plugin '{plugin.name}': {e}")

    async def shutdown_all(self):
        """Shutdown all registered plugins."""
        for registries in (self._providers, self._channels, self._tools):
            for plugin in registries.values():
                try:
                    await plugin.shutdown()
                except Exception as e:
                    logger.error(f"Error shutting down plugin '{plugin.name}': {e}")

    def get_status(self) -> dict:
        """Get status of all plugins for dashboard."""
        return {
            "providers": [
                {"name": p.name, "version": p.version, "enabled": p.enabled, "models": len(p.get_models())}
                for p in self._providers.values()
            ],
            "channels": [
                {"name": c.name, "version": c.version, "enabled": c.enabled}
                for c in self._channels.values()
            ],
            "tools": [
                {"name": t.name, "version": t.version, "enabled": t.enabled, "tool_count": len(t.get_tool_names())}
                for t in self._tools.values()
            ],
        }


# Singleton
plugin_registry = PluginRegistry()
