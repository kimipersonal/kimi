"""Error Pattern Detector — Tracks error patterns across agents and suggests fixes.

Analyzes activity logs, audit entries, and task failures to detect recurring
error patterns. Groups errors by type, time, agent, and tool to identify
systemic issues and suggest root-cause fixes.
"""

import json
import logging
import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

_REDIS_KEY = "error_detector:patterns"


@dataclass
class ErrorPattern:
    """A detected recurring error pattern."""
    pattern_id: str
    error_type: str  # timeout, api_error, permission, parse_error, tool_failure, etc.
    message_template: str  # normalized error message
    count: int
    agent_ids: list[str]
    tool_names: list[str]
    first_seen: str
    last_seen: str
    suggested_fix: str
    severity: str  # low, medium, high, critical


class ErrorPatternDetector:
    """Detects recurring error patterns and provides root-cause analysis.

    Periodically scans audit logs, activity logs and tool analytics for errors,
    clusters them into patterns, and generates fix suggestions.
    """

    def __init__(self) -> None:
        self._patterns: dict[str, dict] = {}  # pattern_id → pattern dict
        self._error_buffer: list[dict] = []  # recent errors for pattern detection
        self._max_buffer = 1000

    def record_error(
        self,
        error_message: str,
        agent_id: str = "unknown",
        tool_name: str = "",
        context: dict | None = None,
    ) -> None:
        """Record an error for pattern analysis."""
        error_type = self._classify_error(error_message)
        normalized = self._normalize_message(error_message)
        now = datetime.now(timezone.utc).isoformat()

        entry = {
            "error_type": error_type,
            "normalized": normalized,
            "raw_message": error_message[:500],
            "agent_id": agent_id,
            "tool_name": tool_name,
            "timestamp": now,
            "context": context or {},
        }

        if len(self._error_buffer) >= self._max_buffer:
            self._error_buffer = self._error_buffer[-(self._max_buffer // 2):]
        self._error_buffer.append(entry)

        # Update patterns incrementally
        self._update_pattern(entry)

    def _classify_error(self, message: str) -> str:
        """Classify error into a high-level type."""
        msg = message.lower()
        if any(w in msg for w in ["timeout", "timed out", "deadline exceeded"]):
            return "timeout"
        if any(w in msg for w in ["rate limit", "429", "too many requests"]):
            return "rate_limit"
        if any(w in msg for w in ["permission", "forbidden", "403", "unauthorized", "401"]):
            return "permission"
        if any(w in msg for w in ["not found", "404", "does not exist"]):
            return "not_found"
        if any(w in msg for w in ["connection", "unreachable", "dns", "network"]):
            return "connection"
        if any(w in msg for w in ["parse", "json", "decode", "invalid format", "syntax"]):
            return "parse_error"
        if any(w in msg for w in ["memory", "oom", "out of memory"]):
            return "resource"
        if any(w in msg for w in ["api", "external", "service unavailable", "503", "502"]):
            return "api_error"
        return "unknown"

    def _normalize_message(self, message: str) -> str:
        """Normalize error message by removing variable parts (IDs, timestamps, etc.)."""
        norm = message[:200]
        # Replace UUIDs
        norm = re.sub(r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}', '<ID>', norm)
        # Replace numbers
        norm = re.sub(r'\b\d{3,}\b', '<NUM>', norm)
        # Replace quoted strings
        norm = re.sub(r'"[^"]{20,}"', '"<STR>"', norm)
        # Replace IP addresses
        norm = re.sub(r'\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}', '<IP>', norm)
        return norm.strip()

    def _get_pattern_id(self, error_type: str, normalized: str) -> str:
        """Generate a stable pattern ID from error type + normalized message."""
        import hashlib
        key = f"{error_type}:{normalized}"
        return f"pat-{hashlib.md5(key.encode()).hexdigest()[:12]}"

    def _update_pattern(self, entry: dict) -> None:
        """Update or create a pattern from an error entry."""
        pattern_id = self._get_pattern_id(entry["error_type"], entry["normalized"])
        now = entry["timestamp"]

        if pattern_id in self._patterns:
            pat = self._patterns[pattern_id]
            pat["count"] += 1
            pat["last_seen"] = now
            if entry["agent_id"] not in pat["agent_ids"]:
                pat["agent_ids"].append(entry["agent_id"])
            if entry["tool_name"] and entry["tool_name"] not in pat["tool_names"]:
                pat["tool_names"].append(entry["tool_name"])
            # Re-evaluate severity based on count
            pat["severity"] = self._assess_severity(pat["count"], pat["error_type"])
            pat["suggested_fix"] = self._suggest_fix(pat)
        else:
            self._patterns[pattern_id] = {
                "pattern_id": pattern_id,
                "error_type": entry["error_type"],
                "message_template": entry["normalized"],
                "sample_message": entry["raw_message"],
                "count": 1,
                "agent_ids": [entry["agent_id"]],
                "tool_names": [entry["tool_name"]] if entry["tool_name"] else [],
                "first_seen": now,
                "last_seen": now,
                "severity": "low",
                "suggested_fix": "",
            }
            pat = self._patterns[pattern_id]
            pat["suggested_fix"] = self._suggest_fix(pat)

    def _assess_severity(self, count: int, error_type: str) -> str:
        """Determine severity based on occurrence count and error type."""
        critical_types = {"resource", "permission"}
        high_types = {"timeout", "rate_limit", "connection"}

        if error_type in critical_types and count >= 3:
            return "critical"
        if error_type in high_types and count >= 5:
            return "high"
        if count >= 10:
            return "high"
        if count >= 5:
            return "medium"
        return "low"

    def _suggest_fix(self, pattern: dict) -> str:
        """Generate a fix suggestion based on the error pattern."""
        etype = pattern["error_type"]
        count = pattern["count"]
        agents = pattern["agent_ids"]
        tools = pattern["tool_names"]

        suggestions = {
            "timeout": (
                f"Timeout errors occurred {count} times. "
                "Consider: (1) Increase timeout limits, (2) Optimize slow tool execution, "
                "(3) Check if external APIs are responsive."
            ),
            "rate_limit": (
                f"Rate limiting hit {count} times. "
                "Consider: (1) Reduce request frequency, (2) Implement request batching, "
                "(3) Upgrade API plan or add caching."
            ),
            "permission": (
                f"Permission errors {count} times for agents: {', '.join(agents[:3])}. "
                "Check: (1) API credentials are valid, (2) Agent permissions are correctly configured."
            ),
            "not_found": (
                f"Not-found errors {count} times. "
                "Check: (1) Resource IDs are valid, (2) Database references are up-to-date."
            ),
            "connection": (
                f"Connection errors {count} times. "
                "Check: (1) Network connectivity, (2) External service status, "
                "(3) DNS resolution, (4) Firewall rules."
            ),
            "parse_error": (
                f"Parse errors {count} times. "
                "Check: (1) API response format changes, (2) Input validation, "
                "(3) JSON schema compatibility."
            ),
            "resource": (
                f"Resource errors {count} times. CRITICAL: "
                "Check: (1) Memory usage, (2) Increase container limits, (3) Optimize data processing."
            ),
            "api_error": (
                f"External API errors {count} times. "
                "Check: (1) API status page, (2) Request parameters, (3) Enable failover/retry."
            ),
        }

        base = suggestions.get(etype, f"Unknown error type '{etype}' occurred {count} times.")

        if len(agents) > 1:
            base += f" Affects multiple agents ({len(agents)}), indicating a systemic issue."
        if tools:
            base += f" Related tools: {', '.join(tools[:5])}."

        return base

    async def get_patterns(
        self,
        severity: str | None = None,
        error_type: str | None = None,
        limit: int = 20,
    ) -> list[dict]:
        """Get detected error patterns, optionally filtered."""
        patterns = list(self._patterns.values())

        if severity:
            patterns = [p for p in patterns if p["severity"] == severity]
        if error_type:
            patterns = [p for p in patterns if p["error_type"] == error_type]

        # Sort by count (most frequent first)
        patterns.sort(key=lambda x: x["count"], reverse=True)
        return patterns[:limit]

    async def get_summary(self) -> dict:
        """Get a summary of all error patterns."""
        patterns = list(self._patterns.values())
        if not patterns:
            return {
                "status": "clean",
                "message": "No error patterns detected.",
                "total_patterns": 0,
                "total_errors": 0,
            }

        total_errors = sum(p["count"] for p in patterns)
        by_severity = defaultdict(int)
        by_type = defaultdict(int)
        for p in patterns:
            by_severity[p["severity"]] += 1
            by_type[p["error_type"]] += p["count"]

        critical = [p for p in patterns if p["severity"] in ("critical", "high")]
        critical.sort(key=lambda x: x["count"], reverse=True)

        return {
            "status": "issues_detected" if critical else "minor_issues",
            "total_patterns": len(patterns),
            "total_errors": total_errors,
            "by_severity": dict(by_severity),
            "by_type": dict(by_type),
            "critical_patterns": critical[:5],
            "top_suggestion": critical[0]["suggested_fix"] if critical else "No critical issues.",
        }

    async def scan_audit_log(self) -> int:
        """Scan recent audit log entries for errors. Returns count of new errors found."""
        try:
            from app.services.audit_log import audit_log
            entries = await audit_log.get_entries(limit=200)
            new_count = 0
            for entry in entries:
                if not entry.get("success", True):
                    self.record_error(
                        error_message=entry.get("result_summary", "Unknown error"),
                        agent_id=entry.get("agent_id", "unknown"),
                        tool_name=entry.get("action", ""),
                    )
                    new_count += 1
            return new_count
        except Exception as e:
            logger.error(f"Failed to scan audit log: {e}")
            return 0

    async def save_to_redis(self) -> None:
        try:
            import redis.asyncio as aioredis
            from app.config import get_settings
            r = aioredis.from_url(get_settings().redis_url, decode_responses=True)
            data = {
                "patterns": self._patterns,
                "buffer_size": len(self._error_buffer),
            }
            await r.set(_REDIS_KEY, json.dumps(data), ex=86400 * 30)
            await r.aclose()
        except Exception as e:
            logger.debug(f"Could not save error patterns: {e}")

    async def load_from_redis(self) -> None:
        try:
            import redis.asyncio as aioredis
            from app.config import get_settings
            r = aioredis.from_url(get_settings().redis_url, decode_responses=True)
            raw = await r.get(_REDIS_KEY)
            await r.aclose()
            if raw:
                data = json.loads(raw)
                self._patterns = data.get("patterns", {})
                logger.info(f"Loaded error detector: {len(self._patterns)} patterns")
        except Exception as e:
            logger.debug(f"Could not load error patterns: {e}")


# Singleton
error_pattern_detector = ErrorPatternDetector()
