"""
FuturAgents — Railway Startup Script
$PORT environment variable'ını Python ile okur,
uvicorn'u programatik olarak başlatır.
Shell expansion sorunu yoktur.
"""
import os
import uvicorn

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=port,
        workers=1,
        log_level=os.environ.get("LOG_LEVEL", "info").lower(),
    )
