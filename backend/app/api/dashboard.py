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


@router.get("/settings")
async def get_settings():
    """Get current system settings."""
    from app.config import get_settings as _get_settings

    s = _get_settings()
    return {
        "llm_model_fast": s.llm_fast,
        "llm_model_smart": s.llm_smart,
        "llm_model_reasoning": s.llm_reasoning,
        "auto_approval_threshold": s.auto_approve_below_usd,
        "vertex_project": s.gcp_project_id,
        "vertex_location": s.gcp_region,
        "telegram_bot_token": "***" if s.telegram_bot_token else None,
        "total_agents": registry.count,
        "active_agents": registry.active_count,
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
