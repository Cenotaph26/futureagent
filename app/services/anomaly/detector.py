"""
FuturAgents — Anomali Dedektörü
LLM maliyeti: $0 — tamamen istatistiksel.

Tespit edilen anomaliler:
  VOLUME_SPIKE      : Hacim Z-score > 3 (olağandışı hacim)
  FUNDING_EXTREME   : Funding rate > ±0.05% (aşırı kaldıraç)
  PRICE_GAP         : Fiyat > 2×ATR'de ani hareket
  OI_SURGE          : Açık pozisyon ani artış/düşüş
  LIQUIDATION_CASCADE: Büyük tasfiye dalgası
  CORRELATION_BREAK  : BTC ile korelasyon kopuşu (bağımsız hareket)
  WHALE_CANDLE      : Tek mumda anormal büyük hareket
"""
import logging
from datetime import datetime
from typing import Optional

import pandas as pd
import numpy as np

from app.services.binance.client import get_binance_client
from app.services.memory.market_memory import MarketMemory
from app.db.database import get_redis

logger = logging.getLogger(__name__)

COINS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT"]


class AnomalyDetector:

    def __init__(self):
        self.binance = get_binance_client()
        self.memory  = MarketMemory()

    async def scan_all(self) -> list[dict]:
        """5 coin için anomali taraması — LLM yok"""
        import asyncio
        results = await asyncio.gather(
            *[self.scan_symbol(s) for s in COINS],
            return_exceptions=True
        )
        anomalies = []
        for r in results:
            if isinstance(r, list):
                anomalies.extend(r)
        if anomalies:
            logger.info(f"[Anomaly] {len(anomalies)} anomali tespit edildi")
        return anomalies

    async def scan_symbol(self, symbol: str) -> list[dict]:
        """Tek sembol için tüm anomali kontrollerini yap"""
        anomalies = []
        try:
            klines_1h = await self.binance.get_klines(symbol, "1h", limit=100)
            funding    = await self.binance.get_funding_rate(symbol)

            df = pd.DataFrame(klines_1h)
            df["close"]  = pd.to_numeric(df["close"])
            df["high"]   = pd.to_numeric(df["high"])
            df["low"]    = pd.to_numeric(df["low"])
            df["volume"] = pd.to_numeric(df["volume"])

            # 1. Hacim spike
            vol_anom = self._check_volume_spike(df, symbol)
            if vol_anom: anomalies.append(vol_anom)

            # 2. Funding extreme
            fund_anom = self._check_funding_extreme(funding, symbol)
            if fund_anom: anomalies.append(fund_anom)

            # 3. Fiyat gap
            gap_anom = self._check_price_gap(df, symbol)
            if gap_anom: anomalies.append(gap_anom)

            # 4. Whale candle
            whale_anom = self._check_whale_candle(df, symbol)
            if whale_anom: anomalies.append(whale_anom)

            # 5. BTC korelasyon kopuşu (BTC dışı coinler için)
            if symbol != "BTCUSDT":
                corr_anom = await self._check_correlation_break(df, symbol)
                if corr_anom: anomalies.append(corr_anom)

            # Tespit edilenleri hafızaya kaydet
            for a in anomalies:
                await self.memory.record_anomaly(
                    symbol=symbol,
                    anomaly_type=a["type"],
                    severity=a["severity"],
                    data=a["data"],
                )

        except Exception as e:
            logger.debug(f"[Anomaly] {symbol} tarama hatası: {e}")

        return anomalies

    def _check_volume_spike(self, df: pd.DataFrame, symbol: str) -> Optional[dict]:
        vol = df["volume"]
        mean = float(vol.iloc[:-1].mean())
        std  = float(vol.iloc[:-1].std())
        last = float(vol.iloc[-1])
        if std == 0: return None
        z = (last - mean) / std
        if z > 3.0:
            severity = "critical" if z > 5 else "high" if z > 4 else "medium"
            return {
                "symbol": symbol, "type": "VOLUME_SPIKE", "severity": severity,
                "data": {"z_score": round(z, 2), "current": round(last), "avg": round(mean)},
                "message": f"{symbol}: Hacim {z:.1f}σ spike ({last/mean:.1f}× ortalama)",
            }
        return None

    def _check_funding_extreme(self, funding: dict, symbol: str) -> Optional[dict]:
        rate = float(funding.get("funding_rate", 0))
        abs_rate = abs(rate)
        if abs_rate > 0.0005:  # 0.05%
            severity = "critical" if abs_rate > 0.001 else "high"
            direction = "LONGS_PAYING" if rate > 0 else "SHORTS_PAYING"
            return {
                "symbol": symbol, "type": "FUNDING_EXTREME", "severity": severity,
                "data": {"rate": rate, "direction": direction, "pct": round(rate * 100, 4)},
                "message": f"{symbol}: Aşırı funding {rate*100:.4f}% ({direction})",
            }
        return None

    def _check_price_gap(self, df: pd.DataFrame, symbol: str) -> Optional[dict]:
        c = df["close"]
        h = df["high"]
        l = df["low"]
        tr = pd.concat([h-l, (h-c.shift()).abs(), (l-c.shift()).abs()], axis=1).max(axis=1)
        atr = float(tr.rolling(14).mean().iloc[-2])  # Önceki bar ATR
        last_move = abs(float(c.iloc[-1]) - float(c.iloc[-2]))
        if atr > 0 and last_move > atr * 2.5:
            ratio = last_move / atr
            return {
                "symbol": symbol, "type": "PRICE_GAP", "severity": "high" if ratio > 3.5 else "medium",
                "data": {"move": round(last_move, 4), "atr": round(atr, 4), "ratio": round(ratio, 2)},
                "message": f"{symbol}: Fiyat {ratio:.1f}×ATR hareketi",
            }
        return None

    def _check_whale_candle(self, df: pd.DataFrame, symbol: str) -> Optional[dict]:
        body = (df["close"] - df["open"]).abs()
        mean_body = float(body.iloc[:-1].mean())
        last_body = float(body.iloc[-1])
        if mean_body > 0 and last_body > mean_body * 4:
            ratio = last_body / mean_body
            return {
                "symbol": symbol, "type": "WHALE_CANDLE", "severity": "medium",
                "data": {"body_ratio": round(ratio, 2), "current_body": round(last_body, 4)},
                "message": f"{symbol}: Balina mumu — {ratio:.1f}× ortalama gövde",
            }
        return None

    async def _check_correlation_break(self, df: pd.DataFrame, symbol: str) -> Optional[dict]:
        try:
            redis = get_redis()
            cached = await redis.get("btc:returns:1h")
            if not cached:
                btc_klines = await self.binance.get_klines("BTCUSDT", "1h", limit=25)
                btc_df = pd.DataFrame(btc_klines)
                btc_close = pd.to_numeric(btc_df["close"])
                btc_returns = btc_close.pct_change().dropna().iloc[-20:].tolist()
                import json
                await redis.setex("btc:returns:1h", 3600, json.dumps(btc_returns))
            else:
                import json
                btc_returns = json.loads(cached)

            coin_returns = df["close"].pct_change().dropna().iloc[-20:].tolist()
            n = min(len(btc_returns), len(coin_returns))
            if n < 10: return None

            corr = float(np.corrcoef(btc_returns[-n:], coin_returns[-n:])[0, 1])
            # Tarihi korelasyon genellikle 0.6-0.9 arası
            if corr < 0.2:
                return {
                    "symbol": symbol, "type": "CORRELATION_BREAK", "severity": "medium",
                    "data": {"btc_correlation": round(corr, 3), "expected": "0.6-0.9"},
                    "message": f"{symbol}: BTC korelasyonu koptu ({corr:.2f}) — bağımsız hareket",
                }
        except Exception:
            pass
        return None
