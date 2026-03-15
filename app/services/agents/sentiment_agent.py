"""
FuturAgents — Sentiment Analysis Agent
Funding rate, L/S oranı, tasfiye verilerini analiz eder.
Haiku kullanır (sayısal veri yorumu için yeterli, %80 ucuz).
"""
import logging
from datetime import datetime
import httpx
from app.services.binance.client import get_binance_client
from app.services.llm.service import get_llm_service

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """Sen bir kripto futures piyasası duyarlılık analisti uzmanısın.
Funding rate, long/short oranı, açık pozisyon değişimi ve tasfiye verilerini analiz ederek
piyasanın genel ruh halini tespit edersin.

Aşırı iyimserlik (açığa satış fırsatı) ve aşırı kötümserlik (alım fırsatı) durumlarını yakala.
"Short squeeze" ve "long squeeze" potansiyelini değerlendir.

Yanıtını JSON formatında ver."""


class SentimentAnalysisAgent:

    def __init__(self):
        self.binance = get_binance_client()
        self.llm = get_llm_service()

    async def analyze(self, symbol: str) -> dict:
        logger.info(f"[SentimentAgent] {symbol}")

        import asyncio
        funding, ticker, liquidations = await asyncio.gather(
            self.binance.get_funding_rate(symbol),
            self.binance.get_24h_ticker(symbol),
            self._get_liquidations(symbol),
            return_exceptions=True,
        )
        if isinstance(funding, Exception):     funding = {}
        if isinstance(ticker, Exception):      ticker = {}
        if isinstance(liquidations, Exception): liquidations = {"long_liq": 0, "short_liq": 0}

        ls_ratio = await self._get_ls_ratio(symbol)

        data = {
            "funding_rate": float(funding.get("funding_rate", 0)),
            "mark_price": float(funding.get("mark_price", 0)),
            "price_change_24h": float(ticker.get("priceChangePercent", 0)),
            "volume_24h": float(ticker.get("quoteVolume", 0)),
            "long_short_ratio": ls_ratio,
            "liquidations": liquidations,
        }
        analysis = await self._llm_interpret(symbol, data)
        return {
            "agent": "sentiment_analysis",
            "symbol": symbol,
            "data": data,
            "llm_analysis": analysis,
            "timestamp": datetime.utcnow().isoformat(),
        }

    async def _get_liquidations(self, symbol: str) -> dict:
        try:
            liqs = await self.binance.get_liquidations(symbol, limit=50)
            long_liq  = sum(float(l["origQty"]) * float(l["price"]) for l in liqs if l.get("side") == "SELL")
            short_liq = sum(float(l["origQty"]) * float(l["price"]) for l in liqs if l.get("side") == "BUY")
            return {
                "long_liq_usd": round(long_liq, 0), "short_liq_usd": round(short_liq, 0),
                "liq_count": len(liqs),
                "dominant": "LONG_SQUEEZE" if long_liq > short_liq * 2 else
                            "SHORT_SQUEEZE" if short_liq > long_liq * 2 else "BALANCED",
            }
        except Exception as e:
            logger.warning(f"Tasfiye verisi alınamadı: {e}")
            return {"long_liq_usd": 0, "short_liq_usd": 0, "liq_count": 0, "dominant": "UNKNOWN"}

    async def _get_ls_ratio(self, symbol: str) -> dict:
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(
                    f"{self.binance.base_url}/futures/data/globalLongShortAccountRatio",
                    params={"symbol": symbol, "period": "1h", "limit": 3}, timeout=8.0)
                if resp.status_code == 200 and resp.json():
                    d = resp.json()[-1]
                    la = float(d.get("longAccount", 0.5))
                    return {
                        "long_ratio": la, "short_ratio": 1 - la,
                        "ls_ratio": float(d.get("longShortRatio", 1.0)),
                        "sentiment": "GREED" if la > 0.6 else "FEAR" if la < 0.4 else "NEUTRAL",
                    }
        except Exception:
            pass
        return {"long_ratio": 0.5, "short_ratio": 0.5, "ls_ratio": 1.0, "sentiment": "NEUTRAL"}

    async def _llm_interpret(self, symbol: str, data: dict) -> dict:
        user_prompt = f"""
Sembol: {symbol}
Funding Rate: {data['funding_rate']:.4%} (pozitif=boğa, negatif=ayı)
24s Fiyat: {data['price_change_24h']:.2f}%
24s Hacim: ${data['volume_24h']:,.0f}
L/S Oranı: {data['long_short_ratio']}
Tasfiyeler: {data['liquidations']}

JSON yanıt:
{{
  "overall_sentiment": "EXTREME_GREED"|"GREED"|"NEUTRAL"|"FEAR"|"EXTREME_FEAR",
  "sentiment_score": -100 ile 100,
  "squeeze_risk": {{"type": "LONG_SQUEEZE"|"SHORT_SQUEEZE"|"NONE", "probability": 0-100}},
  "contrarian_signal": "BUY"|"SELL"|"HOLD",
  "reasoning": "kısa açıklama",
  "warnings": []
}}"""
        try:
            # Haiku kullan — sayısal veri için yeterli, %80 ucuz
            return await self.llm.complete_json(
                system=SYSTEM_PROMPT, user=user_prompt,
                model_tier="fast",   # ← Sonnet'ten Haiku'ya değiştirildi
                max_tokens=512,
            )
        except Exception as e:
            logger.error(f"Sentiment LLM hatası: {e}")
            return {"overall_sentiment": "NEUTRAL", "sentiment_score": 0, "error": str(e)}
