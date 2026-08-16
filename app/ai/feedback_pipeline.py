"""
Feedback Pipeline — captures interaction pairs for future fine-tuning.

Stores labeled (post_a, post_b, signal_type, score) tuples to
store/feedback_pairs.jsonl for use in future contrastive/fine-tuning training.

Signal mapping (implicit labels):
  view:    0.3  (mild positive)
  like:    0.6  (positive)
  fire:    0.6  (positive)
  bulb:    0.6  (positive)
  comment: 0.7  (strong positive — user engaged deeply)
  share:   0.9  (very strong positive)
  save:    0.8  (strong positive)
  hide:   -0.5  (negative — user disliked)

These pairs can later be used to fine-tune or calibrate the embedding/reranker model.
"""
import os
import json
import logging
from typing import Optional, Dict, Any, List
from datetime import datetime

from app.ai.config import FEEDBACK_PAIRS_PATH, USER_SIGNAL_WEIGHTS

logger = logging.getLogger(__name__)


class FeedbackPipeline:
    def __init__(self):
        os.makedirs(os.path.dirname(FEEDBACK_PAIRS_PATH), exist_ok=True)

    def record_interaction(
        self,
        uid: str,
        interacted_post_id: str,
        signal_type: str,
        feed_context_post_ids: Optional[List[str]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ):
        """
        Record a user→post interaction for training data collection.
        feed_context_post_ids: other posts shown in the same feed batch
        (can be used later to build implicit negative pairs).
        """
        score = USER_SIGNAL_WEIGHTS.get(signal_type, 0.0)
        if score == 0.0:
            return  # Uninteresting signal — skip

        entry = {
            "uid": uid,
            "post_id": interacted_post_id,
            "signal": signal_type,
            "score": score,
            "context_post_ids": (feed_context_post_ids or [])[:10],
            "metadata": metadata or {},
            "timestamp": datetime.utcnow().isoformat(),
        }
        try:
            with open(FEEDBACK_PAIRS_PATH, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except Exception as e:
            logger.warning(f"[Feedback] Write error: {e}")

    def record_related_feedback(
        self,
        uid: str,
        source_post_id: str,
        clicked_post_id: str,
        signal_type: str = "click",
    ):
        """
        When a user clicks a related-post recommendation, record that pair.
        These become high-quality positive training pairs.
        """
        entry = {
            "type": "related_pair",
            "uid": uid,
            "source_post_id": source_post_id,
            "clicked_post_id": clicked_post_id,
            "signal": signal_type,
            "score": 0.8,
            "timestamp": datetime.utcnow().isoformat(),
        }
        try:
            with open(FEEDBACK_PAIRS_PATH, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except Exception as e:
            logger.warning(f"[Feedback] Write error: {e}")

    def export_training_pairs(self, min_score: float = 0.6) -> List[Dict[str, Any]]:
        """
        Read all feedback and return pairs with score >= min_score.
        Ready for fine-tuning dataset construction.
        """
        pairs = []
        try:
            if not os.path.exists(FEEDBACK_PAIRS_PATH):
                return []
            with open(FEEDBACK_PAIRS_PATH, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                        if abs(entry.get("score", 0.0)) >= min_score:
                            pairs.append(entry)
                    except Exception:
                        continue
        except Exception as e:
            logger.error(f"[Feedback] Export error: {e}")
        return pairs

    def stats(self) -> Dict[str, Any]:
        """Count feedback entries."""
        total = 0
        by_signal: Dict[str, int] = {}
        try:
            if os.path.exists(FEEDBACK_PAIRS_PATH):
                with open(FEEDBACK_PAIRS_PATH, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            e = json.loads(line)
                            total += 1
                            sig = e.get("signal", "unknown")
                            by_signal[sig] = by_signal.get(sig, 0) + 1
                        except Exception:
                            continue
        except Exception:
            pass
        return {"total_pairs": total, "by_signal": by_signal}


# Singleton
feedback_pipeline = FeedbackPipeline()
