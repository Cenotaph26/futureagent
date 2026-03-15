"""
FuturAgents — Market Memory
Sistem zamanla öğrenir. LLM maliyeti: $0.
"""
import logging
from datetime import datetime, timedelta
from app.db.database import get_db

logger = logging.getLogger(__name__)


class MarketMemory:

    async def record_signal_outcome(self, symbol, direction, entry_price,
                                     exit_price, exit_reason, indicators, confidence, interval="1h"):
        pnl_pct = (exit_price - entry_price) / entry_price * 100
        if direction == "SHORT":
            pnl_pct = -pnl_pct
        won = pnl_pct > 0
        db = get_db()
        await db.signal_outcomes.insert_one({
            "symbol": symbol, "direction": direction, "interval": interval,
            "entry_price": entry_price, "exit_price": exit_price, "exit_reason": exit_reason,
            "pnl_pct": round(pnl_pct, 3), "won": won, "confidence": confidence,
            "indicators": indicators,
            "hour_utc": datetime.utcnow().hour, "day_of_week": datetime.utcnow().weekday(),
            "created_at": datetime.utcnow(),
        })
        await db.coin_stats.update_one({"symbol": symbol},
            {"$inc": {"total_signals": 1, "total_wins": 1 if won else 0, "total_pnl": pnl_pct},
             "$set": {"updated_at": datetime.utcnow()}}, upsert=True)
        logger.info(f"[Memory] {symbol} {direction}: {'WIN' if won else 'LOSS'} %{pnl_pct:.2f}")

    async def get_coin_intelligence(self, symbol: str) -> dict:
        """Bir coin için tüm öğrenilmiş bilgiyi döner"""
        db = get_db()
        cutoff = datetime.utcnow() - timedelta(days=30)
        outcomes = await db.signal_outcomes.find(
            {"symbol": symbol, "created_at": {"$gte": cutoff}}
        ).sort("created_at", -1).limit(50).to_list(50)

        anomalies = await db.anomalies.find(
            {"symbol": symbol, "created_at": {"$gte": datetime.utcnow() - timedelta(days=3)}}
        ).sort("created_at", -1).limit(5).to_list(5)

        news = await db.news_analysis.find_one({"symbol": symbol}, sort=[("created_at", -1)]) or {}
        stats = await db.coin_stats.find_one({"symbol": symbol}) or {}

        total = len(outcomes)
        wins  = sum(1 for o in outcomes if o.get("won"))
        best_hours = await self._get_best_hours(symbol)

        # En son kaybedilen/kazanılan örüntüler
        recent_losses = [o["indicators"].get("ema_trend") for o in outcomes if not o.get("won")][-3:]
        recent_wins   = [o["indicators"].get("ema_trend") for o in outcomes if o.get("won")][-3:]

        return {
            "symbol": symbol,
            "total_signals_30d": total,
            "win_rate_30d": round(wins / total * 100, 1) if total > 0 else None,
            "avg_pnl_30d": round(sum(o["pnl_pct"] for o in outcomes) / total, 2) if total > 0 else None,
            "best_hours_utc": best_hours,
            "current_hour_utc": datetime.utcnow().hour,
            "is_good_hour": datetime.utcnow().hour in best_hours if best_hours else None,
            "recent_anomalies": [{"type": a["type"], "severity": a.get("severity")} for a in anomalies],
            "news_sentiment": news.get("overall_sentiment", "UNKNOWN"),
            "news_key_events": news.get("key_events", []),
            "recent_loss_patterns": recent_losses,
            "recent_win_patterns": recent_wins,
            "all_time_pnl": round(stats.get("total_pnl", 0), 2),
            "all_time_signals": stats.get("total_signals", 0),
        }

    async def get_pattern_score(self, symbol: str, indicators: dict) -> float:
        """Bu indikatör kombinasyonu geçmişte ne kadar başarılıydı? (0-1)"""
        db = get_db()
        rsi = indicators.get("rsi_14", 50)
        ema_trend = indicators.get("ema_trend", "MIXED")
        similar = await db.signal_outcomes.find({
            "symbol": symbol, "indicators.ema_trend": ema_trend,
            "indicators.rsi_14": {"$gte": rsi - 8, "$lte": rsi + 8},
            "created_at": {"$gte": datetime.utcnow() - timedelta(days=60)},
        }).to_list(20)
        if not similar:
            return 0.5
        return round(sum(1 for s in similar if s.get("won")) / len(similar), 2)

    async def record_anomaly(self, symbol, anomaly_type, severity, data):
        await get_db().anomalies.insert_one({
            "symbol": symbol, "type": anomaly_type, "severity": severity,
            "data": data, "created_at": datetime.utcnow(),
        })

    async def _get_best_hours(self, symbol: str) -> list:
        pipeline = [
            {"$match": {"symbol": symbol, "created_at": {"$gte": datetime.utcnow() - timedelta(days=60)}}},
            {"$group": {"_id": "$hour_utc", "wins": {"$sum": {"$cond": ["$won", 1, 0]}}, "total": {"$sum": 1}}},
            {"$match": {"total": {"$gte": 3}}},
            {"$addFields": {"wr": {"$divide": ["$wins", "$total"]}}},
            {"$sort": {"wr": -1}}, {"$limit": 4},
        ]
        results = await get_db().signal_outcomes.aggregate(pipeline).to_list(4)
        return [r["_id"] for r in results]
