"""
Media Storage Service — handles avatar and post image uploads.
Files are stored on local disk under backend/uploads/ and served via /media/ routes.
Swap save_avatar / save_post_image implementations to use cloud buckets in production.
"""
import os
import uuid
import mimetypes
from typing import Tuple, Optional

# Base upload directory (backend/uploads/)
UPLOADS_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "uploads"))
AVATARS_DIR = os.path.join(UPLOADS_DIR, "avatars")
POSTS_DIR   = os.path.join(UPLOADS_DIR, "posts")

# Allowed MIME types
ALLOWED_IMAGE_TYPES = {
    "image/jpeg": ".jpg",
    "image/png":  ".png",
    "image/gif":  ".gif",
    "image/webp": ".webp",
}

MAX_AVATAR_BYTES = 5 * 1024 * 1024   # 5 MB
MAX_POST_IMG_BYTES = 10 * 1024 * 1024  # 10 MB


def _ensure_dir(path: str):
    os.makedirs(path, exist_ok=True)


def _detect_mime(content_type: Optional[str], filename: Optional[str]) -> Tuple[str, str]:
    """Returns (mime_type, extension). Tries content_type header first, then filename."""
    if content_type and content_type in ALLOWED_IMAGE_TYPES:
        return content_type, ALLOWED_IMAGE_TYPES[content_type]
    if filename:
        guessed, _ = mimetypes.guess_type(filename)
        if guessed and guessed in ALLOWED_IMAGE_TYPES:
            return guessed, ALLOWED_IMAGE_TYPES[guessed]
    # Default to jpg
    return "image/jpeg", ".jpg"


def save_avatar(uid: str, file_bytes: bytes, content_type: Optional[str] = None, filename: Optional[str] = None) -> Tuple[str, str]:
    """
    Save a user avatar to disk.
    Returns (disk_absolute_path, served_url).
    Raises ValueError on size or type violations.
    """
    if len(file_bytes) > MAX_AVATAR_BYTES:
        raise ValueError(f"Avatar too large: max {MAX_AVATAR_BYTES // (1024*1024)} MB")

    mime, ext = _detect_mime(content_type, filename)
    if mime not in ALLOWED_IMAGE_TYPES:
        raise ValueError(f"Unsupported image type '{mime}'. Use JPEG, PNG, GIF or WebP.")

    user_dir = os.path.join(AVATARS_DIR, uid)
    _ensure_dir(user_dir)

    # Remove any old avatar files in this user's dir
    for f in os.listdir(user_dir):
        try:
            os.remove(os.path.join(user_dir, f))
        except Exception:
            pass

    filename_out = f"avatar{ext}"
    disk_path = os.path.join(user_dir, filename_out)

    with open(disk_path, "wb") as fout:
        fout.write(file_bytes)

    served_url = f"/media/avatars/{uid}/{filename_out}"
    return disk_path, served_url


def save_post_image(post_id: str, file_bytes: bytes, content_type: Optional[str] = None, filename: Optional[str] = None) -> Tuple[str, str]:
    """
    Save a post image to disk.
    Returns (disk_absolute_path, served_url).
    """
    if len(file_bytes) > MAX_POST_IMG_BYTES:
        raise ValueError(f"Post image too large: max {MAX_POST_IMG_BYTES // (1024*1024)} MB")

    mime, ext = _detect_mime(content_type, filename)
    if mime not in ALLOWED_IMAGE_TYPES:
        raise ValueError(f"Unsupported image type '{mime}'. Use JPEG, PNG, GIF or WebP.")

    post_dir = os.path.join(POSTS_DIR, post_id)
    _ensure_dir(post_dir)

    unique_name = f"img_{uuid.uuid4().hex[:8]}{ext}"
    disk_path = os.path.join(post_dir, unique_name)

    with open(disk_path, "wb") as fout:
        fout.write(file_bytes)

    served_url = f"/media/posts/{post_id}/{unique_name}"
    return disk_path, served_url


def get_avatar_path(uid: str) -> Optional[str]:
    """Return disk path of existing avatar or None."""
    user_dir = os.path.join(AVATARS_DIR, uid)
    if not os.path.isdir(user_dir):
        return None
    for f in os.listdir(user_dir):
        full = os.path.join(user_dir, f)
        if os.path.isfile(full):
            return full
    return None


def get_post_image_path(post_id: str, filename: str) -> Optional[str]:
    """Return disk path of a post image or None."""
    path = os.path.join(POSTS_DIR, post_id, filename)
    return path if os.path.isfile(path) else None
