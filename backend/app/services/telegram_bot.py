"""Telegram Bot — Owner notifications, interactive commands, and daily reports."""

import asyncio
import html
import json
import logging
from datetime import time as dt_time
from datetime import timezone

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
    Update,
)
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from app.config import get_settings

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
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
    """Return the update's Message if the sender is authorised, else None.

    Works for both regular messages and callback queries.
    """
    chat = update.effective_chat
    # Prefer regular message, fall back to callback_query.message
    msg = update.message or (update.callback_query.message if update.callback_query else None)
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

    # --- config accessors ---

    @staticmethod
    def _timeout() -> int:
        return get_settings().ceo_run_timeout

    @staticmethod
    def _msg_limit() -> int:
        return get_settings().max_telegram_msg_len

    @staticmethod
    async def _check_permission(update: Update, command: str) -> bool:
        """Check if the user has permission for a command. Returns True if allowed."""
        chat = update.effective_chat
        if not chat:
            return False
        try:
            from app.services.permissions import permission_manager
            return await permission_manager.can_use_command(str(chat.id), command)
        except Exception:
            return True  # fail-open if permission system errors

    @staticmethod
    async def _check_chat_permission(update: Update) -> bool:
        """Check if the user can send free-text messages to CEO."""
        chat = update.effective_chat
        if not chat:
            return False
        try:
            from app.services.permissions import permission_manager
            return await permission_manager.can_chat(str(chat.id))
        except Exception:
            return True

    # --- helpers ---

    @staticmethod
    def _split_message(text: str, limit: int | None = None) -> list[str]:
        """Split a long string into chunks that fit Telegram's message limit."""
        if limit is None:
            limit = get_settings().max_telegram_msg_len
        if len(text) <= limit:
            return [text]
        parts: list[str] = []
        while text:
            if len(text) <= limit:
                parts.append(text)
                break
            # Try to split at last newline within limit
            cut = text.rfind("\n", 0, limit)
            if cut <= 0:
                cut = limit
            parts.append(text[:cut])
            text = text[cut:].lstrip("\n")
        return parts

    @staticmethod
    def _main_menu_keyboard() -> InlineKeyboardMarkup:
        """Build an inline keyboard with the most common actions."""
        return InlineKeyboardMarkup([
            [
                InlineKeyboardButton("📊 Status", callback_data="cmd_status"),
                InlineKeyboardButton("🤖 Agents", callback_data="cmd_agents"),
                InlineKeyboardButton("🏢 Companies", callback_data="cmd_companies"),
            ],
            [
                InlineKeyboardButton("⏳ Approvals", callback_data="cmd_approvals"),
                InlineKeyboardButton("💰 Costs", callback_data="cmd_costs"),
                InlineKeyboardButton("🏥 Health", callback_data="cmd_health"),
            ],
            [
                InlineKeyboardButton("📋 Report", callback_data="cmd_report"),
                InlineKeyboardButton("🤖 Models", callback_data="cmd_models"),
                InlineKeyboardButton("🌐 Web", callback_data="cmd_web"),
            ],
        ])

    @staticmethod
    def _persistent_keyboard() -> ReplyKeyboardMarkup:
        """Build a persistent bottom keyboard always visible to the owner."""
        return ReplyKeyboardMarkup(
            [
                [KeyboardButton("📊 Status"), KeyboardButton("🤖 Agents"), KeyboardButton("🏢 Companies")],
                [KeyboardButton("⏳ Approvals"), KeyboardButton("💰 Costs"), KeyboardButton("📋 Report")],
                [KeyboardButton("🏥 Health"), KeyboardButton("🌐 Web"), KeyboardButton("📖 Menu")],
            ],
            resize_keyboard=True,
            is_persistent=True,
        )

    # --- lifecycle ---

    async def start(self) -> None:
        settings = get_settings()
        if not settings.telegram_bot_token:
            logger.warning("TELEGRAM_BOT_TOKEN not set — Telegram bot disabled")
            return

        self._app = Application.builder().token(settings.telegram_bot_token).build()

        # Register handlers
        self._register_handlers()

        # Schedule daily CEO report
        if self._app.job_queue:
            settings = get_settings()
            self._app.job_queue.run_daily(
                self._daily_report_job,
                time=dt_time(hour=settings.daily_report_hour, minute=0, tzinfo=timezone.utc),
                name="daily_ceo_report",
            )
            logger.info(f"Daily CEO report scheduled at {settings.daily_report_hour:02d}:00 UTC")

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
        reply_markup = None
        # Add inline approve/reject buttons for approval requests
        if event == "approval_request" and data.get("id"):
            aid = data["id"][:8]
            reply_markup = InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("✅ Approve", callback_data=f"approve_{aid}"),
                    InlineKeyboardButton("❌ Reject", callback_data=f"reject_{aid}"),
                ]
            ])
        try:
            await self._app.bot.send_message(
                chat_id=int(chat_id),
                text=text,
                parse_mode=ParseMode.HTML,
                reply_markup=reply_markup,
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
        self._app.add_handler(CommandHandler("menu", self._cmd_menu))
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
        self._app.add_handler(CommandHandler("models", self._cmd_models))
        self._app.add_handler(CommandHandler("model", self._cmd_model))
        self._app.add_handler(CommandHandler("audit", self._cmd_audit))
        self._app.add_handler(CommandHandler("backup", self._cmd_backup))
        self._app.add_handler(CommandHandler("perf", self._cmd_perf))
        # Inline keyboard callbacks
        self._app.add_handler(CallbackQueryHandler(self._callback_handler))
        # Persistent keyboard button text → route to matching command
        self._app.add_handler(
            MessageHandler(
                filters.Regex(r"^(📊 Status|🤖 Agents|🏢 Companies|⏳ Approvals|💰 Costs|📋 Report|🏥 Health|🌐 Web|📖 Menu)$"),
                self._persistent_keyboard_handler,
            )
        )
        # Photo & document messages → forward caption or description to CEO
        self._app.add_handler(
            MessageHandler(filters.PHOTO | filters.Document.ALL, self._media_to_ceo)
        )
        # Voice messages → transcribe via Vertex AI and forward to CEO
        self._app.add_handler(
            MessageHandler(filters.VOICE | filters.AUDIO, self._voice_to_ceo)
        )
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
            "Use the buttons below or just send a message to the CEO.\n"
            "Type /help for the full command list.",
            parse_mode=ParseMode.HTML,
            reply_markup=self._persistent_keyboard(),
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
            "/models — view current model tiers\n"
            "/model &lt;tier&gt; &lt;model_id&gt; — change a tier's model\n"
            "/audit — recent CEO action log\n"
            "/perf — agent performance scores\n"
            "/backup — export system backup\n"
            "/menu — show quick-action buttons\n"
            "\nOr send any text to chat with the CEO.\n"
            "You can also send <b>photos</b>, <b>documents</b>, or <b>voice messages</b>.",
            parse_mode=ParseMode.HTML,
        )

    async def _cmd_menu(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """Show the inline keyboard menu."""
        msg = _authorised_message(update)
        if not msg:
            return
        await msg.reply_text(
            "🏢 <b>AI Holding — Quick Actions</b>",
            parse_mode=ParseMode.HTML,
            reply_markup=self._main_menu_keyboard(),
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

        if not await self._check_permission(update, "approve"):
            await msg.reply_text("🔒 You don't have permission to approve requests.")
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
            # Notify CEO so it knows the decision
            await self._notify_ceo_approval(result, approved=True)
        else:
            await msg.reply_text("❌ Could not approve (not found or already decided).")

    async def _cmd_reject(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        msg = _authorised_message(update)
        if not msg:
            return

        if not await self._check_permission(update, "reject"):
            await msg.reply_text("🔒 You don't have permission to reject requests.")
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
            # Notify CEO so it knows the decision
            await self._notify_ceo_approval(result, approved=False, reason=reason)
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
            response = await asyncio.wait_for(
                agent.run(question), timeout=self._timeout()
            )
            parts = self._split_message(html.escape(response))
            for i, part in enumerate(parts):
                prefix = f"<b>{html.escape(agent.name)}</b> says:\n\n" if i == 0 else ""
                await msg.reply_text(
                    f"{prefix}{part}", parse_mode=ParseMode.HTML
                )
        except asyncio.TimeoutError:
            await msg.reply_text(
                f"⏱ {html.escape(agent.name)} took too long (>{self._timeout()}s)."
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
            report = await asyncio.wait_for(
                ceo.run(
                    "Give me a concise status report of the entire holding. "
                    "Include: companies, agents, pending approvals, recent activity. "
                    "Be brief and structured."
                ),
                timeout=self._timeout(),
            )
            parts = self._split_message(html.escape(report))
            for i, part in enumerate(parts):
                prefix = "📊 <b>CEO Report</b>\n\n" if i == 0 else ""
                await msg.reply_text(
                    f"{prefix}{part}", parse_mode=ParseMode.HTML
                )
        except asyncio.TimeoutError:
            await msg.reply_text(f"⏱ Report generation timed out (>{self._timeout()}s).")
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

        overview = await cost_tracker.get_overview_with_lifetime()
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
            f"This week: ${overview['total_cost_usd']:.4f}\n"
            f"<b>All-time: ${overview.get('lifetime_cost_usd', 0):.4f}</b> "
            f"({overview.get('lifetime_calls', 0)} calls, "
            f"{overview.get('lifetime_tokens', 0):,} tokens)\n"
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

    async def _cmd_audit(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """Show recent CEO audit log entries."""
        msg = _authorised_message(update)
        if not msg:
            return

        from app.services.audit_log import audit_log

        entries = await audit_log.get_entries(limit=15)
        if not entries:
            await msg.reply_text("📋 No audit entries yet.")
            return

        lines: list[str] = ["📋 <b>Recent Audit Log</b>\n"]
        for e in reversed(entries):
            icon = "✅" if e["success"] else "❌"
            ts = e["timestamp"][:19].replace("T", " ")
            lines.append(f"{icon} <code>{ts}</code> <b>{e['action']}</b>")
        await msg.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)

    async def _cmd_perf(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """Show agent performance scores."""
        msg = _authorised_message(update)
        if not msg:
            return

        from app.services.performance_tracker import performance_tracker

        scores = await performance_tracker.get_all_scores()
        if not scores:
            await msg.reply_text("📊 No performance data yet.")
            return

        grade_emoji = {"A": "🏆", "B": "🥈", "C": "🥉", "D": "📉", "F": "❌", "N/A": "❓"}
        lines: list[str] = ["📊 <b>Agent Performance</b>\n"]
        for s in scores:
            emoji = grade_emoji.get(s["grade"], "❓")
            lines.append(
                f"{emoji} <b>{s['agent_id'][:16]}</b> — "
                f"Score: {s['score']} ({s['grade']}) | "
                f"Success: {s['success_rate']}% | "
                f"Tasks: {s['tasks_completed']}/{s['tasks_completed'] + s['tasks_failed']}"
            )
        await msg.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)

    async def _cmd_backup(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """Export system backup as a JSON file."""
        msg = _authorised_message(update)
        if not msg:
            return

        if not await self._check_permission(update, "backup"):
            await msg.reply_text("🔒 You don't have permission to create backups.")
            return

        import io
        from app.services.backup import create_backup

        await msg.reply_text("💾 Creating backup…")
        try:
            backup = await create_backup()
            backup_json = json.dumps(backup, indent=2, ensure_ascii=False)
            file_bytes = io.BytesIO(backup_json.encode("utf-8"))
            file_bytes.name = f"ai_holding_backup_{backup['created_at'][:10]}.json"
            await msg.reply_document(
                document=file_bytes,
                caption="💾 AI Holding System Backup",
            )
        except Exception as e:
            await msg.reply_text(f"❌ Backup failed: {html.escape(str(e))}")

    async def _cmd_models(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """Show current model tier assignments and available models."""
        msg = _authorised_message(update)
        if not msg:
            return

        from app.services.llm_router import AVAILABLE_MODELS, MODEL_TIERS

        lines = [
            "<b>🤖 Model Tiers</b>\n",
            f"⚡ Fast: <code>{html.escape(MODEL_TIERS['fast'])}</code>",
            f"🧠 Smart: <code>{html.escape(MODEL_TIERS['smart'])}</code>",
            f"💎 Reasoning: <code>{html.escape(MODEL_TIERS['reasoning'])}</code>",
            "\n<b>Available Models:</b>",
        ]
        for m in AVAILABLE_MODELS:
            lines.append(f"  • <code>{html.escape(m['id'])}</code> ({m['name']}) {m['cost']}")
        lines.append("\nChange: /model &lt;fast|smart|reasoning&gt; &lt;model_id&gt;")
        await msg.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)

    async def _cmd_model(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """Change a model tier: /model <tier> <model_id>."""
        msg = _authorised_message(update)
        if not msg:
            return

        if not await self._check_permission(update, "model"):
            await msg.reply_text("🔒 You don't have permission to change models.")
            return

        if not ctx.args or len(ctx.args) < 2:
            await msg.reply_text(
                "Usage: /model &lt;fast|smart|reasoning&gt; &lt;model_id&gt;\n"
                "Example: /model fast gemini-2.5-flash\n\n"
                "See /models for available model IDs.",
                parse_mode=ParseMode.HTML,
            )
            return

        tier = ctx.args[0].lower()
        model_id = ctx.args[1]

        from app.services.llm_router import update_tier

        try:
            update_tier(tier, model_id)
            await msg.reply_text(
                f"✅ Updated <b>{tier}</b> tier → <code>{html.escape(model_id)}</code>",
                parse_mode=ParseMode.HTML,
            )
        except ValueError as e:
            await msg.reply_text(f"❌ {html.escape(str(e))}")

    async def _msg_to_ceo(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """Forward any plain text message to the CEO agent."""
        msg = _authorised_message(update)
        if not msg:
            return

        if not await self._check_chat_permission(update):
            await msg.reply_text("🔒 You don't have permission to chat with the CEO.")
            return

        from app.agents.registry import registry

        ceo = registry.get("ceo")
        if not ceo:
            await msg.reply_text("❌ CEO agent not available.")
            return

        user_text = msg.text or ""
        await msg.reply_text("⏳ Sending to CEO…")

        try:
            response = await asyncio.wait_for(
                ceo.run(user_text), timeout=self._timeout()
            )
            parts = self._split_message(html.escape(response))
            for i, part in enumerate(parts):
                prefix = "<b>CEO</b>:\n" if i == 0 else ""
                await msg.reply_text(
                    f"{prefix}{part}", parse_mode=ParseMode.HTML
                )
        except asyncio.TimeoutError:
            await msg.reply_text(
                f"⏱ CEO took too long (>{self._timeout()}s). Try a simpler question."
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
            report = await asyncio.wait_for(
                ceo.run(
                    "Generate the daily report for the AI Holding. "
                    "Summarise: companies status, all agents activity today, "
                    "tasks completed, pending approvals, any issues or recommendations. "
                    "Format it clearly."
                ),
                timeout=self._timeout(),
            )
            parts = self._split_message(html.escape(report))
            for i, part in enumerate(parts):
                prefix = "📋 <b>Daily CEO Report</b>\n\n" if i == 0 else ""
                await ctx.bot.send_message(
                    chat_id=int(chat_id),
                    text=f"{prefix}{part}",
                    parse_mode=ParseMode.HTML,
                )
        except (asyncio.TimeoutError, Exception) as e:
            err_msg = "timed out" if isinstance(e, asyncio.TimeoutError) else str(e)
            logger.error(f"Daily report failed: {err_msg}")
            await ctx.bot.send_message(
                chat_id=int(chat_id),
                text=f"❌ Daily report generation failed: {html.escape(err_msg)}",
                parse_mode=ParseMode.HTML,
            )

    # --- approval feedback to CEO ---

    @staticmethod
    async def _notify_ceo_approval(
        result: dict, approved: bool, reason: str = ""
    ) -> None:
        """Inform the CEO agent about an approval decision so it can act on it."""
        from app.agents.registry import registry

        ceo = registry.get("ceo")
        if not ceo:
            return

        status = "APPROVED" if approved else "REJECTED"
        reason_text = f" Reason: {reason}" if reason else ""
        description = result.get("description", "unknown request")

        # Fire-and-forget — CEO processes the decision as a new message
        try:
            await asyncio.wait_for(
                ceo.run(
                    f"[SYSTEM] Your approval request '{description}' has been {status} "
                    f"by the Owner.{reason_text} Take the appropriate next steps."
                ),
                timeout=TelegramBot._timeout(),
            )
        except Exception as e:
            logger.debug(f"CEO approval notification failed: {e}")

    # --- inline keyboard callback ---

    async def _callback_handler(
        self, update: Update, ctx: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Handle inline keyboard button presses."""
        query = update.callback_query
        if not query:
            return
        await query.answer()

        # Handle approve/reject inline buttons
        if query.data and query.data.startswith("approve_"):
            aid_prefix = query.data[len("approve_"):]
            approval_id = await self._resolve_approval_id(aid_prefix)
            if not approval_id:
                await query.edit_message_reply_markup(reply_markup=None)
                await query.message.reply_text("❌ Approval not found or already decided.")
                return
            from app.services.approval_service import decide_approval
            result = await decide_approval(approval_id, approved=True, reason="Approved via Telegram button")
            if result:
                await query.edit_message_reply_markup(reply_markup=None)
                await query.message.reply_text(f"✅ Approved: {html.escape(str(result.get('description', '')))}")
                await self._notify_ceo_approval(result, approved=True)
            else:
                await query.edit_message_reply_markup(reply_markup=None)
                await query.message.reply_text("❌ Could not approve (already decided).")
            return

        if query.data and query.data.startswith("reject_"):
            aid_prefix = query.data[len("reject_"):]
            approval_id = await self._resolve_approval_id(aid_prefix)
            if not approval_id:
                await query.edit_message_reply_markup(reply_markup=None)
                await query.message.reply_text("❌ Approval not found or already decided.")
                return
            from app.services.approval_service import decide_approval
            result = await decide_approval(approval_id, approved=False, reason="Rejected via Telegram button")
            if result:
                await query.edit_message_reply_markup(reply_markup=None)
                await query.message.reply_text(f"❌ Rejected: {html.escape(str(result.get('description', '')))}")
                await self._notify_ceo_approval(result, approved=False, reason="Rejected via Telegram button")
            else:
                await query.edit_message_reply_markup(reply_markup=None)
                await query.message.reply_text("❌ Could not reject (already decided).")
            return

        # Map callback_data → handler method
        handlers = {
            "cmd_status": self._cmd_status,
            "cmd_agents": self._cmd_agents,
            "cmd_companies": self._cmd_companies,
            "cmd_approvals": self._cmd_approvals,
            "cmd_costs": self._cmd_costs,
            "cmd_health": self._cmd_health,
            "cmd_report": self._cmd_report,
            "cmd_models": self._cmd_models,
            "cmd_web": self._cmd_web,
        }

        handler = handlers.get(query.data)
        if handler:
            await handler(update, ctx)

    # --- persistent keyboard handler ---

    async def _persistent_keyboard_handler(
        self, update: Update, ctx: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Route persistent keyboard button text to the matching command."""
        msg = _authorised_message(update)
        if not msg or not msg.text:
            return

        _kb_map = {
            "📊 Status": self._cmd_status,
            "🤖 Agents": self._cmd_agents,
            "🏢 Companies": self._cmd_companies,
            "⏳ Approvals": self._cmd_approvals,
            "💰 Costs": self._cmd_costs,
            "📋 Report": self._cmd_report,
            "🏥 Health": self._cmd_health,
            "🌐 Web": self._cmd_web,
            "📖 Menu": self._cmd_menu,
        }

        handler = _kb_map.get(msg.text.strip())
        if handler:
            await handler(update, ctx)

    # --- media handler ---

    async def _voice_to_ceo(
        self, update: Update, ctx: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Handle voice messages — transcribe via Gemini and forward to CEO."""
        msg = _authorised_message(update)
        if not msg:
            return

        voice = msg.voice or msg.audio
        if not voice:
            await msg.reply_text("❌ No voice data found.")
            return

        await msg.reply_text("🎙 Transcribing voice message…")

        try:
            # Download voice file
            voice_file = await ctx.bot.get_file(voice.file_id)
            voice_bytes = await voice_file.download_as_bytearray()

            # Use Gemini to transcribe (multimodal audio support)
            import base64
            from app.services.llm_router import chat as llm_chat

            audio_b64 = base64.b64encode(bytes(voice_bytes)).decode("utf-8")
            mime_type = voice.mime_type or "audio/ogg"

            # Use Gemini flash for fast transcription
            transcription_response = await llm_chat(
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "text",
                                "text": "Transcribe this voice message exactly. Return ONLY the transcription text, nothing else.",
                            },
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": f"data:{mime_type};base64,{audio_b64}",
                                },
                            },
                        ],
                    }
                ],
                tier="fast",
            )

            transcript = transcription_response["content"].strip()
            if not transcript:
                await msg.reply_text("❌ Could not transcribe the voice message.")
                return

            await msg.reply_text(
                f"📝 <b>Transcript:</b> {html.escape(transcript)}",
                parse_mode=ParseMode.HTML,
            )

            # Forward transcription to CEO
            from app.agents.registry import registry

            ceo = registry.get("ceo")
            if not ceo:
                return

            response = await asyncio.wait_for(
                ceo.run(f"[Voice message transcription] {transcript}"),
                timeout=self._timeout(),
            )
            parts = self._split_message(html.escape(response))
            for i, part in enumerate(parts):
                prefix = "<b>CEO</b>:\n" if i == 0 else ""
                await msg.reply_text(f"{prefix}{part}", parse_mode=ParseMode.HTML)
        except asyncio.TimeoutError:
            await msg.reply_text(f"⏱ CEO took too long (>{self._timeout()}s).")
        except Exception as e:
            logger.error(f"Voice message processing failed: {e}")
            await msg.reply_text(f"❌ Voice processing failed: {html.escape(str(e))}")

    async def _media_to_ceo(
        self, update: Update, ctx: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Handle photos and documents — forward the caption to CEO."""
        msg = _authorised_message(update)
        if not msg:
            return

        caption = msg.caption or ""
        file_desc = ""

        if msg.photo:
            file_desc = "[Photo attached]"
        elif msg.document:
            doc = msg.document
            file_desc = f"[Document: {doc.file_name or 'unknown'}, {doc.file_size or 0} bytes]"

        user_text = f"{file_desc} {caption}".strip()
        if not user_text:
            await msg.reply_text(
                "📎 Got your file. Add a caption with instructions for the CEO."
            )
            return

        from app.agents.registry import registry

        ceo = registry.get("ceo")
        if not ceo:
            await msg.reply_text("❌ CEO agent not available.")
            return

        await msg.reply_text("⏳ Sending to CEO…")
        try:
            response = await asyncio.wait_for(
                ceo.run(user_text), timeout=self._timeout()
            )
            parts = self._split_message(html.escape(response))
            for i, part in enumerate(parts):
                prefix = "<b>CEO</b>:\n" if i == 0 else ""
                await msg.reply_text(f"{prefix}{part}", parse_mode=ParseMode.HTML)
        except asyncio.TimeoutError:
            await msg.reply_text(
                f"⏱ CEO took too long (>{self._timeout()}s). Try a simpler question."
            )
        except Exception as e:
            await msg.reply_text(f"❌ Error: {html.escape(str(e))}")

    # --- find / resolve helpers ---

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
