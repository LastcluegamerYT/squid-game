"""Reset the local development data store without changing its schema.

Run only while the API is stopped:
    python scripts/reset_development_data.py

It clears persisted application data, uploaded files, and derived AI artifacts,
then recreates the JSON store with the supported category catalogue at zero
usage. Authentication configuration and source code are deliberately untouched.
"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path


BACKEND_DIR = Path(__file__).resolve().parents[1]
DB_PATH = BACKEND_DIR / "app" / "database" / "platform_data.json"
UPLOADS_DIR = BACKEND_DIR / "uploads"
AI_STORE_DIR = BACKEND_DIR / "app" / "ai" / "store"

if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


def fresh_categories() -> dict[str, dict]:
    from app.database.seed_data import SEED_CATEGORIES

    return {
        category["id"]: {
            **category,
            "posts_count": 0,
            "followers_count": 0,
        }
        for category in SEED_CATEGORIES
    }


def main() -> None:
    payload = {
        "users": {},
        "usernames": {},
        "user_ids": {},
        "posts": {},
        "comments": {},
        "reactions": {},
        "comment_likes": {},
        "hides": {},
        "follows": {},
        "followers": {},
        "user_timelines": {},
        "categories": fresh_categories(),
        "events": [],
        "conversations": {},
        "messages": {},
        "user_conversations": {},
        "notifications": {},
        "notification_reply_windows": {},
        "notification_mention_windows": {},
    }
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    DB_PATH.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    for directory in (UPLOADS_DIR / "avatars", UPLOADS_DIR / "posts"):
        if directory.exists():
            shutil.rmtree(directory)
        directory.mkdir(parents=True, exist_ok=True)

    for name in (
        "embeddings.npy",
        "meta.json",
        "user_profiles.json",
        "feedback_pairs.jsonl",
        "clusters.json",
        "related_cache.json",
    ):
        (AI_STORE_DIR / name).unlink(missing_ok=True)

    print(f"Reset complete: {DB_PATH}")
    print(f"Categories retained: {len(payload['categories'])}; users/posts/comments/messages: 0")
    print("Uploads and persisted AI/recommendation artifacts cleared.")


if __name__ == "__main__":
    main()
