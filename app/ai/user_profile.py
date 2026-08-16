"""
User Interest Profile — cold-start and mature user vectors.

Cold Start (< MIN_INTERACTIONS_FOR_MATURE interactions):
  - Embed the user's selected category/interest labels
  - Average them into a single interest vector

Mature (≥ MIN_INTERACTIONS_FOR_MATURE interactions):
  - Weighted sum of embeddings of interacted posts
  - Weights: save > share > comment > like/fire/bulb > view > (hide = negative)
  - Updated incrementally — no full rebuild per interaction

Profiles stored in store/user_profiles.json
"""
import os
import json
import logging
import threading
import numpy as np
from typing import Dict, List, Optional, Any
from datetime import datetime

from app.ai.config import (
    USER_PROFILES_PATH, USER_SIGNAL_WEIGHTS,
    MIN_INTERACTIONS_FOR_MATURE, EMBEDDING_DIM, EMBEDDING_MODEL_VERSION,
)
from app.ai.embedding_model import embedding_model

logger = logging.getLogger(__name__)


class UserProfileStore:
    """Stores and updates user interest vectors."""

    def __init__(self):
        self._lock = threading.RLock()
        # uid → {vector: list[float], interaction_count: int, model_version: str, updated_at: str}
        self._profiles: Dict[str, Dict[str, Any]] = {}
        self._load()

    def _load(self):
        try:
            if os.path.exists(USER_PROFILES_PATH):
                with open(USER_PROFILES_PATH, "r", encoding="utf-8") as f:
                    self._profiles = json.load(f)
                logger.info(f"[UserProfile] Loaded {len(self._profiles)} user profiles")
        except Exception as e:
            logger.warning(f"[UserProfile] Load error: {e}")
            self._profiles = {}

    def _save(self):
        try:
            os.makedirs(os.path.dirname(USER_PROFILES_PATH), exist_ok=True)
            with open(USER_PROFILES_PATH, "w", encoding="utf-8") as f:
                json.dump(self._profiles, f)
        except Exception as e:
            logger.error(f"[UserProfile] Save error: {e}")

    def build_cold_start_vector(self, interests: List[str]) -> np.ndarray:
        """
        Average the embeddings of interest/category label strings.
        Used when user has <MIN_INTERACTIONS_FOR_MATURE interactions.
        """
        if not interests:
            return np.zeros(EMBEDDING_DIM, dtype=np.float32)
        vecs = embedding_model.encode_texts(interests, is_query=True)
        if len(vecs) == 0 or np.all(vecs == 0):
            return np.zeros(EMBEDDING_DIM, dtype=np.float32)
        avg = vecs.mean(axis=0)
        norm = np.linalg.norm(avg)
        if norm > 0:
            avg = avg / norm
        return avg.astype(np.float32)

    def update_from_interaction(
        self,
        uid: str,
        post_embedding: np.ndarray,
        signal_type: str,   # "like", "save", "comment", "view", "hide", etc.
        decay: float = 0.95,
    ):
        """
        Incrementally update a user's interest vector based on an interaction.
        Uses exponential moving average with signal weight.
        """
        weight = USER_SIGNAL_WEIGHTS.get(signal_type, 0.5)
        if weight == 0.0:
            return

        with self._lock:
            profile = self._profiles.get(uid, {})
            old_vec = np.array(profile.get("vector", [0.0] * EMBEDDING_DIM), dtype=np.float32)
            count = profile.get("interaction_count", 0)

            # EMA update: new = decay * old + (1 - decay) * weight_scaled * post_vec
            weighted_post = post_embedding * abs(weight)
            if weight < 0:
                # Negative signal: move away from this content
                new_vec = decay * old_vec - (1 - decay) * weighted_post
            else:
                new_vec = decay * old_vec + (1 - decay) * weighted_post

            # Renormalize
            norm = np.linalg.norm(new_vec)
            if norm > 0:
                new_vec = new_vec / norm

            self._profiles[uid] = {
                "vector": new_vec.tolist(),
                "interaction_count": count + 1,
                "model_version": EMBEDDING_MODEL_VERSION,
                "updated_at": datetime.utcnow().isoformat(),
            }
            self._save()

    def set_cold_start(self, uid: str, interests: List[str]):
        """Initialize a user profile from their selected interests."""
        vec = self.build_cold_start_vector(interests)
        with self._lock:
            self._profiles[uid] = {
                "vector": vec.tolist(),
                "interaction_count": 0,
                "model_version": EMBEDDING_MODEL_VERSION,
                "updated_at": datetime.utcnow().isoformat(),
                "interests": interests,
            }
            self._save()

    def get_vector(self, uid: str, interests: Optional[List[str]] = None) -> np.ndarray:
        """
        Get the user interest vector.
        Falls back to cold-start from interests if no profile exists.
        """
        with self._lock:
            profile = self._profiles.get(uid)

        if profile and profile.get("model_version") == EMBEDDING_MODEL_VERSION:
            vec = np.array(profile["vector"], dtype=np.float32)
            if not np.all(vec == 0):
                return vec

        # No valid profile — build cold-start
        if interests:
            vec = self.build_cold_start_vector(interests)
            return vec

        return np.zeros(EMBEDDING_DIM, dtype=np.float32)

    def is_mature(self, uid: str) -> bool:
        """Returns True if the user has enough interactions for mature personalization."""
        with self._lock:
            count = self._profiles.get(uid, {}).get("interaction_count", 0)
        return count >= MIN_INTERACTIONS_FOR_MATURE

    def get_profile_summary(self, uid: str) -> Dict[str, Any]:
        """Admin/debug view of a user profile."""
        with self._lock:
            profile = self._profiles.get(uid, {})
        return {
            "uid": uid,
            "interaction_count": profile.get("interaction_count", 0),
            "is_mature": profile.get("interaction_count", 0) >= MIN_INTERACTIONS_FOR_MATURE,
            "model_version": profile.get("model_version"),
            "updated_at": profile.get("updated_at"),
            "interests": profile.get("interests", []),
            "has_vector": bool(profile.get("vector")),
        }

    def total_profiles(self) -> int:
        with self._lock:
            return len(self._profiles)


# Singleton
user_profile_store = UserProfileStore()
