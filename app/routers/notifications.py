"""Authenticated, persisted in-app notifications."""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from app.auth.firebase_auth import get_current_user
from app.database.db import db
from app.models.notification import (
    NotificationListResponse,
    NotificationReadAllResponse,
    NotificationResponse,
    NotificationUnreadCount,
)
from app.models.user import UserProfile


router = APIRouter(prefix="/notifications", tags=["Notifications"])


@router.get("", response_model=NotificationListResponse)
async def list_notifications(
    limit: int = Query(30, ge=1, le=100),
    before_id: Optional[str] = Query(None, description="Load notifications older than this notification ID"),
    unread_only: bool = Query(False),
    current_user: UserProfile = Depends(get_current_user),
):
    """Return the current user's activity alerts, newest first."""
    page = db.get_notifications(
        current_user.uid,
        limit=limit,
        before_id=before_id,
        unread_only=unread_only,
    )
    return NotificationListResponse(**page)


@router.get("/unread-count", response_model=NotificationUnreadCount)
async def get_unread_notification_count(
    current_user: UserProfile = Depends(get_current_user),
):
    """A lightweight endpoint for notification badges."""
    return NotificationUnreadCount(unread_count=db.get_unread_notification_count(current_user.uid))


@router.post("/read-all", response_model=NotificationReadAllResponse)
async def mark_all_notifications_read(
    current_user: UserProfile = Depends(get_current_user),
):
    """Mark every currently unread notification as read."""
    marked_read = db.mark_all_notifications_read(current_user.uid)
    return NotificationReadAllResponse(marked_read=marked_read)


@router.post("/{notification_id}/read", response_model=NotificationResponse)
async def mark_notification_read(
    notification_id: str,
    current_user: UserProfile = Depends(get_current_user),
):
    """Mark one notification as read. A user can only affect their own alerts."""
    notification = db.mark_notification_read(current_user.uid, notification_id)
    if not notification:
        raise HTTPException(status_code=404, detail="Notification not found")
    return NotificationResponse(**notification)
