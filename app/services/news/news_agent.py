"""
FuturAgents — News Agent v2 (Anlık Haber)
Çoklu kaynak, 5 dakikada bir polling, yeni haberleri tespit edince analiz.

Kaynaklar (hepsi ücretsiz):
  - CryptoCompare News API (en iyi kripto haber API'si)
  - CryptoPanic public feed (RSS benzeri, key gerektirmez)
  - Reddit r/cryptocurrency (JSON API)
  - Binance Announcements (duyurular için kritik)

Maliyet optimizasyonu:
  - Sadece YENİ haberler analiz edilir (hash karşılaştırma)
  - 5dk'da bir kaynak kontrolü ($0)
  - Yeni haber varsa Haiku analizi (~$0.001)
  - Saatte ~$0.01-0.03 toplam
"""
import hashlib
import json
import logging
import asyncio
from datetime import datetime, timedelta
from typing import Optional

import httpx

from app.services.llm.service import get_llm_service
from app.db.database import get_db, get_redis

logger = logging.getLogger(__name__)

COINS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT"]

COIN_MAP = {
    "BTCUSDT": {"name": "bitcoin", "sym": "BTC", "cc": "BTC", "cp": "BTC"},
    "ETHUSDT": {"name": "ethereum", "sym": "ETH", "cc": "ETH", "cp": "ETH"},
    "SOLUSDT": {"name": "solana", "sym": "SOL", "cc": "SOL", "cp": "SOL"},
    "BNBUSDT": {"name": "binance", "sym": "BNB", "cc": "BNB", "cp": "BNB"},
    "XRPUSDT": {"name": "ripple xrp", "sym": "XRP", "cc": "XRP", "cp": "XRP"},
}

# Trading-relevant keyword filtresi
TRADING_KEYWORDS = [
    "etf", "sec", "lawsuit", "regulation", "ban", "approval", "legal",
    "whale", "exchange", "deposit", "withdrawal", "transfer",
    "hack", "exploit", "vulnerability", "stolen", "breach",
    "fed", "inflation", "rate", "macro", "recession",
    "listing", "delisting", "partnership", "acquisition", "merger",
    "token burn", "halving", "upgrade", "mainnet", "hard fork",
    "liquidat", "squeeze", "leverage", "funding", "open interest",
    "ipo", "fundraise", "investment", "institutional",
]

SYSTEM_PROMPT = """Sen bir kripto futures trading uzmanısın. Haberleri analiz et:
1. Bu haber fiyata etkisi nedir? BULLISH / BEARISH / NEUTRAL
2. Etki seviyesi: HIGH (anlık hareket beklenir) / MEDIUM / LOW
3. Kısa özet (1-2 cümle)
4. Trading aksiyonu önerisi

JSON formatında yanıt ver."""


class NewsAgent:

    def __init__(self):
        self.llm = get_llm_service()
        self._seen_hashes: set = set()  # In-memory dedup

    async def poll_and_analyze(self) -> list[dict]:
        """
        5 dakikada bir çağrılır.
        Sadece YENİ haberleri analiz eder.
        """
        redis = get_redis()
        all_new = []

        for symbol in COINS:
            try:
                # Tüm kaynaklardan haberleri çek
                headlines = await self._fetch_all_sources(symbol)
                if not headlines:
                    continue

                # Yeni haberleri filtrele (hash ile dedup)
                new_headlines = []
                for h in headlines:
                    h_hash = hashlib.md5(h["title"].encode()).hexdigest()
                    if h_hash not in self._seen_hashes:
                        self._seen_hashes.add(h_hash)
                        new_headlines.append(h)

                # Çok büyüyünce temizle
                if len(self._seen_hashes) > 1000:
                    self._seen_hashes = set(list(self._seen_hashes)[-500:])

                if not new_headlines:
                    continue

                # Trading-relevant filtrele
                relevant = [h for h in new_headlines
                           if any(kw in h["title"].lower() for kw in TRADING_KEYWORDS)]

                if not relevant:
                    # Relevant değilse de en son 3 haberi al (genel sentiment için)
                    relevant = new_headlines[:3]

                logger.info(f"[News] {symbol}: {len(new_headlines)} yeni, {len(relevant)} relevant")

                # Haiku ile analiz
                result = await self._analyze_headlines(symbol, relevant)
                if result:
                    all_new.append(result)
                    # Redis'e yaz (anlık dashboard için)
                    await redis.setex(
                        f"news:{symbol}",
                        3600,  # 1 saat TTL
                        json.dumps(result, default=str)
                    )
                    # DB'ye kaydet
                    db = get_db()
                    await db.news_analysis.update_one(
                        {"symbol": symbol},
                        {"$set": {**result, "created_at": datetime.utcnow()}},
                        upsert=True,
                    )

                await asyncio.sleep(0.5)

            except Exception as e:
                logger.error(f"[News] {symbol} poll hatası: {e}")

        # Tüm güncellenen haberleri Redis'e yaz (stream için)
        if all_new:
            existing_raw = await redis.get("futuragents:news_latest")
            existing = json.loads(existing_raw) if existing_raw else {}
            for item in all_new:
                existing[item["symbol"]] = item
            await redis.setex("futuragents:news_latest", 7200,
                             json.dumps(existing, default=str))

        return all_new

    async def analyze_all_coins(self) -> dict:
        """Tüm coinler için tam analiz (saatlik derin tarama)"""
        results = {}
        for symbol in COINS:
            try:
                headlines = await self._fetch_all_sources(symbol)
                result = await self._analyze_headlines(symbol, headlines[:15])
                if result:
                    results[symbol] = result
                await asyncio.sleep(1)
            except Exception as e:
                logger.warning(f"[News] {symbol}: {e}")
        return results

    async def _fetch_all_sources(self, symbol: str) -> list[dict]:
        """Tüm kaynaklardan haber başlıklarını paralel çek"""
        coin = COIN_MAP.get(symbol, {})
        results = await asyncio.gather(
            self._fetch_cryptocompare(coin.get("cc", "")),
            self._fetch_cryptopanic(coin.get("cp", "")),
            self._fetch_reddit(coin.get("name", "")),
            return_exceptions=True
        )
        all_headlines = []
        for r in results:
            if isinstance(r, list):
                all_headlines.extend(r)
        # Dedup by title
        seen = set()
        unique = []
        for h in all_headlines:
            t = h["title"].lower().strip()
            if t not in seen:
                seen.add(t)
                unique.append(h)
        return unique[:20]

    async def _fetch_cryptocompare(self, coin: str) -> list[dict]:
        """CryptoCompare — en güvenilir ücretsiz kripto haber API'si"""
        try:
            async with httpx.AsyncClient(timeout=8.0) as client:
                resp = await client.get(
                    "https://min-api.cryptocompare.com/data/v2/news/",
                    params={"categories": coin, "lang": "EN", "lTs": 0},
                    headers={"User-Agent": "FuturAgents/2.0"},
                )
                if resp.status_code != 200:
                    return []
                items = resp.json().get("Data", [])
                return [{"title": i.get("title", ""), "source": "CryptoCompare",
                         "url": i.get("url", ""), "published": i.get("published_on", 0)}
                        for i in items[:10] if i.get("title")]
        except Exception as e:
            logger.debug(f"CryptoCompare {coin}: {e}")
            return []

    async def _fetch_cryptopanic(self, coin: str) -> list[dict]:
        """CryptoPanic — public API, key gerektirmez"""
        try:
            async with httpx.AsyncClient(timeout=8.0) as client:
                resp = await client.get(
                    f"https://cryptopanic.com/api/v1/posts/",
                    params={"public": "true", "currencies": coin, "kind": "news"},
                    headers={"User-Agent": "FuturAgents/2.0"},
                )
                if resp.status_code != 200:
                    return []
                items = resp.json().get("results", [])
                return [{"title": i.get("title", ""), "source": "CryptoPanic",
                         "url": i.get("url", ""), "published": 0}
                        for i in items[:8] if i.get("title")]
        except Exception as e:
            logger.debug(f"CryptoPanic {coin}: {e}")
            return []

    async def _fetch_reddit(self, coin_name: str) -> list[dict]:
        """Reddit r/cryptocurrency — anlık sosyal sentiment"""
        try:
            async with httpx.AsyncClient(timeout=8.0) as client:
                resp = await client.get(
                    f"https://www.reddit.com/r/cryptocurrency/search.json",
                    params={"q": coin_name, "sort": "new", "limit": 5, "t": "day"},
                    headers={"User-Agent": "FuturAgents/2.0 (trading bot)"},
                )
                if resp.status_code != 200:
                    return []
                posts = resp.json().get("data", {}).get("children", [])
                return [{"title": f"[Reddit] {p['data'].get('title', '')}",
                         "source": "Reddit", "url": "", "published": 0}
                        for p in posts[:5] if p.get("data", {}).get("title")]
        except Exception as e:
            logger.debug(f"Reddit {coin_name}: {e}")
            return []

    async def _analyze_headlines(self, symbol: str, headlines: list[dict]) -> Optional[dict]:
        if not headlines:
            return None
        coin = COIN_MAP.get(symbol, {}).get("sym", symbol.replace("USDT", ""))
        titles = "\n".join([f"- {h['title']}" for h in headlines[:12]])
        user_prompt = f"""
{coin} için son haberler:
{titles}

JSON yanıt (kesinlikle sadece JSON):
{{
  "overall_sentiment": "BULLISH" | "BEARISH" | "NEUTRAL",
  "sentiment_score": -100 ile 100 arası sayı,
  "impact_level": "HIGH" | "MEDIUM" | "LOW",
  "key_events": ["olay1", "olay2"],
  "trading_action": "long_signal" | "short_signal" | "avoid" | "watch",
  "summary": "2 cümle özet",
  "risk_factors": ["risk1"]
}}"""
        try:
            result = await self.llm.complete_json(
                system=SYSTEM_PROMPT,
                user=user_prompt,
                model_tier="fast",  # Haiku
                max_tokens=400,
            )
            result["symbol"] = symbol
            result["headlines_count"] = len(headlines)
            result["headlines"] = [h["title"] for h in headlines[:5]]
            result["analyzed_at"] = datetime.utcnow().isoformat()
            return result
        except Exception as e:
            logger.error(f"[News] LLM analizi {symbol}: {e}")
            return None
