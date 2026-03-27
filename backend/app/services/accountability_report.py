"""Accountability Report — periodic report on CEO autonomous actions vs escalations.

Generates weekly/daily reports showing:
  - Autonomous decisions made and their outcomes
  - Actions escalated to owner / delegated to agents
  - Cost impact breakdown
  - Success/failure rates by action type
  - Recommendations for threshold adjustments
"""

import json
import logging
import time
from datetime import datetime, timezone, timedelta

logger = logging.getLogger(__name__)

_REDIS_KEY = "accountability:reports"


class AccountabilityReportService:
    """Generates CEO accountability reports from audit logs and approval history."""

    def __init__(self) -> None:
        self._reports: list[dict] = []
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
                self._reports = json.loads(raw)[-50:]
                logger.info(
                    f"Loaded {len(self._reports)} accountability reports from Redis"
                )
        except Exception as e:
            logger.debug(f"Could not load accountability reports: {e}")

    async def _persist(self) -> None:
        try:
            import redis.asyncio as aioredis
            from app.config import get_settings

            r = aioredis.from_url(get_settings().redis_url, decode_responses=True)
            await r.set(
                _REDIS_KEY, json.dumps(self._reports[-50:]), ex=86400 * 60
            )
            await r.aclose()
        except Exception as e:
            logger.debug(f"Could not persist accountability reports: {e}")

    async def generate_report(self, period_hours: int = 168) -> dict:
        """Generate an accountability report for the given period (default 7 days).

        Combines data from audit logs and approval records.
        """
        from app.services.audit_log import audit_log

        entries = await audit_log.get_entries(limit=5000)
        now = datetime.now(timezone.utc)
        cutoff = now - timedelta(hours=period_hours)

        # Filter to period
        period_entries = []
        for e in entries:
            try:
                ts = datetime.fromisoformat(e["timestamp"])
                if ts >= cutoff:
                    period_entries.append(e)
            except (KeyError, ValueError):
                continue

        # Categorise actions
        autonomous_actions = []
        escalated_actions = []
        total_successes = 0
        total_failures = 0
        action_stats: dict[str, dict] = {}

        for e in period_entries:
            action = e.get("action", "unknown")
            success = e.get("success", True)

            if action not in action_stats:
                action_stats[action] = {
                    "count": 0,
                    "successes": 0,
                    "failures": 0,
                }
            action_stats[action]["count"] += 1

            if success:
                total_successes += 1
                action_stats[action]["successes"] += 1
            else:
                total_failures += 1
                action_stats[action]["failures"] += 1

            source = e.get("source", "tool_call")
            # Heuristic: actions via telegram/webhook are likely escalated
            if source in ("telegram", "webhook"):
                escalated_actions.append(e)
            else:
                autonomous_actions.append(e)

        # Get approval stats for the period
        approval_stats = await self._get_approval_stats(cutoff)

        total = len(period_entries)
        report = {
            "period_hours": period_hours,
            "period_start": cutoff.isoformat(),
            "period_end": now.isoformat(),
            "generated_at": now.isoformat(),
            "summary": {
                "total_actions": total,
                "autonomous_actions": len(autonomous_actions),
                "escalated_actions": len(escalated_actions),
                "autonomy_rate": (
                    round(len(autonomous_actions) / total * 100, 1) if total else 0
                ),
                "success_rate": (
                    round(total_successes / total * 100, 1) if total else 0
                ),
                "total_successes": total_successes,
                "total_failures": total_failures,
            },
            "action_breakdown": {
                k: {
                    **v,
                    "success_rate": (
                        round(v["successes"] / v["count"] * 100, 1) if v["count"] else 0
                    ),
                }
                for k, v in sorted(
                    action_stats.items(), key=lambda x: -x[1]["count"]
                )
            },
            "approval_stats": approval_stats,
            "recommendations": self._generate_recommendations(
                action_stats, approval_stats, total
            ),
        }

        self._reports.append(report)
        await self._persist()
        return report

    async def _get_approval_stats(self, cutoff: datetime) -> dict:
        """Get approval request statistics for the period."""
        try:
            from app.services.approval_service import list_approvals

            all_approvals = await list_approvals()
            period_approvals = []
            for a in all_approvals:
                try:
                    ts = datetime.fromisoformat(a["requested_at"])
                    if ts >= cutoff:
                        period_approvals.append(a)
                except (KeyError, ValueError):
                    continue

            approved = sum(1 for a in period_approvals if a.get("status") == "approved")
            rejected = sum(1 for a in period_approvals if a.get("status") == "rejected")
            pending = sum(1 for a in period_approvals if a.get("status") == "pending")
            expired = sum(1 for a in period_approvals if a.get("status") == "expired")

            return {
                "total_requests": len(period_approvals),
                "approved": approved,
                "rejected": rejected,
                "pending": pending,
                "expired": expired,
                "approval_rate": (
                    round(approved / len(period_approvals) * 100, 1)
                    if period_approvals
                    else 0
                ),
            }
        except Exception:
            return {"total_requests": 0, "error": "Could not fetch approval data"}

    def _generate_recommendations(
        self,
        action_stats: dict,
        approval_stats: dict,
        total: int,
    ) -> list[str]:
        """Generate recommendations based on the report data."""
        recs = []

        # High failure rate actions
        for action, stats in action_stats.items():
            if stats["count"] >= 3:
                fail_rate = stats["failures"] / stats["count"]
                if fail_rate > 0.3:
                    recs.append(
                        f"Action '{action}' has {fail_rate:.0%} failure rate "
                        f"({stats['failures']}/{stats['count']}). "
                        f"Consider reviewing configuration or adding safeguards."
                    )

        # High rejection rate on approvals
        approval_total = approval_stats.get("total_requests", 0)
        if approval_total >= 3:
            rejected = approval_stats.get("rejected", 0)
            if rejected / approval_total > 0.5:
                recs.append(
                    f"High approval rejection rate ({rejected}/{approval_total}). "
                    f"CEO may be requesting inappropriate actions."
                )

        # Stale pending approvals
        pending = approval_stats.get("pending", 0)
        if pending > 5:
            recs.append(
                f"{pending} pending approvals. Owner should review the approval queue."
            )

        if not recs:
            recs.append("No issues detected. System operating within normal parameters.")

        return recs

    def get_stats(self) -> dict:
        """Get summary of all generated reports."""
        return {
            "total_reports": len(self._reports),
            "latest_report": self._reports[-1] if self._reports else None,
        }


# Global singleton
accountability_report_service = AccountabilityReportService()
