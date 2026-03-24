"""News Feed skill — fetch news headlines via RSS/Atom feeds."""

import json
import logging

from app.skills.base import Skill, SkillCategory, SkillMetadata

logger = logging.getLogger(__name__)

# Curated list of public RSS feeds (no API key needed)
DEFAULT_FEEDS = {
    "tech": "https://feeds.arstechnica.com/arstechnica/technology-lab",
    "business": "https://feeds.bbci.co.uk/news/business/rss.xml",
    "world": "https://feeds.bbci.co.uk/news/world/rss.xml",
    "science": "https://feeds.bbci.co.uk/news/science_and_environment/rss.xml",
    "crypto": "https://cointelegraph.com/rss",
}


class NewsFeedSkill(Skill):
    """Fetch news headlines from popular RSS feeds."""

    @property
    def name(self) -> str:
        return "news_feed"

    @property
    def display_name(self) -> str:
        return "News Feed"

    @property
    def description(self) -> str:
        return "Fetch latest news headlines from curated RSS feeds. Categories: tech, business, world, science, crypto."

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
            tags=["news", "rss", "feed", "headlines"],
            icon="📰",
        )

    def get_tools_schema(self) -> list[dict]:
        return [
            {
                "type": "function",
                "function": {
                    "name": "get_news",
                    "description": (
                        "Fetch latest news headlines. Available categories: "
                        "tech, business, world, science, crypto. "
                        "Returns titles, links, and publication dates."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "category": {
                                "type": "string",
                                "enum": list(DEFAULT_FEEDS.keys()),
                                "description": "News category to fetch",
                            },
                            "max_items": {
                                "type": "integer",
                                "description": "Maximum number of headlines (1-20, default 10)",
                            },
                        },
                        "required": ["category"],
                    },
                },
            },
        ]

    async def execute(self, tool_name: str, arguments: dict) -> str:
        if tool_name != "get_news":
            return json.dumps({"error": f"Unknown tool: {tool_name}"})

        category = arguments.get("category", "tech")
        max_items = min(max(arguments.get("max_items", 10), 1), 20)

        feed_url = DEFAULT_FEEDS.get(category)
        if not feed_url:
            return json.dumps({
                "error": f"Unknown category: {category}",
                "available": list(DEFAULT_FEEDS.keys()),
            })

        try:
            headlines = await self._fetch_feed(feed_url, max_items)
            return json.dumps({
                "category": category,
                "count": len(headlines),
                "headlines": headlines,
            })
        except Exception as e:
            logger.error(f"News feed failed: {e}")
            return json.dumps({"error": f"Feed fetch failed: {str(e)[:200]}"})

    async def _fetch_feed(self, url: str, max_items: int) -> list[dict]:
        """Fetch and parse an RSS/Atom feed."""
        import asyncio

        try:
            import feedparser
        except ImportError:
            return [{"error": "feedparser package not installed. Run: pip install feedparser"}]

        def _parse():
            feed = feedparser.parse(url)
            return feed.entries[:max_items]

        entries = await asyncio.get_event_loop().run_in_executor(None, _parse)

        headlines = []
        for entry in entries:
            headlines.append({
                "title": entry.get("title", ""),
                "link": entry.get("link", ""),
                "published": entry.get("published", ""),
                "summary": (entry.get("summary", "") or "")[:300],
            })

        return headlines


def register(registry):
    """Register this skill with the skill registry."""
    registry.register(NewsFeedSkill())
