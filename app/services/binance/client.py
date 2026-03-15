"""
FuturAgents — Binance Futures Service
Testnet / Mainnet destekli tam async Binance USDⓈ-M Futures istemcisi.
"""
import asyncio
import hashlib
import hmac
import logging
import time
from decimal import Decimal
from typing import Any
from urllib.parse import urlencode

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)


class BinanceFuturesClient:
    """
    Binance USDⓈ-M Futures REST + WebSocket istemcisi.
    Testnet'te çalışır, production'a geçmek için BINANCE_TESTNET=false yap.
    """

    BASE_URL_TESTNET = "https://testnet.binancefuture.com"
    BASE_URL_MAINNET = "https://fapi.binance.com"
    WS_TESTNET = "wss://stream.binancefuture.com"
    WS_MAINNET = "wss://fstream.binance.com"

    def __init__(self):
        self.api_key = settings.BINANCE_API_KEY
        self.api_secret = settings.BINANCE_API_SECRET
        self.testnet = settings.BINANCE_TESTNET
        self.base_url = self.BASE_URL_TESTNET if self.testnet else self.BASE_URL_MAINNET
        self.ws_url = self.WS_TESTNET if self.testnet else self.WS_MAINNET
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                headers={
                    "X-MBX-APIKEY": self.api_key,
                    "Content-Type": "application/json",
                },
                timeout=15.0,
            )
        return self._client

    def _sign(self, params: dict) -> dict:
        params["timestamp"] = int(time.time() * 1000)
        query = urlencode(params)
        signature = hmac.new(
            self.api_secret.encode(), query.encode(), hashlib.sha256
        ).hexdigest()
        params["signature"] = signature
        return params

    async def _get(self, path: str, params: dict = None, signed: bool = False) -> Any:
        client = await self._get_client()
        p = params or {}
        if signed:
            p = self._sign(p)
        resp = await client.get(path, params=p)
        resp.raise_for_status()
        return resp.json()

    async def _post(self, path: str, params: dict = None) -> Any:
        client = await self._get_client()
        p = self._sign(params or {})
        resp = await client.post(path, params=p)
        resp.raise_for_status()
        return resp.json()

    async def _delete(self, path: str, params: dict = None) -> Any:
        client = await self._get_client()
        p = self._sign(params or {})
        resp = await client.delete(path, params=p)
        resp.raise_for_status()
        return resp.json()

    # ── Market Data ───────────────────────────────────────────────────

    async def get_exchange_info(self) -> dict:
        """Tüm semboller ve kurallar"""
        return await self._get("/fapi/v1/exchangeInfo")

    async def get_price(self, symbol: str) -> float:
        """Anlık fiyat"""
        data = await self._get("/fapi/v1/ticker/price", {"symbol": symbol})
        return float(data["price"])

    async def get_klines(
        self,
        symbol: str,
        interval: str = "1h",
        limit: int = 200,
    ) -> list[dict]:
        """
        OHLCV mum verileri.
        interval: 1m, 5m, 15m, 1h, 4h, 1d, 1w
        """
        raw = await self._get(
            "/fapi/v1/klines",
            {"symbol": symbol, "interval": interval, "limit": limit},
        )
        return [
            {
                "open_time": k[0],
                "open": float(k[1]),
                "high": float(k[2]),
                "low": float(k[3]),
                "close": float(k[4]),
                "volume": float(k[5]),
                "close_time": k[6],
                "quote_volume": float(k[7]),
                "trades": int(k[8]),
            }
            for k in raw
        ]

    async def get_orderbook(self, symbol: str, limit: int = 20) -> dict:
        """Order book"""
        return await self._get("/fapi/v1/depth", {"symbol": symbol, "limit": limit})

    async def get_funding_rate(self, symbol: str) -> dict:
        """Funding rate ve sonraki funding zamanı"""
        data = await self._get("/fapi/v1/premiumIndex", {"symbol": symbol})
        return {
            "symbol": data["symbol"],
            "mark_price": float(data["markPrice"]),
            "funding_rate": float(data["lastFundingRate"]),
            "next_funding_time": data["nextFundingTime"],
        }

    async def get_open_interest(self, symbol: str) -> dict:
        """Açık pozisyon miktarı"""
        return await self._get("/fapi/v1/openInterest", {"symbol": symbol})

    async def get_24h_ticker(self, symbol: str) -> dict:
        """24 saatlik istatistikler"""
        return await self._get("/fapi/v1/ticker/24hr", {"symbol": symbol})

    async def get_liquidations(self, symbol: str, limit: int = 10) -> list:
        """Son tasfiyeler"""
        data = await self._get(
            "/fapi/v1/allForceOrders", {"symbol": symbol, "limit": limit}
        )
        return data

    # ── Account ───────────────────────────────────────────────────────

    async def get_account(self) -> dict:
        """Hesap bakiyesi ve pozisyonlar"""
        return await self._get("/fapi/v2/account", signed=True)

    async def get_balance(self) -> list[dict]:
        """USDT bakiyesi"""
        data = await self._get("/fapi/v2/balance", signed=True)
        return [a for a in data if float(a.get("balance", 0)) > 0]

    async def get_positions(self, symbol: str = None) -> list[dict]:
        """Açık pozisyonlar"""
        params = {}
        if symbol:
            params["symbol"] = symbol
        data = await self._get("/fapi/v2/positionRisk", params=params, signed=True)
        return [p for p in data if float(p.get("positionAmt", 0)) != 0]

    async def get_open_orders(self, symbol: str = None) -> list[dict]:
        """Açık emirler"""
        params = {}
        if symbol:
            params["symbol"] = symbol
        return await self._get("/fapi/v1/openOrders", params=params, signed=True)

    # ── Trading ───────────────────────────────────────────────────────

    async def set_leverage(self, symbol: str, leverage: int) -> dict:
        """Kaldıraç ayarla (1-125x)"""
        leverage = min(leverage, settings.MAX_LEVERAGE)
        return await self._post(
            "/fapi/v1/leverage",
            {"symbol": symbol, "leverage": leverage},
        )

    async def set_margin_type(self, symbol: str, margin_type: str = "ISOLATED") -> dict:
        """Marjin tipi: ISOLATED veya CROSSED"""
        try:
            return await self._post(
                "/fapi/v1/marginType",
                {"symbol": symbol, "marginType": margin_type},
            )
        except Exception as e:
            # 400 = zaten o moddaysa Binance hata döner, tolere et
            err_str = str(e).lower()
            if "400" in err_str or "already" in err_str or "no need" in err_str:
                logger.debug(f"set_margin_type {symbol}: zaten ayarlı — {e}")
                return {"msg": "already set"}
            raise

    async def place_market_order(
        self,
        symbol: str,
        side: str,           # "BUY" veya "SELL"
        quantity: float,
        reduce_only: bool = False,
        position_side: str = None,  # None = One-way mode (BOTH), "LONG"/"SHORT" = Hedge mode
    ) -> dict:
        """Market emri aç"""
        params = {
            "symbol": symbol,
            "side": side,
            "type": "MARKET",
            "quantity": self._format_quantity(quantity, symbol),
        }
        # positionSide sadece hedge mode'da gerekli, one-way mode'da hata verir
        if position_side and position_side != "BOTH":
            params["positionSide"] = position_side
        elif reduce_only:
            params["reduceOnly"] = "true"
        return await self._post("/fapi/v1/order", params)

    async def place_limit_order(
        self,
        symbol: str,
        side: str,
        quantity: float,
        price: float,
        time_in_force: str = "GTC",
        reduce_only: bool = False,
    ) -> dict:
        """Limit emri"""
        return await self._post(
            "/fapi/v1/order",
            {
                "symbol": symbol,
                "side": side,
                "type": "LIMIT",
                "quantity": self._format_quantity(quantity, symbol),
                "price": f"{price:.4f}",
                "timeInForce": time_in_force,
                "reduceOnly": str(reduce_only).lower(),
            },
        )

    async def place_stop_order(
        self,
        symbol: str,
        side: str,
        quantity: float,
        stop_price: float,
        order_type: str = "STOP_MARKET",  # STOP_MARKET veya TAKE_PROFIT_MARKET
    ) -> dict:
        """Stop-loss / Take-profit emri"""
        return await self._post(
            "/fapi/v1/order",
            {
                "symbol": symbol,
                "side": side,
                "type": order_type,
                "quantity": self._format_quantity(quantity, symbol),
                "stopPrice": f"{stop_price:.4f}",
                "reduceOnly": "true",
            },
        )

    async def cancel_order(self, symbol: str, order_id: int) -> dict:
        """Emri iptal et"""
        return await self._delete(
            "/fapi/v1/order",
            {"symbol": symbol, "orderId": order_id},
        )

    async def cancel_all_orders(self, symbol: str) -> dict:
        """Sembol için tüm emirleri iptal et"""
        return await self._delete(
            "/fapi/v1/allOpenOrders",
            {"symbol": symbol},
        )

    async def close_position(self, symbol: str) -> dict:
        """Mevcut pozisyonu market emriyle kapat"""
        positions = await self.get_positions(symbol)
        if not positions:
            return {"msg": "no open position"}

        pos = positions[0]
        amt = float(pos["positionAmt"])
        if amt == 0:
            return {"msg": "position already closed"}

        side = "SELL" if amt > 0 else "BUY"
        return await self.place_market_order(
            symbol=symbol,
            side=side,
            quantity=abs(amt),
            reduce_only=True,
        )

    # ── Risk Helper ───────────────────────────────────────────────────

    async def calculate_position_size(
        self,
        symbol: str,
        risk_usdt: float,
        stop_loss_pct: float = None,
    ) -> float:
        """
        Risk bazlı pozisyon büyüklüğü hesapla.
        stop_loss_pct kadar hareket ederse risk_usdt kaybedilir.
        """
        sl_pct = stop_loss_pct or settings.STOP_LOSS_PERCENT
        price = await self.get_price(symbol)
        # Quantity = risk / (price * stop_loss_pct)
        qty = risk_usdt / (price * sl_pct)
        return round(qty, 3)

    def _format_quantity(self, qty: float, symbol: str) -> str:
        """Coin'e göre precision ayarla"""
        # Binance testnet minimum step size'ları
        precision_map = {
            "BTCUSDT": 3,   # 0.001 BTC
            "ETHUSDT": 3,   # 0.001 ETH
            "SOLUSDT": 1,   # 0.1 SOL
            "BNBUSDT": 2,   # 0.01 BNB
            "XRPUSDT": 1,   # 1 XRP (integer)
        }
        decimals = precision_map.get(symbol, 3)
        # XRP için tam sayı
        if symbol == "XRPUSDT":
            return str(max(1, int(qty)))
        return f"{qty:.{decimals}f}"

    async def close(self):
        if self._client:
            await self._client.aclose()


# Singleton
_binance_client: BinanceFuturesClient | None = None


def get_binance_client() -> BinanceFuturesClient:
    global _binance_client
    if _binance_client is None:
        _binance_client = BinanceFuturesClient()
    return _binance_client
