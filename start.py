import os
import sys

# Startup debug — import öncesi çalışır
print("=== FUTURAGENTS STARTING ===", flush=True)
print(f"Python: {sys.version}", flush=True)
print(f"PORT: {os.environ.get('PORT', 'NOT SET')}", flush=True)

# Tüm env variable adlarını logla (değerleri değil)
env_keys = sorted(os.environ.keys())
print(f"ENV KEYS: {env_keys}", flush=True)

# MongoDB ile ilgili olanları değerleriyle logla
for k in env_keys:
    if any(x in k.upper() for x in ["MONGO", "REDIS", "DATABASE"]):
        v = os.environ[k]
        safe = v[:15] + "***" if len(v) > 15 else v
        print(f"  {k} = {safe}", flush=True)

try:
    print("Importing app...", flush=True)
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    print(f"Starting uvicorn on port {port}", flush=True)
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=port,
        workers=1,
        log_level=os.environ.get("LOG_LEVEL", "info").lower(),
    )
except Exception as e:
    print(f"FATAL ERROR: {e}", flush=True)
    import traceback
    traceback.print_exc()
    sys.exit(1)
