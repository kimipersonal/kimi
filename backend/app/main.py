"""AI Holding — Main FastAPI Application."""

import asyncio
import logging
import logging.handlers
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from slowapi.util import get_remote_address
from sqlalchemy import text as sa_text

from app.config import get_settings
from app.db.database import init_db
from app.agents.registry import registry
from app.api import agents, companies, dashboard, skills, trading, websocket, webhooks
from app import startup as _startup

# Ensure logs directory exists
_log_dir = Path(__file__).resolve().parents[1] / "logs"
_log_dir.mkdir(exist_ok=True)

# Configure root logger with console + rotating file handler
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(name)-25s | %(levelname)-7s | %(message)s",
)
_file_handler = logging.handlers.RotatingFileHandler(
    _log_dir / "backend.log",
    maxBytes=10 * 1024 * 1024,  # 10 MB
    backupCount=5,
    encoding="utf-8",
)
_file_handler.setFormatter(
    logging.Formatter("%(asctime)s | %(name)-25s | %(levelname)-7s | %(message)s")
)
logging.getLogger().addHandler(_file_handler)
logger = logging.getLogger(__name__)
settings = get_settings()

# Ensure GOOGLE_APPLICATION_CREDENTIALS is set as absolute path for LiteLLM/Vertex
if settings.google_application_credentials:
    creds_path = Path(settings.google_application_credentials)
    if not creds_path.is_absolute():
        creds_path = Path(__file__).resolve().parents[2] / creds_path
    os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = str(creds_path)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown logic."""
    logger.info("=== AI Holding Starting ===")

    # ── Environment validation ──
    if not settings.debug:
        missing = []
        if not settings.api_key:
            missing.append("API_KEY")
        if settings.secret_key == "change-me-in-production":
            missing.append("SECRET_KEY (still default)")
        if missing:
            logger.warning("Production security: missing %s", ", ".join(missing))

    # Initialize database
    await init_db()
    logger.info("Database initialized")

    # Plugins, skills, state summary
    await _startup.load_plugins()
    await _startup.load_skills()

    state_summary, company_count, agent_count = await _startup.build_state_summary()

    # Create and register CEO
    ceo = await _startup.register_ceo(state_summary)

    # Restore sub-agents + orphaned tasks
    await _startup.restore_runtime()

    # Core services (Telegram, trading, scheduler, health, watchdog, analytics)
    await _startup.start_services()

    # Phase 2-5 services
    await _startup.load_phase_services()

    # Background tasks
    _bg_tasks = await _create_background_tasks(ceo, company_count, agent_count)

    # Budget wiring
    await _wire_budget_alerts(ceo)

    # Tunnel
    await _startup.setup_tunnel()

    yield

    # Shutdown
    logger.info("=== AI Holding Shutting Down ===")
    for t in _bg_tasks:
        t.cancel()
    await _startup.shutdown_all()


# ---------------------------------------------------------------------------
# Background task factories
# ---------------------------------------------------------------------------

async def _create_background_tasks(ceo, company_count: int, agent_count: int) -> list:
    """Spawn all recurring background tasks and return their handles."""

    async def _approval_expiry_loop():
        from app.services.approval_service import expire_old_approvals
        while True:
            try:
                await asyncio.sleep(3600)
                count = await expire_old_approvals()
                if count:
                    logger.info(f"Expired {count} stale approval(s)")
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Approval expiry task error: {e}")

    async def _daily_cost_reset_loop():
        from datetime import datetime, timezone, timedelta
        from app.services.cost_tracker import cost_tracker
        while True:
            try:
                now = datetime.now(timezone.utc)
                tomorrow = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
                await asyncio.sleep((tomorrow - now).total_seconds())
                cost_tracker.reset_daily()
                from app.services.budget_enforcer import budget_enforcer
                budget_enforcer.reset_daily()
                from app.services.auto_trade_executor import auto_trade_executor as _ate
                _ate.reset_daily()
                try:
                    from app.services.error_pattern_detector import error_pattern_detector
                    from app.services.prompt_optimizer import prompt_optimizer
                    await error_pattern_detector.scan_audit_log()
                    await error_pattern_detector.save_to_redis()
                    await prompt_optimizer.capture_all_snapshots()
                except Exception as _p4e:
                    logger.debug(f"Phase 4 daily tasks: {_p4e}")
                logger.info("Daily cost counters and budget enforcement reset")
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Daily cost reset error: {e}")

    async def _ceo_event_reactor():
        from app.services.event_bus import event_bus
        subscriber = event_bus.subscribe("dashboard")
        try:
            while True:
                event = await subscriber.get()
                event_type = event.get("event", "")
                data = event.get("data", {})
                if event_type not in ("agent_stuck", "task_failed", "agent_report", "task_completed"):
                    continue
                if data.get("agent_id") == "ceo":
                    continue
                try:
                    prompt = _build_ceo_event_prompt(event_type, data)
                    if not prompt:
                        continue
                    logger.info(f"CEO reacting to {event_type}: {prompt[:100]}...")
                    await asyncio.wait_for(ceo.run(prompt), timeout=120)
                except asyncio.TimeoutError:
                    logger.warning(f"CEO reaction to {event_type} timed out")
                except Exception as e:
                    logger.error(f"CEO reaction to {event_type} failed: {e}")
        except asyncio.CancelledError:
            pass

    async def _ceo_proactive_loop():
        await asyncio.sleep(120)
        while True:
            try:
                prompt = (
                    "[SCHEDULED REVIEW] Perform your periodic operations review:\n"
                    "1) Use check_agent_health — restart any stuck or errored agents.\n"
                    "2) Use get_costs — review daily spending. If >80% of budget, "
                    "consider pausing non-essential agents and report to Owner.\n"
                    "3) Use check_status — verify all companies are healthy.\n"
                    "4) If any issues found, take corrective action.\n"
                    "5) Keep responses brief — this is a routine check."
                )
                logger.info("CEO proactive review starting...")
                await asyncio.wait_for(ceo.run(prompt), timeout=180)
            except asyncio.TimeoutError:
                logger.warning("CEO proactive review timed out")
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"CEO proactive loop error: {e}")
            await asyncio.sleep(7200)

    async def _ceo_startup_review():
        await asyncio.sleep(30)
        if company_count > 0 or agent_count > 0:
            try:
                prompt = (
                    "[SYSTEM STARTUP] The system has just started. You must:\n"
                    "1) Use check_status to review all companies and agents.\n"
                    "2) Use check_agent_health to find stuck or errored agents. Restart them.\n"
                    "3) Use list_schedules to check your recurring tasks.\n"
                    "4) If no recurring health check schedule exists, create one:\n"
                    "   schedule_task for yourself: 'check_agent_health and restart stuck agents' hourly.\n"
                    "5) Send a brief startup report to the Owner summarizing the current state."
                )
                logger.info("CEO performing startup review...")
                await asyncio.wait_for(ceo.run(prompt), timeout=180)
            except asyncio.TimeoutError:
                logger.warning("CEO startup review timed out")
            except Exception as e:
                logger.error(f"CEO startup review failed: {e}")

    async def _ceo_bootstrap_schedules():
        await asyncio.sleep(15)
        try:
            from app.services.ceo_self_scheduler import bootstrap_ceo_schedules
            result = await bootstrap_ceo_schedules()
            if result.get("created"):
                logger.info(f"CEO self-scheduler created {len(result['created'])} schedule(s)")
            elif result.get("skipped"):
                logger.info("CEO self-scheduler: schedules already exist")
        except Exception as e:
            logger.error(f"CEO self-scheduler failed: {e}")

    async def _daily_report_loop():
        try:
            from app.services.ceo_self_scheduler import setup_daily_report_schedule
            await setup_daily_report_schedule()
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"Daily report loop failed: {e}")

    async def _trade_monitor_loop():
        """Periodically check open trades against SL/TP levels and expire stale signals."""
        await asyncio.sleep(60)  # Wait for services to initialize
        while True:
            try:
                from app.services.trading.trading_service import trading_service
                if trading_service.is_connected:
                    closed = await trading_service.check_open_trades()
                    if closed:
                        logger.info(f"Trade monitor auto-closed {len(closed)} trade(s): {closed}")
                    expired = await trading_service.expire_stale_signals()
                    if expired:
                        logger.info(f"Trade monitor expired {expired} stale signal(s)")
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.debug(f"Trade monitor error: {e}")
            await asyncio.sleep(30)  # Check every 30 seconds

    async def _position_review_loop():
        """Periodically trigger risk managers to review open positions.

        Uses historical TP achievement data per symbol so the agent can
        make realistic exit decisions instead of blindly waiting for TP.
        """
        await asyncio.sleep(120)  # Wait for services
        while True:
            try:
                from app.services.trading.trading_service import trading_service
                if not trading_service.is_connected:
                    await asyncio.sleep(300)
                    continue

                # Get open trades
                history = await trading_service.get_trade_history(limit=50)
                open_trades = [t for t in history if t.get("status") == "open"]
                if not open_trades:
                    await asyncio.sleep(600)
                    continue

                # Gather historical TP profiles for each symbol in play
                symbols_in_play = set(t["symbol"] for t in open_trades)
                tp_profiles: dict[str, dict] = {}
                for sym in symbols_in_play:
                    try:
                        tp_profiles[sym] = await trading_service._get_tp_profile_cached(sym)
                    except Exception:
                        tp_profiles[sym] = {"sufficient_data": False}

                # Build a rich summary for the risk manager
                positions_summary = []
                for t in open_trades:
                    try:
                        prices = await trading_service.get_prices([t["symbol"]])
                        curr = prices[0].get("last", 0) if prices else 0
                        entry = t.get("entry_price", 0)
                        size = t.get("size", 0)
                        if not (entry and curr and size):
                            continue

                        if t.get("side") == "buy":
                            pnl = (curr - entry) * size
                            pnl_pct = (curr - entry) / entry * 100
                        else:
                            pnl = (entry - curr) * size
                            pnl_pct = (entry - curr) / entry * 100

                        # Compute TP progress
                        tp = t.get("take_profit")
                        tp_note = ""
                        if tp and entry:
                            tp_dist = abs(tp - entry)
                            if t.get("side") == "buy":
                                curr_move = max(0, curr - entry)
                            else:
                                curr_move = max(0, entry - curr)
                            tp_progress = (curr_move / tp_dist * 100) if tp_dist > 0 else 0
                            tp_note = f" TP_progress={tp_progress:.0f}%"

                        # Historical context
                        profile = tp_profiles.get(t["symbol"], {})
                        hist_note = ""
                        if profile.get("sufficient_data"):
                            hist_note = (
                                f" [HISTORY: TP hit rate={profile['tp_hit_rate_pct']:.0f}%, "
                                f"median achieved={profile['median_tp_achieved_pct']:.0f}% of TP, "
                                f"avg peak={profile['avg_peak_toward_tp_pct']:.0f}% of TP]"
                            )

                        # Time open
                        opened = t.get("opened_at", "")
                        time_note = ""
                        if opened:
                            from datetime import datetime, timezone
                            try:
                                if isinstance(opened, str):
                                    opened_dt = datetime.fromisoformat(opened)
                                else:
                                    opened_dt = opened
                                hours = (datetime.now(timezone.utc) - opened_dt).total_seconds() / 3600
                                time_note = f" open={hours:.1f}h"
                            except Exception:
                                pass

                        positions_summary.append(
                            f"  - {t['symbol']} {t['side']} size={size} "
                            f"entry={entry:.2f} curr={curr:.2f} "
                            f"SL={t.get('stop_loss', 'N/A')} TP={tp or 'N/A'} "
                            f"PnL=${pnl:.2f} ({pnl_pct:+.1f}%)"
                            f"{tp_note}{time_note}{hist_note} "
                            f"trade_id={t['id']}"
                        )
                    except Exception:
                        pass

                if not positions_summary:
                    await asyncio.sleep(600)
                    continue

                # Find risk managers and trigger review
                risk_managers = [
                    a for a in registry.get_all()
                    if getattr(a, "role", "") == "risk_manager"
                    and "auto" not in getattr(a, "name", "").lower()
                ]
                if risk_managers:
                    prompt = (
                        f"[POSITION REVIEW] You have {len(open_trades)} open position(s). "
                        f"Review each one and decide if action is needed:\n"
                        + "\n".join(positions_summary) + "\n\n"
                        f"DECISION RULES (follow strictly):\n"
                        f"1. DO NOT close trades that have been open less than 1 hour — they need time to develop.\n"
                        f"2. DO NOT close trades where price is between SL and TP — the SL/TP monitor handles those.\n"
                        f"3. Only close a trade if it has been open > 4 hours AND is making no progress toward TP.\n"
                        f"4. A slightly negative PnL within 1% of entry is NORMAL — do NOT panic-close.\n"
                        f"5. Losing trades: use close_trade — the system will automatically tighten the SL "
                        f"instead of closing, reducing your max loss while giving the trade a chance to recover.\n"
                        f"6. Only profitable trades will actually be closed by close_trade. "
                        f"Losing trades get SL protection instead.\n"
                        f"7. NEVER close all positions at once — evaluate each independently, max ONE per review.\n"
                        f"8. If no trade meets close criteria, report 'No action needed' and move on.\n\n"
                        f"Use check_sl_tp first. Then use close_trade on trades you think need action."
                    )
                    rm = risk_managers[0]
                    try:
                        await asyncio.wait_for(rm.run(prompt), timeout=120)
                    except (asyncio.TimeoutError, Exception) as e:
                        logger.debug(f"Position review by {rm.name} issue: {e}")

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.debug(f"Position review loop error: {e}")
            await asyncio.sleep(600)  # Every 10 minutes

    async def _signal_review_reactor():
        """Event-driven: when a new signal needs review, immediately trigger the
        Risk Manager agent instead of waiting for its next scheduled interval.

        This eliminates the 15-45 minute gap where a signal sits idle.
        """
        from app.services.event_bus import event_bus
        subscriber = event_bus.subscribe("dashboard")
        try:
            while True:
                event = await subscriber.get()
                if event.get("event") != "signal_needs_review":
                    continue

                data = event.get("data", {})
                signal_id = data.get("signal_id")
                symbol = data.get("symbol", "?")
                direction = data.get("direction", "?")
                confidence = data.get("confidence", 0)

                # Find risk_manager agents (exclude auto_trade_executor role —
                # those already handle execution at signal-creation time)
                risk_managers = [
                    a for a in registry.get_all()
                    if getattr(a, "role", "") == "risk_manager"
                    and "auto" not in getattr(a, "name", "").lower()
                    and getattr(a, "role", "") != "auto_trade_executor"
                ]
                if not risk_managers:
                    logger.warning(f"Signal {signal_id} needs review but no risk_manager agents found")
                    continue

                prompt = (
                    f"[URGENT SIGNAL REVIEW] A new trade signal was just created and needs your "
                    f"immediate review:\n"
                    f"  Signal ID: {signal_id}\n"
                    f"  Symbol: {symbol}\n"
                    f"  Direction: {direction}\n"
                    f"  Confidence: {confidence}\n"
                    f"  Entry: {data.get('entry_price')}\n"
                    f"  SL: {data.get('stop_loss')} | TP: {data.get('take_profit')}\n"
                    f"  Reasoning: {data.get('reasoning', 'N/A')}\n\n"
                    f"This signal expires in 15 minutes. Act NOW:\n"
                    f"1. If the signal looks good (R:R >= 1.5, reasonable SL/TP), approve it immediately "
                    f"with approve_signal.\n"
                    f"2. If it's weak, reject it with reject_signal and provide reason.\n"
                    f"Do NOT delay — the price is moving."
                )

                # Run ALL risk managers in PARALLEL — sequential review was
                # burning through the signal TTL and causing expirations
                async def _review(rm, sig_id, sym, dirn, conf):
                    try:
                        logger.info(
                            f"Triggering {rm.name} for immediate signal review: "
                            f"{sym} {dirn} (conf={conf})"
                        )
                        await asyncio.wait_for(rm.run(prompt), timeout=90)
                    except asyncio.TimeoutError:
                        logger.warning(f"Risk Manager {rm.name} timed out reviewing signal {sig_id}")
                    except Exception as e:
                        logger.error(f"Risk Manager {rm.name} failed to review signal: {e}")

                await asyncio.gather(
                    *[_review(rm, signal_id, symbol, direction, confidence) for rm in risk_managers],
                    return_exceptions=True,
                )
        except asyncio.CancelledError:
            pass
        finally:
            event_bus.unsubscribe("dashboard", subscriber)

    return [
        asyncio.create_task(_approval_expiry_loop(), name="approval_expiry"),
        asyncio.create_task(_daily_cost_reset_loop(), name="daily_cost_reset"),
        asyncio.create_task(_ceo_event_reactor(), name="ceo_event_reactor"),
        asyncio.create_task(_ceo_proactive_loop(), name="ceo_proactive_loop"),
        asyncio.create_task(_ceo_startup_review(), name="ceo_startup_review"),
        asyncio.create_task(_ceo_bootstrap_schedules(), name="ceo_bootstrap_schedules"),
        asyncio.create_task(_daily_report_loop(), name="daily_report_loop"),
        asyncio.create_task(_trade_monitor_loop(), name="trade_monitor"),
        asyncio.create_task(_signal_review_reactor(), name="signal_review_reactor"),
        asyncio.create_task(_position_review_loop(), name="position_review"),
    ]


def _build_ceo_event_prompt(event_type: str, data: dict) -> str | None:
    """Build a CEO prompt for a system event."""
    if event_type == "agent_stuck":
        return (
            f"[SYSTEM ALERT] Agent {data.get('agent_id')} is stuck in "
            f"{data.get('status')} state for {data.get('stuck_seconds')}s. "
            f"Use check_agent_health, restart the agent if needed, and report the issue."
        )
    if event_type == "task_failed":
        return (
            f"[SYSTEM ALERT] Task {data.get('task_id', 'unknown')} failed "
            f"for agent {data.get('agent_id', 'unknown')}. "
            f"Error: {str(data.get('error', ''))[:300]}. "
            f"Check what happened and take corrective action."
        )
    if event_type == "agent_report":
        priority = data.get("priority", "medium")
        if priority not in ("high", "critical"):
            return None
        return (
            f"[AGENT REPORT — {priority.upper()}] "
            f"Agent {data.get('agent_name', data.get('agent_id', 'unknown'))} "
            f"reports: \"{data.get('title', 'No title')}\" — "
            f"{str(data.get('content', ''))[:500]}. "
            f"Review and take action if needed."
        )
    if event_type == "task_completed":
        return (
            f"[TASK COMPLETED] Task {data.get('task_id', 'unknown')} "
            f"completed by agent {data.get('agent_name', data.get('agent_id', 'unknown'))}. "
            f"Result preview: {str(data.get('result', ''))[:300]}. "
            f"Determine if any follow-up action is needed."
        )
    return None


async def _wire_budget_alerts(ceo):
    """Wire cost tracker and budget enforcer alerts to Telegram + CEO."""
    from app.services.cost_tracker import cost_tracker
    from app.services.telegram_bot import telegram_bot
    from app.services.budget_enforcer import budget_enforcer

    await cost_tracker._load_from_redis()

    async def _budget_alert(current: float, budget: float):
        try:
            if telegram_bot._running and telegram_bot._app and settings.telegram_owner_chat_id:
                from telegram.constants import ParseMode
                await telegram_bot._app.bot.send_message(
                    chat_id=int(settings.telegram_owner_chat_id),
                    text=f"⚠️ <b>Budget Alert</b>\n\n"
                         f"Daily spend: ${current:.4f} / ${budget:.2f}\n"
                         f"Usage: {current / budget * 100:.0f}%\n\n"
                         f"Consider pausing non-essential agents.",
                    parse_mode=ParseMode.HTML,
                )
        except Exception as e:
            logger.warning(f"Failed to send budget alert: {e}")

    cost_tracker.on_budget_alert(_budget_alert)

    await budget_enforcer._ensure_loaded()

    async def _budget_enforced(entity_id: str, reason: str):
        try:
            if telegram_bot._running and telegram_bot._app and settings.telegram_owner_chat_id:
                from telegram.constants import ParseMode
                await telegram_bot._app.bot.send_message(
                    chat_id=int(settings.telegram_owner_chat_id),
                    text=f"🚫 <b>Budget Enforced</b>\n\n{reason}\n\n"
                         f"Agent/company has been auto-paused.",
                    parse_mode=ParseMode.HTML,
                )
        except Exception as e:
            logger.warning(f"Failed to send budget enforcement alert: {e}")
        try:
            prompt = (
                f"[BUDGET ENFORCEMENT] {reason}\n"
                f"The affected agent/company has been auto-paused. "
                f"Review the situation with check_budget_status and decide "
                f"if any reallocation or action is needed."
            )
            await asyncio.wait_for(ceo.run(prompt), timeout=120)
        except Exception as e:
            logger.warning(f"CEO budget reaction failed: {e}")

    budget_enforcer.on_budget_exceeded(_budget_enforced)

    # Hook event bus → Telegram notifications
    from app.services.event_bus import event_bus

    _original_broadcast = event_bus.broadcast

    async def _broadcast_with_telegram(
        event: str, data: dict, agent_id: str | None = None
    ):
        await _original_broadcast(event, data, agent_id)
        await telegram_bot.notify(event, data, agent_id)

    event_bus.broadcast = _broadcast_with_telegram  # type: ignore[assignment]


app = FastAPI(
    title="AI Holding",
    description="AI-powered holding company with multi-agent management",
    version="0.1.0",
    lifespan=lifespan,
)

# Rate limiting — 60 requests/minute per IP across all endpoints
limiter = Limiter(key_func=get_remote_address, default_limits=["60/minute"])
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.add_middleware(SlowAPIMiddleware)

# CORS — allow dashboard frontend (local + tunnel)
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"https?://(localhost(:\d+)?|127\.0\.0\.1(:\d+)?|.*\.trycloudflare\.com|.*\.devtunnels\.ms)",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount API routers
app.include_router(agents.router)
app.include_router(companies.router)
app.include_router(dashboard.router)
app.include_router(skills.router)
app.include_router(trading.router)
app.include_router(websocket.router)
app.include_router(webhooks.router)


@app.get("/")
async def root():
    return {
        "name": "AI Holding",
        "version": "0.1.0",
        "status": "running",
        "ceo_status": registry.get("ceo").status.value
        if registry.get("ceo")
        else "not_initialized",
        "total_agents": registry.count,
    }


@app.get("/health")
async def health():
    """Detailed health check — verifies DB, Redis, and agent status."""
    checks: dict = {"status": "ok"}

    # Database
    try:
        from app.db.database import async_session
        async with async_session() as session:
            await session.execute(sa_text("SELECT 1"))
        checks["database"] = "ok"
    except Exception as e:
        checks["database"] = f"error: {e}"
        checks["status"] = "degraded"

    # Redis
    try:
        from app.db.database import redis_pool
        await redis_pool.ping()
        checks["redis"] = "ok"
    except Exception as e:
        checks["redis"] = f"error: {e}"
        checks["status"] = "degraded"

    # Agents
    checks["agents_total"] = registry.count
    checks["agents_active"] = registry.active_count

    return checks
