"""Web Search skill — search the web via DuckDuckGo (no API key required)."""

import json
import logging

from app.skills.base import Skill, SkillCategory, SkillMetadata

logger = logging.getLogger(__name__)


class WebSearchSkill(Skill):
    """Search the web using DuckDuckGo HTML results (no API key needed)."""

    @property
    def name(self) -> str:
        return "web_search"

    @property
    def display_name(self) -> str:
        return "Web Search"

    @property
    def description(self) -> str:
        return "Search the web for information using DuckDuckGo. Returns top results with titles, URLs, and snippets."

    @property
    def version(self) -> str:
        return "1.0.0"

    @property
    def category(self) -> SkillCategory:
        return SkillCategory.RESEARCH

    @property
    def metadata(self) -> SkillMetadata:
        return SkillMetadata(
            author="system",
            tags=["search", "web", "research"],
            icon="🔍",
        )

    def get_tools_schema(self) -> list[dict]:
        return [
            {
                "type": "function",
                "function": {
                    "name": "web_search",
                    "description": (
                        "Search the web for information. Returns top results with titles, URLs, and snippets. "
                        "Use for finding current information, researching topics, or verifying facts."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "query": {
                                "type": "string",
                                "description": "The search query",
                            },
                            "max_results": {
                                "type": "integer",
                                "description": "Maximum number of results (1-10, default 5)",
                            },
                        },
                        "required": ["query"],
                    },
                },
            },
        ]

    async def execute(self, tool_name: str, arguments: dict) -> str:
        if tool_name != "web_search":
            return json.dumps({"error": f"Unknown tool: {tool_name}"})

        query = arguments.get("query", "")
        max_results = min(max(arguments.get("max_results", 5), 1), 10)

        if not query.strip():
            return json.dumps({"error": "Empty search query"})

        try:
            results = await self._search(query, max_results)
            return json.dumps({"query": query, "results": results})
        except Exception as e:
            logger.error(f"Web search failed: {e}")
            return json.dumps({"error": f"Search failed: {str(e)[:200]}"})

    async def _search(self, query: str, max_results: int) -> list[dict]:
        """Perform DuckDuckGo search via HTML scraping."""
        import asyncio

        try:
            from duckduckgo_search import DDGS
        except ImportError:
            return [{"error": "duckduckgo_search package not installed. Run: pip install duckduckgo-search"}]

        def _do_search():
            with DDGS() as ddgs:
                return list(ddgs.text(query, max_results=max_results))

        results = await asyncio.get_event_loop().run_in_executor(None, _do_search)

        return [
            {
                "title": r.get("title", ""),
                "url": r.get("href", ""),
                "snippet": r.get("body", ""),
            }
            for r in results
        ]


def register(registry):
    """Register this skill with the skill registry."""
    registry.register(WebSearchSkill())
