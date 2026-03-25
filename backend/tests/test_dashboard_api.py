"""Tests for dashboard API — overview, approvals, logs, settings, models, costs."""

import pytest
from unittest.mock import AsyncMock, patch


class TestOverview:
    @pytest.mark.asyncio
    @patch("app.api.dashboard.registry")
    async def test_get_overview(self, mock_registry):
        mock_registry.count = 3
        mock_registry.active_count = 1

        with (
            patch("app.services.approval_service.list_approvals", new_callable=AsyncMock) as mock_approvals,
            patch("app.services.company_manager.list_companies", new_callable=AsyncMock) as mock_companies,
            patch("app.services.cost_tracker.cost_tracker") as mock_ct,
        ):
            mock_approvals.return_value = [{"id": "a1", "status": "pending"}]
            mock_companies.return_value = [{"name": "TradingCo"}]
            mock_ct.get_total_cost_today.return_value = 0.42

            from app.api.dashboard import get_overview
            result = await get_overview()

        assert result.total_companies == 1
        assert result.total_agents == 3
        assert result.active_agents == 1
        assert result.pending_approvals == 1
        assert result.total_cost_today == 0.42


class TestApprovals:
    @pytest.mark.asyncio
    async def test_get_approvals(self):
        with patch("app.services.approval_service.list_approvals", new_callable=AsyncMock) as mock_list:
            mock_list.return_value = [
                {"id": "a1", "status": "pending", "description": "Test"},
            ]

            from app.api.dashboard import get_approvals
            result = await get_approvals(status="pending")

        assert len(result) == 1
        assert result[0]["status"] == "pending"

    @pytest.mark.asyncio
    async def test_decide_approval_success(self):
        with patch("app.services.approval_service.decide_approval", new_callable=AsyncMock) as mock_decide:
            mock_decide.return_value = {"id": "a1", "status": "approved"}

            from app.api.dashboard import decide_approval
            from app.models.schemas import ApprovalDecision
            result = await decide_approval("a1", ApprovalDecision(approved=True))

        assert result["status"] == "approved"

    @pytest.mark.asyncio
    async def test_decide_approval_not_found(self):
        with patch("app.services.approval_service.decide_approval", new_callable=AsyncMock) as mock_decide:
            mock_decide.return_value = None

            from app.api.dashboard import decide_approval
            from app.models.schemas import ApprovalDecision
            from fastapi import HTTPException

            with pytest.raises(HTTPException) as exc_info:
                await decide_approval("nope", ApprovalDecision(approved=True))
            assert exc_info.value.status_code == 404


class TestActivityLogs:
    @pytest.mark.asyncio
    @patch("app.api.dashboard.event_bus")
    async def test_get_logs(self, mock_bus):
        mock_bus.history = [
            {"agent_id": "ceo", "action": "started"},
            {"agent_id": "trader", "action": "trade_exec"},
        ]

        from app.api.dashboard import get_activity_logs
        result = await get_activity_logs()
        assert len(result) == 2

    @pytest.mark.asyncio
    @patch("app.api.dashboard.event_bus")
    async def test_get_logs_filtered(self, mock_bus):
        mock_bus.history = [
            {"agent_id": "ceo", "action": "started"},
            {"agent_id": "trader", "action": "trade_exec"},
        ]

        from app.api.dashboard import get_activity_logs
        result = await get_activity_logs(agent_id="ceo")
        assert len(result) == 1
        assert result[0]["agent_id"] == "ceo"


class TestSettings:
    @pytest.mark.asyncio
    @patch("app.api.dashboard.registry")
    async def test_get_settings(self, mock_registry):
        mock_registry.count = 2
        mock_registry.active_count = 1

        from app.api.dashboard import get_settings
        result = await get_settings()
        assert "llm_model_fast" in result
        assert "llm_model_smart" in result
        assert "llm_model_reasoning" in result
        assert result["total_agents"] == 2

    @pytest.mark.asyncio
    async def test_update_settings_valid(self):
        with patch("app.services.llm_router.update_tier"):
            from app.api.dashboard import update_settings, SettingsUpdate
            result = await update_settings(SettingsUpdate(llm_fast="gemini-2.5-flash-lite-001"))
            assert "updated" in result
            assert "current_tiers" in result


class TestModels:
    @pytest.mark.asyncio
    async def test_get_models(self):
        from app.api.dashboard import get_available_models
        result = await get_available_models()
        assert "models" in result
        assert "current_tiers" in result
        assert len(result["models"]) > 0


class TestCosts:
    @pytest.mark.asyncio
    async def test_get_cost_overview(self):
        with patch("app.services.cost_tracker.cost_tracker") as mock_ct:
            mock_ct.get_overview.return_value = {
                "total_calls": 10,
                "total_cost_usd": 0.05,
                "agents": [],
            }

            from app.api.dashboard import get_cost_overview
            result = await get_cost_overview()
        assert result["total_calls"] == 10

    @pytest.mark.asyncio
    async def test_get_agent_cost(self):
        with patch("app.services.cost_tracker.cost_tracker") as mock_ct:
            mock_ct.get_agent_summary.return_value = {
                "agent_id": "ceo",
                "total_calls": 5,
                "total_cost_usd": 0.02,
            }

            from app.api.dashboard import get_agent_cost
            result = await get_agent_cost("ceo")
        assert result["agent_id"] == "ceo"


class TestHealth:
    @pytest.mark.asyncio
    async def test_get_health(self):
        with patch("app.services.health_monitor.health_monitor") as mock_hm:
            mock_hm.get_health.return_value = {"status": "healthy"}

            from app.api.dashboard import get_health
            result = await get_health()
        assert result["status"] == "healthy"

    @pytest.mark.asyncio
    async def test_get_circuit_breakers(self):
        with patch("app.services.circuit_breaker.circuit_registry") as mock_cr:
            mock_cr.get_all_status.return_value = [
                {"model": "gemini-2.5-flash", "state": "closed"},
            ]

            from app.api.dashboard import get_circuit_breakers
            result = await get_circuit_breakers()
        assert "circuits" in result
        assert len(result["circuits"]) == 1
