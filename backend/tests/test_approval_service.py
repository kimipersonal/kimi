"""Tests for approval service — create, decide, list approvals (mocks DB)."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timezone


class TestCreateApproval:
    @pytest.mark.asyncio
    @patch("app.services.approval_service.async_session")
    @patch("app.services.approval_service.event_bus")
    async def test_create_approval_returns_dict(self, mock_bus, mock_session_factory):
        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)
        mock_session_factory.return_value = mock_session

        mock_bus.broadcast = AsyncMock()

        from app.services.approval_service import create_approval

        result = await create_approval(
            agent_id="ceo-001",
            description="Create new trading company",
            category="company_creation",
            details={"company_type": "trading"},
        )

        assert result["agent_id"] == "ceo-001"
        assert result["description"] == "Create new trading company"
        assert result["category"] == "company_creation"
        assert result["status"] == "pending"
        assert "id" in result
        mock_session.add.assert_called_once()
        mock_session.commit.assert_called_once()

    @pytest.mark.asyncio
    @patch("app.services.approval_service.async_session")
    @patch("app.services.approval_service.event_bus")
    async def test_create_approval_broadcasts_event(self, mock_bus, mock_session_factory):
        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)
        mock_session_factory.return_value = mock_session

        mock_bus.broadcast = AsyncMock()

        from app.services.approval_service import create_approval

        await create_approval(agent_id="ceo-001", description="Test")
        mock_bus.broadcast.assert_called_once()
        call_args = mock_bus.broadcast.call_args
        assert call_args[0][0] == "approval_request"


class TestDecideApproval:
    @pytest.mark.asyncio
    @patch("app.services.approval_service.async_session")
    @patch("app.services.approval_service.event_bus")
    async def test_decide_approval_approve(self, mock_bus, mock_session_factory):
        from app.db.models import ApprovalStatus

        mock_approval = MagicMock()
        mock_approval.id = "test-id"
        mock_approval.agent_id = "ceo-001"
        mock_approval.description = "Test"
        mock_approval.category = "general"
        mock_approval.status = ApprovalStatus.PENDING
        mock_approval.requested_at = datetime.now(timezone.utc)

        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)
        mock_session.get = AsyncMock(return_value=mock_approval)
        mock_session_factory.return_value = mock_session

        mock_bus.broadcast = AsyncMock()

        from app.services.approval_service import decide_approval

        result = await decide_approval("test-id", approved=True, reason="Looks good")
        assert result is not None
        assert result["status"] == "approved"
        assert mock_approval.status == ApprovalStatus.APPROVED

    @pytest.mark.asyncio
    @patch("app.services.approval_service.async_session")
    @patch("app.services.approval_service.event_bus")
    async def test_decide_approval_reject(self, mock_bus, mock_session_factory):
        from app.db.models import ApprovalStatus

        mock_approval = MagicMock()
        mock_approval.id = "test-id"
        mock_approval.agent_id = "ceo-001"
        mock_approval.description = "Test"
        mock_approval.category = "general"
        mock_approval.status = ApprovalStatus.PENDING
        mock_approval.requested_at = datetime.now(timezone.utc)

        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)
        mock_session.get = AsyncMock(return_value=mock_approval)
        mock_session_factory.return_value = mock_session

        mock_bus.broadcast = AsyncMock()

        from app.services.approval_service import decide_approval

        result = await decide_approval("test-id", approved=False, reason="Too risky")
        assert result["status"] == "rejected"
        assert result["decision_reason"] == "Too risky"

    @pytest.mark.asyncio
    @patch("app.services.approval_service.async_session")
    async def test_decide_not_found(self, mock_session_factory):
        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)
        mock_session.get = AsyncMock(return_value=None)
        mock_session_factory.return_value = mock_session

        from app.services.approval_service import decide_approval

        result = await decide_approval("nonexistent", approved=True)
        assert result is None

    @pytest.mark.asyncio
    @patch("app.services.approval_service.async_session")
    async def test_decide_already_decided(self, mock_session_factory):
        from app.db.models import ApprovalStatus

        mock_approval = MagicMock()
        mock_approval.status = ApprovalStatus.APPROVED

        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)
        mock_session.get = AsyncMock(return_value=mock_approval)
        mock_session_factory.return_value = mock_session

        from app.services.approval_service import decide_approval

        result = await decide_approval("already-done", approved=True)
        assert result is None


class TestListApprovals:
    @pytest.mark.asyncio
    @patch("app.services.approval_service.async_session")
    async def test_list_approvals(self, mock_session_factory):
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = []

        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)
        mock_session.execute = AsyncMock(return_value=mock_result)
        mock_session_factory.return_value = mock_session

        from app.services.approval_service import list_approvals

        result = await list_approvals()
        assert isinstance(result, list)
