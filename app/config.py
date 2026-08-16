import os
from typing import List
from pydantic import BaseModel


class Settings(BaseModel):
    PROJECT_NAME: str = os.getenv("PROJECT_NAME", "Pulse API")
    VERSION: str = "2.0.0"
    API_V1_STR: str = "/api"

    # ── Firebase ──────────────────────────────────────────────────────────────
    FIREBASE_PROJECT_ID: str = os.getenv("FIREBASE_PROJECT_ID", "my-platform-13264")

    # ── CORS (override via env var CORS_ORIGINS=https://myapp.com,...) ───────
    CORS_ORIGINS: List[str] = [
        "http://localhost:3000",
        "http://localhost:5000",
        "http://localhost:5500",
        "http://localhost:8000",
        "http://localhost:8080",
        "http://127.0.0.1:3000",
        "http://127.0.0.1:5000",
        "http://127.0.0.1:5500",
        "http://127.0.0.1:8000",
        "http://127.0.0.1:8080",
        "https://chat.prashant-pandey.com.np",
        "http://chat.prashant-pandey.com.np",
        # add your production domain here:
        # "https://mypulse.app",
    ]

    # ── Feed ranking weights ──────────────────────────────────────────────────
    WEIGHT_SHARE:   float = 20.0
    WEIGHT_COMMENT: float = 10.0
    WEIGHT_LIKE:    float = 5.0
    WEIGHT_FIRE:    float = 7.5
    WEIGHT_BULB:    float = 7.5
    WEIGHT_CLICK:   float = 1.0
    WEIGHT_HIDE:    float = 20.0
    WEIGHT_BLOCK:   float = 50.0

    # ── Time decay (per hour elapsed) ─────────────────────────────────────────
    TIME_DECAY_LAMBDA: float = 0.05

    # ── Feed diversity ratios ─────────────────────────────────────────────────
    FEED_INTEREST_RATIO:    float = 0.75   # 75% interest-matched
    FEED_TRENDING_RATIO:    float = 0.15   # 15% trending
    FEED_SERENDIPITY_RATIO: float = 0.10   # 10% discovery

    # ── Pagination ───────────────────────────────────────────────────────────
    DEFAULT_FEED_PAGE_SIZE: int = 10

    # ── Content limits ───────────────────────────────────────────────────────
    # No file-size limits on uploads (per user preference)
    MAX_USERNAME_LEN:   int = 30
    MAX_BIO_LEN:        int = 500          # characters
    MAX_POST_TEXT_LEN:  int = 50_000       # very generous — no practical limit
    MAX_POST_TITLE_LEN: int = 200
    MAX_IMAGES_PER_POST: int = 4           # up to 4 images per post
    MAX_COMMENT_LEN:    int = 2000
    MAX_MESSAGE_LEN:    int = 4000

    # ── DB persistence ───────────────────────────────────────────────────────
    # Path is resolved relative to db.py location if relative
    DB_FILE_PATH: str = os.getenv("DB_FILE_PATH", "")

    # ── AI ───────────────────────────────────────────────────────────────────
    AI_EMBEDDING_MODEL: str = os.getenv("PULSE_EMBEDDING_MODEL", "intfloat/multilingual-e5-small")


settings = Settings()
