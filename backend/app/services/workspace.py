"""Company Workspace — shared file storage per company.

Each company gets a directory where its agents can read/write files
to share data, reports, and artifacts with each other.
"""

import json
import logging
import os
import shutil
from pathlib import Path

logger = logging.getLogger(__name__)

# Base directory for all company workspaces
_WORKSPACE_ROOT = Path("/tmp/ai-holding/workspaces")


def _ensure_root():
    """Create the root workspace directory if needed."""
    _WORKSPACE_ROOT.mkdir(parents=True, exist_ok=True)


def get_workspace_path(company_id: str) -> Path:
    """Get the workspace directory for a company. Creates it if needed."""
    # Sanitize company_id to prevent path traversal
    safe_id = company_id.replace("/", "").replace("..", "").replace("\\", "")
    if not safe_id:
        raise ValueError("Invalid company_id")
    path = _WORKSPACE_ROOT / safe_id
    path.mkdir(parents=True, exist_ok=True)
    return path


def write_file(company_id: str, filename: str, content: str) -> dict:
    """Write a file to the company workspace."""
    _ensure_root()
    workspace = get_workspace_path(company_id)

    # Sanitize filename — prevent path traversal
    safe_name = Path(filename).name
    if not safe_name or safe_name.startswith("."):
        return {"success": False, "error": f"Invalid filename: {filename}"}

    filepath = workspace / safe_name
    filepath.write_text(content, encoding="utf-8")
    size = filepath.stat().st_size

    logger.info(f"Workspace write: {company_id}/{safe_name} ({size} bytes)")
    return {
        "success": True,
        "path": f"{company_id}/{safe_name}",
        "size_bytes": size,
    }


def read_file(company_id: str, filename: str) -> dict:
    """Read a file from the company workspace."""
    workspace = get_workspace_path(company_id)
    safe_name = Path(filename).name
    filepath = workspace / safe_name

    if not filepath.exists():
        return {"success": False, "error": f"File not found: {filename}"}

    content = filepath.read_text(encoding="utf-8")
    return {
        "success": True,
        "filename": safe_name,
        "content": content,
        "size_bytes": len(content.encode("utf-8")),
    }


def list_files(company_id: str) -> dict:
    """List all files in the company workspace."""
    workspace = get_workspace_path(company_id)
    files = []
    for entry in sorted(workspace.iterdir()):
        if entry.is_file():
            stat = entry.stat()
            files.append({
                "name": entry.name,
                "size_bytes": stat.st_size,
                "modified": stat.st_mtime,
            })
    return {
        "company_id": company_id,
        "files": files,
        "count": len(files),
    }


def delete_file(company_id: str, filename: str) -> dict:
    """Delete a file from the company workspace."""
    workspace = get_workspace_path(company_id)
    safe_name = Path(filename).name
    filepath = workspace / safe_name

    if not filepath.exists():
        return {"success": False, "error": f"File not found: {filename}"}

    filepath.unlink()
    return {"success": True, "deleted": safe_name}


def delete_workspace(company_id: str) -> dict:
    """Delete an entire company workspace."""
    workspace = get_workspace_path(company_id)
    if workspace.exists():
        shutil.rmtree(workspace)
        logger.info(f"Deleted workspace for company {company_id}")
    return {"success": True, "company_id": company_id}
