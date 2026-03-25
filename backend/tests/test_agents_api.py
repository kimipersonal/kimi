"""Tests for agents API — list, get, command, chat endpoints."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from enum import Enum


class MockStatus(Enum):
    IDLE = "idle"
    RUNNING = "running"


def _make_mock_agent(agent_id="agent-1", name="Test Agent", role="worker",
                     status=None, model_tier="fast", tools=None):
    agent = MagicMock()
    agent.agent_id = agent_id
    agent.name = name
    agent.role = role
    agent.status = status or MockStatus.IDLE
    agent.model_tier = model_tier
    agent.tools = tools or []
    agent.start = AsyncMock()
    agent.stop = AsyncMock()
    agent.pause = AsyncMock()
    agent.resume = AsyncMock()
    agent.run = AsyncMock(return_value="Agent response")
    return agent


class TestListAgents:
    @pytest.mark.asyncio
    @patch("app.api.agents.registry")
    async def test_list_agents(self, mock_registry):
        mock_agent = _make_mock_agent()
        mock_registry.get_all.return_value = [mock_agent]

        with patch("app.services.company_manager.list_companies", new_callable=AsyncMock) as mock_companies:
            mock_companies.return_value = []

            from app.api.agents import list_agents
            result = await list_agents()

        assert len(result) == 1
        assert result[0]["id"] == "agent-1"
        assert result[0]["name"] == "Test Agent"
        assert result[0]["status"] == "idle"


class TestGetAgent:
    @pytest.mark.asyncio
    @patch("app.api.agents.registry")
    async def test_get_agent_found(self, mock_registry):
        mock_agent = _make_mock_agent()
        # Make isinstance check fail for CEOAgent
        type(mock_agent).__name__ = "MockAgent"
        mock_registry.get.return_value = mock_agent

        from app.api.agents import get_agent
        result = await get_agent("agent-1")
        assert result["id"] == "agent-1"
        assert result["role"] == "worker"

    @pytest.mark.asyncio
    @patch("app.api.agents.registry")
    async def test_get_agent_not_found(self, mock_registry):
        mock_registry.get.return_value = None

        from app.api.agents import get_agent
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc_info:
            await get_agent("nonexistent")
        assert exc_info.value.status_code == 404


class TestSendCommand:
    @pytest.mark.asyncio
    @patch("app.api.agents.registry")
    async def test_start_command(self, mock_registry):
        mock_agent = _make_mock_agent()
        mock_registry.get.return_value = mock_agent

        from app.api.agents import send_command
        from app.models.schemas import AgentCommand

        result = await send_command("agent-1", AgentCommand(command="start"))
        assert result["status"] == "started"
        mock_agent.start.assert_called_once()

    @pytest.mark.asyncio
    @patch("app.api.agents.registry")
    async def test_stop_command(self, mock_registry):
        mock_agent = _make_mock_agent()
        mock_registry.get.return_value = mock_agent

        from app.api.agents import send_command
        from app.models.schemas import AgentCommand

        result = await send_command("agent-1", AgentCommand(command="stop"))
        assert result["status"] == "stopped"

    @pytest.mark.asyncio
    @patch("app.api.agents.registry")
    async def test_message_command(self, mock_registry):
        mock_agent = _make_mock_agent()
        mock_registry.get.return_value = mock_agent

        from app.api.agents import send_command
        from app.models.schemas import AgentCommand

        result = await send_command("agent-1", AgentCommand(command="message", payload="hello"))
        assert result["status"] == "completed"
        assert result["output"] == "Agent response"

    @pytest.mark.asyncio
    @patch("app.api.agents.registry")
    async def test_unknown_command(self, mock_registry):
        mock_agent = _make_mock_agent()
        mock_registry.get.return_value = mock_agent

        from app.api.agents import send_command
        from app.models.schemas import AgentCommand
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc_info:
            await send_command("agent-1", AgentCommand(command="explode"))
        assert exc_info.value.status_code == 400

    @pytest.mark.asyncio
    @patch("app.api.agents.registry")
    async def test_command_agent_not_found(self, mock_registry):
        mock_registry.get.return_value = None

        from app.api.agents import send_command
        from app.models.schemas import AgentCommand
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc_info:
            await send_command("ghost", AgentCommand(command="start"))
        assert exc_info.value.status_code == 404


class TestChatWithAgent:
    @pytest.mark.asyncio
    @patch("app.api.agents.registry")
    async def test_chat(self, mock_registry):
        mock_agent = _make_mock_agent()
        mock_registry.get.return_value = mock_agent

        from app.api.agents import chat_with_agent

        result = await chat_with_agent("agent-1", {"message": "Hello!"})
        assert result["agent_id"] == "agent-1"
        assert result["response"] == "Agent response"

    @pytest.mark.asyncio
    @patch("app.api.agents.registry")
    async def test_chat_empty_message(self, mock_registry):
        mock_agent = _make_mock_agent()
        mock_registry.get.return_value = mock_agent

        from app.api.agents import chat_with_agent
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc_info:
            await chat_with_agent("agent-1", {"message": ""})
        assert exc_info.value.status_code == 400
