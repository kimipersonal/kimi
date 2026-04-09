"""Backup & Restore — export/import system state as JSON."""

import logging
from datetime import datetime, timezone

from app.db.database import redis_pool
logger = logging.getLogger(__name__)

# Redis keys to include in backup
_REDIS_KEYS = [
    "ceo:conversation_history",
    "cost_tracker:records",
    "cost_tracker:lifetime_cost",
    "cost_tracker:lifetime_calls",
    "cost_tracker:lifetime_tokens",
    "scheduler:tasks",
    "audit_log:entries",
    "agent_templates",
    "performance:agents",
]


async def create_backup() -> dict:
    """Create a full system backup as a JSON-serializable dict."""
    from app.config import get_settings
    from app.services.company_manager import list_companies
    from app.agents.registry import registry

    settings = get_settings()
    backup: dict = {
        "version": "1.0",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "system": {
            "owner_name": settings.owner_name,
            "owner_language": settings.owner_language,
            "daily_budget_usd": settings.daily_budget_usd,
        },
    }

    # Companies from DB
    try:
        backup["companies"] = await list_companies()
    except Exception as e:
        logger.warning(f"Backup: could not export companies: {e}")
        backup["companies"] = []

    # Agents from registry
    agents_data = []
    for agent in registry.get_all():
        agents_data.append({
            "agent_id": agent.agent_id,
            "name": agent.name,
            "role": agent.role,
            "status": agent.status.value,
            "model_tier": agent.model_tier,
        })
    backup["agents"] = agents_data

    # Redis state
    redis_state: dict = {}
    try:
        for key in _REDIS_KEYS:
            val = await redis_pool.get(key)
            if val is not None:
                redis_state[key] = val
    except Exception as e:
        logger.warning(f"Backup: could not export Redis state: {e}")
    backup["redis_state"] = redis_state

    return backup


async def restore_backup(data: dict) -> dict:
    """Restore system state from a backup dict.

    Only restores Redis state — DB/agent recreation would require restart.
    """

    if "version" not in data:
        return {"success": False, "error": "Invalid backup format"}

    restored_keys: list[str] = []
    try:
        redis_state = data.get("redis_state", {})
        for key, val in redis_state.items():
            if key in _REDIS_KEYS:  # only restore known keys
                await redis_pool.set(key, val)
                restored_keys.append(key)
    except Exception as e:
        return {"success": False, "error": str(e)}

    return {
        "success": True,
        "restored_keys": restored_keys,
        "message": f"Restored {len(restored_keys)} Redis keys. Restart services to fully apply.",
    }
