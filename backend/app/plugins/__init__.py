"""Plugin architecture for AI Holding.

Provides abstract base classes for three plugin types:
- ProviderPlugin: LLM providers (Vertex AI, Anthropic, OpenAI, etc.)
- ChannelPlugin: Communication channels (Telegram, Discord, Slack, etc.)
- ToolPlugin: Agent tools (browser, sandbox, custom tools)
"""

from .base import ProviderPlugin, ChannelPlugin, ToolPlugin
from .registry import PluginRegistry, plugin_registry

__all__ = [
    "ProviderPlugin",
    "ChannelPlugin",
    "ToolPlugin",
    "PluginRegistry",
    "plugin_registry",
]
