"""
FuturAgents — Backtest API Routes
LLM maliyeti: $0
"""
import asyncio
import json
import logging
from fastapi import APIRouter, Query, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.services.analysis.backtest import BacktestEngine

logger = logging.getLogger(__name__)
router = APIRouter()


class BacktestRequest(BaseModel):
    symbols: list[str] = []          # Boş = tüm Binance futures
    interval: str = "1h"
    strategy: str = "COMBINED"       # EMA_CROSS, RSI_EXTREME, MACD_CROSS, COMBINED
    period_days: int = 90
    sl_pct: float = 0.02
    tp_pct: float = 0.04
    top_n: int = 20                  # En iyi N sembol dönsün


@router.post("/run")
async def run_backtest(req: BacktestRequest):
    """
    Backtest çalıştır — SSE stream ile adım adım sonuç.
    symbols=[] → Binance'deki TÜM futures semboller taranır.
    LLM maliyeti: $0.00
    """
    async def stream():
        engine = BacktestEngine()
        try:
            # Sembol listesi
            if not req.symbols:
                yield _sse("status", {"message": "Binance'den tüm semboller alınıyor..."})
                symbols = await engine.get_all_futures_symbols()
                yield _sse("status", {"message": f"{len(symbols)} sembol bulundu. Backtest başlıyor...",
                                      "total": len(symbols)})
            else:
                symbols = req.symbols
                yield _sse("status", {"message": f"{len(symbols)} sembol test ediliyor..."})

            # Paralel backtest
            completed = 0
            results_so_far = []
            semaphore = asyncio.Semaphore(15)

            async def run_one(sym: str):
                nonlocal completed
                async with semaphore:
                    try:
                        r = await engine.run(
                            sym, req.interval, req.strategy,
                            req.period_days, req.sl_pct, req.tp_pct
                        )
                        results_so_far.append(r)
                        completed += 1
                        if completed % 10 == 0 or completed == len(symbols):
                            yield _sse("progress", {
                                "completed": completed,
                                "total": len(symbols),
                                "pct": round(completed / len(symbols) * 100)
                            })
                    except Exception as e:
                        completed += 1

            tasks = [run_one(s) for s in symbols]
            # Gather ile ama içeride yield var — farklı yaklaşım
            sem = asyncio.Semaphore(15)
            prog_results = []

            async def worker(sym):
                async with sem:
                    try:
                        r = await engine.run(sym, req.interval, req.strategy,
                                             req.period_days, req.sl_pct, req.tp_pct)
                        prog_results.append(r)
                    except Exception:
                        pass
                    await asyncio.sleep(0.05)

            # Batch'ler halinde işle ve ara sonuç gönder
            batch_size = 30
            for i in range(0, len(symbols), batch_size):
                batch = symbols[i:i+batch_size]
                await asyncio.gather(*[worker(s) for s in batch])
                yield _sse("progress", {
                    "completed": min(i + batch_size, len(symbols)),
                    "total": len(symbols),
                    "pct": round(min(i + batch_size, len(symbols)) / len(symbols) * 100),
                })

            # En iyileri sırala
            prog_results.sort(key=lambda r: r.score, reverse=True)
            top = prog_results[:req.top_n]

            yield _sse("results", {
                "total_tested": len(prog_results),
                "top_n": req.top_n,
                "results": [r.to_dict() for r in top],
            })
            yield _sse("complete", {
                "winner": top[0].to_dict() if top else None,
                "tested": len(prog_results),
            })

        except Exception as e:
            logger.error(f"Backtest hatası: {e}")
            yield _sse("error", {"message": str(e)})

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.get("/single")
async def single_backtest(
    symbol: str = Query("BTCUSDT"),
    interval: str = Query("1h"),
    strategy: str = Query("COMBINED"),
    period_days: int = Query(90),
    sl_pct: float = Query(0.02),
    tp_pct: float = Query(0.04),
):
    """Tek sembol hızlı backtest"""
    engine = BacktestEngine()
    result = await engine.run(symbol, interval, strategy, period_days, sl_pct, tp_pct)
    return result.to_dict()


@router.get("/symbols")
async def get_all_symbols():
    """Binance'deki tüm futures sembollerini listele"""
    engine = BacktestEngine()
    symbols = await engine.get_all_futures_symbols()
    return {"count": len(symbols), "symbols": symbols}


@router.get("/strategies")
async def get_strategies():
    return {
        "strategies": [
            {"id": "EMA_CROSS",   "name": "EMA Kesişimi",     "description": "EMA20/EMA50 kesişimi"},
            {"id": "RSI_EXTREME", "name": "RSI Aşırı Bölge",  "description": "RSI<30 long, RSI>70 short"},
            {"id": "MACD_CROSS",  "name": "MACD Kesişimi",    "description": "MACD histogram sıfır geçişi"},
            {"id": "COMBINED",    "name": "Kombine (Önerilen)","description": "EMA trend + RSI + MACD filtresi"},
        ]
    }


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n"
