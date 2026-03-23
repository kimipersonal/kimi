"""AI Holding — Main FastAPI Application."""

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import get_settings
from app.db.database import init_db
from app.agents.ceo import CEOAgent
from app.agents.registry import registry
from app.api import agents, companies, dashboard, trading, websocket

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(name)-25s | %(levelname)-7s | %(message)s",
)
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

    # Initialize database
    await init_db()
    logger.info("Database initialized")

    # Create and register the CEO agent
    ceo = CEOAgent(agent_id="ceo")
    registry.register(ceo)
    await ceo.start()

    # Ensure CEO exists in DB
    from app.db.database import async_session
    from app.db.models import Agent as AgentModel, AgentStatus, ModelTier

    async with async_session() as session:
        existing = await session.get(AgentModel, "ceo")
        if not existing:
            db_ceo = AgentModel(
                id="ceo",
                name="CEO",
                role="Chief Executive Officer",
                status=AgentStatus.IDLE,
                model_tier=ModelTier.REASONING,
                system_prompt=ceo.system_prompt,
                tools=ceo.tools,
            )
            session.add(db_ceo)
            await session.commit()
            logger.info("CEO saved to database")

    logger.info("CEO Agent registered and started")

    # Restore sub-agents from DB
    from app.services.company_manager import restore_agents_from_db

    restored = await restore_agents_from_db()
    if restored:
        logger.info(f"Restored {restored} agent(s) from database")

    # Start Telegram bot
    from app.services.telegram_bot import telegram_bot

    await telegram_bot.start()

    # Start trading service
    from app.services.trading.trading_service import trading_service

    await trading_service.startup()

    # Start health monitor
    from app.services.health_monitor import health_monitor

    await health_monitor.start()

    # Wire budget alerts → Telegram
    from app.services.cost_tracker import cost_tracker

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

    # Hook event bus → Telegram notifications
    from app.services.event_bus import event_bus

    _original_broadcast = event_bus.broadcast

    async def _broadcast_with_telegram(
        event: str, data: dict, agent_id: str | None = None
    ):
        await _original_broadcast(event, data, agent_id)
        await telegram_bot.notify(event, data, agent_id)

    event_bus.broadcast = _broadcast_with_telegram  # type: ignore[assignment]

    # Start nginx reverse proxy + Cloudflare tunnel
    from app.services.cloudflare_tunnel import cloudflare_tunnel

    nginx_conf = Path(__file__).resolve().parents[2] / "nginx" / "ai-holding.conf"
    if nginx_conf.exists():
        import subprocess
        # Symlink our config and restart nginx
        target = Path("/etc/nginx/sites-enabled/ai-holding.conf")
        if not target.exists():
            subprocess.run(
                ["sudo", "ln", "-sf", str(nginx_conf), str(target)],
                check=False,
            )
            subprocess.run(
                ["sudo", "rm", "-f", "/etc/nginx/sites-enabled/default"],
                check=False,
            )
            subprocess.run(["sudo", "nginx", "-t"], check=False)
            subprocess.run(["sudo", "systemctl", "restart", "nginx"], check=False)
            logger.info("Nginx reverse proxy configured on port 8080")

    tunnel_url = await cloudflare_tunnel.start(port=8080)
    if tunnel_url:
        logger.info(f"Dashboard available at: {tunnel_url}")
        # Notify owner via Telegram
        try:
            settings = get_settings()
            if telegram_bot._running and telegram_bot._app and settings.telegram_owner_chat_id:
                from telegram.constants import ParseMode
                await telegram_bot._app.bot.send_message(
                    chat_id=int(settings.telegram_owner_chat_id),
                    text=f"🌐 <b>AI Holding Online</b>\n\n"
                         f"Dashboard: {tunnel_url}\n"
                         f"Agents: {registry.count} active\n\n"
                         f"Use /web to get this link anytime.",
                    parse_mode=ParseMode.HTML,
                )
        except Exception as e:
            logger.warning(f"Failed to send tunnel URL to Telegram: {e}")

    yield

    # Shutdown
    logger.info("=== AI Holding Shutting Down ===")
    await health_monitor.stop()
    await cloudflare_tunnel.stop()
    await telegram_bot.stop()
    await trading_service.shutdown()
    await registry.stop_all()


app = FastAPI(
    title="AI Holding",
    description="AI-powered holding company with multi-agent management",
    version="0.1.0",
    lifespan=lifespan,
)

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
app.include_router(trading.router)
app.include_router(websocket.router)


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
    return {"status": "ok"}
