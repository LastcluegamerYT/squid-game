from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, status, UploadFile, File, Form
from app.models.user import UserProfile
from app.models.post import IdeaCreate, IdeaUpdate, IdeaResponse, ReactionRequest
from app.models.comment import CommentCreate, CommentResponse
from app.auth.firebase_auth import get_current_user, get_optional_user
from app.database.db import db
from app.services.event_service import event_service
from app.models.event import EventType
from app.media.storage import save_post_image
import asyncio

router = APIRouter(prefix="/posts", tags=["Ideas & Posts"])


def _queue_ai_embed(post_id: str, post_dict: dict):
    """Non-blocking: queue the new post for AI embedding in the background."""
    try:
        from app.ai.background_worker import background_worker
        background_worker.embed_new_post(post_id, post_dict)
    except Exception:
        pass   # AI layer must never break post creation


async def _broadcast_new_post(post: IdeaResponse):
    """Async broadcast after post creation."""
    try:
        await db.broadcast_ws({
            "type": "new_post",
            "post": post.model_dump()
        })
    except Exception:
        pass


def _queue_notification_delivery(notifications: List[dict]) -> None:
    """Deliver already-persisted, recipient-only notifications without blocking a request."""
    for notification in notifications:
        recipient_uid = notification.get("recipient_uid")
        if not recipient_uid:
            continue
        asyncio.create_task(db.broadcast_to_user(
            recipient_uid,
            {"type": "notification", "notification": notification},
        ))


# ─── Create Post (JSON) ───────────────────────────────────────────────────────

@router.post("", response_model=IdeaResponse, status_code=status.HTTP_201_CREATED)
async def create_idea_post(
    req: IdeaCreate,
    current_user: UserProfile = Depends(get_current_user)
):
    """Publish a new idea/pitch to Pulse (JSON body, no image)."""
    created_post = db.create_post(
        author=current_user,
        title=req.title,
        text=req.text,
        topics=req.topics,
        summary=req.summary,
        image_urls=req.image_urls or [],
    )
    event_service.record_event(
        user_id=current_user.uid,
        event_type=EventType.CLICK,
        post_id=created_post.id,
        metadata={"action": "create_post", "topics": req.topics}
    )
    # Queue AI embedding (background — does not block response)
    post_raw = db.posts.get(created_post.id, {})
    _queue_ai_embed(created_post.id, post_raw)
    _queue_notification_delivery(db.create_post_mention_notifications(created_post.id))
    # Broadcast to WebSocket clients (fire-and-forget)
    asyncio.ensure_future(_broadcast_new_post(created_post))
    return created_post


# ─── Create Post with Image (Multipart) ──────────────────────────────────────

@router.post("/with-image", response_model=IdeaResponse, status_code=status.HTTP_201_CREATED)
async def create_idea_post_with_image(
    title: str = Form(..., min_length=3, max_length=200),
    text: str = Form(..., min_length=10, max_length=10000),
    topics: str = Form(..., description="Comma-separated topic tags e.g. 'ai,robotics'"),
    summary: Optional[str] = Form(None, max_length=400),
    image: Optional[UploadFile] = File(None, description="Optional post image — JPEG/PNG/GIF/WebP, max 10 MB"),
    current_user: UserProfile = Depends(get_current_user)
):
    """
    Publish a new idea with an optional attached image.
    Uses multipart/form-data.
    """
    topic_list = [t.strip().lower() for t in topics.split(",") if t.strip()]
    if not topic_list:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="At least one topic required")

    image_url = None
    image_path = None

    if image and image.filename:
        file_bytes = await image.read()
        # Generate a temp post_id for the image path; we'll update after creation
        import uuid
        temp_post_id = f"idea-{uuid.uuid4().hex[:8]}"
        try:
            image_path, image_url = save_post_image(
                post_id=temp_post_id,
                file_bytes=file_bytes,
                content_type=image.content_type,
                filename=image.filename
            )
        except ValueError as e:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))

        created_post = db.create_post(
            author=current_user,
            title=title,
            text=text,
            topics=topic_list,
            summary=summary,
            image_url=image_url,
            image_path=image_path,
        )
    else:
        created_post = db.create_post(
            author=current_user,
            title=title,
            text=text,
            topics=topic_list,
            summary=summary,
        )

    event_service.record_event(
        user_id=current_user.uid,
        event_type=EventType.CLICK,
        post_id=created_post.id,
        metadata={"action": "create_post_with_image", "topics": topic_list, "has_image": image_url is not None}
    )
    # Queue AI embedding (background — does not block response)
    post_raw = db.posts.get(created_post.id, {})
    _queue_ai_embed(created_post.id, post_raw)
    _queue_notification_delivery(db.create_post_mention_notifications(created_post.id))
    asyncio.ensure_future(_broadcast_new_post(created_post))
    return created_post


# ─── Get Single Post ──────────────────────────────────────────────────────────

@router.get("/{post_id}", response_model=IdeaResponse)
async def get_idea_details(
    post_id: str,
    optional_user: Optional[UserProfile] = Depends(get_optional_user)
):
    """Retrieve full details of an idea post."""
    user_id = optional_user.uid if optional_user else None
    post = db.get_post(post_id, user_id)
    if not post:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Idea post not found")

    if user_id:
        db.increment_view(post_id)
        event_service.record_event(user_id=user_id, event_type=EventType.VIEW, post_id=post_id)
    return post


# ─── Reactions ────────────────────────────────────────────────────────────────

@router.post("/{post_id}/reactions")
async def toggle_post_reaction(
    post_id: str,
    req: ReactionRequest,
    current_user: UserProfile = Depends(get_current_user)
):
    """Toggle a reaction (❤️ like, 🔥 fire, 💡 bulb) on an idea post."""
    res = db.toggle_reaction(post_id, current_user.uid, req.reaction_type)
    if not res.get("success"):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=res.get("error"))

    ev_type = EventType.LIKE
    if req.reaction_type.value == "fire":
        ev_type = EventType.FIRE
    elif req.reaction_type.value == "bulb":
        ev_type = EventType.BULB

    event_service.record_event(
        user_id=current_user.uid,
        event_type=ev_type,
        post_id=post_id,
        metadata={"added": res.get("added")}
    )
    # Broadcast reaction update
    asyncio.ensure_future(db.broadcast_ws({
        "type": "reaction_update",
        "post_id": post_id,
        "reaction_type": req.reaction_type.value,
        "stats": res.get("stats", {}),
        "added": res.get("added")
    }))
    return res


# ─── Comments ─────────────────────────────────────────────────────────────────

@router.get("/{post_id}/comments", response_model=List[CommentResponse])
async def get_post_discussions(
    post_id: str,
    optional_user: Optional[UserProfile] = Depends(get_optional_user)
):
    """Retrieve structured discussion threads (Questions, Pros/Cons, General) — emoji supported 🎉."""
    user_id = optional_user.uid if optional_user else None
    return db.get_comments_for_post(post_id, user_id)


@router.post("/{post_id}/comments", response_model=CommentResponse, status_code=status.HTTP_201_CREATED)
async def add_post_comment(
    post_id: str,
    req: CommentCreate,
    current_user: UserProfile = Depends(get_current_user)
):
    """Add a structured comment or reply to an idea discussion. Full emoji support ✅."""
    comment = db.add_comment(
        post_id=post_id,
        author=current_user,
        text=req.text,
        comment_type=req.comment_type,
        parent_id=req.parent_id
    )
    if not comment:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Idea post not found")

    event_service.record_event(
        user_id=current_user.uid,
        event_type=EventType.COMMENT,
        post_id=post_id,
        metadata={"comment_type": req.comment_type.value, "comment_id": comment.id}
    )
    # Broadcast comment event
    asyncio.ensure_future(db.broadcast_ws({
        "type": "new_comment",
        "post_id": post_id,
        "comment_id": comment.id,
        "author_name": current_user.display_name
    }))

    # Mention alerts are persisted first.  If the same recipient would also
    # receive a generic post/comment reply alert, exclude that one duplicate
    # while preserving generic alerts whenever a mention was rate-limited.
    mention_notifications = db.create_comment_mention_notifications(comment.id)
    mentioned_recipient_uids = {
        notification["recipient_uid"] for notification in mention_notifications
    }
    reply_notifications = db.create_comment_notifications(
        comment.id,
        exclude_recipient_uids=mentioned_recipient_uids,
    )
    _queue_notification_delivery(mention_notifications + reply_notifications)
    return comment


@router.post("/comments/{comment_id}/like")
async def toggle_comment_like(
    comment_id: str,
    current_user: UserProfile = Depends(get_current_user)
):
    """Like or unlike a discussion comment."""
    res = db.toggle_comment_like(comment_id, current_user.uid)
    if not res.get("success"):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Comment not found")
    return res


# ─── Hide / Share / Related ───────────────────────────────────────────────────

@router.post("/{post_id}/hide")
async def toggle_hide_post(
    post_id: str,
    current_user: UserProfile = Depends(get_current_user)
):
    """Hide a post from appearing in user's feed."""
    is_hidden = db.toggle_hide_post(current_user.uid, post_id)
    event_service.record_event(
        user_id=current_user.uid,
        event_type=EventType.HIDE,
        post_id=post_id,
        metadata={"is_hidden": is_hidden}
    )
    return {"success": True, "is_hidden": is_hidden}


@router.post("/{post_id}/share")
async def share_post(
    post_id: str,
    optional_user: Optional[UserProfile] = Depends(get_optional_user)
):
    """Record a share action to boost idea score."""
    new_shares_count = db.increment_share(post_id)
    user_id = optional_user.uid if optional_user else "anonymous"
    event_service.record_event(user_id=user_id, event_type=EventType.SHARE, post_id=post_id)
    return {"success": True, "shares_count": new_shares_count}


@router.get("/{post_id}/related", response_model=List[IdeaResponse])
async def get_related_ideas(
    post_id: str,
    optional_user: Optional[UserProfile] = Depends(get_optional_user)
):
    """Returns related ideas based on topic overlap."""
    user_id = optional_user.uid if optional_user else None
    target_post = db.get_post(post_id, user_id)
    if not target_post:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Idea not found")

    target_topics = set(t.lower() for t in target_post.topics)
    all_posts = db.get_all_posts()

    related: List[IdeaResponse] = []
    for p in all_posts:
        if p["id"] != post_id:
            p_topics = set(t.lower() for t in p.get("topics", []))
            if target_topics & p_topics:
                idea = db.get_post(p["id"], user_id)
                if idea:
                    related.append(idea)

    # Sort related by ranking score descending
    related.sort(key=lambda x: x.stats.ranking_score, reverse=True)
    return related[:6]


# ─── Edit Post ────────────────────────────────────────────────────────────────

@router.patch("/{post_id}", response_model=IdeaResponse)
async def edit_post(
    post_id: str,
    req: IdeaUpdate,
    current_user: UserProfile = Depends(get_current_user)
):
    """
    Edit your own post. Only provided fields are updated.
    Re-embeds the post for AI feed after changes.
    """
    updated = db.update_post(
        post_id=post_id,
        requesting_uid=current_user.uid,
        title=req.title,
        text=req.text,
        summary=req.summary,
        topics=req.topics,
        image_urls=req.image_urls,
    )
    if updated is None:
        # Could be not found or not the owner
        post_exists = db.posts.get(post_id)
        if not post_exists:
            raise HTTPException(status_code=404, detail="Post not found")
        raise HTTPException(status_code=403, detail="Not authorised — only the author can edit this post")
    # Broadcast edit event
    asyncio.ensure_future(db.broadcast_ws({
        "type": "post_updated",
        "post_id": post_id,
    }))
    return updated


# ─── Delete Post ──────────────────────────────────────────────────────────────

@router.delete("/{post_id}")
async def delete_post(
    post_id: str,
    current_user: UserProfile = Depends(get_current_user)
):
    """
    Permanently delete your post and all its comments/reactions.
    This action cannot be undone.
    """
    result = db.delete_post(post_id, current_user.uid)
    if not result["success"]:
        code = 404 if "not found" in result["error"].lower() else 403
        raise HTTPException(status_code=code, detail=result["error"])
    # Broadcast deletion
    asyncio.ensure_future(db.broadcast_ws({
        "type": "post_deleted",
        "post_id": post_id,
    }))
    return result


# ─── Delete Comment ───────────────────────────────────────────────────────────

@router.delete("/comments/{comment_id}")
async def delete_comment(
    comment_id: str,
    current_user: UserProfile = Depends(get_current_user)
):
    """
    Permanently delete a comment and all its replies (recursive).
    Allowed for the comment author OR the post owner.
    """
    result = db.delete_comment(comment_id, current_user.uid)
    if not result["success"]:
        code = 404 if "not found" in result["error"].lower() else 403
        raise HTTPException(status_code=code, detail=result["error"])
    return result
