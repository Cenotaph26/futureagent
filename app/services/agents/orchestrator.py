"""
FuturAgents — Orchestrator v3
- Duplicate pozisyon koruması
- Dinamik kaldıraç (confidence bazlı 3x-15x)
- Dinamik pozisyon büyüklüğü (fırsat kalitesine göre)
- Stop/TP her zaman ATR bazlı, LLM müdahalesi yok
"""
import asyncio
import logging
from datetime import datetime, timedelta

from app.core.config import settings
from app.services.agents.technical_agent import TechnicalAnalysisAgent
from app.services.agents.sentiment_agent import SentimentAnalysisAgent
from app.services.agents.risk_agent import RiskManagementAgent
from app.services.memory.market_memory import MarketMemory
from app.services.binance.client import get_binance_client
from app.services.llm.service import get_llm_service
from app.db.database import get_db

logger = logging.getLogger(__name__)

ORCHESTRATOR_SYSTEM = """Sen FuturAgents'in baş trading ajanısın. Kripto futures piyasasında çalışıyorsun.

Karar kriterlerin (sırasıyla):
1. 3 TF'den 2+ aynı yön → EXECUTE için gerekli minimum
2. 3/3 TF aynı yön + güçlü indikatörler → yüksek güven (80+)
3. RSI aşırı bölge (>75 veya <25) + trend → fırsat
4. Anomali yön destekliyorsa → güven +10
5. Haber negatifse ve LONG → bekle
6. R/R minimum 1:2 olmalı

EXECUTE kararında sadece decision, direction, confidence, reasoning, key_risks ver.
Stop/TP otomatik hesaplanıyor. JSON yanıt ver."""


def _calc_dynamic_leverage(confidence: int, signal_strength: float, rsi: float = 50) -> int:
    """
    Confidence + sinyal gücü + RSI bazlı dinamik kaldıraç:
    - 55-64 güven: 3x (düşük güven, temkinli)
    - 65-74 güven: 5x (orta güven)
    - 75-84 güven: 8x (güçlü sinyal)
    - 85+ güven + 3/3 TF: 12x (çok güçlü fırsat)
    - 90+ güven + 3/3 TF + ideal RSI: 15x (nadir, yüksek fırsat)
    """
    base = 3
    if confidence >= 90 and signal_strength >= 1.0 and 35 <= rsi <= 65:
        base = 15  # Mükemmel fırsat: çok güçlü + ideal RSI bölgesi
    elif confidence >= 85 and signal_strength >= 1.0:
        base = 12  # Güçlü fırsat
    elif confidence >= 78:
        base = 8
    elif confidence >= 68:
        base = 5
    else:
        base = 3
    return min(base, settings.MAX_LEVERAGE)


def _calc_dynamic_position(available_usdt: float, confidence: int,
                            leverage: int, entry_price: float,
                            atr: float) -> tuple[float, float]:
    """
    Dinamik pozisyon büyüklüğü:
    - Base risk: bakiyenin %1-5'i (confidence'a göre)
    - Yüksek güven = daha büyük pozisyon
    Returns: (quantity, position_usdt)
    """
    # Risk yüzdesi: %1 (düşük güven) → %5 (yüksek güven)
    risk_pct = 0.01 + (confidence - 55) / (100 - 55) * 0.04
    risk_pct = max(0.01, min(0.05, risk_pct))

    risk_usdt = available_usdt * risk_pct
    stop_dist = 1.5 * atr / entry_price  # % cinsinden

    if stop_dist <= 0:
        stop_dist = 0.01

    # Kelly bazlı pozisyon
    position_usdt = min(
        risk_usdt / stop_dist,
        settings.MAX_POSITION_SIZE_USDT * (leverage / 3),  # kaldıraçla ölçekle
        available_usdt * 0.5,  # max bakiyenin %50'si
    )
    position_usdt = max(position_usdt, 10.0)  # minimum 10 USDT

    quantity = (position_usdt * leverage) / entry_price
    return round(quantity, 4), round(position_usdt, 2)


class OrchestratorAgent:

    def __init__(self):
        self.tech_agent      = TechnicalAnalysisAgent()
        self.sentiment_agent = SentimentAnalysisAgent()
        self.risk_agent      = RiskManagementAgent()
        self.memory          = MarketMemory()
        self.binance         = get_binance_client()
        self.llm             = get_llm_service()

    async def analyze_and_decide(self, symbol: str, interval: str = "1h",
                                  auto_execute: bool = False, user_id: str = None) -> dict:
        logger.info(f"[Orchestrator] {symbol} analiz başlıyor")
        start = datetime.utcnow()

        # Paralel veri toplama
        tech_15m, tech_1h, tech_4h, sentiment, memory_data = await asyncio.gather(
            self.tech_agent.analyze(symbol, "15m", limit=100),
            self.tech_agent.analyze(symbol, "1h",  limit=200),
            self.tech_agent.analyze(symbol, "4h",  limit=100),
            self.sentiment_agent.analyze(symbol),
            self.memory.get_coin_intelligence(symbol),
            return_exceptions=True,
        )

        # Hata toleransı
        def safe(val, default):
            return val if not isinstance(val, Exception) else default

        tech_1h  = safe(tech_1h,  {"llm_analysis": {"signal": "NEUTRAL", "confidence": 0}, "indicators": {}, "current_price": 0})
        tech_15m = safe(tech_15m, {"llm_analysis": {"signal": "NEUTRAL", "confidence": 0}})
        tech_4h  = safe(tech_4h,  {"llm_analysis": {"signal": "NEUTRAL", "confidence": 0}})
        sentiment = safe(sentiment, {"llm_analysis": {"overall_sentiment": "NEUTRAL"}})
        memory_data = safe(memory_data, {})

        indicators    = tech_1h.get("indicators", {})
        current_price = float(tech_1h.get("current_price", 0))
        atr           = float(indicators.get("atr_14", 0))
        pattern_score = await self.memory.get_pattern_score(symbol, indicators)

        # Risk agent (ATR bazlı SL/TP hesaplar)
        tech_signal = tech_1h.get("llm_analysis", {}).get("signal", "NEUTRAL")
        tech_conf   = tech_1h.get("llm_analysis", {}).get("confidence", 0)
        risk = None
        if tech_signal in ("LONG", "SHORT") and tech_conf >= 40 and current_price > 0 and atr > 0:
            risk = await self.risk_agent.evaluate(
                symbol=symbol, signal=tech_signal, entry_price=current_price,
                technical_atr=atr, technical_confidence=tech_conf)
            if isinstance(risk, Exception): risk = None

        # Anomaliler
        anomalies = await get_db().anomalies.find(
            {"symbol": symbol, "created_at": {"$gte": datetime.utcnow() - timedelta(hours=4)}}
        ).sort("created_at", -1).limit(5).to_list(5)

        # LLM sentezi
        final = await self._synthesize(
            symbol=symbol, tech_15m=tech_15m, tech_1h=tech_1h, tech_4h=tech_4h,
            sentiment=sentiment, risk=risk, anomalies=anomalies,
            memory=memory_data, pattern_score=pattern_score,
        )

        # Stop/TP ATR'den hesapla (LLM'e güvenme)
        if final.get("decision") == "EXECUTE" and current_price > 0 and atr > 0:
            direction = final.get("direction", "LONG")
            sl_mult, tp1_mult, tp2_mult = 1.5, 2.5, 5.0
            if direction == "LONG":
                final["stop_loss"]      = round(current_price - atr * sl_mult, 4)
                final["take_profit_1"]  = round(current_price + atr * tp1_mult, 4)
                final["take_profit_2"]  = round(current_price + atr * tp2_mult, 4)
            else:
                final["stop_loss"]      = round(current_price + atr * sl_mult, 4)
                final["take_profit_1"]  = round(current_price - atr * tp1_mult, 4)
                final["take_profit_2"]  = round(current_price - atr * tp2_mult, 4)
            final["entry_price"] = current_price

            # TF konsensüs gücü (0-1)
            sigs = [tech_15m.get("llm_analysis",{}).get("signal","NEUTRAL"),
                    tech_1h.get("llm_analysis",{}).get("signal","NEUTRAL"),
                    tech_4h.get("llm_analysis",{}).get("signal","NEUTRAL")]
            tf_match = sum(1 for s in sigs if s == direction) / 3.0

            # Dinamik kaldıraç ve pozisyon
            conf = final.get("confidence", 55)
            rsi_val = float(indicators.get("rsi_14", 50) or 50)
            leverage = _calc_dynamic_leverage(conf, tf_match, rsi_val)
            final["leverage"] = leverage
            final["tf_strength"] = round(tf_match, 2)
            logger.info(f"[Orchestrator] {symbol} kaldıraç: {leverage}x (conf={conf}, tf={tf_match:.1f}, rsi={rsi_val:.1f})")

            # Bakiye çek
            try:
                bal = await self.risk_agent._get_usdt_balance()
                avail = bal["available"]
            except Exception:
                avail = settings.MAX_POSITION_SIZE_USDT

            qty, pos_usdt = _calc_dynamic_position(avail, conf, leverage, current_price, atr)
            final["quantity"]     = qty
            final["position_usdt"] = pos_usdt

        # Pozisyon aç
        execution = None
        if auto_execute and final.get("decision") == "EXECUTE":
            execution = await self._execute_trade(symbol, final)
            if execution and execution.get("executed"):
                logger.info(f"[Orchestrator] ✅ {symbol} {final.get('direction')} açıldı — lev={final.get('leverage')}x qty={final.get('quantity')}")
            else:
                logger.warning(f"[Orchestrator] ❌ {symbol} açılamadı: {execution}")

        elapsed = (datetime.utcnow() - start).total_seconds()
        actually_executed = bool(execution and execution.get("executed"))
        report = {
            "symbol": symbol, "interval": interval, "user_id": user_id,
            "technical_15m": tech_15m, "technical_1h": tech_1h, "technical_4h": tech_4h,
            "sentiment": sentiment, "risk": risk,
            "anomalies": [{"type": a["type"], "severity": a.get("severity")} for a in anomalies],
            "memory": memory_data, "pattern_score": pattern_score,
            "final_decision": final, "execution": execution,
            "auto_executed": actually_executed,
            "elapsed_seconds": round(elapsed, 1),
            "created_at": datetime.utcnow(),
        }
        await self._save_analysis(report)
        return report

    async def _synthesize(self, symbol, tech_15m, tech_1h, tech_4h,
                           sentiment, risk, anomalies, memory, pattern_score) -> dict:
        t15  = tech_15m.get("llm_analysis", {})
        t1h  = tech_1h.get("llm_analysis", {})
        t4h  = tech_4h.get("llm_analysis", {})
        sent = sentiment.get("llm_analysis", {})
        ind  = tech_1h.get("indicators", {})

        sigs = [s.get("signal", "NEUTRAL") for s in [t15, t1h, t4h]]
        lc, sc = sigs.count("LONG"), sigs.count("SHORT")
        anom_text = "\n".join([f"  - {a['type']} ({a.get('severity','?')})" for a in anomalies]) or "  Yok"

        prompt = f"""
=== {symbol} ANALİZ ===
Fiyat: {tech_1h.get('current_price', '?')}

[3 TF KONSENSÜSü: {lc}/3 LONG, {sc}/3 SHORT]
15m: {t15.get('signal','?')} ({t15.get('confidence',0)}/100)
1h:  {t1h.get('signal','?')} ({t1h.get('confidence',0)}/100) — {t1h.get('reasoning','')[:100]}
4h:  {t4h.get('signal','?')} ({t4h.get('confidence',0)}/100)

[TEKNİK]
RSI: {ind.get('rsi_14','?')} | EMA: {ind.get('ema_trend','?')} | MACD: {ind.get('macd_cross','?')}
BB%B: {ind.get('bb_pct','?')} | ATR%: {ind.get('atr_pct','?')}%

[DUYARLILIK] {sent.get('overall_sentiment','?')} ({sent.get('sentiment_score',0)})
{sent.get('reasoning','')[:80]}

[ANOMALİLER]
{anom_text}

[HAFIZA ({memory.get('total_signals_30d',0)} sinyal, WR:{memory.get('win_rate_30d','?')}%)]
Pattern skoru: {pattern_score} | İyi saat: {memory.get('is_good_hour','?')}
Haber: {memory.get('news_sentiment','?')}

[RİSK]
{'Onay: '+str(risk.get('llm_assessment',{}).get('approved','?'))+' | Sizing: '+str(risk.get('sizing',{}).get('position_usdt','?'))+' USDT' if risk else 'Risk hesaplanamadı'}

Karar:
{{
  "decision": "EXECUTE"|"WAIT"|"ABORT",
  "direction": "LONG"|"SHORT"|null,
  "confidence": 0-100,
  "tf_consensus": "{lc}/3 LONG",
  "reasoning": "kapsamlı türkçe gerekçe",
  "key_risks": ["risk1","risk2"],
  "memory_influenced": true|false,
  "anomaly_influenced": true|false
}}"""

        try:
            result = await self.llm.complete_json(
                system=ORCHESTRATOR_SYSTEM, user=prompt,
                model_tier="analyst", max_tokens=600)
            return result
        except Exception as e:
            logger.error(f"Orchestrator LLM hatası: {e}")
            return {"decision": "ABORT", "reasoning": str(e), "confidence": 0}

    async def _execute_trade(self, symbol: str, decision: dict) -> dict:
        try:
            direction = decision.get("direction")
            quantity  = float(decision.get("quantity", 0))
            leverage  = int(decision.get("leverage", settings.DEFAULT_LEVERAGE))
            stop_loss = float(decision.get("stop_loss", 0))
            tp1       = float(decision.get("take_profit_1", 0))
            entry     = float(decision.get("entry_price", 0))

            if not direction:
                return {"error": "direction eksik", "executed": False}
            if quantity <= 0:
                return {"error": f"geçersiz quantity: {quantity}", "executed": False}
            if stop_loss <= 0:
                return {"error": "stop_loss eksik", "executed": False}

            # ── DUPLICATE POZISYON KORUMASI ──────────────────────────────────
            open_pos = await self.binance.get_positions()
            for p in open_pos:
                if p.get("symbol") == symbol and abs(float(p.get("positionAmt", 0))) > 0:
                    logger.warning(f"[Execute] {symbol} için zaten açık pozisyon var — atlandı")
                    return {"error": "already_open", "executed": False, "skipped": True}

            # MAX_OPEN_POSITIONS kontrolü
            active = [p for p in open_pos if abs(float(p.get("positionAmt", 0))) > 0]
            if len(active) >= settings.MAX_OPEN_POSITIONS:
                logger.warning(f"[Execute] Max pozisyon sayısına ulaşıldı ({settings.MAX_OPEN_POSITIONS})")
                return {"error": "max_positions_reached", "executed": False}

            logger.info(f"[Execute] {symbol} {direction} qty={quantity} lev={leverage}x sl={stop_loss} tp={tp1}")

            # Kaldıraç ve margin
            await self.binance.set_leverage(symbol, leverage)
            try:
                await self.binance.set_margin_type(symbol, "ISOLATED")
            except Exception as e:
                logger.debug(f"[Execute] margin type: {e}")

            # Ana emir
            side = "BUY" if direction == "LONG" else "SELL"
            order = await self.binance.place_market_order(symbol, side, quantity)
            results = {"main_order": order, "executed": True, "direction": direction,
                       "quantity": quantity, "leverage": leverage}

            # SL/TP doğrulama ve yerleştirme
            sl_side = "SELL" if direction == "LONG" else "BUY"

            # SL sanity check
            if direction == "LONG" and stop_loss >= entry:
                stop_loss = round(entry * 0.97, 4)
            elif direction == "SHORT" and stop_loss <= entry:
                stop_loss = round(entry * 1.03, 4)

            try:
                results["stop_loss_order"] = await self.binance.place_stop_order(
                    symbol, sl_side, quantity, stop_loss, "STOP_MARKET")
            except Exception as e:
                results["stop_loss_error"] = str(e)
                logger.warning(f"[Execute] SL hata: {e}")

            if tp1 > 0:
                if direction == "LONG" and tp1 <= entry:
                    tp1 = round(entry * 1.02, 4)
                elif direction == "SHORT" and tp1 >= entry:
                    tp1 = round(entry * 0.98, 4)
                try:
                    results["take_profit_order"] = await self.binance.place_stop_order(
                        symbol, sl_side, quantity, tp1, "TAKE_PROFIT_MARKET")
                except Exception as e:
                    results["take_profit_error"] = str(e)
                    logger.warning(f"[Execute] TP hata: {e}")

            # Kaydı DB'ye yaz
            await get_db().trades.insert_one({
                "symbol": symbol, "direction": direction,
                "entry_price": entry, "quantity": quantity, "leverage": leverage,
                "stop_loss": stop_loss, "take_profit_1": tp1,
                "order_id": order.get("orderId"),
                "status": "open", "created_at": datetime.utcnow(),
            })
            return results

        except Exception as e:
            logger.error(f"[Execute] {symbol} hata: {e}", exc_info=True)
            return {"error": str(e), "executed": False}

    async def _save_analysis(self, report):
        try:
            save = {k: v for k, v in report.items() if k not in ("technical_15m", "technical_4h")}
            await get_db().analyses.insert_one(save)
        except Exception as e:
            logger.error(f"Analiz kaydetme hatası: {e}")
