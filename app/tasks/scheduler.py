"""
FuturAgents — Akıllı Zamanlayıcı v2
5 Coin · $30/ay bütçe · Maksimum performans

ÇALIŞMA TAKVİMİ:
  Her 5 dakika  : Trailing stop kontrolü           ← LLM yok, $0
  Her 15 dakika : Anomali taraması (5 coin)         ← LLM yok, $0
  Her 1 saat    : Tam analiz (3TF + hafıza + haber)← LLM var, ~$0.06/tur
  Her 2 saat    : Haber analizi güncelleme          ← Haiku, ~$0.005/tur
  Her 4 saat    : Sinyal performans takibi          ← LLM yok, $0
  Günde 1 kez   : Öğrenme özeti                    ← Haiku, ~$0.002/gün
  Gece 02:00    : Temizlik                          ← LLM yok, $0

AYLIK MALİYET:
  Saatlik analiz: 5 coin × 24 saat × 30 gün × $0.012 = ~$43
  Ön filtre ile: %55 eleme → ~$24
  Haber analizi: 5 coin × 12/gün × 30 gün × $0.001 = ~$1.8
  Öğrenme özeti: 30 gün × $0.002 = ~$0.06
  TOPLAM        : ~$26/ay ✓
"""
import asyncio
import json
import logging
from datetime import datetime, timedelta

import pandas as pd

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from app.services.binance.client import get_binance_client
from app.services.agents.trailing_stop_agent import TrailingStopAgent
from app.services.anomaly.detector import AnomalyDetector
from app.services.news.news_agent import NewsAgent
from app.services.memory.market_memory import MarketMemory
from app.db.database import get_db, get_redis

logger = logging.getLogger(__name__)

COINS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT"]
MIN_CONFIDENCE = 68
MIN_PREFILTER_SCORE = 45  # Kural tabanlı ön filtre eşiği


# ── Ön Filtre (LLM YOK) ──────────────────────────────────────────────────────

async def _quick_filter(symbol: str, interval: str = "1h") -> dict | None:
    try:
        binance = get_binance_client()
        klines  = await binance.get_klines(symbol, interval, limit=60)
        df = pd.DataFrame(klines)
        c = pd.to_numeric(df["close"])
        h = pd.to_numeric(df["high"])
        l = pd.to_numeric(df["low"])

        ema20 = float(c.ewm(span=20).mean().iloc[-1])
        ema50 = float(c.ewm(span=50).mean().iloc[-1])
        ema200= float(c.ewm(span=200).mean().iloc[-1])
        price = float(c.iloc[-1])

        delta = c.diff()
        rsi = float(100 - (100 / (1 + delta.where(delta>0,0).rolling(14).mean() /
                                    (-delta.where(delta<0,0)).rolling(14).mean() + 1e-10)).iloc[-1])

        ema12 = c.ewm(span=12).mean()
        ema26 = c.ewm(span=26).mean()
        macd_hist = float((ema12-ema26-(ema12-ema26).ewm(span=9).mean()).iloc[-1])

        tr = pd.concat([h-l,(h-c.shift()).abs(),(l-c.shift()).abs()],axis=1).max(axis=1)
        atr = float(tr.rolling(14).mean().iloc[-1])
        atr_pct = atr / price if price > 0 else 0

        long_signal  = (ema20>ema50 and 38<=rsi<=65 and macd_hist>0) or rsi<28
        short_signal = (ema20<ema50 and 35<=rsi<=62 and macd_hist<0) or rsi>72

        if not (long_signal or short_signal):
            return None

        direction = "LONG" if long_signal else "SHORT"
        score = 0
        if direction == "LONG":
            if ema20>ema50: score+=25
            if ema50>ema200: score+=15
            if 38<=rsi<=55: score+=20
            if macd_hist>0: score+=20
            if rsi<30: score+=20
        else:
            if ema20<ema50: score+=25
            if ema50<ema200: score+=15
            if 45<=rsi<=62: score+=20
            if macd_hist<0: score+=20
            if rsi>70: score+=20

        return {"symbol": symbol, "interval": interval, "direction": direction,
                "score": score, "rsi": round(rsi,1), "macd_hist": round(macd_hist,4),
                "ema_trend": "BULLISH" if ema20>ema50 else "BEARISH",
                "atr_pct": round(atr_pct*100,3), "price": price}
    except Exception as e:
        logger.debug(f"Ön filtre {symbol}/{interval}: {e}")
        return None


async def _multi_tf_filter(symbol: str) -> dict | None:
    """3TF konsensüs ön filtresi — LLM yok"""
    results = await asyncio.gather(
        _quick_filter(symbol, "15m"),
        _quick_filter(symbol, "1h"),
        _quick_filter(symbol, "4h"),
        return_exceptions=True,
    )
    valid = [r for r in results if isinstance(r, dict)]
    if not valid: return None
    long_c  = sum(1 for r in valid if r["direction"]=="LONG")
    short_c = sum(1 for r in valid if r["direction"]=="SHORT")
    if long_c >= 2:  dominant = "LONG"
    elif short_c >= 2: dominant = "SHORT"
    else: return None
    avg_score = sum(r["score"] for r in valid if r["direction"]==dominant) / len(valid)
    if avg_score < MIN_PREFILTER_SCORE: return None
    return {"symbol": symbol, "dominant": dominant, "score": round(avg_score,1),
            "tf_count": len(valid), "long": long_c, "short": short_c}


# ── Ana Saatlik Analiz ────────────────────────────────────────────────────────

async def hourly_smart_scan() -> None:
    """
    Saatlik akıllı tarama — 5 coin için tam analiz.
    Ön filtre geçmeyenler LLM görmez → maliyet düşer.
    """
    from app.services.agents.orchestrator import OrchestratorAgent
    orchestrator = OrchestratorAgent()
    redis = get_redis()
    strong_signals = []
    stats = {"total": len(COINS), "filtered_out": 0, "analyzed": 0, "signals": 0}

    logger.info(f"⏰ Saatlik tarama başladı ({len(COINS)} coin)")

    for symbol in COINS:
        try:
            # Adım 1: Kural bazlı 3TF ön filtre (LLM yok)
            consensus = await _multi_tf_filter(symbol)
            if not consensus:
                stats["filtered_out"] += 1
                logger.debug(f"  ⏭ {symbol}: ön filtre geçmedi")
                await asyncio.sleep(0.3)
                continue

            stats["analyzed"] += 1
            logger.info(f"  🔍 {symbol}: {consensus['dominant']} konsensüs → tam analiz")

            # Adım 2: Tam LLM analizi (3TF + hafıza + anomali + haber)
            report = await orchestrator.analyze_and_decide(
                symbol=symbol, interval="1h", auto_execute=False)

            decision = report.get("final_decision", {})
            conf     = decision.get("confidence", 0)

            if decision.get("decision") == "EXECUTE" and conf >= MIN_CONFIDENCE:
                stats["signals"] += 1
                sig = {
                    "symbol": symbol,
                    "direction": decision.get("direction"),
                    "confidence": conf,
                    "entry": decision.get("entry_price"),
                    "stop_loss": decision.get("stop_loss"),
                    "take_profit_1": decision.get("take_profit_1"),
                    "leverage": decision.get("leverage"),
                    "memory_influenced": decision.get("memory_influenced", False),
                    "anomaly_influenced": decision.get("anomaly_influenced", False),
                    "timestamp": datetime.utcnow().isoformat(),
                }
                strong_signals.append(sig)
                await redis.setex(f"signal:{symbol}:latest", 21600,
                                  json.dumps(sig, default=str))
                logger.info(f"  🎯 EXECUTE: {symbol} {decision.get('direction')} ({conf}/100)")

            await asyncio.sleep(2)

        except Exception as e:
            logger.error(f"  ❌ {symbol}: {e}")

    if strong_signals:
        await redis.setex("futuragents:latest_signals", 7200,
                          json.dumps(strong_signals, default=str))

    logger.info(f"⏰ Tarama bitti | Analiz: {stats['analyzed']}/{stats['total']} | "
                f"Elenen: {stats['filtered_out']} | Sinyal: {stats['signals']}")


# ── Anomali Taraması (15dk, LLM yok) ─────────────────────────────────────────

async def scan_anomalies() -> None:
    try:
        detector = AnomalyDetector()
        anomalies = await detector.scan_all()
        if anomalies:
            critical = [a for a in anomalies if a.get("severity") == "critical"]
            if critical:
                logger.warning(f"🚨 {len(critical)} KRİTİK ANOMALİ: {[a['message'] for a in critical]}")
            else:
                logger.info(f"[Anomaly] {len(anomalies)} anomali tespit edildi")
    except Exception as e:
        logger.error(f"[Anomaly] Hata: {e}")


# ── Haber Analizi (2 saatte bir, Haiku) ──────────────────────────────────────

async def refresh_news() -> None:
    try:
        agent = NewsAgent()
        results = await agent.analyze_all_coins()
        sentiments = {s: r.get("overall_sentiment", "?") for s, r in results.items()}
        logger.info(f"[News] Güncellendi: {sentiments}")
    except Exception as e:
        logger.error(f"[News] Hata: {e}")


# ── Trailing Stop (5dk, LLM yok) ─────────────────────────────────────────────

async def check_trailing_stops() -> None:
    try:
        result = await TrailingStopAgent().run()
        if result["actions"]:
            logger.info(f"[Trail] {result['checked']} pos → {len(result['actions'])} aksiyon")
    except Exception as e:
        logger.error(f"[Trail] Hata: {e}")


# ── Sinyal Performans Takibi (4 saatte bir, LLM yok) ─────────────────────────

async def track_performance() -> None:
    try:
        db = get_db()
        binance = get_binance_client()
        cutoff = datetime.utcnow() - timedelta(hours=24)
        docs = await db.analyses.find({
            "created_at": {"$gte": cutoff},
            "final_decision.decision": "EXECUTE",
            "performance": {"$exists": False},
        }).to_list(20)
        for doc in docs:
            try:
                sym   = doc.get("symbol")
                entry = doc.get("final_decision", {}).get("entry_price", 0)
                direc = doc.get("final_decision", {}).get("direction")
                if not (sym and entry and direc): continue
                current = await binance.get_price(sym)
                pnl = (current - entry) / entry * 100
                if direc == "SHORT": pnl = -pnl
                await db.analyses.update_one({"_id": doc["_id"]}, {"$set": {
                    "performance": {"current": current, "pnl_pct": round(pnl, 2),
                                    "checked_at": datetime.utcnow()}}})
            except Exception:
                pass
        logger.debug(f"[Perf] {len(docs)} sinyal takip edildi")
    except Exception as e:
        logger.error(f"[Perf] Hata: {e}")


# ── Günlük Öğrenme Özeti (Haiku, ~$0.002/gün) ────────────────────────────────

async def daily_learning_summary() -> None:
    """
    Gün içindeki tüm sinyallerin sonuçlarını özetle.
    Haiku ile hangi koşulların iyi/kötü çalıştığını analiz et.
    """
    try:
        from app.services.llm.service import get_llm_service
        db  = get_db()
        llm = get_llm_service()
        cutoff = datetime.utcnow() - timedelta(hours=24)
        outcomes = await db.signal_outcomes.find(
            {"created_at": {"$gte": cutoff}}
        ).to_list(100)
        if not outcomes:
            return

        wins   = [o for o in outcomes if o.get("won")]
        losses = [o for o in outcomes if not o.get("won")]

        summary_prompt = f"""
Bugünkü trading özeti:
- Toplam sinyal: {len(outcomes)}
- Kazanılan: {len(wins)} (%{len(wins)/len(outcomes)*100:.0f})
- Kaybedilen: {len(losses)}
- Ortalama PnL: %{sum(o['pnl_pct'] for o in outcomes)/len(outcomes):.2f}

Kazanan sinyallerin ortak özellikleri:
{[{'symbol': o['symbol'], 'ema': o.get('indicators',{}).get('ema_trend'), 'rsi': o.get('indicators',{}).get('rsi_14')} for o in wins[:5]]}

Kaybeden sinyallerin ortak özellikleri:
{[{'symbol': o['symbol'], 'ema': o.get('indicators',{}).get('ema_trend'), 'rsi': o.get('indicators',{}).get('rsi_14')} for o in losses[:5]]}

JSON yanıt:
{{
  "win_patterns": ["başarılı örüntü 1", "2"],
  "loss_patterns": ["başarısız örüntü 1", "2"],
  "recommendations": ["öneri 1", "2"],
  "avoid_tomorrow": ["yarın kaçınılacak durum"]
}}"""

        insight = await llm.complete_json(
            system="Sen bir trading performans analisti uzmanısın.",
            user=summary_prompt,
            model_tier="fast",  # Haiku yeterli
            max_tokens=512,
        )
        await db.learning_insights.insert_one({
            **insight,
            "date": datetime.utcnow().date().isoformat(),
            "total_signals": len(outcomes),
            "win_rate": len(wins)/len(outcomes) if outcomes else 0,
            "created_at": datetime.utcnow(),
        })
        logger.info(f"[Learning] Günlük özet kaydedildi: {insight.get('recommendations', [])[:2]}")
    except Exception as e:
        logger.error(f"[Learning] Hata: {e}")


# ── Temizlik ──────────────────────────────────────────────────────────────────

async def cleanup() -> None:
    try:
        db = get_db()
        r1 = await db.analyses.delete_many(
            {"created_at": {"$lt": datetime.utcnow() - timedelta(days=30)}})
        r2 = await db.alerts.delete_many({
            "created_at": {"$lt": datetime.utcnow() - timedelta(days=7)}, "read": True})
        r3 = await db.anomalies.delete_many(
            {"created_at": {"$lt": datetime.utcnow() - timedelta(days=7)}})
        logger.info(f"🧹 {r1.deleted_count} analiz, {r2.deleted_count} alarm, {r3.deleted_count} anomali silindi")
    except Exception as e:
        logger.error(f"[Cleanup] {e}")


# ── Scheduler ─────────────────────────────────────────────────────────────────

def create_scheduler() -> AsyncIOScheduler:
    s = AsyncIOScheduler(timezone="UTC")

    # Trailing stop — her 5dk (LLM yok)
    s.add_job(check_trailing_stops, IntervalTrigger(minutes=5),
              id="trailing", name="Trailing Stop", replace_existing=True, max_instances=1)

    # Anomali taraması — her 15dk (LLM yok)
    s.add_job(scan_anomalies, IntervalTrigger(minutes=15),
              id="anomaly", name="Anomali Taraması", replace_existing=True, max_instances=1)

    # Tam analiz — saatlik
    s.add_job(hourly_smart_scan, CronTrigger(minute=0),
              id="hourly_scan", name="Saatlik Akıllı Tarama",
              replace_existing=True, max_instances=1, misfire_grace_time=300)

    # Haber güncelleme — 2 saatte bir
    s.add_job(refresh_news, IntervalTrigger(hours=2),
              id="news", name="Haber Analizi", replace_existing=True, max_instances=1)

    # Performans takibi — 4 saatte bir (LLM yok)
    s.add_job(track_performance, IntervalTrigger(hours=4),
              id="perf", name="Performans Takibi", replace_existing=True)

    # Günlük öğrenme özeti — 23:00 UTC
    s.add_job(daily_learning_summary, CronTrigger(hour=23, minute=0),
              id="learning", name="Günlük Öğrenme", replace_existing=True)

    # Temizlik — gece 02:00
    s.add_job(cleanup, CronTrigger(hour=2, minute=0),
              id="cleanup", name="Temizlik", replace_existing=True)

    return s
