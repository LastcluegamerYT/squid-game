"""
Vector Index — semantic nearest-neighbour search.

Two-stage automatic switching:
  ≤ FAISS_THRESHOLD posts  → exact cosine (numpy dot product on L2-normalised vectors)
  > FAISS_THRESHOLD posts  → FAISS IndexFlatIP for sub-linear ANN search

Both modes return identical interfaces: search(query_vec, top_k) → [(post_id, score)]

FAISS is optional — if not installed, exact mode is always used regardless of size.
"""
import logging
import threading
import numpy as np
from typing import List, Tuple, Optional

from app.ai.config import EMBEDDING_DIM, FAISS_THRESHOLD, CANDIDATE_POOL_SIZE

logger = logging.getLogger(__name__)

# Try importing FAISS — gracefully degrade if not available
try:
    import faiss  # type: ignore
    FAISS_AVAILABLE = True
    logger.info("[VectorIndex] FAISS available — will use ANN above threshold")
except ImportError:
    FAISS_AVAILABLE = False
    logger.info("[VectorIndex] FAISS not available — using exact cosine throughout")


class VectorIndex:
    """
    Unified vector similarity index.
    Internally uses numpy (exact) or FAISS (ANN) depending on corpus size.
    All stored vectors must be L2-normalised (cosine similarity = dot product).
    """

    def __init__(self):
        self._lock = threading.RLock()
        # Exact mode state
        self._matrix: Optional[np.ndarray] = None   # shape (N, D)
        self._post_ids: List[str] = []
        # FAISS mode state
        self._faiss_index = None
        self._faiss_post_ids: List[str] = []
        self._mode = "exact"

    def rebuild(self, matrix: np.ndarray, post_ids: List[str]):
        """
        Rebuild the index from scratch given a full embedding matrix.
        matrix: shape (N, D), float32, L2-normalised
        post_ids: aligned list of post IDs
        """
        try:
            matrix = np.asarray(matrix, dtype=np.float32)
            if matrix.ndim != 2 or matrix.shape[1] != EMBEDDING_DIM or len(matrix) != len(post_ids):
                raise ValueError(f"invalid matrix shape {getattr(matrix, 'shape', None)} for {len(post_ids)} IDs")
            if not np.isfinite(matrix).all():
                raise ValueError("matrix contains non-finite values")
        except (TypeError, ValueError) as exc:
            logger.error("[VectorIndex] Refusing invalid rebuild input: %s", exc)
            matrix = np.zeros((0, EMBEDDING_DIM), dtype=np.float32)
            post_ids = []

        if len(post_ids) == 0:
            with self._lock:
                self._matrix = None
                self._post_ids = []
                self._faiss_index = None
                self._faiss_post_ids = []
                self._mode = "exact"
            logger.info("[VectorIndex] Empty corpus — index cleared")
            return

        with self._lock:
            n, d = matrix.shape
            if FAISS_AVAILABLE and n > FAISS_THRESHOLD:
                self._matrix = None
                self._post_ids = []
                self._build_faiss(matrix, post_ids, d)
                self._mode = "faiss"
            else:
                self._faiss_index = None
                self._faiss_post_ids = []
                self._matrix = matrix.copy()
                self._post_ids = list(post_ids)
                self._mode = "exact"
                logger.info(f"[VectorIndex] Exact mode: {n} vectors, dim={d}")

    def _build_faiss(self, matrix: np.ndarray, post_ids: List[str], d: int):
        """Build FAISS IndexFlatIP (inner product = cosine for normalised vecs)."""
        try:
            index = faiss.IndexFlatIP(d)
            index.add(matrix.astype(np.float32))
            self._faiss_index = index
            self._faiss_post_ids = list(post_ids)
            logger.info(f"[VectorIndex] FAISS mode: {len(post_ids)} vectors, dim={d}")
        except Exception as e:
            logger.error(f"[VectorIndex] FAISS build failed: {e} — falling back to exact")
            self._faiss_index = None
            self._faiss_post_ids = []
            self._matrix = matrix.copy()
            self._post_ids = list(post_ids)
            self._mode = "exact"

    def add(self, post_id: str, vector: np.ndarray):
        """
        Incrementally add a single vector.
        In exact mode: appends to matrix.
        In FAISS mode: adds to index (IndexFlatIP supports add).
        """
        vec = np.asarray(vector, dtype=np.float32).reshape(1, -1)
        if vec.shape[1] != EMBEDDING_DIM or not np.isfinite(vec).all() or not np.any(vec):
            logger.error("[VectorIndex] Refusing invalid vector for %s", post_id)
            return
        with self._lock:
            if self._mode == "faiss" and self._faiss_index is not None:
                try:
                    self._faiss_index.add(vec)
                    self._faiss_post_ids.append(post_id)
                except Exception as e:
                    logger.error(f"[VectorIndex] FAISS add error: {e}")
            else:
                # Exact mode
                if post_id in self._post_ids:
                    # Update existing
                    idx = self._post_ids.index(post_id)
                    if self._matrix is not None:
                        self._matrix[idx] = vec[0]
                else:
                    if self._matrix is None:
                        self._matrix = vec.copy()
                    else:
                        self._matrix = np.vstack([self._matrix, vec])
                    self._post_ids.append(post_id)

                # Auto-upgrade to FAISS if threshold crossed
                if FAISS_AVAILABLE and len(self._post_ids) > FAISS_THRESHOLD and self._matrix is not None:
                    logger.info(f"[VectorIndex] Crossing FAISS threshold — upgrading index")
                    self._build_faiss(self._matrix, self._post_ids, self._matrix.shape[1])
                    self._mode = "faiss"

    def search(
        self,
        query_vec: np.ndarray,
        top_k: int = 50,
        exclude_ids: Optional[List[str]] = None,
    ) -> List[Tuple[str, float]]:
        """
        Find top_k most similar post IDs and their scores.
        Returns [(post_id, score)] sorted by score descending.
        Excludes any post_ids in exclude_ids.
        """
        exclude = set(exclude_ids or [])
        q = query_vec.astype(np.float32).flatten()
        if q.ndim != 1 or not np.isfinite(q).all() or np.linalg.norm(q) <= 1e-8:
            return []

        with self._lock:
            if self._mode == "faiss" and self._faiss_index is not None:
                return self._search_faiss(q, top_k, exclude)
            else:
                return self._search_exact(q, top_k, exclude)

    def _search_exact(
        self,
        query: np.ndarray,
        top_k: int,
        exclude: set,
    ) -> List[Tuple[str, float]]:
        if self._matrix is None or len(self._post_ids) == 0:
            return []
        try:
            # Dot product = cosine sim for L2-normalised vecs
            scores = self._matrix @ query   # shape (N,)
            # Get top_k+len(exclude) to ensure enough after filtering
            k = min(top_k + len(exclude) + 1, len(scores))
            top_indices = np.argpartition(scores, -k)[-k:]
            top_indices = top_indices[np.argsort(scores[top_indices])[::-1]]
            results = []
            for idx in top_indices:
                pid = self._post_ids[idx]
                if pid in exclude:
                    continue
                score = float(scores[idx])
                # Clamp to [0, 1] — cosine can be slightly outside due to float ops
                score = max(0.0, min(1.0, score))
                results.append((pid, score))
                if len(results) >= top_k:
                    break
            return results
        except Exception as e:
            logger.error(f"[VectorIndex] exact search error: {e}")
            return []

    def _search_faiss(
        self,
        query: np.ndarray,
        top_k: int,
        exclude: set,
    ) -> List[Tuple[str, float]]:
        try:
            fetch_k = min(top_k + len(exclude) + 10, self._faiss_index.ntotal)
            scores_arr, indices_arr = self._faiss_index.search(
                query.reshape(1, -1), fetch_k
            )
            results = []
            for idx, score in zip(indices_arr[0], scores_arr[0]):
                if idx < 0 or idx >= len(self._faiss_post_ids):
                    continue
                pid = self._faiss_post_ids[idx]
                if pid in exclude:
                    continue
                results.append((pid, float(max(0.0, min(1.0, score)))))
                if len(results) >= top_k:
                    break
            return results
        except Exception as e:
            logger.error(f"[VectorIndex] FAISS search error: {e}")
            return []

    @property
    def size(self) -> int:
        with self._lock:
            if self._mode == "faiss" and self._faiss_index:
                return self._faiss_index.ntotal
            return len(self._post_ids) if self._post_ids else 0

    @property
    def mode(self) -> str:
        with self._lock:
            return self._mode

    def status(self) -> dict:
        with self._lock:
            return {
                "mode": self._mode,
                "size": self.size,
                "faiss_available": FAISS_AVAILABLE,
                "faiss_threshold": FAISS_THRESHOLD,
            }


# Singleton
vector_index = VectorIndex()
