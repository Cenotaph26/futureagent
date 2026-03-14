"""
FuturAgents — Core Configuration
"""
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field
from typing import Optional
from functools import lru_cache
import os
import re


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
    MONGODB_URL: Optional[str] = None
    MONGO_URL: Optional[str] = None
    MONGO_PRIVATE_URL: Optional[str] = None
    MONGOHOST: Optional[str] = None
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
        candidates = [
            self.MONGODB_URL,
            self.MONGO_URL,
            self.MONGO_PRIVATE_URL,
        ]
        for url in candidates:
            if url:
                return self._finalize_mongo_url(url)

        for key in ["MONGODB_URL", "MONGO_URL", "MONGO_PRIVATE_URL", "DATABASE_URL"]:
            val = os.environ.get(key)
            if val and val.startswith("mongodb"):
                return self._finalize_mongo_url(val)

        host = self.MONGOHOST or os.environ.get("MONGOHOST")
        port = self.MONGOPORT or os.environ.get("MONGOPORT", "27017")
        user = self.MONGOUSER or os.environ.get("MONGOUSER", "mongo")
        pwd  = self.MONGOPASSWORD or os.environ.get("MONGOPASSWORD")
        if host and pwd:
            url = f"mongodb://{user}:{pwd}@{host}:{port}"
            return self._finalize_mongo_url(url)

        raise RuntimeError("MongoDB URL bulunamadı! MONGODB_URL variable'ını ekle.")

    def _finalize_mongo_url(self, url: str) -> str:
        """
        URL'yi normalize et:
        - Çift slash'ları temizle
        - /futuragents ekle (yoksa)
        - ?authSource=admin ekle (yoksa)
        """
        # Önce query string'i ayır
        if "?" in url:
            base, query = url.split("?", 1)
        else:
            base, query = url, ""

        # host:port kısmından sonraki path'i parse et
        # mongodb://user:pass@host:port/dbname  formatı
        # Regex: protocol://credentials@host:port/path
        match = re.match(r"^(mongodb(?:\+srv)?://[^/]+/?)(.*)$", base)
        if match:
            prefix = match.group(1).rstrip("/")  # mongodb://...@host:port
            path   = match.group(2).strip("/")   # mevcut db adı (varsa)

            if not path or path == "":
                # DB adı yok, ekle
                new_base = f"{prefix}/futuragents"
            elif path == "futuragents":
                # Zaten doğru
                new_base = f"{prefix}/futuragents"
            else:
                # Başka bir db adı var, futuragents ile değiştir
                new_base = f"{prefix}/futuragents"
        else:
            new_base = base.rstrip("/") + "/futuragents"

        # query string'i yeniden ekle
        if query:
            # authSource yoksa ekle
            if "authSource" not in query:
                final = f"{new_base}?{query}&authSource=admin"
            else:
                final = f"{new_base}?{query}"
        else:
            final = f"{new_base}?authSource=admin"

        return final

    @property
    def effective_redis_url(self) -> str:
        for url in [self.REDIS_URL, self.REDIS_PRIVATE_URL]:
            if url:
                return url

        for key in ["REDIS_URL", "REDIS_PRIVATE_URL"]:
            val = os.environ.get(key)
            if val:
                return val

        host = self.REDISHOST or os.environ.get("REDISHOST")
        port = self.REDISPORT or os.environ.get("REDISPORT", "6379")
        pwd  = self.REDISPASSWORD or os.environ.get("REDISPASSWORD")
        user = self.REDISUSER or os.environ.get("REDISUSER", "default")

        if host and pwd:
            return f"redis://{user}:{pwd}@{host}:{port}"
        if host:
            return f"redis://{host}:{port}"

        raise RuntimeError("Redis URL bulunamadı! REDIS_URL variable'ını ekle.")

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
