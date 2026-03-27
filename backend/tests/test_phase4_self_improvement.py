"""Tests for Phase 4: CEO Self-Improvement.

Tests:
- PromptOptimizer: snapshot capture, analysis, recommendations
- ToolUsageOptimizer: analysis, agent profiles, recommendations
- KnowledgeBase: add, search, stats, delete
- ErrorPatternDetector: record, classify, patterns, summary, severity
- ABTestingService: create, record results, determine winner, list
- CEO Phase 4 tool integration: tools list and schemas
"""

import json
import pytest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch


# ========================== PromptOptimizer Tests ==========================


class TestPromptOptimizer:
    """Tests for the prompt optimization service."""

    def _make_optimizer(self):
        from app.services.prompt_optimizer import PromptOptimizer
        return PromptOptimizer()

    def test_init(self):
        po = self._make_optimizer()
        assert po._snapshots == {}
        assert po._suggestions == []

    @pytest.mark.asyncio
    async def test_capture_snapshot_no_agent(self):
        po = self._make_optimizer()
        with patch("app.db.database.async_session") as mock_sess:
            mock_ctx = AsyncMock()
            mock_ctx.get.return_value = None
            mock_sess.return_value.__aenter__ = AsyncMock(return_value=mock_ctx)
            mock_sess.return_value.__aexit__ = AsyncMock(return_value=False)
            result = await po.capture_snapshot("nonexistent")
            assert result is None

    @pytest.mark.asyncio
    async def test_capture_snapshot_no_performance(self):
        po = self._make_optimizer()
        mock_agent = MagicMock()
        mock_agent.name = "TestAgent"
        mock_agent.system_prompt = "Test prompt"

        with patch("app.db.database.async_session") as mock_sess, \
             patch("app.services.performance_tracker.performance_tracker") as mock_perf:
            mock_ctx = AsyncMock()
            mock_ctx.get.return_value = mock_agent
            mock_sess.return_value.__aenter__ = AsyncMock(return_value=mock_ctx)
            mock_sess.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_perf.get_score = AsyncMock(return_value={"total_calls": 0, "score": 0, "grade": "N/A", "success_rate": 0})
            result = await po.capture_snapshot("agent1")
            assert result is None

    @pytest.mark.asyncio
    async def test_capture_snapshot_success(self):
        po = self._make_optimizer()
        mock_agent = MagicMock()
        mock_agent.name = "AnalystBot"
        mock_agent.system_prompt = "Analyze forex markets daily"

        with patch("app.db.database.async_session") as mock_sess, \
             patch("app.services.performance_tracker.performance_tracker") as mock_perf:
            mock_ctx = AsyncMock()
            mock_ctx.get.return_value = mock_agent
            mock_sess.return_value.__aenter__ = AsyncMock(return_value=mock_ctx)
            mock_sess.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_perf.get_score = AsyncMock(return_value={
                "total_calls": 10, "score": 85, "grade": "B",
                "success_rate": 90.0,
            })
            # Mock _persist
            po._persist = AsyncMock()

            result = await po.capture_snapshot("agent1")
            assert result is not None
            assert result["agent_name"] == "AnalystBot"
            assert result["score"] == 85
            assert result["grade"] == "B"
            assert "agent1" in po._snapshots
            assert len(po._snapshots["agent1"]) == 1

    @pytest.mark.asyncio
    async def test_analyze_empty(self):
        po = self._make_optimizer()
        analysis = await po.analyze()
        assert analysis["total_agents_tracked"] == 0
        assert analysis["top_performers"] == []

    @pytest.mark.asyncio
    async def test_analyze_with_data(self):
        po = self._make_optimizer()
        now = datetime.now(timezone.utc).isoformat()

        # Add a top performer
        po._snapshots["agent_good"] = [
            {"agent_id": "agent_good", "agent_name": "GoodBot", "system_prompt": "x" * 500,
             "score": 92, "grade": "A", "success_rate": 95.0, "total_tasks": 50, "timestamp": now}
        ]
        # Add an underperformer
        po._snapshots["agent_bad"] = [
            {"agent_id": "agent_bad", "agent_name": "BadBot", "system_prompt": "y" * 50,
             "score": 30, "grade": "F", "success_rate": 30.0, "total_tasks": 20, "timestamp": now}
        ]

        analysis = await po.analyze()
        assert analysis["total_agents_tracked"] == 2
        assert len(analysis["top_performers"]) == 1
        assert analysis["top_performers"][0]["agent_id"] == "agent_good"
        assert len(analysis["underperformers"]) == 1
        assert analysis["underperformers"][0]["agent_id"] == "agent_bad"
        assert len(analysis["recommendations"]) >= 1

    @pytest.mark.asyncio
    async def test_analyze_trend_detection(self):
        po = self._make_optimizer()
        now = datetime.now(timezone.utc).isoformat()

        # Agent with declining trend
        po._snapshots["declining"] = [
            {"agent_id": "declining", "agent_name": "Decliner", "system_prompt": "x" * 200,
             "score": 80, "grade": "B", "success_rate": 85.0, "total_tasks": 10, "timestamp": now},
            {"agent_id": "declining", "agent_name": "Decliner", "system_prompt": "x" * 200,
             "score": 75, "grade": "C", "success_rate": 78.0, "total_tasks": 15, "timestamp": now},
            {"agent_id": "declining", "agent_name": "Decliner", "system_prompt": "x" * 200,
             "score": 55, "grade": "D", "success_rate": 60.0, "total_tasks": 20, "timestamp": now},
            {"agent_id": "declining", "agent_name": "Decliner", "system_prompt": "x" * 200,
             "score": 45, "grade": "F", "success_rate": 50.0, "total_tasks": 25, "timestamp": now},
        ]

        analysis = await po.analyze()
        assert len(analysis["declining"]) >= 1

    @pytest.mark.asyncio
    async def test_get_agent_history(self):
        po = self._make_optimizer()
        po._snapshots["agent1"] = [{"score": 80}, {"score": 85}]
        history = await po.get_agent_history("agent1")
        assert len(history) == 2
        assert await po.get_agent_history("nonexistent") == []


# ========================== ToolUsageOptimizer Tests ==========================


class TestToolUsageOptimizer:
    """Tests for the tool usage optimization service."""

    def _make_optimizer(self):
        from app.services.tool_usage_optimizer import ToolUsageOptimizer
        return ToolUsageOptimizer()

    @pytest.mark.asyncio
    async def test_analyze_no_data(self):
        opt = self._make_optimizer()
        with patch("app.services.tool_analytics.tool_analytics") as mock_ta:
            mock_ta.get_stats.return_value = {"total_calls": 0, "tools": {}}
            result = await opt.analyze()
            assert result["status"] == "insufficient_data"

    @pytest.mark.asyncio
    async def test_analyze_with_tools(self):
        opt = self._make_optimizer()
        with patch("app.services.tool_analytics.tool_analytics") as mock_ta:
            mock_ta.get_stats.return_value = {
                "total_calls": 200,
                "unique_tools_used": 5,
                "tools": {
                    "check_status": {"total_calls": 80, "successes": 79, "failures": 1, "success_rate": 98.8, "avg_duration_ms": 150},
                    "assign_task": {"total_calls": 50, "successes": 45, "failures": 5, "success_rate": 90.0, "avg_duration_ms": 300},
                    "get_costs": {"total_calls": 40, "successes": 40, "failures": 0, "success_rate": 100.0, "avg_duration_ms": 100},
                    "broken_tool": {"total_calls": 20, "successes": 8, "failures": 12, "success_rate": 40.0, "avg_duration_ms": 2000},
                    "slow_tool": {"total_calls": 10, "successes": 9, "failures": 1, "success_rate": 90.0, "avg_duration_ms": 8000},
                },
            }
            # Mock _persist
            opt._persist = AsyncMock()

            result = await opt.analyze()
            assert result["status"] == "analyzed"
            assert result["total_calls"] == 200
            # broken_tool should be a disable candidate (60% failure rate)
            disable_names = [d["tool_name"] for d in result["disable_candidates"]]
            assert "broken_tool" in disable_names
            # slow_tool should be an optimize candidate
            optimize_names = [o["tool_name"] for o in result["optimize"]]
            assert "slow_tool" in optimize_names
            # check_status should be a top tool
            top_names = [t["tool_name"] for t in result["top_tools"]]
            assert "check_status" in top_names

    @pytest.mark.asyncio
    async def test_analyze_unused_tools(self):
        opt = self._make_optimizer()
        with patch("app.services.tool_analytics.tool_analytics") as mock_ta:
            mock_ta.get_stats.return_value = {
                "total_calls": 100,
                "unique_tools_used": 2,
                "tools": {
                    "active_tool": {"total_calls": 99, "successes": 95, "failures": 4, "success_rate": 96.0, "avg_duration_ms": 200},
                    "unused_tool": {"total_calls": 1, "successes": 1, "failures": 0, "success_rate": 100.0, "avg_duration_ms": 50},
                },
            }
            opt._persist = AsyncMock()
            result = await opt.analyze()
            disable_names = [d["tool_name"] for d in result["disable_candidates"]]
            assert "unused_tool" in disable_names

    @pytest.mark.asyncio
    async def test_get_agent_tool_profile(self):
        opt = self._make_optimizer()
        with patch("app.services.tool_analytics.tool_analytics") as mock_ta:
            mock_ta.get_agent_stats.return_value = {
                "agent_id": "agent1",
                "total_calls": 30,
                "tools": {"check_status": 15, "assign_task": 10, "get_costs": 5},
            }
            result = await opt.get_agent_tool_profile("agent1")
            assert result["total_calls"] == 30
            assert result["unique_tools"] == 3
            assert result["most_used"][0]["tool"] == "check_status"

    @pytest.mark.asyncio
    async def test_get_agent_tool_profile_no_data(self):
        opt = self._make_optimizer()
        with patch("app.services.tool_analytics.tool_analytics") as mock_ta:
            mock_ta.get_agent_stats.return_value = {
                "agent_id": "agent1",
                "total_calls": 0,
                "tools": {},
            }
            result = await opt.get_agent_tool_profile("agent1")
            assert result["status"] == "no_data"

    def test_build_summary(self):
        opt = self._make_optimizer()
        summary = opt._build_summary(
            [{"tool_name": "t1"}],
            [{"tool_name": "t2"}],
            [{"tool_name": "t3"}],
            [{"tool_name": "t4", "metrics": {"total_calls": 50, "success_rate": 99}}],
            500,
        )
        assert "500 total tool calls" in summary
        assert "1 tool(s) recommended for disabling" in summary


# ========================== KnowledgeBase Tests ==========================


class TestKnowledgeBase:
    """Tests for the shared knowledge base service."""

    def _make_kb(self):
        from app.services.knowledge_base import KnowledgeBase
        return KnowledgeBase()

    @pytest.mark.asyncio
    async def test_add_entry_success(self):
        kb = self._make_kb()
        with patch("app.db.database.async_session") as mock_sess:
            mock_ctx = AsyncMock()
            # Mock count query
            mock_count = MagicMock()
            mock_count.scalar.return_value = 5
            mock_ctx.execute.return_value = mock_count
            mock_ctx.commit = AsyncMock()
            mock_ctx.add = MagicMock()
            mock_sess.return_value.__aenter__ = AsyncMock(return_value=mock_ctx)
            mock_sess.return_value.__aexit__ = AsyncMock(return_value=False)

            result = await kb.add_entry(
                content="EUR/USD tends to reverse at round numbers",
                category="market_insights",
                tags=["forex", "eurusd"],
                importance=0.8,
            )
            assert result["success"] is True
            assert "entry_id" in result
            assert result["category"] == "market_insights"

    @pytest.mark.asyncio
    async def test_add_entry_failure(self):
        kb = self._make_kb()
        with patch("app.db.database.async_session", side_effect=Exception("DB down")):
            result = await kb.add_entry(content="test")
            assert result["success"] is False

    @pytest.mark.asyncio
    async def test_get_stats(self):
        kb = self._make_kb()
        with patch("app.db.database.async_session") as mock_sess:
            mock_ctx = AsyncMock()
            # Mock total count
            mock_total = MagicMock()
            mock_total.scalar.return_value = 42

            # Mock category query
            mock_cats = MagicMock()
            mock_cats.fetchall.return_value = [
                ("market_insights", 20),
                ("best_practices", 15),
                ("lessons_learned", 7),
            ]

            mock_ctx.execute = AsyncMock(side_effect=[mock_total, mock_cats])
            mock_sess.return_value.__aenter__ = AsyncMock(return_value=mock_ctx)
            mock_sess.return_value.__aexit__ = AsyncMock(return_value=False)

            stats = await kb.get_stats()
            assert stats["total_entries"] == 42
            assert stats["categories"]["market_insights"] == 20

    @pytest.mark.asyncio
    async def test_delete_entry(self):
        kb = self._make_kb()
        with patch("app.db.database.async_session") as mock_sess:
            mock_ctx = AsyncMock()
            mock_result = MagicMock()
            mock_result.rowcount = 1
            mock_ctx.execute.return_value = mock_result
            mock_ctx.commit = AsyncMock()
            mock_sess.return_value.__aenter__ = AsyncMock(return_value=mock_ctx)
            mock_sess.return_value.__aexit__ = AsyncMock(return_value=False)

            assert await kb.delete_entry("some-id") is True


# ========================== ErrorPatternDetector Tests ==========================


class TestErrorPatternDetector:
    """Tests for the error pattern detection service."""

    def _make_detector(self):
        from app.services.error_pattern_detector import ErrorPatternDetector
        return ErrorPatternDetector()

    def test_classify_timeout(self):
        det = self._make_detector()
        assert det._classify_error("Request timed out after 30s") == "timeout"
        assert det._classify_error("Deadline exceeded for API call") == "timeout"

    def test_classify_rate_limit(self):
        det = self._make_detector()
        assert det._classify_error("429 Too Many Requests") == "rate_limit"
        assert det._classify_error("Rate limit exceeded") == "rate_limit"

    def test_classify_permission(self):
        det = self._make_detector()
        assert det._classify_error("403 Forbidden") == "permission"
        assert det._classify_error("Unauthorized access") == "permission"

    def test_classify_connection(self):
        det = self._make_detector()
        assert det._classify_error("Connection refused") == "connection"
        assert det._classify_error("DNS resolution failed") == "connection"

    def test_classify_parse(self):
        det = self._make_detector()
        assert det._classify_error("JSON decode error") == "parse_error"
        assert det._classify_error("Invalid format in response") == "parse_error"

    def test_classify_api(self):
        det = self._make_detector()
        assert det._classify_error("503 Service Unavailable") == "api_error"

    def test_classify_unknown(self):
        det = self._make_detector()
        assert det._classify_error("Something weird happened") == "unknown"

    def test_normalize_message(self):
        det = self._make_detector()
        # UUIDs replaced
        norm = det._normalize_message("Error for agent 550e8400-e29b-41d4-a716-446655440000")
        assert "<ID>" in norm
        # Numbers replaced
        norm = det._normalize_message("Failed after 12345 retries")
        assert "<NUM>" in norm

    def test_record_error_creates_pattern(self):
        det = self._make_detector()
        det.record_error("Connection refused", agent_id="agent1", tool_name="check_status")
        assert len(det._patterns) == 1
        assert len(det._error_buffer) == 1

        pattern = list(det._patterns.values())[0]
        assert pattern["error_type"] == "connection"
        assert pattern["count"] == 1
        assert "agent1" in pattern["agent_ids"]

    def test_record_error_increments_count(self):
        det = self._make_detector()
        det.record_error("Connection refused", agent_id="agent1")
        det.record_error("Connection refused", agent_id="agent2")
        det.record_error("Connection refused", agent_id="agent3")

        assert len(det._patterns) == 1
        pattern = list(det._patterns.values())[0]
        assert pattern["count"] == 3
        assert len(pattern["agent_ids"]) == 3

    def test_severity_escalation(self):
        det = self._make_detector()
        # 5+ timeouts → high severity
        for i in range(6):
            det.record_error("Request timed out", agent_id=f"agent{i}")
        pattern = list(det._patterns.values())[0]
        assert pattern["severity"] == "high"

    def test_severity_critical(self):
        det = self._make_detector()
        # permission errors are critical-prone
        for i in range(4):
            det.record_error("403 Forbidden", agent_id=f"agent{i}")
        pattern = list(det._patterns.values())[0]
        assert pattern["severity"] == "critical"

    def test_suggested_fix_generated(self):
        det = self._make_detector()
        det.record_error("Rate limit exceeded", agent_id="agent1", tool_name="assign_task")
        pattern = list(det._patterns.values())[0]
        assert "rate" in pattern["suggested_fix"].lower() or "request" in pattern["suggested_fix"].lower()

    @pytest.mark.asyncio
    async def test_get_patterns_empty(self):
        det = self._make_detector()
        patterns = await det.get_patterns()
        assert patterns == []

    @pytest.mark.asyncio
    async def test_get_patterns_filtered(self):
        det = self._make_detector()
        det.record_error("Connection refused", agent_id="a1")
        det.record_error("JSON decode error", agent_id="a2")

        all_patterns = await det.get_patterns()
        assert len(all_patterns) == 2

        conn_patterns = await det.get_patterns(error_type="connection")
        assert len(conn_patterns) == 1

    @pytest.mark.asyncio
    async def test_get_summary_clean(self):
        det = self._make_detector()
        summary = await det.get_summary()
        assert summary["status"] == "clean"
        assert summary["total_patterns"] == 0

    @pytest.mark.asyncio
    async def test_get_summary_with_errors(self):
        det = self._make_detector()
        for i in range(10):
            det.record_error("Request timed out", agent_id=f"a{i}")
        det.record_error("JSON decode error", agent_id="a1")

        summary = await det.get_summary()
        assert summary["status"] in ("issues_detected", "minor_issues")
        assert summary["total_errors"] == 11
        assert "timeout" in summary["by_type"]

    @pytest.mark.asyncio
    async def test_scan_audit_log(self):
        det = self._make_detector()
        with patch("app.services.audit_log.audit_log") as mock_audit:
            mock_audit.get_entries = AsyncMock(return_value=[
                {"success": False, "result_summary": "Connection refused to API", "agent_id": "a1", "action": "check_status"},
                {"success": True, "result_summary": "OK", "agent_id": "a2", "action": "get_costs"},
                {"success": False, "result_summary": "Rate limit exceeded", "agent_id": "a3", "action": "assign_task"},
            ])
            count = await det.scan_audit_log()
            assert count == 2  # Only failures
            assert len(det._patterns) == 2

    def test_buffer_trimming(self):
        det = self._make_detector()
        det._max_buffer = 10
        for i in range(15):
            det.record_error(f"Error {i}", agent_id="agent1")
        assert len(det._error_buffer) <= 10


# ========================== ABTestingService Tests ==========================


class TestABTestingService:
    """Tests for the A/B testing service."""

    def _make_service(self):
        from app.services.ab_testing_service import ABTestingService
        return ABTestingService()

    @pytest.mark.asyncio
    async def test_create_experiment(self):
        svc = self._make_service()
        with patch.object(svc, '_get_agent_name', new_callable=AsyncMock, side_effect=["AgentA", "AgentB"]):
            svc._persist = AsyncMock()
            result = await svc.create_experiment(
                name="Flash vs Pro",
                hypothesis="Pro produces better signals",
                agent_a_id="agent_a",
                agent_a_desc="Using flash model",
                agent_b_id="agent_b",
                agent_b_desc="Using pro model",
                max_tasks=10,
            )
            assert result["name"] == "Flash vs Pro"
            assert result["status"] == "running"
            assert result["variant_a"]["agent_name"] == "AgentA"
            assert result["variant_b"]["agent_name"] == "AgentB"
            assert len(svc._experiments) == 1

    @pytest.mark.asyncio
    async def test_record_result(self):
        svc = self._make_service()
        svc._persist = AsyncMock()
        with patch.object(svc, '_get_agent_name', new_callable=AsyncMock, return_value="TestAgent"):
            exp = await svc.create_experiment(
                name="Test", hypothesis="Test", agent_a_id="a", agent_a_desc="A",
                agent_b_id="b", agent_b_desc="B", max_tasks=5,
            )

        result = await svc.record_result(exp["experiment_id"], "A", True, score=80, cost_usd=0.001, tokens=100, time_s=2.0)
        assert result["tasks_completed"] == 1
        assert result["total_cost_usd"] == 0.001

    @pytest.mark.asyncio
    async def test_record_result_invalid_experiment(self):
        svc = self._make_service()
        result = await svc.record_result("nonexistent", "A", True)
        assert "error" in result

    @pytest.mark.asyncio
    async def test_auto_complete(self):
        svc = self._make_service()
        svc._persist = AsyncMock()
        with patch.object(svc, '_get_agent_name', new_callable=AsyncMock, return_value="Agent"):
            exp = await svc.create_experiment(
                name="T", hypothesis="T", agent_a_id="a", agent_a_desc="A",
                agent_b_id="b", agent_b_desc="B", max_tasks=2,
            )
        eid = exp["experiment_id"]

        # A wins: 2 successes
        await svc.record_result(eid, "A", True, score=90)
        await svc.record_result(eid, "A", True, score=85)
        # B loses: 2 failures
        await svc.record_result(eid, "B", False, score=20)
        await svc.record_result(eid, "B", False, score=10)

        assert svc._experiments[eid]["status"] == "completed"
        assert svc._experiments[eid]["winner"] == "A"

    @pytest.mark.asyncio
    async def test_get_results(self):
        svc = self._make_service()
        svc._persist = AsyncMock()
        with patch.object(svc, '_get_agent_name', new_callable=AsyncMock, return_value="Agent"):
            exp = await svc.create_experiment(
                name="T", hypothesis="T", agent_a_id="a", agent_a_desc="A",
                agent_b_id="b", agent_b_desc="B",
            )
        await svc.record_result(exp["experiment_id"], "A", True, score=80)

        results = await svc.get_results(exp["experiment_id"])
        assert results["name"] == "T"
        assert results["variant_a"]["total_tasks"] == 1
        assert results["variant_a"]["success_rate"] == 100.0

    @pytest.mark.asyncio
    async def test_get_results_not_found(self):
        svc = self._make_service()
        result = await svc.get_results("nonexistent")
        assert "error" in result

    @pytest.mark.asyncio
    async def test_list_experiments(self):
        svc = self._make_service()
        svc._persist = AsyncMock()
        with patch.object(svc, '_get_agent_name', new_callable=AsyncMock, return_value="Agent"):
            await svc.create_experiment(name="E1", hypothesis="H1", agent_a_id="a", agent_a_desc="A", agent_b_id="b", agent_b_desc="B")
            await svc.create_experiment(name="E2", hypothesis="H2", agent_a_id="c", agent_a_desc="C", agent_b_id="d", agent_b_desc="D")

        experiments = await svc.list_experiments()
        assert len(experiments) == 2

        running = await svc.list_experiments(status="running")
        assert len(running) == 2

        completed = await svc.list_experiments(status="completed")
        assert len(completed) == 0

    @pytest.mark.asyncio
    async def test_cancel_experiment(self):
        svc = self._make_service()
        svc._persist = AsyncMock()
        with patch.object(svc, '_get_agent_name', new_callable=AsyncMock, return_value="Agent"):
            exp = await svc.create_experiment(
                name="T", hypothesis="T", agent_a_id="a", agent_a_desc="A",
                agent_b_id="b", agent_b_desc="B",
            )
        result = await svc.cancel_experiment(exp["experiment_id"])
        assert result["success"] is True
        assert svc._experiments[exp["experiment_id"]]["status"] == "cancelled"

    @pytest.mark.asyncio
    async def test_cancel_nonexistent(self):
        svc = self._make_service()
        result = await svc.cancel_experiment("nonexistent")
        assert "error" in result

    def test_calculate_variant_score(self):
        svc = self._make_service()
        variant = {
            "tasks_completed": 8,
            "tasks_failed": 2,
            "total_cost_usd": 0.005,
            "total_tokens": 1000,
            "total_time_s": 20.0,
            "scores": [80, 85, 90, 75, 80, 85, 90, 80],
        }
        score = svc._calculate_variant_score(variant)
        assert 0 < score <= 100

    def test_calculate_variant_score_empty(self):
        svc = self._make_service()
        variant = {
            "tasks_completed": 0, "tasks_failed": 0,
            "total_cost_usd": 0, "total_tokens": 0,
            "total_time_s": 0, "scores": [],
        }
        assert svc._calculate_variant_score(variant) == 0.0

    @pytest.mark.asyncio
    async def test_tie_result(self):
        svc = self._make_service()
        svc._persist = AsyncMock()
        with patch.object(svc, '_get_agent_name', new_callable=AsyncMock, return_value="Agent"):
            exp = await svc.create_experiment(
                name="Tie", hypothesis="Both equal", agent_a_id="a", agent_a_desc="A",
                agent_b_id="b", agent_b_desc="B", max_tasks=1,
            )
        eid = exp["experiment_id"]
        await svc.record_result(eid, "A", True, score=50, cost_usd=0.01)
        await svc.record_result(eid, "B", True, score=50, cost_usd=0.01)

        assert svc._experiments[eid]["status"] == "completed"
        assert svc._experiments[eid]["winner"] == "tie"


# ========================== CEO Phase 4 Integration Tests ==========================


class TestCEOPhase4Tools:
    """Tests for Phase 4 tools in the CEO agent."""

    def _make_ceo(self):
        from app.agents.ceo import CEOAgent
        return CEOAgent(agent_id="ceo-test")

    def test_phase4_tools_in_list(self):
        ceo = self._make_ceo()
        phase4_tools = [
            "analyze_prompts", "get_prompt_recommendations",
            "analyze_tool_usage", "get_agent_tool_profile",
            "add_knowledge", "search_knowledge", "get_knowledge_stats",
            "get_error_patterns", "get_error_summary", "scan_errors",
            "create_ab_test", "record_ab_result", "get_ab_results", "list_ab_tests",
        ]
        for tool in phase4_tools:
            assert tool in ceo.tools, f"Phase 4 tool '{tool}' missing from CEO tools list"

    def test_phase4_schemas_present(self):
        ceo = self._make_ceo()
        schemas = ceo._get_tools_schema()
        schema_names = [s["function"]["name"] for s in schemas]
        phase4_tools = [
            "analyze_prompts", "get_prompt_recommendations",
            "analyze_tool_usage", "get_agent_tool_profile",
            "add_knowledge", "search_knowledge", "get_knowledge_stats",
            "get_error_patterns", "get_error_summary", "scan_errors",
            "create_ab_test", "record_ab_result", "get_ab_results", "list_ab_tests",
        ]
        for tool in phase4_tools:
            assert tool in schema_names, f"Phase 4 schema '{tool}' missing from CEO schemas"

    def test_total_ceo_tools_count(self):
        ceo = self._make_ceo()
        # Phase 1(4) + Phase 2(8) + Phase 3(11) + Phase 4(14) + base(24) = 61
        assert len(ceo.tools) >= 61, f"CEO should have >= 61 tools, got {len(ceo.tools)}"


# ========================== Config Serialization Tests ==========================


class TestPhase4Serialization:
    """Tests for Phase 4 service Redis serialization."""

    @pytest.mark.asyncio
    async def test_prompt_optimizer_roundtrip(self):
        from app.services.prompt_optimizer import PromptOptimizer
        po = PromptOptimizer()
        po._snapshots["test"] = [{"score": 80, "agent_name": "TestBot"}]
        # Simulate save/load via JSON
        data = json.dumps(po._snapshots)
        po2 = PromptOptimizer()
        po2._snapshots = json.loads(data)
        assert po2._snapshots["test"][0]["score"] == 80

    @pytest.mark.asyncio
    async def test_error_detector_roundtrip(self):
        from app.services.error_pattern_detector import ErrorPatternDetector
        det = ErrorPatternDetector()
        det.record_error("Connection refused", agent_id="a1")
        data = json.dumps({"patterns": det._patterns})
        det2 = ErrorPatternDetector()
        det2._patterns = json.loads(data)["patterns"]
        assert len(det2._patterns) == 1

    @pytest.mark.asyncio
    async def test_ab_testing_roundtrip(self):
        from app.services.ab_testing_service import ABTestingService
        svc = ABTestingService()
        svc._experiments["exp-1"] = {
            "experiment_id": "exp-1",
            "name": "Test",
            "status": "running",
            "variant_a": {"scores": [80, 85]},
            "variant_b": {"scores": [70, 75]},
        }
        data = json.dumps(svc._experiments)
        svc2 = ABTestingService()
        svc2._experiments = json.loads(data)
        assert svc2._experiments["exp-1"]["name"] == "Test"

    @pytest.mark.asyncio
    async def test_tool_optimizer_roundtrip(self):
        from app.services.tool_usage_optimizer import ToolUsageOptimizer
        opt = ToolUsageOptimizer()
        opt._last_analysis = {"status": "analyzed", "total_calls": 100}
        data = json.dumps(opt._last_analysis)
        opt2 = ToolUsageOptimizer()
        opt2._last_analysis = json.loads(data)
        assert opt2._last_analysis["total_calls"] == 100
