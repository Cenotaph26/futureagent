"""
FuturAgents — Technical Analysis Agent
Binance'den OHLCV verisi çekip teknik indikatörler hesaplar.
Haiku modeli kullanır (hızlı + ucuz).
"""
import logging
from datetime import datetime
from typing import Any

import pandas as pd
import numpy as np

from app.services.binance.client import get_binance_client
from app.services.llm.service import get_llm_service

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """Sen bir kripto futures piyasası teknik analiz uzmanısın.
Sana verilen OHLCV verileri ve hesaplanmış teknik indikatörleri analiz ederek:
1. Kısa vadeli trend yönünü belirle (LONG / SHORT / NEUTRAL)
2. Sinyal gücünü 0-100 arasında puanla
3. Önemli destek / direnç seviyelerini tespit et
4. Risk seviyesini değerlendir (LOW / MEDIUM / HIGH / EXTREME)

Yanıtını her zaman JSON formatında ver."""


class TechnicalAnalysisAgent:
    """
    Binance'den veri çeker, pandas-ta ile indikatör hesaplar,
    Claude Haiku ile yorumlar.
    """

    def __init__(self):
        self.binance = get_binance_client()
        self.llm = get_llm_service()

    async def analyze(
        self,
        symbol: str,
        interval: str = "1h",
        limit: int = 200,
    ) -> dict:
        """Tam teknik analiz yap ve sinyal üret"""
        logger.info(f"[TechAgent] {symbol} teknik analiz başlıyor ({interval})")

        # 1. Veri çek
        klines = await self.binance.get_klines(symbol, interval, limit)
        df = self._to_dataframe(klines)

        # 2. İndikatör hesapla
        indicators = self._calculate_indicators(df)

        # 3. Funding + OI verisi
        funding = await self.binance.get_funding_rate(symbol)
        current_price = float(df["close"].iloc[-1])

        # 4. LLM ile yorumla
        analysis = await self._llm_interpret(symbol, interval, indicators, funding, current_price)

        return {
            "agent": "technical_analysis",
            "symbol": symbol,
            "interval": interval,
            "current_price": current_price,
            "indicators": indicators,
            "funding_rate": funding["funding_rate"],
            "mark_price": funding["mark_price"],
            "llm_analysis": analysis,
            "timestamp": datetime.utcnow().isoformat(),
        }

    def _to_dataframe(self, klines: list[dict]) -> pd.DataFrame:
        df = pd.DataFrame(klines)
        df["open_time"] = pd.to_datetime(df["open_time"], unit="ms")
        for col in ["open", "high", "low", "close", "volume"]:
            df[col] = pd.to_numeric(df[col])
        df.set_index("open_time", inplace=True)
        return df

    def _calculate_indicators(self, df: pd.DataFrame) -> dict:
        close = df["close"]
        high = df["high"]
        low = df["low"]
        volume = df["volume"]

        # Trend indikatörleri
        ema20 = close.ewm(span=20).mean()
        ema50 = close.ewm(span=50).mean()
        ema200 = close.ewm(span=200).mean()

        # RSI
        rsi = self._rsi(close, 14)

        # MACD
        ema12 = close.ewm(span=12).mean()
        ema26 = close.ewm(span=26).mean()
        macd_line = ema12 - ema26
        signal_line = macd_line.ewm(span=9).mean()
        macd_hist = macd_line - signal_line

        # Bollinger Bands
        sma20 = close.rolling(20).mean()
        std20 = close.rolling(20).std()
        bb_upper = sma20 + 2 * std20
        bb_lower = sma20 - 2 * std20
        bb_pct = (close - bb_lower) / (bb_upper - bb_lower)

        # ATR (volatilite)
        atr = self._atr(high, low, close, 14)

        # Stochastic RSI
        rsi_min = rsi.rolling(14).min()
        rsi_max = rsi.rolling(14).max()
        stoch_rsi = (rsi - rsi_min) / (rsi_max - rsi_min + 1e-10)

        # Volume analizi
        vol_sma20 = volume.rolling(20).mean()
        vol_ratio = volume / vol_sma20

        # Son değerler
        i = -1  # en son bar
        curr = float(close.iloc[i])

        # Fibonacci seviyeleri (son 50 bar high/low'dan)
        period_high = float(high.iloc[-50:].max())
        period_low = float(low.iloc[-50:].min())
        fib_range = period_high - period_low
        fibs = {
            "0.0":   round(period_low, 4),
            "0.236": round(period_low + fib_range * 0.236, 4),
            "0.382": round(period_low + fib_range * 0.382, 4),
            "0.5":   round(period_low + fib_range * 0.5, 4),
            "0.618": round(period_low + fib_range * 0.618, 4),
            "0.786": round(period_low + fib_range * 0.786, 4),
            "1.0":   round(period_high, 4),
        }

        return {
            # Trend
            "ema20": round(float(ema20.iloc[i]), 4),
            "ema50": round(float(ema50.iloc[i]), 4),
            "ema200": round(float(ema200.iloc[i]), 4),
            "price_vs_ema20": round((curr - float(ema20.iloc[i])) / float(ema20.iloc[i]) * 100, 2),
            "price_vs_ema200": round((curr - float(ema200.iloc[i])) / float(ema200.iloc[i]) * 100, 2),
            "ema_trend": "BULLISH" if float(ema20.iloc[i]) > float(ema50.iloc[i]) > float(ema200.iloc[i]) else
                         "BEARISH" if float(ema20.iloc[i]) < float(ema50.iloc[i]) < float(ema200.iloc[i]) else "MIXED",

            # Momentum
            "rsi_14": round(float(rsi.iloc[i]), 2),
            "rsi_signal": "OVERSOLD" if float(rsi.iloc[i]) < 30 else "OVERBOUGHT" if float(rsi.iloc[i]) > 70 else "NEUTRAL",
            "macd_line": round(float(macd_line.iloc[i]), 4),
            "macd_signal": round(float(signal_line.iloc[i]), 4),
            "macd_hist": round(float(macd_hist.iloc[i]), 4),
            "macd_cross": "BULLISH" if float(macd_hist.iloc[i]) > 0 and float(macd_hist.iloc[i-1]) <= 0 else
                          "BEARISH" if float(macd_hist.iloc[i]) < 0 and float(macd_hist.iloc[i-1]) >= 0 else "NONE",
            "stoch_rsi": round(float(stoch_rsi.iloc[i]), 3),

            # Volatilite
            "bb_upper": round(float(bb_upper.iloc[i]), 4),
            "bb_lower": round(float(bb_lower.iloc[i]), 4),
            "bb_pct": round(float(bb_pct.iloc[i]), 3),  # 0=alt band, 1=üst band
            "atr_14": round(float(atr.iloc[i]), 4),
            "atr_pct": round(float(atr.iloc[i]) / curr * 100, 2),  # fiyatın %'si

            # Hacim
            "volume_ratio": round(float(vol_ratio.iloc[i]), 2),  # >1.5 = yüksek hacim
            "volume_trend": "HIGH" if float(vol_ratio.iloc[i]) > 1.5 else "LOW" if float(vol_ratio.iloc[i]) < 0.7 else "NORMAL",

            # Fibonacci
            "fibonacci": fibs,
            "period_high": round(period_high, 4),
            "period_low": round(period_low, 4),
        }

    def _rsi(self, close: pd.Series, period: int = 14) -> pd.Series:
        delta = close.diff()
        gain = delta.where(delta > 0, 0).rolling(period).mean()
        loss = -delta.where(delta < 0, 0).rolling(period).mean()
        rs = gain / (loss + 1e-10)
        return 100 - (100 / (1 + rs))

    def _atr(self, high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
        tr1 = high - low
        tr2 = (high - close.shift()).abs()
        tr3 = (low - close.shift()).abs()
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        return tr.rolling(period).mean()

    async def _llm_interpret(
        self,
        symbol: str,
        interval: str,
        indicators: dict,
        funding: dict,
        price: float,
    ) -> dict:
        user_prompt = f"""
Sembol: {symbol}
Zaman dilimi: {interval}
Güncel Fiyat: {price}
Funding Rate: {funding['funding_rate']:.4%}
Mark Price: {funding['mark_price']}

Teknik İndikatörler:
{indicators}

Bu verileri analiz ederek aşağıdaki JSON yapısında yanıt ver:
{{
  "signal": "LONG" | "SHORT" | "NEUTRAL",
  "confidence": 0-100,
  "reasoning": "kısa açıklama",
  "entry_zone": {{"low": sayı, "high": sayı}},
  "stop_loss": sayı,
  "take_profit_1": sayı,
  "take_profit_2": sayı,
  "risk_level": "LOW" | "MEDIUM" | "HIGH" | "EXTREME",
  "key_levels": {{"support": [sayı, ...], "resistance": [sayı, ...]}}
}}
"""
        try:
            return await self.llm.complete_json(
                system=SYSTEM_PROMPT,
                user=user_prompt,
                model_tier="fast",  # Haiku — hızlı + ucuz
                max_tokens=640,
            )
        except Exception as e:
            logger.error(f"LLM teknik analiz hatası: {e}")
            return {"signal": "NEUTRAL", "confidence": 0, "error": str(e)}
