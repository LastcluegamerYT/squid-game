"""
AI Router — /api/ai/* endpoints.

Public:
  GET  /api/ai/related/{post_id}           → top 20 semantically related posts
  GET  /api/ai/similar-check               → near-duplicate advisory for new post
  GET  /api/ai/user-profile/{uid}          → user interest profile summary
  POST /api/ai/user-profile/feedback       → record interaction → update profile vector
  GET  /api/ai/clusters                    → all clusters with labels
  GET  /api/ai/cluster/{cluster_id}        → posts in a specific cluster

Admin (protected in production by X-AI-Admin-Token):
  POST /api/ai/admin/rebuild-embeddings    → queue full embedding rebuild
  POST /api/ai/admin/rebuild-clusters      → queue HDBSCAN rerun
  POST /api/ai/admin/rebuild-index         → rebuild vector index from stored embeddings
  GET  /api/ai/admin/status               → full AI system status snapshot
  GET  /api/ai/admin/quality-scan         → quality scores for all posts
  GET  /api/ai/admin/feedback-stats       → feedback pipeline statistics
"""
import logging
import os
import secrets
from typing import Optional, List
from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from pydantic import BaseModel

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/ai", tags=["AI & Recommendations"])


def require_ai_admin(
    x_ai_admin_token: Optional[str] = Header(default=None),
) -> None:
    """Protect expensive maintenance controls without changing public AI APIs.

    Local development remains convenient when no token is configured. In a
    production environment an operator must set AI_ADMIN_TOKEN and send it as
    X-AI-Admin-Token, preventing unauthenticated rebuild floods.
    """
    configured_token = os.getenv("AI_ADMIN_TOKEN", "")
    is_production = os.getenv("ENV", "development").lower() == "production"
    if not configured_token:
        if is_production:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="AI admin controls are not configured",
            )
        return
    if not x_ai_admin_token or not secrets.compare_digest(x_ai_admin_token, configured_token):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid AI admin token")


# ─── Request / Response Models ────────────────────────────────────────────────

class FeedbackRequest(BaseModel):
    uid: str
    post_id: str
    signal_type: str   # "like", "save", "comment", "view", "hide", "share"
    context_post_ids: Optional[List[str]] = None

class RelatedFeedbackRequest(BaseModel):
    uid: str
    source_post_id: str
    clicked_post_id: str


# ─── Related Posts ────────────────────────────────────────────────────────────

@router.get("/related/{post_id}")
async def get_related_posts(
    post_id: str,
    top_n: int = Query(default=20, ge=1, le=50),
    use_cache: bool = Query(default=True),
):
    """
    Get the top N semantically related posts for a given post.
    
    Pipeline: embed → vector search → rerank → MMR diversity → return.
    Scores are calibrated semantic-relatedness: 0.8+ = very closely related.
    """
    from app.database.db import db
    from app.ai.similarity import similarity_service

    post_dict = db.posts.get(post_id)
    if not post_dict:
        raise HTTPException(status_code=404, detail=f"Post {post_id} not found")

    result = similarity_service.get_related_posts(
        post_id=post_id,
        post_dict=post_dict,
        all_posts=db.posts,
        top_n=top_n,
        use_cache=use_cache,
    )
    return result


@router.post("/user-profile/related-feedback")
async def record_related_feedback(req: RelatedFeedbackRequest):
    """Record when a user clicks a related-post recommendation (positive training pair)."""
    from app.ai.feedback_pipeline import feedback_pipeline
    feedback_pipeline.record_related_feedback(
        uid=req.uid,
        source_post_id=req.source_post_id,
        clicked_post_id=req.clicked_post_id,
    )
    return {"status": "recorded"}


# ─── Duplicate / Similar Check ───────────────────────────────────────────────

@router.get("/similar-check")
async def similar_check(
    title: str = Query(..., min_length=3),
    text:  str = Query(..., min_length=10),
    threshold: float = Query(default=0.88, ge=0.5, le=1.0),
):
    """
    Advisory check: does a new post resemble existing ones?
    Does NOT block creation — returns similar posts for the user to review.
    """
    from app.database.db import db
    from app.ai.similarity import similarity_service

    result = similarity_service.check_duplicate(
        title=title,
        text=text,
        all_posts=db.posts,
        threshold=threshold,
    )
    return result


# ─── User Interest Profile ────────────────────────────────────────────────────

@router.get("/user-profile/{uid}")
async def get_user_profile_summary(uid: str):
    """Return AI profile summary for a user (interaction count, maturity, etc.)."""
    from app.ai.user_profile import user_profile_store
    return user_profile_store.get_profile_summary(uid)


@router.post("/user-profile/feedback")
async def record_user_feedback(req: FeedbackRequest):
    """
    Record a user→post interaction.
    Updates user interest vector and logs to feedback pipeline for future fine-tuning.
    """
    from app.database.db import db
    from app.ai.embedding_store import embedding_store
    from app.ai.user_profile import user_profile_store
    from app.ai.feedback_pipeline import feedback_pipeline

    user = db.get_user(req.uid)
    interests = user.interests if user else []

    # Update user interest vector
    post_vec = embedding_store.get(req.post_id)
    if post_vec is not None:
        user_profile_store.update_from_interaction(
            uid=req.uid,
            post_embedding=post_vec,
            signal_type=req.signal_type,
        )

    # Log to feedback pipeline
    feedback_pipeline.record_interaction(
        uid=req.uid,
        interacted_post_id=req.post_id,
        signal_type=req.signal_type,
        feed_context_post_ids=req.context_post_ids,
    )

    return {"status": "recorded", "signal": req.signal_type}


# ─── Clusters ────────────────────────────────────────────────────────────────

@router.get("/clusters")
async def get_clusters():
    """List all discovered idea clusters with labels and sizes."""
    from app.ai.clustering import clustering_service
    clusters = clustering_service.get_clusters()
    return {
        "clusters": clusters,
        "total": len(clusters),
        "status": clustering_service.status(),
    }


@router.get("/cluster/{cluster_id}")
async def get_cluster_posts(cluster_id: str, limit: int = Query(default=20, ge=1, le=100)):
    """Get posts belonging to a specific idea cluster."""
    from app.database.db import db
    from app.ai.clustering import clustering_service

    posts = clustering_service.get_cluster_posts(cluster_id, db.posts)
    if not posts:
        raise HTTPException(status_code=404, detail=f"Cluster {cluster_id} not found or empty")

    return {
        "cluster_id": cluster_id,
        "posts": posts[:limit],
        "total": len(posts),
    }


# ─── Admin Endpoints ─────────────────────────────────────────────────────────

@router.post("/admin/rebuild-embeddings")
async def admin_rebuild_embeddings(_: None = Depends(require_ai_admin)):
    """Queue a full embedding rebuild for all posts missing current model embeddings."""
    from app.database.db import db
    from app.ai.background_worker import background_worker

    background_worker.rebuild_all_embeddings(db.posts)
    return {
        "status": "queued",
        "message": f"Rebuilding embeddings for {len(db.posts)} posts in background",
    }


@router.post("/admin/rebuild-clusters")
async def admin_rebuild_clusters(_: None = Depends(require_ai_admin)):
    """Queue an HDBSCAN cluster rebuild."""
    from app.database.db import db
    from app.ai.background_worker import background_worker

    background_worker.rebuild_clusters(db.posts)
    return {"status": "queued", "message": "Cluster rebuild queued in background"}


@router.post("/admin/rebuild-index")
async def admin_rebuild_index(_: None = Depends(require_ai_admin)):
    """Rebuild the vector similarity index from stored embeddings."""
    from app.ai.background_worker import background_worker

    background_worker.rebuild_vector_index()
    return {"status": "queued", "message": "Vector index rebuild queued"}


@router.get("/admin/status")
async def admin_ai_status(_: None = Depends(require_ai_admin)):
    """Full AI system status snapshot — embedding count, model version, index size, etc."""
    from app.database.db import db
    from app.ai.embedding_store import embedding_store
    from app.ai.vector_index import vector_index
    from app.ai.embedding_model import embedding_model
    from app.ai.reranker_model import reranker_model
    from app.ai.background_worker import background_worker
    from app.ai.clustering import clustering_service
    from app.ai.user_profile import user_profile_store
    from app.ai.feedback_pipeline import feedback_pipeline

    total_posts = len(db.posts)
    embedded    = embedding_store.count
    missing     = max(0, total_posts - embedded)

    return {
        "platform": {
            "total_posts": total_posts,
            "total_users": len(db.users),
            "total_categories": len(db.categories),
        },
        "embedding_model": {
            "name": embedding_model.model_version,
            "available": embedding_model.is_available,
            "load_error": getattr(embedding_model, '_load_error', None),
        },
        "reranker_model": {
            "name": reranker_model.model_version,
            "available": reranker_model.is_available,
            "load_error": getattr(reranker_model, '_load_error', None),
        },
        "embedding_store": embedding_store.status(),
        "vector_index": vector_index.status(),
        "missing_embeddings": missing,
        "embedding_coverage_pct": round(100 * embedded / max(1, total_posts), 1),
        "clustering": clustering_service.status(),
        "user_profiles": {"total_profiles": user_profile_store.total_profiles()},
        "background_worker": background_worker.status(),
        "feedback": feedback_pipeline.stats(),
    }


@router.get("/admin/quality-scan")
async def admin_quality_scan(
    limit: int = Query(default=50, ge=1, le=500),
    _: None = Depends(require_ai_admin),
):
    """Return quality scores for all posts (sorted worst → best for review)."""
    from app.database.db import db
    from app.ai.quality_scorer import score_post_quality

    results = []
    for pid, post in db.posts.items():
        q = post.get("ai_quality_score") or score_post_quality(post)
        results.append({
            "post_id": pid,
            "title": post.get("title", "")[:80],
            "quality_score": round(q, 4),
            "topics": post.get("topics", []),
        })
    results.sort(key=lambda x: x["quality_score"])
    return {"posts": results[:limit], "total": len(results)}


@router.get("/admin/feedback-stats")
async def admin_feedback_stats(_: None = Depends(require_ai_admin)):
    """Feedback pipeline statistics and export info."""
    from app.ai.feedback_pipeline import feedback_pipeline
    return feedback_pipeline.stats()
