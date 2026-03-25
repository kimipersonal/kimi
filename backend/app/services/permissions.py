"""Multi-Owner Permissions — role-based access control for Telegram users.

Roles:
- admin: Full access (same as owner) — can approve, reject, manage agents, backup
- manager: Can chat with CEO, view status, but cannot approve/reject or backup
- viewer: Read-only — can view status, agents, costs, but cannot interact with CEO

The owner (telegram_owner_chat_id) always has admin role.
Additional users in telegram_admin_chat_ids default to admin.
Custom roles are stored in Redis.
"""

import json
import logging
from enum import Enum

logger = logging.getLogger(__name__)

_REDIS_KEY = "permissions:roles"


class OwnerRole(str, Enum):
    ADMIN = "admin"
    MANAGER = "manager"
    VIEWER = "viewer"


# Commands each role can access
ROLE_COMMANDS: dict[OwnerRole, set[str]] = {
    OwnerRole.ADMIN: {
        "start", "help", "menu", "status", "agents", "companies",
        "approvals", "approve", "reject", "stop_agent", "start_agent",
        "ask", "report", "notify", "web", "costs", "health", "models",
        "model", "audit", "perf", "backup",
    },
    OwnerRole.MANAGER: {
        "start", "help", "menu", "status", "agents", "companies",
        "approvals", "ask", "report", "web", "costs", "health", "models",
        "audit", "perf",
    },
    OwnerRole.VIEWER: {
        "start", "help", "menu", "status", "agents", "companies",
        "costs", "health", "models", "perf",
    },
}

# Whether the role can send free-text messages to CEO
ROLE_CAN_CHAT: dict[OwnerRole, bool] = {
    OwnerRole.ADMIN: True,
    OwnerRole.MANAGER: True,
    OwnerRole.VIEWER: False,
}


class PermissionManager:
    """Manage user roles with Redis persistence."""

    def __init__(self) -> None:
        self._roles: dict[str, str] = {}  # chat_id → role
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
                self._roles = json.loads(raw)
        except Exception as e:
            logger.debug(f"Could not load permissions: {e}")

    async def _persist(self) -> None:
        try:
            import redis.asyncio as aioredis
            from app.config import get_settings
            r = aioredis.from_url(get_settings().redis_url, decode_responses=True)
            await r.set(_REDIS_KEY, json.dumps(self._roles))
            await r.aclose()
        except Exception as e:
            logger.debug(f"Could not persist permissions: {e}")

    async def get_role(self, chat_id: str) -> OwnerRole:
        """Get the role for a chat ID."""
        await self._ensure_loaded()
        from app.config import get_settings
        settings = get_settings()

        # Owner always admin
        if chat_id == settings.telegram_owner_chat_id:
            return OwnerRole.ADMIN

        # Check custom role
        role_str = self._roles.get(chat_id)
        if role_str:
            try:
                return OwnerRole(role_str)
            except ValueError:
                pass

        # Admin chat IDs default to admin
        if settings.telegram_admin_chat_ids:
            admin_ids = set(settings.telegram_admin_chat_ids.split(","))
            if chat_id in admin_ids:
                return OwnerRole.ADMIN

        return OwnerRole.VIEWER

    async def set_role(self, chat_id: str, role: OwnerRole) -> None:
        """Set a custom role for a chat ID."""
        await self._ensure_loaded()
        self._roles[chat_id] = role.value
        await self._persist()

    async def can_use_command(self, chat_id: str, command: str) -> bool:
        """Check if a user can use a specific command."""
        role = await self.get_role(chat_id)
        return command in ROLE_COMMANDS.get(role, set())

    async def can_chat(self, chat_id: str) -> bool:
        """Check if a user can send free-text messages to CEO."""
        role = await self.get_role(chat_id)
        return ROLE_CAN_CHAT.get(role, False)

    async def list_roles(self) -> dict[str, str]:
        """List all custom role assignments."""
        await self._ensure_loaded()
        return dict(self._roles)


# Global singleton
permission_manager = PermissionManager()
