"""
FuturAgents — Backtest Motoru
LLM KULLANMAZ — sıfır API maliyeti.
Geçmiş kline verisiyle teknik indikatör tabanlı strateji simülasyonu.
"""
import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime

import pandas as pd
import numpy as np

from app.services.binance.client import get_binance_client

logger = logging.getLogger(__name__)


@dataclass
class Trade:
    symbol: str
    side: str
    entry_price: float
    exit_price: float
    entry_time: str
    exit_time: str
    pnl_pct: float
    pnl_usdt: float
    exit_reason: str  # TP / SL / SIGNAL_REVERSE / END


@dataclass
class BacktestResult:
    symbol: str
    interval: str
    strategy: str
    period_days: int
    total_trades: int
    winning_trades: int
    losing_trades: int
    win_rate: float
    total_return_pct: float
    max_drawdown_pct: float
    sharpe_ratio: float
    profit_factor: float
    avg_win_pct: float
    avg_loss_pct: float
    best_trade_pct: float
    worst_trade_pct: float
    score: float = 0.0
    trades: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "interval": self.interval,
            "strategy": self.strategy,
            "period_days": self.period_days,
            "total_trades": self.total_trades,
            "winning_trades": self.winning_trades,
            "losing_trades": self.losing_trades,
            "win_rate": round(self.win_rate * 100, 1),
            "total_return_pct": self.total_return_pct,
            "max_drawdown_pct": self.max_drawdown_pct,
            "sharpe_ratio": self.sharpe_ratio,
            "profit_factor": self.profit_factor,
            "avg_win_pct": self.avg_win_pct,
            "avg_loss_pct": self.avg_loss_pct,
            "best_trade_pct": self.best_trade_pct,
            "worst_trade_pct": self.worst_trade_pct,
            "score": self.score,
        }


class BacktestEngine:
    """Kural tabanlı backtest — LLM maliyeti $0"""

    def __init__(self):
        self.binance = get_binance_client()

    async def run(
        self,
        symbol: str,
        interval: str = "1h",
        strategy: str = "COMBINED",
        period_days: int = 90,
        sl_pct: float = 0.02,
        tp_pct: float = 0.04,
        position_size_usdt: float = 1000.0,
    ) -> BacktestResult:
        limit = min(int(period_days * 24 / self._interval_hours(interval)) + 50, 1000)
        klines = await self.binance.get_klines(symbol, interval, limit=limit)
        if len(klines) < 50:
            raise ValueError(f"Yetersiz veri: {len(klines)} bar")
        df = self._prepare_df(klines)
        signals = self._generate_signals(df, strategy)
        trades = self._simulate(df, signals, sl_pct, tp_pct, position_size_usdt, symbol)
        return self._calculate_stats(symbol, interval, strategy, period_days, trades)

    async def run_multi(
        self,
        symbols: list[str],
        interval: str = "1h",
        strategy: str = "COMBINED",
        period_days: int = 90,
        sl_pct: float = 0.02,
        tp_pct: float = 0.04,
        top_n: int = 10,
    ) -> list[BacktestResult]:
        """Tüm semboller — paralel, LLM yok, maliyet $0"""
        results = []
        semaphore = asyncio.Semaphore(15)  # Max 15 paralel

        async def run_one(sym: str):
            async with semaphore:
                try:
                    r = await self.run(sym, interval, strategy, period_days, sl_pct, tp_pct)
                    results.append(r)
                except Exception as e:
                    logger.debug(f"[Backtest] {sym} atlandı: {e}")
                await asyncio.sleep(0.1)  # Binance rate limit

        await asyncio.gather(*[run_one(s) for s in symbols])
        results.sort(key=lambda r: r.score, reverse=True)
        return results[:top_n]

    async def get_all_futures_symbols(self) -> list[str]:
        """Binance USDⓈ-M Futures tüm aktif semboller (~370)"""
        info = await self.binance.get_exchange_info()
        syms = [
            s["symbol"] for s in info.get("symbols", [])
            if s.get("status") == "TRADING"
            and s.get("quoteAsset") == "USDT"
            and s.get("contractType") == "PERPETUAL"
        ]
        logger.info(f"[Backtest] {len(syms)} aktif sembol bulundu")
        return sorted(syms)

    def _prepare_df(self, klines: list[dict]) -> pd.DataFrame:
        df = pd.DataFrame(klines)
        for col in ["open", "high", "low", "close", "volume"]:
            df[col] = pd.to_numeric(df[col])
        df["time"] = pd.to_datetime(df["open_time"], unit="ms")
        df.set_index("time", inplace=True)

        df["ema20"]  = df["close"].ewm(span=20).mean()
        df["ema50"]  = df["close"].ewm(span=50).mean()
        df["ema200"] = df["close"].ewm(span=200).mean()

        delta = df["close"].diff()
        gain  = delta.where(delta > 0, 0).rolling(14).mean()
        loss  = (-delta.where(delta < 0, 0)).rolling(14).mean()
        df["rsi"] = 100 - (100 / (1 + gain / (loss + 1e-10)))

        ema12 = df["close"].ewm(span=12).mean()
        ema26 = df["close"].ewm(span=26).mean()
        df["macd"]      = ema12 - ema26
        df["macd_sig"]  = df["macd"].ewm(span=9).mean()
        df["macd_hist"] = df["macd"] - df["macd_sig"]

        tr1 = df["high"] - df["low"]
        tr2 = (df["high"] - df["close"].shift()).abs()
        tr3 = (df["low"]  - df["close"].shift()).abs()
        df["atr"] = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1).rolling(14).mean()

        return df.dropna()

    def _generate_signals(self, df: pd.DataFrame, strategy: str) -> pd.Series:
        s = pd.Series(0, index=df.index)
        if strategy == "EMA_CROSS":
            s[df["ema20"] > df["ema50"]] = 1
            s[df["ema20"] < df["ema50"]] = -1
        elif strategy == "RSI_EXTREME":
            s[df["rsi"] < 30] = 1
            s[df["rsi"] > 70] = -1
        elif strategy == "MACD_CROSS":
            s[(df["macd_hist"] > 0) & (df["macd_hist"].shift() <= 0)] = 1
            s[(df["macd_hist"] < 0) & (df["macd_hist"].shift() >= 0)] = -1
        elif strategy == "COMBINED":
            bull = (df["ema20"] > df["ema50"]) & (df["close"] > df["ema200"])
            bear = (df["ema20"] < df["ema50"]) & (df["close"] < df["ema200"])
            s[bull & (df["rsi"] < 55) & (df["macd_hist"] > 0)] =  1
            s[bear & (df["rsi"] > 45) & (df["macd_hist"] < 0)] = -1
        return s

    def _simulate(self, df, signals, sl_pct, tp_pct, pos_size, symbol) -> list[Trade]:
        trades = []
        position = None
        for i in range(1, len(df)):
            bar = df.iloc[i]
            sig = signals.iloc[i - 1]

            if position:
                ep = position["entry_price"]
                exit_price = exit_reason = None
                if position["side"] == "LONG":
                    if bar["low"] <= position["sl"]:
                        exit_price, exit_reason = position["sl"], "SL"
                    elif bar["high"] >= position["tp"]:
                        exit_price, exit_reason = position["tp"], "TP"
                    elif sig == -1:
                        exit_price, exit_reason = bar["open"], "SIGNAL_REVERSE"
                else:
                    if bar["high"] >= position["sl"]:
                        exit_price, exit_reason = position["sl"], "SL"
                    elif bar["low"] <= position["tp"]:
                        exit_price, exit_reason = position["tp"], "TP"
                    elif sig == 1:
                        exit_price, exit_reason = bar["open"], "SIGNAL_REVERSE"

                if exit_price:
                    pnl = (exit_price - ep) / ep
                    if position["side"] == "SHORT":
                        pnl = -pnl
                    trades.append(Trade(
                        symbol=symbol, side=position["side"],
                        entry_price=ep, exit_price=exit_price,
                        entry_time=str(position["entry_time"]),
                        exit_time=str(bar.name),
                        pnl_pct=round(pnl * 100, 3),
                        pnl_usdt=round(position["qty"] * ep * pnl, 4),
                        exit_reason=exit_reason,
                    ))
                    position = None

            if position is None and sig != 0:
                entry = float(bar["open"])
                qty = pos_size / entry
                side = "LONG" if sig == 1 else "SHORT"
                position = {
                    "side": side, "entry_price": entry,
                    "entry_time": bar.name, "qty": qty,
                    "sl": entry * (1 - sl_pct) if side == "LONG" else entry * (1 + sl_pct),
                    "tp": entry * (1 + tp_pct) if side == "LONG" else entry * (1 - tp_pct),
                }

        if position:
            last = df.iloc[-1]
            ep = position["entry_price"]
            ex = float(last["close"])
            pnl = (ex - ep) / ep
            if position["side"] == "SHORT":
                pnl = -pnl
            trades.append(Trade(
                symbol=symbol, side=position["side"],
                entry_price=ep, exit_price=ex,
                entry_time=str(position["entry_time"]),
                exit_time=str(last.name),
                pnl_pct=round(pnl * 100, 3),
                pnl_usdt=round(position["qty"] * ep * pnl, 4),
                exit_reason="END",
            ))
        return trades

    def _calculate_stats(self, symbol, interval, strategy, period_days, trades) -> BacktestResult:
        if not trades:
            return BacktestResult(symbol=symbol, interval=interval, strategy=strategy,
                period_days=period_days, total_trades=0, winning_trades=0, losing_trades=0,
                win_rate=0, total_return_pct=0, max_drawdown_pct=0, sharpe_ratio=0,
                profit_factor=0, avg_win_pct=0, avg_loss_pct=0, best_trade_pct=0,
                worst_trade_pct=0, score=0)

        pnls = [t.pnl_pct for t in trades]
        wins = [p for p in pnls if p > 0]
        loss = [p for p in pnls if p <= 0]

        cumulative = peak = 0
        drawdowns = []
        for p in pnls:
            cumulative += p
            if cumulative > peak:
                peak = cumulative
            drawdowns.append(cumulative - peak)
        max_dd = min(drawdowns) if drawdowns else 0

        arr = np.array(pnls)
        sharpe = float(arr.mean() / (arr.std() + 1e-10)) * np.sqrt(252) if len(arr) > 1 else 0
        pf = sum(wins) / (abs(sum(loss)) + 1e-10)

        # Skor hesapla
        wr = len(wins) / len(trades)
        score = min(wr * 60, 40) + min(max(sharpe * 10, 0), 25) + min(max(100 - abs(max_dd) * 3, 0), 20) + min(len(trades) / 5, 15)

        return BacktestResult(
            symbol=symbol, interval=interval, strategy=strategy,
            period_days=period_days, total_trades=len(trades),
            winning_trades=len(wins), losing_trades=len(loss),
            win_rate=wr, total_return_pct=round(cumulative, 2),
            max_drawdown_pct=round(max_dd, 2), sharpe_ratio=round(sharpe, 2),
            profit_factor=round(pf, 2),
            avg_win_pct=round(sum(wins)/len(wins), 3) if wins else 0,
            avg_loss_pct=round(sum(loss)/len(loss), 3) if loss else 0,
            best_trade_pct=round(max(pnls), 3), worst_trade_pct=round(min(pnls), 3),
            score=round(score, 1), trades=trades,
        )

    def _interval_hours(self, interval: str) -> float:
        return {"1m": 1/60, "5m": 5/60, "15m": 0.25, "30m": 0.5,
                "1h": 1, "4h": 4, "1d": 24, "1w": 168}.get(interval, 1)
