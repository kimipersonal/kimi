"""Tests for the model failover service."""

import time
from unittest.mock import AsyncMock, patch

import pytest

from app.services.model_failover import ModelHealth, ModelFailoverService, MODEL_COOLDOWN_SECONDS


class TestModelHealth:
    def test_initial_state(self):
        h = ModelHealth(model_id="test-model")
        assert h.consecutive_failures == 0
        assert h.is_cooled_down is True
        assert h.success_rate == 1.0

    def test_record_success(self):
        h = ModelHealth(model_id="test-model")
        h.record_success(100.0)
        assert h.total_successes == 1
        assert h.avg_latency_ms == 100.0
        assert h.consecutive_failures == 0

    def test_record_failure(self):
        h = ModelHealth(model_id="test-model")
        h.record_failure()
        assert h.consecutive_failures == 1
        assert h.total_failures == 1
        assert h.is_cooled_down is False  # Just failed, in cooldown

    def test_success_resets_failures(self):
        h = ModelHealth(model_id="test-model")
        h.record_failure()
        h.record_failure()
        assert h.consecutive_failures == 2
        h.record_success(50.0)
        assert h.consecutive_failures == 0

    def test_cooldown_expires(self):
        h = ModelHealth(model_id="test-model")
        h.record_failure()
        # Simulate time passing
        h.last_failure_time = time.time() - MODEL_COOLDOWN_SECONDS - 1
        assert h.is_cooled_down is True

    def test_success_rate(self):
        h = ModelHealth(model_id="test-model")
        h.record_success(50.0)
        h.record_success(60.0)
        h.record_failure()
        assert abs(h.success_rate - 2/3) < 0.01

    def test_latency_tracking(self):
        h = ModelHealth(model_id="test-model")
        h.record_success(100.0)
        h.record_success(200.0)
        assert h.avg_latency_ms == 150.0


class TestModelFailoverService:
    def test_get_tier_models(self):
        svc = ModelFailoverService()
        models = svc.get_tier_models("fast")
        assert len(models) > 0

    def test_health_report_empty(self):
        svc = ModelFailoverService()
        report = svc.get_health_report()
        assert isinstance(report, list)

    @pytest.mark.asyncio
    async def test_chat_with_model_override(self):
        """model_override should bypass tier failover."""
        svc = ModelFailoverService()
        mock_response = {
            "content": "test",
            "tool_calls": None,
            "model": "test-model",
            "usage": {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
            "finish_reason": "stop",
        }

        with patch.object(svc, "_try_model", new_callable=AsyncMock) as mock_try:
            mock_try.return_value = mock_response
            result = await svc.chat_with_failover(
                messages=[{"role": "user", "content": "hi"}],
                model_override="specific-model",
            )
            mock_try.assert_called_once()
            assert result["content"] == "test"
