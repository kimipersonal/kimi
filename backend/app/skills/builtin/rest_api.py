"""REST API skill — call any external REST API with configurable auth.

Allows agents to make HTTP requests to external APIs with
support for API keys, Bearer tokens, and basic auth.
"""

import json
import logging
from urllib.parse import urlparse

from app.skills.base import Skill, SkillCategory, SkillMetadata

logger = logging.getLogger(__name__)

# Blocked hosts for security
_BLOCKED_HOSTS = {"localhost", "127.0.0.1", "0.0.0.0", "169.254.169.254", "[::1]"}
_BLOCKED_PREFIXES = ("10.", "172.16.", "172.17.", "172.18.", "172.19.", "172.20.",
                     "172.21.", "172.22.", "172.23.", "172.24.", "172.25.", "172.26.",
                     "172.27.", "172.28.", "172.29.", "172.30.", "172.31.", "192.168.")
MAX_RESPONSE_SIZE = 50000  # 50KB max response


class RestAPISkill(Skill):
    """Make HTTP requests to external REST APIs."""

    @property
    def name(self) -> str:
        return "rest_api"

    @property
    def display_name(self) -> str:
        return "REST API"

    @property
    def description(self) -> str:
        return "Call external REST APIs with configurable authentication. Supports GET, POST, PUT, PATCH, DELETE methods."

    @property
    def version(self) -> str:
        return "1.0.0"

    @property
    def category(self) -> SkillCategory:
        return SkillCategory.DATA

    @property
    def metadata(self) -> SkillMetadata:
        return SkillMetadata(
            author="system",
            tags=["api", "http", "rest", "integration"],
            icon="🔗",
        )

    def get_tools_schema(self) -> list[dict]:
        return [
            {
                "type": "function",
                "function": {
                    "name": "http_request",
                    "description": (
                        "Make an HTTP request to an external REST API. "
                        "Supports GET, POST, PUT, PATCH, DELETE methods with optional auth."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "url": {"type": "string", "description": "Full URL to call (must be HTTPS for security)"},
                            "method": {
                                "type": "string",
                                "enum": ["GET", "POST", "PUT", "PATCH", "DELETE"],
                                "description": "HTTP method (default: GET)",
                            },
                            "headers": {
                                "type": "object",
                                "description": "Additional HTTP headers as key-value pairs",
                            },
                            "body": {
                                "type": "object",
                                "description": "JSON request body (for POST/PUT/PATCH)",
                            },
                            "auth_type": {
                                "type": "string",
                                "enum": ["none", "bearer", "api_key", "basic"],
                                "description": "Authentication type (default: none)",
                            },
                            "auth_value": {
                                "type": "string",
                                "description": "Auth credential: Bearer token, API key value, or 'user:password' for basic auth",
                            },
                            "api_key_header": {
                                "type": "string",
                                "description": "Header name for API key auth (default: X-API-Key)",
                            },
                        },
                        "required": ["url"],
                    },
                },
            },
        ]

    async def execute(self, tool_name: str, arguments: dict) -> str:
        if tool_name != "http_request":
            return json.dumps({"error": f"Unknown tool: {tool_name}"})
        return await self._http_request(arguments)

    async def _http_request(self, args: dict) -> str:
        """Execute an HTTP request with safety checks."""
        url = args.get("url", "").strip()
        method = args.get("method", "GET").upper()
        headers = args.get("headers") or {}
        body = args.get("body")
        auth_type = args.get("auth_type", "none")
        auth_value = args.get("auth_value", "")
        api_key_header = args.get("api_key_header", "X-API-Key")

        # Validate URL
        if not url:
            return json.dumps({"error": "URL is required"})

        parsed = urlparse(url)
        if parsed.scheme not in ("https", "http"):
            return json.dumps({"error": "Only HTTP/HTTPS URLs are supported"})

        # Block internal/private IPs (SSRF protection)
        hostname = parsed.hostname or ""
        if hostname in _BLOCKED_HOSTS or any(hostname.startswith(p) for p in _BLOCKED_PREFIXES):
            return json.dumps({"error": "Requests to internal/private networks are not allowed"})

        # Validate method
        if method not in ("GET", "POST", "PUT", "PATCH", "DELETE"):
            return json.dumps({"error": f"Unsupported method: {method}"})

        # Apply auth
        if auth_type == "bearer" and auth_value:
            headers["Authorization"] = f"Bearer {auth_value}"
        elif auth_type == "api_key" and auth_value:
            headers[api_key_header] = auth_value
        elif auth_type == "basic" and auth_value:
            import base64
            encoded = base64.b64encode(auth_value.encode()).decode()
            headers["Authorization"] = f"Basic {encoded}"

        try:
            import httpx
            async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
                kwargs: dict = {"headers": headers}
                if body and method in ("POST", "PUT", "PATCH"):
                    kwargs["json"] = body

                resp = await client.request(method, url, **kwargs)

            # Truncate large responses
            content = resp.text[:MAX_RESPONSE_SIZE]
            truncated = len(resp.text) > MAX_RESPONSE_SIZE

            # Try to parse as JSON
            try:
                parsed_body = json.loads(content)
            except (json.JSONDecodeError, ValueError):
                parsed_body = None

            return json.dumps({
                "status_code": resp.status_code,
                "headers": dict(list(resp.headers.items())[:20]),  # limit headers
                "body": parsed_body if parsed_body is not None else content,
                "content_type": resp.headers.get("content-type", ""),
                "truncated": truncated,
            })

        except ImportError:
            return json.dumps({"error": "httpx not installed. Run: pip install httpx"})
        except httpx.TimeoutException:
            return json.dumps({"error": f"Request timed out after 30s: {url}"})
        except Exception as e:
            logger.error(f"HTTP request failed: {e}")
            return json.dumps({"error": f"Request failed: {str(e)[:300]}"})


def register(registry):
    """Register this skill with the skill registry."""
    registry.register(RestAPISkill())
