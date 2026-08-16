"""
Content Quality Scorer — fast, deterministic content quality signals.

No ML model required — heuristic rules produce a [0,1] quality score
that's cached in the post dict as `ai_quality_score`.

Signals analyzed:
  - Title: length, capitalization, question/exclamation markers
  - Body: length, paragraph structure, vocabulary richness
  - Emoji: balance (some = expressive, too many = spam)
  - Topic specificity: generic vs specific tags
  - Structure: presence of paragraphs, lists, headers
"""
import re
import math
import logging
from typing import Dict, Any

from app.ai.config import (
    MIN_QUALITY_TITLE_LEN, MIN_QUALITY_BODY_LEN,
    IDEAL_QUALITY_BODY_LEN, MAX_EMOJI_RATIO,
)

logger = logging.getLogger(__name__)

# Emoji detection pattern
EMOJI_PATTERN = re.compile(
    "[\U00010000-\U0010ffff"
    "\U0001F600-\U0001F64F"
    "\U0001F300-\U0001F5FF"
    "\U0001F680-\U0001F6FF"
    "\U0001F1E0-\U0001F1FF"
    "]+",
    flags=re.UNICODE,
)

# Generic/low-signal topics that add little specificity
GENERIC_TOPICS = {"general", "other", "misc", "random", "ideas"}


def _emoji_ratio(text: str) -> float:
    """Fraction of characters that are emoji."""
    if not text:
        return 0.0
    emoji_chars = sum(len(m.group()) for m in EMOJI_PATTERN.finditer(text))
    return emoji_chars / max(len(text), 1)


def _vocabulary_richness(text: str) -> float:
    """Type-Token Ratio (unique words / total words), capped at 1.0."""
    words = re.findall(r"\b[a-zA-Z]{3,}\b", text.lower())
    if len(words) < 5:
        return 0.3
    return min(1.0, len(set(words)) / len(words))


def _has_structure(text: str) -> bool:
    """Check if the body has multiple paragraphs or list markers."""
    paragraphs = [p.strip() for p in text.split("\n") if p.strip()]
    if len(paragraphs) >= 3:
        return True
    if re.search(r"^[\-\*\d\.]\s", text, re.MULTILINE):
        return True
    return False


def score_post_quality(post: Dict[str, Any]) -> float:
    """
    Compute a [0, 1] quality score for a post.
    Higher = better quality content signal.
    Does NOT measure semantic interestingness — only structural quality.
    """
    title  = (post.get("title") or "").strip()
    body   = (post.get("text") or "").strip()
    topics = post.get("topics") or []

    score = 0.0
    reasons = {}

    # ── Title quality (0–0.25) ─────────────────────────────────────────────
    title_len = len(title)
    if title_len >= MIN_QUALITY_TITLE_LEN:
        t_score = min(0.25, 0.10 + 0.15 * min(1.0, title_len / 60))
    else:
        t_score = max(0.0, 0.10 * title_len / MIN_QUALITY_TITLE_LEN)
    # Bonus for title ending in "?" (question) or "!" (excitement)
    if title.endswith("?") or title.endswith("!"):
        t_score = min(0.25, t_score + 0.03)
    score += t_score
    reasons["title"] = round(t_score, 3)

    # ── Body length quality (0–0.30) ─────────────────────────────────────
    body_len = len(body)
    if body_len < MIN_QUALITY_BODY_LEN:
        b_score = 0.05 * (body_len / MIN_QUALITY_BODY_LEN)
    elif body_len <= IDEAL_QUALITY_BODY_LEN:
        b_score = 0.15 + 0.15 * ((body_len - MIN_QUALITY_BODY_LEN) / (IDEAL_QUALITY_BODY_LEN - MIN_QUALITY_BODY_LEN))
    else:
        # Diminishing returns for very long posts
        b_score = 0.30 - 0.05 * math.log10(max(1, body_len / IDEAL_QUALITY_BODY_LEN))
        b_score = max(0.20, min(0.30, b_score))
    score += b_score
    reasons["body_length"] = round(b_score, 3)

    # ── Vocabulary richness (0–0.20) ─────────────────────────────────────
    all_text = f"{title} {body}"
    richness = _vocabulary_richness(all_text)
    v_score = 0.20 * richness
    score += v_score
    reasons["vocab_richness"] = round(v_score, 3)

    # ── Emoji balance (0–0.10 or penalty) ────────────────────────────────
    er = _emoji_ratio(all_text)
    if er == 0.0:
        e_score = 0.05   # no emoji — neutral/slightly lower
    elif er <= MAX_EMOJI_RATIO:
        e_score = 0.10   # healthy emoji use
    else:
        e_score = max(0.0, 0.10 - (er - MAX_EMOJI_RATIO) * 2)  # penalty
    score += e_score
    reasons["emoji_balance"] = round(e_score, 3)

    # ── Structure (0–0.10) ────────────────────────────────────────────────
    if _has_structure(body):
        score += 0.10
        reasons["structure"] = 0.10
    else:
        reasons["structure"] = 0.0

    # ── Topic specificity (0–0.05) ────────────────────────────────────────
    specific_topics = [t for t in topics if t.lower() not in GENERIC_TOPICS]
    t_spec = min(0.05, 0.025 * len(specific_topics))
    score += t_spec
    reasons["topic_specificity"] = round(t_spec, 3)

    # Clamp to [0, 1]
    final = min(1.0, max(0.0, score))
    return round(final, 4)


def enrich_post_with_quality(post: Dict[str, Any]) -> Dict[str, Any]:
    """
    Compute quality score and write it into the post dict in-place.
    Returns the post dict for chaining.
    """
    post["ai_quality_score"] = score_post_quality(post)
    return post
