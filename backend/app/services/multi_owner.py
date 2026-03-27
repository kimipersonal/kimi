"""Multi-Owner — role-based owner management for the AI Holding system.

Supports multiple human owners with different permission roles:
  - admin: full access — approve/reject, configure, manage owners
  - approver: can approve/reject actions, view reports
  - viewer: read-only access to dashboards and reports

Owners persist in Redis and are checked by the approval system.
"""

import json
import logging
import time
from datetime import datetime, timezone
from enum import Enum

logger = logging.getLogger(__name__)

_REDIS_KEY = "multi_owner:owners"


class OwnerRole(str, Enum):
    ADMIN = "admin"
    APPROVER = "approver"
    VIEWER = "viewer"


# Permissions for each role
ROLE_PERMISSIONS: dict[str, list[str]] = {
    "admin": [
        "approve",
        "reject",
        "configure",
        "manage_owners",
        "view_reports",
        "rollback",
        "manage_thresholds",
    ],
    "approver": ["approve", "reject", "view_reports"],
    "viewer": ["view_reports"],
}


class MultiOwnerService:
    """Manages multiple owners with role-based permissions."""

    def __init__(self) -> None:
        self._owners: dict[str, dict] = {}
        self._loaded = False

    async def load_from_redis(self) -> None:
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
                self._owners = json.loads(raw)
                logger.info(f"Loaded {len(self._owners)} owners from Redis")
        except Exception as e:
            logger.debug(f"Could not load owners: {e}")

    async def _persist(self) -> None:
        try:
            import redis.asyncio as aioredis
            from app.config import get_settings

            r = aioredis.from_url(get_settings().redis_url, decode_responses=True)
            await r.set(_REDIS_KEY, json.dumps(self._owners), ex=86400 * 90)
            await r.aclose()
        except Exception as e:
            logger.debug(f"Could not persist owners: {e}")

    async def add_owner(
        self, owner_id: str, name: str, role: str = "viewer", contact: str = ""
    ) -> dict:
        """Add or update an owner."""
        if role not in ROLE_PERMISSIONS:
            return {"error": f"Invalid role '{role}'. Must be: admin, approver, viewer"}

        is_new = owner_id not in self._owners
        self._owners[owner_id] = {
            "id": owner_id,
            "name": name,
            "role": role,
            "contact": contact,
            "permissions": ROLE_PERMISSIONS[role],
            "added_at": (
                self._owners[owner_id]["added_at"]
                if not is_new
                else datetime.now(timezone.utc).isoformat()
            ),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        await self._persist()
        return {
            "owner": self._owners[owner_id],
            "action": "added" if is_new else "updated",
        }

    async def remove_owner(self, owner_id: str) -> dict:
        """Remove an owner. Cannot remove the last admin."""
        if owner_id not in self._owners:
            return {"error": f"Owner '{owner_id}' not found"}

        # Prevent removing the last admin
        owner = self._owners[owner_id]
        if owner["role"] == "admin":
            admin_count = sum(
                1 for o in self._owners.values() if o["role"] == "admin"
            )
            if admin_count <= 1:
                return {"error": "Cannot remove the last admin owner"}

        removed = self._owners.pop(owner_id)
        await self._persist()
        return {"removed": removed}

    def list_owners(self, role: str | None = None) -> list[dict]:
        """List all owners, optionally filtered by role."""
        owners = list(self._owners.values())
        if role:
            owners = [o for o in owners if o["role"] == role]
        return owners

    def get_owner(self, owner_id: str) -> dict | None:
        """Get a specific owner."""
        return self._owners.get(owner_id)

    def has_permission(self, owner_id: str, permission: str) -> bool:
        """Check if an owner has a specific permission."""
        owner = self._owners.get(owner_id)
        if not owner:
            return False
        return permission in owner.get("permissions", [])

    def get_approvers(self) -> list[dict]:
        """Get all owners who can approve actions (admins + approvers)."""
        return [
            o
            for o in self._owners.values()
            if o["role"] in ("admin", "approver")
        ]

    def get_permissions(self, owner_id: str) -> dict:
        """Get permissions for an owner."""
        owner = self._owners.get(owner_id)
        if not owner:
            return {"error": f"Owner '{owner_id}' not found"}
        return {
            "owner_id": owner_id,
            "name": owner["name"],
            "role": owner["role"],
            "permissions": owner["permissions"],
        }

    def get_stats(self) -> dict:
        """Get owner statistics."""
        role_counts: dict[str, int] = {}
        for o in self._owners.values():
            role = o["role"]
            role_counts[role] = role_counts.get(role, 0) + 1
        return {
            "total_owners": len(self._owners),
            "by_role": role_counts,
            "total_approvers": len(self.get_approvers()),
        }


# Global singleton
multi_owner_service = MultiOwnerService()
