from .auth import router as auth_router
from .users import router as users_router
from .posts import router as posts_router
from .feed import router as feed_router
from .media import router as media_router
from .search import router as search_router
from .ai import router as ai_router
from .messages import router as messages_router
from .notifications import router as notifications_router
from .link_preview import router as link_preview_router

__all__ = [
    "auth_router", "users_router", "posts_router", "feed_router",
    "media_router", "search_router", "ai_router", "messages_router", "notifications_router", "link_preview_router",
]
