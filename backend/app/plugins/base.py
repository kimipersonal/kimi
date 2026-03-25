"""Plugin base classes — abstract interfaces for providers, channels, and tools."""

from abc import ABC, abstractmethod


class PluginBase(ABC):
    """Common interface for all plugins."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Unique plugin name (e.g., 'vertex_ai', 'telegram')."""
        ...

    @property
    @abstractmethod
    def version(self) -> str:
        """Plugin version string."""
        ...

    @property
    def description(self) -> str:
        """Human-readable description."""
        return ""

    @property
    def enabled(self) -> bool:
        """Whether this plugin is currently enabled."""
        return True

    async def initialize(self):
        """Called once when plugin is loaded. Override for setup logic."""
        pass

    async def shutdown(self):
        """Called on application shutdown. Override for cleanup."""
        pass


class ProviderPlugin(PluginBase):
    """LLM provider plugin — wraps an LLM API (Vertex AI, Anthropic, OpenAI, etc.)."""

    @abstractmethod
    def get_models(self) -> list[dict]:
        """Return list of available models with metadata.

        Each dict should have: id, name, cost, type, tier_hint
        """
        ...

    @abstractmethod
    async def chat(
        self,
        model: str,
        messages: list[dict],
        temperature: float = 0.7,
        max_tokens: int = 4096,
        tools: list[dict] | None = None,
    ) -> dict:
        """Send a chat completion request.

        Returns dict with: content, tool_calls, model, usage, finish_reason
        """
        ...

    async def get_embedding(self, text: str) -> list[float] | None:
        """Get text embedding. Return None if not supported."""
        return None

    def get_context_window(self, model: str) -> int:
        """Get context window size for a model. Default 128K."""
        return 131_072


class ChannelPlugin(PluginBase):
    """Communication channel plugin — wraps a messaging platform."""

    @abstractmethod
    async def start(self):
        """Start listening for messages on this channel."""
        ...

    @abstractmethod
    async def stop(self):
        """Stop the channel."""
        ...

    @abstractmethod
    async def send_message(self, recipient: str, text: str, **kwargs) -> bool:
        """Send a message to a recipient."""
        ...

    async def notify(self, event: str, data: dict, agent_id: str | None = None):
        """Handle an event notification. Override to filter/format."""
        pass


class ToolPlugin(PluginBase):
    """Agent tool plugin — provides tools that agents can use."""

    @abstractmethod
    def get_tools_schema(self) -> list[dict]:
        """Return OpenAI-format tool schemas."""
        ...

    @abstractmethod
    async def execute(self, tool_name: str, arguments: dict) -> str:
        """Execute a tool by name. Returns JSON result string."""
        ...

    def get_tool_names(self) -> list[str]:
        """Get list of tool names provided by this plugin."""
        return [t["function"]["name"] for t in self.get_tools_schema()]
