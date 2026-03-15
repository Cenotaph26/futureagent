"""
FuturAgents — FastAPI Ana Uygulama
"""
import logging, sys, os
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from app.core.config import settings
from app.db.database import connect_mongo, disconnect_mongo, connect_redis, disconnect_redis, ensure_indexes
from app.api.routes import analysis, positions, market, auth, health, signals, backtest, alerts
from app.tasks.scheduler import create_scheduler

logging.basicConfig(level=getattr(logging, settings.LOG_LEVEL),
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s", stream=sys.stdout)
logger = logging.getLogger(__name__)
_scheduler = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _scheduler
    import os as _os
    mongo_keys = {k: "***" for k in _os.environ if "MONGO" in k.upper()}
    redis_keys = {k: "***" for k in _os.environ if "REDIS" in k.upper()}
    logger.info(f"🔍 MONGO env: {list(mongo_keys.keys())}")
    logger.info(f"🔍 REDIS env: {list(redis_keys.keys())}")
    logger.info(f"🚀 {settings.APP_NAME} v{settings.APP_VERSION}")
    logger.info(f"   Mod: {'TESTNET' if settings.BINANCE_TESTNET else '🔴 MAINNET'}")
    logger.info(f"   LLM Orchestrator: {settings.ANTHROPIC_MODEL}")
    await connect_mongo()
    await connect_redis()
    await ensure_indexes()
    _scheduler = create_scheduler()
    _scheduler.start()
    logger.info("✅ Tüm servisler hazır — Trailing stop her 5dk aktif")
    yield
    logger.info("🛑 Kapatılıyor...")
    if _scheduler:
        _scheduler.shutdown(wait=False)
    await disconnect_mongo()
    await disconnect_redis()


app = FastAPI(title="FuturAgents API",
    description="AI Multi-Agent Binance Futures + Backtest + Trailing Stop",
    version=settings.APP_VERSION, docs_url="/docs", redoc_url="/redoc", lifespan=lifespan)

app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins_list,
    allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

app.include_router(health.router,    prefix="/api",            tags=["Health"])
app.include_router(auth.router,      prefix="/api/auth",       tags=["Auth"])
app.include_router(analysis.router,  prefix="/api/analysis",   tags=["Analysis"])
app.include_router(positions.router, prefix="/api/positions",  tags=["Positions"])
app.include_router(market.router,    prefix="/api/market",     tags=["Market"])
app.include_router(signals.router,   prefix="/api/signals",    tags=["Signals"])
app.include_router(backtest.router,  prefix="/api/backtest",   tags=["Backtest"])
app.include_router(alerts.router,    prefix="/api/alerts",     tags=["Alerts"])

static_dir = "frontend_static"
if os.path.isdir(static_dir) and os.listdir(static_dir):
    if os.path.isdir(f"{static_dir}/assets"):
        app.mount("/assets", StaticFiles(directory=f"{static_dir}/assets"), name="assets")
    @app.get("/{full_path:path}", include_in_schema=False)
    async def serve_spa(full_path: str):
        index = f"{static_dir}/index.html"
        return FileResponse(index) if os.path.isfile(index) else {"message": "/docs adresini kullan"}


def start():
    import uvicorn
    uvicorn.run("app.main:app", host="0.0.0.0", port=settings.PORT, workers=1)

if __name__ == "__main__":
    start()
