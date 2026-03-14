"""
FuturAgents — Sentiment Analysis Agent
Piyasa duyarlılığını şu kaynaklardan analiz eder:
  - Funding Rate geçmişi
  - Büyük tasfiyeler (liquidations)
  - Long/Short oranı
  - Açık pozisyon trendi (OI)
Sonnet modeli kullanır.
"""
import logging
from datetime import datetime

import httpx

from app.core.config import settings
from app.services.binance.client import get_binance_client
from app.services.llm.service import get_llm_service

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """Sen bir kripto futures piyasası duyarlılık analisti uzmanısın.
Funding rate, long/short oranı, açık pozisyon değişimi ve tasfiye verilerini analiz ederek
piyasanın genel ruh halini (sentiment) tespit edersin.

Aşırı iyimserlik (açığa satış fırsatı) ve aşırı kötümserlik (alım fırsatı) durumlarını yakala.
Özellikle "short squeeze" ve "long squeeze" potansiyelini değerlendir.

Yanıtını JSON formatında ver."""


class SentimentAnalysisAgent:
    """
    Piyasa duyarlılık analisti.
    Funding, OI, liquidation ve L/S oranından sinyal üretir.
    """

    def __init__(self):
        self.binance = get_binance_client()
        self.llm = get_llm_service()

    async def analyze(self, symbol: str) -> dict:
        logger.info(f"[SentimentAgent] {symbol} duyarlılık analizi")

        # Paralel veri çekimi
        import asyncio
        funding, oi, ticker, liquidations = await asyncio.gather(
            self.binance.get_funding_rate(symbol),
            self.binance.get_open_interest(symbol),
            self.binance.get_24h_ticker(symbol),
            self._get_liquidations(symbol),
            return_exceptions=True,
        )

        # Hata kontrolü
        if isinstance(funding, Exception):
            funding = {}
        if isinstance(oi, Exception):
            oi = {}
        if isinstance(ticker, Exception):
            ticker = {}
        if isinstance(liquidations, Exception):
            liquidations = {"long_liq": 0, "short_liq": 0}

        # Long/Short oranı (Binance Global L/S)
        ls_ratio = await self._get_ls_ratio(symbol)

        sentiment_data = {
            "funding_rate": float(funding.get("funding_rate", 0)),
            "mark_price": float(funding.get("mark_price", 0)),
            "open_interest": float(oi.get("openInterest", 0)),
            "price_change_24h": float(ticker.get("priceChangePercent", 0)),
            "volume_24h": float(ticker.get("quoteVolume", 0)),
            "long_short_ratio": ls_ratio,
            "liquidations": liquidations,
        }

        # LLM yorumu
        analysis = await self._llm_interpret(symbol, sentiment_data)

        return {
            "agent": "sentiment_analysis",
            "symbol": symbol,
            "data": sentiment_data,
            "llm_analysis": analysis,
            "timestamp": datetime.utcnow().isoformat(),
        }

    async def _get_liquidations(self, symbol: str) -> dict:
        """Son 24 saat içindeki tasfiye verisini özetle"""
        try:
            liqs = await self.binance.get_liquidations(symbol, limit=50)
            long_liq = sum(
                float(l["origQty"]) * float(l["price"])
                for l in liqs
                if l.get("side") == "SELL"  # Long pozisyon tasfiyesi
            )
            short_liq = sum(
                float(l["origQty"]) * float(l["price"])
                for l in liqs
                if l.get("side") == "BUY"  # Short pozisyon tasfiyesi
            )
            return {
                "long_liq_usd": round(long_liq, 0),
                "short_liq_usd": round(short_liq, 0),
                "liq_count": len(liqs),
                "dominant": "LONG_SQUEEZE" if long_liq > short_liq * 2 else
                            "SHORT_SQUEEZE" if short_liq > long_liq * 2 else "BALANCED",
            }
        except Exception as e:
            logger.warning(f"Tasfiye verisi alınamadı: {e}")
            return {"long_liq_usd": 0, "short_liq_usd": 0, "liq_count": 0, "dominant": "UNKNOWN"}

    async def _get_ls_ratio(self, symbol: str) -> dict:
        """Global Long/Short pozisyon oranı"""
        try:
            async with httpx.AsyncClient() as client:
                base = self.binance.base_url
                resp = await client.get(
                    f"{base}/futures/data/globalLongShortAccountRatio",
                    params={"symbol": symbol, "period": "1h", "limit": 5},
                    timeout=10.0,
                )
                if resp.status_code == 200:
                    data = resp.json()
                    if data:
                        latest = data[-1]
                        return {
                            "long_ratio": float(latest.get("longAccount", 0.5)),
                            "short_ratio": float(latest.get("shortAccount", 0.5)),
                            "ls_ratio": float(latest.get("longShortRatio", 1.0)),
                            "sentiment": "GREED" if float(latest.get("longAccount", 0.5)) > 0.6 else
                                         "FEAR" if float(latest.get("longAccount", 0.5)) < 0.4 else "NEUTRAL",
                        }
        except Exception as e:
            logger.warning(f"L/S oranı alınamadı: {e}")
        return {"long_ratio": 0.5, "short_ratio": 0.5, "ls_ratio": 1.0, "sentiment": "NEUTRAL"}

    async def _llm_interpret(self, symbol: str, data: dict) -> dict:
        user_prompt = f"""
Sembol: {symbol}
Duyarlılık Verileri:
- Funding Rate: {data['funding_rate']:.4%} (pozitif = longs öder, piyasa boğa; negatif = ayı)
- 24s Fiyat Değişimi: {data['price_change_24h']:.2f}%
- 24s Hacim: ${data['volume_24h']:,.0f}
- Açık Pozisyon: {data['open_interest']} adet
- Long/Short Oranı: {data['long_short_ratio']}
- Tasfiyeler: {data['liquidations']}

Bu verileri analiz et ve şu JSON formatında yanıtla:
{{
  "overall_sentiment": "EXTREME_GREED" | "GREED" | "NEUTRAL" | "FEAR" | "EXTREME_FEAR",
  "sentiment_score": -100 ile 100 arası (negatif = ayı, pozitif = boğa),
  "squeeze_risk": {{"type": "LONG_SQUEEZE" | "SHORT_SQUEEZE" | "NONE", "probability": 0-100}},
  "contrarian_signal": "BUY" | "SELL" | "HOLD",
  "reasoning": "kısa açıklama",
  "warnings": ["önemli uyarılar listesi"]
}}
"""
        try:
            return await self.llm.complete_json(
                system=SYSTEM_PROMPT,
                user=user_prompt,
                model_tier="analyst",  # Sonnet
            )
        except Exception as e:
            logger.error(f"Sentiment LLM hatası: {e}")
            return {"overall_sentiment": "NEUTRAL", "sentiment_score": 0, "error": str(e)}
