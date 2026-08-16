"""
Message / Conversation Models
"""
from typing import List, Optional
from pydantic import BaseModel, Field
from datetime import datetime
import uuid


class MessageCreate(BaseModel):
    text: str = Field(..., min_length=1, max_length=4000)
    image_url: Optional[str] = None   # pre-uploaded image URL


class MessageResponse(BaseModel):
    id: str
    conv_id: str
    sender_uid: str
    sender_name: str
    sender_avatar: Optional[str] = None
    text: str
    image_url: Optional[str] = None
    read_by: List[str] = Field(default_factory=list)
    edited: bool = False
    deleted: bool = False
    created_at: str
    edited_at: Optional[str] = None


class MessageEdit(BaseModel):
    text: str = Field(..., min_length=1, max_length=4000)


class ConversationMeta(BaseModel):
    """Summary shown in inbox list"""
    conv_id: str
    other_uid: str
    other_display_name: str
    other_avatar: Optional[str] = None
    other_username: Optional[str] = None
    last_message_text: Optional[str] = None
    last_message_at: Optional[str] = None
    unread_count: int = 0
    is_friend: bool = False          # both follow each other


class ConversationStartRequest(BaseModel):
    target_uid: str


class UserSummary(BaseModel):
    """Compact user representation for follow/friend lists"""
    uid: str
    user_id: str
    username: Optional[str] = None
    display_name: Optional[str] = None
    avatar_url: Optional[str] = None
    bio: Optional[str] = None
    followers_count: int = 0
    following_count: int = 0
    is_following: bool = False        # does the requester follow this person?
    is_friend: bool = False           # mutual follow?
