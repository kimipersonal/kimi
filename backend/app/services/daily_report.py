"""Daily Intelligence Report — auto-generated CEO report covering all operations.

Collects data from cost tracker, performance tracker, trading service, health monitor,
audit log, and agent registry to produce a comprehensive daily report.
Sends via Telegram and event bus.
"""

import json
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


async def generate_daily_report() -> dict:
    """Generate a comprehensive daily intelligence report.

    Returns a dict with all sections so the CEO can format/send it.
    """
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "sections": {},
    }

    # --- 1. Cost Summary ---
    try:
        from app.services.cost_tracker import cost_tracker

        overview = await cost_tracker.get_overview_with_lifetime()
        report["sections"]["costs"] = {
            "cost_today_usd": overview.get("cost_today_usd", 0),
            "daily_budget_usd": overview.get("daily_budget_usd", 0),
            "budget_used_pct": overview.get("budget_used_pct", 0),
            "calls_today": overview.get("calls_today", 0),
            "lifetime_cost_usd": overview.get("lifetime_cost_usd", 0),
            "lifetime_calls": overview.get("lifetime_calls", 0),
            "top_spenders": sorted(
                overview.get("agents", []),
                key=lambda a: a.get("cost_today_usd", 0),
                reverse=True,
            )[:5],
        }
    except Exception as e:
        report["sections"]["costs"] = {"error": str(e)}

    # --- 2. Agent Performance ---
    try:
        from app.services.performance_tracker import performance_tracker

        scores = await performance_tracker.get_all_scores()
        report["sections"]["performance"] = {
            "agents": scores,
            "top_performers": [s for s in scores if s.get("grade") in ("A", "B")][:5],
            "underperformers": [s for s in scores if s.get("grade") in ("D", "F")],
            "total_tracked": len(scores),
        }
    except Exception as e:
        report["sections"]["performance"] = {"error": str(e)}

    # --- 3. Agent/Company Status ---
    try:
        from app.services.company_manager import list_companies
        from app.agents.registry import registry

        companies = await list_companies()
        total_agents = registry.count
        active_agents = sum(
            1 for a in registry._agents.values()
            if a.status.value not in ("stopped", "error", "paused")
        )
        error_agents = [
            {"id": aid, "name": a.name, "status": a.status.value}
            for aid, a in registry._agents.items()
            if a.status.value in ("error", "paused")
        ]
        report["sections"]["operations"] = {
            "total_companies": len(companies),
            "total_agents": total_agents,
            "active_agents": active_agents,
            "error_or_paused_agents": error_agents,
            "companies": [
                {
                    "name": c.get("name"),
                    "type": c.get("type"),
                    "agent_count": len(c.get("agents", [])),
                }
                for c in companies
            ],
        }
    except Exception as e:
        report["sections"]["operations"] = {"error": str(e)}

    # --- 4. Trading Summary ---
    try:
        from app.services.trading.trading_service import trading_service

        portfolio = await trading_service.get_portfolio_summary()
        report["sections"]["trading"] = portfolio
    except Exception as e:
        report["sections"]["trading"] = {"error": str(e)}

    # --- 5. Health Status ---
    try:
        from app.services.health_monitor import health_monitor

        health = await health_monitor.get_health()
        report["sections"]["health"] = {
            "overall": health.get("status", "unknown"),
            "db": health.get("database", {}).get("status", "unknown"),
            "redis": health.get("redis", {}).get("status", "unknown"),
            "uptime_seconds": health.get("uptime_seconds", 0),
        }
    except Exception as e:
        report["sections"]["health"] = {"error": str(e)}

    # --- 6. Budget Enforcement Status ---
    try:
        from app.services.budget_enforcer import budget_enforcer

        budgets = await budget_enforcer.get_budgets()
        report["sections"]["budgets"] = budgets
    except Exception as e:
        report["sections"]["budgets"] = {"error": str(e)}

    # --- 7. Audit Summary (last 24h) ---
    try:
        from app.services.audit_log import audit_log

        entries = await audit_log.get_entries(limit=100)
        total_actions = len(entries)
        failed_actions = sum(1 for e in entries if not e.get("success", True))
        action_types: dict[str, int] = {}
        for e in entries:
            action = e.get("action", "unknown")
            action_types[action] = action_types.get(action, 0) + 1
        report["sections"]["audit"] = {
            "total_actions_24h": total_actions,
            "failed_actions": failed_actions,
            "top_actions": sorted(
                action_types.items(), key=lambda x: x[1], reverse=True
            )[:10],
        }
    except Exception as e:
        report["sections"]["audit"] = {"error": str(e)}

    # --- 8. Scheduler Status ---
    try:
        from app.services.scheduler import scheduler

        tasks = scheduler.list_tasks()
        report["sections"]["scheduled_tasks"] = {
            "total": len(tasks),
            "tasks": tasks,
        }
    except Exception as e:
        report["sections"]["scheduled_tasks"] = {"error": str(e)}

    return report


def format_report_text(report: dict) -> str:
    """Format the report dict into a human-readable text string."""
    lines = []
    lines.append("📊 DAILY INTELLIGENCE REPORT")
    lines.append(f"Generated: {report.get('generated_at', 'N/A')}")
    lines.append("")

    sections = report.get("sections", {})

    # Costs
    costs = sections.get("costs", {})
    if "error" not in costs:
        lines.append("💰 COSTS")
        lines.append(f"  Today: ${costs.get('cost_today_usd', 0):.4f} / ${costs.get('daily_budget_usd', 0):.2f} ({costs.get('budget_used_pct', 0):.1f}%)")
        lines.append(f"  Calls today: {costs.get('calls_today', 0)}")
        lines.append(f"  Lifetime: ${costs.get('lifetime_cost_usd', 0):.4f} ({costs.get('lifetime_calls', 0)} calls)")
        top = costs.get("top_spenders", [])
        if top:
            lines.append("  Top spenders:")
            for s in top[:3]:
                lines.append(f"    - {s.get('agent_id', '?')}: ${s.get('cost_today_usd', 0):.4f}")
    else:
        lines.append(f"💰 COSTS: Error — {costs['error']}")
    lines.append("")

    # Performance
    perf = sections.get("performance", {})
    if "error" not in perf:
        lines.append("📈 AGENT PERFORMANCE")
        lines.append(f"  Tracked: {perf.get('total_tracked', 0)} agents")
        underperformers = perf.get("underperformers", [])
        if underperformers:
            lines.append("  ⚠️ Underperformers:")
            for u in underperformers:
                lines.append(f"    - {u.get('agent_id', '?')}: Grade {u.get('grade', '?')} (success: {u.get('success_rate', 0)}%)")
        else:
            lines.append("  All agents performing well ✅")
    lines.append("")

    # Operations
    ops = sections.get("operations", {})
    if "error" not in ops:
        lines.append("🏢 OPERATIONS")
        lines.append(f"  Companies: {ops.get('total_companies', 0)}")
        lines.append(f"  Agents: {ops.get('active_agents', 0)} active / {ops.get('total_agents', 0)} total")
        errors = ops.get("error_or_paused_agents", [])
        if errors:
            lines.append("  ⚠️ Issues:")
            for e in errors:
                lines.append(f"    - {e.get('name', '?')} ({e.get('id', '?')}): {e.get('status', '?')}")
    lines.append("")

    # Trading
    trading = sections.get("trading", {})
    if "error" not in trading and trading:
        lines.append("📉 TRADING")
        for k, v in trading.items():
            if isinstance(v, (int, float, str)):
                lines.append(f"  {k}: {v}")
    lines.append("")

    # Health
    health = sections.get("health", {})
    if "error" not in health:
        lines.append("🏥 SYSTEM HEALTH")
        lines.append(f"  Overall: {health.get('overall', '?')}")
        lines.append(f"  DB: {health.get('db', '?')} | Redis: {health.get('redis', '?')}")
        uptime_h = health.get("uptime_seconds", 0) / 3600
        lines.append(f"  Uptime: {uptime_h:.1f}h")
    lines.append("")

    # Budgets
    budgets = sections.get("budgets", {})
    if "error" not in budgets:
        agent_budgets = budgets.get("agent_budgets", {})
        company_budgets = budgets.get("company_budgets", {})
        paused = budgets.get("paused_agents_today", [])
        if agent_budgets or company_budgets:
            lines.append("💳 BUDGET ENFORCEMENT")
            lines.append(f"  Global: ${budgets.get('global_daily_budget_usd', 0):.2f}/day")
            if agent_budgets:
                lines.append(f"  Agent budgets: {len(agent_budgets)} configured")
            if company_budgets:
                lines.append(f"  Company budgets: {len(company_budgets)} configured")
            if paused:
                lines.append(f"  ⚠️ Paused today: {', '.join(paused)}")
            lines.append("")

    # Audit
    audit = sections.get("audit", {})
    if "error" not in audit:
        lines.append("📋 AUDIT")
        lines.append(f"  Actions (24h): {audit.get('total_actions_24h', 0)}")
        lines.append(f"  Failed: {audit.get('failed_actions', 0)}")
    lines.append("")

    # Schedules
    sched = sections.get("scheduled_tasks", {})
    if "error" not in sched:
        lines.append(f"⏰ SCHEDULED TASKS: {sched.get('total', 0)} active")

    return "\n".join(lines)


async def send_daily_report_telegram() -> None:
    """Generate the daily report and send it via Telegram to the owner."""
    try:
        report = await generate_daily_report()
        text = format_report_text(report)

        from app.services.telegram_bot import telegram_bot
        from app.config import get_settings

        settings = get_settings()
        if telegram_bot._running and telegram_bot._app and settings.telegram_owner_chat_id:
            from telegram.constants import ParseMode

            # Telegram has 4096 char limit — split if needed
            max_len = settings.max_telegram_msg_len
            if len(text) <= max_len:
                await telegram_bot._app.bot.send_message(
                    chat_id=int(settings.telegram_owner_chat_id),
                    text=text,
                )
            else:
                # Split into chunks
                chunks = [text[i : i + max_len] for i in range(0, len(text), max_len)]
                for chunk in chunks:
                    await telegram_bot._app.bot.send_message(
                        chat_id=int(settings.telegram_owner_chat_id),
                        text=chunk,
                    )
            logger.info("Daily intelligence report sent via Telegram")

        # Also broadcast via event bus for dashboard
        from app.services.event_bus import event_bus

        await event_bus.broadcast(
            "daily_report",
            {"report": report, "text": text[:2000]},
            agent_id="ceo",
        )

    except Exception as e:
        logger.error(f"Failed to generate/send daily report: {e}")
