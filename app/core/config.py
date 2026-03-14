"""
FuturAgents — Core Configuration
Railway MongoDB plugin şu variable'ları inject eder:
  MONGOHOST, MONGOPORT, MONGOUSER, MONGOPASSWORD, MONGO_URL, MONGODB_URL
Hepsini dener, bulunanı kullanır.
"""
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field
from typing import Optional
from functools import lru_cache
import os


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

    # ── MongoDB — tüm olası Railway inject adları ─────────────────────
    MONGODB_URL: Optional[str] = None       # Manuel eklenen
    MONGO_URL: Optional[str] = None         # Railway MONGO_URL
    MONGO_PRIVATE_URL: Optional[str] = None # Railway private URL
    MONGOHOST: Optional[str] = None         # Railway ayrı parçalar
    MONGOPORT: Optional[str] = None
    MONGOUSER: Optional[str] = None
    MONGOPASSWORD: Optional[str] = None
    MONGODB_DATABASE: str = "futuragents"

    # ── Redis ─────────────────────────────────────────────────────────
    REDIS_URL: Optional[str] = None
    REDIS_PRIVATE_URL: Optional[str] = None
    REDISHOST: Optional[str] = None
    REDISPORT: Optional[str] = None
    REDISPASSWORD: Optional[str] = None
    REDISUSER: Optional[str] = None

    # ── JWT / Auth ────────────────────────────────────────────────────
    JWT_SECRET: str = "change-me-in-production-min-32-chars"
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
        """
        Railway MongoDB URL'sini şu sırayla dener:
        1. MONGODB_URL
        2. MONGO_URL
        3. MONGO_PRIVATE_URL
        4. MONGOHOST/MONGOPORT/MONGOPASSWORD parçalarından yeniden inşa
        5. os.environ'dan direkt okuma (pydantic görmese bile)
        """
        # pydantic field'lardan dene
        candidates = [
            self.MONGODB_URL,
            self.MONGO_URL,
            self.MONGO_PRIVATE_URL,
        ]
        for url in candidates:
            if url:
                return self._finalize_mongo_url(url)

        # os.environ'dan direkt dene (pydantic bazen görmez)
        for key in ["MONGODB_URL", "MONGO_URL", "MONGO_PRIVATE_URL",
                    "MONGO_PRIVATE_URL", "DATABASE_URL"]:
            val = os.environ.get(key)
            if val and val.startswith("mongodb"):
                return self._finalize_mongo_url(val)

        # Parçalardan URL inşa et
        host = self.MONGOHOST or os.environ.get("MONGOHOST")
        port = self.MONGOPORT or os.environ.get("MONGOPORT", "27017")
        user = self.MONGOUSER or os.environ.get("MONGOUSER", "mongo")
        pwd  = self.MONGOPASSWORD or os.environ.get("MONGOPASSWORD")

        if host and pwd:
            url = f"mongodb://{user}:{pwd}@{host}:{port}"
            return self._finalize_mongo_url(url)

        # Hiçbiri yoksa tüm env'i logla ve hata ver
        all_mongo = {k: "***" for k in os.environ if "MONGO" in k.upper()}
        raise RuntimeError(
            f"MongoDB URL bulunamadı! "
            f"Mevcut MONGO* değişkenler: {all_mongo}. "
            f"Railway Variables'a MONGODB_URL ekle."
        )

    def _finalize_mongo_url(self, url: str) -> str:
        """URL'ye /futuragents ve ?authSource=admin ekle"""
        if "/futuragents" not in url:
            url = url.rstrip("/") + "/futuragents"
        if "authSource" not in url:
            sep = "&" if "?" in url else "?"
            url += f"{sep}authSource=admin"
        return url

    @property
    def effective_redis_url(self) -> str:
        # pydantic field'lardan
        for url in [self.REDIS_URL, self.REDIS_PRIVATE_URL]:
            if url:
                return url

        # os.environ'dan
        for key in ["REDIS_URL", "REDIS_PRIVATE_URL"]:
            val = os.environ.get(key)
            if val:
                return val

        # Parçalardan inşa
        host = self.REDISHOST or os.environ.get("REDISHOST")
        port = self.REDISPORT or os.environ.get("REDISPORT", "6379")
        pwd  = self.REDISPASSWORD or os.environ.get("REDISPASSWORD")
        user = self.REDISUSER or os.environ.get("REDISUSER", "default")

        if host and pwd:
            return f"redis://{user}:{pwd}@{host}:{port}"
        if host:
            return f"redis://{host}:{port}"

        all_redis = {k: "***" for k in os.environ if "REDIS" in k.upper()}
        raise RuntimeError(
            f"Redis URL bulunamadı! "
            f"Mevcut REDIS* değişkenler: {all_redis}. "
            f"Railway Variables'a REDIS_URL ekle."
        )

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
