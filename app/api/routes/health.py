"""Health check endpoints"""
from fastapi import APIRouter
from app.db.database import get_db, get_redis
from app.core.config import settings

router = APIRouter()


@router.get("/health")
async def health():
    checks = {"status": "ok", "version": settings.APP_VERSION, "testnet": settings.BINANCE_TESTNET}
    try:
        await get_db().command("ping")
        checks["mongodb"] = "ok"
    except Exception as e:
        checks["mongodb"] = f"error: {e}"
        checks["status"] = "degraded"
    try:
        await get_redis().ping()
        checks["redis"] = "ok"
    except Exception as e:
        checks["redis"] = f"error: {e}"
        checks["status"] = "degraded"
    return checks
