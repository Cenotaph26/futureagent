"""
FuturAgents — Core Configuration
Tüm environment variable'lar burada yönetilir.
"""
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field
from typing import Optional
from functools import lru_cache


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore"
    )

    # ── App ──────────────────────────────────────────────────────────
    APP_NAME: str = "FuturAgents"
    APP_VERSION: str = "1.0.0"
    DEBUG: bool = False
    PORT: int = 8000
    WORKERS: int = 1
    CORS_ORIGINS: str = "*"

    # ── MongoDB ───────────────────────────────────────────────────────
    MONGODB_URL: str = Field(..., description="MongoDB connection string")
    MONGODB_DATABASE: str = "futuragents"

    # ── Redis ─────────────────────────────────────────────────────────
    REDIS_URL: str = Field(..., description="Redis connection string")

    # ── JWT / Auth ────────────────────────────────────────────────────
    JWT_SECRET: str = Field(..., description="Secret key for JWT tokens")
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 480
    REFRESH_TOKEN_EXPIRE_DAYS: int = 30
    BCRYPT_ROUNDS: int = 12

    # ── Anthropic Claude (ANA LLM) ────────────────────────────────────
    ANTHROPIC_API_KEY: str = Field(..., description="Anthropic Claude API key")
    ANTHROPIC_MODEL: str = "claude-opus-4-5"          # Orchestrator için güçlü model
    ANTHROPIC_FAST_MODEL: str = "claude-haiku-4-5-20251001"  # Hızlı analizler için
    ANTHROPIC_SONNET_MODEL: str = "claude-sonnet-4-6"  # Balanced tasks için

    # ── Binance Futures ───────────────────────────────────────────────
    BINANCE_API_KEY: str = Field(..., description="Binance API key")
    BINANCE_API_SECRET: str = Field(..., description="Binance API secret")
    BINANCE_TESTNET: bool = True                        # False = GERÇEK PARA
    BINANCE_FUTURES_BASE_URL: str = "https://testnet.binancefuture.com"  # Testnet
    # BINANCE_FUTURES_BASE_URL: str = "https://fapi.binance.com"  # Mainnet

    # ── Risk Management ───────────────────────────────────────────────
    MAX_LEVERAGE: int = 10             # Maksimum kaldıraç
    DEFAULT_LEVERAGE: int = 3          # Varsayılan kaldıraç
    MAX_POSITION_SIZE_USDT: float = 100.0  # Tek pozisyon max $ (testnet)
    MAX_OPEN_POSITIONS: int = 5        # Aynı anda max açık pozisyon
    DEFAULT_RISK_PER_TRADE: float = 0.02   # Sermayenin %2'si per trade
    STOP_LOSS_PERCENT: float = 0.02    # %2 stop loss
    TAKE_PROFIT_PERCENT: float = 0.04  # %4 take profit

    # ── Finnhub (opsiyonel piyasa haberleri) ──────────────────────────
    FINNHUB_API_KEY: Optional[str] = None
    FINNHUB_ENABLED: bool = False

    # ── Logging ───────────────────────────────────────────────────────
    LOG_LEVEL: str = "INFO"
    LOG_DIR: str = "/app/logs"

    # ── Agent Tuning ──────────────────────────────────────────────────
    AGENT_MAX_ITERATIONS: int = 3      # Her agent max döngü
    ANALYSIS_TIMEOUT_SECONDS: int = 120
    CACHE_TTL_SECONDS: int = 300       # 5 dakika kısa-term cache

    @property
    def binance_testnet_futures_url(self) -> str:
        return "https://testnet.binancefuture.com" if self.BINANCE_TESTNET else "https://fapi.binance.com"

    @property
    def binance_testnet_stream_url(self) -> str:
        return "wss://stream.binancefuture.com" if self.BINANCE_TESTNET else "wss://fstream.binance.com"

    @property
    def cors_origins_list(self) -> list[str]:
        return [o.strip() for o in self.CORS_ORIGINS.split(",")]


@lru_cache()
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
