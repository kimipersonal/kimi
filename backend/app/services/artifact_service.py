"""Artifact Service — structured deliverables created by agents.

Artifacts are typed outputs (reports, datasets, charts, etc.) stored
in the company workspace with metadata tracking. They go beyond plain
workspace files by adding type, format, and provenance information.
"""

import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from app.services.workspace import get_workspace_path

logger = logging.getLogger(__name__)

ARTIFACT_MANIFEST = ".artifacts.json"


@dataclass
class Artifact:
    id: str
    name: str
    artifact_type: str  # report, dataset, chart, analysis, summary, code
    format: str  # markdown, json, csv, html, text
    filename: str
    agent_id: str
    agent_name: str
    company_id: str
    description: str = ""
    tags: list[str] = field(default_factory=list)
    size_bytes: int = 0
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "type": self.artifact_type,
            "format": self.format,
            "filename": self.filename,
            "agent_id": self.agent_id,
            "agent_name": self.agent_name,
            "company_id": self.company_id,
            "description": self.description,
            "tags": self.tags,
            "size_bytes": self.size_bytes,
            "created_at": self.created_at,
        }


def _load_manifest(company_id: str) -> list[dict]:
    """Load the artifact manifest for a company."""
    ws = get_workspace_path(company_id)
    manifest_path = ws / ARTIFACT_MANIFEST
    if manifest_path.exists():
        try:
            return json.loads(manifest_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return []


def _save_manifest(company_id: str, artifacts: list[dict]) -> None:
    """Save the artifact manifest."""
    ws = get_workspace_path(company_id)
    manifest_path = ws / ARTIFACT_MANIFEST
    manifest_path.write_text(json.dumps(artifacts, indent=2), encoding="utf-8")


def _extension_for_format(fmt: str) -> str:
    return {
        "markdown": ".md",
        "json": ".json",
        "csv": ".csv",
        "html": ".html",
        "text": ".txt",
    }.get(fmt, ".txt")


def create_artifact(
    company_id: str,
    agent_id: str,
    agent_name: str,
    name: str,
    content: str,
    artifact_type: str = "report",
    format: str = "markdown",
    description: str = "",
    tags: list[str] | None = None,
) -> dict:
    """Create a new artifact in the company workspace.

    Returns artifact metadata dict.
    """
    artifact_id = str(uuid4())[:8]
    ext = _extension_for_format(format)
    safe_name = "".join(c for c in name if c.isalnum() or c in " -_").strip().replace(" ", "_")
    filename = f"{safe_name}_{artifact_id}{ext}"

    # Write to artifacts/ subdirectory within workspace
    ws = get_workspace_path(company_id)
    artifacts_dir = ws / "artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    filepath = artifacts_dir / filename
    filepath.write_text(content, encoding="utf-8")
    size_bytes = filepath.stat().st_size

    artifact = Artifact(
        id=artifact_id,
        name=name,
        artifact_type=artifact_type,
        format=format,
        filename=filename,
        agent_id=agent_id,
        agent_name=agent_name,
        company_id=company_id,
        description=description,
        tags=tags or [],
        size_bytes=size_bytes,
    )

    # Update manifest
    manifest = _load_manifest(company_id)
    manifest.append(artifact.to_dict())
    _save_manifest(company_id, manifest)

    logger.info(f"Artifact created: {name} ({artifact_type}/{format}) by {agent_name}")
    return artifact.to_dict()


def list_artifacts(
    company_id: str,
    artifact_type: str | None = None,
    agent_id: str | None = None,
) -> list[dict]:
    """List all artifacts for a company, optionally filtered."""
    manifest = _load_manifest(company_id)
    if artifact_type:
        manifest = [a for a in manifest if a.get("type") == artifact_type]
    if agent_id:
        manifest = [a for a in manifest if a.get("agent_id") == agent_id]
    return manifest


def get_artifact(company_id: str, artifact_id: str) -> dict | None:
    """Get artifact metadata by ID."""
    manifest = _load_manifest(company_id)
    for a in manifest:
        if a.get("id") == artifact_id:
            return a
    return None


def read_artifact_content(company_id: str, artifact_id: str) -> dict:
    """Read the content of an artifact."""
    artifact = get_artifact(company_id, artifact_id)
    if not artifact:
        return {"success": False, "error": f"Artifact {artifact_id} not found"}

    ws = get_workspace_path(company_id)
    filepath = ws / "artifacts" / artifact["filename"]
    if not filepath.exists():
        return {"success": False, "error": f"Artifact file not found: {artifact['filename']}"}

    content = filepath.read_text(encoding="utf-8")
    return {
        "success": True,
        "filename": artifact["filename"],
        "content": content,
        "size_bytes": len(content.encode("utf-8")),
    }
