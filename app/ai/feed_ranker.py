"""
AI Feed Ranker — hybrid scoring + MMR diversity for the personalized feed.

Hybrid score formula:
  score = W_SEMANTIC   × semantic_relevance_to_user
        + W_CATEGORY   × category_interest_match
        + W_QUALITY    × content_quality
        + W_ENGAGEMENT × normalized_engagement
        + W_RECENCY    × recency_score

Post-scoring:
  - Author repetition cap (max MAX_AUTHOR_POSTS_IN_FEED per author in top-N)
  - MMR diversity pass on final ranked list

Falls back to original rule-based scores if AI is unavailable.
This class fills the `_ai_personalization_hook()` in feed_service.py.
"""
import logging
import math
from typing import List, Dict, Any, Optional, Tuple
from datetime import datetime, timezone

import numpy as np

from app.ai.config import (
    W_SEMANTIC, W_CATEGORY, W_QUALITY, W_ENGAGEMENT, W_RECENCY,
    MMR_LAMBDA, MAX_AUTHOR_POSTS_IN_FEED, RECENCY_HALF_LIFE_HOURS,
    FEED_MMR_CANDIDATE_LIMIT,
)
from app.ai.embedding_store import embedding_store
from app.ai.user_profile import user_profile_store
from app.ai.quality_scorer import score_post_quality

logger = logging.getLogger(__name__)


def _recency_score(created_at_str: Optional[str], half_life_hours: float = RECENCY_HALF_LIFE_HOURS) -> float:
    """Exponential decay based on post age. Returns [0, 1]."""
    if not created_at_str:
        return 0.5
    try:
        dt = datetime.fromisoformat(created_at_str.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        hours_old = max(0.0, (now - dt.astimezone(timezone.utc)).total_seconds() / 3600.0)
        return math.exp(-math.log(2) * hours_old / half_life_hours)
    except Exception:
        return 0.5


def _engagement_score(stats: Dict[str, Any]) -> float:
    """Normalize engagement to [0, 1]. Uses log scale to avoid viral dominance."""
    raw = (
        stats.get("likes", 0)
        + stats.get("fires", 0) * 1.5
        + stats.get("bulbs", 0) * 1.5
        + stats.get("comments", 0) * 2.0
        + stats.get("shares", 0) * 3.0
    )
    # Log scale with soft ceiling
    return min(1.0, math.log1p(raw) / math.log1p(200))


def _category_match_score(post_topics: List[str], user_interests: List[str]) -> float:
    """Fraction of user interests that overlap with post topics."""
    if not user_interests:
        return 0.5
    post_set = set(t.lower() for t in post_topics)
    user_set  = set(t.lower() for t in user_interests)
    overlap = post_set & user_set
    return min(1.0, len(overlap) / max(1, len(user_set)) * 2)


def _mmr_rerank(
    user_vec: np.ndarray,
    candidates: List[Tuple[str, float, Dict[str, Any]]],
    top_k: int,
    lam: float = MMR_LAMBDA,
) -> List[Tuple[str, float, Dict[str, Any]]]:
    """MMR over (post_id, hybrid_score, post_dict) triples."""
    if not candidates:
        return []

    # Fetch each vector once. The old implementation repeatedly acquired the
    # embedding-store lock inside the O(n²) MMR loop, which becomes expensive
    # as a user's candidate pool grows.
    vectors: Dict[str, np.ndarray] = {}
    for pid, _, _ in candidates:
        vector = embedding_store.get(pid)
        if vector is not None and np.isfinite(vector).all() and np.any(vector):
            vectors[pid] = vector

    selected: List[Tuple[str, float, Dict[str, Any]]] = []
    remaining = list(candidates)

    while remaining and len(selected) < top_k:
        best_item = None
        best_mmr  = -float("inf")

        for pid, score, pdict in remaining:
            relevance = lam * score
            # Diversity: avoid posts whose embeddings are too close to already-selected
            if selected:
                pid_vec = vectors.get(pid)
                if pid_vec is not None:
                    sims = [
                        float(np.dot(pid_vec, vectors[s_pid]))
                        for s_pid, _, _ in selected
                        if s_pid in vectors
                    ]
                    redundancy = (1 - lam) * max(sims) if sims else 0.0
                else:
                    redundancy = 0.0
            else:
                redundancy = 0.0

            mmr = relevance - redundancy
            if mmr > best_mmr:
                best_mmr  = mmr
                best_item = (pid, score, pdict)

        if best_item:
            selected.append(best_item)
            remaining = [(p, s, d) for p, s, d in remaining if p != best_item[0]]
        else:
            break

    return selected


class AIFeedRanker:
    """
    Scores a list of candidate posts for a specific user.
    Called from feed_service._ai_personalization_hook().
    """

    def rank_candidates(
        self,
        uid: Optional[str],
        user_interests: List[str],
        candidates: List[Dict[str, Any]],
        top_k: int = 50,
    ) -> List[Dict[str, Any]]:
        """
        Re-score and re-rank candidate posts for a user.

        Returns the same list re-ordered with an added `ai_feed_score` and
        `ai_signals` field per post dict.

        Falls back to original order if AI embeddings are unavailable.
        """
        if not candidates:
            return candidates

        # Get user interest vector
        user_vec: Optional[np.ndarray] = None
        if uid:
            user_vec = user_profile_store.get_vector(uid, user_interests)
            if np.all(user_vec == 0):
                user_vec = None

        scored: List[Tuple[str, float, Dict[str, Any]]] = []

        for post in candidates:
            pid = post.get("id", "")
            topics = post.get("topics") or []
            stats  = post.get("stats") or {}

            # 1. Semantic relevance to user vector
            if user_vec is not None:
                post_vec = embedding_store.get(pid)
                if post_vec is not None and not np.all(post_vec == 0):
                    semantic = float(np.dot(user_vec, post_vec))
                    semantic = max(0.0, min(1.0, semantic))
                else:
                    semantic = 0.4   # no embedding → neutral
            else:
                semantic = 0.4

            # 2. Category match
            cat_match = _category_match_score(topics, user_interests)

            # 3. Content quality
            quality = post.get("ai_quality_score") or score_post_quality(post)

            # 4. Engagement
            engagement = _engagement_score(stats)

            # 5. Recency
            recency = _recency_score(post.get("created_at"))

            # Hybrid score
            hybrid = (
                W_SEMANTIC   * semantic
                + W_CATEGORY * cat_match
                + W_QUALITY  * quality
                + W_ENGAGEMENT * engagement
                + W_RECENCY  * recency
            )

            post_copy = dict(post)
            post_copy["ai_feed_score"] = round(hybrid, 4)
            post_copy["ai_signals"] = {
                "semantic":    round(semantic, 4),
                "category":    round(cat_match, 4),
                "quality":     round(quality, 4),
                "engagement":  round(engagement, 4),
                "recency":     round(recency, 4),
                "hybrid":      round(hybrid, 4),
            }
            scored.append((pid, hybrid, post_copy))

        # Sort by hybrid score
        scored.sort(key=lambda x: -x[1])

        # Author repetition cap
        author_counts: Dict[str, int] = {}
        capped: List[Tuple[str, float, Dict[str, Any]]] = []
        overflow: List[Tuple[str, float, Dict[str, Any]]] = []
        for pid, score, pdict in scored:
            author = pdict.get("author_id", "")
            c = author_counts.get(author, 0)
            if c < MAX_AUTHOR_POSTS_IN_FEED:
                capped.append((pid, score, pdict))
                author_counts[author] = c + 1
            else:
                overflow.append((pid, score, pdict))
        # Append overflow at the end so they're still reachable
        capped.extend(overflow)

        # MMR is quadratic. Diversify only a bounded first window, then retain
        # every remaining ranked result for deep pagination.
        if user_vec is not None and len(capped) > 1:
            mmr_limit = min(len(capped), top_k, max(1, FEED_MMR_CANDIDATE_LIMIT))
            diversified = _mmr_rerank(user_vec, capped[:mmr_limit], top_k=mmr_limit)
            remaining_ids = {p for p, _, _ in diversified}
            tail = [(p, s, d) for p, s, d in capped if p not in remaining_ids]
            final = diversified + tail
        else:
            final = capped

        return [pdict for _, _, pdict in final]

    def get_recommendation_reason(self, post: Dict[str, Any], user_interests: List[str]) -> str:
        """Human-readable reason for the recommendation."""
        signals = post.get("ai_signals", {})
        semantic = signals.get("semantic", 0.0)
        cat      = signals.get("category", 0.0)

        topics = post.get("topics", [])
        matched = [t for t in topics if t.lower() in {i.lower() for i in user_interests}]

        if cat >= 0.8 and matched:
            return f"Matches your interest in #{matched[0]}"
        if semantic >= 0.7:
            return "Semantically relevant to your profile"
        if signals.get("engagement", 0) >= 0.7:
            return "Trending in your interests"
        if signals.get("recency", 0) >= 0.8:
            return "Fresh idea"
        return "Recommended for you"


# Singleton
ai_feed_ranker = AIFeedRanker()
