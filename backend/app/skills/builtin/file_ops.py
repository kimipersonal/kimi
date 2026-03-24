"""File Operations skill — read, write, list files in a sandboxed workspace."""

import json
import logging
from pathlib import Path

from app.skills.base import Skill, SkillCategory, SkillMetadata

logger = logging.getLogger(__name__)

# Sandboxed workspace root — agents can only operate within this directory
WORKSPACE_ROOT = Path("/tmp/ai-holding-workspace")


class FileOpsSkill(Skill):
    """File operations within a sandboxed workspace directory."""

    @property
    def name(self) -> str:
        return "file_ops"

    @property
    def display_name(self) -> str:
        return "File Operations"

    @property
    def description(self) -> str:
        return "Read, write, and list files within a sandboxed workspace. Useful for generating reports, saving data, and managing documents."

    @property
    def version(self) -> str:
        return "1.0.0"

    @property
    def category(self) -> SkillCategory:
        return SkillCategory.PRODUCTIVITY

    @property
    def metadata(self) -> SkillMetadata:
        return SkillMetadata(
            author="system",
            tags=["files", "read", "write", "workspace"],
            icon="📁",
        )

    async def initialize(self):
        WORKSPACE_ROOT.mkdir(parents=True, exist_ok=True)

    def get_tools_schema(self) -> list[dict]:
        return [
            {
                "type": "function",
                "function": {
                    "name": "read_file",
                    "description": "Read the contents of a file in the workspace.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "path": {
                                "type": "string",
                                "description": "Relative path within the workspace (e.g., 'reports/summary.txt')",
                            },
                        },
                        "required": ["path"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "write_file",
                    "description": "Write content to a file in the workspace. Creates parent directories if needed.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "path": {
                                "type": "string",
                                "description": "Relative path within the workspace (e.g., 'reports/summary.txt')",
                            },
                            "content": {
                                "type": "string",
                                "description": "Content to write to the file",
                            },
                        },
                        "required": ["path", "content"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "list_files",
                    "description": "List files and directories in a workspace path.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "path": {
                                "type": "string",
                                "description": "Relative directory path (default: root of workspace)",
                            },
                        },
                    },
                },
            },
        ]

    def _resolve_path(self, relative: str) -> Path | None:
        """Resolve a relative path within the workspace, preventing path traversal."""
        try:
            resolved = (WORKSPACE_ROOT / relative).resolve()
            # Prevent path traversal outside workspace
            if not str(resolved).startswith(str(WORKSPACE_ROOT.resolve())):
                return None
            return resolved
        except (ValueError, OSError):
            return None

    async def execute(self, tool_name: str, arguments: dict) -> str:
        match tool_name:
            case "read_file":
                return self._read_file(arguments)
            case "write_file":
                return self._write_file(arguments)
            case "list_files":
                return self._list_files(arguments)
            case _:
                return json.dumps({"error": f"Unknown tool: {tool_name}"})

    def _read_file(self, arguments: dict) -> str:
        path = arguments.get("path", "")
        if not path:
            return json.dumps({"error": "Path is required"})

        resolved = self._resolve_path(path)
        if not resolved:
            return json.dumps({"error": "Invalid path — path traversal not allowed"})

        if not resolved.exists():
            return json.dumps({"error": f"File not found: {path}"})
        if not resolved.is_file():
            return json.dumps({"error": f"Not a file: {path}"})

        try:
            content = resolved.read_text(encoding="utf-8")
            # Limit output size
            if len(content) > 50_000:
                content = content[:50_000] + "\n... (truncated at 50K chars)"
            return json.dumps({"path": path, "content": content, "size": resolved.stat().st_size})
        except Exception as e:
            return json.dumps({"error": f"Read failed: {str(e)[:200]}"})

    def _write_file(self, arguments: dict) -> str:
        path = arguments.get("path", "")
        content = arguments.get("content", "")
        if not path:
            return json.dumps({"error": "Path is required"})

        resolved = self._resolve_path(path)
        if not resolved:
            return json.dumps({"error": "Invalid path — path traversal not allowed"})

        try:
            resolved.parent.mkdir(parents=True, exist_ok=True)
            resolved.write_text(content, encoding="utf-8")
            return json.dumps({"path": path, "size": len(content), "status": "written"})
        except Exception as e:
            return json.dumps({"error": f"Write failed: {str(e)[:200]}"})

    def _list_files(self, arguments: dict) -> str:
        path = arguments.get("path", "")
        resolved = self._resolve_path(path) if path else WORKSPACE_ROOT

        if not resolved:
            return json.dumps({"error": "Invalid path"})
        if not resolved.exists():
            return json.dumps({"error": f"Directory not found: {path}"})
        if not resolved.is_dir():
            return json.dumps({"error": f"Not a directory: {path}"})

        try:
            entries = []
            for entry in sorted(resolved.iterdir()):
                rel = str(entry.relative_to(WORKSPACE_ROOT))
                entries.append({
                    "name": entry.name,
                    "path": rel,
                    "type": "directory" if entry.is_dir() else "file",
                    "size": entry.stat().st_size if entry.is_file() else None,
                })
            return json.dumps({"path": path or "/", "entries": entries, "count": len(entries)})
        except Exception as e:
            return json.dumps({"error": f"List failed: {str(e)[:200]}"})


def register(registry):
    """Register this skill with the skill registry."""
    registry.register(FileOpsSkill())
