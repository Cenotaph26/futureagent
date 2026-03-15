"""
FuturAgents — Core Configuration
Pydantic Settings'i bypass ederek os.environ'dan direkt okur.
Bu Railway environment variable inject sorununu çözer.
"""
import os
import re
from functools import lru_cache


def _get(key: str, default=None):
    """Case-insensitive env var okuma"""
    # Önce tam adı dene
    val = os.environ.get(key)
    if val:
        return val
    # Büyük harf dene
    val = os.environ.get(key.upper())
    if val:
        return val
    # Küçük harf dene
    val = os.environ.get(key.lower())
    if val:
        return val
    return default


def _get_bool(key: str, default: bool = False) -> bool:
    val = _get(key, str(default))
    return val.lower() in ("true", "1", "yes")


def _get_int(key: str, default: int = 0) -> int:
    val = _get(key, str(default))
    try:
        return int(val)
    except (ValueError, TypeError):
        return default


def _get_float(key: str, default: float = 0.0) -> float:
    val = _get(key, str(default))
    try:
        return float(val)
    except (ValueError, TypeError):
        return default


def _finalize_mongo_url(url: str, db: str = "futuragents") -> str:
    """URL'ye db adı ve authSource ekler, çift slash temizler"""
    # query string ayır
    if "?" in url:
        base, query = url.split("?", 1)
    else:
        base, query = url, ""

    # mongodb://credentials@host:port/dbname formatını parse et
    # port numarasından sonraki her şeyi al
    base = re.sub(r"/{2,}", "//", base)  # çift slash temizle (auth kısmı hariç)
    # Daha doğrusu: port sonrası path'i normalize et
    # mongodb://user:pass@host:12345/olddb -> mongodb://user:pass@host:12345
    base_no_path = re.sub(r"(mongodb(?:\+srv)?://[^/]+/[^/?]*).*", r"\1", base)
    # host:port kısmını al, path'siz
    host_part = re.match(r"(mongodb(?:\+srv)?://[^/]+)", base)
    if host_part:
        new_base = f"{host_part.group(1)}/{db}"
    else:
        new_base = base.rstrip("/") + f"/{db}"

    # query string
    if query:
        if "authSource" not in query:
            return f"{new_base}?{query}&authSource=admin"
        return f"{new_base}?{query}"
    return f"{new_base}?authSource=admin"


class Settings:
    # App
    APP_NAME: str = "FuturAgents"
    APP_VERSION: str = "1.0.0"
    PORT: int = _get_int("PORT", 8000)
    DEBUG: bool = _get_bool("DEBUG", False)
    CORS_ORIGINS: str = _get("CORS_ORIGINS", "*")

    # MongoDB
    MONGODB_DATABASE: str = _get("MONGODB_DATABASE", "futuragents")

    # JWT
    JWT_SECRET: str = _get("JWT_SECRET", "change-me-in-production-32chars")
    JWT_ALGORITHM: str = _get("JWT_ALGORITHM", "HS256")
    ACCESS_TOKEN_EXPIRE_MINUTES: int = _get_int("ACCESS_TOKEN_EXPIRE_MINUTES", 480)
    REFRESH_TOKEN_EXPIRE_DAYS: int = _get_int("REFRESH_TOKEN_EXPIRE_DAYS", 30)
    BCRYPT_ROUNDS: int = _get_int("BCRYPT_ROUNDS", 12)

    # Anthropic
    ANTHROPIC_API_KEY: str = _get("ANTHROPIC_API_KEY", "not-set")
    ANTHROPIC_MODEL: str = _get("ANTHROPIC_MODEL", "claude-opus-4-5")
    ANTHROPIC_FAST_MODEL: str = _get("ANTHROPIC_FAST_MODEL", "claude-haiku-4-5-20251001")
    ANTHROPIC_SONNET_MODEL: str = _get("ANTHROPIC_SONNET_MODEL", "claude-sonnet-4-6")

    # Binance
    BINANCE_API_KEY: str = _get("BINANCE_API_KEY", "not-set")
    BINANCE_API_SECRET: str = _get("BINANCE_API_SECRET", "not-set")
    BINANCE_TESTNET: bool = _get_bool("BINANCE_TESTNET", True)
    BINANCE_FUTURES_BASE_URL: str = _get(
        "BINANCE_FUTURES_BASE_URL", "https://testnet.binancefuture.com"
    )

    # Risk
    MAX_LEVERAGE: int = _get_int("MAX_LEVERAGE", 10)
    DEFAULT_LEVERAGE: int = _get_int("DEFAULT_LEVERAGE", 3)
    MAX_POSITION_SIZE_USDT: float = _get_float("MAX_POSITION_SIZE_USDT", 100.0)
    MAX_OPEN_POSITIONS: int = _get_int("MAX_OPEN_POSITIONS", 5)
    DEFAULT_RISK_PER_TRADE: float = _get_float("DEFAULT_RISK_PER_TRADE", 0.02)
    STOP_LOSS_PERCENT: float = _get_float("STOP_LOSS_PERCENT", 0.02)
    TAKE_PROFIT_PERCENT: float = _get_float("TAKE_PROFIT_PERCENT", 0.04)

    # Auto Execute — Railway Variable: AUTO_EXECUTE_ENABLED=true
    AUTO_EXECUTE_ENABLED: bool = _get_bool("AUTO_EXECUTE_ENABLED", False)

    # Finnhub
    FINNHUB_API_KEY: str = _get("FINNHUB_API_KEY", "")
    FINNHUB_ENABLED: bool = _get_bool("FINNHUB_ENABLED", False)

    # Logging
    LOG_LEVEL: str = _get("LOG_LEVEL", "INFO")
    LOG_DIR: str = _get("LOG_DIR", "/app/logs")

    # Agent
    AGENT_MAX_ITERATIONS: int = _get_int("AGENT_MAX_ITERATIONS", 3)
    ANALYSIS_TIMEOUT_SECONDS: int = _get_int("ANALYSIS_TIMEOUT_SECONDS", 120)
    CACHE_TTL_SECONDS: int = _get_int("CACHE_TTL_SECONDS", 300)

    @property
    def effective_mongodb_url(self) -> str:
        # Tüm olası adları sırayla dene
        for key in ["MONGODB_URL", "MONGO_URL", "MONGO_PRIVATE_URL", "DATABASE_URL"]:
            val = _get(key)
            if val and "mongodb" in val:
                return _finalize_mongo_url(val, self.MONGODB_DATABASE)

        # Parçalardan inşa et
        host = _get("MONGOHOST")
        port = _get("MONGOPORT", "27017")
        user = _get("MONGOUSER", "mongo")
        pwd  = _get("MONGOPASSWORD")
        if host and pwd:
            url = f"mongodb://{user}:{pwd}@{host}:{port}"
            return _finalize_mongo_url(url, self.MONGODB_DATABASE)

        # Debug için mevcut env'i listele
        mongo_keys = [k for k in os.environ if "MONGO" in k.upper() or "DATABASE" in k.upper()]
        raise RuntimeError(
            f"MongoDB URL bulunamadı! "
            f"Mevcut DB variable adları: {mongo_keys}"
        )

    @property
    def effective_redis_url(self) -> str:
        for key in ["REDIS_URL", "REDIS_PRIVATE_URL"]:
            val = _get(key)
            if val:
                return val

        host = _get("REDISHOST")
        port = _get("REDISPORT", "6379")
        pwd  = _get("REDISPASSWORD")
        user = _get("REDISUSER", "default")
        if host and pwd:
            return f"redis://{user}:{pwd}@{host}:{port}"
        if host:
            return f"redis://{host}:{port}"

        redis_keys = [k for k in os.environ if "REDIS" in k.upper()]
        raise RuntimeError(
            f"Redis URL bulunamadı! "
            f"Mevcut REDIS variable adları: {redis_keys}"
        )

    @property
    def binance_testnet_futures_url(self) -> str:
        return "https://testnet.binancefuture.com" if self.BINANCE_TESTNET else "https://fapi.binance.com"

    @property
    def cors_origins_list(self) -> list[str]:
        return [o.strip() for o in self.CORS_ORIGINS.split(",")]


# Singleton — modül yüklendiğinde bir kez oluşturulur
settings = Settings()
