"""Tests for cost tracker — cost estimation, recording, summaries, budget alerts."""

import pytest

from app.services.cost_tracker import CostTracker, _estimate_cost


class TestCostEstimation:
    def test_known_model(self):
        # gemini-2.5-flash: $0.15 / $0.60 per 1M tokens
        cost = _estimate_cost("gemini-2.5-flash-001", 1_000_000, 1_000_000)
        assert abs(cost - 0.75) < 0.01  # 0.15 + 0.60

    def test_unknown_model_uses_default(self):
        # default: $0.30 / $0.90 per 1M tokens
        cost = _estimate_cost("some-unknown-model", 1_000_000, 1_000_000)
        assert abs(cost - 1.20) < 0.01

    def test_zero_tokens(self):
        cost = _estimate_cost("gemini-2.5-pro", 0, 0)
        assert cost == 0.0

    def test_pro_model_higher_cost(self):
        # gemini-2.5-pro: $1.25 / $10.0 per 1M tokens
        cost = _estimate_cost("gemini-2.5-pro", 1_000_000, 1_000_000)
        assert cost > 10.0


class TestCostTracker:
    @pytest.fixture
    def tracker(self):
        return CostTracker(daily_budget_usd=1.0, alert_threshold=0.8)

    @pytest.mark.asyncio
    async def test_record_and_summary(self, tracker):
        await tracker.record("agent-1", "gemini-2.5-flash", 1000, 500)
        summary = tracker.get_agent_summary("agent-1")
        assert summary["total_calls"] == 1
        assert summary["total_input_tokens"] == 1000
        assert summary["total_output_tokens"] == 500
        assert summary["total_cost_usd"] > 0

    @pytest.mark.asyncio
    async def test_multiple_records(self, tracker):
        await tracker.record("agent-1", "gemini-2.5-flash", 1000, 500)
        await tracker.record("agent-1", "gemini-2.5-flash", 2000, 1000)
        summary = tracker.get_agent_summary("agent-1")
        assert summary["total_calls"] == 2
        assert summary["total_input_tokens"] == 3000
        assert summary["total_output_tokens"] == 1500

    @pytest.mark.asyncio
    async def test_separate_agents(self, tracker):
        await tracker.record("agent-1", "gemini-2.5-flash", 1000, 500)
        await tracker.record("agent-2", "gemini-2.5-pro", 2000, 1000)
        s1 = tracker.get_agent_summary("agent-1")
        s2 = tracker.get_agent_summary("agent-2")
        assert s1["total_calls"] == 1
        assert s2["total_calls"] == 1

    @pytest.mark.asyncio
    async def test_unknown_agent_summary(self, tracker):
        summary = tracker.get_agent_summary("nonexistent")
        assert summary["total_calls"] == 0
        assert summary["total_cost_usd"] == 0

    @pytest.mark.asyncio
    async def test_overview(self, tracker):
        await tracker.record("agent-1", "gemini-2.5-flash", 1000, 500)
        overview = await tracker.get_overview()
        assert overview["total_calls"] >= 1
        assert overview["total_cost_usd"] > 0
        assert "agents" in overview

    @pytest.mark.asyncio
    async def test_get_all_summaries(self, tracker):
        await tracker.record("agent-1", "gemini-2.5-flash", 1000, 500)
        await tracker.record("agent-2", "gemini-2.5-pro", 2000, 1000)
        summaries = tracker.get_all_summaries()
        assert len(summaries) == 2

    @pytest.mark.asyncio
    async def test_cost_today(self, tracker):
        await tracker.record("agent-1", "gemini-2.5-flash", 1000, 500)
        assert tracker.get_total_cost_today() > 0

    @pytest.mark.asyncio
    async def test_budget_alert(self, tracker):
        alert_fired = []
        tracker.on_budget_alert(lambda total, budget: alert_fired.append((total, budget)))
        # Record enough to exceed 80% of $1.00 budget
        # gemini-2.5-pro: ~$11.25 per 1M tokens — use large counts
        await tracker.record("agent-1", "gemini-2.5-pro", 100_000, 100_000)
        assert len(alert_fired) == 1
        assert alert_fired[0][1] == 1.0  # budget

    @pytest.mark.asyncio
    async def test_budget_alert_fires_once(self, tracker):
        alert_count = []
        tracker.on_budget_alert(lambda total, budget: alert_count.append(1))
        await tracker.record("agent-1", "gemini-2.5-pro", 100_000, 100_000)
        await tracker.record("agent-1", "gemini-2.5-pro", 100_000, 100_000)
        assert len(alert_count) == 1  # Only fires once
