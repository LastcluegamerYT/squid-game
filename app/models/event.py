from enum import Enum
from typing import Optional, Dict, Any
from pydantic import BaseModel, Field
from datetime import datetime

class EventType(str, Enum):
    VIEW = "view"
    CLICK = "click"
    LIKE = "like"
    FIRE = "fire"
    BULB = "bulb"
    COMMENT = "comment"
    SHARE = "share"
    HIDE = "hide"
    FOLLOW = "follow"
    SEARCH = "search"

class UserEvent(BaseModel):
    id: Optional[str] = None
    user_id: str
    event_type: EventType
    post_id: Optional[str] = None
    target_user_id: Optional[str] = None
    topic: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)
    timestamp: str = Field(default_factory=lambda: datetime.utcnow().isoformat())
