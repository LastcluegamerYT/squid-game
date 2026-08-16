from typing import List, Optional
from enum import Enum
from pydantic import BaseModel, Field
from .post import IdeaResponse

class FeedTab(str, Enum):
    FOR_YOU   = "for_you"
    FOLLOWING = "following"
    TRENDING  = "trending"
    LATEST    = "latest"

class CategoryInfo(BaseModel):
    id: str
    name: str
    icon: str
    description: str
    posts_count: int = 0
    followers_count: int = 0
    color: Optional[str] = "#6366f1"   # Hex accent color for UI chips


class FeedItem(BaseModel):
    idea: IdeaResponse
    recommendation_reason: Optional[str] = "Recommended for you"
    source_type: str = "interest"  # "interest", "trending", "serendipity", "following"

class FeedFilter(BaseModel):
    tab: FeedTab = FeedTab.FOR_YOU
    topic: Optional[str] = None
    q: Optional[str] = None
    offset: int = 0
    limit: int = 10

class FeedResponse(BaseModel):
    items: List[FeedItem]
    total: int
    has_more: bool
    next_offset: Optional[int] = None
    tab: FeedTab
    filter_topic: Optional[str] = None

