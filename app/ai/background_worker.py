"""
Background Worker — non-blocking thread-pool for AI jobs.

Jobs:
  - embed_new_post(post_id, post_dict)  : called on post creation
  - rebuild_all_embeddings(db)          : full re-embed (model change / admin)
  - rebuild_clusters(db)                : HDBSCAN rerun
  - rebuild_vector_index()              : reload FAISS/exact index from store

Jobs are deduplicated — if post_id already queued for embedding, won't re-queue.
Uses ThreadPoolExecutor(2) — keeps API response times fast.
"""
import logging
import threading
from concurrent.futures import ThreadPoolExecutor
import numpy as np
from typing import Dict, Any, Set, Optional
from datetime import datetime

logger = logging.getLogger(__name__)


class BackgroundWorker:
    def __init__(self, max_workers: int = 2, queue_max: int = 500):
        self._executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="pulse-ai")
        self._slots = threading.BoundedSemaphore(max_workers + max(0, queue_max))
        self._queued_ids: Set[str] = set()   # dedup set for embedding jobs
        self._lock = threading.Lock()
        self._stopped = False
        self._stats = {
            "jobs_submitted": 0,
            "jobs_completed": 0,
            "jobs_failed": 0,
            "jobs_rejected": 0,
            "last_job_at": None,
        }

    # ─── Public job submission ────────────────────────────────────────────────

    def embed_new_post(self, post_id: str, post_dict: Dict[str, Any]):
        """Queue an embedding job for a single new post."""
        with self._lock:
            if post_id in self._queued_ids:
                return   # already queued
            self._queued_ids.add(post_id)
        if not self._submit(self._job_embed_post, post_id, dict(post_dict)):
            with self._lock:
                self._queued_ids.discard(post_id)

    def rebuild_all_embeddings(self, db_posts: Dict[str, Dict[str, Any]]):
        """Queue a full embedding rebuild (all posts missing current model version)."""
        self._submit(self._job_rebuild_all, db_posts)

    def rebuild_clusters(self, db_posts: Dict[str, Dict[str, Any]]):
        """Queue a HDBSCAN cluster rebuild."""
        self._submit(self._job_rebuild_clusters, db_posts)

    def rebuild_vector_index(self):
        """Reload the vector index from embedding store."""
        self._submit(self._job_rebuild_index)

    def embed_missing_on_startup(self, db_posts: Dict[str, Dict[str, Any]]):
        """
        Called at server startup: embed any posts that are missing or stale.
        Runs in background so startup is not blocked.
        """
        self._submit(self._job_startup_embed, db_posts)

    # ─── Internal job runners ─────────────────────────────────────────────────

    def _submit(self, fn, *args):
        with self._lock:
            if self._stopped:
                logger.warning("[BackgroundWorker] Ignoring %s during shutdown", fn.__name__)
                return False
        if not self._slots.acquire(blocking=False):
            with self._lock:
                self._stats["jobs_rejected"] += 1
            logger.warning("[BackgroundWorker] Queue is full; rejected %s", fn.__name__)
            return False
        with self._lock:
            self._stats["jobs_submitted"] += 1
        try:
            self._executor.submit(self._wrapped_run, fn, *args)
            return True
        except Exception:
            self._slots.release()
            raise

    def _wrapped_run(self, fn, *args):
        try:
            fn(*args)
            with self._lock:
                self._stats["jobs_completed"] += 1
                self._stats["last_job_at"] = datetime.utcnow().isoformat()
        except Exception as e:
            with self._lock:
                self._stats["jobs_failed"] += 1
            logger.error(f"[BackgroundWorker] Job {fn.__name__} failed: {e}", exc_info=True)
        finally:
            self._slots.release()

    def _job_embed_post(self, post_id: str, post_dict: Dict[str, Any]):
        from app.ai.embedding_model import embedding_model
        from app.ai.embedding_store import embedding_store
        from app.ai.vector_index import vector_index
        from app.ai.similarity import similarity_service
        from app.ai.quality_scorer import score_post_quality

        try:
            # Quality score (fast, deterministic).
            post_dict["ai_quality_score"] = score_post_quality(post_dict)

            if not embedding_model.is_available and not embedding_model._ensure_loaded():
                logger.warning("[BackgroundWorker] Model unavailable; skipped embedding %s", post_id)
                return

            vecs = embedding_model.encode_posts([post_dict])
            if len(vecs) > 0 and np.any(vecs[0]):
                vec = vecs[0]
                embedding_store.add_or_update(post_id, vec)
                if vector_index.mode == "faiss":
                    matrix, post_ids = embedding_store.get_all()
                    vector_index.rebuild(matrix, post_ids)
                else:
                    vector_index.add(post_id, vec)
                # One new vector changes related candidates for every existing
                # idea, not only the just-created post.
                similarity_service.invalidate_cache()
                logger.info(f"[BackgroundWorker] Embedded post {post_id}")
        finally:
            # A failed inference must never leave the dedupe key stuck forever.
            with self._lock:
                self._queued_ids.discard(post_id)

    def _job_rebuild_all(self, db_posts: Dict[str, Dict[str, Any]]):
        from app.ai.embedding_model import embedding_model
        from app.ai.embedding_store import embedding_store
        from app.ai.vector_index import vector_index
        from app.ai.similarity import similarity_service
        from app.ai.quality_scorer import score_post_quality

        if not embedding_model.is_available and not embedding_model._ensure_loaded():
            logger.warning("[BackgroundWorker] Embedding model unavailable; rebuild will retry later")
            return

        missing = embedding_store.get_missing_post_ids(list(db_posts.keys()))
        if not missing:
            logger.info("[BackgroundWorker] All embeddings up to date")
            self._job_rebuild_index()
            return

        logger.info(f"[BackgroundWorker] Embedding {len(missing)} posts...")
        from app.ai.config import EMBEDDING_BATCH_SIZE
        batch_size = EMBEDDING_BATCH_SIZE
        for i in range(0, len(missing), batch_size):
            batch_ids  = missing[i:i+batch_size]
            batch_posts = [db_posts[pid] for pid in batch_ids if pid in db_posts]
            if not batch_posts:
                continue

            # Quality scores
            for pd in batch_posts:
                pd["ai_quality_score"] = score_post_quality(pd)

            vecs = embedding_model.encode_posts(batch_posts)
            for pid, vec in zip(batch_ids, vecs):
                if np.any(vec):
                    embedding_store.add_or_update(pid, vec)

            logger.info(f"[BackgroundWorker] Embedded batch {i//batch_size + 1}/{(len(missing)+batch_size-1)//batch_size}")

        # Rebuild vector index after all embeddings done
        self._job_rebuild_index()
        similarity_service.invalidate_cache()
        logger.info("[BackgroundWorker] Full rebuild complete")

    def _job_rebuild_index(self):
        from app.ai.embedding_store import embedding_store
        from app.ai.vector_index import vector_index

        matrix, post_ids = embedding_store.get_all()
        vector_index.rebuild(matrix, post_ids)
        if len(post_ids) > 0:
            logger.info(f"[BackgroundWorker] Vector index rebuilt: {len(post_ids)} posts, mode={vector_index.mode}")
        else:
            logger.info("[BackgroundWorker] No embeddings — index not built")

    def _job_startup_embed(self, db_posts: Dict[str, Dict[str, Any]]):
        """Startup: embed missing + rebuild index."""
        logger.info("[BackgroundWorker] Startup embedding job starting...")
        self._job_rebuild_all(db_posts)
        logger.info("[BackgroundWorker] Startup embedding job complete")

    def _job_rebuild_clusters(self, db_posts: Dict[str, Dict[str, Any]]):
        from app.ai.clustering import clustering_service
        result = clustering_service.rebuild_clusters(db_posts)
        logger.info(f"[BackgroundWorker] Cluster rebuild: {result}")

    # ─── Status ───────────────────────────────────────────────────────────────

    def status(self) -> Dict[str, Any]:
        with self._lock:
            return {
                **self._stats,
                "queued_embedding_jobs": len(self._queued_ids),
                "stopped": self._stopped,
            }

    def shutdown(self, wait: bool = True):
        """Stop accepting background work during a graceful application shutdown."""
        with self._lock:
            self._stopped = True
        self._executor.shutdown(wait=wait, cancel_futures=True)


# Singleton
from app.ai.config import WORKER_THREADS, EMBEDDING_QUEUE_MAX

background_worker = BackgroundWorker(max_workers=WORKER_THREADS, queue_max=EMBEDDING_QUEUE_MAX)
