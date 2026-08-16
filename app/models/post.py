from typing import List, Optional
from enum import Enum
from pydantic import BaseModel, Field
from datetime import datetime


class ReactionType(str, Enum):
    LIKE = "like"    # ❤️ Like / Support
    FIRE = "fire"    # 🔥 Fire / Breakthrough Idea
    BULB = "bulb"    # 💡 Insight / Mind-expanding


class ReactionRequest(BaseModel):
    reaction_type: ReactionType


class PostStats(BaseModel):
    likes: int = 0
    fires: int = 0
    bulbs: int = 0
    comments: int = 0
    shares: int = 0
    views: int = 0
    hides: int = 0
    ranking_score: float = 0.0


class IdeaCreate(BaseModel):
    title: str = Field(..., min_length=3, max_length=200, description="Catchy, bold idea headline")
    text: str = Field(..., min_length=10, max_length=50_000, description="Full explanation or pitch — emoji supported 🚀")
    topics: List[str] = Field(..., min_length=1, max_length=8, description="Tags/Topics (e.g. AI, Robotics, Design)")
    summary: Optional[str] = Field(None, max_length=400, description="Optional brief snippet")
    image_urls: Optional[List[str]] = Field(None, max_length=4, description="Up to 4 image URLs (pass existing URLs from /media/upload)")


class IdeaUpdate(BaseModel):
    """Partial update — only provided fields are changed."""
    title:    Optional[str] = Field(None, min_length=3, max_length=200)
    text:     Optional[str] = Field(None, min_length=10, max_length=50_000)
    summary:  Optional[str] = Field(None, max_length=400)
    topics:   Optional[List[str]] = Field(None, max_length=8)
    image_urls: Optional[List[str]] = Field(None, max_length=4)


class IdeaResponse(BaseModel):
    id: str
    title: str
    text: str
    summary: str
    author_id: str
    author_name: str
    author_handle: Optional[str] = None
    author_photo: Optional[str] = None
    author_avatar_url: Optional[str] = None  # Server-hosted avatar takes priority
    topics: List[str]
    stats: PostStats
    image_url: Optional[str] = None         # Primary image (backward compat)
    image_urls: List[str] = Field(default_factory=list)  # Up to 4 images
    user_reactions: List[str] = Field(default_factory=list, description="Reactions given by requesting user")
    user_hidden: bool = False
    is_following_author: bool = False
    is_own_post: bool = False               # True when requesting user is the author
    created_at: str
    updated_at: str
