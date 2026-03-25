"""Tests for the circuit breaker."""

import pytest
from unittest.mock import AsyncMock

from app.services.circuit_breaker import CircuitBreaker, CircuitOpenError


class TestCircuitBreaker:
    def test_initial_state_closed(self):
        cb = CircuitBreaker("test")
        assert cb.state == "closed"

    @pytest.mark.asyncio
    async def test_successful_call(self):
        cb = CircuitBreaker("test")
        mock_fn = AsyncMock(return_value="ok")
        result = await cb.call(mock_fn, x=1)
        assert result == "ok"
        assert cb._failure_count == 0

    @pytest.mark.asyncio
    async def test_failure_increments_count(self):
        cb = CircuitBreaker("test", failure_threshold=3)
        mock_fn = AsyncMock(side_effect=RuntimeError("fail"))
        with pytest.raises(RuntimeError):
            await cb.call(mock_fn)
        assert cb._failure_count == 1

    @pytest.mark.asyncio
    async def test_circuit_opens_after_threshold(self):
        cb = CircuitBreaker("test", failure_threshold=2)
        mock_fn = AsyncMock(side_effect=RuntimeError("fail"))
        for _ in range(2):
            with pytest.raises(RuntimeError):
                await cb.call(mock_fn)
        assert cb.state == "open"

    @pytest.mark.asyncio
    async def test_open_circuit_raises_error(self):
        cb = CircuitBreaker("test", failure_threshold=1)
        mock_fn = AsyncMock(side_effect=RuntimeError("fail"))
        with pytest.raises(RuntimeError):
            await cb.call(mock_fn)
        # Now circuit is open
        with pytest.raises(CircuitOpenError):
            await cb.call(mock_fn)
