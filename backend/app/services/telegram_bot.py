"""Telegram Bot — Owner notifications, interactive commands, and daily reports."""

import html
import logging
from datetime import time as dt_time
from datetime import timezone

from telegram import Message, Update
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from app.config import get_settings

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Notification levels
# ---------------------------------------------------------------------------

LEVEL_CRITICAL = "critical"  # always notify
LEVEL_IMPORTANT = "important"  # notify if enabled (default on)
LEVEL_INFO = "info"  # dashboard only by default


# Maps event → notification level
EVENT_LEVELS: dict[str, str] = {
    "approval_request": LEVEL_CRITICAL,
    "approval_decided": LEVEL_IMPORTANT,
    "approvals_expired": LEVEL_IMPORTANT,
    "agent_error": LEVEL_CRITICAL,
    "agent_state_change": LEVEL_INFO,
    "company_created": LEVEL_IMPORTANT,
    "agent_hired": LEVEL_INFO,
    "agent_fired": LEVEL_IMPORTANT,
    "trade_signal": LEVEL_IMPORTANT,
    "task_completed": LEVEL_INFO,
    "report": LEVEL_IMPORTANT,
    "log": LEVEL_INFO,
}


def _authorised_message(update: Update) -> Message | None:
    """Return the update's Message if the sender is authorised, else None."""
    chat = update.effective_chat
    msg = update.message
    if chat is None or msg is None:
        return None
    settings = get_settings()
    allowed = {settings.telegram_owner_chat_id}
    if settings.telegram_admin_chat_ids:
        allowed |= set(settings.telegram_admin_chat_ids.split(","))
    if str(chat.id) not in allowed:
        return None
    return msg


# ---------------------------------------------------------------------------
# Bot singleton
# ---------------------------------------------------------------------------


class TelegramBot:
    """Wraps python-telegram-bot Application with AI Holding commands."""

    def __init__(self) -> None:
        self._app: Application | None = None
        self._running = False
        # Which levels are forwarded to Telegram
        self._notify_levels: set[str] = {LEVEL_CRITICAL, LEVEL_IMPORTANT}

    # --- lifecycle ---

    async def start(self) -> None:
        settings = get_settings()
        if not settings.telegram_bot_token:
            logger.warning("TELEGRAM_BOT_TOKEN not set — Telegram bot disabled")
            return

        self._app = Application.builder().token(settings.telegram_bot_token).build()

        # Register handlers
        self._register_handlers()

        # Schedule daily CEO report (09:00 UTC)
        if self._app.job_queue:
            self._app.job_queue.run_daily(
                self._daily_report_job,
                time=dt_time(hour=9, minute=0, tzinfo=timezone.utc),
                name="daily_ceo_report",
            )
            logger.info("Daily CEO report scheduled at 09:00 UTC")

        await self._app.initialize()
        await self._app.start()
        if self._app.updater:
            await self._app.updater.start_polling(drop_pending_updates=True)
        self._running = True
        logger.info("Telegram bot started (polling)")

    async def stop(self) -> None:
        if not self._app or not self._running:
            return
        self._running = False
        if self._app.updater:
            await self._app.updater.stop()
        await self._app.stop()
        await self._app.shutdown()
        logger.info("Telegram bot stopped")

    # --- notification dispatch ---

    async def notify(self, event: str, data: dict, agent_id: str | None = None) -> None:
        """Send a Telegram notification if the event level warrants it."""
        if not self._app or not self._running:
            return

        level = EVENT_LEVELS.get(event, LEVEL_INFO)
        if level not in self._notify_levels:
            return

        settings = get_settings()
        chat_id = settings.telegram_owner_chat_id
        if not chat_id:
            return

        text = self._format_notification(event, data, agent_id)
        try:
            await self._app.bot.send_message(
                chat_id=int(chat_id),
                text=text,
                parse_mode=ParseMode.HTML,
            )
        except Exception as e:
            logger.error(f"Telegram send failed: {e}")

    # --- formatting ---

    @staticmethod
    def _format_notification(event: str, data: dict, agent_id: str | None) -> str:
        """Build a human-readable HTML message for Telegram."""
        if event == "approval_request":
            return (
                "🔔 <b>Approval Request</b>\n"
                f"From: <code>{html.escape(str(data.get('agent_id', '?')))}</code>\n"
                f"Category: {html.escape(str(data.get('category', '?')))}\n"
                f"<i>{html.escape(str(data.get('description', '')))}</i>\n\n"
                f"ID: <code>{html.escape(str(data.get('id', '?')))}</code>\n"
                "Reply: /approve &lt;id&gt; or /reject &lt;id&gt; &lt;reason&gt;"
            )
        if event == "approval_decided":
            emoji = "✅" if data.get("status") == "approved" else "❌"
            return (
                f"{emoji} <b>Approval {html.escape(str(data.get('status', '?')))}</b>\n"
                f"{html.escape(str(data.get('description', '')))}\n"
                f"Reason: {html.escape(str(data.get('decision_reason', '-')))}"
            )
        if event == "report":
            return ("📊 <b>Report from {agent}</b>\n\n{content}").format(
                agent=html.escape(str(data.get("agent_id", "?"))),
                content=html.escape(str(data.get("content", "")))[:3500],
            )
        if event == "company_created":
            return (
                "🏢 <b>New Company Created</b>\n"
                f"Name: {html.escape(str(data.get('name', '?')))}\n"
                f"Type: {html.escape(str(data.get('type', '?')))}\n"
                f"Agents: {data.get('agents_spawned', 0)}"
            )
        if event == "agent_error":
            return (
                "🚨 <b>Agent Error</b>\n"
                f"Agent: {html.escape(str(agent_id or '?'))}\n"
                f"Error: {html.escape(str(data.get('error', '?')))}"
            )
        # Generic fallback
        return f"ℹ️ <b>{html.escape(event)}</b>\n{html.escape(str(data)[:500])}"

    # --- handler registration ---

    def _register_handlers(self) -> None:
        assert self._app is not None
        self._app.add_handler(CommandHandler("start", self._cmd_start))
        self._app.add_handler(CommandHandler("help", self._cmd_help))
        self._app.add_handler(CommandHandler("status", self._cmd_status))
        self._app.add_handler(CommandHandler("agents", self._cmd_agents))
        self._app.add_handler(CommandHandler("companies", self._cmd_companies))
        self._app.add_handler(CommandHandler("approve", self._cmd_approve))
        self._app.add_handler(CommandHandler("reject", self._cmd_reject))
        self._app.add_handler(CommandHandler("stop_agent", self._cmd_stop_agent))
        self._app.add_handler(CommandHandler("start_agent", self._cmd_start_agent))
        self._app.add_handler(CommandHandler("ask", self._cmd_ask))
        self._app.add_handler(CommandHandler("report", self._cmd_report))
        self._app.add_handler(CommandHandler("approvals", self._cmd_approvals))
        self._app.add_handler(CommandHandler("notify", self._cmd_notify))
        self._app.add_handler(CommandHandler("web", self._cmd_web))
        self._app.add_handler(CommandHandler("costs", self._cmd_costs))
        self._app.add_handler(CommandHandler("health", self._cmd_health))
        # Catch-all: treat plain text as a message to CEO
        self._app.add_handler(
            MessageHandler(filters.TEXT & ~filters.COMMAND, self._msg_to_ceo)
        )

    # --- commands ---

    async def _cmd_start(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        msg = _authorised_message(update)
        if not msg:
            return
        await msg.reply_text(
            "🏢 <b>AI Holding Bot</b>\n"
            "Type /help to see available commands.\n"
            "Or just send a message and I'll forward it to the CEO.",
            parse_mode=ParseMode.HTML,
        )

    async def _cmd_help(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        msg = _authorised_message(update)
        if not msg:
            return
        await msg.reply_text(
            "<b>Commands</b>\n"
            "/status — dashboard overview\n"
            "/agents — list all agents\n"
            "/companies — list companies\n"
            "/approvals — pending approvals\n"
            "/approve &lt;id&gt; — approve request\n"
            "/reject &lt;id&gt; &lt;reason&gt; — reject request\n"
            "/stop_agent &lt;name_or_id&gt; — stop an agent\n"
            "/start_agent &lt;name_or_id&gt; — start an agent\n"
            "/ask &lt;agent&gt; &lt;question&gt; — ask a specific agent\n"
            "/report — get CEO daily report now\n"
            "/notify &lt;critical|important|info&gt; — toggle notification level\n"
            "/web — get current dashboard URL\n"
            "/costs — token usage &amp; cost breakdown\n"
            "/health — system health status\n"
            "\nOr send any text to chat with the CEO.",
            parse_mode=ParseMode.HTML,
        )

    async def _cmd_status(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        msg = _authorised_message(update)
        if not msg:
            return

        from app.agents.registry import registry
        from app.services.approval_service import list_approvals
        from app.services.company_manager import list_companies

        companies = await list_companies()
        pending = await list_approvals(status="pending")

        text = (
            "📊 <b>AI Holding Status</b>\n\n"
            f"Companies: {len(companies)}\n"
            f"Agents: {registry.count}\n"
            f"Active: {registry.active_count}\n"
            f"Pending approvals: {len(pending)}\n"
        )
        if companies:
            text += "\n<b>Companies:</b>\n"
            for c in companies:
                agent_count = len(c.get("agents", []))
                text += f"  • {html.escape(c['name'])} ({c['type']}) — {agent_count} agents\n"

        await msg.reply_text(text, parse_mode=ParseMode.HTML)

    async def _cmd_agents(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        msg = _authorised_message(update)
        if not msg:
            return

        from app.agents.registry import registry

        agents = registry.get_all()
        if not agents:
            await msg.reply_text("No agents registered.")
            return

        lines = ["<b>🤖 Agents</b>\n"]
        status_emoji = {
            "idle": "🟢",
            "thinking": "🟡",
            "acting": "⚡",
            "paused": "⏸",
            "stopped": "🔴",
            "error": "🚨",
            "waiting_approval": "⏳",
        }
        for a in agents:
            emoji = status_emoji.get(a.status.value, "❓")
            lines.append(
                f"{emoji} <b>{html.escape(a.name)}</b> ({html.escape(a.role)})\n"
                f"    ID: <code>{a.agent_id}</code>  Model: {a.model_tier}"
            )
        await msg.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)

    async def _cmd_companies(
        self, update: Update, ctx: ContextTypes.DEFAULT_TYPE
    ) -> None:
        msg = _authorised_message(update)
        if not msg:
            return

        from app.services.company_manager import list_companies

        companies = await list_companies()
        if not companies:
            await msg.reply_text("No companies yet.")
            return

        lines = ["<b>🏢 Companies</b>\n"]
        for c in companies:
            agents_list = c.get("agents", [])
            lines.append(
                f"<b>{html.escape(c['name'])}</b> ({c['type']})\n"
                f"  Status: {c['status']} | Agents: {len(agents_list)}"
            )
            for a in agents_list:
                lines.append(f"    • {html.escape(a['name'])} — {a['role']}")
        await msg.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)

    async def _cmd_approvals(
        self, update: Update, ctx: ContextTypes.DEFAULT_TYPE
    ) -> None:
        msg = _authorised_message(update)
        if not msg:
            return

        from app.services.approval_service import list_approvals

        pending = await list_approvals(status="pending")
        if not pending:
            await msg.reply_text("✅ No pending approvals.")
            return

        lines = ["<b>⏳ Pending Approvals</b>\n"]
        for a in pending:
            lines.append(
                f"ID: <code>{a['id'][:8]}…</code>\n"
                f"  {html.escape(str(a['description']))}\n"
                f"  Category: {a['category']} | From: {a['agent_id']}\n"
                f"  /approve {a['id'][:8]}\n"
                f"  /reject {a['id'][:8]} reason"
            )
        await msg.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)

    async def _cmd_approve(
        self, update: Update, ctx: ContextTypes.DEFAULT_TYPE
    ) -> None:
        msg = _authorised_message(update)
        if not msg:
            return

        if not ctx.args:
            await msg.reply_text("Usage: /approve <id>")
            return

        approval_id = await self._resolve_approval_id(ctx.args[0])
        if not approval_id:
            await msg.reply_text("❌ Approval not found.")
            return

        from app.services.approval_service import decide_approval

        result = await decide_approval(
            approval_id, approved=True, reason="Approved via Telegram"
        )
        if result:
            await msg.reply_text(
                f"✅ Approved: {html.escape(str(result.get('description', '')))}"
            )
        else:
            await msg.reply_text("❌ Could not approve (not found or already decided).")

    async def _cmd_reject(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        msg = _authorised_message(update)
        if not msg:
            return

        if not ctx.args:
            await msg.reply_text("Usage: /reject <id> [reason]")
            return

        approval_id = await self._resolve_approval_id(ctx.args[0])
        reason = (
            " ".join(ctx.args[1:]) if len(ctx.args) > 1 else "Rejected via Telegram"
        )

        if not approval_id:
            await msg.reply_text("❌ Approval not found.")
            return

        from app.services.approval_service import decide_approval

        result = await decide_approval(approval_id, approved=False, reason=reason)
        if result:
            await msg.reply_text(
                f"❌ Rejected: {html.escape(str(result.get('description', '')))}"
            )
        else:
            await msg.reply_text("❌ Could not reject (not found or already decided).")

    async def _cmd_stop_agent(
        self, update: Update, ctx: ContextTypes.DEFAULT_TYPE
    ) -> None:
        msg = _authorised_message(update)
        if not msg:
            return

        if not ctx.args:
            await msg.reply_text("Usage: /stop_agent <name_or_id>")
            return

        agent = self._find_agent(" ".join(ctx.args))
        if not agent:
            await msg.reply_text("❌ Agent not found.")
            return

        await agent.stop()
        await msg.reply_text(
            f"🔴 Stopped agent <b>{html.escape(agent.name)}</b>",
            parse_mode=ParseMode.HTML,
        )

    async def _cmd_start_agent(
        self, update: Update, ctx: ContextTypes.DEFAULT_TYPE
    ) -> None:
        msg = _authorised_message(update)
        if not msg:
            return

        if not ctx.args:
            await msg.reply_text("Usage: /start_agent <name_or_id>")
            return

        agent = self._find_agent(" ".join(ctx.args))
        if not agent:
            await msg.reply_text("❌ Agent not found.")
            return

        await agent.start()
        await msg.reply_text(
            f"🟢 Started agent <b>{html.escape(agent.name)}</b>",
            parse_mode=ParseMode.HTML,
        )

    async def _cmd_ask(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """Ask a specific agent a question: /ask <agent> <question>."""
        msg = _authorised_message(update)
        if not msg:
            return

        if not ctx.args or len(ctx.args) < 2:
            await msg.reply_text("Usage: /ask <agent_name_or_id> <question>")
            return

        agent = self._find_agent(ctx.args[0])
        if not agent:
            await msg.reply_text("❌ Agent not found.")
            return

        question = " ".join(ctx.args[1:])
        await msg.reply_text(
            f"⏳ Asking {html.escape(agent.name)}…", parse_mode=ParseMode.HTML
        )

        try:
            response = await agent.run(question)
            truncated = response[:3800]
            if len(response) > 3800:
                truncated += "\n\n… (truncated)"
            await msg.reply_text(
                f"<b>{html.escape(agent.name)}</b> says:\n\n{html.escape(truncated)}",
                parse_mode=ParseMode.HTML,
            )
        except Exception as e:
            await msg.reply_text(f"❌ Error: {html.escape(str(e))}")

    async def _cmd_report(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """Trigger an immediate CEO report."""
        msg = _authorised_message(update)
        if not msg:
            return

        from app.agents.registry import registry

        ceo = registry.get("ceo")
        if not ceo:
            await msg.reply_text("❌ CEO agent not available.")
            return

        await msg.reply_text("⏳ Generating CEO report…")
        try:
            report = await ceo.run(
                "Give me a concise status report of the entire holding. "
                "Include: companies, agents, pending approvals, recent activity. "
                "Be brief and structured."
            )
            truncated = report[:3800]
            if len(report) > 3800:
                truncated += "\n\n… (truncated)"
            await msg.reply_text(
                f"📊 <b>CEO Report</b>\n\n{html.escape(truncated)}",
                parse_mode=ParseMode.HTML,
            )
        except Exception as e:
            await msg.reply_text(f"❌ Report failed: {html.escape(str(e))}")

    async def _cmd_notify(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """Toggle notification levels: /notify critical|important|info."""
        msg = _authorised_message(update)
        if not msg:
            return

        if not ctx.args:
            current = ", ".join(sorted(self._notify_levels))
            await msg.reply_text(
                f"<b>Notification levels</b>: {current}\n\n"
                "Toggle with: /notify critical|important|info",
                parse_mode=ParseMode.HTML,
            )
            return

        level = ctx.args[0].lower()
        if level not in (LEVEL_CRITICAL, LEVEL_IMPORTANT, LEVEL_INFO):
            await msg.reply_text("Unknown level. Use: critical, important, info")
            return

        if level in self._notify_levels:
            self._notify_levels.discard(level)
            await msg.reply_text(
                f"🔕 Disabled <b>{level}</b> notifications",
                parse_mode=ParseMode.HTML,
            )
        else:
            self._notify_levels.add(level)
            await msg.reply_text(
                f"🔔 Enabled <b>{level}</b> notifications",
                parse_mode=ParseMode.HTML,
            )

    async def _cmd_web(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """Return the current Cloudflare tunnel dashboard URL."""
        msg = _authorised_message(update)
        if not msg:
            return

        from app.services.cloudflare_tunnel import cloudflare_tunnel

        url = cloudflare_tunnel.url
        if url:
            await msg.reply_text(
                f"🌐 <b>AI Holding Dashboard</b>\n\n"
                f"<a href=\"{url}\">{html.escape(url)}</a>\n\n"
                f"✅ Tunnel active — HTTPS secured by Cloudflare",
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=True,
            )
        else:
            await msg.reply_text(
                "❌ Cloudflare tunnel is not running.\n"
                "The dashboard is starting or the tunnel failed to initialize."
            )

    async def _cmd_costs(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """Show token usage and cost breakdown."""
        msg = _authorised_message(update)
        if not msg:
            return

        from app.services.cost_tracker import cost_tracker

        overview = cost_tracker.get_overview()
        agents_text = ""
        for a in overview.get("agents", []):
            if a["total_calls"] > 0:
                agents_text += (
                    f"  • {a['agent_id']}: {a['total_calls']} calls, "
                    f"${a['total_cost_usd']:.4f}\n"
                )
        if not agents_text:
            agents_text = "  No LLM calls recorded yet\n"

        await msg.reply_text(
            f"💰 <b>Cost Tracking</b>\n\n"
            f"Today: ${overview['cost_today_usd']:.4f} / ${overview['daily_budget_usd']:.2f} "
            f"({overview['budget_used_pct']}%)\n"
            f"Total: ${overview['total_cost_usd']:.4f}\n"
            f"Calls today: {overview['calls_today']}\n\n"
            f"<b>Per Agent:</b>\n{agents_text}",
            parse_mode=ParseMode.HTML,
        )

    async def _cmd_health(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """Show system health status."""
        msg = _authorised_message(update)
        if not msg:
            return

        from app.services.health_monitor import health_monitor

        health = health_monitor.get_health()
        checks_text = ""
        for name, check in health.get("checks", {}).items():
            icon = "✅" if check.get("status") == "healthy" else "❌"
            checks_text += f"  {icon} {name}: {check.get('status', 'unknown')}\n"

        if not checks_text:
            checks_text = "  No health checks run yet\n"

        await msg.reply_text(
            f"🏥 <b>System Health</b>\n\n"
            f"Status: {'✅ Healthy' if health['status'] == 'healthy' else '⚠️ Degraded'}\n"
            f"Uptime: {health['uptime_human']}\n\n"
            f"<b>Service Checks:</b>\n{checks_text}",
            parse_mode=ParseMode.HTML,
        )

    async def _msg_to_ceo(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """Forward any plain text message to the CEO agent."""
        msg = _authorised_message(update)
        if not msg:
            return

        from app.agents.registry import registry

        ceo = registry.get("ceo")
        if not ceo:
            await msg.reply_text("❌ CEO agent not available.")
            return

        user_text = msg.text or ""
        await msg.reply_text("⏳ Sending to CEO…")

        try:
            response = await ceo.run(user_text)
            truncated = response[:3800]
            if len(response) > 3800:
                truncated += "\n\n… (truncated)"
            await msg.reply_text(
                f"<b>CEO</b>:\n{html.escape(truncated)}",
                parse_mode=ParseMode.HTML,
            )
        except Exception as e:
            await msg.reply_text(f"❌ Error: {html.escape(str(e))}")

    # --- daily report job ---

    async def _daily_report_job(self, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """APScheduler job: generate and send daily CEO report."""
        settings = get_settings()
        chat_id = settings.telegram_owner_chat_id
        if not chat_id:
            return

        from app.agents.registry import registry

        ceo = registry.get("ceo")
        if not ceo:
            return

        try:
            report = await ceo.run(
                "Generate the daily report for the AI Holding. "
                "Summarise: companies status, all agents activity today, "
                "tasks completed, pending approvals, any issues or recommendations. "
                "Format it clearly."
            )
            truncated = report[:3800]
            if len(report) > 3800:
                truncated += "\n\n… (truncated)"
            await ctx.bot.send_message(
                chat_id=int(chat_id),
                text=f"📋 <b>Daily CEO Report</b>\n\n{html.escape(truncated)}",
                parse_mode=ParseMode.HTML,
            )
        except Exception as e:
            logger.error(f"Daily report failed: {e}")
            await ctx.bot.send_message(
                chat_id=int(chat_id),
                text=f"❌ Daily report generation failed: {html.escape(str(e))}",
                parse_mode=ParseMode.HTML,
            )

    # --- helpers ---

    @staticmethod
    def _find_agent(query: str):
        """Find an agent by ID or name (case-insensitive)."""
        from app.agents.registry import registry

        # Try exact ID first
        agent = registry.get(query)
        if agent:
            return agent
        # Try name match
        q_lower = query.lower()
        for a in registry.get_all():
            if a.name.lower() == q_lower or a.agent_id.lower().startswith(q_lower):
                return a
        return None

    @staticmethod
    async def _resolve_approval_id(partial_id: str) -> str | None:
        """Resolve a partial approval ID (first 8 chars) to full ID."""
        from app.services.approval_service import list_approvals

        pending = await list_approvals(status="pending")
        for a in pending:
            if a["id"].startswith(partial_id):
                return a["id"]
        return None


# Global singleton
telegram_bot = TelegramBot()
