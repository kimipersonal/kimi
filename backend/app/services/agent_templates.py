"""Agent Templates — save and load reusable agent configurations.

Templates are stored in Redis and can be used by the CEO to quickly
hire agents with pre-defined roles, instructions, and model settings.
"""

import json
import logging
import time
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

_REDIS_KEY = "agent_templates"


class AgentTemplateStore:
    """Manage reusable agent templates in Redis."""

    def __init__(self) -> None:
        self._templates: dict[str, dict] = {}
        self._loaded = False

    async def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        try:
            import redis.asyncio as aioredis
            from app.config import get_settings
            r = aioredis.from_url(get_settings().redis_url, decode_responses=True)
            raw = await r.get(_REDIS_KEY)
            await r.aclose()
            if raw:
                self._templates = json.loads(raw)
                logger.info(f"Loaded {len(self._templates)} agent templates from Redis")
        except Exception as e:
            logger.debug(f"Could not load agent templates: {e}")

    async def _persist(self) -> None:
        try:
            import redis.asyncio as aioredis
            from app.config import get_settings
            r = aioredis.from_url(get_settings().redis_url, decode_responses=True)
            await r.set(_REDIS_KEY, json.dumps(self._templates))
            await r.aclose()
        except Exception as e:
            logger.debug(f"Could not persist agent templates: {e}")

    async def save_template(
        self,
        name: str,
        role: str,
        instructions: str,
        model_tier: str = "smart",
        model_id: str | None = None,
        skills: list[str] | None = None,
        sandbox_enabled: bool = False,
        browser_enabled: bool = False,
        network_enabled: bool = False,
    ) -> dict:
        """Save a new agent template."""
        await self._ensure_loaded()
        template_id = name.lower().replace(" ", "_")
        template = {
            "id": template_id,
            "name": name,
            "role": role,
            "instructions": instructions,
            "model_tier": model_tier,
            "model_id": model_id,
            "skills": skills or [],
            "sandbox_enabled": sandbox_enabled,
            "browser_enabled": browser_enabled,
            "network_enabled": network_enabled,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        self._templates[template_id] = template
        await self._persist()
        return template

    async def get_template(self, template_id: str) -> dict | None:
        """Get a specific template by ID."""
        await self._ensure_loaded()
        return self._templates.get(template_id)

    async def list_templates(self) -> list[dict]:
        """List all saved templates."""
        await self._ensure_loaded()
        return list(self._templates.values())

    async def delete_template(self, template_id: str) -> bool:
        """Delete a template by ID."""
        await self._ensure_loaded()
        if template_id in self._templates:
            del self._templates[template_id]
            await self._persist()
            return True
        return False


# Global singleton
template_store = AgentTemplateStore()
