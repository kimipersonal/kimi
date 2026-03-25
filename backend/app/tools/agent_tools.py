"""Browser tool schemas and executor for agents.

Provides browse_url and screenshot_url tools via the BrowserService.
"""

import json

BROWSER_TOOLS_SCHEMA: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "browse_url",
            "description": (
                "Navigate to a URL and extract the page's text content. "
                "Returns the page title and text (up to 10K chars). "
                "Use for research, reading articles, checking websites."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "The URL to browse (must be http or https)",
                    },
                },
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "screenshot_url",
            "description": (
                "Take a screenshot of a web page. "
                "Returns a base64-encoded PNG image (1280x720). "
                "Use when visual layout matters."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "The URL to screenshot (must be http or https)",
                    },
                },
                "required": ["url"],
            },
        },
    },
]

SANDBOX_TOOLS_SCHEMA: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "execute_code",
            "description": (
                "Execute Python code in a sandboxed Docker container. "
                "Has pandas, numpy available. Network disabled. 30s timeout. 256MB memory. "
                "Use for calculations, data analysis, testing hypotheses."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "code": {
                        "type": "string",
                        "description": "Python code to execute",
                    },
                    "language": {
                        "type": "string",
                        "enum": ["python"],
                        "description": "Programming language (currently only python)",
                    },
                },
                "required": ["code"],
            },
        },
    },
]


async def execute_browser_tool(tool_name: str, arguments: dict) -> str:
    """Execute a browser tool and return JSON result."""
    from app.services.browser_service import browser_service

    match tool_name:
        case "browse_url":
            result = await browser_service.browse_url(arguments["url"])
        case "screenshot_url":
            result = await browser_service.screenshot_url(arguments["url"])
        case _:
            result = {"error": f"Unknown browser tool: {tool_name}"}

    return json.dumps(result, default=str)


async def execute_sandbox_tool(tool_name: str, arguments: dict, network_enabled: bool = False) -> str:
    """Execute a sandbox tool and return JSON result."""
    from app.services.sandbox_service import sandbox_service

    if tool_name != "execute_code":
        return json.dumps({"error": f"Unknown sandbox tool: {tool_name}"})

    result = await sandbox_service.execute(
        code=arguments["code"],
        language=arguments.get("language", "python"),
        network_enabled=network_enabled,
    )

    return json.dumps({
        "success": result.success,
        "output": result.output,
        "error": result.error,
        "exit_code": result.exit_code,
        "timed_out": result.timed_out,
    })
