"""Market data endpoints"""
from fastapi import APIRouter, Query
from app.services.binance.client import get_binance_client

router = APIRouter()


@router.get("/price/{symbol}")
async def get_price(symbol: str):
    binance = get_binance_client()
    price = await binance.get_price(symbol.upper())
    return {"symbol": symbol.upper(), "price": price}


@router.get("/klines/{symbol}")
async def get_klines(
    symbol: str,
    interval: str = Query("1h"),
    limit: int = Query(200, le=1000),
):
    binance = get_binance_client()
    return await binance.get_klines(symbol.upper(), interval, limit)


@router.get("/funding/{symbol}")
async def get_funding(symbol: str):
    binance = get_binance_client()
    return await binance.get_funding_rate(symbol.upper())


@router.get("/ticker/{symbol}")
async def get_ticker(symbol: str):
    binance = get_binance_client()
    return await binance.get_24h_ticker(symbol.upper())


@router.get("/orderbook/{symbol}")
async def get_orderbook(symbol: str, limit: int = Query(20)):
    binance = get_binance_client()
    return await binance.get_orderbook(symbol.upper(), limit)


@router.get("/account/balance")
async def get_balance():
    binance = get_binance_client()
    return await binance.get_balance()
