"""
Event Service — records user interaction events for the feed ranking engine.
Also handles batched disk persistence so high-frequency view events don't
hammer disk I/O on every request.
"""
import threading
import time
from typing import Optional, Dict, Any
from app.models.event import UserEvent, EventType
from app.database.db import db


class EventService:
    def __init__(self):
        # Batched view counter: post_id → pending view count
        self._pending_views: Dict[str, int] = {}
        self._pending_lock = threading.Lock()
        # Flush every 10 seconds in background
        self._flush_thread = threading.Thread(target=self._flush_loop, daemon=True)
        self._flush_thread.start()

    def record_event(
        self,
        user_id: str,
        event_type: EventType,
        post_id: Optional[str] = None,
        target_user_id: Optional[str] = None,
        topic: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None
    ) -> UserEvent:
        event = UserEvent(
            user_id=user_id,
            event_type=event_type,
            post_id=post_id,
            target_user_id=target_user_id,
            topic=topic,
            metadata=metadata or {}
        )

        # Batch view increments — don't write to disk on every view
        if post_id and event_type == EventType.VIEW:
            with self._pending_lock:
                self._pending_views[post_id] = self._pending_views.get(post_id, 0) + 1
            # Immediately update in-memory counter (no disk write yet)
            post = db.posts.get(post_id)
            if post:
                stats = post.setdefault("stats", {})
                stats["views"] = stats.get("views", 0) + 1
        else:
            # All other events: record normally (writes to disk for important events)
            db.log_event(event)

        return event

    def _flush_loop(self):
        """Background thread: flush pending view counts to disk every 10s."""
        while True:
            time.sleep(10)
            self._flush_views()

    def _flush_views(self):
        with self._pending_lock:
            if not self._pending_views:
                return
            pending = self._pending_views.copy()
            self._pending_views.clear()

        # Persist batched views
        with db._lock:
            for post_id, count in pending.items():
                post = db.posts.get(post_id)
                if post:
                    stats = post.setdefault("stats", {})
                    # Already applied in-memory above; just ensure disk is up to date
            db._save()


event_service = EventService()
