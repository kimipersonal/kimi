"""Webhook API — receive external triggers (GitHub, trading alerts, etc.)."""

import hashlib
import hmac
import json
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel

from app.config import get_settings
from app.services.event_bus import event_bus

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/webhooks", tags=["webhooks"])


class WebhookPayload(BaseModel):
    source: str  # github, trading, custom, etc.
    event: str  # push, alert, signal, etc.
    data: dict  # arbitrary payload


@router.post("/incoming")
async def receive_webhook(payload: WebhookPayload):
    """Generic incoming webhook — routes to CEO agent."""
    logger.info(f"Webhook received: {payload.source}/{payload.event}")

    # Broadcast event
    await event_bus.broadcast(
        f"webhook_{payload.source}",
        {
            "source": payload.source,
            "event": payload.event,
            "data": payload.data,
            "received_at": datetime.now(timezone.utc).isoformat(),
        },
    )

    # Route important webhooks to CEO
    ceo_message = _format_for_ceo(payload)
    if ceo_message:
        from app.agents.registry import registry

        ceo = registry.get("ceo")
        if ceo:
            import asyncio

            try:
                response = await asyncio.wait_for(ceo.run(ceo_message), timeout=120)
                return {
                    "status": "processed",
                    "ceo_response": response[:500],
                }
            except asyncio.TimeoutError:
                return {"status": "accepted", "note": "CEO processing timed out"}
            except Exception as e:
                logger.error(f"Webhook CEO processing error: {e}")
                return {"status": "accepted", "note": "CEO processing failed"}

    return {"status": "accepted"}


@router.post("/github")
async def github_webhook(request: Request, x_hub_signature_256: str | None = Header(None)):
    """GitHub webhook endpoint with optional signature verification."""
    body = await request.body()
    settings = get_settings()

    # Verify signature if secret is configured
    if settings.secret_key and settings.secret_key != "change-me-in-production":
        if x_hub_signature_256:
            expected = "sha256=" + hmac.new(
                settings.secret_key.encode(), body, hashlib.sha256
            ).hexdigest()
            if not hmac.compare_digest(expected, x_hub_signature_256):
                raise HTTPException(status_code=401, detail="Invalid signature")

    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    event_type = request.headers.get("X-GitHub-Event", "ping")
    logger.info(f"GitHub webhook: {event_type}")

    if event_type == "ping":
        return {"status": "pong"}

    # Format and send to CEO
    summary = _format_github_event(event_type, data)
    if summary:
        await event_bus.broadcast(
            "webhook_github",
            {"event": event_type, "summary": summary},
        )

        from app.agents.registry import registry
        import asyncio

        ceo = registry.get("ceo")
        if ceo:
            try:
                await asyncio.wait_for(
                    ceo.run(f"[GITHUB WEBHOOK] {summary}"), timeout=120
                )
            except Exception as e:
                logger.debug(f"GitHub webhook CEO notification failed: {e}")

    return {"status": "accepted", "event": event_type}


@router.post("/trading-alert")
async def trading_alert_webhook(payload: WebhookPayload):
    """Trading alert webhook — forwards to CEO for decision."""
    logger.info(f"Trading alert: {payload.event}")

    await event_bus.broadcast(
        "webhook_trading",
        {
            "event": payload.event,
            "data": payload.data,
            "received_at": datetime.now(timezone.utc).isoformat(),
        },
    )

    from app.agents.registry import registry
    import asyncio

    ceo = registry.get("ceo")
    if not ceo:
        return {"status": "accepted", "note": "CEO not available"}

    alert_text = (
        f"[TRADING ALERT] {payload.event}\n"
        f"Symbol: {payload.data.get('symbol', 'N/A')}\n"
        f"Signal: {payload.data.get('signal', 'N/A')}\n"
        f"Price: {payload.data.get('price', 'N/A')}\n"
        f"Details: {json.dumps(payload.data, indent=2)[:500]}"
    )

    try:
        response = await asyncio.wait_for(ceo.run(alert_text), timeout=120)
        return {"status": "processed", "ceo_response": response[:500]}
    except Exception:
        return {"status": "accepted", "note": "CEO processing in progress"}


def _format_for_ceo(payload: WebhookPayload) -> str | None:
    """Format a generic webhook payload into a CEO message."""
    return (
        f"[WEBHOOK: {payload.source}] Event: {payload.event}\n"
        f"Data: {json.dumps(payload.data, indent=2)[:1000]}"
    )


def _format_github_event(event_type: str, data: dict) -> str | None:
    """Format GitHub webhook event into human-readable summary."""
    repo = data.get("repository", {}).get("full_name", "unknown")

    if event_type == "push":
        commits = data.get("commits", [])
        branch = data.get("ref", "").split("/")[-1]
        return (
            f"Push to {repo}/{branch}: {len(commits)} commit(s)\n"
            + "\n".join(f"  - {c.get('message', '')[:80]}" for c in commits[:5])
        )
    if event_type == "pull_request":
        pr = data.get("pull_request", {})
        action = data.get("action", "")
        return f"PR #{pr.get('number', '?')} {action}: {pr.get('title', '')[:100]} in {repo}"

    if event_type == "issues":
        issue = data.get("issue", {})
        action = data.get("action", "")
        return f"Issue #{issue.get('number', '?')} {action}: {issue.get('title', '')[:100]} in {repo}"

    if event_type in ("workflow_run", "check_run"):
        name = data.get("workflow_run", data.get("check_run", {})).get("name", "?")
        conclusion = data.get("workflow_run", data.get("check_run", {})).get("conclusion", "?")
        return f"CI: {name} → {conclusion} in {repo}"

    return f"GitHub event '{event_type}' on {repo}"
