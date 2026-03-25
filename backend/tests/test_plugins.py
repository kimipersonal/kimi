"""Tests for the plugin registry system."""

import pytest

from app.plugins.base import ProviderPlugin, ChannelPlugin, ToolPlugin
from app.plugins.registry import PluginRegistry


class MockProvider(ProviderPlugin):
    @property
    def name(self) -> str:
        return "mock_provider"

    @property
    def version(self) -> str:
        return "0.1.0"

    def get_models(self) -> list[dict]:
        return [
            {"id": "mock-model-1", "name": "Mock Model 1", "cost": "~$0.001", "type": "test", "tier_hint": "fast"},
            {"id": "mock-model-2", "name": "Mock Model 2", "cost": "~$0.010", "type": "test", "tier_hint": "smart"},
        ]

    async def chat(self, model, messages, **kwargs) -> dict:
        return {"content": "mock response", "tool_calls": None, "model": model, "usage": {}, "finish_reason": "stop"}


class MockChannel(ChannelPlugin):
    @property
    def name(self) -> str:
        return "mock_channel"

    @property
    def version(self) -> str:
        return "0.1.0"

    async def start(self):
        pass

    async def stop(self):
        pass

    async def send_message(self, recipient: str, text: str, **kwargs) -> bool:
        return True


class MockTool(ToolPlugin):
    @property
    def name(self) -> str:
        return "mock_tool"

    @property
    def version(self) -> str:
        return "0.1.0"

    def get_tools_schema(self) -> list[dict]:
        return [{
            "type": "function",
            "function": {
                "name": "mock_action",
                "description": "A mock tool",
                "parameters": {"type": "object", "properties": {}},
            },
        }]

    async def execute(self, tool_name: str, arguments: dict) -> str:
        return '{"result": "mock"}'


class TestPluginRegistry:
    def test_register_provider(self):
        reg = PluginRegistry()
        reg.register(MockProvider())
        assert "mock_provider" in reg.providers
        assert len(reg.providers) == 1

    def test_register_channel(self):
        reg = PluginRegistry()
        reg.register(MockChannel())
        assert "mock_channel" in reg.channels

    def test_register_tool(self):
        reg = PluginRegistry()
        reg.register(MockTool())
        assert "mock_tool" in reg.tools

    def test_unregister(self):
        reg = PluginRegistry()
        reg.register(MockProvider())
        assert len(reg.providers) == 1
        reg.unregister("mock_provider")
        assert len(reg.providers) == 0

    def test_get_all_models(self):
        reg = PluginRegistry()
        reg.register(MockProvider())
        models = reg.get_all_models()
        assert len(models) == 2
        assert all(m["provider"] == "mock_provider" for m in models)

    def test_get_all_tool_schemas(self):
        reg = PluginRegistry()
        reg.register(MockTool())
        schemas = reg.get_all_tool_schemas()
        assert len(schemas) == 1
        assert schemas[0]["function"]["name"] == "mock_action"

    @pytest.mark.asyncio
    async def test_execute_tool(self):
        reg = PluginRegistry()
        reg.register(MockTool())
        result = await reg.execute_tool("mock_action", {})
        assert result == '{"result": "mock"}'

    @pytest.mark.asyncio
    async def test_execute_unknown_tool(self):
        reg = PluginRegistry()
        result = await reg.execute_tool("nonexistent", {})
        assert result is None

    def test_get_status(self):
        reg = PluginRegistry()
        reg.register(MockProvider())
        reg.register(MockChannel())
        reg.register(MockTool())
        status = reg.get_status()
        assert len(status["providers"]) == 1
        assert len(status["channels"]) == 1
        assert len(status["tools"]) == 1
        assert status["providers"][0]["models"] == 2

    @pytest.mark.asyncio
    async def test_discover_builtin(self):
        reg = PluginRegistry()
        await reg.discover_and_load()
        # Should find vertex_provider and telegram_channel at minimum
        assert len(reg.providers) >= 1
        assert "vertex_ai" in reg.providers

    @pytest.mark.asyncio
    async def test_initialize_and_shutdown(self):
        reg = PluginRegistry()
        reg.register(MockProvider())
        reg.register(MockChannel())
        await reg.initialize_all()
        await reg.shutdown_all()
        # Should not raise
