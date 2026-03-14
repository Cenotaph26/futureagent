"""
FuturAgents — Database Layer
"""
import logging
from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase
import redis.asyncio as aioredis
from app.core.config import settings

logger = logging.getLogger(__name__)

_mongo_client: AsyncIOMotorClient | None = None
_redis_client: aioredis.Redis | None = None


async def connect_mongo() -> None:
    global _mongo_client
    url = settings.effective_mongodb_url
    # Şifreyi loglamadan host kısmını göster
    safe_url = url.split("@")[-1] if "@" in url else url
    logger.info(f"MongoDB bağlanıyor: {safe_url}")
    _mongo_client = AsyncIOMotorClient(
        url,
        serverSelectionTimeoutMS=10000,
        maxPoolSize=10,
        minPoolSize=1,
    )
    await _mongo_client.admin.command("ping")
    logger.info("✅ MongoDB bağlantısı kuruldu")


async def disconnect_mongo() -> None:
    global _mongo_client
    if _mongo_client:
        _mongo_client.close()


async def connect_redis() -> None:
    global _redis_client
    url = settings.effective_redis_url
    safe_url = url.split("@")[-1] if "@" in url else url
    logger.info(f"Redis bağlanıyor: {safe_url}")
    _redis_client = await aioredis.from_url(
        url,
        encoding="utf-8",
        decode_responses=True,
        max_connections=10,
    )
    await _redis_client.ping()
    logger.info("✅ Redis bağlantısı kuruldu")


async def disconnect_redis() -> None:
    global _redis_client
    if _redis_client:
        await _redis_client.aclose()


def get_db() -> AsyncIOMotorDatabase:
    if _mongo_client is None:
        raise RuntimeError("MongoDB bağlantısı kurulmamış")
    return _mongo_client[settings.MONGODB_DATABASE]


def get_redis() -> aioredis.Redis:
    if _redis_client is None:
        raise RuntimeError("Redis bağlantısı kurulmamış")
    return _redis_client


async def ensure_indexes() -> None:
    """
    Index oluşturma — disk dolu olduğunda atla.
    Index'ler olmadan uygulama çalışır, sadece yavaş sorgular olabilir.
    """
    try:
        db = get_db()
        await db.users.create_index("email", unique=True)
        await db.users.create_index("username", unique=True)
        await db.analyses.create_index([("symbol", 1), ("created_at", -1)])
        await db.signals.create_index([("symbol", 1), ("created_at", -1)])
        logger.info("✅ MongoDB indeksleri oluşturuldu")
    except Exception as e:
        # Disk dolu veya başka hata — index olmadan devam et
        logger.warning(f"⚠️ Index oluşturulamadı (uygulama çalışmaya devam eder): {e}")
