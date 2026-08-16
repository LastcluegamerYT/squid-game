import os
import json
import time
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.config import settings
from app.media.storage import UPLOADS_DIR
from app.routers import (
    auth_router, users_router, posts_router, feed_router,
    media_router, search_router, ai_router, messages_router, notifications_router, link_preview_router
)

logger = logging.getLogger("pulse")

# ─── Lifespan (replaces deprecated on_event) ─────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup → yield → Shutdown"""
    import asyncio
    from app.database.db import db
    from app.services.search_service import search_service
    from app.ai.background_worker import background_worker
    from app.ai.embedding_store import embedding_store
    from app.ai.vector_index import vector_index

    loop = asyncio.get_running_loop()

    # 1. Keyword search index (non-blocking)
    def _build_search():
        count = search_service.build_index(db)
        print(f"  [Search] Index built: {count} documents indexed", flush=True)
    loop.run_in_executor(None, _build_search)

    # 2. Restore vector index from disk (sync — milliseconds, no model needed)
    matrix, post_ids = embedding_store.get_all()
    if len(post_ids) > 0:
        vector_index.rebuild(matrix, post_ids)
        print(f"  [AI] Vector index restored: {len(post_ids)} posts, mode={vector_index.mode}", flush=True)
    else:
        vector_index.rebuild(matrix, post_ids)
        print("  [AI] No stored embeddings found — will build after model loads", flush=True)

    # 3. Embed missing posts in background (may download model on first run)
    def _start_ai():
        print("  [AI] Background embedding pipeline starting...", flush=True)
        background_worker.embed_missing_on_startup(db.posts)
    loop.run_in_executor(None, _start_ai)

    yield  # ← server is running

    # Shutdown: flush any pending DB writes
    print("  [Pulse] Graceful shutdown — flushing DB...", flush=True)
    # Cancel queued work rather than letting model inference delay a deploy.
    # Missing embeddings are picked up by the startup reconciliation job.
    background_worker.shutdown(wait=False)
    db._save()


# ─── App factory ─────────────────────────────────────────────────────────────

ENV     = os.getenv("ENV", "development").lower()
IS_PROD = ENV == "production"

app = FastAPI(
    title=settings.PROJECT_NAME,
    version=settings.VERSION,
    description=(
        "Pulse — Idea-Centric Social Platform API\n\n"
        "Auth: Firebase ID token → `Authorization: Bearer <token>`\n\n"
        "Features: Profiles · Posts (multi-image) · Threaded Comments · "
        "Follow/Friends · Private Messaging · AI Feed · Real-time WebSocket"
    ),
    openapi_url=f"{settings.API_V1_STR}/openapi.json",
    docs_url=f"{settings.API_V1_STR}/docs",
    redoc_url=f"{settings.API_V1_STR}/redoc",
    # Hide docs in production (optional — remove if you want them public)
    # docs_url=None if IS_PROD else f"{settings.API_V1_STR}/docs",
    lifespan=lifespan,
)

# ─── GZip compression (reduces response size ~70% for JSON) ──────────────────
app.add_middleware(GZipMiddleware, minimum_size=500)

# ─── CORS ────────────────────────────────────────────────────────────────────
# In production set CORS_ORIGINS env var to your actual domain(s), e.g.:
#   CORS_ORIGINS=https://mypulse.app,https://www.mypulse.app
_raw_origins = os.getenv("CORS_ORIGINS", "")
ALLOWED_ORIGINS = (
    [o.strip() for o in _raw_origins.split(",") if o.strip()]
    if _raw_origins else settings.CORS_ORIGINS
)
# A broad host-suffix regex combined with credentials lets unrelated deployments
# on a shared provider call authenticated APIs. Operators can still opt into an
# explicit regex when they truly control that namespace.
ALLOWED_ORIGIN_REGEX = os.getenv("CORS_ORIGIN_REGEX", "").strip() or None

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_origin_regex=ALLOWED_ORIGIN_REGEX,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-Requested-With", "Accept"],
    expose_headers=["X-Total-Count", "X-Request-Id"],
    max_age=600,  # preflight cache for 10 min — reduces OPTIONS round-trips
)

# ─── Security headers middleware ──────────────────────────────────────────────
@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    if IS_PROD:
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return response

# ─── Request timing middleware ────────────────────────────────────────────────
@app.middleware("http")
async def add_timing(request: Request, call_next):
    t0 = time.perf_counter()
    response = await call_next(request)
    ms = round((time.perf_counter() - t0) * 1000, 1)
    response.headers["X-Response-Time"] = f"{ms}ms"
    return response

# ─── Global exception handlers (no crash on bad input) ───────────────────────
@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException):
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.detail, "status_code": exc.status_code},
    )

@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    errors = []
    for error in exc.errors():
        field = " → ".join(str(loc) for loc in error.get("loc", []))
        errors.append({"field": field, "message": error.get("msg", ""), "type": error.get("type", "")})
    return JSONResponse(
        status_code=422,
        content={"detail": "Validation error", "errors": errors},
    )

@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    logger.error(f"Unhandled error on {request.method} {request.url}: {exc}", exc_info=True)
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error. The team has been notified."},
    )

# ─── API Routers ──────────────────────────────────────────────────────────────
app.include_router(auth_router,     prefix=settings.API_V1_STR)
app.include_router(users_router,    prefix=settings.API_V1_STR)
app.include_router(posts_router,    prefix=settings.API_V1_STR)
app.include_router(feed_router,     prefix=settings.API_V1_STR)
app.include_router(media_router,    prefix=settings.API_V1_STR)
app.include_router(search_router,   prefix=settings.API_V1_STR)
app.include_router(ai_router,       prefix=settings.API_V1_STR)
app.include_router(messages_router, prefix=settings.API_V1_STR)
app.include_router(notifications_router, prefix=settings.API_V1_STR)
app.include_router(link_preview_router, prefix=settings.API_V1_STR)

# Upload responses intentionally use public ``/media/...`` URLs so they can be
# embedded directly in post content.  The API router remains available at
# ``/api/media/...`` for uploads and backwards compatibility; this mount is a
# read-only alias for the returned asset URLs.
os.makedirs(UPLOADS_DIR, exist_ok=True)
app.mount("/media", StaticFiles(directory=UPLOADS_DIR), name="media")

# ─── Health Check ─────────────────────────────────────────────────────────────
@app.get(f"{settings.API_V1_STR}/health", tags=["System"])
async def health_check():
    """Server health — used by hosting platforms (Railway, Render, Fly.io) for liveness probes."""
    from app.database.db import db
    from app.ai.vector_index import vector_index
    return {
        "status": "healthy",
        "env": ENV,
        "app": settings.PROJECT_NAME,
        "version": settings.VERSION,
        "firebase_project": settings.FIREBASE_PROJECT_ID,
        "users_count": len(db.users),
        "posts_count": len(db.posts),
        "categories_count": len(db.categories),
        "messages_count": sum(len(v) for v in db.messages.values()),
        "conversations_count": len(db.conversations),
        "ws_connections": len(db.ws_connections),
        "ai_vector_index_size": vector_index.size,
    }

# ─── WebSocket — Real-time Feed Events ───────────────────────────────────────
@app.websocket("/ws/feed")
async def websocket_feed(websocket: WebSocket):
    """
    Real-time feed WebSocket. Broadcasts:
    - {"type": "new_post", "post": {...}}
    - {"type": "reaction_update", "post_id": "...", "stats": {...}}
    - {"type": "new_comment", "post_id": "...", "comment_id": "..."}
    - {"type": "pong"} (reply to "ping")
    """
    from app.database.db import db
    await websocket.accept()
    db.ws_connections.append(websocket)
    try:
        await websocket.send_text(json.dumps({"type": "connected", "message": "Pulse Live connected"}))
        while True:
            data = await websocket.receive_text()
            if data.strip().lower() == "ping":
                await websocket.send_text(json.dumps({"type": "pong"}))
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        try:
            db.ws_connections.remove(websocket)
        except ValueError:
            pass

# ─── Optional legacy SPA hosting ────────────────────────────────────────────
# THE IDEON's Next.js application is deployed independently.  Never mount the
# source ``frontend/`` directory here: doing so could expose .env files and
# source code through a static route.  A legacy pre-built SPA can be hosted only
# when an operator explicitly provides a dedicated, publishable directory.
legacy_spa_dir = os.getenv("LEGACY_SPA_DIR", "").strip()
if legacy_spa_dir:
    legacy_spa_dir = os.path.abspath(legacy_spa_dir)
    legacy_index = os.path.join(legacy_spa_dir, "index.html")
    if os.path.isfile(legacy_index):
        app.mount("/static", StaticFiles(directory=legacy_spa_dir), name="static")

        @app.get("/", include_in_schema=False)
        async def serve_index():
            return FileResponse(legacy_index, media_type="text/html; charset=utf-8")

        @app.get("/{path:path}", include_in_schema=False)
        async def serve_spa(path: str):
            """Serve an explicitly configured legacy SPA for client-side routes."""
            if path.startswith(("api/", "ws/", "media/")):
                return JSONResponse(status_code=404, content={"detail": "Not found"})
            return FileResponse(legacy_index, media_type="text/html; charset=utf-8")
