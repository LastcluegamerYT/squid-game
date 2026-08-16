"""Notification models for persisted, in-app activity alerts."""

from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, Field


class NotificationType(str, Enum):
    """The small, user-facing set of activity alerts currently supported."""

    POST_COMMENT = "post_comment"
    COMMENT_REPLY = "comment_reply"
    MENTION = "mention"


class NotificationResponse(BaseModel):
    """A notification that belongs only to the authenticated recipient."""

    id: str
    type: NotificationType
    recipient_uid: str
    actor_uid: str
    actor_name: str
    actor_avatar: Optional[str] = None
    post_id: str
    comment_id: Optional[str] = None
    parent_comment_id: Optional[str] = None
    title: str
    body: str
    created_at: str
    read_at: Optional[str] = None
    is_read: bool = False


class NotificationListResponse(BaseModel):
    """A bounded notification page plus the current unread badge count."""

    notifications: List[NotificationResponse] = Field(default_factory=list)
    unread_count: int = 0
    next_before_id: Optional[str] = None


class NotificationUnreadCount(BaseModel):
    unread_count: int = 0


class NotificationReadAllResponse(BaseModel):
    success: bool = True
    marked_read: int = 0
