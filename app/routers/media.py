"""
Media Router — serves uploaded avatar and post images from local disk.
"""
import os
import uuid
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from fastapi.responses import FileResponse
from app.auth.firebase_auth import get_current_user
from app.media.storage import AVATARS_DIR, POSTS_DIR, ALLOWED_IMAGE_TYPES, save_post_image
from app.models.user import UserProfile

router = APIRouter(prefix="/media", tags=["Media"])

# Reverse MIME lookup from extension
EXT_TO_MIME = {v: k for k, v in ALLOWED_IMAGE_TYPES.items()}


def _media_response(disk_path: str) -> FileResponse:
    if not os.path.isfile(disk_path):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Media file not found")
    ext = os.path.splitext(disk_path)[1].lower()
    media_type = EXT_TO_MIME.get(ext, "image/jpeg")
    return FileResponse(disk_path, media_type=media_type)


@router.post("/upload")
async def upload_post_media(
    file: UploadFile = File(..., description="Post image — JPEG, PNG, GIF or WebP, max 10 MB"),
    current_user: UserProfile = Depends(get_current_user),
):
    """Upload one post image and return its server URL for ``POST /posts``.

    The post creation route accepts up to four already-uploaded ``image_urls``.
    Keeping this small upload endpoint separate lets the client upload a gallery
    before submitting the final idea payload, without changing the post contract.
    """
    file_bytes = await file.read()
    upload_id = f"upload-{uuid.uuid4().hex[:12]}"
    try:
        _, served_url = save_post_image(
            post_id=upload_id,
            file_bytes=file_bytes,
            content_type=file.content_type,
            filename=file.filename,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))

    return {"url": served_url}


@router.get("/avatars/{uid}/{filename}")
async def serve_avatar(uid: str, filename: str):
    """Serve a user's uploaded avatar image."""
    # Basic path traversal protection
    if ".." in uid or ".." in filename or "/" in filename or "\\" in filename:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid path")
    disk_path = os.path.join(AVATARS_DIR, uid, filename)
    return _media_response(disk_path)


@router.get("/posts/{post_id}/{filename}")
async def serve_post_image(post_id: str, filename: str):
    """Serve a post's attached image."""
    if ".." in post_id or ".." in filename or "/" in filename or "\\" in filename:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid path")
    disk_path = os.path.join(POSTS_DIR, post_id, filename)
    return _media_response(disk_path)
