"""FuturAgents — News API Routes"""
import json
import logging
from fastapi import APIRouter
from app.db.database import get_db, get_redis

logger = logging.getLogger(__name__)
router = APIRouter()

COINS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT"]


@router.get("/latest")
async def get_latest_news():
    """Redis'ten son haber analizlerini döner — sayfa yenilemede geçmişi korur"""
    redis = get_redis()
    result = {}

    # 1. Önce Redis bulk cache'den dene
    raw = await redis.get("futuragents:news_latest")
    if raw:
        return json.loads(raw)

    # 2. Yoksa her coin için ayrı cache'den dene
    for sym in COINS:
        try:
            coin_raw = await redis.get(f"news:{sym}")
            if coin_raw:
                result[sym] = json.loads(coin_raw)
        except Exception:
            pass

    if result:
        return result

    # 3. Yoksa DB'den çek
    db = get_db()
    cursor = db.news_analysis.find({}).sort("created_at", -1).limit(10)
    async for doc in cursor:
        sym = doc.get("symbol")
        if sym and sym not in result:
            doc.pop("_id", None)
            result[sym] = doc

    return result


@router.get("/history")
async def get_news_history(limit: int = 50):
    """Son haberleri DB'den döner"""
    db = get_db()
    result = []
    cursor = db.news_analysis.find({}).sort("created_at", -1).limit(limit)
    async for doc in cursor:
        doc["_id"] = str(doc.get("_id", ""))
        result.append(doc)
    return result
