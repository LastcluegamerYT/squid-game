from typing import List, Optional
from enum import Enum
from pydantic import BaseModel, Field
from datetime import datetime

class CommentType(str, Enum):
    GENERAL = "general"
    QUESTION = "question"   # ❓ Questions & Inquiries
    PRO = "pro"             # 🟢 Pros / Upsides / Strengths
    CON = "con"             # 🔴 Cons / Risks / Challenges

class CommentCreate(BaseModel):
    text: str = Field(..., min_length=1, max_length=1500)
    comment_type: CommentType = Field(default=CommentType.GENERAL)
    parent_id: Optional[str] = None

class CommentResponse(BaseModel):
    id: str
    post_id: str
    author_id: str
    author_name: str
    author_handle: Optional[str] = None
    author_photo: Optional[str] = None
    text: str
    comment_type: CommentType
    parent_id: Optional[str] = None
    depth: int = 0              # 0 = top-level, 1 = reply, 2 = reply-to-reply ...
    likes_count: int = 0
    reply_count: int = 0        # total direct children (for lazy-load hint)
    user_liked: bool = False
    created_at: str
    replies: List["CommentResponse"] = Field(default_factory=list)

# Update forward refs for recursive replies
CommentResponse.model_rebuild()
