"""Shared pytest fixtures for the AI Holding test suite."""

import asyncio
import os
from unittest.mock import AsyncMock, patch

import pytest


# Ensure test settings
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://kimi:kimi@localhost:5432/ai_holding_test")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/1")
os.environ.setdefault("DEBUG", "true")
os.environ.setdefault("GCP_PROJECT_ID", "test-project")
os.environ.setdefault("GCP_REGION", "us-central1")


@pytest.fixture(scope="session")
def event_loop():
    """Create a session-scoped event loop."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture
def mock_llm_response():
    """Factory for creating mock LLM responses."""
    def _make(content: str = "Test response", tool_calls=None):
        return {
            "content": content,
            "tool_calls": tool_calls,
            "model": "test-model",
            "usage": {
                "input_tokens": 100,
                "output_tokens": 50,
                "total_tokens": 150,
            },
            "finish_reason": "stop",
        }
    return _make


@pytest.fixture
def mock_embedding():
    """Return a mock 768-dim embedding vector."""
    return [0.1] * 768


@pytest.fixture
def mock_llm_router(mock_llm_response):
    """Patch llm_router.chat to return a mock response."""
    with patch("app.services.llm_router.chat", new_callable=AsyncMock) as mock_chat:
        mock_chat.return_value = mock_llm_response()
        yield mock_chat


@pytest.fixture
def mock_failover_service(mock_llm_response):
    """Patch failover_service.chat_with_failover."""
    with patch(
        "app.services.model_failover.failover_service.chat_with_failover",
        new_callable=AsyncMock,
    ) as mock:
        mock.return_value = mock_llm_response()
        yield mock


@pytest.fixture
def mock_event_bus():
    """Patch event bus broadcast."""
    with patch("app.services.event_bus.event_bus.broadcast", new_callable=AsyncMock) as mock:
        yield mock


@pytest.fixture
def mock_cost_tracker():
    """Patch cost tracker record."""
    with patch("app.services.cost_tracker.cost_tracker.record", new_callable=AsyncMock) as mock:
        yield mock


@pytest.fixture
def mock_get_embedding(mock_embedding):
    """Patch get_embedding to return a fixed vector."""
    with patch("app.services.llm_router.get_embedding", new_callable=AsyncMock) as mock:
        mock.return_value = mock_embedding
        yield mock
