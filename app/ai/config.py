"""
AI Layer — Central Configuration
All model names, weights, and thresholds in one place.
Change models or tune weights here without touching business logic.
"""
import os

# ─── Force PyTorch backend BEFORE any transformers/sentence-transformers import ──
# Prevents "Keras 3 not supported in Transformers" error when TensorFlow is present.
os.environ.setdefault("USE_TF", "0")
os.environ.setdefault("USE_TORCH", "1")
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")

# ─── Model Identifiers ────────────────────────────────────────────────────────
# Change these to swap models. Old embeddings will be detected as stale and
# regenerated automatically (check embedding_model_version field).
EMBEDDING_MODEL = os.getenv("PULSE_EMBEDDING_MODEL", "intfloat/multilingual-e5-small")
RERANKER_MODEL  = os.getenv("PULSE_RERANKER_MODEL",  "BAAI/bge-reranker-v2-m3")

# Version string — bump this whenever you change the embedding model so
# stale embeddings get detected and regenerated automatically.
# Keep the legacy defaults so existing persisted embeddings remain usable.  When
# deploying a different model, set both PULSE_EMBEDDING_MODEL and the matching
# PULSE_EMBEDDING_MODEL_VERSION (and dimension) in the host environment.  That
# makes stale vectors re-embed safely instead of silently mixing model spaces.
EMBEDDING_MODEL_VERSION = os.getenv("PULSE_EMBEDDING_MODEL_VERSION", "me5-small-v1")
RERANKER_MODEL_VERSION  = os.getenv("PULSE_RERANKER_MODEL_VERSION", "bge-reranker-v2m3-v1")

# ─── Storage Paths ────────────────────────────────────────────────────────────
AI_STORE_DIR = os.path.join(os.path.dirname(__file__), "store")
EMBEDDINGS_PATH       = os.path.join(AI_STORE_DIR, "embeddings.npy")
EMBEDDING_META_PATH   = os.path.join(AI_STORE_DIR, "meta.json")
USER_PROFILES_PATH    = os.path.join(AI_STORE_DIR, "user_profiles.json")
FEEDBACK_PAIRS_PATH   = os.path.join(AI_STORE_DIR, "feedback_pairs.jsonl")
CLUSTERS_PATH         = os.path.join(AI_STORE_DIR, "clusters.json")
RELATED_CACHE_PATH    = os.path.join(AI_STORE_DIR, "related_cache.json")

# ─── Embedding Inference ──────────────────────────────────────────────────────
EMBEDDING_BATCH_SIZE  = int(os.getenv("PULSE_EMBEDDING_BATCH_SIZE", "32"))
EMBEDDING_MAX_TOKENS  = int(os.getenv("PULSE_EMBEDDING_MAX_TOKENS", "512"))
EMBEDDING_DIM         = int(os.getenv("PULSE_EMBEDDING_DIM", "384"))
EMBEDDING_DEVICE      = os.getenv("PULSE_AI_DEVICE") or None
# A failed remote model load must not be retried on every request.  The retry
# window still lets a recovered host/load balancer self-heal without a restart.
MODEL_LOAD_RETRY_SECONDS = float(os.getenv("PULSE_AI_MODEL_RETRY_SECONDS", "300"))

# E5 prefix convention
E5_PASSAGE_PREFIX = "passage: "   # for stored post embeddings
E5_QUERY_PREFIX   = "query: "     # for search / user queries

# ─── Vector Index ─────────────────────────────────────────────────────────────
# Below this count → exact cosine (numpy). Above → FAISS ANN for speed.
FAISS_THRESHOLD       = 5000      # auto-switch point
CANDIDATE_POOL_SIZE   = 50        # embedding retrieval candidates before reranking
RERANKER_CANDIDATE_LIMIT = int(os.getenv("PULSE_RERANKER_CANDIDATE_LIMIT", "40"))

# ─── Similarity Scoring ───────────────────────────────────────────────────────
# Calibrated score bands — not scientifically exact, configurable.
SIMILARITY_BANDS = [
    (0.80, 1.00, "very closely related"),
    (0.60, 0.80, "strongly related"),
    (0.40, 0.60, "somewhat related"),
    (0.20, 0.40, "weakly related"),
    (0.00, 0.20, "unrelated"),
]
NEAR_DUPLICATE_THRESHOLD = 0.88   # above this → surface advisory to user
RELATED_TOP_N            = 20     # final related posts returned
RELATED_CACHE_TTL_SECONDS = int(os.getenv("PULSE_RELATED_CACHE_TTL_SECONDS", "900"))

# ─── Feed Ranking Weights ─────────────────────────────────────────────────────
# All weights [0,1]-ish, normalized in feed_ranker.py
W_SEMANTIC    = float(os.getenv("W_SEMANTIC",    "0.40"))  # semantic match to user vector
W_CATEGORY    = float(os.getenv("W_CATEGORY",    "0.20"))  # category interest overlap
W_QUALITY     = float(os.getenv("W_QUALITY",     "0.15"))  # content quality signal
W_ENGAGEMENT  = float(os.getenv("W_ENGAGEMENT",  "0.15"))  # normalized engagement score
W_RECENCY     = float(os.getenv("W_RECENCY",     "0.10"))  # time decay score

# ─── MMR (Maximal Marginal Relevance) ─────────────────────────────────────────
# 1.0 = pure relevance, 0.0 = pure diversity
MMR_LAMBDA    = float(os.getenv("MMR_LAMBDA", "0.70"))
# MMR is quadratic in the number of candidates.  Keep the diverse first page
# bounded, then retain the remaining ranked candidates for deep pagination.
FEED_MMR_CANDIDATE_LIMIT = int(os.getenv("PULSE_FEED_MMR_CANDIDATE_LIMIT", "120"))
# Semantic scoring is most valuable for the first portion of a feed. Bounding
# this pool keeps a large corpus responsive while ranked fallback candidates
# remain available for deeper pagination.
FEED_AI_CANDIDATE_LIMIT = int(os.getenv("PULSE_FEED_AI_CANDIDATE_LIMIT", "250"))

# ─── Author Repetition ────────────────────────────────────────────────────────
MAX_AUTHOR_POSTS_IN_FEED = 2      # max posts from same author in top-N

# ─── User Profile Interaction Weights ────────────────────────────────────────
# How much each signal contributes to the user interest vector
USER_SIGNAL_WEIGHTS = {
    "save":    3.0,
    "share":   2.5,
    "comment": 2.0,
    "like":    1.5,
    "fire":    1.5,
    "bulb":    1.5,
    "view":    0.5,
    "hide":   -1.0,   # negative signal
}

# ─── Cold-Start User Profile ──────────────────────────────────────────────────
# Min interactions before switching from cold-start to mature profile
MIN_INTERACTIONS_FOR_MATURE = 5

# ─── Clustering ───────────────────────────────────────────────────────────────
HDBSCAN_MIN_CLUSTER_SIZE  = 3
HDBSCAN_MIN_SAMPLES       = 2

# ─── Quality Scoring ──────────────────────────────────────────────────────────
MIN_QUALITY_TITLE_LEN  = 10
MIN_QUALITY_BODY_LEN   = 50
IDEAL_QUALITY_BODY_LEN = 300
MAX_EMOJI_RATIO        = 0.15   # >15% emoji chars → quality penalty

# ─── Background Worker ────────────────────────────────────────────────────────
WORKER_THREADS         = int(os.getenv("PULSE_AI_WORKER_THREADS", "2"))
EMBEDDING_QUEUE_MAX    = int(os.getenv("PULSE_AI_QUEUE_MAX", "500"))

# ─── Recency ─────────────────────────────────────────────────────────────────
RECENCY_HALF_LIFE_HOURS = 48.0  # post score halves every 48h for recency signal
