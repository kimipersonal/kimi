"""Dashboard API — overview, approvals, activity logs, settings, messaging."""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.agents.registry import registry
from app.models.schemas import DashboardOverview, ApprovalDecision
from app.services.event_bus import event_bus

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])


@router.get("/overview", response_model=DashboardOverview)
async def get_overview():
    """Get high-level dashboard overview."""
    from app.services.approval_service import list_approvals
    from app.services.company_manager import list_companies
    from app.services.cost_tracker import cost_tracker

    companies = await list_companies()
    pending = await list_approvals(status="pending")

    return DashboardOverview(
        total_companies=len(companies),
        total_agents=registry.count,
        active_agents=registry.active_count,
        pending_approvals=len(pending),
        tasks_today=0,
        total_cost_today=cost_tracker.get_total_cost_today(),
    )


# --- Approvals ---


@router.get("/approvals")
async def get_approvals(status: str | None = None):
    """List all approval requests, optionally filtered by status."""
    from app.services.approval_service import list_approvals

    return await list_approvals(status=status)


@router.post("/approvals/{approval_id}/decide")
async def decide_approval(approval_id: str, decision: ApprovalDecision):
    """Approve or reject a pending approval request."""
    from app.services.approval_service import decide_approval as _decide

    result = await _decide(approval_id, decision.approved, decision.reason)
    if not result:
        raise HTTPException(
            status_code=404, detail="Approval not found or already decided"
        )
    return result


# --- Activity Logs ---


@router.get("/logs")
async def get_activity_logs(agent_id: str | None = None, limit: int = 100):
    """Get recent activity logs from the event bus history."""
    logs = event_bus.history
    if agent_id:
        logs = [e for e in logs if e.get("agent_id") == agent_id]
    return logs[-limit:]


# --- Agent Conversations ---


@router.get("/conversations/{agent_id}")
async def get_conversations(agent_id: str):
    """Get chat conversation history for a specific agent."""
    agent = registry.get(agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    from app.agents.ceo import CEOAgent

    if isinstance(agent, CEOAgent):
        return agent._conversations[-100:]
    return []


# --- Messages ---


@router.get("/messages")
async def get_messages(
    agent_id: str | None = None, message_type: str | None = None, limit: int = 50
):
    """Get inter-agent messages from DB."""
    from app.services.messaging import get_messages as _get

    return await _get(agent_id=agent_id, message_type=message_type, limit=limit)


# --- Companies (from DB) ---


@router.get("/companies")
async def list_companies_detail():
    """List all companies with their agents from DB."""
    from app.services.company_manager import list_companies

    return await list_companies()


# --- Trading Evaluation ---


@router.get("/trading/evaluate")
async def evaluate_trading_platforms():
    """Evaluate all configured trading platforms and rank them."""
    from app.services.trading.manager import evaluate_all_platforms

    results = await evaluate_all_platforms()
    best = results[0] if results and results[0].get("score", 0) > 0 else None
    return {
        "platforms": results,
        "recommended": best.get("platform") if best else None,
        "total_configured": len(results),
    }


# --- Settings ---


class SettingsUpdate(BaseModel):
    auto_approval_threshold: float | None = None
    llm_fast: str | None = None
    llm_smart: str | None = None
    llm_reasoning: str | None = None


@router.get("/settings")
async def get_settings():
    """Get current system settings."""
    from app.config import get_settings as _get_settings
    from app.services.llm_router import MODEL_TIERS

    s = _get_settings()
    return {
        "llm_model_fast": MODEL_TIERS["fast"],
        "llm_model_smart": MODEL_TIERS["smart"],
        "llm_model_reasoning": MODEL_TIERS["reasoning"],
        "auto_approval_threshold": s.auto_approve_below_usd,
        "vertex_project": s.gcp_project_id,
        "vertex_location": s.gcp_region,
        "telegram_bot_token": "***" if s.telegram_bot_token else None,
        "total_agents": registry.count,
        "active_agents": registry.active_count,
    }


@router.put("/settings")
async def update_settings(body: SettingsUpdate):
    """Update model tier assignments at runtime (no restart needed)."""
    from fastapi import HTTPException
    from app.services.llm_router import MODEL_TIERS, update_tier

    changes: dict[str, str] = {}
    tier_fields = {"llm_fast": "fast", "llm_smart": "smart", "llm_reasoning": "reasoning"}
    for field, tier in tier_fields.items():
        value = getattr(body, field, None)
        if value is not None:
            try:
                update_tier(tier, value)
            except ValueError as e:
                raise HTTPException(status_code=400, detail=str(e))
            changes[tier] = value

    return {
        "updated": changes,
        "current_tiers": {
            "fast": MODEL_TIERS["fast"],
            "smart": MODEL_TIERS["smart"],
            "reasoning": MODEL_TIERS["reasoning"],
        },
    }


@router.get("/models")
async def get_available_models():
    """Get all available Vertex AI models for agent selection."""
    from app.services.llm_router import AVAILABLE_MODELS, MODEL_TIERS

    return {
        "models": AVAILABLE_MODELS,
        "current_tiers": {
            "fast": MODEL_TIERS["fast"],
            "smart": MODEL_TIERS["smart"],
            "reasoning": MODEL_TIERS["reasoning"],
        },
    }


# --- Cost Tracking ---


@router.get("/costs")
async def get_cost_overview():
    """Get cost tracking overview with per-agent breakdowns."""
    from app.services.cost_tracker import cost_tracker

    return cost_tracker.get_overview()


@router.get("/costs/{agent_id}")
async def get_agent_cost(agent_id: str):
    """Get cost summary for a specific agent."""
    from app.services.cost_tracker import cost_tracker

    return cost_tracker.get_agent_summary(agent_id)


# --- Health ---


@router.get("/health")
async def get_health():
    """Get system health status."""
    from app.services.health_monitor import health_monitor

    return health_monitor.get_health()


@router.get("/circuits")
async def get_circuit_breakers():
    """Get circuit breaker statuses for all models."""
    from app.services.circuit_breaker import circuit_registry

    return {"circuits": circuit_registry.get_all_status()}


# --- Audit Log ---


@router.get("/audit")
async def get_audit_log(
    agent_id: str | None = None,
    action: str | None = None,
    source: str | None = None,
    limit: int = 100,
):
    """Get audit log entries with optional filters."""
    from app.services.audit_log import audit_log

    return await audit_log.get_entries(
        agent_id=agent_id, action=action, source=source, limit=limit
    )


@router.get("/audit/stats")
async def get_audit_stats():
    """Get audit log summary statistics."""
    from app.services.audit_log import audit_log

    return await audit_log.get_stats()


# --- Performance ---


@router.get("/performance")
async def get_performance_scores():
    """Get performance scores for all agents."""
    from app.services.performance_tracker import performance_tracker

    return await performance_tracker.get_all_scores()


@router.get("/performance/leaderboard")
async def get_leaderboard(limit: int = 10):
    """Get top agents by performance score."""
    from app.services.performance_tracker import performance_tracker

    return await performance_tracker.get_leaderboard(limit=limit)


@router.get("/performance/{agent_id}")
async def get_agent_performance(agent_id: str):
    """Get performance score for a specific agent."""
    from app.services.performance_tracker import performance_tracker

    return await performance_tracker.get_score(agent_id)


# --- Backup / Restore ---


@router.get("/backup")
async def create_backup_endpoint():
    """Export the entire system state as JSON."""
    from app.services.backup import create_backup

    return await create_backup()


@router.post("/restore")
async def restore_backup_endpoint(data: dict):
    """Restore system state from a JSON backup."""
    from app.services.backup import restore_backup

    return await restore_backup(data)


# --- Task Queue ---


@router.get("/tasks")
async def list_async_tasks(status: str | None = None):
    """List all async tasks, optionally filtered by status."""
    from app.services.task_queue import task_queue, AsyncTaskStatus

    if status:
        try:
            s = AsyncTaskStatus(status)
            return {"tasks": task_queue.get_all_tasks(status=s), "running": task_queue.get_running_count()}
        except ValueError:
            pass
    return {"tasks": task_queue.get_all_tasks(), "running": task_queue.get_running_count()}


@router.get("/tasks/{task_id}")
async def get_task_status(task_id: str):
    """Get the status and result of a specific async task."""
    from app.services.task_queue import task_queue

    task = task_queue.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return task.to_dict()


# --- Company Workspace ---


@router.get("/workspace/{company_id}")
async def list_workspace_files(company_id: str):
    """List all files in a company's shared workspace."""
    from app.services.workspace import list_files

    return list_files(company_id)


@router.get("/workspace/{company_id}/{filename}")
async def read_workspace_file(company_id: str, filename: str):
    """Read a file from a company's shared workspace."""
    from app.services.workspace import read_file

    result = read_file(company_id, filename)
    if not result.get("success", False):
        raise HTTPException(status_code=404, detail=result.get("error", "File not found"))
    return result


# --- Company Memory ---


@router.get("/memory/{company_id}")
async def get_company_memory_stats(company_id: str):
    """Get shared memory statistics for a company."""
    from app.services.company_memory import get_company_memory_count

    count = await get_company_memory_count(company_id)
    return {"company_id": company_id, "memory_count": count}


# --- Artifacts ---


@router.get("/artifacts/{company_id}")
async def list_company_artifacts(company_id: str, artifact_type: str | None = None):
    """List all artifacts for a company."""
    from app.services.artifact_service import list_artifacts

    return {"artifacts": list_artifacts(company_id, artifact_type=artifact_type)}


@router.get("/artifacts/{company_id}/{artifact_id}")
async def get_artifact_content(company_id: str, artifact_id: str):
    """Read artifact content by ID."""
    from app.services.artifact_service import read_artifact_content

    result = read_artifact_content(company_id, artifact_id)
    if not result.get("success", False):
        raise HTTPException(status_code=404, detail=result.get("error", "Artifact not found"))
    return result


# --- Tool Analytics ---


@router.get("/tool-analytics")
async def get_tool_analytics():
    """Get aggregated tool usage statistics."""
    from app.services.tool_analytics import tool_analytics
    return tool_analytics.get_stats()


@router.get("/tool-analytics/recent")
async def get_recent_tool_calls(limit: int = 50):
    """Get most recent tool call records."""
    from app.services.tool_analytics import tool_analytics
    return {"records": tool_analytics.get_recent(min(limit, 200))}


@router.get("/tool-analytics/agent/{agent_id}")
async def get_agent_tool_analytics(agent_id: str):
    """Get tool usage stats for a specific agent."""
    from app.services.tool_analytics import tool_analytics
    return tool_analytics.get_agent_stats(agent_id)
