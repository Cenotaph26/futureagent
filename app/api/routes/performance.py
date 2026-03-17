"""
FuturAgents — Performans API
Trade geçmişi, PnL analizi, win rate, Sharpe ratio
"""
import json
import logging
from datetime import datetime, timedelta
from fastapi import APIRouter, Query
from app.db.database import get_db, get_redis
from app.services.binance.client import get_binance_client

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/summary")
async def performance_summary(days: int = Query(30)):
    """Toplam performans özeti"""
    db = get_db()
    since = datetime.utcnow() - timedelta(days=days)

    trades = []
    async for t in db.trades.find({"created_at": {"$gte": since}}).sort("created_at", -1):
        t["_id"] = str(t["_id"])
        trades.append(t)

    closed = [t for t in trades if t.get("status") == "closed" and t.get("pnl") is not None]
    open_trades = [t for t in trades if t.get("status") == "open"]

    total_pnl = sum(t.get("pnl", 0) for t in closed)
    wins = [t for t in closed if t.get("pnl", 0) > 0]
    losses = [t for t in closed if t.get("pnl", 0) <= 0]
    win_rate = round(len(wins) / len(closed) * 100, 1) if closed else 0

    avg_win_pnl = sum(t["pnl"] for t in wins) / len(wins) if wins else 0
    avg_loss_pnl = sum(t["pnl"] for t in losses) / len(losses) if losses else 0
    profit_factor = round(abs(avg_win_pnl * len(wins)) / max(abs(avg_loss_pnl * len(losses)), 0.01), 2) if losses else 0

    # PnL % hesapla (giriş değerine göre)
    def pnl_pct(trade):
        ep = trade.get("entry_price", 0)
        qty = trade.get("quantity", 0)
        lev = trade.get("leverage", 1)
        if ep and qty and lev:
            pos_val = ep * qty / lev
            return (trade.get("pnl", 0) / pos_val * 100) if pos_val > 0 else 0
        return 0

    total_pnl_pct = sum(pnl_pct(t) for t in closed)
    avg_win_pct = sum(pnl_pct(t) for t in wins) / len(wins) if wins else 0
    avg_loss_pct = sum(pnl_pct(t) for t in losses) / len(losses) if losses else 0
    best = max(closed, key=lambda t: pnl_pct(t), default={})
    worst = min(closed, key=lambda t: pnl_pct(t), default={})

    # Coin bazlı
    by_coin = {}
    for t in closed:
        sym = t.get("symbol", "?")
        if sym not in by_coin:
            by_coin[sym] = {"symbol": sym, "total": 0, "wins": 0, "losses": 0, "total_pnl": 0, "pnl_pcts": []}
        by_coin[sym]["total"] += 1
        pp = pnl_pct(t)
        by_coin[sym]["pnl_pcts"].append(pp)
        by_coin[sym]["total_pnl"] += pp
        if t.get("pnl", 0) > 0:
            by_coin[sym]["wins"] += 1
        else:
            by_coin[sym]["losses"] += 1

    coin_breakdown = []
    for sym, d in by_coin.items():
        coin_breakdown.append({
            "symbol": sym,
            "total": d["total"],
            "wins": d["wins"],
            "losses": d["losses"],
            "win_rate": round(d["wins"] / d["total"] * 100, 1) if d["total"] else 0,
            "total_pnl": round(d["total_pnl"], 2),
        })
    coin_breakdown.sort(key=lambda x: x["total_pnl"], reverse=True)

    # Analiz istatistikleri
    analyze_stats = []
    pipeline = [
        {"$match": {"created_at": {"$gte": since}}},
        {"$group": {"_id": "$final_decision.decision", "count": {"$sum": 1},
                    "avg_confidence": {"$avg": "$final_decision.confidence"},
                    "avg_leverage": {"$avg": "$final_decision.leverage"}}},
    ]
    async for doc in db.analyses.aggregate(pipeline):
        analyze_stats.append(doc)

    daily_pnl = {}
    for t in closed:
        day = t["created_at"].strftime("%Y-%m-%d") if isinstance(t.get("created_at"), datetime) else "?"
        daily_pnl[day] = round(daily_pnl.get(day, 0) + t.get("pnl", 0), 2)

    return {
        "period_days": days,
        "total_trades": len(closed),
        "open_trades_count": len(open_trades),
        "total_pnl": round(total_pnl, 2),
        "total_pnl_pct": round(total_pnl_pct, 2),
        "win_rate": win_rate,
        "wins": len(wins),
        "losses": len(losses),
        "avg_win_pct": round(avg_win_pct, 2),
        "avg_loss_pct": round(avg_loss_pct, 2),
        "profit_factor": profit_factor,
        "best_trade_pct": round(pnl_pct(best), 2) if best else 0,
        "worst_trade_pct": round(pnl_pct(worst), 2) if worst else 0,
        "coin_breakdown": coin_breakdown,
        "analyze_stats": analyze_stats,
        "daily_pnl": [{"date": k, "pnl": v} for k, v in sorted(daily_pnl.items())],
    }


@router.get("/trades")
async def list_trades(
    symbol: str = Query(None),
    status: str = Query(None),
    days: int = Query(30),
    limit: int = Query(50),
):
    """Trade geçmişi"""
    db = get_db()
    query = {"created_at": {"$gte": datetime.utcnow() - timedelta(days=days)}}
    if symbol: query["symbol"] = symbol.upper()
    if status: query["status"] = status

    trades = []
    async for t in db.trades.find(query).sort("created_at", -1).limit(limit):
        t["_id"] = str(t["_id"])
        trades.append(t)
    return trades


@router.post("/sync-closed")
async def sync_closed_trades():
    """Binance'den kapalı trade'leri çek ve DB'ye kaydet"""
    db = get_db()
    binance = get_binance_client()
    synced = 0
    try:
        # Son 7 günün işlem geçmişi
        for symbol in ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT"]:
            try:
                history = await binance.get_trade_history(symbol, limit=50)
                for trade in history:
                    order_id = str(trade.get("orderId", ""))
                    existing = await db.trades.find_one({"order_id": order_id})
                    if not existing and float(trade.get("realizedPnl", 0)) != 0:
                        await db.trades.insert_one({
                            "symbol": symbol,
                            "order_id": order_id,
                            "direction": "LONG" if trade.get("side") == "BUY" else "SHORT",
                            "entry_price": float(trade.get("price", 0)),
                            "quantity": float(trade.get("qty", 0)),
                            "leverage": int(trade.get("leverage", 1)),
                            "pnl": float(trade.get("realizedPnl", 0)),
                            "commission": float(trade.get("commission", 0)),
                            "status": "closed",
                            "closed_at": datetime.utcfromtimestamp(trade.get("time", 0) / 1000),
                            "created_at": datetime.utcnow(),
                            "source": "binance_sync",
                        })
                        synced += 1
            except Exception as e:
                logger.warning(f"Sync {symbol}: {e}")
        return {"synced": synced}
    except Exception as e:
        return {"error": str(e), "synced": synced}


@router.get("/leverage-stats")
async def leverage_stats(days: int = Query(30)):
    """Kaldıraç kullanım istatistikleri"""
    db = get_db()
    since = datetime.utcnow() - timedelta(days=days)
    pipeline = [
        {"$match": {"created_at": {"$gte": since}, "final_decision.leverage": {"$exists": True}}},
        {"$group": {
            "_id": "$final_decision.leverage",
            "count": {"$sum": 1},
            "avg_confidence": {"$avg": "$final_decision.confidence"},
        }},
        {"$sort": {"_id": 1}},
    ]
    result = []
    async for doc in db.analyses.aggregate(pipeline):
        result.append(doc)
    return result


@router.get("/open-trades")
async def open_trades():
    """Açık pozisyonlar detaylı (mark price + unrealized PnL)"""
    binance = get_binance_client()
    db = get_db()
    try:
        positions = await binance.get_positions()
        result = []
        for p in positions:
            amt = float(p.get("positionAmt", 0))
            if abs(amt) < 0.0001:
                continue
            ep = float(p.get("entryPrice", 0))
            mp = float(p.get("markPrice", 0))
            upnl = float(p.get("unRealizedProfit", 0))
            direction = "LONG" if amt > 0 else "SHORT"
            pnl_pct = ((mp - ep) / ep * 100 * (1 if direction=="LONG" else -1)) if ep > 0 else 0

            # DB'den trade detaylarını al
            trade = await db.trades.find_one({"symbol": p["symbol"], "status": "open"})

            result.append({
                "symbol": p["symbol"],
                "direction": direction,
                "quantity": abs(amt),
                "entry_price": ep,
                "mark_price": mp,
                "unrealized_pnl": round(upnl, 2),
                "pnl_pct": round(pnl_pct, 2),
                "leverage": int(p.get("leverage", 1)),
                "stop_loss": trade.get("stop_loss") if trade else None,
                "take_profit_1": trade.get("take_profit_1") if trade else None,
                "confidence": trade.get("confidence") if trade else None,
            })
        return result
    except Exception as e:
        return []


@router.get("/hourly")
async def hourly_performance(days: int = Query(30)):
    """Saat bazlı performans (UTC)"""
    db = get_db()
    since = datetime.utcnow() - timedelta(days=days)

    # Trade'leri saat bazlı grupla
    trades = []
    async for t in db.trades.find(
        {"created_at": {"$gte": since}, "status": "closed", "pnl": {"$exists": True}}
    ):
        if isinstance(t.get("created_at"), datetime):
            hour = t["created_at"].hour
            trades.append({"hour": hour, "pnl": t.get("pnl", 0)})

    # Saat bazlı özet
    hourly = {}
    for t in trades:
        h = t["hour"]
        if h not in hourly:
            hourly[h] = {"hour": h, "trades": 0, "wins": 0, "pnl": 0}
        hourly[h]["trades"] += 1
        if t["pnl"] > 0:
            hourly[h]["wins"] += 1
        hourly[h]["pnl"] += t["pnl"]

    result = []
    for h, d in hourly.items():
        result.append({
            "hour": h,
            "trades": d["trades"],
            "win_rate": round(d["wins"] / d["trades"] * 100, 1) if d["trades"] else 0,
            "avg_pnl": round(d["pnl"] / d["trades"], 2) if d["trades"] else 0,
            "total_pnl": round(d["pnl"], 2),
        })
    return sorted(result, key=lambda x: x["hour"])
