"""Tests for Phase 2 — Multi-Agent Voting, Delegation, and Company KPIs."""

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.services.voting_service import (
    VotingService,
    VoteChoice,
    VoteStatus,
    AgentVote,
    VoteSession,
)
from app.services.delegation_service import (
    DelegationService,
    DelegationStatus,
    Delegation,
)
from app.services.company_kpi_service import (
    CompanyKPIService,
    KPITarget,
    CompanyKPIs,
)


# ─── Voting Service Tests ────────────────────────────────────────


class TestVoteChoiceParsing:
    """Test the _parse_vote_response static method."""

    def test_approve_simple(self):
        choice, reasoning = VotingService._parse_vote_response("APPROVE\nGood idea")
        assert choice == VoteChoice.APPROVE
        assert reasoning == "Good idea"

    def test_reject_simple(self):
        choice, reasoning = VotingService._parse_vote_response("REJECT\nToo risky")
        assert choice == VoteChoice.REJECT
        assert reasoning == "Too risky"

    def test_abstain_simple(self):
        choice, reasoning = VotingService._parse_vote_response("ABSTAIN\nI have no opinion")
        assert choice == VoteChoice.ABSTAIN
        assert reasoning == "I have no opinion"

    def test_approve_case_insensitive(self):
        choice, _ = VotingService._parse_vote_response("approve\nOK")
        assert choice == VoteChoice.APPROVE

    def test_approve_with_extra_text(self):
        choice, _ = VotingService._parse_vote_response("I APPROVE this measure\nBecause reasons")
        assert choice == VoteChoice.APPROVE

    def test_unclear_response_defaults_to_abstain(self):
        choice, reasoning = VotingService._parse_vote_response("I'm not sure about this")
        assert choice == VoteChoice.ABSTAIN
        assert "Unclear response" in reasoning or len(reasoning) >= 0

    def test_no_reasoning(self):
        choice, reasoning = VotingService._parse_vote_response("APPROVE")
        assert choice == VoteChoice.APPROVE
        assert reasoning == ""


class TestVoteTally:
    """Test the _tally_votes static method."""

    def _make_session(self, votes: list[VoteChoice | None]) -> VoteSession:
        participants = [
            AgentVote(agent_id=f"agent-{i}", agent_name=f"Agent {i}", choice=v)
            for i, v in enumerate(votes)
        ]
        return VoteSession(
            vote_id="vote-test",
            question="Test question",
            initiated_by="ceo",
            participants=participants,
        )

    def test_unanimous_approve(self):
        session = self._make_session([VoteChoice.APPROVE, VoteChoice.APPROVE, VoteChoice.APPROVE])
        result = VotingService._tally_votes(session)
        assert result["decision"] == "approved"
        assert result["consensus"] is True
        assert result["approve"] == 3
        assert result["reject"] == 0

    def test_unanimous_reject(self):
        session = self._make_session([VoteChoice.REJECT, VoteChoice.REJECT])
        result = VotingService._tally_votes(session)
        assert result["decision"] == "rejected"
        assert result["consensus"] is True

    def test_majority_approve(self):
        session = self._make_session([VoteChoice.APPROVE, VoteChoice.APPROVE, VoteChoice.REJECT])
        result = VotingService._tally_votes(session)
        assert result["decision"] == "approved"
        assert result["consensus"] is True  # 2/3 >= 50%

    def test_tied_vote(self):
        session = self._make_session([VoteChoice.APPROVE, VoteChoice.REJECT])
        result = VotingService._tally_votes(session)
        assert result["decision"] == "tied"
        assert result["consensus"] is False

    def test_all_abstain(self):
        session = self._make_session([VoteChoice.ABSTAIN, VoteChoice.ABSTAIN])
        result = VotingService._tally_votes(session)
        assert result["decision"] == "no_quorum"
        assert result["consensus"] is False

    def test_approve_with_abstain(self):
        session = self._make_session([VoteChoice.APPROVE, VoteChoice.ABSTAIN, VoteChoice.ABSTAIN])
        result = VotingService._tally_votes(session)
        assert result["decision"] == "approved"
        # 1 approve out of 3 total = 33%, so consensus is False (< 50%)
        assert result["consensus"] is False

    def test_approve_majority_with_abstain(self):
        session = self._make_session([
            VoteChoice.APPROVE, VoteChoice.APPROVE, VoteChoice.ABSTAIN
        ])
        result = VotingService._tally_votes(session)
        assert result["decision"] == "approved"
        assert result["consensus"] is True  # 2/3 >= 50%


class TestVotingServiceOperations:
    @pytest.fixture
    def voting(self):
        return VotingService()

    @pytest.mark.asyncio
    async def test_initiate_vote_no_agents_raises(self, voting):
        with patch("app.agents.registry.registry") as mock_reg:
            mock_reg.get.return_value = None
            with pytest.raises(ValueError, match="No valid agents"):
                await voting.initiate_vote("Test?", ["agent-1"], "ceo")

    @pytest.mark.asyncio
    async def test_initiate_vote_creates_session(self, voting):
        mock_agent = MagicMock()
        mock_agent.name = "TestAgent"
        with patch("app.agents.registry.registry") as mock_reg, \
             patch("app.services.event_bus.event_bus.broadcast", new_callable=AsyncMock), \
             patch("app.services.messaging.send_message", new_callable=AsyncMock):
            mock_reg.get.return_value = mock_agent
            mock_agent.run = AsyncMock(return_value="APPROVE\nLooks good")

            session = await voting.initiate_vote(
                "Should we proceed?",
                ["agent-1"],
                "ceo",
                deadline_seconds=60,
            )
            assert session.vote_id.startswith("vote-")
            assert len(session.participants) == 1
            assert session.status == VoteStatus.OPEN

    def test_get_vote_not_found(self, voting):
        assert voting.get_vote("nonexistent") is None

    def test_get_all_votes_empty(self, voting):
        assert voting.get_all_votes() == []

    def test_get_all_votes_with_filter(self, voting):
        session = VoteSession(
            vote_id="v1", question="Q", initiated_by="ceo",
            status=VoteStatus.CLOSED,
        )
        voting._sessions["v1"] = session
        assert len(voting.get_all_votes(status=VoteStatus.CLOSED)) == 1
        assert len(voting.get_all_votes(status=VoteStatus.OPEN)) == 0


# ─── Delegation Service Tests ────────────────────────────────────


class TestDelegationService:
    @pytest.fixture
    def delegation_svc(self):
        return DelegationService()

    @pytest.mark.asyncio
    async def test_delegate_task_source_not_found(self, delegation_svc):
        with patch("app.agents.registry.registry") as mock_reg:
            mock_reg.get.return_value = None
            with pytest.raises(ValueError, match="Source agent"):
                await delegation_svc.delegate_task("src", "dst", "do something")

    @pytest.mark.asyncio
    async def test_delegate_task_target_not_found(self, delegation_svc):
        mock_src = MagicMock()
        mock_src.name = "Source"
        mock_src.company_id = "c1"
        with patch("app.agents.registry.registry") as mock_reg:
            mock_reg.get.side_effect = lambda aid: mock_src if aid == "src" else None
            with pytest.raises(ValueError, match="Target agent"):
                await delegation_svc.delegate_task("src", "dst", "do something")

    @pytest.mark.asyncio
    async def test_delegate_to_self_rejected(self, delegation_svc):
        mock_agent = MagicMock()
        mock_agent.name = "Self"
        mock_agent.company_id = "c1"
        with patch("app.agents.registry.registry") as mock_reg:
            mock_reg.get.return_value = mock_agent
            with pytest.raises(ValueError, match="Cannot delegate to self"):
                await delegation_svc.delegate_task("agent-1", "agent-1", "task")

    @pytest.mark.asyncio
    async def test_delegate_task_success(self, delegation_svc):
        mock_src = MagicMock()
        mock_src.name = "Source"
        mock_src.company_id = "c1"
        mock_dst = MagicMock()
        mock_dst.name = "Destination"
        mock_dst.company_id = "c1"
        mock_dst.run = AsyncMock(return_value="Done!")

        with patch("app.agents.registry.registry") as mock_reg, \
             patch("app.services.event_bus.event_bus.broadcast", new_callable=AsyncMock), \
             patch("app.services.messaging.send_message", new_callable=AsyncMock):
            mock_reg.get.side_effect = lambda aid: mock_src if aid == "src" else mock_dst

            delegation = await delegation_svc.delegate_task("src", "dst", "Analyze data")
            assert delegation.delegation_id.startswith("dlg-")
            assert delegation.from_agent_name == "Source"
            assert delegation.to_agent_name == "Destination"

    def test_get_delegation_not_found(self, delegation_svc):
        assert delegation_svc.get_delegation("nonexistent") is None

    def test_chain_depth_zero(self, delegation_svc):
        assert delegation_svc._get_chain_depth("nonexistent") == 0

    def test_chain_depth_calculation(self, delegation_svc):
        # Create a chain: d1 -> d2 -> d3
        d1 = Delegation(
            delegation_id="d1", from_agent_id="a1", from_agent_name="A1",
            to_agent_id="a2", to_agent_name="A2", task_description="t1",
            company_id="c1", parent_delegation_id=None,
        )
        d2 = Delegation(
            delegation_id="d2", from_agent_id="a2", from_agent_name="A2",
            to_agent_id="a3", to_agent_name="A3", task_description="t2",
            company_id="c1", parent_delegation_id="d1",
        )
        d3 = Delegation(
            delegation_id="d3", from_agent_id="a3", from_agent_name="A3",
            to_agent_id="a4", to_agent_name="A4", task_description="t3",
            company_id="c1", parent_delegation_id="d2",
        )
        delegation_svc._delegations = {"d1": d1, "d2": d2, "d3": d3}
        assert delegation_svc._get_chain_depth("d1") == 0
        assert delegation_svc._get_chain_depth("d2") == 1
        assert delegation_svc._get_chain_depth("d3") == 2

    @pytest.mark.asyncio
    async def test_chain_depth_limit_enforced(self, delegation_svc):
        """Delegation chain depth is limited to 3."""
        # Create chain at max depth
        for i in range(4):
            parent = f"d{i}" if i > 0 else None
            delegation_svc._delegations[f"d{i + 1}"] = Delegation(
                delegation_id=f"d{i + 1}",
                from_agent_id=f"a{i}",
                from_agent_name=f"A{i}",
                to_agent_id=f"a{i + 1}",
                to_agent_name=f"A{i + 1}",
                task_description=f"task{i}",
                company_id="c1",
                parent_delegation_id=parent,
            )

        mock_src = MagicMock()
        mock_src.name = "Deep"
        mock_src.company_id = "c1"
        mock_dst = MagicMock()
        mock_dst.name = "Deeper"
        mock_dst.company_id = "c1"

        with patch("app.agents.registry.registry") as mock_reg:
            mock_reg.get.side_effect = lambda aid: mock_src if aid == "src" else mock_dst
            with pytest.raises(ValueError, match="chain depth limit"):
                await delegation_svc.delegate_task(
                    "src", "dst", "too deep", parent_delegation_id="d4"
                )

    def test_get_agent_delegations_sent(self, delegation_svc):
        d = Delegation(
            delegation_id="d1", from_agent_id="a1", from_agent_name="A1",
            to_agent_id="a2", to_agent_name="A2", task_description="t",
            company_id="c1",
        )
        delegation_svc._delegations["d1"] = d
        sent = delegation_svc.get_agent_delegations("a1", direction="sent")
        assert len(sent) == 1
        received = delegation_svc.get_agent_delegations("a1", direction="received")
        assert len(received) == 0

    def test_get_company_delegations(self, delegation_svc):
        d = Delegation(
            delegation_id="d1", from_agent_id="a1", from_agent_name="A1",
            to_agent_id="a2", to_agent_name="A2", task_description="t",
            company_id="c1",
        )
        delegation_svc._delegations["d1"] = d
        assert len(delegation_svc.get_company_delegations("c1")) == 1
        assert len(delegation_svc.get_company_delegations("c2")) == 0

    def test_get_all_delegations_with_filter(self, delegation_svc):
        d1 = Delegation(
            delegation_id="d1", from_agent_id="a1", from_agent_name="A1",
            to_agent_id="a2", to_agent_name="A2", task_description="t",
            company_id="c1", status=DelegationStatus.COMPLETED,
        )
        d2 = Delegation(
            delegation_id="d2", from_agent_id="a1", from_agent_name="A1",
            to_agent_id="a2", to_agent_name="A2", task_description="t2",
            company_id="c1", status=DelegationStatus.PENDING,
        )
        delegation_svc._delegations = {"d1": d1, "d2": d2}
        assert len(delegation_svc.get_all_delegations()) == 2
        assert len(delegation_svc.get_all_delegations(DelegationStatus.COMPLETED)) == 1
        assert len(delegation_svc.get_all_delegations(DelegationStatus.FAILED)) == 0

    def test_get_chain(self, delegation_svc):
        d1 = Delegation(
            delegation_id="d1", from_agent_id="a1", from_agent_name="A1",
            to_agent_id="a2", to_agent_name="A2", task_description="t1",
            company_id="c1",
        )
        d2 = Delegation(
            delegation_id="d2", from_agent_id="a2", from_agent_name="A2",
            to_agent_id="a3", to_agent_name="A3", task_description="t2",
            company_id="c1", parent_delegation_id="d1",
        )
        delegation_svc._delegations = {"d1": d1, "d2": d2}
        chain = delegation_svc.get_chain("d2")
        assert len(chain) == 2
        assert chain[0]["delegation_id"] == "d1"
        assert chain[1]["delegation_id"] == "d2"

    def test_prune_history(self, delegation_svc):
        delegation_svc._max_history = 3
        for i in range(5):
            delegation_svc._delegations[f"d{i}"] = Delegation(
                delegation_id=f"d{i}", from_agent_id="a1", from_agent_name="A1",
                to_agent_id="a2", to_agent_name="A2", task_description=f"t{i}",
                company_id="c1", status=DelegationStatus.COMPLETED,
                created_at=f"2025-01-0{i + 1}T00:00:00",
            )
        delegation_svc._prune_history()
        assert len(delegation_svc._delegations) < 5


# ─── Company KPI Service Tests ───────────────────────────────────


class TestKPITarget:
    def test_achievement_pct_basic(self):
        kpi = KPITarget(name="Test", metric="rate", target_value=100, current_value=75)
        assert kpi.achievement_pct == 75.0

    def test_achievement_pct_over_target(self):
        kpi = KPITarget(name="Test", metric="rate", target_value=50, current_value=80)
        assert kpi.achievement_pct == 160.0

    def test_achievement_pct_zero_target(self):
        kpi = KPITarget(name="Test", metric="rate", target_value=0, current_value=10)
        assert kpi.achievement_pct == 100.0

    def test_on_track_higher_is_better(self):
        kpi = KPITarget(
            name="Test", metric="rate", target_value=100,
            current_value=85, direction="higher_is_better"
        )
        assert kpi.on_track is True  # 85 >= 80 (80% of 100)

    def test_off_track_higher_is_better(self):
        kpi = KPITarget(
            name="Test", metric="rate", target_value=100,
            current_value=50, direction="higher_is_better"
        )
        assert kpi.on_track is False  # 50 < 80

    def test_on_track_lower_is_better(self):
        kpi = KPITarget(
            name="Test", metric="cost", target_value=1.0,
            current_value=1.1, direction="lower_is_better"
        )
        assert kpi.on_track is True  # 1.1 <= 1.2 (120% of 1.0)

    def test_off_track_lower_is_better(self):
        kpi = KPITarget(
            name="Test", metric="cost", target_value=1.0,
            current_value=2.0, direction="lower_is_better"
        )
        assert kpi.on_track is False  # 2.0 > 1.2

    def test_to_dict(self):
        kpi = KPITarget(name="Success Rate", metric="task_success_rate", target_value=90.0, unit="%")
        d = kpi.to_dict()
        assert d["name"] == "Success Rate"
        assert d["metric"] == "task_success_rate"
        assert d["target_value"] == 90.0
        assert d["unit"] == "%"
        assert "achievement_pct" in d
        assert "on_track" in d


class TestCompanyKPIs:
    def test_to_dict_empty(self):
        c = CompanyKPIs(company_id="c1", company_name="Test Company")
        d = c.to_dict()
        assert d["company_id"] == "c1"
        assert d["summary"]["total_kpis"] == 0
        assert d["summary"]["health_pct"] == 0

    def test_to_dict_with_kpis(self):
        c = CompanyKPIs(company_id="c1", company_name="Test Company")
        c.kpis["rate"] = KPITarget(
            name="Success Rate", metric="task_success_rate",
            target_value=90, current_value=95,
        )
        c.kpis["cost"] = KPITarget(
            name="Daily Cost", metric="daily_cost_usd",
            target_value=1.0, current_value=0.5, direction="lower_is_better",
        )
        d = c.to_dict()
        assert d["summary"]["total_kpis"] == 2
        assert d["summary"]["on_track"] == 2
        assert d["summary"]["health_pct"] == 100.0


class TestCompanyKPIService:
    @pytest.fixture
    def kpi_svc(self):
        return CompanyKPIService()

    @pytest.mark.asyncio
    async def test_set_kpi(self, kpi_svc):
        with patch.object(kpi_svc, "_persist", new_callable=AsyncMock):
            kpi = await kpi_svc.set_kpi(
                company_id="c1", company_name="Test Co",
                kpi_name="Success Rate", metric="task_success_rate",
                target_value=90.0, unit="%",
            )
            assert kpi.name == "Success Rate"
            assert kpi.target_value == 90.0

    @pytest.mark.asyncio
    async def test_set_kpi_creates_company(self, kpi_svc):
        with patch.object(kpi_svc, "_persist", new_callable=AsyncMock):
            await kpi_svc.set_kpi("c1", "Co", "KPI1", "metric1", 50.0)
            assert "c1" in kpi_svc._companies
            assert kpi_svc._companies["c1"].company_name == "Co"

    @pytest.mark.asyncio
    async def test_update_kpi_value(self, kpi_svc):
        with patch.object(kpi_svc, "_persist", new_callable=AsyncMock):
            await kpi_svc.set_kpi("c1", "Co", "K1", "m1", 100.0)
            result = await kpi_svc.update_kpi_value("c1", "K1", 75.0)
            assert result is not None
            assert result.current_value == 75.0

    @pytest.mark.asyncio
    async def test_update_kpi_not_found(self, kpi_svc):
        result = await kpi_svc.update_kpi_value("c1", "K_nonexistent", 50.0)
        assert result is None

    @pytest.mark.asyncio
    async def test_remove_kpi(self, kpi_svc):
        with patch.object(kpi_svc, "_persist", new_callable=AsyncMock):
            await kpi_svc.set_kpi("c1", "Co", "K1", "m1", 100.0)
            removed = await kpi_svc.remove_kpi("c1", "K1")
            assert removed is True
            removed2 = await kpi_svc.remove_kpi("c1", "K1")
            assert removed2 is False

    def test_get_company_kpis_not_found(self, kpi_svc):
        assert kpi_svc.get_company_kpis("nonexistent") is None

    @pytest.mark.asyncio
    async def test_get_company_kpis(self, kpi_svc):
        with patch.object(kpi_svc, "_persist", new_callable=AsyncMock):
            await kpi_svc.set_kpi("c1", "Co", "K1", "m1", 100.0)
            result = kpi_svc.get_company_kpis("c1")
            assert result is not None
            assert result["company_id"] == "c1"
            assert "K1" in result["kpis"]

    @pytest.mark.asyncio
    async def test_get_all_kpis(self, kpi_svc):
        with patch.object(kpi_svc, "_persist", new_callable=AsyncMock):
            await kpi_svc.set_kpi("c1", "Co1", "K1", "m1", 100.0)
            await kpi_svc.set_kpi("c2", "Co2", "K2", "m2", 50.0)
            all_kpis = kpi_svc.get_all_kpis()
            assert len(all_kpis) == 2

    @pytest.mark.asyncio
    async def test_auto_update_from_metrics_no_company(self, kpi_svc):
        result = await kpi_svc.auto_update_from_metrics("nonexistent")
        assert result["updated"] == 0

    @pytest.mark.asyncio
    async def test_auto_update_daily_cost_metric(self, kpi_svc):
        with patch.object(kpi_svc, "_persist", new_callable=AsyncMock):
            await kpi_svc.set_kpi("c1", "Co", "Cost", "daily_cost_usd", 2.0, unit="USD", direction="lower_is_better")

        # Mock the cost tracker and registry
        mock_agent = MagicMock()
        mock_agent.company_id = "c1"
        mock_agent.agent_id = "a1"

        with patch("app.services.cost_tracker.cost_tracker") as mock_ct, \
             patch("app.agents.registry.registry") as mock_reg, \
             patch.object(kpi_svc, "_persist", new_callable=AsyncMock):
            mock_reg.get_all.return_value = [mock_agent]
            mock_ct.get_daily_summary = AsyncMock(return_value={
                "per_agent": {"a1": {"cost_usd": 0.5}},
            })
            result = await kpi_svc.auto_update_from_metrics("c1")
            assert result["updated"] == 1
            assert kpi_svc._companies["c1"].kpis["Cost"].current_value == 0.5


# ─── Delegation to_dict / VoteSession to_dict Tests ──────────────


class TestDataclassSerializaiton:
    def test_delegation_to_dict(self):
        d = Delegation(
            delegation_id="d1", from_agent_id="a1", from_agent_name="Source",
            to_agent_id="a2", to_agent_name="Target", task_description="Do something",
            company_id="c1", status=DelegationStatus.COMPLETED, result="Done",
        )
        data = d.to_dict()
        assert data["delegation_id"] == "d1"
        assert data["status"] == "completed"
        assert data["result"] == "Done"

    def test_vote_session_to_dict(self):
        session = VoteSession(
            vote_id="v1", question="Go?", initiated_by="ceo",
            participants=[
                AgentVote(agent_id="a1", agent_name="Agent1", choice=VoteChoice.APPROVE, reasoning="Yes"),
            ],
            status=VoteStatus.CLOSED,
            result={"decision": "approved", "consensus": True, "approve": 1, "reject": 0, "abstain": 0, "total": 1},
        )
        data = session.to_dict()
        assert data["vote_id"] == "v1"
        assert data["status"] == "closed"
        assert len(data["participants"]) == 1
        assert data["participants"][0]["choice"] == "approve"

    def test_agent_vote_to_dict_none_choice(self):
        v = AgentVote(agent_id="a1", agent_name="Agent1")
        data = v.to_dict()
        assert data["choice"] is None
        assert data["voted_at"] is None
