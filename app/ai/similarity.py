"""
Similarity Service — full pipeline for related-post retrieval.

Pipeline (per the implementation plan):
  Query post
  → encode with embedding model
  → vector index search → top CANDIDATE_POOL_SIZE candidates
  → reranker (cross-encoder) → reranked top N
  → remove original post + obvious duplicates
  → MMR diversity pass
  → return top RELATED_TOP_N with calibrated scores + explanations

Scores are calibrated to [0, 1] semantic-relatedness bands.
Results are cached per post_id and invalidated when new posts are indexed.
"""
import logging
import json
import os
import threading
import tempfile
from typing import List, Dict, Any, Optional, Tuple
from datetime import datetime, timezone

import numpy as np

from app.ai.config import (
    CANDIDATE_POOL_SIZE, RELATED_TOP_N, NEAR_DUPLICATE_THRESHOLD,
    SIMILARITY_BANDS, MMR_LAMBDA, RELATED_CACHE_PATH,
    EMBEDDING_MODEL_VERSION, RELATED_CACHE_TTL_SECONDS,
)
from app.ai.embedding_model import embedding_model
from app.ai.embedding_store import embedding_store
from app.ai.vector_index import vector_index
from app.ai.reranker_model import reranker_model

logger = logging.getLogger(__name__)


def _get_relationship_label(score: float) -> str:
    for lo, hi, label in SIMILARITY_BANDS:
        if lo <= score <= hi:
            return label
    return "unrelated"


def _mmr_select(
    query_vec: np.ndarray,
    candidates: List[Tuple[str, float]],
    post_embeddings: Dict[str, np.ndarray],
    top_k: int,
    mmr_lambda: float = MMR_LAMBDA,
) -> List[Tuple[str, float]]:
    """
    Maximal Marginal Relevance selection.
    Balances relevance to query with diversity among selected items.
    mmr_lambda=1.0 → pure relevance, mmr_lambda=0.0 → pure diversity.
    """
    if not candidates:
        return []

    selected: List[Tuple[str, float]] = []
    remaining = list(candidates)

    while remaining and len(selected) < top_k:
        mmr_scores = []
        for pid, rel_score in remaining:
            # Relevance term
            relevance = mmr_lambda * rel_score
            # Diversity term: max similarity to already-selected
            if not selected or pid not in post_embeddings:
                redundancy = 0.0
            else:
                pid_vec = post_embeddings.get(pid)
                if pid_vec is None:
                    redundancy = 0.0
                else:
                    sims = [
                        float(np.dot(pid_vec, post_embeddings[s_pid]))
                        for s_pid, _ in selected
                        if s_pid in post_embeddings
                    ]
                    redundancy = (1 - mmr_lambda) * max(sims) if sims else 0.0
            mmr_scores.append((pid, rel_score, relevance - redundancy))

        # Pick the one with highest MMR score
        best = max(mmr_scores, key=lambda x: x[2])
        selected.append((best[0], best[1]))
        remaining = [(p, s) for p, s in remaining if p != best[0]]

    return selected


class SimilarityService:
    """High-level related-post retrieval with caching and full pipeline."""

    def __init__(self):
        self._cache: Dict[str, Any] = {}
        self._cache_lock = threading.RLock()
        self._cache_model_version = EMBEDDING_MODEL_VERSION
        self._load_cache()

    def _load_cache(self):
        """Load persisted related-post cache."""
        try:
            if os.path.exists(RELATED_CACHE_PATH):
                with open(RELATED_CACHE_PATH, "r", encoding="utf-8") as f:
                    data = json.load(f)
                # Invalidate if model version changed
                if data.get("model_version") == EMBEDDING_MODEL_VERSION:
                    cached = data.get("cache", {})
                    self._cache = {
                        post_id: result
                        for post_id, result in cached.items()
                        if self._is_cache_fresh(result)
                    }
                    logger.info(f"[Similarity] Loaded {len(self._cache)} cached related-post results")
                else:
                    logger.info("[Similarity] Cache invalidated (model version changed)")
        except Exception as e:
            logger.warning(f"[Similarity] Cache load error: {e}")

    def _save_cache(self):
        temp_path = None
        try:
            cache_dir = os.path.dirname(RELATED_CACHE_PATH)
            os.makedirs(cache_dir, exist_ok=True)
            file_descriptor, temp_path = tempfile.mkstemp(
                prefix=".related-cache-",
                suffix=".json",
                dir=cache_dir,
            )
            with os.fdopen(file_descriptor, "w", encoding="utf-8") as f:
                json.dump({
                    "model_version": EMBEDDING_MODEL_VERSION,
                    "cache": self._cache,
                    "saved_at": datetime.utcnow().isoformat(),
                }, f)
                f.flush()
                os.fsync(f.fileno())
            os.replace(temp_path, RELATED_CACHE_PATH)
            temp_path = None
        except Exception as e:
            logger.warning(f"[Similarity] Cache save error: {e}")
        finally:
            if temp_path and os.path.exists(temp_path):
                try:
                    os.unlink(temp_path)
                except OSError:
                    pass

    @staticmethod
    def _is_cache_fresh(result: Any) -> bool:
        """Return false for malformed or expired related-idea cache entries."""
        if not isinstance(result, dict):
            return False
        try:
            computed_at = datetime.fromisoformat(str(result.get("computed_at", "")).replace("Z", "+00:00"))
            if computed_at.tzinfo is None:
                computed_at = computed_at.replace(tzinfo=timezone.utc)
            age_seconds = (datetime.now(timezone.utc) - computed_at.astimezone(timezone.utc)).total_seconds()
            return 0 <= age_seconds <= RELATED_CACHE_TTL_SECONDS
        except (TypeError, ValueError):
            return False

    def invalidate_cache(self, post_id: Optional[str] = None):
        """Invalidate cache for a specific post, or all if post_id is None."""
        with self._cache_lock:
            if post_id:
                self._cache.pop(post_id, None)
            else:
                self._cache.clear()
            self._save_cache()

    def get_related_posts(
        self,
        post_id: str,
        post_dict: Dict[str, Any],
        all_posts: Dict[str, Dict[str, Any]],
        top_n: int = RELATED_TOP_N,
        use_cache: bool = True,
    ) -> Dict[str, Any]:
        """
        Full related-post pipeline.

        Returns:
        {
          "post_id": str,
          "related_posts": [
            {
              "post_id": str,
              "similarity_score": float,
              "relationship": str,
              "title": str,
              "author_name": str,
              "topics": [...],
              "signals": {"embedding": float, "reranker": float}
            }
          ],
          "total_candidates": int,
          "pipeline": "embed+rerank+mmr" | "embed+mmr" | "fallback",
          "computed_at": str
        }
        """
        # Check cache
        if use_cache:
            with self._cache_lock:
                cached = self._cache.get(post_id)
                if cached and self._is_cache_fresh(cached):
                    return cached
                if cached:
                    self._cache.pop(post_id, None)

        result = self._compute_related(post_id, post_dict, all_posts, top_n)

        # Cache result
        with self._cache_lock:
            self._cache[post_id] = result
            self._save_cache()

        return result

    def _compute_related(
        self,
        post_id: str,
        post_dict: Dict[str, Any],
        all_posts: Dict[str, Dict[str, Any]],
        top_n: int,
    ) -> Dict[str, Any]:
        pipeline_used = "fallback"

        # Step 1: Get or generate embedding for query post
        query_vec = embedding_store.get(post_id)
        if query_vec is None:
            # Embed on the fly
            vecs = embedding_model.encode_posts([post_dict])
            query_vec = vecs[0] if len(vecs) > 0 else None

        if query_vec is None or np.all(query_vec == 0):
            logger.warning(f"[Similarity] No embedding for {post_id} — returning empty")
            return self._empty_result(post_id)

        # Step 2: Vector index search → top candidates
        candidates_raw = vector_index.search(
            query_vec,
            top_k=CANDIDATE_POOL_SIZE,
            exclude_ids=[post_id],
        )  # [(post_id, embed_score)]

        if not candidates_raw:
            logger.info(f"[Similarity] No vector candidates for {post_id} — index may not be built yet")
            return self._empty_result(post_id)

        pipeline_used = "embed+mmr"

        # Step 3: Rerank with cross-encoder
        candidate_triples = []
        for pid, embed_score in candidates_raw:
            pd = all_posts.get(pid)
            if pd:
                candidate_triples.append((pid, embed_score, pd))

        if candidate_triples:
            reranked = reranker_model.rerank(post_dict, candidate_triples, top_k=top_n * 2)
            if reranker_model.is_available:
                pipeline_used = "embed+rerank+mmr"
            # Blend: 40% embed score, 60% reranker score
            embed_scores = {pid: s for pid, s, _ in candidate_triples}
            blended = [
                (pid, 0.4 * embed_scores.get(pid, 0.0) + 0.6 * rerank_score)
                for pid, rerank_score in reranked
            ]
        else:
            # No reranker — use embedding scores
            blended = [(pid, score) for pid, score, _ in candidate_triples]

        if not blended:
            return self._empty_result(post_id)

        # Deduplicate near-identical (above threshold)
        deduped = [(pid, s) for pid, s in blended if s < NEAR_DUPLICATE_THRESHOLD or pid == blended[0][0]]

        # Step 4: MMR diversity pass
        embed_vecs = {}
        for pid, _ in blended:
            v = embedding_store.get(pid)
            if v is not None:
                embed_vecs[pid] = v

        final_ranked = _mmr_select(query_vec, deduped, embed_vecs, top_k=top_n)

        # Step 5: Build response
        embed_scores_map = {pid: s for pid, s, _ in candidate_triples}
        related = []
        for pid, final_score in final_ranked:
            pd = all_posts.get(pid, {})
            embed_s = embed_scores_map.get(pid, final_score)
            related.append({
                "post_id": pid,
                "similarity_score": round(final_score, 4),
                "relationship": _get_relationship_label(final_score),
                "title": pd.get("title", ""),
                "author_name": pd.get("author_name", ""),
                "author_handle": pd.get("author_handle"),
                "topics": pd.get("topics", []),
                "stats": pd.get("stats", {}),
                "image_url": pd.get("image_url"),
                "created_at": pd.get("created_at"),
                "signals": {
                    "embedding_score": round(embed_s, 4),
                    "final_score": round(final_score, 4),
                    "reranker_used": reranker_model.is_available,
                },
            })

        return {
            "post_id": post_id,
            "related_posts": related,
            "total_candidates": len(candidates_raw),
            "pipeline": pipeline_used,
            "computed_at": datetime.utcnow().isoformat(),
        }

    def check_duplicate(
        self,
        title: str,
        text: str,
        all_posts: Dict[str, Dict[str, Any]],
        threshold: float = NEAR_DUPLICATE_THRESHOLD,
    ) -> Dict[str, Any]:
        """
        Check if a new post is a near-duplicate of existing posts.
        Does NOT block creation — returns advisory only.
        """
        query_text = f"{title}. {text}"
        query_vec = embedding_model.encode_query(query_text)

        if np.all(query_vec == 0):
            return {"is_near_duplicate": False, "similar_posts": [], "max_score": 0.0}

        candidates = vector_index.search(query_vec, top_k=10)
        similar = []
        max_score = 0.0
        for pid, score in candidates:
            if score >= threshold:
                pd = all_posts.get(pid, {})
                similar.append({
                    "post_id": pid,
                    "similarity_score": round(score, 4),
                    "relationship": _get_relationship_label(score),
                    "title": pd.get("title", ""),
                })
                max_score = max(max_score, score)

        return {
            "is_near_duplicate": bool(similar),
            "similar_posts": similar[:5],
            "max_score": round(max_score, 4),
        }

    @staticmethod
    def _empty_result(post_id: str) -> Dict[str, Any]:
        return {
            "post_id": post_id,
            "related_posts": [],
            "total_candidates": 0,
            "pipeline": "unavailable",
            "computed_at": datetime.utcnow().isoformat(),
        }


# Singleton
similarity_service = SimilarityService()
