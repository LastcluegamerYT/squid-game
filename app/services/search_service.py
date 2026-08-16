"""
Fast Search Service — in-memory inverted index for sub-millisecond full-text search.

Architecture:
- Inverted index: token → set of (doc_type, doc_id) pointers
- Trigram index for fuzzy/prefix matching on short queries
- BM25-lite scoring: term frequency × inverse document frequency
- Indexes are rebuilt lazily on first query and kept warm (auto-invalidated on write)
"""

import re
import math
import time
import threading
from typing import List, Dict, Set, Tuple, Optional, Any
from dataclasses import dataclass, field
from collections import defaultdict


# ─── Stop words (ignored during indexing) ────────────────────────────────────
STOP_WORDS = {
    "a","an","the","and","or","but","in","on","at","to","for","of","with",
    "is","are","was","were","be","been","being","have","has","had","do","does",
    "did","will","would","could","should","may","might","by","from","this","that",
    "these","those","it","its","i","you","we","they","he","she","not","no"
}


def tokenize(text: str) -> List[str]:
    """Lowercase, split on non-alphanumeric, remove stop words, deduplicate."""
    if not text:
        return []
    tokens = re.findall(r"[a-z0-9]+", text.lower())
    return [t for t in tokens if len(t) >= 2 and t not in STOP_WORDS]


def trigrams(token: str) -> Set[str]:
    """Generate character trigrams for fuzzy matching."""
    padded = f"_{token}_"
    return {padded[i:i+3] for i in range(len(padded) - 2)}


def weighted_document_text(doc: "SearchDoc") -> str:
    """Build the exact token source used for both indexing and removal.

    Separating repeated fields with spaces is important: multiplying a string
    directly joins its last and first words together (``storytellingFilm``),
    creating tokens that a user never actually wrote.
    """
    return " ".join([
        *([doc.title] * 3),
        *([doc.subtitle] * 2),
        *( [" ".join(doc.tags)] * 2),
        doc.text,
    ])


def fuzzy_token_similarity(query_token: str, indexed_token: str) -> float:
    """Return a conservative similarity for a possible typo.

    A single shared trigram (for example ``dogesh`` and ``images`` sharing
    ``ges``) is not evidence that two words match.  This keeps typo tolerance
    for close spellings while preventing unrelated fields from leaking into
    search results.
    """
    if len(query_token) < 4 or len(indexed_token) < 4:
        return 0.0
    query_grams = trigrams(query_token)
    indexed_grams = trigrams(indexed_token)
    overlap = len(query_grams & indexed_grams)
    if overlap < 2:
        return 0.0
    jaccard = overlap / max(len(query_grams | indexed_grams), 1)

    # This accepts familiar one-character typos such as "neroscience" for
    # "neuroscience", but rejects a loose one-trigram coincidence.
    if jaccard < 0.38:
        return 0.0
    length_similarity = min(len(query_token), len(indexed_token)) / max(len(query_token), len(indexed_token))
    if length_similarity < 0.6:
        return 0.0
    return 0.55 + (0.35 * jaccard)


@dataclass
class SearchDoc:
    doc_type: str          # "post" | "user" | "category"
    doc_id: str
    title: str
    subtitle: str = ""
    text: str = ""
    tags: List[str] = field(default_factory=list)
    score: float = 0.0
    extra: Dict[str, Any] = field(default_factory=dict)


class InvertedIndex:
    """
    Thread-safe in-memory inverted index with BM25-lite scoring.
    Supports exact token match, prefix match, and trigram fuzzy match.
    """

    def __init__(self):
        self._lock = threading.RLock()
        # token → {doc_id: term_frequency}
        self._index: Dict[str, Dict[str, int]] = defaultdict(lambda: defaultdict(int))
        # trigram → {token}
        self._trigram_idx: Dict[str, Set[str]] = defaultdict(set)
        # doc_id → SearchDoc
        self._docs: Dict[str, SearchDoc] = {}
        # doc_id → token count (document length)
        self._doc_lengths: Dict[str, int] = {}
        self._total_docs = 0
        self._avg_doc_length = 1.0
        self._dirty = True
        self._last_built = 0.0

    def add_doc(self, doc: SearchDoc):
        """Index a document. Safe to call multiple times (overwrites)."""
        with self._lock:
            doc_key = f"{doc.doc_type}:{doc.doc_id}"

            # Remove old entry if re-indexing.
            if doc_key in self._docs:
                self._remove_doc(doc_key)

            # Tokenize all text fields.
            tokens = tokenize(weighted_document_text(doc))

            # Build term frequency map.
            tf: Dict[str, int] = defaultdict(int)
            for token in tokens:
                tf[token] += 1

            # Write to inverted index.
            for token, freq in tf.items():
                self._index[token][doc_key] = freq
                for trigram in trigrams(token):
                    self._trigram_idx[trigram].add(token)

            self._docs[doc_key] = doc
            self._doc_lengths[doc_key] = len(tokens)
            self._total_docs = len(self._docs)
            self._dirty = True

    def remove_doc(self, doc_type: str, doc_id: str):
        """Remove an indexed document without requiring a complete reindex."""
        with self._lock:
            self._remove_doc(f"{doc_type}:{doc_id}")

    def _remove_doc(self, doc_key: str):
        old_doc = self._docs.pop(doc_key, None)
        if old_doc:
            # Use the exact same fields and weights as add_doc so tags and
            # trigrams cannot remain as stale search hits after an edit/delete.
            for token in set(tokenize(weighted_document_text(old_doc))):
                postings = self._index.get(token)
                if not postings:
                    continue
                postings.pop(doc_key, None)
                if postings:
                    continue
                self._index.pop(token, None)
                for trigram in trigrams(token):
                    tokens = self._trigram_idx.get(trigram)
                    if not tokens:
                        continue
                    tokens.discard(token)
                    if not tokens:
                        self._trigram_idx.pop(trigram, None)
            self._doc_lengths.pop(doc_key, None)
            self._total_docs = len(self._docs)
            self._dirty = True

    def _update_avg_length(self):
        if self._doc_lengths:
            self._avg_doc_length = sum(self._doc_lengths.values()) / len(self._doc_lengths)
        else:
            self._avg_doc_length = 1.0
        self._dirty = False

    def search(self, query: str, doc_type: Optional[str] = None,
               limit: int = 10, fuzzy: bool = True) -> List[Tuple[SearchDoc, float]]:
        """
        Search with BM25-lite scoring.
        Returns sorted list of (doc, score) tuples.
        """
        with self._lock:
            if not query.strip():
                return []

            if self._dirty:
                self._update_avg_length()

            query_tokens = tokenize(query)
            if not query_tokens:
                query_tokens = [query.lower().strip()[:30]]

            # Collect candidate tokens (exact + prefix + conservative typo
            # matching). Each match carries a quality multiplier so a true
            # word/handle match always outranks a typo approximation.
            candidate_tokens: Dict[str, float] = {}

            def add_candidate(token: str, quality: float) -> None:
                candidate_tokens[token] = max(candidate_tokens.get(token, 0.0), quality)

            for qt in query_tokens:
                if qt in self._index:
                    add_candidate(qt, 1.0)
                if len(qt) >= 3:
                    for idx_token in self._index:
                        if idx_token.startswith(qt):
                            add_candidate(idx_token, 0.92 if idx_token != qt else 1.0)
                            if len(candidate_tokens) > 200:
                                break
                if fuzzy and len(qt) >= 3:
                    for trigram in trigrams(qt):
                        for token in self._trigram_idx.get(trigram, set()):
                            similarity = fuzzy_token_similarity(qt, token)
                            if similarity:
                                add_candidate(token, similarity)

            if not candidate_tokens:
                return []

            # BM25-lite scoring.
            k1 = 1.5
            b = 0.75
            document_count = max(self._total_docs, 1)
            scores: Dict[str, float] = defaultdict(float)
            for token, match_quality in candidate_tokens.items():
                postings = self._index.get(token, {})
                document_frequency = len(postings)
                if document_frequency == 0:
                    continue
                idf = math.log((document_count - document_frequency + 0.5) / (document_frequency + 0.5) + 1)
                for doc_key, term_frequency in postings.items():
                    if doc_type and not doc_key.startswith(f"{doc_type}:"):
                        continue
                    document_length = self._doc_lengths.get(doc_key, 1)
                    normalized_tf = (term_frequency * (k1 + 1)) / (
                        term_frequency + k1 * (1 - b + b * document_length / self._avg_doc_length)
                    )
                    scores[doc_key] += idf * normalized_tf * match_quality

            ranked = sorted(scores.items(), key=lambda item: -item[1])[:limit]
            return [
                (self._docs[doc_key], round(score, 4))
                for doc_key, score in ranked
                if doc_key in self._docs
            ]


class SearchService:
    """High-level search facade over InvertedIndex — indexes all DB content."""

    def __init__(self):
        self._idx = InvertedIndex()
        self._lock = threading.RLock()
        self._build_lock = threading.Lock()
        self._indexed_at = 0.0
        self._built = False

    def build_index(self, db_instance) -> int:
        """Build (or rebuild) the full index from the DB. Returns doc count."""
        idx = InvertedIndex()

        # Take a short, consistent snapshot under the DB lock, then do the
        # tokenization work outside it so writes and WebSocket delivery stay
        # responsive while a large index is rebuilt.
        with db_instance._lock:
            posts = [dict(post) for post in db_instance.posts.values()]
            users = [(uid, dict(user)) for uid, user in db_instance.users.items()]
            categories = [dict(category) for category in db_instance.categories.values()]

        # Index posts
        for post in posts:
            idx.add_doc(SearchDoc(
                doc_type="post",
                doc_id=post["id"],
                title=post.get("title", ""),
                subtitle=post.get("author_name", ""),
                text=post.get("text", "") + " " + post.get("summary", ""),
                tags=post.get("topics", []),
                extra={
                    "author_id": post.get("author_id"),
                    "author_name": post.get("author_name"),
                    "author_handle": post.get("author_handle"),
                    "author_photo": post.get("author_photo"),
                    "topics": post.get("topics", []),
                    "stats": post.get("stats", {}),
                    "image_url": post.get("image_url"),
                    "created_at": post.get("created_at"),
                }
            ))

        # Index users
        for uid, user in users:
            idx.add_doc(SearchDoc(
                doc_type="user",
                doc_id=uid,
                title=user.get("display_name") or "",
                subtitle=(user.get("username") or "") + " " + (user.get("bio") or ""),
                text=" ".join(user.get("interests") or []) + " " + (user.get("role") or ""),
                tags=user.get("interests") or [],
                extra={
                    "uid": uid,
                    "user_id": user.get("user_id", ""),
                    "username": user.get("username"),
                    "display_name": user.get("display_name"),
                    "avatar_url": user.get("avatar_url") or user.get("photo_url"),
                    "photo_url": user.get("photo_url"),
                    "role": user.get("role"),
                    "bio": user.get("bio"),
                    "ideas_count": user.get("ideas_count", 0),
                    "followers_count": user.get("followers_count", 0),
                    "following_count": user.get("following_count", 0),
                    "is_following": False,
                    "is_friend": False,
                }
            ))

        # Index categories
        for cat in categories:
            idx.add_doc(SearchDoc(
                doc_type="category",
                doc_id=cat["id"],
                title=cat.get("name", ""),
                subtitle=cat.get("description", ""),
                text=cat.get("description", ""),
                tags=[cat["id"]],
                extra={
                    "icon": cat.get("icon"),
                    "color": cat.get("color"),
                    "posts_count": cat.get("posts_count", 0),
                    "followers_count": cat.get("followers_count", 0),
                }
            ))

        with self._lock:
            self._idx = idx
            self._indexed_at = time.time()
            self._built = True
        return len(posts) + len(users) + len(categories)

    def _ensure_index(self):
        with self._lock:
            if self._built:
                return
        # Prevent a first burst of requests from launching duplicate full
        # rebuilds. The expensive work happens only once per invalidation.
        with self._build_lock:
            with self._lock:
                if self._built:
                    return
            from app.database.db import db
            self.build_index(db)

    @staticmethod
    def _semantic_post_documents(query: str, limit: int) -> List[Tuple[SearchDoc, float]]:
        """Return semantic post matches only when the existing AI index is warm.

        This deliberately never triggers a first model download from a search
        request. Keyword results remain instant, while embedding recall becomes
        available automatically after the background pipeline has initialized.
        """
        try:
            import numpy as np
            from app.ai.embedding_model import embedding_model
            from app.ai.vector_index import vector_index
            from app.database.db import db

            if not embedding_model.is_available or vector_index.size == 0:
                return []
            query_vector = embedding_model.encode_query(query)
            if not np.any(query_vector):
                return []
            matches = vector_index.search(query_vector, top_k=limit)
            if not matches:
                return []
            with db._lock:
                posts = {post_id: dict(db.posts[post_id]) for post_id, _ in matches if post_id in db.posts}

            results: List[Tuple[SearchDoc, float]] = []
            for post_id, similarity in matches:
                post = posts.get(post_id)
                if not post or post.get("deleted"):
                    continue
                results.append((SearchDoc(
                    doc_type="post",
                    doc_id=post_id,
                    title=post.get("title", ""),
                    subtitle=post.get("author_name", ""),
                    text=post.get("text", "") + " " + post.get("summary", ""),
                    tags=post.get("topics", []),
                    extra={
                        "author_id": post.get("author_id"),
                        "author_name": post.get("author_name"),
                        "author_handle": post.get("author_handle"),
                        "author_photo": post.get("author_photo"),
                        "topics": post.get("topics", []),
                        "stats": post.get("stats", {}),
                        "image_url": post.get("image_url"),
                        "created_at": post.get("created_at"),
                    },
                ), round(float(similarity), 4)))
            return results
        except Exception:
            # Search is a core route; the semantic enhancement must be optional.
            return []

    def invalidate(self):
        """Mark index as dirty — will rebuild on next search."""
        with self._lock:
            self._built = False

    def search_all(self, query: str, limit: int = 20, fuzzy: bool = True) -> Dict[str, List[Dict]]:
        """
        Search across posts, users, and categories.
        Returns {"posts": [...], "users": [...], "categories": [...]}
        """
        self._ensure_index()
        with self._lock:
            index = self._idx
        results = index.search(query, limit=limit, fuzzy=fuzzy)

        # Semantic nearest-neighbour search is a useful fallback when there is
        # no text match at all. It must not add unrelated ideas beside a clear
        # handle/name/category result (e.g. searching "dogesh" should not show
        # a random AI-image post just because it is the closest embedding).
        if not results:
            results = self._semantic_post_documents(query, limit=limit)

        # A document can arrive via more than one route. Keep one visible
        # instance, preserving the lexical result's ordering and score.
        deduplicated: List[Tuple[SearchDoc, float]] = []
        seen = set()
        for doc, score in results:
            key = (doc.doc_type, doc.doc_id)
            if key in seen:
                continue
            seen.add(key)
            deduplicated.append((doc, score))

        posts, users, categories = [], [], []
        for doc, score in deduplicated:
            item = {
                "id": doc.doc_id,
                "title": doc.title,
                "subtitle": doc.subtitle,
                "score": score,
                **doc.extra
            }
            if doc.doc_type == "post":
                posts.append(item)
            elif doc.doc_type == "user":
                users.append(item)
            elif doc.doc_type == "category":
                categories.append(item)

        visible_posts = posts[:8]
        visible_users = users[:5]
        visible_categories = categories[:5]
        return {
            "posts": visible_posts,
            "users": visible_users,
            "categories": visible_categories,
            # `total` now exactly represents the items returned to the client,
            # rather than a larger, hidden pre-slice count.
            "total": len(visible_posts) + len(visible_users) + len(visible_categories),
            "query": query,
        }

    def search_posts_only(self, query: str, limit: int = 20) -> List[Dict]:
        """Fast post-only search for feed filtering."""
        self._ensure_index()
        with self._lock:
            index = self._idx
        results = index.search(query, doc_type="post", limit=limit)
        lexical_ids = {doc.doc_id for doc, _ in results}
        results.extend(
            (doc, score)
            for doc, score in self._semantic_post_documents(query, limit=limit)
            if doc.doc_id not in lexical_ids
        )
        return [{"id": d.doc_id, "score": s, **d.extra} for d, s in results[:limit]]

    def index_new_post(self, post: Dict[str, Any]):
        """Incrementally index a single new post (no full rebuild)."""
        document = SearchDoc(
            doc_type="post",
            doc_id=post["id"],
            title=post.get("title", ""),
            subtitle=post.get("author_name", ""),
            text=post.get("text", "") + " " + post.get("summary", ""),
            tags=post.get("topics", []),
            extra={
                "author_id": post.get("author_id"),
                "author_name": post.get("author_name"),
                "author_handle": post.get("author_handle"),
                "author_photo": post.get("author_photo"),
                "topics": post.get("topics", []),
                "stats": post.get("stats", {}),
                "image_url": post.get("image_url"),
                "created_at": post.get("created_at"),
            },
        )
        with self._lock:
            self._idx.add_doc(document)

    def index_new_user(self, uid: str, user: Dict[str, Any]):
        """Incrementally index or update a user record."""
        document = SearchDoc(
            doc_type="user",
            doc_id=uid,
            title=user.get("display_name") or "",
            subtitle=(user.get("username") or "") + " " + (user.get("bio") or ""),
            text=" ".join(user.get("interests") or []) + " " + (user.get("role") or ""),
            tags=user.get("interests") or [],
            extra={
                "uid": uid,
                "user_id": user.get("user_id", ""),
                "username": user.get("username"),
                "display_name": user.get("display_name"),
                "avatar_url": user.get("avatar_url") or user.get("photo_url"),
                "photo_url": user.get("photo_url"),
                "role": user.get("role"),
                "bio": user.get("bio"),
                "ideas_count": user.get("ideas_count", 0),
                "followers_count": user.get("followers_count", 0),
                "following_count": user.get("following_count", 0),
                "is_following": False,
                "is_friend": False,
            },
        )
        with self._lock:
            self._idx.add_doc(document)

    def remove_post(self, post_id: str):
        """Remove a deleted idea immediately so it cannot linger in results."""
        with self._lock:
            self._idx.remove_doc("post", post_id)

    def remove_user(self, uid: str):
        """Remove a deleted user immediately so search stays consistent."""
        with self._lock:
            self._idx.remove_doc("user", uid)


# Singleton
search_service = SearchService()
