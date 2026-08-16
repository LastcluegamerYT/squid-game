from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from app.models.user import UserProfile
from app.models.feed import FeedResponse, FeedTab
from app.auth.firebase_auth import get_optional_user, get_current_user
from app.services.feed_service import feed_service

router = APIRouter(prefix="/feed", tags=["Pulse Feed Pipeline"])

@router.get("", response_model=FeedResponse)
async def get_pulse_feed(
    tab: FeedTab = Query(FeedTab.FOR_YOU, description="Feed tab: 'for_you', 'trending', 'latest', or 'following'"),
    topic: Optional[str] = Query(None, description="Filter by topic tag (e.g. 'ai', 'robotics')"),
    q: Optional[str] = Query(None, description="Search query string for idea titles or content"),
    offset: int = Query(0, ge=0, description="Pagination offset"),
    limit: int = Query(10, ge=1, le=50, description="Items per page"),
    refresh_seed: Optional[int] = Query(None, description="Client-generated seed for fresh serendipity on pull-to-refresh"),
    optional_user: Optional[UserProfile] = Depends(get_optional_user)
):
    """
    Returns the scored and diversified Pulse feed:
    - **For You**: 3-stage candidate generation + AI re-ranking + diversity interleaving.
      Followed users' posts appear FIRST, then interest matches, then trending/serendipity.
    - **Following**: Only posts from people you follow, newest-first (requires auth).
    - **Trending**: Strictly ranked by engagement velocity score.
    - **Latest**: Chronological reverse-timestamp ordering.

    Pass `refresh_seed` (any integer, change it on each pull-to-refresh) to get a
    freshly shuffled serendipity section on every refresh.
    """
    # Following tab requires auth
    if tab == FeedTab.FOLLOWING:
        if not optional_user:
            raise HTTPException(
                status_code=401,
                detail="Sign in to see your Following feed"
            )
        return feed_service.get_following_feed(
            user=optional_user,
            offset=offset,
            limit=limit,
        )

    return feed_service.get_feed(
        user=optional_user,
        tab=tab,
        topic=topic,
        search_query=q,
        offset=offset,
        limit=limit,
        refresh_seed=refresh_seed,
    )
