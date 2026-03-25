"""Tests for the base agent — state machine, memory integration, context compaction."""

from unittest.mock import AsyncMock, patch

import pytest

from app.agents.base import BaseAgent, AgentState
from app.db.models import AgentStatus


class TestBaseAgent:
    def _make_agent(self, **kwargs):
        defaults = {
            "agent_id": "test-agent",
            "name": "Test Agent",
            "role": "tester",
            "system_prompt": "You are a test agent.",
            "model_tier": "fast",
        }
        defaults.update(kwargs)
        return BaseAgent(**defaults)

    def test_creation(self):
        agent = self._make_agent()
        assert agent.agent_id == "test-agent"
        assert agent.status == AgentStatus.IDLE

    @pytest.mark.asyncio
    async def test_start_stop(self):
        agent = self._make_agent()
        with patch.object(agent, "_set_status", new_callable=AsyncMock):
            with patch.object(agent, "log_activity", new_callable=AsyncMock):
                await agent.start()
                assert not agent._stopped
                await agent.stop()
                assert agent._stopped

    @pytest.mark.asyncio
    async def test_run_returns_output(self):
        agent = self._make_agent()

        mock_response = {
            "content": "Hello from LLM",
            "tool_calls": None,
            "model": "test",
            "usage": {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
            "finish_reason": "stop",
        }

        with patch("app.services.model_failover.failover_service.chat_with_failover", new_callable=AsyncMock) as mock_chat, \
             patch.object(agent, "_set_status", new_callable=AsyncMock), \
             patch.object(agent, "log_activity", new_callable=AsyncMock), \
             patch("app.services.llm_router.get_embedding", new_callable=AsyncMock) as mock_emb, \
             patch("app.services.memory_service.recall_memories", new_callable=AsyncMock) as mock_recall, \
             patch("app.services.memory_service.store_memory", new_callable=AsyncMock):
            mock_chat.return_value = mock_response
            mock_emb.return_value = [0.1] * 768
            mock_recall.return_value = []

            result = await agent.run("test input")
            assert "Hello from LLM" in result

    @pytest.mark.asyncio
    async def test_stopped_agent_refuses_run(self):
        agent = self._make_agent()
        agent._stopped = True
        result = await agent.run("test")
        assert "stopped" in result.lower()

    def test_should_act_no_tools(self):
        agent = self._make_agent()
        state: AgentState = {
            "messages": [],
            "current_input": "",
            "output": "test",
            "tool_calls": [],
            "should_continue": False,
            "awaiting_approval": False,
            "approval_result": None,
            "error": None,
        }
        assert agent._should_act(state) == "end"

    def test_should_act_with_tools(self):
        agent = self._make_agent()
        state: AgentState = {
            "messages": [],
            "current_input": "",
            "output": "",
            "tool_calls": [{"name": "test_tool", "arguments": {}}],
            "should_continue": False,
            "awaiting_approval": False,
            "approval_result": None,
            "error": None,
        }
        assert agent._should_act(state) == "act"

    def test_should_act_with_error(self):
        agent = self._make_agent()
        state: AgentState = {
            "messages": [],
            "current_input": "",
            "output": "",
            "tool_calls": [{"name": "test_tool", "arguments": {}}],
            "should_continue": False,
            "awaiting_approval": False,
            "approval_result": None,
            "error": "something broke",
        }
        assert agent._should_act(state) == "end"
