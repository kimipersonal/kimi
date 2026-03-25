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

    # Alternate LLM Providers (for multi-provider failover)
    anthropic_api_key: str = ""
    openai_api_key: str = ""
    groq_api_key: str = ""

    # LLM Model tiers (Vertex AI model identifiers)
    llm_fast: str = "gemini-2.5-flash-lite"
    llm_smart: str = "deepseek-ai/deepseek-v3.2-maas"
    llm_reasoning: str = "gemini-2.5-pro"

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

    # Owner
    owner_name: str = ""  # Owner's display name (used in CEO prompt)
    owner_language: str = ""  # e.g. "Uzbek", "English" — empty = match user's language

    # GitHub Models API (optional — for using GitHub-hosted models)
    github_token: str = ""  # GitHub personal access token with copilot scope

    # Agent settings
    approval_timeout_hours: int = 24
    auto_approve_below_usd: float = 10.0
    ceo_auto_mode: bool = False  # When True, CEO auto-approves all actions (no human approval needed)
    conversation_history_size: int = 20  # messages kept in CEO's rolling context

    # Telegram settings
    ceo_run_timeout: int = 120  # seconds — max time for a single CEO run
    max_telegram_msg_len: int = 4000  # Telegram limit ~4096, leave headroom
    daily_report_hour: int = 9  # UTC hour for daily CEO report

    # Cost tracking
    daily_budget_usd: float = 5.0
    budget_alert_threshold: float = 0.8  # alert at 80% of budget

    # Plugins
    enabled_plugins: str = ""  # comma-separated list, empty = load all

    model_config = {"env_file": str(_ENV_FILE), "env_file_encoding": "utf-8"}


@lru_cache
def get_settings() -> Settings:
    return Settings()
