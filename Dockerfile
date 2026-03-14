# FuturAgents — Railway Optimized Dockerfile
# Multi-stage build: daha küçük final image
FROM python:3.11-slim-bookworm AS builder

WORKDIR /build
RUN pip install --upgrade pip

COPY pyproject.toml README.md ./
RUN pip install --prefer-binary --no-cache-dir \
    fastapi uvicorn[standard] pydantic pydantic-settings \
    motor pymongo "redis>=5.0.0" \
    PyJWT bcrypt python-dotenv python-multipart \
    apscheduler aiofiles httpx sse-starlette \
    python-binance ccxt \
    anthropic "langchain-anthropic>=0.1.23" langchain langchain-community langgraph \
    pandas numpy ta stockstats yfinance \
    finnhub-python requests \
    rich psutil pytz tqdm plotly \
    concurrent-log-handler

# ── Final Image ─────────────────────────────────────────────────────────────
FROM python:3.11-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    PORT=8000 \
    TZ=UTC

WORKDIR /app

# Sistem araçları
RUN apt-get update && apt-get install -y --no-install-recommends curl && \
    rm -rf /var/lib/apt/lists/*

# Python paketlerini builder'dan kopyala
COPY --from=builder /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

# Uygulama kodu
COPY app ./app
COPY pyproject.toml README.md ./

# Dizinler
RUN mkdir -p /app/logs /app/data /app/frontend_static

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
    CMD curl -f http://localhost:${PORT}/api/health || exit 1

EXPOSE ${PORT}

CMD python -m uvicorn app.main:app --host 0.0.0.0 --port ${PORT} --workers 1
