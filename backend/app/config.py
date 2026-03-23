"""Application configuration loaded from environment variables."""

from pathlib import Path
from pydantic_settings import BaseSettings
from functools import lru_cache

# .env is in project root (one level above backend/)
_ENV_FILE = Path(__file__).resolve().parents[2] / ".env"


class Settings(BaseSettings):
    # App
    app_name: str = "AI Holding"
    debug: bool = False
    secret_key: str = "change-me-in-production"

    # Database
    database_url: str = "postgresql+asyncpg://kimi:kimi@localhost:5432/ai_holding"

    # Redis
    redis_url: str = "redis://localhost:6379/0"

    # Vertex AI
    gcp_project_id: str = ""
    gcp_region: str = "global"
    google_application_credentials: str = ""

    # LLM Model tiers (Vertex AI model identifiers)
    llm_fast: str = "google/gemini-3.1-flash-lite-preview"
    llm_smart: str = "deepseek-ai/deepseek-v3.2-maas"
    llm_reasoning: str = "claude-sonnet-4-6"

    # Telegram
    telegram_bot_token: str = ""
    telegram_owner_chat_id: str = ""
    telegram_admin_chat_ids: str = ""

    # OANDA (Forex)
    oanda_api_key: str = ""
    oanda_account_id: str = ""
    oanda_api_url: str = "https://api-fxpractice.oanda.com"

    # MetaAPI (MT5 wrapper)
    metaapi_token: str = ""
    metaapi_account_id: str = ""

    # Binance Testnet
    binance_testnet_api_key: str = ""
    binance_testnet_api_secret: str = ""

    # Capital.com
    capital_api_key: str = ""
    capital_email: str = ""
    capital_password: str = ""

    # Agent settings
    approval_timeout_hours: int = 24
    auto_approve_below_usd: float = 10.0

    model_config = {"env_file": str(_ENV_FILE), "env_file_encoding": "utf-8"}


@lru_cache
def get_settings() -> Settings:
    return Settings()
