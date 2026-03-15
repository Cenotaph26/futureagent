"""
FuturAgents — Mega Orchestrator v2
$30/ay bütçe · 5 coin · Maksimum zeka

Her analiz şunları bilir:
  1. Teknik analiz (3 zaman dilimi: 15m + 1h + 4h)
  2. Sentiment analizi (Haiku)
  3. Risk değerlendirmesi (Haiku)
  4. Anomali raporu (LLM yok — $0)
  5. Haber sentimenti (Haiku, 30dk cache)
  6. Hafıza — geçmişte bu coin/pattern ne performans gösterdi?
  7. Saat bazlı optimizasyon — şu an iyi saat mi?
  8. Orchestrator final karar (Sonnet)
"""
import asyncio
import logging
from datetime import datetime

from app.core.config import settings
from app.services.agents.technical_agent import TechnicalAnalysisAgent
from app.services.agents.sentiment_agent import SentimentAnalysisAgent
from app.services.agents.risk_agent import RiskManagementAgent
from app.services.memory.market_memory import MarketMemory
from app.services.binance.client import get_binance_client
from app.services.llm.service import get_llm_service
from app.db.database import get_db

logger = logging.getLogger(__name__)

ORCHESTRATOR_SYSTEM = """Sen FuturAgents'in baş trading ajanısın.

Sana verilecek bilgiler:
- 3 zaman dilimi teknik analiz (15m, 1h, 4h)
- Piyasa duyarlılığı (funding, L/S, tasfiyeler)
- Risk değerlendirmesi
- Anomali raporu (anormal hacim, funding, fiyat hareketleri)
- Haber sentimenti
- Geçmiş performans (bu coinде bu pattern kaç kez kazandı?)
- Şu an iyi saat mi? (geçmişte bu saatte win rate neydi?)

Karar kriterlerin:
1. 3 TF'den en az 2'si aynı yönde → daha güvenilir
2. Anomali varsa ve yönü destekliyorsa → güven artır
3. Haberler olumsuzsa → long girmeyi ertele
4. Geçmişte bu pattern başarısızsa → güveni düşür
5. Kötü saat ise → bekle
6. Risk/ödül minimum 1:2 olmalı

EXECUTE kararında tam parametreler ver.
Yanıt kesinlikle JSON olmalı."""


class OrchestratorAgent:

    def __init__(self):
        self.tech_agent      = TechnicalAnalysisAgent()
        self.sentiment_agent = SentimentAnalysisAgent()
        self.risk_agent      = RiskManagementAgent()
        self.memory          = MarketMemory()
        self.binance         = get_binance_client()
        self.llm             = get_llm_service()

    async def analyze_and_decide(
        self,
        symbol: str,
        interval: str = "1h",
        auto_execute: bool = False,
        user_id: str = None,
    ) -> dict:
        logger.info(f"[Orchestrator v2] {symbol} tam analiz başlıyor")
        start = datetime.utcnow()

        # ── Paralel veri toplama ──────────────────────────────────────────────
        # 3 zaman dilimi teknik analiz aynı anda
        tech_15m_task = self.tech_agent.analyze(symbol, "15m", limit=100)
        tech_1h_task  = self.tech_agent.analyze(symbol, "1h",  limit=200)
        tech_4h_task  = self.tech_agent.analyze(symbol, "4h",  limit=100)

        tech_15m, tech_1h, tech_4h, sentiment, memory_data = await asyncio.gather(
            tech_15m_task, tech_1h_task, tech_4h_task,
            self.sentiment_agent.analyze(symbol),
            self.memory.get_coin_intelligence(symbol),
            return_exceptions=True,
        )

        # Hata toleransı
        for name, val in [("tech_15m", tech_15m), ("tech_1h", tech_1h),
                          ("tech_4h", tech_4h), ("sentiment", sentiment)]:
            if isinstance(val, Exception):
                logger.warning(f"{name} hatası: {val}")

        tech_1h = tech_1h if not isinstance(tech_1h, Exception) else {"llm_analysis": {"signal": "NEUTRAL", "confidence": 0}, "indicators": {}, "current_price": 0}
        tech_15m = tech_15m if not isinstance(tech_15m, Exception) else {"llm_analysis": {"signal": "NEUTRAL", "confidence": 0}}
        tech_4h  = tech_4h  if not isinstance(tech_4h,  Exception) else {"llm_analysis": {"signal": "NEUTRAL", "confidence": 0}}
        sentiment = sentiment if not isinstance(sentiment, Exception) else {"llm_analysis": {"overall_sentiment": "NEUTRAL"}}
        if isinstance(memory_data, Exception):
            memory_data = {}

        # Hafızadan pattern skoru
        indicators = tech_1h.get("indicators", {})
        pattern_score = await self.memory.get_pattern_score(symbol, indicators)

        # Mevcut fiyat ve risk
        current_price = tech_1h.get("current_price", 0)
        atr = indicators.get("atr_14", 0)
        tech_signal = tech_1h.get("llm_analysis", {}).get("signal", "NEUTRAL")
        tech_conf   = tech_1h.get("llm_analysis", {}).get("confidence", 0)

        risk = None
        if tech_signal in ("LONG", "SHORT") and tech_conf >= 45 and current_price > 0 and atr > 0:
            risk = await self.risk_agent.evaluate(
                symbol=symbol, signal=tech_signal,
                entry_price=current_price, technical_atr=atr,
                technical_confidence=tech_conf,
            )
            if isinstance(risk, Exception):
                risk = None

        # Anomali raporu (DB'den — detector ayrı çalışıyor)
        from datetime import timedelta
        anomalies = await get_db().anomalies.find(
            {"symbol": symbol, "created_at": {"$gte": datetime.utcnow() - timedelta(hours=4)}}
        ).sort("created_at", -1).limit(5).to_list(5)

        # ── Orchestrator sentezi ──────────────────────────────────────────────
        final = await self._synthesize(
            symbol=symbol,
            tech_15m=tech_15m, tech_1h=tech_1h, tech_4h=tech_4h,
            sentiment=sentiment, risk=risk,
            anomalies=anomalies, memory=memory_data, pattern_score=pattern_score,
        )

        # ── Opsiyonel işlem ───────────────────────────────────────────────────
        execution = None
        if auto_execute and final.get("decision") == "EXECUTE":
            execution = await self._execute_trade(symbol, final, risk)

        elapsed = (datetime.utcnow() - start).total_seconds()
        report = {
            "symbol": symbol, "interval": interval, "user_id": user_id,
            "technical_15m": tech_15m, "technical_1h": tech_1h, "technical_4h": tech_4h,
            "sentiment": sentiment, "risk": risk,
            "anomalies": [{"type": a["type"], "severity": a.get("severity")} for a in anomalies],
            "memory": memory_data, "pattern_score": pattern_score,
            "final_decision": final,
            "execution": execution,
            "auto_executed": auto_execute and execution is not None,
            "elapsed_seconds": round(elapsed, 1),
            "created_at": datetime.utcnow(),
        }
        await self._save_analysis(report)
        logger.info(f"[Orchestrator v2] {symbol}: {final.get('decision')} ({elapsed:.1f}s)")
        return report

    async def _synthesize(self, symbol, tech_15m, tech_1h, tech_4h,
                           sentiment, risk, anomalies, memory, pattern_score) -> dict:

        t15 = tech_15m.get("llm_analysis", {})
        t1h = tech_1h.get("llm_analysis", {})
        t4h = tech_4h.get("llm_analysis", {})
        sent = sentiment.get("llm_analysis", {})
        ind  = tech_1h.get("indicators", {})

        # TF konsensüs sayımı
        signals = [s.get("signal", "NEUTRAL") for s in [t15, t1h, t4h]]
        long_count  = signals.count("LONG")
        short_count = signals.count("SHORT")
        tf_consensus = f"{long_count}/3 LONG, {short_count}/3 SHORT"

        anom_text = "\n".join([f"  - {a['type']} ({a.get('severity', '?')} şiddet)" for a in anomalies]) or "  Yok"

        user_prompt = f"""
=== {symbol} TAM ANALİZ ===

[3 ZAMAN DİLİMİ KONSENSÜSü: {tf_consensus}]
15m: {t15.get('signal','?')} ({t15.get('confidence',0)}/100)
1h:  {t1h.get('signal','?')} ({t1h.get('confidence',0)}/100) | Gerekçe: {t1h.get('reasoning','')[:80]}
4h:  {t4h.get('signal','?')} ({t4h.get('confidence',0)}/100)

[TEKNİK İNDİKATÖRLER (1h)]
RSI: {ind.get('rsi_14','?')} ({ind.get('rsi_signal','?')})
EMA Trend: {ind.get('ema_trend','?')}
MACD Cross: {ind.get('macd_cross','?')}
BB %B: {ind.get('bb_pct','?')}
Funding: {tech_1h.get('funding_rate',0):.4%}
ATR: {ind.get('atr_pct','?')}%

[DUYARLILIK]
Genel: {sent.get('overall_sentiment','?')} (skor: {sent.get('sentiment_score',0)})
Squeeze riski: {sent.get('squeeze_risk',{})}
Gerekçe: {sent.get('reasoning','')[:80]}

[ANOMALİLER (son 4 saat)]
{anom_text}

[HAFIZA — Bu coin geçmişte nasıl performans gösterdi?]
Son 30 gün sinyal sayısı: {memory.get('total_signals_30d', 'Veri yok')}
Son 30 gün win rate: {memory.get('win_rate_30d', 'Veri yok')}%
Ortalama PnL: {memory.get('avg_pnl_30d', 'Veri yok')}%
Bu pattern skoru: {pattern_score} (0=kötü, 1=iyi)
İyi saatler (UTC): {memory.get('best_hours_utc', [])}
Şu an iyi saat mi: {memory.get('is_good_hour', '?')}
Son anomaliler: {memory.get('recent_anomalies', [])}
Haber sentimenti: {memory.get('news_sentiment', '?')}
Haber özeti: {memory.get('news_key_events', [])}

[RİSK DEĞERLENDİRMESİ]
{f"Onaylandı: {risk.get('llm_assessment',{}).get('approved','?')}, Miktar: {risk.get('sizing',{}).get('quantity','?')}, Kaldıraç: {risk.get('sizing',{}).get('leverage','?')}x" if risk else "Risk değerlendirmesi yapılmadı"}

Tüm bu bilgileri sentezle. Hafıza ve anomali verileri kararında önemli rol oynasın.

{{
  "decision": "EXECUTE"|"WAIT"|"ABORT",
  "direction": "LONG"|"SHORT"|null,
  "entry_price": sayı,
  "quantity": sayı,
  "leverage": sayı,
  "stop_loss": sayı,
  "take_profit_1": sayı,
  "take_profit_2": sayı,
  "confidence": 0-100,
  "tf_consensus": "{tf_consensus}",
  "reasoning": "kapsamlı gerekçe (hafıza ve anomali dahil)",
  "key_risks": [],
  "memory_influenced": true|false,
  "anomaly_influenced": true|false,
  "wait_until": "eğer WAIT ise"
}}"""

        try:
            return await self.llm.complete_json(
                system=ORCHESTRATOR_SYSTEM,
                user=user_prompt,
                model_tier="analyst",   # Sonnet — Opus değil, dengeli
                max_tokens=1024,
            )
        except Exception as e:
            logger.error(f"Orchestrator LLM hatası: {e}")
            return {"decision": "ABORT", "reasoning": str(e), "confidence": 0}

    async def _execute_trade(self, symbol, decision, risk):
        try:
            direction = decision.get("direction")
            quantity  = decision.get("quantity") or (risk.get("sizing", {}).get("quantity") if risk else None)
            leverage  = decision.get("leverage", settings.DEFAULT_LEVERAGE)
            stop_loss = decision.get("stop_loss")
            tp1       = decision.get("take_profit_1")
            if not all([direction, quantity, stop_loss]):
                return {"error": "Eksik parametre", "executed": False}
            await self.binance.set_leverage(symbol, leverage)
            await self.binance.set_margin_type(symbol, "ISOLATED")
            side = "BUY" if direction == "LONG" else "SELL"
            order = await self.binance.place_market_order(symbol, side, quantity)
            results = {"main_order": order, "executed": True}
            sl_side = "SELL" if direction == "LONG" else "BUY"
            try:
                results["stop_loss_order"] = await self.binance.place_stop_order(
                    symbol, sl_side, quantity, stop_loss, "STOP_MARKET")
            except Exception as e:
                results["stop_loss_error"] = str(e)
            if tp1:
                try:
                    results["take_profit_order"] = await self.binance.place_stop_order(
                        symbol, sl_side, quantity, tp1, "TAKE_PROFIT_MARKET")
                except Exception as e:
                    results["take_profit_error"] = str(e)
            return results
        except Exception as e:
            return {"error": str(e), "executed": False}

    async def _save_analysis(self, report):
        try:
            db = get_db()
            save = {k: v for k, v in report.items()
                    if k not in ("technical_15m", "technical_4h")}  # Büyük alanları çıkar
            await db.analyses.insert_one(save)
        except Exception as e:
            logger.error(f"Analiz kaydetme hatası: {e}")
