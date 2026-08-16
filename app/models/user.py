from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field
from datetime import datetime
import uuid


class UserBase(BaseModel):
    uid: str
    email: Optional[str] = None
    display_name: Optional[str] = "Pulse Explorer"
    photo_url: Optional[str] = None


class UserProfile(UserBase):
    # Platform-issued unique ID (separate from Firebase UID)
    user_id: str = Field(default_factory=lambda: f"usr-{uuid.uuid4().hex[:12]}")

    # Unique handle — enforced at DB layer
    username: Optional[str] = None

    # Role & Persona
    role: Optional[str] = "innovator"             # engineer, researcher, designer, founder, student, explorer
    experience_level: Optional[str] = "intermediate"  # beginner, intermediate, advanced

    # Profile Content
    bio: Optional[str] = ""
    things_i_love: List[str] = Field(default_factory=list, description="Public passion tags, e.g. ['Deep learning', 'sci-fi novels']")
    location: Optional[str] = None
    contact_email: Optional[str] = None           # Public contact email (separate from auth email)
    social_links: Dict[str, str] = Field(
        default_factory=dict,
        description="Social handles: {'twitter':'...','github':'...','website':'...','linkedin':'...'}"
    )

    # Avatar — stored on disk, served via /media/
    avatar_path: Optional[str] = None             # Absolute disk path
    avatar_url: Optional[str] = None              # Public URL e.g. /media/avatars/{uid}/avatar.jpg

    # Interests & Feed Personalization
    interests: List[str] = Field(default_factory=list)
    content_tastes: List[str] = Field(default_factory=list)  # deep_dives, breakthroughs, debates, startups
    followed_authors: List[str] = Field(default_factory=list)

    # Onboarding State
    onboarding_completed: bool = False
    terms_accepted_at: Optional[str] = None
    terms_version: Optional[str] = None

    # Public Stats (denormalized for fast profile loads)
    ideas_count: int = 0
    likes_received_count: int = 0
    followers_count: int = 0
    following_count: int = 0

    # Future AI Personalization & Recommender Meta-Vectors
    ai_profile_metadata: Dict[str, Any] = Field(
        default_factory=lambda: {
            "topic_affinities": {},
            "embedding_id": None,
            "interaction_weights": {"views": 1.0, "likes": 5.0, "comments": 10.0, "shares": 20.0},
            "last_active_persona": "innovator",
            "model_version": "v1-deterministic-hybrid"
        }
    )

    created_at: str = Field(default_factory=lambda: datetime.utcnow().isoformat())
    updated_at: str = Field(default_factory=lambda: datetime.utcnow().isoformat())


class UserOnboardingRequest(BaseModel):
    interests: List[str] = Field(..., min_length=1, description="List of topic categories selected by user")
    username: Optional[str] = Field(None, min_length=3, max_length=30, description="Unique @handle")
    role: Optional[str] = "innovator"
    experience_level: Optional[str] = "intermediate"
    content_tastes: List[str] = Field(default_factory=list, description="Content format preferences")
    bio: Optional[str] = ""
    things_i_love: List[str] = Field(default_factory=list)
    location: Optional[str] = None
    contact_email: Optional[str] = None
    social_links: Dict[str, str] = Field(default_factory=dict)
    accepted_terms: bool = Field(..., description="Explicit acceptance of the THE IDEON community agreement")
    terms_version: Optional[str] = Field(None, max_length=32)


class UserUpdateRequest(BaseModel):
    display_name: Optional[str] = None
    username: Optional[str] = Field(None, min_length=3, max_length=30)
    role: Optional[str] = None
    bio: Optional[str] = None
    interests: Optional[List[str]] = None
    content_tastes: Optional[List[str]] = None
    things_i_love: Optional[List[str]] = None
    location: Optional[str] = None
    contact_email: Optional[str] = None
    social_links: Optional[Dict[str, str]] = None
    experience_level: Optional[str] = None


class UserPublicProfile(BaseModel):
    """Reduced profile for public display (no sensitive auth data)"""
    uid: str
    user_id: str
    username: Optional[str]
    display_name: Optional[str]
    avatar_url: Optional[str]
    photo_url: Optional[str]
    role: Optional[str]
    bio: Optional[str]
    things_i_love: List[str]
    location: Optional[str]
    contact_email: Optional[str]
    social_links: Dict[str, str]
    interests: List[str]
    ideas_count: int
    followers_count: int
    following_count: int
    likes_received_count: int
    created_at: str


class UserFullInfo(BaseModel):
    """Comprehensive user info returned by GET /users/{uid}/full"""
    profile: UserPublicProfile
    recent_posts: List[Any] = Field(default_factory=list)
    is_following: bool = False
    is_friend: bool = False           # mutual follow (both follow each other)
    followers_preview: List[Any] = Field(default_factory=list)   # first 5 followers
    following_preview: List[Any] = Field(default_factory=list)   # first 5 following
