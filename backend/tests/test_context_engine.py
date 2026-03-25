"""Tests for the context engine — token counting and compaction."""

from unittest.mock import AsyncMock, patch

import pytest

from app.services.context_engine import count_tokens, count_message_tokens, compact_messages


class TestTokenCounting:
    def test_count_tokens_basic(self):
        tokens = count_tokens("Hello, world!")
        assert tokens > 0
        assert isinstance(tokens, int)

    def test_count_tokens_empty(self):
        assert count_tokens("") == 0

    def test_count_tokens_long(self):
        text = "word " * 1000
        tokens = count_tokens(text)
        assert tokens > 500  # ~1000 words should be > 500 tokens

    def test_count_message_tokens(self):
        messages = [
            {"role": "system", "content": "You are a helpful assistant."},
            {"role": "user", "content": "Hello!"},
            {"role": "assistant", "content": "Hi there!"},
        ]
        tokens = count_message_tokens(messages)
        assert tokens > 0

    def test_count_message_tokens_empty(self):
        assert count_message_tokens([]) == 0


class TestCompaction:
    @pytest.mark.asyncio
    async def test_no_compaction_needed(self):
        """Short messages should pass through unchanged."""
        messages = [
            {"role": "system", "content": "System prompt"},
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi"},
        ]
        with patch("app.services.context_engine.llm_router") as mock_router:
            mock_router.get_context_window.return_value = 100_000
            result = await compact_messages(messages, tier="smart")

        assert len(result) == 3
        assert result[0]["content"] == "System prompt"

    @pytest.mark.asyncio
    async def test_compaction_triggered(self):
        """Large conversation should be compacted."""
        messages = [
            {"role": "system", "content": "System prompt"},
        ]
        # Add many messages to exceed a small context window
        for i in range(50):
            messages.append({"role": "user", "content": f"Message {i} " * 100})
            messages.append({"role": "assistant", "content": f"Response {i} " * 100})

        with patch("app.services.context_engine.llm_router") as mock_router:
            mock_router.get_context_window.return_value = 2000
            mock_router.chat = AsyncMock(return_value={"content": "Summary of conversation."})
            result = await compact_messages(messages, tier="smart", max_tokens=2000)

        # Should be much shorter than original
        assert len(result) < len(messages)
        # Should start with system message
        assert result[0]["role"] == "system"

    @pytest.mark.asyncio
    async def test_empty_messages(self):
        result = await compact_messages([])
        assert result == []
