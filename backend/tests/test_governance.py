"""Tests for Phase 5: Advanced Governance services.

Covers:
  - TieredApprovalService — tier evaluation, threshold management, analytics
  - AuditAnalyticsService — timeline, anomaly detection, action breakdown
  - AccountabilityReportService — report generation, stats
  - RollbackService — action recording, execution, history
  - MultiOwnerService — owner CRUD, permissions, roles
  - CEO tool dispatch for all 15 Phase 5 tools
"""

import json
import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock, patch


# ---------------------------------------------------------------------------
# Tiered Approval Service
# ---------------------------------------------------------------------------

class TestTieredApprovalService:

    def _make_service(self):
        from app.services.tiered_approval import TieredApprovalService
        svc = TieredApprovalService()
        svc._loaded = True
        return svc

    def test_evaluate_low_tier(self):
        svc = self._make_service()
        decision = svc.evaluate("general", 5.0)
        assert decision.tier == "low"
        assert decision.action == "auto_approve"

    def test_evaluate_medium_tier(self):
        svc = self._make_service()
        decision = svc.evaluate("general", 50.0)
        assert decision.tier == "medium"
        assert decision.action == "ceo_decide"

    def test_evaluate_high_tier(self):
        svc = self._make_service()
        decision = svc.evaluate("general", 200.0)
        assert decision.tier == "high"
        assert decision.action == "owner_approval"

    def test_evaluate_trade_category(self):
        svc = self._make_service()
        # trade: [50, 500]
        decision = svc.evaluate("trade", 30.0)
        assert decision.tier == "low"
        decision = svc.evaluate("trade", 200.0)
        assert decision.tier == "medium"
        decision = svc.evaluate("trade", 1000.0)
        assert decision.tier == "high"

    def test_evaluate_company_creation_always_high(self):
        svc = self._make_service()
        # company_creation: [0, 0] — always owner approval
        decision = svc.evaluate("company_creation", 0.0)
        assert decision.tier == "high"
        assert decision.action == "owner_approval"

    @pytest.mark.asyncio
    async def test_set_thresholds(self):
        svc = self._make_service()
        with patch.object(svc, "_persist", new_callable=AsyncMock):
            result = await svc.set_thresholds("custom_cat", 25.0, 250.0)
        assert result["updated"] is True
        assert result["category"] == "custom_cat"

        # Verify new thresholds apply
        decision = svc.evaluate("custom_cat", 20.0)
        assert decision.tier == "low"
        decision = svc.evaluate("custom_cat", 100.0)
        assert decision.tier == "medium"
        decision = svc.evaluate("custom_cat", 300.0)
        assert decision.tier == "high"

    @pytest.mark.asyncio
    async def test_set_thresholds_validation(self):
        svc = self._make_service()
        result = await svc.set_thresholds("x", -1, 10)
        assert "error" in result
        result = await svc.set_thresholds("x", 100, 50)
        assert "error" in result

    def test_get_thresholds_all(self):
        svc = self._make_service()
        result = svc.get_thresholds()
        assert "trade" in result
        assert "general" in result

    def test_get_thresholds_specific(self):
        svc = self._make_service()
        result = svc.get_thresholds("trade")
        assert result["low_max_usd"] == 50.0
        assert result["medium_max_usd"] == 500.0

    def test_get_thresholds_unknown(self):
        svc = self._make_service()
        result = svc.get_thresholds("nonexistent")
        assert "error" in result

    def test_get_analytics(self):
        svc = self._make_service()
        # Make some decisions first
        svc.evaluate("trade", 10.0)
        svc.evaluate("trade", 200.0)
        svc.evaluate("general", 500.0)
        analytics = svc.get_analytics()
        assert analytics["total_decisions"] == 3
        assert analytics["tier_distribution"]["low"] >= 1

    @pytest.mark.asyncio
    async def test_load_from_redis(self):
        from app.services.tiered_approval import TieredApprovalService
        svc = TieredApprovalService()
        mock_redis = AsyncMock()
        mock_redis.get = AsyncMock(return_value=json.dumps({
            "thresholds": {"trade": [100, 1000]},
            "decisions": [],
        }))
        mock_redis.aclose = AsyncMock()
        with patch("redis.asyncio.from_url", return_value=mock_redis):
            await svc.load_from_redis()
        assert svc._thresholds["trade"] == [100, 1000]


# ---------------------------------------------------------------------------
# Audit Analytics Service
# ---------------------------------------------------------------------------

class TestAuditAnalyticsService:

    def _make_entries(self, count=20, hours_spread=24):
        now = datetime.now(timezone.utc)
        entries = []
        for i in range(count):
            ts = now - timedelta(hours=hours_spread * i / count)
            entries.append({
                "timestamp": ts.isoformat(),
                "agent_id": "ceo-1",
                "agent_name": "CEO",
                "action": "create_company" if i % 3 == 0 else "hire_agent",
                "arguments": {},
                "result_summary": "ok",
                "success": i % 5 != 0,  # 80% success rate
                "source": "tool_call",
            })
        return entries

    @pytest.mark.asyncio
    async def test_get_timeline(self):
        from app.services.audit_analytics import AuditAnalyticsService
        svc = AuditAnalyticsService()
        entries = self._make_entries(20, 12)
        with patch.object(svc, "_get_entries", new_callable=AsyncMock, return_value=entries):
            result = await svc.get_timeline(hours=24, bucket_minutes=60)
        assert "timeline" in result
        assert result["hours"] == 24

    @pytest.mark.asyncio
    async def test_detect_anomalies_not_enough_data(self):
        from app.services.audit_analytics import AuditAnalyticsService
        svc = AuditAnalyticsService()
        with patch.object(svc, "_get_entries", new_callable=AsyncMock, return_value=[]):
            result = await svc.detect_anomalies()
        assert result["anomalies"] == []

    @pytest.mark.asyncio
    async def test_detect_anomalies_with_data(self):
        from app.services.audit_analytics import AuditAnalyticsService
        svc = AuditAnalyticsService()
        # Create a spike: many actions in one hour, few in others
        now = datetime.now(timezone.utc)
        entries = []
        # Normal hours: 2 entries each
        for h in range(10):
            ts = now - timedelta(hours=h + 1)
            for _ in range(2):
                entries.append({
                    "timestamp": ts.isoformat(),
                    "agent_id": "ceo",
                    "agent_name": "CEO",
                    "action": "test",
                    "success": True,
                    "source": "tool_call",
                })
        # Spike hour: 50 entries
        spike_ts = now - timedelta(hours=5, minutes=30)
        for _ in range(50):
            entries.append({
                "timestamp": spike_ts.isoformat(),
                "agent_id": "ceo",
                "agent_name": "CEO",
                "action": "bulk_action",
                "success": True,
                "source": "tool_call",
            })
        with patch.object(svc, "_get_entries", new_callable=AsyncMock, return_value=entries):
            result = await svc.detect_anomalies(sensitivity=1.5)
        assert "stats" in result
        assert result["stats"]["total_entries"] == len(entries)

    @pytest.mark.asyncio
    async def test_get_action_breakdown(self):
        from app.services.audit_analytics import AuditAnalyticsService
        svc = AuditAnalyticsService()
        entries = self._make_entries(10, 6)
        with patch.object(svc, "_get_entries", new_callable=AsyncMock, return_value=entries):
            result = await svc.get_action_breakdown(hours=24)
        assert "by_action" in result
        assert "by_agent" in result


# ---------------------------------------------------------------------------
# Accountability Report Service
# ---------------------------------------------------------------------------

class TestAccountabilityReportService:

    def _make_service(self):
        from app.services.accountability_report import AccountabilityReportService
        svc = AccountabilityReportService()
        svc._loaded = True
        return svc

    def _mock_entries(self, count=10):
        now = datetime.now(timezone.utc)
        return [
            {
                "timestamp": (now - timedelta(hours=i)).isoformat(),
                "agent_id": "ceo",
                "agent_name": "CEO",
                "action": f"action_{i % 3}",
                "arguments": {},
                "result_summary": "ok",
                "success": i % 4 != 0,
                "source": "tool_call" if i % 2 == 0 else "telegram",
            }
            for i in range(count)
        ]

    @pytest.mark.asyncio
    async def test_generate_report(self):
        svc = self._make_service()
        entries = self._mock_entries(20)
        with patch("app.services.audit_log.audit_log.get_entries", new_callable=AsyncMock, return_value=entries), \
             patch("app.services.approval_service.list_approvals", new_callable=AsyncMock, return_value=[]), \
             patch.object(svc, "_persist", new_callable=AsyncMock):
            report = await svc.generate_report(period_hours=48)

        assert "summary" in report
        assert report["summary"]["total_actions"] > 0
        assert "action_breakdown" in report
        assert "recommendations" in report
        assert len(svc._reports) == 1

    @pytest.mark.asyncio
    async def test_generate_report_with_approvals(self):
        svc = self._make_service()
        now = datetime.now(timezone.utc)
        entries = self._mock_entries(5)
        approvals = [
            {"requested_at": (now - timedelta(hours=1)).isoformat(), "status": "approved"},
            {"requested_at": (now - timedelta(hours=2)).isoformat(), "status": "rejected"},
            {"requested_at": (now - timedelta(hours=3)).isoformat(), "status": "pending"},
        ]
        with patch("app.services.audit_log.audit_log.get_entries", new_callable=AsyncMock, return_value=entries), \
             patch("app.services.approval_service.list_approvals", new_callable=AsyncMock, return_value=approvals), \
             patch.object(svc, "_persist", new_callable=AsyncMock):
            report = await svc.generate_report(period_hours=48)

        stats = report["approval_stats"]
        assert stats["approved"] == 1
        assert stats["rejected"] == 1

    def test_get_stats_empty(self):
        svc = self._make_service()
        stats = svc.get_stats()
        assert stats["total_reports"] == 0
        assert stats["latest_report"] is None

    def test_recommendations_high_failure(self):
        svc = self._make_service()
        action_stats = {"bad_action": {"count": 10, "successes": 2, "failures": 8}}
        recs = svc._generate_recommendations(action_stats, {"total_requests": 0}, 10)
        assert any("bad_action" in r for r in recs)

    def test_recommendations_high_rejection(self):
        svc = self._make_service()
        approval_stats = {"total_requests": 10, "rejected": 8, "pending": 0}
        recs = svc._generate_recommendations({}, approval_stats, 10)
        assert any("rejection" in r.lower() for r in recs)

    @pytest.mark.asyncio
    async def test_load_from_redis(self):
        from app.services.accountability_report import AccountabilityReportService
        svc = AccountabilityReportService()
        mock_redis = AsyncMock()
        mock_redis.get = AsyncMock(return_value=json.dumps([{"test": "report"}]))
        mock_redis.aclose = AsyncMock()
        with patch("redis.asyncio.from_url", return_value=mock_redis):
            await svc.load_from_redis()
        assert len(svc._reports) == 1


# ---------------------------------------------------------------------------
# Rollback Service
# ---------------------------------------------------------------------------

class TestRollbackService:

    def _make_service(self):
        from app.services.rollback_service import RollbackService
        svc = RollbackService()
        svc._loaded = True
        return svc

    @pytest.mark.asyncio
    async def test_record_reversible_action(self):
        svc = self._make_service()
        with patch.object(svc, "_persist", new_callable=AsyncMock):
            result = await svc.record_action(
                action="hire_agent",
                args={"agent_id": "a1", "name": "Test Agent"},
                agent_id="ceo",
                description="Hired Test Agent",
            )
        assert result is not None
        assert result["original_action"] == "hire_agent"
        assert result["inverse_action"] == "fire_agent"
        assert result["status"] == "available"

    @pytest.mark.asyncio
    async def test_record_non_reversible_action(self):
        svc = self._make_service()
        result = await svc.record_action(
            action="send_message",
            args={},
            agent_id="ceo",
            description="Sent a message",
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_execute_rollback(self):
        svc = self._make_service()
        with patch.object(svc, "_persist", new_callable=AsyncMock):
            entry = await svc.record_action(
                action="create_company",
                args={"company_id": "c1"},
                agent_id="ceo",
                description="Created company",
            )
            result = await svc.execute_rollback(entry["id"])
        assert result["status"] == "rolled_back"
        assert result["inverse_action"] == "dissolve_company"

    @pytest.mark.asyncio
    async def test_execute_rollback_not_found(self):
        svc = self._make_service()
        result = await svc.execute_rollback("nonexistent")
        assert "error" in result

    @pytest.mark.asyncio
    async def test_execute_rollback_already_done(self):
        svc = self._make_service()
        with patch.object(svc, "_persist", new_callable=AsyncMock):
            entry = await svc.record_action(
                action="hire_agent",
                args={"agent_id": "a1"},
                agent_id="ceo",
                description="Hired",
            )
            await svc.execute_rollback(entry["id"])
            result = await svc.execute_rollback(entry["id"])
        assert "error" in result

    def test_list_actions(self):
        svc = self._make_service()
        svc._actions = [
            {"id": "1", "status": "available", "original_action": "hire_agent"},
            {"id": "2", "status": "rolled_back", "original_action": "fire_agent"},
        ]
        assert len(svc.list_actions()) == 2
        assert len(svc.list_actions(status="available")) == 1

    def test_get_history(self):
        svc = self._make_service()
        svc._actions = [
            {"id": "1", "status": "available", "original_action": "hire_agent"},
            {"id": "2", "status": "rolled_back", "original_action": "fire_agent"},
            {"id": "3", "status": "available", "original_action": "hire_agent"},
        ]
        history = svc.get_history()
        assert history["total_tracked"] == 3
        assert history["available"] == 2
        assert history["rolled_back"] == 1

    @pytest.mark.asyncio
    async def test_record_set_auto_trade_mode(self):
        svc = self._make_service()
        with patch.object(svc, "_persist", new_callable=AsyncMock):
            result = await svc.record_action(
                action="set_auto_trade_mode",
                args={"enabled": True},
                agent_id="ceo",
                description="Enabled auto-trade",
            )
        assert result["inverse_args"]["enabled"] is False

    @pytest.mark.asyncio
    async def test_load_from_redis(self):
        from app.services.rollback_service import RollbackService
        svc = RollbackService()
        mock_redis = AsyncMock()
        mock_redis.get = AsyncMock(return_value=json.dumps([
            {"id": "rb-1", "status": "available", "original_action": "hire_agent"}
        ]))
        mock_redis.aclose = AsyncMock()
        with patch("redis.asyncio.from_url", return_value=mock_redis):
            await svc.load_from_redis()
        assert len(svc._actions) == 1


# ---------------------------------------------------------------------------
# Multi-Owner Service
# ---------------------------------------------------------------------------

class TestMultiOwnerService:

    def _make_service(self):
        from app.services.multi_owner import MultiOwnerService
        svc = MultiOwnerService()
        svc._loaded = True
        return svc

    @pytest.mark.asyncio
    async def test_add_owner(self):
        svc = self._make_service()
        with patch.object(svc, "_persist", new_callable=AsyncMock):
            result = await svc.add_owner("owner-1", "Alice", "admin", "alice@test.com")
        assert result["action"] == "added"
        assert result["owner"]["role"] == "admin"
        assert "approve" in result["owner"]["permissions"]

    @pytest.mark.asyncio
    async def test_add_owner_invalid_role(self):
        svc = self._make_service()
        result = await svc.add_owner("owner-1", "Alice", "superadmin")
        assert "error" in result

    @pytest.mark.asyncio
    async def test_update_owner(self):
        svc = self._make_service()
        with patch.object(svc, "_persist", new_callable=AsyncMock):
            await svc.add_owner("owner-1", "Alice", "viewer")
            result = await svc.add_owner("owner-1", "Alice", "admin")
        assert result["action"] == "updated"
        assert result["owner"]["role"] == "admin"

    @pytest.mark.asyncio
    async def test_remove_owner(self):
        svc = self._make_service()
        with patch.object(svc, "_persist", new_callable=AsyncMock):
            await svc.add_owner("owner-1", "Alice", "admin")
            await svc.add_owner("owner-2", "Bob", "admin")
            result = await svc.remove_owner("owner-1")
        assert "removed" in result
        assert len(svc.list_owners()) == 1

    @pytest.mark.asyncio
    async def test_cannot_remove_last_admin(self):
        svc = self._make_service()
        with patch.object(svc, "_persist", new_callable=AsyncMock):
            await svc.add_owner("owner-1", "Alice", "admin")
            result = await svc.remove_owner("owner-1")
        assert "error" in result
        assert "last admin" in result["error"]

    @pytest.mark.asyncio
    async def test_remove_nonexistent_owner(self):
        svc = self._make_service()
        result = await svc.remove_owner("ghost")
        assert "error" in result

    def test_list_owners(self):
        svc = self._make_service()
        svc._owners = {
            "o1": {"id": "o1", "name": "A", "role": "admin", "permissions": []},
            "o2": {"id": "o2", "name": "B", "role": "viewer", "permissions": []},
        }
        assert len(svc.list_owners()) == 2
        assert len(svc.list_owners(role="admin")) == 1

    def test_has_permission(self):
        svc = self._make_service()
        svc._owners = {
            "o1": {"id": "o1", "name": "A", "role": "admin", "permissions": ["approve", "reject", "configure"]},
            "o2": {"id": "o2", "name": "B", "role": "viewer", "permissions": ["view_reports"]},
        }
        assert svc.has_permission("o1", "approve") is True
        assert svc.has_permission("o2", "approve") is False
        assert svc.has_permission("o2", "view_reports") is True
        assert svc.has_permission("ghost", "approve") is False

    def test_get_approvers(self):
        svc = self._make_service()
        svc._owners = {
            "o1": {"id": "o1", "name": "A", "role": "admin", "permissions": []},
            "o2": {"id": "o2", "name": "B", "role": "approver", "permissions": []},
            "o3": {"id": "o3", "name": "C", "role": "viewer", "permissions": []},
        }
        approvers = svc.get_approvers()
        assert len(approvers) == 2

    def test_get_permissions(self):
        svc = self._make_service()
        svc._owners = {
            "o1": {"id": "o1", "name": "A", "role": "admin", "permissions": ["approve"]},
        }
        result = svc.get_permissions("o1")
        assert result["role"] == "admin"
        result = svc.get_permissions("ghost")
        assert "error" in result

    def test_get_stats(self):
        svc = self._make_service()
        svc._owners = {
            "o1": {"id": "o1", "name": "A", "role": "admin", "permissions": ["approve"]},
            "o2": {"id": "o2", "name": "B", "role": "viewer", "permissions": []},
        }
        stats = svc.get_stats()
        assert stats["total_owners"] == 2
        assert stats["by_role"]["admin"] == 1

    @pytest.mark.asyncio
    async def test_load_from_redis(self):
        from app.services.multi_owner import MultiOwnerService
        svc = MultiOwnerService()
        mock_redis = AsyncMock()
        mock_redis.get = AsyncMock(return_value=json.dumps({
            "o1": {"id": "o1", "name": "A", "role": "admin", "permissions": []}
        }))
        mock_redis.aclose = AsyncMock()
        with patch("redis.asyncio.from_url", return_value=mock_redis):
            await svc.load_from_redis()
        assert len(svc._owners) == 1


# ---------------------------------------------------------------------------
# CEO Tool Dispatch (Phase 5 tools)
# ---------------------------------------------------------------------------

class TestCEOPhase5Tools:

    @pytest.fixture
    def ceo(self):
        from app.agents.ceo import CEOAgent
        agent = CEOAgent.__new__(CEOAgent)
        agent.agent_id = "ceo-test"
        agent.name = "CEO"
        return agent

    @pytest.mark.asyncio
    async def test_evaluate_approval_tier(self, ceo):
        with patch("app.services.tiered_approval.tiered_approval_service") as mock_svc:
            from dataclasses import asdict
            from app.services.tiered_approval import TierDecision
            mock_svc.evaluate.return_value = TierDecision(
                category="trade", estimated_cost_usd=100.0,
                tier="medium", action="ceo_decide", thresholds=[50, 500],
            )
            result = await ceo._tool_evaluate_approval_tier(
                {"category": "trade", "estimated_cost_usd": 100}
            )
        data = json.loads(result)
        assert data["tier"] == "medium"

    @pytest.mark.asyncio
    async def test_set_approval_thresholds(self, ceo):
        with patch("app.services.tiered_approval.tiered_approval_service") as mock_svc:
            mock_svc.set_thresholds = AsyncMock(return_value={"updated": True})
            result = await ceo._tool_set_approval_thresholds(
                {"category": "trade", "low_max_usd": 100, "medium_max_usd": 1000}
            )
        assert json.loads(result)["updated"] is True

    @pytest.mark.asyncio
    async def test_get_approval_thresholds(self, ceo):
        with patch("app.services.tiered_approval.tiered_approval_service") as mock_svc:
            mock_svc.get_thresholds.return_value = {"trade": {"low_max_usd": 50, "medium_max_usd": 500}}
            result = await ceo._tool_get_approval_thresholds({})
        assert "trade" in json.loads(result)

    @pytest.mark.asyncio
    async def test_get_tier_analytics(self, ceo):
        with patch("app.services.tiered_approval.tiered_approval_service") as mock_svc:
            mock_svc.get_analytics.return_value = {"total_decisions": 10}
            result = await ceo._tool_get_tier_analytics({})
        assert json.loads(result)["total_decisions"] == 10

    @pytest.mark.asyncio
    async def test_get_audit_timeline(self, ceo):
        with patch("app.services.audit_analytics.audit_analytics_service") as mock_svc:
            mock_svc.get_timeline = AsyncMock(return_value={"timeline": {}, "hours": 24})
            result = await ceo._tool_get_audit_timeline({"hours": 24})
        assert json.loads(result)["hours"] == 24

    @pytest.mark.asyncio
    async def test_detect_audit_anomalies(self, ceo):
        with patch("app.services.audit_analytics.audit_analytics_service") as mock_svc:
            mock_svc.detect_anomalies = AsyncMock(return_value={"anomalies": []})
            result = await ceo._tool_detect_audit_anomalies({})
        assert json.loads(result)["anomalies"] == []

    @pytest.mark.asyncio
    async def test_get_action_breakdown(self, ceo):
        with patch("app.services.audit_analytics.audit_analytics_service") as mock_svc:
            mock_svc.get_action_breakdown = AsyncMock(return_value={"by_action": {}})
            result = await ceo._tool_get_action_breakdown({})
        assert "by_action" in json.loads(result)

    @pytest.mark.asyncio
    async def test_generate_accountability_report(self, ceo):
        with patch("app.services.accountability_report.accountability_report_service") as mock_svc:
            mock_svc.generate_report = AsyncMock(return_value={"summary": {"total_actions": 5}})
            result = await ceo._tool_generate_accountability_report({})
        assert json.loads(result)["summary"]["total_actions"] == 5

    @pytest.mark.asyncio
    async def test_get_accountability_stats(self, ceo):
        with patch("app.services.accountability_report.accountability_report_service") as mock_svc:
            mock_svc.get_stats.return_value = {"total_reports": 2}
            result = await ceo._tool_get_accountability_stats({})
        assert json.loads(result)["total_reports"] == 2

    @pytest.mark.asyncio
    async def test_list_rollback_actions(self, ceo):
        with patch("app.services.rollback_service.rollback_service") as mock_svc:
            mock_svc.list_actions.return_value = [{"id": "rb-1"}]
            result = await ceo._tool_list_rollback_actions({})
        assert len(json.loads(result)) == 1

    @pytest.mark.asyncio
    async def test_request_rollback(self, ceo):
        with patch("app.services.rollback_service.rollback_service") as mock_svc:
            mock_svc.execute_rollback = AsyncMock(return_value={"status": "rolled_back"})
            result = await ceo._tool_request_rollback({"rollback_id": "rb-1"})
        assert json.loads(result)["status"] == "rolled_back"

    @pytest.mark.asyncio
    async def test_get_rollback_history(self, ceo):
        with patch("app.services.rollback_service.rollback_service") as mock_svc:
            mock_svc.get_history.return_value = {"total_tracked": 5}
            result = await ceo._tool_get_rollback_history({})
        assert json.loads(result)["total_tracked"] == 5

    @pytest.mark.asyncio
    async def test_list_owners(self, ceo):
        with patch("app.services.multi_owner.multi_owner_service") as mock_svc:
            mock_svc.list_owners.return_value = [{"id": "o1", "name": "Alice"}]
            result = await ceo._tool_list_owners({})
        assert len(json.loads(result)) == 1

    @pytest.mark.asyncio
    async def test_add_owner(self, ceo):
        with patch("app.services.multi_owner.multi_owner_service") as mock_svc:
            mock_svc.add_owner = AsyncMock(return_value={"action": "added"})
            result = await ceo._tool_add_owner(
                {"owner_id": "o1", "name": "Alice", "role": "admin"}
            )
        assert json.loads(result)["action"] == "added"

    @pytest.mark.asyncio
    async def test_get_owner_permissions(self, ceo):
        with patch("app.services.multi_owner.multi_owner_service") as mock_svc:
            mock_svc.get_permissions.return_value = {"role": "admin", "permissions": ["approve"]}
            result = await ceo._tool_get_owner_permissions({"owner_id": "o1"})
        assert json.loads(result)["role"] == "admin"


# ---------------------------------------------------------------------------
# CEO Tool Schema & Tool Count
# ---------------------------------------------------------------------------

class TestCEOPhase5ToolCount:

    @pytest.mark.asyncio
    async def test_ceo_has_76_tools(self):
        """CEO should have 76 tools after Phase 5 (61 Phase 4 + 15 Phase 5)."""
        from app.agents.ceo import CEOAgent
        ceo = CEOAgent.__new__(CEOAgent)
        ceo.agent_id = "ceo-test"
        ceo.name = "CEO"
        ceo.role = "ceo"
        ceo.model_tier = "smart"
        ceo.model_id = None
        ceo._tools_list = [
            "create_company", "list_companies", "get_company_details",
            "hire_agent", "fire_agent", "list_agents", "send_message",
            "broadcast_message", "assign_task", "get_task_status",
            "delegate_to_agent", "check_budget_status", "generate_daily_report",
            "schedule_recurring_task", "cancel_scheduled_task", "list_scheduled_tasks",
            "update_schedule", "check_budget_status", "generate_daily_report",
            "initiate_vote", "get_vote_results", "list_votes",
            "set_company_kpi", "get_company_kpis", "get_all_kpis", "update_kpi_value",
            "list_delegations",
            "set_auto_trade_mode", "get_auto_trade_status",
            "calculate_position_size", "assess_portfolio_risk", "update_risk_limits",
            "record_trade_journal", "query_trade_journal", "get_trade_stats",
            "create_market_alert", "list_market_alerts", "cancel_market_alert",
            "analyze_prompts", "get_prompt_recommendations",
            "analyze_tool_usage", "get_agent_tool_profile",
            "add_knowledge", "search_knowledge", "get_knowledge_stats",
            "get_error_patterns", "get_error_summary", "scan_errors",
            "create_ab_test", "record_ab_result", "get_ab_results", "list_ab_tests",
            # Phase 5
            "evaluate_approval_tier", "set_approval_thresholds",
            "get_approval_thresholds", "get_tier_analytics",
            "get_audit_timeline", "detect_audit_anomalies", "get_action_breakdown",
            "generate_accountability_report", "get_accountability_stats",
            "list_rollback_actions", "request_rollback", "get_rollback_history",
            "list_owners", "add_owner", "get_owner_permissions",
        ]
        # Verify Phase 5 tools are in the schema
        ceo.system_prompt = "test"
        ceo.tools = []
        ceo.config = {}
        ceo.company_id = None
        ceo.sandbox_enabled = False
        ceo.browser_enabled = False
        ceo.skills = None
        ceo.network_enabled = False
        ceo.standing_instructions = None
        ceo.work_interval_seconds = 3600
        ceo._conversations = []
        ceo._history_loaded = False
        import asyncio
        ceo._run_lock = asyncio.Lock()
        schemas = ceo._get_tools_schema()
        tool_names = [s["function"]["name"] for s in schemas]
        phase5_tools = [
            "evaluate_approval_tier", "set_approval_thresholds",
            "get_approval_thresholds", "get_tier_analytics",
            "get_audit_timeline", "detect_audit_anomalies", "get_action_breakdown",
            "generate_accountability_report", "get_accountability_stats",
            "list_rollback_actions", "request_rollback", "get_rollback_history",
            "list_owners", "add_owner", "get_owner_permissions",
        ]
        for tool in phase5_tools:
            assert tool in tool_names, f"Missing Phase 5 tool: {tool}"
