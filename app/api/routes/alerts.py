"""Alerts route — trailing stop alarmları"""
from datetime import datetime, timedelta
from fastapi import APIRouter, Query
from app.db.database import get_db

router = APIRouter()

@router.get("")
async def get_alerts(unread_only: bool = Query(False), limit: int = Query(50)):
    db = get_db()
    q = {"read": False} if unread_only else {}
    cursor = db.alerts.find(q).sort("created_at", -1).limit(limit)
    results = []
    async for doc in cursor:
        doc["_id"] = str(doc["_id"])
        results.append(doc)
    return results

@router.post("/{alert_id}/read")
async def mark_read(alert_id: str):
    from bson import ObjectId
    db = get_db()
    await db.alerts.update_one({"_id": ObjectId(alert_id)}, {"$set": {"read": True}})
    return {"ok": True}

@router.get("/trailing-stops")
async def get_trailing_stops():
    db = get_db()
    cursor = db.trailing_stops.find({})
    results = []
    async for doc in cursor:
        doc["_id"] = str(doc["_id"])
        results.append(doc)
    return results
