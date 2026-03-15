"""
FuturAgents — Analysis API Routes
Multi-agent analiz tetikleme ve SSE streaming sonuç.
"""
import asyncio
import json
import logging
from datetime import datetime, timedelta
from typing import AsyncIterator

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.services.agents.orchestrator import OrchestratorAgent
from app.services.agents.technical_agent import TechnicalAnalysisAgent
from app.services.agents.sentiment_agent import SentimentAnalysisAgent
from app.db.database import get_db

logger = logging.getLogger(__name__)
router = APIRouter()


class AnalysisRequest(BaseModel):
    symbol: str = Field(..., example="BTCUSDT", description="Binance Futures sembol")
    interval: str = Field("1h", example="1h", description="1m|5m|15m|1h|4h|1d")
    auto_execute: bool = Field(False, description="Onaylanan sinyali otomatik işleme al")


class QuickAnalysisRequest(BaseModel):
    symbol: str
    interval: str = "1h"


# ── Tam Multi-Agent Analiz (SSE Streaming) ────────────────────────────────────

@router.post("/run")
async def run_analysis(req: AnalysisRequest):
    """
    Tüm agentları çalıştır ve SSE stream ile adım adım sonuç gönder.
    Frontend bunu EventSource ile dinler.
    """
    async def event_stream() -> AsyncIterator[str]:
        try:
            yield _sse("status", {"message": f"🔍 {req.symbol} analizi başlatıldı", "step": 1, "total": 4})
            await asyncio.sleep(0.1)

            orchestrator = OrchestratorAgent()

            # Step 1: Teknik analiz
            yield _sse("status", {"message": "📊 Teknik analiz (Haiku)...", "step": 2, "total": 4})
            tech = await orchestrator.tech_agent.analyze(req.symbol, req.interval)
            yield _sse("technical", tech)

            # Step 2: Sentiment analiz
            yield _sse("status", {"message": "🧠 Duyarlılık analizi (Sonnet)...", "step": 3, "total": 4})
            sentiment = await orchestrator.sentiment_agent.analyze(req.symbol)
            yield _sse("sentiment", sentiment)

            yield _sse("status", {"message": "🎯 Orchestrator karar veriyor (Opus)...", "step": 4, "total": 4})
            report = await orchestrator.analyze_and_decide(
                symbol=req.symbol,
                interval=req.interval,
                auto_execute=req.auto_execute,
            )
            # auto_executed ve execution detayını decision event'e ekle
            decision_data = {**report["final_decision"]}
            decision_data["auto_executed"] = report.get("auto_executed", False)
            execution = report.get("execution") or {}
            if execution.get("error"):
                decision_data["execution_error"] = execution["error"]
            yield _sse("decision", decision_data)
            yield _sse("complete", {
                "report_id": str(report.get("_id", "")),
                "elapsed": report.get("elapsed_seconds"),
                "decision": report["final_decision"].get("decision"),
                "auto_executed": report.get("auto_executed"),
            })

        except Exception as e:
            logger.error(f"Analiz stream hatası: {e}")
            yield _sse("error", {"message": str(e)})

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n"


# ── Hızlı Tek-Agent Analizler ─────────────────────────────────────────────────

@router.post("/technical")
async def quick_technical(req: QuickAnalysisRequest):
    """Sadece teknik analiz — hızlı"""
    agent = TechnicalAnalysisAgent()
    return await agent.analyze(req.symbol, req.interval)


@router.post("/sentiment")
async def quick_sentiment(req: QuickAnalysisRequest):
    """Sadece duyarlılık analizi"""
    agent = SentimentAnalysisAgent()
    return await agent.analyze(req.symbol)


# ── Geçmiş Analizler ──────────────────────────────────────────────────────────

@router.get("/history")
async def get_analysis_history(
    symbol: str = Query(None),
    limit: int = Query(20, le=100),
    days: int = Query(7),
):
    """Son analizleri listele"""
    db = get_db()
    query = {"created_at": {"$gte": datetime.utcnow() - timedelta(days=days)}}
    if symbol:
        query["symbol"] = symbol.upper()

    cursor = db.analyses.find(
        query,
        {"technical_4h": 0},  # sadece 4h'ı exclude et, 1h ve sentiment kalsın
    ).sort("created_at", -1).limit(limit)

    results = []
    async for doc in cursor:
        doc["_id"] = str(doc["_id"])
        results.append(doc)
    return results


@router.get("/history/{analysis_id}")
async def get_analysis_detail(analysis_id: str):
    """Tekil analiz detayı"""
    from bson import ObjectId
    db = get_db()
    try:
        doc = await db.analyses.find_one({"_id": ObjectId(analysis_id)})
    except Exception:
        raise HTTPException(404, "Analiz bulunamadı")
    if not doc:
        raise HTTPException(404, "Analiz bulunamadı")
    doc["_id"] = str(doc["_id"])
    return doc


# ── Desteklenen Semboller ─────────────────────────────────────────────────────

@router.get("/symbols")
async def get_popular_symbols():
    """Popüler futures sembollerini listele"""
    return {
        "popular": [
            "BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT",
            "ADAUSDT", "DOGEUSDT", "AVAXUSDT", "DOTUSDT", "MATICUSDT",
            "LINKUSDT", "UNIUSDT", "ATOMUSDT", "LTCUSDT", "NEARUSDT",
        ],
        "intervals": ["1m", "5m", "15m", "30m", "1h", "4h", "1d"],
    }
