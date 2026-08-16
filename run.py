import os
import sys
import uvicorn

# Ensure the backend directory is in sys.path
backend_dir = os.path.dirname(os.path.abspath(__file__))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

# Force UTF-8 stdout so emoji in log lines don't crash on Windows cp1252
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

if __name__ == "__main__":
    # ── Environment detection ─────────────────────────────────────────────────
    ENV         = os.getenv("ENV", "development").lower()        # "production" or "development"
    PORT        = int(os.getenv("PORT", "8000"))                 # Injected by Railway / Render / Fly.io
    HOST        = os.getenv("HOST", "0.0.0.0")
    IS_PROD     = ENV == "production"
    configured_workers = max(1, int(os.getenv("WORKERS", "1")))
    # The current persistence and WebSocket registries are process-local. More
    # than one Uvicorn worker would create divergent JSON state and miss live
    # events. One async worker still serves many concurrent connections; scale
    # horizontally only after moving those stores to shared infrastructure.
    WORKERS     = 1
    MAX_CONCURRENCY = max(1, int(os.getenv("MAX_CONCURRENCY", "500")))
    MAX_REQUESTS = int(os.getenv("MAX_REQUESTS", "10000"))
    LOG_LEVEL   = os.getenv("LOG_LEVEL", "warning" if IS_PROD else "info")

    if IS_PROD and configured_workers > 1:
        print("  [Pulse] WORKERS>1 ignored: JSON storage and live WebSockets require one process.")

    print("=" * 60)
    print(f"  Pulse API Server  [{ENV.upper()}]")
    print(f"  Local:   http://localhost:{PORT}")
    print(f"  Docs:    http://localhost:{PORT}/api/docs")
    print(f"  WS feed: ws://localhost:{PORT}/ws/feed")
    print(f"  WS chat: ws://localhost:{PORT}/api/messages/ws/{{uid}}")
    print("=" * 60)

    uvicorn.run(
        "app.main:app",
        host=HOST,
        port=PORT,
        reload=not IS_PROD,                          # No reload in production
        reload_dirs=[backend_dir] if not IS_PROD else None,
        workers=WORKERS,
        log_level=LOG_LEVEL,
        access_log=not IS_PROD,                      # Suppress access log spam in production
        timeout_keep_alive=30,                       # Kill idle connections after 30s
        limit_concurrency=MAX_CONCURRENCY,           # Tunable concurrent-connection guard
        limit_max_requests=MAX_REQUESTS if MAX_REQUESTS > 0 else None,
    )
