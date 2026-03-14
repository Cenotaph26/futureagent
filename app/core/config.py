"""
FuturAgents — Core Configuration
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

    # ── MongoDB — Railway MONGO_URL de olabilir ───────────────────────
    # Railway plugin'i MONGO_URL olarak inject eder, biz MONGODB_URL de kabul ederiz
    MONGODB_URL: Optional[str] = None
    MONGO_URL: Optional[str] = None          # Railway'in kendi adı
    MONGODB_DATABASE: str = "futuragents"

    # ── Redis — Railway REDIS_URL zaten doğru adı kullanıyor ──────────
    REDIS_URL: Optional[str] = None

    # ── JWT / Auth ────────────────────────────────────────────────────
    JWT_SECRET: str = "change-me-in-production-min-32-chars-!!"
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 480
    REFRESH_TOKEN_EXPIRE_DAYS: int = 30
    BCRYPT_ROUNDS: int = 12

    # ── Anthropic Claude ──────────────────────────────────────────────
    ANTHROPIC_API_KEY: str = "not-set"
    ANTHROPIC_MODEL: str = "claude-opus-4-5"
    ANTHROPIC_FAST_MODEL: str = "claude-haiku-4-5-20251001"
    ANTHROPIC_SONNET_MODEL: str = "claude-sonnet-4-6"

    # ── Binance Futures ───────────────────────────────────────────────
    BINANCE_API_KEY: str = "not-set"
    BINANCE_API_SECRET: str = "not-set"
    BINANCE_TESTNET: bool = True
    BINANCE_FUTURES_BASE_URL: str = "https://testnet.binancefuture.com"

    # ── Risk Management ───────────────────────────────────────────────
    MAX_LEVERAGE: int = 10
    DEFAULT_LEVERAGE: int = 3
    MAX_POSITION_SIZE_USDT: float = 100.0
    MAX_OPEN_POSITIONS: int = 5
    DEFAULT_RISK_PER_TRADE: float = 0.02
    STOP_LOSS_PERCENT: float = 0.02
    TAKE_PROFIT_PERCENT: float = 0.04

    # ── Finnhub ───────────────────────────────────────────────────────
    FINNHUB_API_KEY: Optional[str] = None
    FINNHUB_ENABLED: bool = False

    # ── Logging ───────────────────────────────────────────────────────
    LOG_LEVEL: str = "INFO"
    LOG_DIR: str = "/app/logs"

    # ── Agent Tuning ──────────────────────────────────────────────────
    AGENT_MAX_ITERATIONS: int = 3
    ANALYSIS_TIMEOUT_SECONDS: int = 120
    CACHE_TTL_SECONDS: int = 300

    @property
    def effective_mongodb_url(self) -> str:
        """MONGODB_URL > MONGO_URL sırasıyla dener, /futuragents ekler"""
        url = self.MONGODB_URL or self.MONGO_URL
        if not url:
            raise RuntimeError(
                "MongoDB URL bulunamadı! "
                "MONGODB_URL veya MONGO_URL variable'ını ekle."
            )
        # Eğer /futuragents yoksa ekle
        if "/futuragents" not in url:
            url = url.rstrip("/") + "/futuragents"
        # authSource yoksa ekle
        if "authSource" not in url:
            url += "?authSource=admin"
        return url

    @property
    def effective_redis_url(self) -> str:
        if not self.REDIS_URL:
            raise RuntimeError(
                "Redis URL bulunamadı! REDIS_URL variable'ını ekle."
            )
        return self.REDIS_URL

    @property
    def binance_testnet_futures_url(self) -> str:
        return "https://testnet.binancefuture.com" if self.BINANCE_TESTNET else "https://fapi.binance.com"

    @property
    def cors_origins_list(self) -> list[str]:
        return [o.strip() for o in self.CORS_ORIGINS.split(",")]


@lru_cache()
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
