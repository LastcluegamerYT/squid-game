"""
Reranker Model — BAAI/bge-reranker-v2-m3 singleton.

Architecture:
  - Cross-encoder: reads [query_text, candidate_text] pair → relevance score
  - Run ONLY on top-N candidates after embedding retrieval (not on full corpus)
  - Falls back to embedding scores if model unavailable

Why cross-encoder over bi-encoder for reranking:
  - Bi-encoder embeds independently — fast but misses fine-grained interactions
  - Cross-encoder reads both texts jointly — slower but much higher precision
  - Running it on top 50 candidates keeps latency low
"""
import logging
import threading
import time
import numpy as np
from typing import List, Tuple, Dict, Any, Optional

from app.ai.config import (
    RERANKER_MODEL,
    RERANKER_MODEL_VERSION,
    RERANKER_CANDIDATE_LIMIT,
    EMBEDDING_DEVICE,
    MODEL_LOAD_RETRY_SECONDS,
)

logger = logging.getLogger(__name__)


def _post_to_reranker_text(post: Dict[str, Any]) -> str:
    """Serialize a post dict to a short text for the cross-encoder."""
    title   = (post.get("title") or "").strip()
    body    = (post.get("text") or post.get("summary") or "").strip()
    topics  = ", ".join((post.get("topics") or [])[:4])
    # Cross-encoder input: keep under ~512 tokens total (both texts combined)
    return f"{title}. {body[:400]} [{topics}]"


class RerankerModel:
    """Singleton cross-encoder wrapper for bge-reranker-v2-m3."""

    _instance: Optional["RerankerModel"] = None
    _lock = threading.Lock()

    def __init__(self):
        self._model = None
        self._model_lock = threading.Lock()
        self._inference_lock = threading.Lock()
        self._available = False
        self._load_error: Optional[str] = None
        self._last_load_attempt = 0.0

    @classmethod
    def get(cls) -> "RerankerModel":
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    @property
    def model_version(self) -> str:
        return RERANKER_MODEL_VERSION

    def _ensure_loaded(self) -> bool:
        if self._model is not None:
            return True
        if self._load_error and time.monotonic() - self._last_load_attempt < MODEL_LOAD_RETRY_SECONDS:
            return False
        with self._model_lock:
            if self._model is not None:
                return True
            if self._load_error and time.monotonic() - self._last_load_attempt < MODEL_LOAD_RETRY_SECONDS:
                return False
            try:
                import os
                os.environ.setdefault("USE_TF", "0")
                os.environ.setdefault("USE_TORCH", "1")
                os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")

                logger.info(f"[AI] Loading reranker: {RERANKER_MODEL}")
                from sentence_transformers import CrossEncoder
                self._last_load_attempt = time.monotonic()
                init_kwargs = {"device": EMBEDDING_DEVICE} if EMBEDDING_DEVICE else {}
                self._model = CrossEncoder(RERANKER_MODEL, max_length=512, **init_kwargs)
                self._available = True
                self._load_error = None
                logger.info(f"[AI] Reranker loaded: {RERANKER_MODEL}")
                return True
            except Exception as e:
                self._load_error = str(e)
                logger.error(f"[AI] Failed to load reranker: {e}")
                logger.warning("[AI] Falling back to embedding-only ranking")
                return False

    @property
    def is_available(self) -> bool:
        return self._available

    def rerank(
        self,
        query_post: Dict[str, Any],
        candidates: List[Tuple[str, float, Dict[str, Any]]],
        top_k: int = 20,
    ) -> List[Tuple[str, float]]:
        """
        Rerank candidate posts against a query post.

        Args:
            query_post: the source post dict (what we're finding related posts for)
            candidates: list of (post_id, embedding_score, post_dict)
            top_k: number to return

        Returns:
            [(post_id, calibrated_score)] sorted by score descending
        """
        if not candidates:
            return []

        if not self._ensure_loaded():
            # Fallback: return embedding scores unchanged
            return [
                (pid, score) for pid, score, _ in
                sorted(candidates, key=lambda x: x[1], reverse=True)[:top_k]
            ]

        candidate_window = candidates[:max(1, RERANKER_CANDIDATE_LIMIT)]
        query_text = _post_to_reranker_text(query_post)
        pairs = [(query_text, _post_to_reranker_text(post_dict))
                 for _, _, post_dict in candidate_window]

        try:
            with self._inference_lock:
                raw_scores = self._model.predict(pairs, show_progress_bar=False)
            # Sigmoid to map logits → [0, 1]
            calibrated = 1.0 / (1.0 + np.exp(-np.array(raw_scores, dtype=np.float32)))

            scored = [
                (candidate_window[i][0], float(calibrated[i]))
                for i in range(len(candidate_window))
            ]
            scored.sort(key=lambda x: -x[1])
            return scored[:top_k]
        except Exception as e:
            logger.error(f"[AI] Reranker predict error: {e}")
            # Fallback to embedding scores
            return [
                (pid, score) for pid, score, _ in
                sorted(candidates, key=lambda x: x[1], reverse=True)[:top_k]
            ]

    def rerank_for_query_text(
        self,
        query_text: str,
        candidates: List[Tuple[str, float, Dict[str, Any]]],
        top_k: int = 20,
    ) -> List[Tuple[str, float]]:
        """
        Rerank candidates against a free-form query string (e.g. user interest text).
        """
        if not candidates:
            return []
        if not self._ensure_loaded():
            return [(pid, s) for pid, s, _ in sorted(candidates, key=lambda x: -x[1])[:top_k]]

        candidate_window = candidates[:max(1, RERANKER_CANDIDATE_LIMIT)]
        pairs = [(query_text[:500], _post_to_reranker_text(pd)) for _, _, pd in candidate_window]
        try:
            with self._inference_lock:
                raw = self._model.predict(pairs, show_progress_bar=False)
            cal = 1.0 / (1.0 + np.exp(-np.array(raw, dtype=np.float32)))
            scored = [(candidate_window[i][0], float(cal[i])) for i in range(len(candidate_window))]
            scored.sort(key=lambda x: -x[1])
            return scored[:top_k]
        except Exception as e:
            logger.error(f"[AI] rerank_for_query_text error: {e}")
            return [(pid, s) for pid, s, _ in sorted(candidates, key=lambda x: -x[1])[:top_k]]


# Singleton
reranker_model = RerankerModel.get()
