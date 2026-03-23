"""Approval service — DB-backed HITL approval flow with timeout."""

import logging
from datetime import datetime, timezone, timedelta
from uuid import uuid4

from sqlalchemy import select, update

from app.db.database import async_session
from app.db.models import Approval, ApprovalStatus
from app.config import get_settings
from app.services.event_bus import event_bus

logger = logging.getLogger(__name__)


async def create_approval(
    agent_id: str,
    description: str,
    category: str = "general",
    details: dict | None = None,
    task_id: str | None = None,
) -> dict:
    """Create a new approval request in DB and broadcast it."""
    approval_id = str(uuid4())
    now = datetime.now(timezone.utc)

    async with async_session() as session:
        approval = Approval(
            id=approval_id,
            agent_id=agent_id,
            task_id=task_id,
            description=description,
            category=category,
            details=details,
            status=ApprovalStatus.PENDING,
            requested_at=now,
        )
        session.add(approval)
        await session.commit()

    result = {
        "id": approval_id,
        "agent_id": agent_id,
        "description": description,
        "category": category,
        "details": details,
        "status": "pending",
        "requested_at": now.isoformat(),
    }

    await event_bus.broadcast("approval_request", result, agent_id=agent_id)
    logger.info(f"Approval request created: {approval_id} ({category})")
    return result


async def decide_approval(
    approval_id: str,
    approved: bool,
    reason: str | None = None,
) -> dict | None:
    """Approve or reject a pending approval."""
    now = datetime.now(timezone.utc)
    new_status = ApprovalStatus.APPROVED if approved else ApprovalStatus.REJECTED

    async with async_session() as session:
        db_approval = await session.get(Approval, approval_id)
        if not db_approval:
            return None
        if db_approval.status != ApprovalStatus.PENDING:
            return None

        db_approval.status = new_status
        db_approval.decision_reason = reason
        db_approval.decided_at = now
        await session.commit()

        result = {
            "id": db_approval.id,
            "agent_id": db_approval.agent_id,
            "description": db_approval.description,
            "category": db_approval.category,
            "status": new_status.value,
            "decision_reason": reason,
            "requested_at": db_approval.requested_at.isoformat()
            if db_approval.requested_at
            else None,
            "decided_at": now.isoformat(),
        }

    await event_bus.broadcast("approval_decided", result, agent_id=result["agent_id"])
    logger.info(f"Approval {approval_id} → {new_status.value}")
    return result


async def list_approvals(
    status: str | None = None,
    agent_id: str | None = None,
    limit: int = 100,
) -> list[dict]:
    """List approval requests from DB."""
    async with async_session() as session:
        query = select(Approval).order_by(Approval.requested_at.desc()).limit(limit)

        if status:
            status_enum = ApprovalStatus(status)
            query = query.where(Approval.status == status_enum)
        if agent_id:
            query = query.where(Approval.agent_id == agent_id)

        result = await session.execute(query)
        approvals = result.scalars().all()

        return [
            {
                "id": a.id,
                "agent_id": a.agent_id,
                "task_id": a.task_id,
                "description": a.description,
                "category": a.category,
                "details": a.details,
                "status": a.status.value,
                "decision_reason": a.decision_reason,
                "requested_at": a.requested_at.isoformat() if a.requested_at else None,
                "decided_at": a.decided_at.isoformat() if a.decided_at else None,
            }
            for a in approvals
        ]


async def expire_old_approvals() -> int:
    """Expire approvals that have exceeded the timeout. Returns count expired."""
    settings = get_settings()
    cutoff = datetime.now(timezone.utc) - timedelta(
        hours=settings.approval_timeout_hours
    )

    async with async_session() as session:
        stmt = (
            update(Approval)
            .where(Approval.status == ApprovalStatus.PENDING)
            .where(Approval.requested_at < cutoff)
            .values(
                status=ApprovalStatus.EXPIRED,
                decision_reason="Expired: no decision within timeout period",
                decided_at=datetime.now(timezone.utc),
            )
        )
        result = await session.execute(stmt)
        await session.commit()
        count = result.rowcount or 0

    if count > 0:
        logger.info(f"Expired {count} stale approval(s)")
        await event_bus.broadcast("approvals_expired", {"count": count})

    return count
