"""Signals Route — Kaydedilmiş sinyaller ve bildirimler"""
from datetime import datetime, timedelta
from fastapi import APIRouter, Query
from app.db.database import get_db

router = APIRouter()


@router.get("")
async def list_signals(
    symbol: str = Query(None),
    decision: str = Query(None),
    limit: int = Query(50, le=200),
    days: int = Query(3),
):
    """Geçmiş sinyaller"""
    db = get_db()
    query = {"created_at": {"$gte": datetime.utcnow() - timedelta(days=days)}}
    if symbol:
        query["symbol"] = symbol.upper()
    if decision:
        query["final_decision.decision"] = decision.upper()

    cursor = db.analyses.find(
        query,
        {"final_decision": 1, "symbol": 1, "interval": 1, "created_at": 1, "auto_executed": 1}
    ).sort("created_at", -1).limit(limit)

    results = []
    async for doc in cursor:
        doc["_id"] = str(doc["_id"])
        results.append(doc)
    return results


@router.get("/stats")
async def signal_stats(days: int = Query(7)):
    """Sinyal istatistikleri"""
    db = get_db()
    pipeline = [
        {"$match": {"created_at": {"$gte": datetime.utcnow() - timedelta(days=days)}}},
        {"$group": {
            "_id": "$final_decision.decision",
            "count": {"$sum": 1},
            "avg_confidence": {"$avg": "$final_decision.confidence"},
        }},
    ]
    results = []
    async for doc in db.analyses.aggregate(pipeline):
        results.append(doc)
    return {"period_days": days, "breakdown": results}
