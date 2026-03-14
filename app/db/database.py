"""
FuturAgents — Database Layer
MongoDB (motor async) + Redis bağlantı yönetimi
"""
import logging
from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase
import redis.asyncio as aioredis
from app.core.config import settings

logger = logging.getLogger(__name__)

# Global bağlantı nesneleri
_mongo_client: AsyncIOMotorClient | None = None
_redis_client: aioredis.Redis | None = None


async def connect_mongo() -> None:
    global _mongo_client
    _mongo_client = AsyncIOMotorClient(
        settings.MONGODB_URL,
        serverSelectionTimeoutMS=5000,
        maxPoolSize=20,
        minPoolSize=5,
    )
    # Bağlantıyı test et
    await _mongo_client.admin.command("ping")
    logger.info("✅ MongoDB bağlantısı kuruldu")


async def disconnect_mongo() -> None:
    global _mongo_client
    if _mongo_client:
        _mongo_client.close()
        logger.info("MongoDB bağlantısı kapatıldı")


async def connect_redis() -> None:
    global _redis_client
    _redis_client = await aioredis.from_url(
        settings.REDIS_URL,
        encoding="utf-8",
        decode_responses=True,
        max_connections=20,
    )
    await _redis_client.ping()
    logger.info("✅ Redis bağlantısı kuruldu")


async def disconnect_redis() -> None:
    global _redis_client
    if _redis_client:
        await _redis_client.aclose()
        logger.info("Redis bağlantısı kapatıldı")


def get_db() -> AsyncIOMotorDatabase:
    if _mongo_client is None:
        raise RuntimeError("MongoDB bağlantısı kurulmamış")
    return _mongo_client[settings.MONGODB_DATABASE]


def get_redis() -> aioredis.Redis:
    if _redis_client is None:
        raise RuntimeError("Redis bağlantısı kurulmamış")
    return _redis_client


async def ensure_indexes() -> None:
    """Gerekli MongoDB indekslerini oluştur"""
    db = get_db()

    # Users
    await db.users.create_index("email", unique=True)
    await db.users.create_index("username", unique=True)

    # Positions
    await db.positions.create_index([("symbol", 1), ("status", 1)])
    await db.positions.create_index("opened_at")
    await db.positions.create_index("user_id")

    # Analysis history
    await db.analyses.create_index([("symbol", 1), ("created_at", -1)])
    await db.analyses.create_index("user_id")

    # Agent signals
    await db.signals.create_index([("symbol", 1), ("created_at", -1)])
    await db.signals.create_index("status")

    # Orders
    await db.orders.create_index("binance_order_id", unique=True, sparse=True)
    await db.orders.create_index([("symbol", 1), ("created_at", -1)])

    # Trade logs
    await db.trade_logs.create_index("created_at")
    await db.trade_logs.create_index("position_id")

    logger.info("✅ MongoDB indeksleri oluşturuldu")
