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


@router.get("/scheduler")
async def scheduler_status():
    """Scheduler job durumlarını döner"""
    try:
        from app.main import _scheduler
        if not _scheduler or not _scheduler.running:
            return {"status": "stopped", "jobs": []}
        jobs = []
        for job in _scheduler.get_jobs():
            jobs.append({
                "id": job.id,
                "name": job.name,
                "next_run": job.next_run_time.isoformat() if job.next_run_time else None,
            })
        return {"status": "running", "jobs": jobs}
    except Exception as e:
        return {"status": "error", "error": str(e)}
