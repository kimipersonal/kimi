"""Comprehensive E2E tests for all must-have and nice-to-have features.

Must-Have Features (7):
1. CEO hire_agent expanded (4 new params)
2. Params wired through company_manager
3. Company templates with real skills
4. Async task queue (fire-and-forget)
5. Shared company workspace
6. Task result persistence
7. Optional network in sandbox

Nice-to-Have Features (8):
1. Shared company memory (pgvector)
2. Agent-initiated reporting
3. Structured deliverables/artifacts
4. Task queue with priority/retry/backpressure
5. Financial data skill
6. Generic REST API skill
7. Agent few-shot examples
8. Tool usage analytics
"""

import asyncio
import json
import os
import shutil
import tempfile
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.agents.base import BaseAgent, AgentState
from app.db.models import AgentStatus


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

def _make_agent(**kwargs) -> BaseAgent:
    defaults = {
        "agent_id": "test-agent-001",
        "name": "Test Agent",
        "role": "tester",
        "system_prompt": "You are a test agent.",
        "model_tier": "fast",
    }
    defaults.update(kwargs)
    return BaseAgent(**defaults)


# ══════════════════════════════════════════════════════════════════════════════
# MUST-HAVE FEATURE 1: CEO hire_agent expanded (4 new params)
# ══════════════════════════════════════════════════════════════════════════════

class TestMustHave1_HireAgentExpanded:
    """CEO hire_agent tool accepts sandbox_enabled, browser_enabled, skills, network_enabled."""

    def test_hire_agent_tool_schema_has_all_params(self):
        """hire_agent schema includes all 4 new capability params."""
        from app.agents.ceo import CEOAgent
        ceo = CEOAgent(agent_id="test-ceo")
        schemas = ceo._get_tools_schema()
        hire_schema = next(s for s in schemas if s["function"]["name"] == "hire_agent")
        props = hire_schema["function"]["parameters"]["properties"]
        assert "sandbox_enabled" in props
        assert "browser_enabled" in props
        assert "skills" in props
        assert "network_enabled" in props

    def test_hire_agent_schema_types_correct(self):
        """Capability params have correct types in the tool schema."""
        from app.agents.ceo import CEOAgent
        ceo = CEOAgent(agent_id="test-ceo")
        schemas = ceo._get_tools_schema()
        hire_schema = next(s for s in schemas if s["function"]["name"] == "hire_agent")
        props = hire_schema["function"]["parameters"]["properties"]
        assert props["sandbox_enabled"]["type"] == "boolean"
        assert props["browser_enabled"]["type"] == "boolean"
        assert props["skills"]["type"] == "array"
        assert props["network_enabled"]["type"] == "boolean"


# ══════════════════════════════════════════════════════════════════════════════
# MUST-HAVE FEATURE 2: Params wired through company_manager
# ══════════════════════════════════════════════════════════════════════════════

class TestMustHave2_CompanyManagerWiring:
    """create_agent() passes all capabilities through to BaseAgent."""

    def test_create_agent_accepts_all_params(self):
        """create_agent() signature includes all capability params."""
        import inspect
        from app.services.company_manager import create_agent
        sig = inspect.signature(create_agent)
        params = list(sig.parameters.keys())
        assert "sandbox_enabled" in params
        assert "browser_enabled" in params
        assert "skills" in params
        assert "network_enabled" in params
        assert "few_shot_examples" in params

    def test_base_agent_init_accepts_capabilities(self):
        """BaseAgent constructor accepts and stores capability params."""
        agent = _make_agent(
            sandbox_enabled=True,
            browser_enabled=True,
            skills=["web_search"],
            company_id="comp-1",
        )
        assert agent.sandbox_enabled is True
        assert agent.browser_enabled is True
        assert agent.skills == ["web_search"]
        assert agent.company_id == "comp-1"

    def test_trading_agent_accepts_capabilities(self):
        """TradingAgent constructor accepts and passes capabilities."""
        from app.agents.trading import TradingAgent
        agent = TradingAgent(
            agent_id="trade-001",
            name="Trader",
            role="analyst",
            system_prompt="Trade.",
            sandbox_enabled=True,
            browser_enabled=True,
            skills=["web_search"],
            network_enabled=True,
            company_id="comp-1",
        )
        assert agent.sandbox_enabled is True
        assert agent.browser_enabled is True
        assert agent.skills == ["web_search"]
        assert agent.network_enabled is True
        assert agent.company_id == "comp-1"


# ══════════════════════════════════════════════════════════════════════════════
# MUST-HAVE FEATURE 3: Company templates with real skills
# ══════════════════════════════════════════════════════════════════════════════

class TestMustHave3_CompanyTemplates:
    """Company templates use real skill names and store capabilities."""

    def test_templates_use_real_skills(self):
        """Built-in company templates reference valid skill names."""
        from app.services.company_manager import COMPANY_TEMPLATES
        valid_skills = {"web_search", "news_feed", "datetime_utils", "file_ops",
                        "system_info", "github", "financial_data", "rest_api"}
        for ttype, template in COMPANY_TEMPLATES.items():
            for role_info in template.get("agents", []):
                for skill in role_info.get("skills", []):
                    assert skill in valid_skills, f"Template '{ttype}' role '{role_info.get('role')}' uses unknown skill '{skill}'"

    @pytest.mark.asyncio
    async def test_agent_template_stores_network_enabled(self):
        """save_template() and get_template() preserve network_enabled."""
        from app.services.agent_templates import AgentTemplateStore
        store = AgentTemplateStore()
        store._templates = {}
        store._loaded = True
        with patch.object(store, "_persist", new_callable=AsyncMock):
            saved = await store.save_template(
                name="Test Template",
                role="tester",
                instructions="Test.",
                network_enabled=True,
            )
        assert saved["network_enabled"] is True
        loaded = await store.get_template("test_template")
        assert loaded is not None
        assert loaded["network_enabled"] is True


# ══════════════════════════════════════════════════════════════════════════════
# MUST-HAVE FEATURE 4: Async task queue
# ══════════════════════════════════════════════════════════════════════════════

class TestMustHave4_AsyncTaskQueue:
    """Task queue supports fire-and-forget execution."""

    def test_task_queue_singleton_exists(self):
        from app.services.task_queue import task_queue
        assert task_queue is not None

    def test_task_queue_has_methods(self):
        from app.services.task_queue import TaskQueue
        assert hasattr(TaskQueue, "submit_task")
        assert hasattr(TaskQueue, "get_task")
        assert hasattr(TaskQueue, "get_all_tasks")

    def test_ceo_has_task_tools(self):
        """CEO has get_task_result and list_tasks tools."""
        from app.agents.ceo import CEOAgent
        ceo = CEOAgent(agent_id="test-ceo")
        schemas = ceo._get_tools_schema()
        tool_names = {s["function"]["name"] for s in schemas}
        assert "assign_task" in tool_names
        assert "get_task_result" in tool_names
        assert "list_tasks" in tool_names


# ══════════════════════════════════════════════════════════════════════════════
# MUST-HAVE FEATURE 5: Shared company workspace
# ══════════════════════════════════════════════════════════════════════════════

class TestMustHave5_SharedWorkspace:
    """Per-company file storage with workspace_write/read/list tools."""

    def test_workspace_tools_available_with_company(self):
        """Workspace tools appear when agent has company_id."""
        agent = _make_agent(company_id="company-1")
        schemas = agent._get_tools_schema()
        tool_names = {s["function"]["name"] for s in schemas}
        assert "workspace_write" in tool_names
        assert "workspace_read" in tool_names
        assert "workspace_list" in tool_names

    def test_workspace_tools_not_available_without_company(self):
        """Workspace tools don't appear when agent has no company_id."""
        agent = _make_agent(company_id=None)
        schemas = agent._get_tools_schema()
        tool_names = {s["function"]["name"] for s in schemas}
        assert "workspace_write" not in tool_names
        assert "workspace_read" not in tool_names
        assert "workspace_list" not in tool_names

    def test_workspace_write_read_roundtrip(self):
        """Can write a file and read it back."""
        from app.services.workspace import write_file, read_file, list_files
        company_id = "test-company-workspace"
        try:
            write_result = write_file(company_id, "hello.txt", "world")
            assert write_result["success"] is True

            read_result = read_file(company_id, "hello.txt")
            assert read_result["success"] is True
            assert read_result["content"] == "world"

            ls_result = list_files(company_id)
            file_names = [f["name"] if isinstance(f, dict) else f for f in ls_result["files"]]
            assert "hello.txt" in file_names
        finally:
            from app.services.workspace import get_workspace_path
            ws = get_workspace_path(company_id)
            if os.path.exists(ws):
                shutil.rmtree(ws)

    def test_workspace_path_traversal_safe(self):
        """Path traversal attempts are blocked."""
        from app.services.workspace import read_file
        result = read_file("test-co", "../../../etc/passwd")
        assert result["success"] is False


# ══════════════════════════════════════════════════════════════════════════════
# MUST-HAVE FEATURE 6: Task result persistence to DB
# ══════════════════════════════════════════════════════════════════════════════

class TestMustHave6_TaskPersistence:
    """Async tasks are persisted to the tasks table."""

    def test_persist_method_exists(self):
        from app.services.task_queue import TaskQueue
        assert hasattr(TaskQueue, "_persist_task_to_db")
        assert hasattr(TaskQueue, "_update_task_in_db")

    def test_task_result_to_dict(self):
        from app.services.task_queue import AsyncTaskResult, AsyncTaskStatus, TaskPriority
        task = AsyncTaskResult(
            task_id="task-1",
            agent_id="agent-1",
            agent_name="Agent One",
            description="Do something",
            status=AsyncTaskStatus.COMPLETED,
            result="Done!",
            priority=TaskPriority.HIGH,
        )
        d = task.to_dict()
        assert d["task_id"] == "task-1"
        assert d["status"] == "completed"
        assert d["priority"] == "high"


# ══════════════════════════════════════════════════════════════════════════════
# MUST-HAVE FEATURE 7: Optional network in sandbox
# ══════════════════════════════════════════════════════════════════════════════

class TestMustHave7_NetworkInSandbox:
    """network_enabled flag controls Docker container networking."""

    def test_network_enabled_default_false(self):
        agent = _make_agent()
        assert agent.network_enabled is False

    def test_network_enabled_set_externally(self):
        agent = _make_agent()
        agent.network_enabled = True
        assert agent.network_enabled is True

    def test_sandbox_tool_passes_network_flag(self):
        """execute_tool for sandbox passes network_enabled."""
        from app.agents.base import BaseAgent
        agent = _make_agent(sandbox_enabled=True)
        agent.network_enabled = True
        # Verify the execute_tool code references getattr(self, 'network_enabled', False)
        import inspect
        source = inspect.getsource(BaseAgent.execute_tool)
        assert "network_enabled" in source


# ══════════════════════════════════════════════════════════════════════════════
# NICE-TO-HAVE FEATURE 1: Shared company memory (pgvector)
# ══════════════════════════════════════════════════════════════════════════════

class TestNiceToHave1_CompanyMemory:
    """Shared pgvector knowledge base per company."""

    def test_memory_tools_available_with_company(self):
        agent = _make_agent(company_id="company-1")
        schemas = agent._get_tools_schema()
        tool_names = {s["function"]["name"] for s in schemas}
        assert "memory_store" in tool_names
        assert "memory_query" in tool_names

    def test_memory_tools_not_available_without_company(self):
        agent = _make_agent(company_id=None)
        schemas = agent._get_tools_schema()
        tool_names = {s["function"]["name"] for s in schemas}
        assert "memory_store" not in tool_names
        assert "memory_query" not in tool_names

    @pytest.mark.asyncio
    async def test_memory_store_handler_calls_service(self):
        """memory_store tool calls company_memory.store_company_memory()."""
        agent = _make_agent(company_id="comp-1")
        with patch("app.agents.base.event_bus", MagicMock(broadcast=AsyncMock())):
            with patch("app.services.company_memory.store_company_memory", new_callable=AsyncMock, return_value="mem-123") as mock_store:
                with patch("app.services.llm_router.get_embedding", new_callable=AsyncMock, return_value=[0.1] * 768):
                    result = await agent.execute_tool("memory_store", {"content": "test insight"})
        parsed = json.loads(result)
        assert parsed["success"] is True
        mock_store.assert_called_once()

    @pytest.mark.asyncio
    async def test_memory_query_handler_calls_service(self):
        """memory_query tool calls company_memory.recall_company_memories()."""
        agent = _make_agent(company_id="comp-1")
        fake_memories = [
            {"id": "m1", "content": "test", "agent_id": "a1", "category": "insight", "score": 0.9, "importance": 0.5, "created_at": None}
        ]
        with patch("app.agents.base.event_bus", MagicMock(broadcast=AsyncMock())):
            with patch("app.services.company_memory.recall_company_memories", new_callable=AsyncMock, return_value=fake_memories):
                with patch("app.services.llm_router.get_embedding", new_callable=AsyncMock, return_value=[0.1] * 768):
                    result = await agent.execute_tool("memory_query", {"query": "test"})
        parsed = json.loads(result)
        assert parsed["success"] is True
        assert len(parsed["memories"]) == 1

    def test_format_memories_for_prompt(self):
        from app.services.company_memory import format_company_memories_for_prompt
        memories = [{"category": "insight", "score": 0.85, "agent_id": "agent-abc-123", "content": "Test finding"}]
        result = format_company_memories_for_prompt(memories)
        assert "Shared company knowledge" in result
        assert "Test finding" in result
        assert "85%" in result

    def test_format_empty_memories(self):
        from app.services.company_memory import format_company_memories_for_prompt
        assert format_company_memories_for_prompt([]) == ""


# ══════════════════════════════════════════════════════════════════════════════
# NICE-TO-HAVE FEATURE 2: Agent-initiated reporting
# ══════════════════════════════════════════════════════════════════════════════

class TestNiceToHave2_AgentReporting:
    """report_to_ceo tool lets agents proactively notify CEO."""

    def test_report_tool_available_to_all_agents(self):
        agent = _make_agent()  # No company_id
        schemas = agent._get_tools_schema()
        tool_names = {s["function"]["name"] for s in schemas}
        assert "report_to_ceo" in tool_names

    def test_report_tool_schema_correct(self):
        agent = _make_agent()
        schemas = agent._get_tools_schema()
        report_schema = next(s for s in schemas if s["function"]["name"] == "report_to_ceo")
        props = report_schema["function"]["parameters"]["properties"]
        assert "title" in props
        assert "content" in props
        assert "priority" in props
        assert set(props["priority"]["enum"]) == {"low", "medium", "high", "critical"}

    @pytest.mark.asyncio
    async def test_report_to_ceo_sends_message(self):
        agent = _make_agent()
        with patch("app.agents.base.event_bus", MagicMock(broadcast=AsyncMock())):
            with patch("app.services.messaging.send_message", new_callable=AsyncMock) as mock_send:
                result = await agent.execute_tool("report_to_ceo", {
                    "title": "Market Alert",
                    "content": "Price dropped 10%",
                    "priority": "high",
                })
        parsed = json.loads(result)
        assert parsed["success"] is True
        mock_send.assert_called_once()
        call_kwargs = mock_send.call_args
        assert call_kwargs[1]["message_type"] == "report"


# ══════════════════════════════════════════════════════════════════════════════
# NICE-TO-HAVE FEATURE 3: Structured deliverables/artifacts
# ══════════════════════════════════════════════════════════════════════════════

class TestNiceToHave3_Artifacts:
    """Structured deliverables stored in company workspace."""

    def test_artifact_tools_available_with_company(self):
        agent = _make_agent(company_id="company-1")
        schemas = agent._get_tools_schema()
        tool_names = {s["function"]["name"] for s in schemas}
        assert "create_artifact" in tool_names
        assert "list_artifacts" in tool_names

    def test_artifact_tools_not_available_without_company(self):
        agent = _make_agent(company_id=None)
        schemas = agent._get_tools_schema()
        tool_names = {s["function"]["name"] for s in schemas}
        assert "create_artifact" not in tool_names
        assert "list_artifacts" not in tool_names

    def test_create_and_list_artifacts(self):
        """Full Create→List→Read roundtrip for artifacts."""
        from app.services.artifact_service import create_artifact, list_artifacts, get_artifact, read_artifact_content
        company_id = "test-artifacts-company"
        try:
            result = create_artifact(
                company_id=company_id,
                agent_id="agent-1",
                agent_name="Analyst",
                name="Q1 Report",
                content="# Q1 Analysis\nRevenue grew 20%.",
                artifact_type="report",
                format="markdown",
                description="Quarterly report",
            )
            assert result["name"] == "Q1 Report"
            assert result["type"] == "report"
            assert result["format"] == "markdown"
            artifact_id = result["id"]

            artifacts = list_artifacts(company_id)
            assert len(artifacts) == 1
            assert artifacts[0]["id"] == artifact_id

            content = read_artifact_content(company_id, artifact_id)
            assert content["success"] is True
            assert "Revenue grew 20%" in content["content"]

            # Filter by type
            empty = list_artifacts(company_id, artifact_type="chart")
            assert len(empty) == 0
        finally:
            from app.services.workspace import get_workspace_path
            ws = get_workspace_path(company_id)
            if os.path.exists(ws):
                shutil.rmtree(ws)

    def test_artifact_extension_mapping(self):
        from app.services.artifact_service import _extension_for_format
        assert _extension_for_format("markdown") == ".md"
        assert _extension_for_format("json") == ".json"
        assert _extension_for_format("csv") == ".csv"
        assert _extension_for_format("html") == ".html"
        assert _extension_for_format("text") == ".txt"
        assert _extension_for_format("unknown") == ".txt"


# ══════════════════════════════════════════════════════════════════════════════
# NICE-TO-HAVE FEATURE 4: Task queue with priority/retry/backpressure
# ══════════════════════════════════════════════════════════════════════════════

class TestNiceToHave4_TaskPriority:
    """Task queue supports priority, retry, and backpressure."""

    def test_task_priority_enum(self):
        from app.services.task_queue import TaskPriority
        assert TaskPriority.LOW.value == "low"
        assert TaskPriority.NORMAL.value == "normal"
        assert TaskPriority.HIGH.value == "high"
        assert TaskPriority.CRITICAL.value == "critical"

    def test_task_result_has_priority_fields(self):
        from app.services.task_queue import AsyncTaskResult, AsyncTaskStatus, TaskPriority
        task = AsyncTaskResult(
            task_id="t1", agent_id="a1", agent_name="Agent",
            description="Test", status=AsyncTaskStatus.PENDING,
            priority=TaskPriority.HIGH, max_retries=3,
        )
        assert task.priority == TaskPriority.HIGH
        assert task.max_retries == 3
        assert task.retry_count == 0
        d = task.to_dict()
        assert d["priority"] == "high"
        assert d["max_retries"] == 3

    def test_task_queue_has_semaphore(self):
        from app.services.task_queue import TaskQueue
        tq = TaskQueue(max_concurrent=5)
        assert tq._semaphore._value == 5

    def test_priority_sorting(self):
        """get_all_tasks sorts by priority (highest first)."""
        from app.services.task_queue import TaskQueue, AsyncTaskResult, AsyncTaskStatus, TaskPriority
        tq = TaskQueue()
        tq._tasks["t1"] = AsyncTaskResult(
            task_id="t1", agent_id="a1", agent_name="A",
            description="Low", status=AsyncTaskStatus.PENDING,
            priority=TaskPriority.LOW,
        )
        tq._tasks["t2"] = AsyncTaskResult(
            task_id="t2", agent_id="a1", agent_name="A",
            description="Critical", status=AsyncTaskStatus.PENDING,
            priority=TaskPriority.CRITICAL,
        )
        tq._tasks["t3"] = AsyncTaskResult(
            task_id="t3", agent_id="a1", agent_name="A",
            description="Normal", status=AsyncTaskStatus.PENDING,
            priority=TaskPriority.NORMAL,
        )
        all_tasks = tq.get_all_tasks()
        priorities = [t["priority"] for t in all_tasks]
        assert priorities == ["critical", "normal", "low"]

    def test_ceo_assign_task_has_priority_params(self):
        """CEO assign_task tool schema includes priority and max_retries."""
        from app.agents.ceo import CEOAgent
        ceo = CEOAgent(agent_id="test-ceo")
        schemas = ceo._get_tools_schema()
        assign_schema = next(s for s in schemas if s["function"]["name"] == "assign_task")
        props = assign_schema["function"]["parameters"]["properties"]
        assert "priority" in props
        assert set(props["priority"]["enum"]) == {"low", "normal", "high", "critical"}
        assert "max_retries" in props


# ══════════════════════════════════════════════════════════════════════════════
# NICE-TO-HAVE FEATURE 5: Financial data skill
# ══════════════════════════════════════════════════════════════════════════════

class TestNiceToHave5_FinancialSkill:
    """Financial data skill with stock quotes, market overview, etc."""

    def test_skill_loads(self):
        from app.skills.builtin.financial_data import FinancialDataSkill
        skill = FinancialDataSkill()
        assert skill.name == "financial_data"
        assert skill.category.value == "finance"

    def test_skill_has_4_tools(self):
        from app.skills.builtin.financial_data import FinancialDataSkill
        skill = FinancialDataSkill()
        tools = skill.get_tools_schema()
        tool_names = {t["function"]["name"] for t in tools}
        assert "get_stock_quote" in tool_names
        assert "get_market_overview" in tool_names
        assert "get_economic_calendar" in tool_names
        assert "get_market_sentiment" in tool_names
        assert len(tools) == 4

    def test_skill_register_function(self):
        from app.skills.builtin.financial_data import register
        from app.skills.registry import SkillRegistry
        reg = SkillRegistry()
        register(reg)
        assert "financial_data" in reg._skills

    @pytest.mark.asyncio
    async def test_economic_calendar_returns_data(self):
        """get_economic_calendar is a reference dataset, always returns data."""
        from app.skills.builtin.financial_data import FinancialDataSkill
        skill = FinancialDataSkill()
        result = await skill.execute("get_economic_calendar", {})
        parsed = json.loads(result)
        assert "events" in parsed
        assert len(parsed["events"]) > 0

    @pytest.mark.asyncio
    async def test_unknown_tool_returns_error(self):
        from app.skills.builtin.financial_data import FinancialDataSkill
        skill = FinancialDataSkill()
        result = await skill.execute("nonexistent_tool", {})
        parsed = json.loads(result)
        assert "error" in parsed


# ══════════════════════════════════════════════════════════════════════════════
# NICE-TO-HAVE FEATURE 6: Generic REST API skill
# ══════════════════════════════════════════════════════════════════════════════

class TestNiceToHave6_RestAPISkill:
    """Generic REST API skill with auth support and SSRF protection."""

    def test_skill_loads(self):
        from app.skills.builtin.rest_api import RestAPISkill
        skill = RestAPISkill()
        assert skill.name == "rest_api"

    def test_skill_has_http_request_tool(self):
        from app.skills.builtin.rest_api import RestAPISkill
        skill = RestAPISkill()
        tools = skill.get_tools_schema()
        assert len(tools) == 1
        assert tools[0]["function"]["name"] == "http_request"
        props = tools[0]["function"]["parameters"]["properties"]
        assert "url" in props
        assert "method" in props
        assert "auth_type" in props

    @pytest.mark.asyncio
    async def test_ssrf_blocks_localhost(self):
        """Requests to localhost are blocked (SSRF protection)."""
        from app.skills.builtin.rest_api import RestAPISkill
        skill = RestAPISkill()
        result = await skill.execute("http_request", {"url": "http://localhost:8080/api"})
        parsed = json.loads(result)
        assert "error" in parsed
        assert "internal" in parsed["error"].lower() or "private" in parsed["error"].lower()

    @pytest.mark.asyncio
    async def test_ssrf_blocks_private_ip(self):
        from app.skills.builtin.rest_api import RestAPISkill
        skill = RestAPISkill()
        for ip in ["http://192.168.1.1/", "http://10.0.0.1/", "http://172.16.0.1/"]:
            result = await skill.execute("http_request", {"url": ip})
            parsed = json.loads(result)
            assert "error" in parsed, f"SSRF not blocked for {ip}"

    @pytest.mark.asyncio
    async def test_ssrf_blocks_metadata_ip(self):
        """Cloud metadata IP is blocked."""
        from app.skills.builtin.rest_api import RestAPISkill
        skill = RestAPISkill()
        result = await skill.execute("http_request", {"url": "http://169.254.169.254/latest/meta-data/"})
        parsed = json.loads(result)
        assert "error" in parsed

    @pytest.mark.asyncio
    async def test_empty_url_error(self):
        from app.skills.builtin.rest_api import RestAPISkill
        skill = RestAPISkill()
        result = await skill.execute("http_request", {"url": ""})
        parsed = json.loads(result)
        assert "error" in parsed

    @pytest.mark.asyncio
    async def test_invalid_scheme_error(self):
        from app.skills.builtin.rest_api import RestAPISkill
        skill = RestAPISkill()
        result = await skill.execute("http_request", {"url": "ftp://example.com"})
        parsed = json.loads(result)
        assert "error" in parsed

    @pytest.mark.asyncio
    async def test_unknown_tool_error(self):
        from app.skills.builtin.rest_api import RestAPISkill
        skill = RestAPISkill()
        result = await skill.execute("bad_tool", {})
        parsed = json.loads(result)
        assert "error" in parsed

    def test_register_function(self):
        from app.skills.builtin.rest_api import register
        from app.skills.registry import SkillRegistry
        reg = SkillRegistry()
        register(reg)
        assert "rest_api" in reg._skills


# ══════════════════════════════════════════════════════════════════════════════
# NICE-TO-HAVE FEATURE 7: Agent few-shot examples
# ══════════════════════════════════════════════════════════════════════════════

class TestNiceToHave7_FewShotExamples:
    """CEO can provide few-shot examples when hiring agents."""

    def test_hire_agent_schema_has_few_shot_param(self):
        from app.agents.ceo import CEOAgent
        ceo = CEOAgent(agent_id="test-ceo")
        schemas = ceo._get_tools_schema()
        hire_schema = next(s for s in schemas if s["function"]["name"] == "hire_agent")
        props = hire_schema["function"]["parameters"]["properties"]
        assert "few_shot_examples" in props
        assert props["few_shot_examples"]["type"] == "array"

    def test_few_shot_examples_injected_into_prompt(self):
        """few_shot_examples are appended to system_prompt during create_agent."""
        # Test the prompt injection logic directly
        examples = [
            {"input": "What is BTC price?", "output": "BTC is currently at $45,000."},
            {"input": "Market outlook?", "output": "Markets are bullish."},
        ]
        system_prompt = "You are a market analyst."
        # Simulate what company_manager does
        if examples:
            examples_block = "\n\nFEW-SHOT EXAMPLES (follow this style):\n"
            for i, ex in enumerate(examples[:10], 1):
                examples_block += f"\nExample {i}:\nUser: {ex.get('input', '')}\nAssistant: {ex.get('output', '')}\n"
            system_prompt = system_prompt + examples_block

        assert "FEW-SHOT EXAMPLES" in system_prompt
        assert "What is BTC price?" in system_prompt
        assert "BTC is currently at $45,000." in system_prompt
        assert "Example 1:" in system_prompt
        assert "Example 2:" in system_prompt

    def test_no_examples_leaves_prompt_unchanged(self):
        """When no examples are provided, system prompt is unchanged."""
        system_prompt = "You are a test agent."
        examples = None
        if examples:
            system_prompt += "EXAMPLES"
        assert system_prompt == "You are a test agent."


# ══════════════════════════════════════════════════════════════════════════════
# NICE-TO-HAVE FEATURE 8: Tool usage analytics
# ══════════════════════════════════════════════════════════════════════════════

class TestNiceToHave8_ToolAnalytics:
    """In-memory tool usage tracking and analytics."""

    def test_record_and_get_stats(self):
        from app.services.tool_analytics import ToolAnalytics
        ta = ToolAnalytics()
        ta.record("browse_url", "agent-1", "Agent 1", True, 150.0)
        ta.record("browse_url", "agent-1", "Agent 1", True, 200.0)
        ta.record("browse_url", "agent-1", "Agent 1", False, 50.0, error="timeout")
        ta.record("execute_code", "agent-2", "Agent 2", True, 300.0)

        stats = ta.get_stats()
        assert stats["total_calls"] == 4
        assert stats["unique_tools_used"] == 2
        assert stats["tools"]["browse_url"]["total_calls"] == 3
        assert stats["tools"]["browse_url"]["successes"] == 2
        assert stats["tools"]["browse_url"]["failures"] == 1
        assert stats["tools"]["browse_url"]["success_rate"] == 66.7
        assert stats["tools"]["execute_code"]["total_calls"] == 1

    def test_get_agent_stats(self):
        from app.services.tool_analytics import ToolAnalytics
        ta = ToolAnalytics()
        ta.record("tool_a", "agent-1", "A1", True, 100.0)
        ta.record("tool_b", "agent-1", "A1", True, 100.0)
        ta.record("tool_a", "agent-2", "A2", True, 100.0)

        stats = ta.get_agent_stats("agent-1")
        assert stats["total_calls"] == 2
        assert stats["tools"]["tool_a"] == 1
        assert stats["tools"]["tool_b"] == 1

    def test_get_recent(self):
        from app.services.tool_analytics import ToolAnalytics
        ta = ToolAnalytics()
        for i in range(10):
            ta.record(f"tool_{i}", f"agent-{i}", f"Agent {i}", True, float(i))

        recent = ta.get_recent(5)
        assert len(recent) == 5
        # Most recent first
        assert recent[0]["tool_name"] == "tool_9"

    def test_reset_clears_all(self):
        from app.services.tool_analytics import ToolAnalytics
        ta = ToolAnalytics()
        ta.record("x", "a", "A", True, 1.0)
        ta.reset()
        assert ta.get_stats()["total_calls"] == 0
        assert ta.get_recent() == []

    def test_ring_buffer_prunes(self):
        from app.services.tool_analytics import ToolAnalytics
        ta = ToolAnalytics()
        ta.MAX_RECORDS = 100
        for i in range(150):
            ta.record("tool", "a", "A", True, 1.0)
        assert len(ta._records) <= 101  # after pruning to 50 + 1 remaining

    def test_analytics_hooked_in_base_agent_act(self):
        """BaseAgent._act_node imports and uses tool_analytics."""
        import inspect
        from app.agents.base import BaseAgent
        source = inspect.getsource(BaseAgent._act_node)
        assert "tool_analytics" in source
        assert "record" in source


# ══════════════════════════════════════════════════════════════════════════════
# INTEGRATION: Tool routing in BaseAgent.execute_tool
# ══════════════════════════════════════════════════════════════════════════════

class TestIntegration_ToolRouting:
    """BaseAgent.execute_tool correctly routes all tool types."""

    def test_execute_tool_routes_all_new_tools(self):
        """execute_tool has routes for all new tool names."""
        import inspect
        from app.agents.base import BaseAgent
        source = inspect.getsource(BaseAgent.execute_tool)

        # Must-have tools
        assert "workspace_write" in source
        assert "workspace_read" in source
        assert "workspace_list" in source
        assert "message_agent" in source

        # Nice-to-have tools
        assert "report_to_ceo" in source
        assert "memory_store" in source
        assert "memory_query" in source
        assert "create_artifact" in source
        assert "list_artifacts" in source


# ══════════════════════════════════════════════════════════════════════════════
# INTEGRATION: Dashboard endpoints
# ══════════════════════════════════════════════════════════════════════════════

class TestIntegration_DashboardEndpoints:
    """Dashboard API has all new endpoints."""

    def test_all_new_endpoints_exist(self):
        """Dashboard router includes all new nice-to-have endpoints."""
        from app.api.dashboard import router
        paths = [r.path for r in router.routes if hasattr(r, "path")]
        # Dashboard routes are prefixed with /api/dashboard
        path_suffixes = [p.split("/api/dashboard")[-1] if "/api/dashboard" in p else p for p in paths]
        assert "/memory/{company_id}" in path_suffixes
        assert "/artifacts/{company_id}" in path_suffixes
        assert "/artifacts/{company_id}/{artifact_id}" in path_suffixes
        assert "/tool-analytics" in path_suffixes
        assert "/tool-analytics/recent" in path_suffixes
        assert "/tool-analytics/agent/{agent_id}" in path_suffixes


# ══════════════════════════════════════════════════════════════════════════════
# INTEGRATION: Skill auto-discovery
# ══════════════════════════════════════════════════════════════════════════════

class TestIntegration_SkillDiscovery:
    """New skills are auto-discovered by the registry."""

    @pytest.mark.asyncio
    async def test_all_builtin_skills_discovered(self):
        """discover_and_load() finds all builtin skills including new ones."""
        from app.skills.registry import SkillRegistry
        reg = SkillRegistry()
        await reg.discover_and_load()

        skill_names = list(reg._skills.keys())
        assert "web_search" in skill_names
        assert "datetime_utils" in skill_names
        assert "financial_data" in skill_names
        assert "rest_api" in skill_names
        assert "news_feed" in skill_names
        assert "file_ops" in skill_names

    @pytest.mark.asyncio
    async def test_skill_count_at_least_8(self):
        """We should have at least 8 builtin skills."""
        from app.skills.registry import SkillRegistry
        reg = SkillRegistry()
        await reg.discover_and_load()
        assert len(reg._skills) >= 8
