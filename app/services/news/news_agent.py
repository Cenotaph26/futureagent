"""
FuturAgents — News Agent (Haber Analizi)
Ücretsiz kaynaklardan haber çeker, Haiku ile analiz eder.
Maliyet: ~$0.001 / haber seti (çok ucuz)

Kaynaklar (ücretsiz):
  - CoinGecko trending / news
  - CryptoCompare news (ücretsiz tier)
  - Binance announcements RSS
  - Reddit r/cryptocurrency (RSS)
  - Finnhub (eğer key varsa)
"""
import logging
import httpx
from datetime import datetime
from app.services.llm.service import get_llm_service
from app.db.database import get_db, get_redis

logger = logging.getLogger(__name__)

COIN_KEYWORDS = {
    "BTCUSDT": ["bitcoin", "btc", "bitcoin etf", "bitcoin halving"],
    "ETHUSDT": ["ethereum", "eth", "ethereum merge", "eth2"],
    "SOLUSDT": ["solana", "sol", "solana network"],
    "BNBUSDT": ["binance", "bnb", "binance coin", "bsc"],
    "XRPUSDT": ["ripple", "xrp", "ripple sec", "xrp lawsuit"],
}

NEWS_SOURCES = [
    "https://cryptopanic.com/api/v1/posts/?auth_token=&public=true&currencies={coin}&kind=news",
    "https://min-api.cryptocompare.com/data/v2/news/?categories={coin}&lTs=0",
]

SYSTEM_PROMPT = """Sen bir kripto para piyasası haber analisti uzmanısın.
Verilen haberleri analiz ederek:
1. Genel sentiment (BULLISH / BEARISH / NEUTRAL)
2. Önemli olaylar listesi
3. Fiyata etkisi olabilecek gelişmeler
4. Risk faktörleri

Kısa ve net JSON yanıt ver."""


class NewsAgent:

    def __init__(self):
        self.llm = get_llm_service()

    async def analyze_all_coins(self) -> dict:
        """5 coin için haber analizi — saatte bir çalışır"""
        import asyncio
        results = {}
        for coin in COIN_KEYWORDS:
            try:
                result = await self.analyze_coin(coin)
                results[coin] = result
                await asyncio.sleep(1)
            except Exception as e:
                logger.warning(f"[News] {coin} haber hatası: {e}")
                results[coin] = {"error": str(e)}
        return results

    async def analyze_coin(self, symbol: str) -> dict:
        """Tek coin için haber topla ve analiz et"""
        redis = get_redis()
        cache_key = f"news:{symbol}"

        # 30dk cache
        cached = await redis.get(cache_key)
        if cached:
            import json
            return json.loads(cached)

        # Haberleri çek
        headlines = await self._fetch_headlines(symbol)
        if not headlines:
            return {"overall_sentiment": "UNKNOWN", "summary": "Haber bulunamadı", "key_events": []}

        # Haiku ile analiz (ucuz)
        coin_name = symbol.replace("USDT", "")
        user_prompt = f"""
{coin_name} için son haberler:

{chr(10).join(f'- {h}' for h in headlines[:15])}

JSON yanıt:
{{
  "overall_sentiment": "BULLISH"|"BEARISH"|"NEUTRAL",
  "sentiment_score": -100 ile 100,
  "key_events": ["olay1", "olay2"],
  "risk_factors": ["risk1"],
  "opportunity_signals": ["fırsat1"],
  "summary": "2-3 cümle özet"
}}"""

        try:
            result = await self.llm.complete_json(
                system=SYSTEM_PROMPT, user=user_prompt,
                model_tier="fast",   # Haiku — haber analizi için yeterli
                max_tokens=512,
            )
        except Exception as e:
            result = {"overall_sentiment": "NEUTRAL", "summary": str(e), "key_events": []}

        result["symbol"] = symbol
        result["headlines_count"] = len(headlines)
        result["analyzed_at"] = datetime.utcnow().isoformat()

        # DB'ye kaydet
        db = get_db()
        await db.news_analysis.update_one(
            {"symbol": symbol},
            {"$set": {**result, "created_at": datetime.utcnow()}},
            upsert=True,
        )

        # Cache
        import json
        await redis.setex(cache_key, 1800, json.dumps(result, default=str))  # 30dk

        logger.info(f"[News] {symbol}: {result.get('overall_sentiment')} ({len(headlines)} haber)")
        return result

    async def _fetch_headlines(self, symbol: str) -> list[str]:
        """Ücretsiz kaynaklardan haber başlıklarını çek"""
        headlines = []
        coin = symbol.replace("USDT", "")
        keywords = COIN_KEYWORDS.get(symbol, [coin.lower()])

        try:
            # CryptoCompare (ücretsiz, key gerektirmez)
            async with httpx.AsyncClient(timeout=8.0) as client:
                resp = await client.get(
                    f"https://min-api.cryptocompare.com/data/v2/news/?categories={coin}&lang=EN",
                    headers={"User-Agent": "FuturAgents/1.0"}
                )
                if resp.status_code == 200:
                    data = resp.json()
                    for item in data.get("Data", [])[:10]:
                        title = item.get("title", "")
                        if any(kw in title.lower() for kw in keywords):
                            headlines.append(title)
                        elif not headlines:  # Keyword yoksa da al
                            headlines.append(title)
        except Exception as e:
            logger.debug(f"CryptoCompare hata: {e}")

        try:
            # Reddit RSS (ücretsiz)
            async with httpx.AsyncClient(timeout=8.0) as client:
                resp = await client.get(
                    f"https://www.reddit.com/r/cryptocurrency/search.json?q={coin.lower()}&sort=new&limit=5",
                    headers={"User-Agent": "FuturAgents/1.0"}
                )
                if resp.status_code == 200:
                    posts = resp.json().get("data", {}).get("children", [])
                    for post in posts[:5]:
                        title = post.get("data", {}).get("title", "")
                        if title:
                            headlines.append(f"[Reddit] {title}")
        except Exception as e:
            logger.debug(f"Reddit RSS hata: {e}")

        return headlines[:20]  # Max 20 başlık
