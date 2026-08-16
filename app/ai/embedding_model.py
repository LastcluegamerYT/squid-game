"""
Embedding Model — multilingual-e5-small singleton.

Loads once on first use (lazy initialization).
Falls back gracefully if model unavailable — returns zero vectors with a warning.
Uses E5 prefix convention: "passage: " for stored posts, "query: " for queries.
"""
import logging
import threading
import time
import numpy as np
from typing import List, Optional, Dict, Any

from app.ai.config import (
    EMBEDDING_MODEL, EMBEDDING_MODEL_VERSION,
    EMBEDDING_BATCH_SIZE, EMBEDDING_MAX_TOKENS, EMBEDDING_DIM,
    E5_PASSAGE_PREFIX, E5_QUERY_PREFIX, EMBEDDING_DEVICE,
    MODEL_LOAD_RETRY_SECONDS,
)

logger = logging.getLogger(__name__)


def _build_post_text(post: Dict[str, Any]) -> str:
    """
    Concatenate meaningful post fields into a single passage string.
    Category/topics weighted by repetition but capped so they don't dominate meaning.
    """
    title    = (post.get("title") or "").strip()
    body     = (post.get("text") or post.get("summary") or "").strip()
    topics   = " ".join((post.get("topics") or [])[:4])  # at most 4 topics in text
    # Format: title (repeated for weight) + body + topics
    combined = f"{title}. {title}. {body} {topics}".strip()
    # Truncate at ~2000 chars — tokenizer will handle exact token truncation
    return combined[:2000]


class EmbeddingModel:
    """Singleton wrapper around sentence-transformers for multilingual-e5-small."""

    _instance: Optional["EmbeddingModel"] = None
    _lock = threading.Lock()

    def __init__(self):
        self._model = None
        self._model_lock = threading.Lock()
        # SentenceTransformers inference can otherwise contend for the same GPU
        # (or CPU thread pool) across request and background-worker threads.
        self._inference_lock = threading.Lock()
        self._available = False
        self._load_error: Optional[str] = None
        self._last_load_attempt = 0.0

    @classmethod
    def get(cls) -> "EmbeddingModel":
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    def _ensure_loaded(self) -> bool:
        """Lazy-load the model on first use. Returns True if model is ready."""
        if self._model is not None:
            return True
        if (
            self._load_error
            and time.monotonic() - self._last_load_attempt < MODEL_LOAD_RETRY_SECONDS
        ):
            return False

        with self._model_lock:
            if self._model is not None:
                return True
            if (
                self._load_error
                and time.monotonic() - self._last_load_attempt < MODEL_LOAD_RETRY_SECONDS
            ):
                return False
            try:
                import os
                # Force PyTorch backend — prevents Keras 3 / TensorFlow conflict
                os.environ.setdefault("USE_TF", "0")
                os.environ.setdefault("USE_TORCH", "1")
                os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")

                logger.info(f"[AI] Loading embedding model: {EMBEDDING_MODEL}")
                from sentence_transformers import SentenceTransformer
                self._last_load_attempt = time.monotonic()
                init_kwargs = {"device": EMBEDDING_DEVICE} if EMBEDDING_DEVICE else {}
                model = SentenceTransformer(EMBEDDING_MODEL, **init_kwargs)
                actual_dim = int(model.get_sentence_embedding_dimension())
                if actual_dim != EMBEDDING_DIM:
                    raise RuntimeError(
                        f"Embedding model dimension {actual_dim} does not match "
                        f"PULSE_EMBEDDING_DIM={EMBEDDING_DIM}. Configure both "
                        "model version and dimension before deploying a model swap."
                    )
                self._model = model
                self._available = True
                self._load_error = None  # clear any prior error
                logger.info(f"[AI] Embedding model loaded: {EMBEDDING_MODEL} (dim={EMBEDDING_DIM})")
                return True
            except Exception as e:
                self._model = None
                self._available = False
                self._load_error = str(e)
                logger.error(f"[AI] Failed to load embedding model: {e}")
                logger.warning("[AI] Falling back to zero-vector embeddings — semantic search disabled")
                return False

    @staticmethod
    def _validated_vectors(vecs: Any, expected_rows: int) -> np.ndarray:
        """Return finite, L2-normalised vectors or a shape-safe zero matrix."""
        try:
            matrix = np.asarray(vecs, dtype=np.float32)
            if matrix.shape != (expected_rows, EMBEDDING_DIM):
                raise ValueError(f"unexpected embedding shape {matrix.shape}")
            if not np.isfinite(matrix).all():
                raise ValueError("embedding contains non-finite values")
            norms = np.linalg.norm(matrix, axis=1, keepdims=True)
            nonzero = norms[:, 0] > 1e-8
            if np.any(nonzero):
                matrix[nonzero] = matrix[nonzero] / norms[nonzero]
            matrix[~nonzero] = 0.0
            return matrix
        except Exception as e:
            logger.error(f"[AI] Invalid embedding output: {e}")
            return np.zeros((expected_rows, EMBEDDING_DIM), dtype=np.float32)

    def _encode(self, texts: List[str], batch_size: int) -> np.ndarray:
        """Serialize model inference and validate its output before it reaches storage."""
        try:
            with self._inference_lock:
                # The model is only assigned once after a successful load.
                vecs = self._model.encode(
                    texts,
                    batch_size=batch_size,
                    normalize_embeddings=True,
                    show_progress_bar=False,
                    convert_to_numpy=True,
                )
            return self._validated_vectors(vecs, len(texts))
        except Exception as e:
            logger.error(f"[AI] embedding encode error: {e}")
            return np.zeros((len(texts), EMBEDDING_DIM), dtype=np.float32)

    @property
    def is_available(self) -> bool:
        return self._available

    @property
    def model_version(self) -> str:
        return EMBEDDING_MODEL_VERSION

    def encode_posts(self, posts: List[Dict[str, Any]]) -> np.ndarray:
        """
        Encode a list of post dicts into a float32 embedding matrix.
        Shape: (len(posts), EMBEDDING_DIM)
        Falls back to zero matrix if model unavailable.
        """
        if not posts:
            return np.zeros((0, EMBEDDING_DIM), dtype=np.float32)

        if not self._ensure_loaded():
            logger.warning(f"[AI] encode_posts: model unavailable, returning zeros for {len(posts)} posts")
            return np.zeros((len(posts), EMBEDDING_DIM), dtype=np.float32)

        texts = [E5_PASSAGE_PREFIX + _build_post_text(p) for p in posts]
        return self._encode(texts, batch_size=EMBEDDING_BATCH_SIZE)

    def encode_query(self, text: str) -> np.ndarray:
        """
        Encode a single query string (user search / user interest text).
        Returns shape (EMBEDDING_DIM,) — 1D.
        """
        if not text or not text.strip():
            return np.zeros(EMBEDDING_DIM, dtype=np.float32)

        if not self._ensure_loaded():
            return np.zeros(EMBEDDING_DIM, dtype=np.float32)

        query_text = E5_QUERY_PREFIX + text.strip()[:1000]
        return self._encode([query_text], batch_size=1)[0]

    def encode_texts(self, texts: List[str], is_query: bool = False) -> np.ndarray:
        """
        Encode arbitrary text strings (e.g., category names, interest labels).
        """
        if not texts:
            return np.zeros((0, EMBEDDING_DIM), dtype=np.float32)
        if not self._ensure_loaded():
            return np.zeros((len(texts), EMBEDDING_DIM), dtype=np.float32)

        prefix = E5_QUERY_PREFIX if is_query else E5_PASSAGE_PREFIX
        prefixed = [prefix + t.strip()[:1000] for t in texts]
        return self._encode(prefixed, batch_size=EMBEDDING_BATCH_SIZE)


# Module-level singleton accessor
embedding_model = EmbeddingModel.get()
