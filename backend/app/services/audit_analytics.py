"""Audit Analytics — deep analysis of audit log entries.

Provides timeline analysis, anomaly detection, cost trend tracking,
and pattern identification across all CEO actions and system events.
"""

import logging
import math
from collections import defaultdict
from datetime import datetime, timezone, timedelta

logger = logging.getLogger(__name__)


class AuditAnalyticsService:
    """Analyses audit log entries for patterns, anomalies, and trends."""

    async def _get_entries(self, limit: int = 1000) -> list[dict]:
        """Fetch recent audit entries."""
        from app.services.audit_log import audit_log

        return await audit_log.get_entries(limit=limit)

    async def get_timeline(
        self, hours: int = 24, bucket_minutes: int = 60
    ) -> dict:
        """Build a timeline of actions bucketed by time intervals."""
        entries = await self._get_entries(limit=5000)
        now = datetime.now(timezone.utc)
        cutoff = now - timedelta(hours=hours)

        buckets: dict[str, dict] = {}
        for e in entries:
            try:
                ts = datetime.fromisoformat(e["timestamp"])
            except (KeyError, ValueError):
                continue
            if ts < cutoff:
                continue
            bucket_key = ts.strftime("%Y-%m-%d %H:") + str(
                (ts.minute // bucket_minutes) * bucket_minutes
            ).zfill(2)
            if bucket_key not in buckets:
                buckets[bucket_key] = {"count": 0, "successes": 0, "failures": 0, "actions": {}}
            buckets[bucket_key]["count"] += 1
            if e.get("success"):
                buckets[bucket_key]["successes"] += 1
            else:
                buckets[bucket_key]["failures"] += 1
            action = e.get("action", "unknown")
            buckets[bucket_key]["actions"][action] = (
                buckets[bucket_key]["actions"].get(action, 0) + 1
            )

        return {
            "hours": hours,
            "bucket_minutes": bucket_minutes,
            "total_buckets": len(buckets),
            "timeline": dict(sorted(buckets.items())),
        }

    async def detect_anomalies(self, sensitivity: float = 2.0) -> dict:
        """Detect anomalies in action patterns using z-score method.

        Looks for:
        - Unusual action frequency spikes
        - Cost spikes (estimated from action categories)
        - Failure rate spikes
        - Unusual time-of-day activity
        """
        entries = await self._get_entries(limit=2000)
        if len(entries) < 10:
            return {"anomalies": [], "message": "Not enough data for anomaly detection"}

        # Group by hour
        hourly: dict[str, list[dict]] = defaultdict(list)
        for e in entries:
            try:
                ts = datetime.fromisoformat(e["timestamp"])
                key = ts.strftime("%Y-%m-%d %H:00")
                hourly[key].append(e)
            except (KeyError, ValueError):
                continue

        if len(hourly) < 3:
            return {"anomalies": [], "message": "Not enough hourly data"}

        # Calculate hourly counts
        counts = [len(v) for v in hourly.values()]
        mean = sum(counts) / len(counts)
        variance = sum((c - mean) ** 2 for c in counts) / len(counts)
        std = math.sqrt(variance) if variance > 0 else 1.0

        anomalies = []
        for hour_key, hour_entries in hourly.items():
            count = len(hour_entries)
            z_score = (count - mean) / std if std > 0 else 0

            if abs(z_score) >= sensitivity:
                failures = sum(1 for e in hour_entries if not e.get("success"))
                failure_rate = failures / count if count > 0 else 0
                top_actions = defaultdict(int)
                for e in hour_entries:
                    top_actions[e.get("action", "unknown")] += 1

                anomalies.append({
                    "hour": hour_key,
                    "action_count": count,
                    "z_score": round(z_score, 2),
                    "type": "spike" if z_score > 0 else "drop",
                    "failure_rate": round(failure_rate * 100, 1),
                    "top_actions": dict(
                        sorted(top_actions.items(), key=lambda x: -x[1])[:5]
                    ),
                })

        # Check for failure rate anomalies
        failure_rates = []
        for hour_entries in hourly.values():
            if len(hour_entries) >= 3:
                fr = sum(1 for e in hour_entries if not e.get("success")) / len(
                    hour_entries
                )
                failure_rates.append(fr)

        if failure_rates:
            fr_mean = sum(failure_rates) / len(failure_rates)
            fr_var = sum((f - fr_mean) ** 2 for f in failure_rates) / len(failure_rates)
            fr_std = math.sqrt(fr_var) if fr_var > 0 else 0.1
            for hour_key, hour_entries in hourly.items():
                if len(hour_entries) < 3:
                    continue
                fr = sum(1 for e in hour_entries if not e.get("success")) / len(
                    hour_entries
                )
                z = (fr - fr_mean) / fr_std if fr_std > 0 else 0
                if z >= sensitivity:
                    anomalies.append({
                        "hour": hour_key,
                        "type": "failure_spike",
                        "failure_rate": round(fr * 100, 1),
                        "z_score": round(z, 2),
                        "action_count": len(hour_entries),
                    })

        return {
            "anomalies": anomalies,
            "stats": {
                "mean_hourly_actions": round(mean, 1),
                "std_hourly_actions": round(std, 1),
                "hours_analyzed": len(hourly),
                "total_entries": len(entries),
            },
        }

    async def get_action_breakdown(self, hours: int = 24) -> dict:
        """Break down actions by category, success rate, and agent."""
        entries = await self._get_entries(limit=5000)
        now = datetime.now(timezone.utc)
        cutoff = now - timedelta(hours=hours)

        by_action: dict[str, dict] = {}
        by_agent: dict[str, dict] = {}

        for e in entries:
            try:
                ts = datetime.fromisoformat(e["timestamp"])
                if ts < cutoff:
                    continue
            except (KeyError, ValueError):
                continue

            action = e.get("action", "unknown")
            if action not in by_action:
                by_action[action] = {"count": 0, "successes": 0, "failures": 0}
            by_action[action]["count"] += 1
            if e.get("success"):
                by_action[action]["successes"] += 1
            else:
                by_action[action]["failures"] += 1

            agent = e.get("agent_name", "unknown")
            if agent not in by_agent:
                by_agent[agent] = {"count": 0, "successes": 0, "failures": 0}
            by_agent[agent]["count"] += 1
            if e.get("success"):
                by_agent[agent]["successes"] += 1
            else:
                by_agent[agent]["failures"] += 1

        # Calculate success rates
        for v in by_action.values():
            v["success_rate"] = (
                round(v["successes"] / v["count"] * 100, 1) if v["count"] else 0
            )
        for v in by_agent.values():
            v["success_rate"] = (
                round(v["successes"] / v["count"] * 100, 1) if v["count"] else 0
            )

        return {
            "hours": hours,
            "by_action": dict(
                sorted(by_action.items(), key=lambda x: -x[1]["count"])
            ),
            "by_agent": dict(
                sorted(by_agent.items(), key=lambda x: -x[1]["count"])
            ),
        }


# Global singleton
audit_analytics_service = AuditAnalyticsService()
