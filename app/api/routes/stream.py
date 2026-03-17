"""
FuturAgents — SSE Stream Routes
Dashboard'un canlı veri akışı için.
/api/stream/dashboard   → tüm coinlerin fiyat + sinyal + anomali verileri
/api/stream/news        → haber akışı
/api/stream/status      → sistem durumu
"""
import asyncio
import json
import logging
from datetime import datetime

from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from app.services.binance.client import get_binance_client
from app.db.database import get_redis, get_db

logger = logging.getLogger(__name__)
router = APIRouter()

COINS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT"]


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n"


@router.get("/dashboard")
async def stream_dashboard():
    """
    Her 10 saniyede 5 coinın tüm verilerini yayınlar.
    Dashboard bu stream'i dinler — polling yok.
    """
    async def generate():
        binance = get_binance_client()
        redis   = get_redis()
        while True:
            try:
                # 5 coin fiyat verileri paralel
                prices = await asyncio.gather(
                    *[binance.get_24h_ticker(c) for c in COINS],
                    return_exceptions=True
                )
                coin_data = {}
                for i, coin in enumerate(COINS):
                    t = prices[i] if not isinstance(prices[i], Exception) else {}
                    # Son sinyal
                    sig_raw = await redis.get(f"signal:{coin}:latest")
                    sig = json.loads(sig_raw) if sig_raw else None
                    coin_data[coin] = {
                        "price": float(t.get("lastPrice", 0)),
                        "change": float(t.get("priceChangePercent", 0)),
                        "volume": float(t.get("quoteVolume", 0)),
                        "high": float(t.get("highPrice", 0)),
                        "low": float(t.get("lowPrice", 0)),
                        "signal": sig,
                    }
                yield _sse("prices", {"coins": coin_data, "ts": datetime.utcnow().isoformat()})

                # Son tarama zamanı + bir sonraki tarama
                scan_raw = await redis.get("futuragents:last_scan")
                if scan_raw:
                    scan_data = json.loads(scan_raw)
                    # Bir sonraki tarama zamanını ekle (20dk = 1200s)
                    if scan_data.get("time"):
                        from datetime import timezone
                        last_dt = datetime.fromisoformat(scan_data["time"].replace("Z",""))
                        next_dt = last_dt + timedelta(seconds=1200)
                        scan_data["next_scan"] = next_dt.isoformat()
                    yield _sse("scan_status", scan_data)

                # Kritik anomaliler
                anom_raw = await redis.get("futuragents:critical_anomalies")
                if anom_raw:
                    yield _sse("critical_anomaly", {"anomalies": json.loads(anom_raw)})

                await asyncio.sleep(10)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"[Stream] dashboard hatası: {e}")
                await asyncio.sleep(5)

    return StreamingResponse(generate(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no",
                                      "Access-Control-Allow-Origin": "*"})


@router.get("/news")
async def stream_news():
    """
    Haber akışı — her 2 dakikada bir yeni haberler varsa push'lar.
    """
    async def generate():
        redis = get_redis()
        last_hash = None
        while True:
            try:
                news_raw = await redis.get("futuragents:news_latest")
                if news_raw:
                    import hashlib
                    h = hashlib.md5(news_raw.encode()).hexdigest()
                    if h != last_hash:
                        last_hash = h
                        yield _sse("news_update", json.loads(news_raw))

                # Son olaylar DB'den
                db = get_db()
                events = await db.news_analysis.find(
                    {}, {"symbol": 1, "overall_sentiment": 1, "key_events": 1,
                         "analyzed_at": 1, "headlines_count": 1}
                ).sort("created_at", -1).limit(5).to_list(5)
                if events:
                    for e in events:
                        e["_id"] = str(e.get("_id", ""))
                    yield _sse("news_history", {"items": events})

                await asyncio.sleep(30)  # 30 saniye — haber yoksa bile kontrol

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"[Stream] news hatası: {e}")
                await asyncio.sleep(30)

    return StreamingResponse(generate(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no",
                                      "Access-Control-Allow-Origin": "*"})


@router.get("/status")
async def stream_status():
    """Sistem durumu stream — her 30 saniyede"""
    async def generate():
        redis = get_redis()
        while True:
            try:
                scan_raw = await redis.get("futuragents:last_scan")
                scan = json.loads(scan_raw) if scan_raw else {}
                anom_raw = await redis.get("futuragents:critical_anomalies")
                # Scheduler next run
                next_run = None
                try:
                    from app.main import _scheduler
                    if _scheduler and _scheduler.running:
                        job = _scheduler.get_job("auto_scan")
                        if job and job.next_run_time:
                            next_run = job.next_run_time.isoformat()
                except Exception:
                    pass
                yield _sse("status", {
                    "last_scan": scan.get("time"),
                    "last_scan_stats": scan,
                    "critical_anomalies": len(json.loads(anom_raw)) if anom_raw else 0,
                    "next_scan": next_run,
                    "ts": datetime.utcnow().isoformat(),
                })
                await asyncio.sleep(30)
            except asyncio.CancelledError:
                break
            except Exception as e:
                await asyncio.sleep(30)

    return StreamingResponse(generate(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.get("/klines/{symbol}")
async def get_klines(symbol: str, interval: str = "1h", limit: int = 100):
    """Grafik verisi — lightweight-charts için"""
    binance = get_binance_client()
    klines  = await binance.get_klines(symbol, interval, limit=limit)
    # lightweight-charts formatı
    chart_data = []
    for k in klines:
        chart_data.append({
            "time": int(k["open_time"]) // 1000,
            "open": float(k["open"]), "high": float(k["high"]),
            "low": float(k["low"]),   "close": float(k["close"]),
            "volume": float(k["volume"]),
        })
    return chart_data
