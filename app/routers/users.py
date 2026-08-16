import re
from datetime import datetime
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, status, UploadFile, File, Query
from app.models.user import UserProfile, UserOnboardingRequest, UserUpdateRequest, UserFullInfo
from app.models.feed import CategoryInfo
from app.auth.firebase_auth import get_current_user, get_optional_user
from app.database.db import db
from app.services.event_service import event_service
from app.models.event import EventType
from app.media.storage import save_avatar

router = APIRouter(prefix="/users", tags=["Users & Profiles"])

USERNAME_RE = re.compile(r"^[a-z0-9_]{3,30}$")


# ─── Categories ─────────────────────────────────────────────────────────────

@router.get("/categories", response_model=List[CategoryInfo])
async def get_topic_categories():
    """Retrieve all available topic categories sorted by popularity."""
    return db.get_categories()


# ─── Username Availability ───────────────────────────────────────────────────

@router.get("/check-username")
async def check_username_availability(username: str = Query(..., min_length=1, max_length=100)):
    """
    Check if a username is available.
    Returns {available: bool, valid: bool, message: str}
    Works for any input — always returns 200, never 422.
    """
    clean = username.lower().strip()
    # Length check (30 max)
    if len(clean) > 30:
        return {
            "available": False,
            "valid": False,
            "message": f"Username must be 30 characters or fewer (you entered {len(clean)})."
        }
    if len(clean) < 3:
        return {
            "available": False,
            "valid": False,
            "message": "Username must be at least 3 characters."
        }
    valid = bool(USERNAME_RE.match(clean))
    if not valid:
        return {
            "available": False,
            "valid": False,
            "message": "Usernames can only contain lowercase letters, numbers, and underscores."
        }
    available = db.is_username_available(clean)
    return {
        "available": available,
        "valid": True,
        "message": f"@{clean} is {'available! ✅' if available else 'already taken.'}"
    }


# ─── Authenticated User ──────────────────────────────────────────────────────

@router.get("/me", response_model=UserProfile)
async def get_my_profile(current_user: UserProfile = Depends(get_current_user)):
    """Get the full authenticated user's own profile (includes private fields)."""
    return current_user


@router.put("/me", response_model=UserProfile)
async def update_my_profile(
    req: UserUpdateRequest,
    current_user: UserProfile = Depends(get_current_user)
):
    """Update profile: display name, username, role, bio, interests, social links, things_i_love, location, contact email."""
    if req.display_name is not None:
        current_user.display_name = req.display_name.strip()
    if req.role is not None:
        current_user.role = req.role
    if req.experience_level is not None:
        current_user.experience_level = req.experience_level
    if req.bio is not None:
        current_user.bio = req.bio
    if req.things_i_love is not None:
        current_user.things_i_love = [t.strip() for t in req.things_i_love if t.strip()]
    if req.location is not None:
        current_user.location = req.location.strip() if req.location else None
    if req.contact_email is not None:
        current_user.contact_email = req.contact_email.strip() if req.contact_email else None
    if req.social_links is not None:
        current_user.social_links = {k.strip(): v.strip() for k, v in req.social_links.items() if v.strip()}
    if req.interests is not None:
        current_user.interests = [i.lower().strip() for i in req.interests]
        # Update AI affinities
        affinities = current_user.ai_profile_metadata.get("topic_affinities", {})
        for t in current_user.interests:
            affinities[t] = affinities.get(t, 1.0)
        current_user.ai_profile_metadata["topic_affinities"] = affinities
    if req.content_tastes is not None:
        current_user.content_tastes = [t.lower().strip() for t in req.content_tastes]

    # Handle username separately (uniqueness enforcement)
    new_username = req.username
    try:
        return db.save_user(current_user, new_username=new_username)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))


@router.delete("/me")
async def delete_my_account(
    current_user: UserProfile = Depends(get_current_user)
):
    """
    **Permanently delete your account and all associated data.**

    This removes:
    - All your posts (and their comments/reactions)
    - Your username reservation
    - Your follows/followers
    - Your conversations and messages
    - Your account record

    ⚠️ This action is **irreversible**. The Firebase user is NOT deleted here
    (call Firebase Auth `deleteUser()` on the frontend after this succeeds).
    """
    result = db.delete_user(current_user.uid)
    if not result.get("success"):
        raise HTTPException(status_code=404, detail=result.get("error", "User not found"))
    return result


# ─── Avatar Upload ───────────────────────────────────────────────────────────

@router.post("/avatar", response_model=UserProfile)
async def upload_avatar(
    file: UploadFile = File(..., description="Profile picture — JPEG, PNG, GIF or WebP, max 5 MB"),
    current_user: UserProfile = Depends(get_current_user)
):
    """
    Upload or replace your profile avatar.
    Image is stored on the server at uploads/avatars/{uid}/ and served via /media/.
    """
    file_bytes = await file.read()
    try:
        disk_path, served_url = save_avatar(
            uid=current_user.uid,
            file_bytes=file_bytes,
            content_type=file.content_type,
            filename=file.filename
        )
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))

    current_user.avatar_path = disk_path
    current_user.avatar_url = served_url

    saved = db.save_user(current_user)
    event_service.record_event(
        user_id=current_user.uid,
        event_type=EventType.CLICK,
        metadata={"action": "avatar_upload", "url": served_url}
    )
    return saved


# ─── Onboarding ──────────────────────────────────────────────────────────────

@router.post("/onboarding", response_model=UserProfile)
async def complete_onboarding(
    req: UserOnboardingRequest,
    current_user: UserProfile = Depends(get_current_user)
):
    """
    Completes first-time onboarding:
    - Captures selected interest categories, role, content tastes
    - Optionally sets unique username, bio, things_i_love, social links, contact email
    - Seeds AI personalization topic affinity weights
    """
    if not req.accepted_terms:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="You must agree to the THE IDEON community agreement before onboarding.",
        )

    clean_interests = [i.lower().strip() for i in req.interests]
    current_user.interests = clean_interests

    if req.role:
        current_user.role = req.role.strip()
    if req.experience_level:
        current_user.experience_level = req.experience_level
    if req.content_tastes:
        current_user.content_tastes = [t.lower().strip() for t in req.content_tastes]
    if req.bio:
        current_user.bio = req.bio
    if req.things_i_love:
        current_user.things_i_love = [t.strip() for t in req.things_i_love if t.strip()]
    if req.location:
        current_user.location = req.location.strip()
    if req.contact_email:
        current_user.contact_email = req.contact_email.strip()
    if req.social_links:
        current_user.social_links = {k: v for k, v in req.social_links.items() if v.strip()}

    # Seed AI personalization affinity vectors
    current_user.ai_profile_metadata = {
        "topic_affinities": {t: 1.0 for t in clean_interests},
        "embedding_id": f"emb-{current_user.uid}",
        "role_affinity": current_user.role,
        "content_tastes": current_user.content_tastes,
        "model_version": "v1-deterministic-hybrid",
        "interaction_weights": {"views": 1.0, "likes": 5.0, "comments": 10.0, "shares": 20.0}
    }
    current_user.onboarding_completed = True
    current_user.terms_accepted_at = datetime.utcnow().isoformat()
    current_user.terms_version = req.terms_version or "2026-08-16"

    try:
        saved_user = db.save_user(current_user, new_username=req.username)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))

    event_service.record_event(
        user_id=current_user.uid,
        event_type=EventType.CLICK,
        metadata={
            "action": "onboarding_completed",
            "role": current_user.role,
            "interests": clean_interests,
            "tastes": current_user.content_tastes
        }
    )
    return saved_user


# ─── Public Profiles ─────────────────────────────────────────────────────────

@router.get("/profile/{uid_or_username}", response_model=UserProfile)
async def get_user_profile(uid_or_username: str):
    """Get basic profile by Firebase UID or @username."""
    # Try UID first
    user = db.get_user(uid_or_username)
    if not user:
        # Try username (strip leading @)
        uname = uid_or_username.lstrip("@")
        user = db.get_user_by_username(uname)
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    return user


@router.get("/{uid_or_username}/full", response_model=UserFullInfo)
async def get_user_full_info(
    uid_or_username: str,
    optional_user: Optional[UserProfile] = Depends(get_optional_user)
):
    """
    Comprehensive user info: public profile + recent posts + follower/following counts.
    Accepts Firebase UID or @username.
    """
    # Resolve uid
    user = db.get_user(uid_or_username)
    if not user:
        uname = uid_or_username.lstrip("@")
        user = db.get_user_by_username(uname)
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    requesting_uid = optional_user.uid if optional_user else None
    full_info = db.get_user_full_info(user.uid, requesting_uid)
    if not full_info:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    return full_info


# ─── Follow / Unfollow ───────────────────────────────────────────────────────

@router.post("/{target_uid}/follow")
async def follow_user(
    target_uid: str,
    current_user: UserProfile = Depends(get_current_user)
):
    """
    Follow a user. If they already follow you back, you become friends (mutual follow).
    Also fans-in their recent posts into your timeline feed.
    """
    if current_user.uid == target_uid:
        raise HTTPException(status_code=400, detail="You cannot follow yourself")

    # Resolve target (uid or @username)
    target = db.get_user(target_uid) or db.get_user_by_username(target_uid.lstrip("@"))
    if not target:
        raise HTTPException(status_code=404, detail="User not found")

    # If already following, return current state without toggling
    if target.uid in db.follows.get(current_user.uid, set()):
        is_friend = db.is_friend(current_user.uid, target.uid)
        return {"success": True, "is_following": True, "is_friend": is_friend,
                "target_uid": target.uid, "message": "Already following"}

    db.toggle_follow_author(current_user.uid, target.uid)
    is_friend = db.is_friend(current_user.uid, target.uid)
    event_service.record_event(
        user_id=current_user.uid,
        event_type=EventType.FOLLOW,
        target_user_id=target.uid,
        metadata={"action": "follow", "is_friend": is_friend}
    )
    return {
        "success": True,
        "is_following": True,
        "is_friend": is_friend,
        "target_uid": target.uid,
        "message": "Now friends! 🤝" if is_friend else f"Now following @{target.username or target.uid}"
    }


@router.delete("/{target_uid}/follow")
async def unfollow_user(
    target_uid: str,
    current_user: UserProfile = Depends(get_current_user)
):
    """Unfollow a user. Removes their posts from your timeline."""
    if current_user.uid == target_uid:
        raise HTTPException(status_code=400, detail="Cannot unfollow yourself")

    target = db.get_user(target_uid) or db.get_user_by_username(target_uid.lstrip("@"))
    if not target:
        raise HTTPException(status_code=404, detail="User not found")

    if target.uid not in db.follows.get(current_user.uid, set()):
        return {"success": True, "is_following": False, "message": "Not following"}

    db.toggle_follow_author(current_user.uid, target.uid)
    event_service.record_event(
        user_id=current_user.uid,
        event_type=EventType.FOLLOW,
        target_user_id=target.uid,
        metadata={"action": "unfollow"}
    )
    return {"success": True, "is_following": False, "target_uid": target.uid}


# Legacy toggle endpoint kept for backward compatibility
@router.post("/follow/{target_uid}")
async def toggle_follow_user(
    target_uid: str,
    current_user: UserProfile = Depends(get_current_user)
):
    """Toggle follow (legacy). Prefer POST/DELETE /{uid}/follow."""
    if current_user.uid == target_uid:
        raise HTTPException(status_code=400, detail="You cannot follow yourself")
    target = db.get_user(target_uid) or db.get_user_by_username(target_uid.lstrip("@"))
    if not target:
        raise HTTPException(status_code=404, detail="User not found")

    is_following = db.toggle_follow_author(current_user.uid, target.uid)
    is_friend = db.is_friend(current_user.uid, target.uid)
    event_service.record_event(
        user_id=current_user.uid, event_type=EventType.FOLLOW,
        target_user_id=target.uid,
        metadata={"is_following": is_following, "is_friend": is_friend}
    )
    return {"success": True, "is_following": is_following, "is_friend": is_friend,
            "target_user_id": target.uid}


# ─── Friends / Followers / Following Lists ───────────────────────────────────

@router.get("/{uid}/friends")
async def get_friends(
    uid: str,
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    optional_user: Optional[UserProfile] = Depends(get_optional_user)
):
    """
    Get mutual-follow friends for a user.
    Friends = users that both follow each other.
    """
    target = db.get_user(uid) or db.get_user_by_username(uid.lstrip("@"))
    if not target:
        raise HTTPException(status_code=404, detail="User not found")

    friends = db.get_friends_list(target.uid, limit=limit, offset=offset)
    return {
        "uid": target.uid,
        "total": len(db.get_friends_list(target.uid, limit=9999)),
        "friends": friends,
        "offset": offset,
        "limit": limit,
    }


@router.get("/{uid}/followers")
async def get_followers(
    uid: str,
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    optional_user: Optional[UserProfile] = Depends(get_optional_user)
):
    """Get paginated list of users who follow this user."""
    target = db.get_user(uid) or db.get_user_by_username(uid.lstrip("@"))
    if not target:
        raise HTTPException(status_code=404, detail="User not found")

    req_uid = optional_user.uid if optional_user else None
    followers = db.get_followers_list(target.uid, requesting_uid=req_uid,
                                      limit=limit, offset=offset)
    total = len(db.followers.get(target.uid, set()))
    return {"uid": target.uid, "total": total, "followers": followers,
            "offset": offset, "limit": limit}


@router.get("/{uid}/following")
async def get_following(
    uid: str,
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    optional_user: Optional[UserProfile] = Depends(get_optional_user)
):
    """Get paginated list of users this user follows."""
    target = db.get_user(uid) or db.get_user_by_username(uid.lstrip("@"))
    if not target:
        raise HTTPException(status_code=404, detail="User not found")

    req_uid = optional_user.uid if optional_user else None
    following = db.get_following_list(target.uid, requesting_uid=req_uid,
                                      limit=limit, offset=offset)
    total = len(db.follows.get(target.uid, set()))
    return {"uid": target.uid, "total": total, "following": following,
            "offset": offset, "limit": limit}


@router.get("/{uid}/relationship")
async def get_relationship(
    uid: str,
    current_user: UserProfile = Depends(get_current_user)
):
    """
    Get the full relationship status between the authenticated user and another user.
    Returns is_following, is_followed_by, is_friend.
    """
    target = db.get_user(uid) or db.get_user_by_username(uid.lstrip("@"))
    if not target:
        raise HTTPException(status_code=404, detail="User not found")

    you_follow_them = target.uid in db.follows.get(current_user.uid, set())
    they_follow_you = current_user.uid in db.follows.get(target.uid, set())
    return {
        "target_uid": target.uid,
        "is_following": you_follow_them,
        "is_followed_by": they_follow_you,
        "is_friend": you_follow_them and they_follow_you,
    }
