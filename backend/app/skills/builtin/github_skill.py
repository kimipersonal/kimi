"""GitHub skill — interact with GitHub API for repository information."""

import json
import logging

from app.skills.base import Skill, SkillCategory, SkillMetadata

logger = logging.getLogger(__name__)


class GitHubSkill(Skill):
    """Query GitHub repositories, issues, and pull requests via the GitHub API."""

    @property
    def name(self) -> str:
        return "github"

    @property
    def display_name(self) -> str:
        return "GitHub"

    @property
    def description(self) -> str:
        return "Search repositories, list issues and PRs, get repo info from GitHub. Requires GITHUB_TOKEN for private repos."

    @property
    def version(self) -> str:
        return "1.0.0"

    @property
    def category(self) -> SkillCategory:
        return SkillCategory.DEVELOPMENT

    @property
    def metadata(self) -> SkillMetadata:
        return SkillMetadata(
            author="system",
            tags=["github", "git", "repository", "issues", "pr"],
            requires_config=["GITHUB_TOKEN"],
            icon="🐙",
        )

    def get_tools_schema(self) -> list[dict]:
        return [
            {
                "type": "function",
                "function": {
                    "name": "github_get_repo",
                    "description": "Get information about a GitHub repository (stars, forks, description, language).",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "owner": {"type": "string", "description": "Repository owner (user or org)"},
                            "repo": {"type": "string", "description": "Repository name"},
                        },
                        "required": ["owner", "repo"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "github_list_issues",
                    "description": "List open issues for a repository. Returns title, number, labels, and author.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "owner": {"type": "string", "description": "Repository owner"},
                            "repo": {"type": "string", "description": "Repository name"},
                            "state": {
                                "type": "string",
                                "enum": ["open", "closed", "all"],
                                "description": "Issue state filter (default: open)",
                            },
                            "max_results": {
                                "type": "integer",
                                "description": "Maximum number of issues (1-30, default 10)",
                            },
                        },
                        "required": ["owner", "repo"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "github_search_repos",
                    "description": "Search GitHub repositories by query.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "query": {"type": "string", "description": "Search query (e.g., 'fastapi framework language:python')"},
                            "max_results": {
                                "type": "integer",
                                "description": "Maximum number of results (1-20, default 5)",
                            },
                        },
                        "required": ["query"],
                    },
                },
            },
        ]

    def _get_headers(self) -> dict:
        import os
        headers = {"Accept": "application/vnd.github.v3+json"}
        token = os.environ.get("GITHUB_TOKEN")
        if token:
            headers["Authorization"] = f"Bearer {token}"
        return headers

    async def execute(self, tool_name: str, arguments: dict) -> str:
        match tool_name:
            case "github_get_repo":
                return await self._get_repo(arguments)
            case "github_list_issues":
                return await self._list_issues(arguments)
            case "github_search_repos":
                return await self._search_repos(arguments)
            case _:
                return json.dumps({"error": f"Unknown tool: {tool_name}"})

    async def _get_repo(self, arguments: dict) -> str:
        owner = arguments.get("owner", "")
        repo = arguments.get("repo", "")
        if not owner or not repo:
            return json.dumps({"error": "owner and repo are required"})

        try:
            import httpx
            async with httpx.AsyncClient(timeout=10) as client:
                r = await client.get(
                    f"https://api.github.com/repos/{owner}/{repo}",
                    headers=self._get_headers(),
                )
                if r.status_code == 404:
                    return json.dumps({"error": f"Repository not found: {owner}/{repo}"})
                r.raise_for_status()
                data = r.json()

            return json.dumps({
                "full_name": data.get("full_name"),
                "description": data.get("description"),
                "language": data.get("language"),
                "stars": data.get("stargazers_count"),
                "forks": data.get("forks_count"),
                "open_issues": data.get("open_issues_count"),
                "created_at": data.get("created_at"),
                "updated_at": data.get("updated_at"),
                "topics": data.get("topics", []),
                "html_url": data.get("html_url"),
            })
        except Exception as e:
            return json.dumps({"error": f"GitHub API error: {str(e)[:200]}"})

    async def _list_issues(self, arguments: dict) -> str:
        owner = arguments.get("owner", "")
        repo = arguments.get("repo", "")
        state = arguments.get("state", "open")
        max_results = min(max(arguments.get("max_results", 10), 1), 30)

        if not owner or not repo:
            return json.dumps({"error": "owner and repo are required"})

        try:
            import httpx
            async with httpx.AsyncClient(timeout=10) as client:
                r = await client.get(
                    f"https://api.github.com/repos/{owner}/{repo}/issues",
                    headers=self._get_headers(),
                    params={"state": state, "per_page": max_results},
                )
                r.raise_for_status()
                data = r.json()

            issues = []
            for item in data[:max_results]:
                issues.append({
                    "number": item.get("number"),
                    "title": item.get("title"),
                    "state": item.get("state"),
                    "author": item.get("user", {}).get("login"),
                    "labels": [label.get("name") for label in item.get("labels", [])],
                    "created_at": item.get("created_at"),
                    "html_url": item.get("html_url"),
                    "is_pull_request": "pull_request" in item,
                })

            return json.dumps({"issues": issues, "count": len(issues)})
        except Exception as e:
            return json.dumps({"error": f"GitHub API error: {str(e)[:200]}"})

    async def _search_repos(self, arguments: dict) -> str:
        query = arguments.get("query", "")
        max_results = min(max(arguments.get("max_results", 5), 1), 20)

        if not query.strip():
            return json.dumps({"error": "Search query is required"})

        try:
            import httpx
            async with httpx.AsyncClient(timeout=10) as client:
                r = await client.get(
                    "https://api.github.com/search/repositories",
                    headers=self._get_headers(),
                    params={"q": query, "per_page": max_results, "sort": "stars"},
                )
                r.raise_for_status()
                data = r.json()

            repos = []
            for item in data.get("items", [])[:max_results]:
                repos.append({
                    "full_name": item.get("full_name"),
                    "description": (item.get("description") or "")[:200],
                    "language": item.get("language"),
                    "stars": item.get("stargazers_count"),
                    "html_url": item.get("html_url"),
                })

            return json.dumps({"query": query, "repos": repos, "count": len(repos)})
        except Exception as e:
            return json.dumps({"error": f"GitHub API error: {str(e)[:200]}"})


def register(registry):
    """Register this skill with the skill registry."""
    registry.register(GitHubSkill())
