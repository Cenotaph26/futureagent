"""
FuturAgents — Trailing Stop & Pozisyon Takip Ajanı
LLM KULLANMAZ — kural tabanlı, maliyet $0.
"""
import logging
from datetime import datetime
from typing import Optional

import pandas as pd

from app.core.config import settings
from app.services.binance.client import get_binance_client
from app.db.database import get_db, get_redis

logger = logging.getLogger(__name__)

TRAIL_ATR_MULT   = 1.5    # Stop mesafesi = 1.5 × ATR
MIN_PROFIT_TRAIL = 0.005  # %0.5 kârda trailing başlar
DANGER_MOVE_PCT  = 0.015  # %1.5 ters hareket = alarm
LIQ_WARN_PCT     = 0.03   # Likidasyona %3 kaldıysa uyar


class TrailingStopAgent:

    def __init__(self):
        self.binance = get_binance_client()

    async def run(self) -> dict:
        try:
            positions = await self.binance.get_positions()
        except Exception as e:
            logger.warning(f"[TrailingStop] Pozisyon alınamadı: {e}")
            return {"checked": 0, "actions": []}

        # Kapanan pozisyonların PnL'ini DB'ye yaz
        await self._sync_closed_pnl(positions)

        if not positions:
            return {"checked": 0, "actions": []}

        actions = []
        for pos in positions:
            try:
                action = await self._check_position(pos)
                if action:
                    actions.append(action)
            except Exception as e:
                logger.error(f"[TrailingStop] {pos.get('symbol')} hata: {e}")

        if actions:
            logger.info(f"[TrailingStop] {len(actions)} aksiyon: {[a['type'] for a in actions]}")

        return {"checked": len(positions), "actions": actions,
                "timestamp": datetime.utcnow().isoformat()}

    async def _check_position(self, pos: dict) -> Optional[dict]:
        symbol = pos["symbol"]
        amt    = float(pos.get("positionAmt", 0))
        if amt == 0:
            return None

        is_long     = amt > 0
        entry       = float(pos.get("entryPrice", 0))
        mark        = float(pos.get("markPrice", 0))
        liq         = float(pos.get("liquidationPrice", 0))
        atr         = await self._get_atr(symbol)

        pnl_pct = ((mark - entry) / entry) * (1 if is_long else -1) if entry > 0 else 0

        # Alarm: büyük ters hareket
        if pnl_pct < -DANGER_MOVE_PCT:
            await self._save_alert(symbol, "DANGER_MOVE", {
                "pnl_pct": round(pnl_pct * 100, 2), "mark": mark, "entry": entry})
            return {"type": "DANGER_ALARM", "symbol": symbol,
                    "pnl_pct": round(pnl_pct * 100, 2),
                    "message": f"{symbol} {'LONG' if is_long else 'SHORT'}: %{pnl_pct*100:.1f} ters!"}

        # Alarm: likidasyona yakın
        if liq > 0:
            dist = abs(mark - liq) / mark
            if dist < LIQ_WARN_PCT:
                await self._save_alert(symbol, "NEAR_LIQUIDATION",
                    {"dist_pct": round(dist*100, 2), "liq": liq, "mark": mark})
                return {"type": "NEAR_LIQUIDATION", "symbol": symbol,
                        "distance_pct": round(dist * 100, 2)}

        # Trailing stop güncelleme
        if pnl_pct >= MIN_PROFIT_TRAIL and atr > 0:
            return await self._update_trail(symbol, is_long, mark, entry, abs(amt), atr)

        return None

    async def _update_trail(self, symbol, is_long, mark, entry, qty, atr) -> Optional[dict]:
        redis = get_redis()
        key   = f"trail:{symbol}:{'long' if is_long else 'short'}"
        stored = await redis.get(key)
        peak  = float(stored) if stored else mark
        new_peak = max(peak, mark) if is_long else min(peak, mark)
        if new_peak != peak:
            await redis.setex(key, 86400, str(new_peak))

        new_stop = new_peak - TRAIL_ATR_MULT * atr if is_long else new_peak + TRAIL_ATR_MULT * atr

        db = get_db()
        doc = await db.trailing_stops.find_one({"symbol": symbol, "side": "long" if is_long else "short"})
        old_stop = float(doc.get("stop_price", 0)) if doc else 0
        improved = (is_long and new_stop > old_stop) or (not is_long and new_stop < old_stop) or old_stop == 0

        if improved:
            await db.trailing_stops.update_one(
                {"symbol": symbol, "side": "long" if is_long else "short"},
                {"$set": {"stop_price": new_stop, "peak": new_peak, "atr": atr,
                          "updated_at": datetime.utcnow()}},
                upsert=True,
            )
            logger.info(f"[Trail] {symbol} stop: {old_stop:.4f} → {new_stop:.4f}")
            return {"type": "TRAIL_UPDATED", "symbol": symbol,
                    "old_stop": round(old_stop, 4), "new_stop": round(new_stop, 4),
                    "peak": round(new_peak, 4)}
        return None

    async def _get_atr(self, symbol: str, interval: str = "15m") -> float:
        redis = get_redis()
        cached = await redis.get(f"atr:{symbol}:{interval}")
        if cached:
            return float(cached)
        try:
            klines = await self.binance.get_klines(symbol, interval, limit=20)
            df = pd.DataFrame(klines)
            h, l, c = pd.to_numeric(df["high"]), pd.to_numeric(df["low"]), pd.to_numeric(df["close"])
            tr = pd.concat([h-l, (h-c.shift()).abs(), (l-c.shift()).abs()], axis=1).max(axis=1)
            atr = float(tr.rolling(14).mean().iloc[-1])
            await redis.setex(f"atr:{symbol}:{interval}", 300, str(atr))
            return atr
        except Exception:
            return 0

    async def _save_alert(self, symbol: str, alert_type: str, data: dict):
        try:
            await get_db().alerts.insert_one({
                "symbol": symbol, "type": alert_type, "data": data,
                "created_at": datetime.utcnow(), "read": False})
        except Exception as e:
            logger.error(f"Alert kaydı hatası: {e}")

    async def _sync_closed_pnl(self, live_positions: list):
        """Kapanan pozisyonları tespit edip DB'ye PnL yaz"""
        db = get_db()
        try:
            # DB'deki açık trade'ler
            open_trades = []
            async for t in db.trades.find({"status": "open"}):
                open_trades.append(t)
            if not open_trades:
                return
            # Canlı pozisyon sembollerini al
            live_syms = {p["symbol"] for p in live_positions
                         if abs(float(p.get("positionAmt", 0))) > 0.0001}
            # DB'de open ama canlıda yok = kapandı
            for trade in open_trades:
                sym = trade.get("symbol")
                if sym and sym not in live_syms:
                    # Binance'den gerçekleşen PnL'i çek
                    try:
                        history = await self.binance.get_trade_history(sym, limit=10)
                        realized = sum(float(h.get("realizedPnl", 0)) for h in history
                                       if str(h.get("orderId")) == str(trade.get("order_id"))
                                       or h.get("time", 0) > trade.get("created_at",
                                          datetime.utcnow()).timestamp() * 1000 - 86400000)
                    except Exception:
                        realized = None
                    update = {"status": "closed", "closed_at": datetime.utcnow()}
                    if realized is not None:
                        update["pnl"] = round(realized, 4)
                    await db.trades.update_one(
                        {"_id": trade["_id"]}, {"$set": update})
                    logger.info(f"[PnL] {sym} kapatıldı — PnL: {realized}")
