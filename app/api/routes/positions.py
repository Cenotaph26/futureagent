"""Positions Route"""
import logging
from datetime import datetime
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from app.services.binance.client import get_binance_client
from app.db.database import get_db

logger = logging.getLogger(__name__)
router = APIRouter()


class ManualTradeRequest(BaseModel):
    symbol: str
    side: str = Field(..., description="BUY veya SELL")
    quantity: float
    leverage: int = Field(3, ge=1, le=20)
    stop_loss: float = None
    take_profit: float = None


@router.get("")
async def list_positions():
    """Tüm açık pozisyonlar"""
    binance = get_binance_client()
    return await binance.get_positions()


@router.get("/{symbol}")
async def get_position(symbol: str):
    """Belirli sembol pozisyonu"""
    binance = get_binance_client()
    positions = await binance.get_positions(symbol.upper())
    return positions[0] if positions else {"symbol": symbol, "positionAmt": "0"}


@router.post("/open")
async def open_position(req: ManualTradeRequest):
    """Manuel pozisyon aç"""
    binance = get_binance_client()
    symbol = req.symbol.upper()
    await binance.set_leverage(symbol, req.leverage)
    await binance.set_margin_type(symbol, "ISOLATED")
    order = await binance.place_market_order(symbol, req.side, req.quantity)

    results = {"order": order}
    if req.stop_loss:
        sl_side = "SELL" if req.side == "BUY" else "BUY"
        try:
            results["stop_loss"] = await binance.place_stop_order(symbol, sl_side, req.quantity, req.stop_loss)
        except Exception as e:
            results["stop_loss_error"] = str(e)
    if req.take_profit:
        tp_side = "SELL" if req.side == "BUY" else "BUY"
        try:
            results["take_profit"] = await binance.place_stop_order(symbol, tp_side, req.quantity, req.take_profit, "TAKE_PROFIT_MARKET")
        except Exception as e:
            results["take_profit_error"] = str(e)
    return results


@router.delete("/{symbol}/close")
async def close_position(symbol: str):
    """Pozisyonu kapat"""
    binance = get_binance_client()
    return await binance.close_position(symbol.upper())


@router.get("/orders/open")
async def list_open_orders(symbol: str = None):
    """Açık emirler"""
    binance = get_binance_client()
    return await binance.get_open_orders(symbol.upper() if symbol else None)
