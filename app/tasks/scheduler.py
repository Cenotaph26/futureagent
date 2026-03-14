"""
FuturAgents — Zamanlanmış Görevler
APScheduler ile her saat otomatik piyasa taraması.
Güçlü sinyal üretilirse DB'ye kaydeder.
"""
import asyncio
import logging
from datetime import datetime

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from app.core.config import settings
from app.services.agents.orchestrator import OrchestratorAgent
from app.db.database import get_db, get_redis

logger = logging.getLogger(__name__)

# Otomatik taranacak semboller
WATCHLIST = [
    "BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT",
    "ADAUSDT", "AVAXUSDT", "DOTUSDT", "LINKUSDT", "NEARUSDT",
]

# Minimum güven skoru — bu eşiğin altındaki sinyaller bildirilmez
MIN_CONFIDENCE_THRESHOLD = 65


async def scan_market() -> None:
    """
    Tüm watchlist sembollerini tara.
    Her 1 saatte bir çalışır.
    """
    logger.info(f"⏰ Otomatik piyasa taraması başladı — {len(WATCHLIST)} sembol")
    orchestrator = OrchestratorAgent()

    strong_signals = []

    for symbol in WATCHLIST:
        try:
            report = await orchestrator.analyze_and_decide(
                symbol=symbol,
                interval="1h",
                auto_execute=False,  # Zamanlı görevde otomatik işlem yok
            )
            decision = report.get("final_decision", {})
            confidence = decision.get("confidence", 0)

            if (
                decision.get("decision") == "EXECUTE"
                and confidence >= MIN_CONFIDENCE_THRESHOLD
            ):
                strong_signals.append({
                    "symbol": symbol,
                    "direction": decision.get("direction"),
                    "confidence": confidence,
                    "entry": decision.get("entry_price"),
                    "stop_loss": decision.get("stop_loss"),
                    "tp1": decision.get("take_profit_1"),
                })
                logger.info(f"  🎯 GÜÇLÜ SİNYAL: {symbol} {decision.get('direction')} ({confidence}/100)")
            else:
                logger.debug(f"  — {symbol}: {decision.get('decision')} ({confidence}/100)")

            # Rate limit önlemi
            await asyncio.sleep(2)

        except Exception as e:
            logger.error(f"  ❌ {symbol} tarama hatası: {e}")

    # Güçlü sinyalleri Redis'e yaz (son 2 saat için)
    if strong_signals:
        redis = get_redis()
        import json
        await redis.setex(
            "futuragents:latest_signals",
            7200,  # 2 saat TTL
            json.dumps(strong_signals, default=str),
        )
        logger.info(f"✅ {len(strong_signals)} güçlü sinyal Redis'e kaydedildi")

    logger.info("⏰ Piyasa taraması tamamlandı")


async def cleanup_old_analyses() -> None:
    """30 günden eski analizleri temizle"""
    try:
        from datetime import timedelta
        db = get_db()
        cutoff = datetime.utcnow() - timedelta(days=30)
        result = await db.analyses.delete_many({"created_at": {"$lt": cutoff}})
        if result.deleted_count > 0:
            logger.info(f"🧹 {result.deleted_count} eski analiz silindi")
    except Exception as e:
        logger.error(f"Temizleme hatası: {e}")


def create_scheduler() -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler(timezone="UTC")

    # Her saat başı piyasa tarama
    scheduler.add_job(
        scan_market,
        CronTrigger(minute=0),  # Her saatin :00'ında
        id="market_scan",
        name="Saatlik Piyasa Taraması",
        replace_existing=True,
        max_instances=1,
        misfire_grace_time=300,
    )

    # Her gece 02:00 UTC'de eski veri temizliği
    scheduler.add_job(
        cleanup_old_analyses,
        CronTrigger(hour=2, minute=0),
        id="cleanup",
        name="Eski Veri Temizliği",
        replace_existing=True,
    )

    return scheduler
