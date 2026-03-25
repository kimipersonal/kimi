"""Tests for messaging service — send, get, conversation retrieval (mocks DB)."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timezone


class TestSendMessage:
    @pytest.mark.asyncio
    @patch("app.services.messaging.async_session")
    @patch("app.services.messaging.event_bus")
    async def test_send_message(self, mock_bus, mock_session_factory):
        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)
        mock_session_factory.return_value = mock_session

        mock_bus.broadcast = AsyncMock()

        from app.services.messaging import send_message

        result = await send_message(
            from_agent_id="agent-1",
            to_agent_id="agent-2",
            content="Hello there",
            message_type="chat",
        )

        assert result["from_agent_id"] == "agent-1"
        assert result["to_agent_id"] == "agent-2"
        assert result["content"] == "Hello there"
        assert result["message_type"] == "chat"
        assert "id" in result
        mock_session.add.assert_called_once()
        mock_session.commit.assert_called_once()

    @pytest.mark.asyncio
    @patch("app.services.messaging.async_session")
    @patch("app.services.messaging.event_bus")
    async def test_send_message_broadcasts(self, mock_bus, mock_session_factory):
        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)
        mock_session_factory.return_value = mock_session

        mock_bus.broadcast = AsyncMock()

        from app.services.messaging import send_message

        await send_message("agent-1", "agent-2", "Test message")
        mock_bus.broadcast.assert_called_once()
        assert mock_bus.broadcast.call_args[0][0] == "message"

    @pytest.mark.asyncio
    @patch("app.services.messaging.async_session")
    @patch("app.services.messaging.event_bus")
    async def test_owner_to_agent_message(self, mock_bus, mock_session_factory):
        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)
        mock_session_factory.return_value = mock_session

        mock_bus.broadcast = AsyncMock()

        from app.services.messaging import send_message

        result = await send_message(
            from_agent_id=None,
            to_agent_id="agent-1",
            content="Owner instruction",
        )
        assert result["from_agent_id"] is None
        assert result["to_agent_id"] == "agent-1"


class TestGetMessages:
    @pytest.mark.asyncio
    @patch("app.services.messaging.async_session")
    async def test_get_messages_empty(self, mock_session_factory):
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = []

        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)
        mock_session.execute = AsyncMock(return_value=mock_result)
        mock_session_factory.return_value = mock_session

        from app.services.messaging import get_messages

        result = await get_messages()
        assert isinstance(result, list)
        assert len(result) == 0

    @pytest.mark.asyncio
    @patch("app.services.messaging.async_session")
    async def test_get_messages_with_data(self, mock_session_factory):
        mock_msg = MagicMock()
        mock_msg.id = "msg-1"
        mock_msg.from_agent_id = "agent-1"
        mock_msg.to_agent_id = "agent-2"
        mock_msg.content = "Hello"
        mock_msg.message_type = "chat"
        mock_msg.metadata_ = None
        mock_msg.created_at = datetime.now(timezone.utc)

        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [mock_msg]

        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)
        mock_session.execute = AsyncMock(return_value=mock_result)
        mock_session_factory.return_value = mock_session

        from app.services.messaging import get_messages

        result = await get_messages(agent_id="agent-1")
        assert len(result) == 1
        assert result[0]["content"] == "Hello"


class TestGetConversation:
    @pytest.mark.asyncio
    @patch("app.services.messaging.async_session")
    async def test_get_conversation_empty(self, mock_session_factory):
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = []

        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)
        mock_session.execute = AsyncMock(return_value=mock_result)
        mock_session_factory.return_value = mock_session

        from app.services.messaging import get_conversation

        result = await get_conversation("agent-1", "agent-2")
        assert isinstance(result, list)
        assert len(result) == 0
