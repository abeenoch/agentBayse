from functools import lru_cache
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", case_sensitive=False, extra="ignore")

    app_secret_key: str = Field(..., env="APP_SECRET_KEY")
    admin_username: str = Field(..., env="ADMIN_USERNAME")
    admin_password: str = Field(..., env="ADMIN_PASSWORD")
    jwt_algorithm: str = Field("HS256", env="JWT_ALGORITHM")
    access_token_expire_minutes: int = Field(1440, env="ACCESS_TOKEN_EXPIRE_MINUTES")

    bayse_base_url: str = Field("https://relay.bayse.markets/v1", env="BAYSE_API_BASE_URL")
    bayse_public_key: str = Field("", env="BAYSE_PUBLIC_KEY")
    bayse_private_key: str = Field("", env="BAYSE_PRIVATE_KEY")
    bayse_default_currency: str = Field("NGN", env="BAYSE_DEFAULT_CURRENCY")

    ai_provider: str = Field("gemini", env="AI_PROVIDER")
    gemini_api_key: str = Field("", env="GEMINI_API_KEY")
    gemini_model: str = Field("gemini-2.5-flash", env="GEMINI_MODEL")
    groq_api_key: str = Field("", env="GROQ_API_KEY")
    groq_model: str = Field("llama-3.3-70b-versatile", env="GROQ_MODEL")
    anthropic_api_key: str = Field("", env="ANTHROPIC_API_KEY")
    openai_api_key: str = Field("", env="OPENAI_API_KEY")

    search_provider: str = Field("tavily", env="SEARCH_PROVIDER")
    tavily_api_key: str = Field("", env="TAVILY_API_KEY")
    serpapi_key: str = Field("", env="SERPAPI_KEY")
    search_depth: str = Field("advanced", env="SEARCH_DEPTH")
    search_max_results: int = Field(8, env="SEARCH_MAX_RESULTS")
    search_time_range: str | None = Field(None, env="SEARCH_TIME_RANGE")
    search_include_domains: str | None = Field(None, env="SEARCH_INCLUDE_DOMAINS")
    search_exclude_domains: str | None = Field(None, env="SEARCH_EXCLUDE_DOMAINS")

    agent_auto_trade: bool = Field(False, env="AGENT_AUTO_TRADE")
    agent_max_position_size: float = Field(5000.0, env="AGENT_MAX_POSITION_SIZE")
    agent_scan_interval_seconds: int = Field(900, env="AGENT_SCAN_INTERVAL_SECONDS")
    agent_max_daily_trades: int = Field(20, env="AGENT_MAX_DAILY_TRADES")
    agent_min_confidence: int = Field(20, env="AGENT_MIN_CONFIDENCE")
    agent_max_open_positions: int = Field(3, env="AGENT_MAX_OPEN_POSITIONS")
    agent_balance_reserve_pct: float = Field(0.30, env="AGENT_BALANCE_RESERVE_PCT")  # keep 30% back
    agent_ignore_balance_check: bool = Field(False, env="AGENT_IGNORE_BALANCE_CHECK")
    agent_event_page_size: int = Field(50, env="AGENT_EVENT_PAGE_SIZE")
    agent_event_pages: int = Field(4, env="AGENT_EVENT_PAGES")
    agent_reanalyze_minutes: int = Field(25, env="AGENT_REANALYZE_MINUTES")
    # Comma-separated series slugs for the regular agent cycle (empty = all known series)
    agent_series_slugs: str = Field("", env="AGENT_SERIES_SLUGS")

    database_url: str = Field(
        "postgresql+asyncpg://postgres:postgres@localhost:5432/agent_bayse",
        env="DATABASE_URL",
    )

    frontend_origin: str = Field("http://localhost:5173", env="FRONTEND_ORIGIN")

    mock_mode: bool = Field(True, env="MOCK_MODE")

    # Sniper
    snipe_observe_seconds: int = Field(300, env="SNIPE_OBSERVE_SECONDS")  # start watching 5 min out
    snipe_min_seconds: int = Field(8, env="SNIPE_MIN_SECONDS")            # abort if < 8s remain
    snipe_series_slugs: str = Field("crypto-sol-5min,crypto-btc-5min,crypto-eth-5min,fx-usdngn-1h,fx-gbpusd-1h", env="SNIPE_SERIES_SLUGS")

    # Stop-loss
    stop_loss_pct: float = Field(0.35, env="STOP_LOSS_PCT")

@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
