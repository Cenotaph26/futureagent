"""
FuturAgents — Orchestrator Agent (Baş Agent)
Claude Opus kullanır — en pahalı ama en akıllı model.
Tüm sub-agent raporlarını sentezler ve nihai kararı verir:
  EXECUTE | WAIT | ABORT

Aynı zamanda mevcut pozisyonları yönetir (stop revize, TP aktif, çıkış).
"""
import asyncio
import logging
from datetime import datetime
from typing import Any

from app.core.config import settings
from app.services.agents.technical_agent import TechnicalAnalysisAgent
from app.services.agents.sentiment_agent import SentimentAnalysisAgent
from app.services.agents.risk_agent import RiskManagementAgent
from app.services.binance.client import get_binance_client
from app.services.llm.service import get_llm_service
from app.db.database import get_db

logger = logging.getLogger(__name__)

ORCHESTRATOR_SYSTEM = """Sen FuturAgents'in baş trading ajanısın. Claude Opus modeliyle çalışıyorsun.

Görevin: Teknik analiz, duyarlılık analizi ve risk yönetimi raporlarını sentezleyerek
KESIN ve ACTIONABLE bir karar vermek.

Karar kriterlerin:
1. ≥2 agent aynı yönde sinyal vermeli
2. Risk/Ödül oranı minimum 1:2 olmalı
3. Portfolio riski maksimum %80 exposure
4. Funding rate aşırı pozitif veya negatifse dikkatli ol
5. Piyasa volatilitesi EXTREME ise küçük pozisyon veya geç

EXECUTE kararı verirsen tam parametreleri belirt (entry, stop, TP, quantity, leverage).
WAIT kararı verirsen ne zaman tekrar değerlendireceğini belirt.
ABORT kararı verirsen kesin sebebi açıkla.

Yanıtın JSON formatında olmalı."""


class OrchestratorAgent:
    """
    Multi-agent orkestratörü.
    Paralel sub-agent analizi → sentez → final karar → (opsiyonel) işlem.
    """

    def __init__(self):
        self.tech_agent = TechnicalAnalysisAgent()
        self.sentiment_agent = SentimentAnalysisAgent()
        self.risk_agent = RiskManagementAgent()
        self.binance = get_binance_client()
        self.llm = get_llm_service()

    async def analyze_and_decide(
        self,
        symbol: str,
        interval: str = "1h",
        auto_execute: bool = False,
        user_id: str = None,
    ) -> dict:
        """
        Tam analiz döngüsü:
        1. Tüm agentları paralel çalıştır
        2. Orchestrator ile sentez yap
        3. auto_execute=True ise işlemi gerçekleştir
        """
        logger.info(f"[Orchestrator] {symbol} analiz başlıyor (auto_execute={auto_execute})")
        start_time = datetime.utcnow()

        # ── Aşama 1: Paralel Sub-Agent Analizi ───────────────────────
        tech_task = self.tech_agent.analyze(symbol, interval)
        sentiment_task = self.sentiment_agent.analyze(symbol)

        tech_result, sentiment_result = await asyncio.gather(
            tech_task, sentiment_task, return_exceptions=True
        )

        # Hata kontrolü
        if isinstance(tech_result, Exception):
            logger.error(f"Teknik analiz hatası: {tech_result}")
            tech_result = {"error": str(tech_result), "llm_analysis": {"signal": "NEUTRAL", "confidence": 0}}
        if isinstance(sentiment_result, Exception):
            logger.error(f"Duyarlılık hatası: {sentiment_result}")
            sentiment_result = {"error": str(sentiment_result), "llm_analysis": {"overall_sentiment": "NEUTRAL"}}

        # ── Aşama 2: İlk Sinyal Değerlendirmesi ──────────────────────
        tech_signal = tech_result.get("llm_analysis", {}).get("signal", "NEUTRAL")
        tech_confidence = tech_result.get("llm_analysis", {}).get("confidence", 0)
        current_price = tech_result.get("current_price", 0)
        atr = tech_result.get("indicators", {}).get("atr_14", 0)

        # Sinyal varsa risk değerlendirmesi yap
        risk_result = None
        if tech_signal in ("LONG", "SHORT") and tech_confidence >= 50:
            risk_result = await self.risk_agent.evaluate(
                symbol=symbol,
                signal=tech_signal,
                entry_price=current_price,
                technical_atr=atr,
                technical_confidence=tech_confidence,
                user_id=user_id,
            )
        
        if isinstance(risk_result, Exception):
            risk_result = None

        # ── Aşama 3: Orchestrator Sentezi ────────────────────────────
        final_decision = await self._synthesize(
            symbol=symbol,
            tech=tech_result,
            sentiment=sentiment_result,
            risk=risk_result,
        )

        # ── Aşama 4: Opsiyonel İşlem Gerçekleştirme ──────────────────
        execution_result = None
        if auto_execute and final_decision.get("decision") == "EXECUTE":
            execution_result = await self._execute_trade(symbol, final_decision, risk_result)

        # ── Sonucu DB'ye kaydet ───────────────────────────────────────
        elapsed = (datetime.utcnow() - start_time).total_seconds()
        report = {
            "symbol": symbol,
            "interval": interval,
            "user_id": user_id,
            "technical": tech_result,
            "sentiment": sentiment_result,
            "risk": risk_result,
            "final_decision": final_decision,
            "execution": execution_result,
            "auto_executed": auto_execute and execution_result is not None,
            "elapsed_seconds": round(elapsed, 1),
            "created_at": datetime.utcnow(),
        }

        await self._save_analysis(report)

        logger.info(
            f"[Orchestrator] {symbol} tamamlandı: {final_decision.get('decision')} "
            f"({elapsed:.1f}s)"
        )
        return report

    async def _synthesize(
        self,
        symbol: str,
        tech: dict,
        sentiment: dict,
        risk: dict | None,
    ) -> dict:
        """Claude Opus ile nihai karar"""

        tech_llm = tech.get("llm_analysis", {})
        sent_llm = sentiment.get("llm_analysis", {})
        risk_llm = risk.get("llm_assessment", {}) if risk else {}

        user_prompt = f"""
=== {symbol} TAM ANALİZ RAPORU ===

[TEKNİK ANALİZ - {tech.get('interval', '1h')}]
Sinyal: {tech_llm.get('signal', 'NEUTRAL')} (Güven: {tech_llm.get('confidence', 0)}/100)
Gerekçe: {tech_llm.get('reasoning', 'N/A')}
Entry Zone: {tech_llm.get('entry_zone', {})}
Stop-Loss: {tech_llm.get('stop_loss', 'N/A')}
TP1: {tech_llm.get('take_profit_1', 'N/A')}
TP2: {tech_llm.get('take_profit_2', 'N/A')}
Risk Seviyesi: {tech_llm.get('risk_level', 'UNKNOWN')}
Temel İndikatörler:
  - RSI: {tech.get('indicators', {}).get('rsi_14', 'N/A')}
  - EMA Trend: {tech.get('indicators', {}).get('ema_trend', 'N/A')}
  - MACD Cross: {tech.get('indicators', {}).get('macd_cross', 'N/A')}
  - Funding Rate: {tech.get('funding_rate', 0):.4%}

[DUYARLILIK ANALİZİ]
Genel Duyarlılık: {sent_llm.get('overall_sentiment', 'NEUTRAL')}
Skor: {sent_llm.get('sentiment_score', 0)}/100
Squeeze Riski: {sent_llm.get('squeeze_risk', {})}
Kontrarian Sinyal: {sent_llm.get('contrarian_signal', 'HOLD')}
Gerekçe: {sent_llm.get('reasoning', 'N/A')}
Uyarılar: {sent_llm.get('warnings', [])}

[RİSK YÖNETİMİ]
{"Onaylı: " + str(risk_llm.get('approved', False)) if risk else "Risk değerlendirmesi yapılmadı (sinyal yok)"}
{f"Risk Skoru: {risk_llm.get('risk_score', 'N/A')}/100" if risk else ""}
{f"Miktar: {risk.get('sizing', {}).get('quantity', 'N/A')}" if risk else ""}
{f"Kaldıraç: {risk.get('sizing', {}).get('leverage', 'N/A')}x" if risk else ""}
{f"Stop-Loss: {risk.get('levels', {}).get('stop_loss', 'N/A')}" if risk else ""}
{f"TP1: {risk.get('levels', {}).get('take_profit_1', 'N/A')}" if risk else ""}
{f"Risk Uyarıları: {risk_llm.get('warnings', [])}" if risk else ""}

Tüm bu bilgileri sentezleyerek KESIN kararını ver:

{{
  "decision": "EXECUTE" | "WAIT" | "ABORT",
  "direction": "LONG" | "SHORT" | null,
  "entry_price": sayı,
  "quantity": sayı,
  "leverage": sayı,
  "stop_loss": sayı,
  "take_profit_1": sayı,
  "take_profit_2": sayı,
  "confidence": 0-100,
  "reasoning": "kapsamlı gerekçe",
  "key_risks": ["risk listesi"],
  "wait_until": "eğer WAIT ise ne zaman tekrar bak",
  "agent_consensus": {{"technical": "LONG/SHORT/NEUTRAL", "sentiment": "BULLISH/BEARISH/NEUTRAL", "risk": "APPROVED/REJECTED/NA"}}
}}
"""
        try:
            return await self.llm.complete_json(
                system=ORCHESTRATOR_SYSTEM,
                user=user_prompt,
                model_tier="orchestrator",  # Claude Opus
                max_tokens=2000,
            )
        except Exception as e:
            logger.error(f"Orchestrator LLM hatası: {e}")
            return {
                "decision": "ABORT",
                "reasoning": f"Orchestrator hatası: {e}",
                "confidence": 0,
            }

    async def _execute_trade(
        self, symbol: str, decision: dict, risk: dict | None
    ) -> dict:
        """Onaylanan trade'i Binance'e gönder"""
        try:
            direction = decision.get("direction")
            quantity = decision.get("quantity") or (risk.get("sizing", {}).get("quantity") if risk else None)
            leverage = decision.get("leverage", settings.DEFAULT_LEVERAGE)
            stop_loss = decision.get("stop_loss")
            tp1 = decision.get("take_profit_1")

            if not all([direction, quantity, stop_loss]):
                return {"error": "Eksik parametre", "executed": False}

            # Kaldıraç ayarla
            await self.binance.set_leverage(symbol, leverage)
            await self.binance.set_margin_type(symbol, "ISOLATED")

            # Ana emir
            side = "BUY" if direction == "LONG" else "SELL"
            order = await self.binance.place_market_order(symbol, side, quantity)

            results = {"main_order": order, "executed": True}

            # Stop-loss emri
            sl_side = "SELL" if direction == "LONG" else "BUY"
            try:
                sl_order = await self.binance.place_stop_order(
                    symbol, sl_side, quantity, stop_loss, "STOP_MARKET"
                )
                results["stop_loss_order"] = sl_order
            except Exception as e:
                logger.error(f"Stop-loss emri hatası: {e}")
                results["stop_loss_error"] = str(e)

            # Take-profit emri
            if tp1:
                try:
                    tp_order = await self.binance.place_stop_order(
                        symbol, sl_side, quantity, tp1, "TAKE_PROFIT_MARKET"
                    )
                    results["take_profit_order"] = tp_order
                except Exception as e:
                    logger.error(f"TP emri hatası: {e}")
                    results["take_profit_error"] = str(e)

            return results

        except Exception as e:
            logger.error(f"Trade execution hatası: {e}")
            return {"error": str(e), "executed": False}

    async def _save_analysis(self, report: dict) -> None:
        try:
            db = get_db()
            # datetime nesnelerini MongoDB için uygun tut
            await db.analyses.insert_one(report)
        except Exception as e:
            logger.error(f"Analiz kaydetme hatası: {e}")
