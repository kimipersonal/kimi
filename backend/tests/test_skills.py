"""Tests for the skills system — registry, builtin skills, and API."""

import json

import pytest

from app.skills.base import Skill, SkillCategory, SkillMetadata
from app.skills.registry import SkillRegistry


# --- Mock skill for testing ---

class MockSkill(Skill):
    @property
    def name(self) -> str:
        return "mock_skill"

    @property
    def display_name(self) -> str:
        return "Mock Skill"

    @property
    def description(self) -> str:
        return "A test skill"

    @property
    def version(self) -> str:
        return "1.0.0"

    @property
    def category(self) -> SkillCategory:
        return SkillCategory.UTILITY

    @property
    def metadata(self) -> SkillMetadata:
        return SkillMetadata(author="test", tags=["test"], icon="🧪")

    def get_tools_schema(self) -> list[dict]:
        return [
            {
                "type": "function",
                "function": {
                    "name": "mock_tool",
                    "description": "A mock tool",
                    "parameters": {"type": "object", "properties": {}},
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "mock_tool_2",
                    "description": "Another mock tool",
                    "parameters": {"type": "object", "properties": {}},
                },
            },
        ]

    async def execute(self, tool_name: str, arguments: dict) -> str:
        if tool_name == "mock_tool":
            return json.dumps({"result": "mock_result"})
        if tool_name == "mock_tool_2":
            return json.dumps({"result": "mock_result_2"})
        return json.dumps({"error": f"Unknown tool: {tool_name}"})


# --- Registry tests ---

class TestSkillRegistry:
    def test_register(self):
        reg = SkillRegistry()
        reg.register(MockSkill())
        assert "mock_skill" in {s.name for s in reg.get_all()}
        assert reg.count == 1

    def test_unregister(self):
        reg = SkillRegistry()
        reg.register(MockSkill())
        assert reg.count == 1
        reg.unregister("mock_skill")
        assert reg.count == 0

    def test_tool_index(self):
        reg = SkillRegistry()
        reg.register(MockSkill())
        assert "mock_tool" in reg._tool_index
        assert "mock_tool_2" in reg._tool_index
        assert reg._tool_index["mock_tool"] == "mock_skill"

    def test_unregister_clears_index(self):
        reg = SkillRegistry()
        reg.register(MockSkill())
        reg.unregister("mock_skill")
        assert "mock_tool" not in reg._tool_index
        assert "mock_tool_2" not in reg._tool_index

    def test_get_by_category(self):
        reg = SkillRegistry()
        reg.register(MockSkill())
        utils = reg.get_by_category(SkillCategory.UTILITY)
        assert len(utils) == 1
        research = reg.get_by_category(SkillCategory.RESEARCH)
        assert len(research) == 0

    def test_get_enabled(self):
        reg = SkillRegistry()
        reg.register(MockSkill())
        enabled = reg.get_enabled()
        assert len(enabled) == 1

    def test_get_tool_schemas(self):
        reg = SkillRegistry()
        reg.register(MockSkill())
        schemas = reg.get_tool_schemas()
        assert len(schemas) == 2
        names = {s["function"]["name"] for s in schemas}
        assert "mock_tool" in names
        assert "mock_tool_2" in names

    def test_get_tool_schemas_filtered(self):
        reg = SkillRegistry()
        reg.register(MockSkill())
        schemas = reg.get_tool_schemas(["mock_skill"])
        assert len(schemas) == 2
        schemas = reg.get_tool_schemas(["nonexistent"])
        assert len(schemas) == 0

    @pytest.mark.asyncio
    async def test_execute_tool(self):
        reg = SkillRegistry()
        reg.register(MockSkill())
        result = await reg.execute_tool("mock_tool", {})
        assert result is not None
        data = json.loads(result)
        assert data["result"] == "mock_result"

    @pytest.mark.asyncio
    async def test_execute_unknown_tool(self):
        reg = SkillRegistry()
        reg.register(MockSkill())
        result = await reg.execute_tool("nonexistent_tool", {})
        assert result is None

    def test_get_status(self):
        reg = SkillRegistry()
        reg.register(MockSkill())
        status = reg.get_status()
        assert status["total"] == 1
        assert status["enabled"] == 1
        assert "utility" in status["categories"]
        assert len(status["skills"]) == 1

    @pytest.mark.asyncio
    async def test_discover_builtin(self):
        reg = SkillRegistry()
        await reg.discover_and_load()
        # Should find our builtin skills
        assert reg.count >= 3  # web_search, datetime_utils, news_feed, system_info
        names = {s.name for s in reg.get_all()}
        assert "web_search" in names
        assert "datetime_utils" in names
        assert "news_feed" in names
        assert "system_info" in names


# --- Skill base tests ---

class TestSkillBase:
    def test_to_dict(self):
        skill = MockSkill()
        d = skill.to_dict()
        assert d["name"] == "mock_skill"
        assert d["display_name"] == "Mock Skill"
        assert d["version"] == "1.0.0"
        assert d["category"] == "utility"
        assert d["enabled"] is True
        assert d["tool_count"] == 2
        assert "mock_tool" in d["tools"]
        assert d["metadata"]["icon"] == "🧪"

    def test_get_tool_names(self):
        skill = MockSkill()
        names = skill.get_tool_names()
        assert set(names) == {"mock_tool", "mock_tool_2"}

    def test_is_configured_no_requirements(self):
        skill = MockSkill()
        assert skill.is_configured() is True


# --- Builtin skill tests ---

class TestDateTimeSkill:
    @pytest.mark.asyncio
    async def test_get_current_time(self):
        from app.skills.builtin.datetime_utils import DateTimeSkill
        skill = DateTimeSkill()
        result = await skill.execute("get_current_time", {})
        data = json.loads(result)
        assert "utc" in data
        assert "unix_timestamp" in data

    @pytest.mark.asyncio
    async def test_get_current_time_with_offset(self):
        from app.skills.builtin.datetime_utils import DateTimeSkill
        skill = DateTimeSkill()
        result = await skill.execute("get_current_time", {"timezone_offset": 3})
        data = json.loads(result)
        assert data["timezone"] == "UTC+3"

    @pytest.mark.asyncio
    async def test_calculate_basic(self):
        from app.skills.builtin.datetime_utils import DateTimeSkill
        skill = DateTimeSkill()
        result = await skill.execute("calculate", {"expression": "2 + 3 * 4"})
        data = json.loads(result)
        assert data["result"] == 14

    @pytest.mark.asyncio
    async def test_calculate_sqrt(self):
        from app.skills.builtin.datetime_utils import DateTimeSkill
        skill = DateTimeSkill()
        result = await skill.execute("calculate", {"expression": "sqrt(144)"})
        data = json.loads(result)
        assert data["result"] == 12.0

    @pytest.mark.asyncio
    async def test_calculate_rejects_forbidden(self):
        from app.skills.builtin.datetime_utils import DateTimeSkill
        skill = DateTimeSkill()
        result = await skill.execute("calculate", {"expression": "import os"})
        data = json.loads(result)
        assert "error" in data
        assert "forbidden" in data["error"].lower()


class TestWebSearchSkill:
    def test_schema(self):
        from app.skills.builtin.web_search import WebSearchSkill
        skill = WebSearchSkill()
        schemas = skill.get_tools_schema()
        assert len(schemas) == 1
        assert schemas[0]["function"]["name"] == "web_search"

    @pytest.mark.asyncio
    async def test_empty_query(self):
        from app.skills.builtin.web_search import WebSearchSkill
        skill = WebSearchSkill()
        result = await skill.execute("web_search", {"query": ""})
        data = json.loads(result)
        assert "error" in data


class TestNewsFeedSkill:
    def test_schema(self):
        from app.skills.builtin.news_feed import NewsFeedSkill
        skill = NewsFeedSkill()
        schemas = skill.get_tools_schema()
        assert len(schemas) == 1
        assert schemas[0]["function"]["name"] == "get_news"

    @pytest.mark.asyncio
    async def test_invalid_category(self):
        from app.skills.builtin.news_feed import NewsFeedSkill
        skill = NewsFeedSkill()
        result = await skill.execute("get_news", {"category": "nonexistent"})
        data = json.loads(result)
        assert "error" in data
        assert "available" in data


class TestSystemInfoSkill:
    def test_schema(self):
        from app.skills.builtin.system_info import SystemInfoSkill
        skill = SystemInfoSkill()
        schemas = skill.get_tools_schema()
        assert len(schemas) == 3
        names = {s["function"]["name"] for s in schemas}
        assert names == {"get_system_status", "list_agents", "list_skills"}
