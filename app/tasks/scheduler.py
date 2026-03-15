"""
FuturAgents — 7/24 Otomatik Ajan Sistemi
Kullanıcı butona basmadan çalışır.

ÇALIŞMA TAKVİMİ:
  Her 5dk  : Trailing stop + pozisyon PnL takibi (LLM yok)
  Her 15dk : Anomali taraması 5 coin (LLM yok)
  Her 30dk : 5 coin tam analiz — 3TF + hafıza + anomali + haber (LLM)
  Her 1s   : Haber çekme + sentiment güncelleme (Haiku)
  Her 4s   : Sinyal performans takibi (LLM yok)
  23:00 UTC: Günlük öğrenme özeti (Haiku)
  02:00 UTC: Temizlik (LLM yok)
"""
import asyncio
import json
import logging
from datetime import datetime, timedelta

import pandas as pd

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from app.core.config import settings
from app.services.binance.client import get_binance_client
from app.services.agents.trailing_stop_agent import TrailingStopAgent
from app.services.anomaly.detector import AnomalyDetector
from app.services.news.news_agent import NewsAgent
from app.services.memory.market_memory import MarketMemory
from app.db.database import get_db, get_redis

logger = logging.getLogger(__name__)

COINS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT"]
MIN_CONFIDENCE = 68
MIN_PREFILTER_SCORE = 42


# ── Kural Bazlı Ön Filtre (LLM yok) ──────────────────────────────────────────

async def _quick_filter(symbol: str, interval: str = "1h") -> dict | None:
    try:
        binance = get_binance_client()
        klines  = await binance.get_klines(symbol, interval, limit=60)
        df = pd.DataFrame(klines)
        c  = pd.to_numeric(df["close"])
        h  = pd.to_numeric(df["high"])
        l  = pd.to_numeric(df["low"])
        ema20  = float(c.ewm(span=20).mean().iloc[-1])
        ema50  = float(c.ewm(span=50).mean().iloc[-1])
        ema200 = float(c.ewm(span=200).mean().iloc[-1])
        price  = float(c.iloc[-1])
        delta  = c.diff()
        rsi    = float(100 - (100 / (1 + delta.where(delta>0,0).rolling(14).mean() /
                               ((-delta.where(delta<0,0)).rolling(14).mean() + 1e-10))).iloc[-1])
        ema12  = c.ewm(span=12).mean()
        ema26  = c.ewm(span=26).mean()
        macd_h = float((ema12-ema26-(ema12-ema26).ewm(span=9).mean()).iloc[-1])
        tr     = pd.concat([h-l,(h-c.shift()).abs(),(l-c.shift()).abs()],axis=1).max(axis=1)
        atr_pct= float(tr.rolling(14).mean().iloc[-1]) / price if price > 0 else 0

        long_sig  = (ema20>ema50 and 38<=rsi<=65 and macd_h>0) or rsi<28
        short_sig = (ema20<ema50 and 35<=rsi<=62 and macd_h<0) or rsi>72
        if not (long_sig or short_sig):
            return None
        direction = "LONG" if long_sig else "SHORT"
        score = 0
        if direction == "LONG":
            if ema20>ema50: score+=25
            if ema50>ema200: score+=15
            if 38<=rsi<=55: score+=20
            if macd_h>0: score+=20
            if rsi<30: score+=20
        else:
            if ema20<ema50: score+=25
            if ema50<ema200: score+=15
            if 45<=rsi<=62: score+=20
            if macd_h<0: score+=20
            if rsi>70: score+=20
        return {"symbol":symbol,"interval":interval,"direction":direction,"score":score,
                "rsi":round(rsi,1),"ema_trend":"BULLISH" if ema20>ema50 else "BEARISH","atr_pct":round(atr_pct*100,3),"price":price}
    except Exception as e:
        logger.debug(f"Filtre {symbol}/{interval}: {e}")
        return None


async def _multi_tf_filter(symbol: str) -> dict | None:
    results = await asyncio.gather(
        _quick_filter(symbol,"15m"),_quick_filter(symbol,"1h"),_quick_filter(symbol,"4h"),
        return_exceptions=True)
    valid = [r for r in results if isinstance(r,dict)]
    if not valid: return None
    long_c  = sum(1 for r in valid if r["direction"]=="LONG")
    short_c = sum(1 for r in valid if r["direction"]=="SHORT")
    if long_c>=2: dominant="LONG"
    elif short_c>=2: dominant="SHORT"
    else: return None
    avg_score = sum(r["score"] for r in valid if r["direction"]==dominant)/len(valid)
    if avg_score < MIN_PREFILTER_SCORE: return None
    return {"symbol":symbol,"dominant":dominant,"score":round(avg_score,1),"tf_count":len(valid)}


# ── 7/24 Otomatik Analiz (30dk'da bir) ───────────────────────────────────────

async def auto_scan_and_trade() -> None:
    """
    7/24 ÇALIŞAN ANA AJAN.
    Kullanıcı aksiyonu gerektirmez.
    Güçlü sinyal → pozisyon aç (auto_execute=True).
    """
    from app.services.agents.orchestrator import OrchestratorAgent
    orchestrator = OrchestratorAgent()
    redis = get_redis()
    signals_found = []
    stats = {"analyzed":0,"filtered":0,"signals":0,"positions_opened":0}

    logger.info(f"🤖 Otomatik tarama: {len(COINS)} coin, {datetime.utcnow().strftime('%H:%M UTC')}")

    for symbol in COINS:
        try:
            # 1. Kural bazlı ön filtre
            consensus = await _multi_tf_filter(symbol)
            if not consensus:
                stats["filtered"] += 1
                logger.debug(f"  ⏭ {symbol}: filtre geçmedi")
                await asyncio.sleep(0.5)
                continue

            stats["analyzed"] += 1
            logger.info(f"  🔍 {symbol}: {consensus['dominant']} → tam analiz")

            # 2. Tam LLM analizi — AUTO EXECUTE = settings'den
            auto_exec = getattr(settings, "AUTO_EXECUTE_ENABLED", False)
            logger.info(f"  {symbol}: auto_exec={auto_exec}")
            report = await orchestrator.analyze_and_decide(
                symbol=symbol, interval="1h", auto_execute=auto_exec)

            decision = report.get("final_decision", {})
            dec       = decision.get("decision")
            conf      = decision.get("confidence", 0)
            direction = decision.get("direction")

            if dec == "EXECUTE" and conf >= MIN_CONFIDENCE:
                stats["signals"] += 1
                sig = {
                    "symbol": symbol, "direction": direction, "confidence": conf,
                    "entry": decision.get("entry_price"), "stop_loss": decision.get("stop_loss"),
                    "take_profit_1": decision.get("take_profit_1"),
                    "leverage": decision.get("leverage"),
                    "timestamp": datetime.utcnow().isoformat(),
                    "auto_executed": report.get("auto_executed", False),
                }
                signals_found.append(sig)
                await redis.setex(f"signal:{symbol}:latest", 21600, json.dumps(sig, default=str))
                if auto_exec and report.get("auto_executed"):
                    stats["positions_opened"] += 1
                    logger.info(f"  📈 POZİSYON AÇILDI: {symbol} {direction} ({conf}/100)")
                else:
                    logger.info(f"  🎯 SİNYAL: {symbol} {direction} ({conf}/100)")

            await asyncio.sleep(2)

        except Exception as e:
            logger.error(f"  ❌ {symbol}: {e}")

    # Güncel sinyal listesini Redis'e yaz
    if signals_found:
        await redis.setex("futuragents:active_signals", 3600,
                          json.dumps(signals_found, default=str))
    await redis.setex("futuragents:last_scan", 3600, json.dumps({
        "time": datetime.utcnow().isoformat(), **stats}, default=str))
    logger.info(f"🤖 Tarama bitti | Analiz:{stats['analyzed']} Filtre:{stats['filtered']} "
                f"Sinyal:{stats['signals']} Açılan:{stats['positions_opened']}")


# ── Anomali Taraması (15dk, LLM yok) ─────────────────────────────────────────

async def scan_anomalies() -> None:
    try:
        anomalies = await AnomalyDetector().scan_all()
        critical = [a for a in anomalies if a.get("severity") == "critical"]
        if critical:
            redis = get_redis()
            await redis.setex("futuragents:critical_anomalies", 900,
                              json.dumps(critical, default=str))
            logger.warning(f"🚨 {len(critical)} KRİTİK ANOMALİ: {[a.get('message','') for a in critical]}")
        elif anomalies:
            logger.info(f"[Anomaly] {len(anomalies)} anomali tespit edildi")
    except Exception as e:
        logger.error(f"[Anomaly] {e}")


# ── Haber Çekme (saatlik, Haiku) ─────────────────────────────────────────────

async def refresh_news() -> None:
    try:
        agent = NewsAgent()
        # 5dk polling — sadece yeni haberler
        new_items = await agent.poll_and_analyze()
        if new_items:
            sentiments = {r["symbol"]: r.get("overall_sentiment","?") for r in new_items}
            logger.info(f"[News] {len(new_items)} yeni haber: {sentiments}")
        else:
            logger.debug("[News] Yeni haber yok")
    except Exception as e:
        logger.error(f"[News] {e}")


# ── Trailing Stop (5dk, LLM yok) ─────────────────────────────────────────────

async def check_trailing_stops() -> None:
    try:
        result = await TrailingStopAgent().run()
        if result.get("actions"):
            logger.info(f"[Trail] {result['checked']} pos → {len(result['actions'])} aksiyon")
    except Exception as e:
        logger.error(f"[Trail] {e}")


# ── Sinyal Performans Takibi (4s, LLM yok) ───────────────────────────────────

async def track_performance() -> None:
    try:
        db = get_db(); binance = get_binance_client()
        cutoff = datetime.utcnow() - timedelta(hours=24)
        docs = await db.analyses.find({
            "created_at": {"$gte": cutoff},
            "final_decision.decision": "EXECUTE",
            "performance": {"$exists": False},
        }).to_list(20)
        for doc in docs:
            try:
                sym = doc.get("symbol"); entry = doc.get("final_decision",{}).get("entry_price",0)
                direc = doc.get("final_decision",{}).get("direction")
                if not (sym and entry and direc): continue
                current = await binance.get_price(sym)
                pnl = (current-entry)/entry*100
                if direc=="SHORT": pnl=-pnl
                await db.analyses.update_one({"_id":doc["_id"]},
                    {"$set":{"performance":{"current":current,"pnl_pct":round(pnl,2),"checked_at":datetime.utcnow()}}})
            except Exception: pass
    except Exception as e:
        logger.error(f"[Perf] {e}")


# ── Günlük Öğrenme (23:00 UTC, Haiku) ────────────────────────────────────────

async def daily_learning() -> None:
    try:
        from app.services.llm.service import get_llm_service
        db = get_db(); llm = get_llm_service()
        cutoff = datetime.utcnow() - timedelta(hours=24)
        outcomes = await db.signal_outcomes.find({"created_at":{"$gte":cutoff}}).to_list(100)
        if not outcomes: return
        wins = [o for o in outcomes if o.get("won")]
        losses = [o for o in outcomes if not o.get("won")]
        prompt = f"""Bugünkü trading özeti:
Toplam: {len(outcomes)}, Kazanan: {len(wins)} (%{len(wins)/len(outcomes)*100:.0f}), Kaybeden: {len(losses)}
Ortalama PnL: %{sum(o['pnl_pct'] for o in outcomes)/len(outcomes):.2f}

JSON yanıt:
{{"win_patterns":["..."],"loss_patterns":["..."],"recommendations":["..."],"avoid_tomorrow":["..."]}}"""
        insight = await llm.complete_json(
            system="Sen bir trading performans analisti uzmanısın.",
            user=prompt, model_tier="fast", max_tokens=512)
        await db.learning_insights.insert_one({
            **insight, "date": datetime.utcnow().date().isoformat(),
            "total": len(outcomes), "win_rate": len(wins)/len(outcomes) if outcomes else 0,
            "created_at": datetime.utcnow()})
        logger.info(f"[Learning] Günlük özet: {insight.get('recommendations',[][:2])}")
    except Exception as e:
        logger.error(f"[Learning] {e}")


# ── Temizlik ──────────────────────────────────────────────────────────────────

async def cleanup() -> None:
    try:
        db = get_db()
        r1 = await db.analyses.delete_many({"created_at":{"$lt":datetime.utcnow()-timedelta(days=30)}})
        r2 = await db.alerts.delete_many({"created_at":{"$lt":datetime.utcnow()-timedelta(days=7)},"read":True})
        r3 = await db.anomalies.delete_many({"created_at":{"$lt":datetime.utcnow()-timedelta(days=7)}})
        logger.info(f"🧹 {r1.deleted_count} analiz, {r2.deleted_count} alarm, {r3.deleted_count} anomali")
    except Exception as e:
        logger.error(f"[Cleanup] {e}")


# ── Scheduler ─────────────────────────────────────────────────────────────────

def create_scheduler() -> AsyncIOScheduler:
    s = AsyncIOScheduler(timezone="UTC")

    # 7/24 OTOMATİK ANALİZ + İŞLEM — 30 dakikada bir
    s.add_job(auto_scan_and_trade, IntervalTrigger(minutes=30),
              id="auto_scan", name="7/24 Otomatik Analiz+İşlem",
              replace_existing=True, max_instances=1, misfire_grace_time=300)

    # Trailing stop — her 5dk
    s.add_job(check_trailing_stops, IntervalTrigger(minutes=5),
              id="trailing", name="Trailing Stop", replace_existing=True, max_instances=1)

    # Anomali — her 15dk
    s.add_job(scan_anomalies, IntervalTrigger(minutes=15),
              id="anomaly", name="Anomali Taraması", replace_existing=True, max_instances=1)

    # Haber — her 5 dakika (sadece yeni haberler analiz edilir, maliyet düşük)
    s.add_job(refresh_news, IntervalTrigger(minutes=5),
              id="news", name="Anlık Haber Analizi", replace_existing=True, max_instances=1)

    # Performans — 4 saatte bir
    s.add_job(track_performance, IntervalTrigger(hours=4),
              id="perf", name="Performans Takibi", replace_existing=True)

    # Günlük öğrenme — 23:00 UTC
    s.add_job(daily_learning, CronTrigger(hour=23, minute=0),
              id="learning", name="Günlük Öğrenme", replace_existing=True)

    # Temizlik — 02:00 UTC
    s.add_job(cleanup, CronTrigger(hour=2, minute=0),
              id="cleanup", name="Temizlik", replace_existing=True)

    return s
