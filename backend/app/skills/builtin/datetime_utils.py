"""DateTime & Utility skill — time, date, calculations, and general utilities."""

import json
import logging
import math
from datetime import datetime, timezone

from app.skills.base import Skill, SkillCategory, SkillMetadata

logger = logging.getLogger(__name__)


class DateTimeSkill(Skill):
    """Date, time, and calculation utilities."""

    @property
    def name(self) -> str:
        return "datetime_utils"

    @property
    def display_name(self) -> str:
        return "Date & Time Utilities"

    @property
    def description(self) -> str:
        return "Get current date/time, calculate time differences, and perform unit conversions."

    @property
    def version(self) -> str:
        return "1.0.0"

    @property
    def category(self) -> SkillCategory:
        return SkillCategory.UTILITY

    @property
    def metadata(self) -> SkillMetadata:
        return SkillMetadata(
            author="system",
            tags=["time", "date", "utility", "calculation"],
            icon="🕐",
        )

    def get_tools_schema(self) -> list[dict]:
        return [
            {
                "type": "function",
                "function": {
                    "name": "get_current_time",
                    "description": "Get the current date and time in UTC and optional timezone.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "timezone_offset": {
                                "type": "number",
                                "description": "UTC offset in hours (e.g., 3 for UTC+3, -5 for UTC-5). Default is 0 (UTC).",
                            },
                        },
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "calculate",
                    "description": (
                        "Evaluate a mathematical expression safely. "
                        "Supports +, -, *, /, **, %, sqrt, sin, cos, log, pi, e."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "expression": {
                                "type": "string",
                                "description": "Mathematical expression (e.g., 'sqrt(144) + 2**3')",
                            },
                        },
                        "required": ["expression"],
                    },
                },
            },
        ]

    async def execute(self, tool_name: str, arguments: dict) -> str:
        match tool_name:
            case "get_current_time":
                return self._get_time(arguments)
            case "calculate":
                return self._calculate(arguments)
            case _:
                return json.dumps({"error": f"Unknown tool: {tool_name}"})

    def _get_time(self, arguments: dict) -> str:
        offset_hours = arguments.get("timezone_offset", 0)
        try:
            from datetime import timedelta
            tz = timezone(timedelta(hours=offset_hours))
            now = datetime.now(tz)
            return json.dumps({
                "utc": datetime.now(timezone.utc).isoformat(),
                "local": now.isoformat(),
                "timezone": f"UTC{'+' if offset_hours >= 0 else ''}{offset_hours}",
                "unix_timestamp": int(datetime.now(timezone.utc).timestamp()),
            })
        except Exception as e:
            return json.dumps({"error": str(e)})

    def _calculate(self, arguments: dict) -> str:
        expression = arguments.get("expression", "")
        if not expression.strip():
            return json.dumps({"error": "Empty expression"})

        # Allow only safe math characters and functions

        # Remove any dangerous content
        cleaned = expression.strip()
        # Check for import, exec, eval, __
        if any(forbidden in cleaned.lower() for forbidden in ['import', 'exec', 'eval', '__', 'open', 'os.', 'sys.']):
            return json.dumps({"error": "Expression contains forbidden operations"})

        # Build safe namespace
        safe_namespace = {
            "sqrt": math.sqrt, "sin": math.sin, "cos": math.cos,
            "tan": math.tan, "log": math.log, "log10": math.log10,
            "log2": math.log2, "ceil": math.ceil, "floor": math.floor,
            "abs": abs, "round": round, "pow": pow, "min": min, "max": max,
            "pi": math.pi, "e": math.e,
            "__builtins__": {},
        }

        try:
            result = eval(cleaned, safe_namespace)  # noqa: S307
            return json.dumps({"expression": expression, "result": result})
        except Exception as e:
            return json.dumps({"error": f"Calculation failed: {str(e)[:200]}"})


def register(registry):
    """Register this skill with the skill registry."""
    registry.register(DateTimeSkill())
