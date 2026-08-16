"""
Search Router — fast full-text search across posts, users, and categories.
Uses in-memory inverted index with BM25-lite scoring + trigram fuzzy matching.
"""
import time
from typing import Optional
from fastapi import APIRouter, Query, HTTPException, status, Depends
from app.models.user import UserProfile
from app.auth.firebase_auth import get_optional_user
from app.services.search_service import search_service

router = APIRouter(prefix="/search", tags=["Search"])


@router.get("")
async def search(
    q: str = Query(..., min_length=1, max_length=200, description="Search query"),
    type: Optional[str] = Query(None, description="Filter by type: 'post', 'user', 'category'"),
    limit: int = Query(20, ge=1, le=50),
    fuzzy: bool = Query(True, description="Enable trigram fuzzy matching"),
    optional_user: Optional[UserProfile] = Depends(get_optional_user)
):
    """
    **Fast full-text search** across all posts, users, and categories.

    - Uses in-memory BM25-lite inverted index — typically < 1ms response
    - Supports prefix matching (query 'rob' finds 'robotics')
    - Supports trigram fuzzy matching (query 'neroscience' finds 'neuroscience')
    - Results ranked by relevance score (TF-IDF weighted)

    Returns: `{posts: [...], users: [...], categories: [...], total: N, elapsed_ms: N}`
    """
    t0 = time.perf_counter()

    if type and type not in ("post", "user", "category"):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="type must be 'post', 'user', or 'category'"
        )

    results = search_service.search_all(q.strip(), limit=limit, fuzzy=fuzzy)

    # Filter by type if requested
    if type == "post":
        results = {"posts": results["posts"], "users": [], "categories": [], "total": len(results["posts"]), "query": q}
    elif type == "user":
        results = {"posts": [], "users": results["users"], "categories": [], "total": len(results["users"]), "query": q}
    elif type == "category":
        results = {"posts": [], "users": [], "categories": results["categories"], "total": len(results["categories"]), "query": q}

    elapsed = round((time.perf_counter() - t0) * 1000, 2)
    results["elapsed_ms"] = elapsed
    return results


@router.get("/posts")
async def search_posts(
    q: str = Query(..., min_length=1, max_length=200),
    limit: int = Query(10, ge=1, le=30),
    optional_user: Optional[UserProfile] = Depends(get_optional_user)
):
    """
    Fast post-only search endpoint optimized for feed filtering.
    Returns post stubs with scores — use GET /posts/{id} for full post.
    """
    t0 = time.perf_counter()
    posts = search_service.search_posts_only(q.strip(), limit=limit)
    elapsed = round((time.perf_counter() - t0) * 1000, 2)
    return {"posts": posts, "total": len(posts), "query": q, "elapsed_ms": elapsed}


@router.post("/reindex")
async def reindex(optional_user: Optional[UserProfile] = Depends(get_optional_user)):
    """
    Force a full index rebuild. Useful after bulk imports or data fixes.
    Typically completes in < 50ms even for 10,000 documents.
    """
    from app.database.db import db
    t0 = time.perf_counter()
    total = search_service.build_index(db)
    elapsed = round((time.perf_counter() - t0) * 1000, 2)
    return {"success": True, "indexed": total, "elapsed_ms": elapsed}
