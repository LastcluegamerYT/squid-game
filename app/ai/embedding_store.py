"""
Embedding Store — persistent disk storage for post embeddings.

Layout:
  store/embeddings.npy   — float32 matrix, shape (N, EMBEDDING_DIM)
  store/meta.json        — list of {post_id, model_version, embedded_at, matrix_idx}

Design:
- O(1) post_id → matrix row lookup via in-memory dict
- Incremental add/update: no full rewrite on each new post
- Stale detection: compare stored model_version to current EMBEDDING_MODEL_VERSION
- Thread-safe for concurrent reads; write lock for mutations
"""
import os
import json
import logging
import threading
import tempfile
import numpy as np
from typing import Dict, List, Optional, Tuple
from datetime import datetime

from app.ai.config import (
    EMBEDDINGS_PATH, EMBEDDING_META_PATH,
    EMBEDDING_DIM, EMBEDDING_MODEL_VERSION,
)

logger = logging.getLogger(__name__)


class EmbeddingStore:
    """Thread-safe persistent embedding store backed by numpy + JSON."""

    def __init__(self):
        self._lock = threading.RLock()
        # In-memory state
        self._matrix: np.ndarray = np.zeros((0, EMBEDDING_DIM), dtype=np.float32)
        # post_id → row index in _matrix
        self._id_to_idx: Dict[str, int] = {}
        # post_id → meta dict {model_version, embedded_at}
        self._meta: Dict[str, dict] = {}
        # Free rows (from deleted posts) — reuse them
        self._free_rows: List[int] = []
        self._load()

    # ─── Persistence ─────────────────────────────────────────────────────────

    def _load(self):
        """Load matrix and metadata from disk."""
        with self._lock:
            try:
                if os.path.exists(EMBEDDINGS_PATH) and os.path.exists(EMBEDDING_META_PATH):
                    matrix = np.load(EMBEDDINGS_PATH)
                    if matrix.ndim != 2 or matrix.shape[1] != EMBEDDING_DIM:
                        raise ValueError(
                            f"stored embedding dimension {getattr(matrix, 'shape', None)} "
                            f"does not match configured dimension {EMBEDDING_DIM}"
                        )
                    with open(EMBEDDING_META_PATH, "r", encoding="utf-8") as f:
                        meta_list = json.load(f)
                    if not isinstance(meta_list, list):
                        raise ValueError("embedding metadata must be a list")
                    self._matrix = matrix.astype(np.float32, copy=False)
                    self._id_to_idx = {}
                    self._meta = {}
                    for entry in meta_list:
                        pid = entry.get("post_id")
                        idx = entry.get("matrix_idx")
                        if not isinstance(pid, str) or not isinstance(idx, int) or idx < 0 or idx >= len(self._matrix):
                            continue
                        if pid in self._id_to_idx:
                            continue
                        self._id_to_idx[pid] = idx
                        self._meta[pid] = {
                            "model_version": entry.get("model_version", ""),
                            "embedded_at":   entry.get("embedded_at", ""),
                        }
                    logger.info(f"[EmbeddingStore] Loaded {len(self._id_to_idx)} embeddings")
                else:
                    logger.info("[EmbeddingStore] No stored embeddings found — starting fresh")
            except Exception as e:
                logger.warning(f"[EmbeddingStore] Ignoring incompatible or invalid store: {e}")
                self._matrix = np.zeros((0, EMBEDDING_DIM), dtype=np.float32)
                self._id_to_idx = {}
                self._meta = {}

    def _save(self):
        """Persist matrix and metadata atomically to avoid partial writes."""
        matrix_tmp = None
        meta_tmp = None
        try:
            store_dir = os.path.dirname(EMBEDDINGS_PATH)
            os.makedirs(store_dir, exist_ok=True)
            meta_list = [
                {
                    "post_id":       pid,
                    "matrix_idx":    idx,
                    "model_version": self._meta[pid]["model_version"],
                    "embedded_at":   self._meta[pid]["embedded_at"],
                }
                for pid, idx in self._id_to_idx.items()
            ]
            matrix_fd, matrix_tmp = tempfile.mkstemp(prefix="embeddings-", suffix=".npy", dir=store_dir)
            with os.fdopen(matrix_fd, "wb") as f:
                np.save(f, self._matrix)
            meta_fd, meta_tmp = tempfile.mkstemp(prefix="embedding-meta-", suffix=".json", dir=store_dir)
            try:
                with os.fdopen(meta_fd, "w", encoding="utf-8") as f:
                    json.dump(meta_list, f, indent=2)
                os.replace(matrix_tmp, EMBEDDINGS_PATH)
                matrix_tmp = None
                os.replace(meta_tmp, EMBEDDING_META_PATH)
                meta_tmp = None
            finally:
                for temp_path in (matrix_tmp, meta_tmp):
                    if temp_path and os.path.exists(temp_path):
                        os.unlink(temp_path)
        except Exception as e:
            logger.error(f"[EmbeddingStore] Save error: {e}")

    # ─── Public API ──────────────────────────────────────────────────────────

    def add_or_update(self, post_id: str, vector: np.ndarray):
        """
        Store or update the embedding for a post.
        If the post already exists, overwrites its row in the matrix.
        """
        vec = vector.astype(np.float32).flatten()
        if len(vec) != EMBEDDING_DIM:
            logger.error(f"[EmbeddingStore] Wrong dim for {post_id}: {len(vec)} != {EMBEDDING_DIM}")
            return
        if not np.isfinite(vec).all():
            logger.error(f"[EmbeddingStore] Refusing non-finite embedding for {post_id}")
            return

        with self._lock:
            if post_id in self._id_to_idx:
                # Overwrite existing row
                idx = self._id_to_idx[post_id]
                self._matrix[idx] = vec
            else:
                # Append new row (or reuse a free row)
                if self._free_rows:
                    idx = self._free_rows.pop()
                    self._matrix[idx] = vec
                else:
                    # Grow matrix by 1 row
                    self._matrix = np.vstack([self._matrix, vec.reshape(1, -1)])
                    idx = len(self._matrix) - 1
                self._id_to_idx[post_id] = idx

            self._meta[post_id] = {
                "model_version": EMBEDDING_MODEL_VERSION,
                "embedded_at":   datetime.utcnow().isoformat(),
            }
            self._save()

    def get(self, post_id: str) -> Optional[np.ndarray]:
        """Retrieve embedding vector for a post. Returns None if not found."""
        with self._lock:
            idx = self._id_to_idx.get(post_id)
            if idx is None:
                return None
            return self._matrix[idx].copy()

    def get_all(self) -> Tuple[np.ndarray, List[str]]:
        """
        Return (matrix, post_ids_list) in aligned order.
        matrix[i] corresponds to post_ids[i].
        Excludes any free/deleted rows.
        """
        with self._lock:
            if not self._id_to_idx:
                return np.zeros((0, EMBEDDING_DIM), dtype=np.float32), []
            # Build aligned arrays
            items = sorted(self._id_to_idx.items(), key=lambda x: x[1])
            post_ids = [pid for pid, _ in items]
            indices  = [idx for _, idx in items]
            matrix   = self._matrix[indices].copy()
            return matrix, post_ids

    def needs_reembedding(self, post_id: str) -> bool:
        """Returns True if the post has no embedding OR its embedding is stale (model changed)."""
        with self._lock:
            if post_id not in self._meta:
                return True
            return self._meta[post_id].get("model_version") != EMBEDDING_MODEL_VERSION

    def get_missing_post_ids(self, all_post_ids: List[str]) -> List[str]:
        """Return which post IDs from the given list have no current embedding."""
        with self._lock:
            return [
                pid for pid in all_post_ids
                if pid not in self._id_to_idx
                or self._meta.get(pid, {}).get("model_version") != EMBEDDING_MODEL_VERSION
            ]

    def remove(self, post_id: str):
        """Remove a post embedding (marks the row as free for reuse)."""
        with self._lock:
            if post_id in self._id_to_idx:
                idx = self._id_to_idx.pop(post_id)
                self._meta.pop(post_id, None)
                self._free_rows.append(idx)
                # Zero out the freed row to prevent accidental matches
                self._matrix[idx] = 0.0
                self._save()

    def delete(self, post_id: str):
        """Backward-compatible alias used by the existing post deletion path."""
        self.remove(post_id)

    @property
    def count(self) -> int:
        with self._lock:
            return len(self._id_to_idx)

    @property
    def model_version(self) -> str:
        return EMBEDDING_MODEL_VERSION

    def status(self) -> dict:
        """Admin status snapshot."""
        with self._lock:
            return {
                "total_embeddings": len(self._id_to_idx),
                "matrix_rows": len(self._matrix),
                "model_version": EMBEDDING_MODEL_VERSION,
                "free_rows": len(self._free_rows),
                "store_path": EMBEDDINGS_PATH,
            }


# Singleton
embedding_store = EmbeddingStore()
