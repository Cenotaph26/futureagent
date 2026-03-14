"""
FuturAgents — FastAPI Ana Uygulama
"""
import logging
import sys
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from app.core.config import settings
from app.db.database import (
    connect_mongo, disconnect_mongo,
    connect_redis, disconnect_redis,
    ensure_indexes,
)
from app.api.routes import analysis, positions, market, auth, health, signals
from app.tasks.scheduler import create_scheduler

logging.basicConfig(
    level=getattr(logging, settings.LOG_LEVEL),
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger(__name__)
_scheduler = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _scheduler

    # ── Tüm env değişkenlerini logla (debug için) ─────────────────────
    mongo_vars = {k: v[:20]+"***" if len(v) > 20 else v
                  for k, v in os.environ.items()
                  if "MONGO" in k.upper()}
    redis_vars = {k: v[:10]+"***" if len(v) > 10 else v
                  for k, v in os.environ.items()
                  if "REDIS" in k.upper()}
    logger.info(f"🔍 MONGO env vars: {mongo_vars}")
    logger.info(f"🔍 REDIS env vars: {redis_vars}")
    logger.info(f"🚀 {settings.APP_NAME} v{settings.APP_VERSION} başlatılıyor...")
    logger.info(f"   Mod: {'⚠️  TESTNET' if settings.BINANCE_TESTNET else '🔴 MAINNET'}")

    await connect_mongo()
    await connect_redis()
    await ensure_indexes()

    _scheduler = create_scheduler()
    _scheduler.start()
    logger.info("✅ Tüm servisler hazır")
    yield

    logger.info("🛑 Servisler kapatılıyor...")
    if _scheduler:
        _scheduler.shutdown(wait=False)
    await disconnect_mongo()
    await disconnect_redis()


app = FastAPI(
    title="FuturAgents API",
    description="AI Multi-Agent Binance Futures Trading System",
    version=settings.APP_VERSION,
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router,     prefix="/api",           tags=["Health"])
app.include_router(auth.router,       prefix="/api/auth",      tags=["Auth"])
app.include_router(analysis.router,   prefix="/api/analysis",  tags=["Analysis"])
app.include_router(positions.router,  prefix="/api/positions", tags=["Positions"])
app.include_router(market.router,     prefix="/api/market",    tags=["Market"])
app.include_router(signals.router,    prefix="/api/signals",   tags=["Signals"])

static_dir = "frontend_static"
if os.path.isdir(static_dir) and os.listdir(static_dir):
    if os.path.isdir(f"{static_dir}/assets"):
        app.mount("/assets", StaticFiles(directory=f"{static_dir}/assets"), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    async def serve_spa(full_path: str):
        index = f"{static_dir}/index.html"
        if os.path.isfile(index):
            return FileResponse(index)
        return {"message": "Frontend henüz build edilmedi. /docs adresini kullan."}


def start():
    import uvicorn
    uvicorn.run("app.main:app", host="0.0.0.0", port=settings.PORT, workers=1)


if __name__ == "__main__":
    start()
