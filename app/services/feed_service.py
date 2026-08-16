import random
from typing import List, Optional, Dict, Any, Tuple
from datetime import datetime, timezone

from app.config import settings
from app.models.user import UserProfile
from app.models.feed import FeedItem, FeedResponse, FeedTab
from app.models.post import IdeaResponse
from app.database.db import db

class FeedService:
    def __init__(self):
        self.w_share = settings.WEIGHT_SHARE
        self.w_comment = settings.WEIGHT_COMMENT
        self.w_like = settings.WEIGHT_LIKE
        self.w_fire = settings.WEIGHT_FIRE
        self.w_bulb = settings.WEIGHT_BULB
        self.w_click = settings.WEIGHT_CLICK
        self.w_hide = settings.WEIGHT_HIDE
        self.w_block = settings.WEIGHT_BLOCK
        self.time_decay_lambda = settings.TIME_DECAY_LAMBDA
        self._mix_schedule = self._build_mix_schedule()

    def _build_mix_schedule(self, slots: int = 20) -> List[str]:
        """Create a smooth weighted cycle from the configured feed ratios."""
        weights = {
            "interest": max(0.0, settings.FEED_INTEREST_RATIO),
            "trending": max(0.0, settings.FEED_TRENDING_RATIO),
            "serendipity": max(0.0, settings.FEED_SERENDIPITY_RATIO),
        }
        active = [source for source, weight in weights.items() if weight > 0]
        if not active:
            return ["interest"]

        total_weight = sum(weights[source] for source in active)
        running = {source: 0.0 for source in active}
        schedule: List[str] = []
        for _ in range(slots):
            for source in active:
                running[source] += weights[source]
            chosen = max(active, key=lambda source: running[source])
            running[chosen] -= total_weight
            schedule.append(chosen)
        return schedule

    def calculate_score(self, post_dict: Dict[str, Any], now: datetime, user: Optional[UserProfile] = None) -> float:
        stats = post_dict.get("stats", {})
        likes = stats.get("likes", 0)
        fires = stats.get("fires", 0)
        bulbs = stats.get("bulbs", 0)
        comments = stats.get("comments", 0)
        shares = stats.get("shares", 0)
        clicks = stats.get("views", 0)
        hides = stats.get("hides", 0)

        # Parse created_at
        created_str = post_dict.get("created_at")
        hours_ago = 1.0
        if created_str:
            try:
                dt = datetime.fromisoformat(created_str.replace("Z", "+00:00"))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                hours_ago = max(0.1, (now - dt.astimezone(timezone.utc)).total_seconds() / 3600.0)
            except Exception:
                pass

        # Formula: w_share*shares + w_comment*comments + w_fire*fires + w_bulb*bulbs + w_like*likes + w_click*clicks - w_hide*hides - lambda*hours_ago
        engagement = (
            (self.w_share * shares)
            + (self.w_comment * comments)
            + (self.w_fire * fires)
            + (self.w_bulb * bulbs)
            + (self.w_like * likes)
            + (self.w_click * min(clicks, 100) * 0.1)
            - (self.w_hide * hides)
        )
        decay = self.time_decay_lambda * hours_ago
        base_score = round(engagement - decay, 4)

        # Persona & AI Affinity Boost (Programmed V1 Rule-Based Layer)
        if user:
            base_score += self._calculate_persona_affinity_boost(user, post_dict)

        return base_score

    def _calculate_persona_affinity_boost(self, user: UserProfile, post_dict: Dict[str, Any]) -> float:
        """
        Applies programmatic affinity boost based on user's role, selected interests, and content tastes.
        This provides instant personalization while preparing telemetry for future AI models.
        """
        boost = 0.0
        post_topics = set(t.lower() for t in post_dict.get("topics", []))
        user_interests = set(t.lower() for t in user.interests)
        
        # Topic overlap boost (scaled by AI affinity metadata if present)
        matched_topics = post_topics & user_interests
        if matched_topics:
            affinities = user.ai_profile_metadata.get("topic_affinities", {})
            for t in matched_topics:
                boost += 25.0 * affinities.get(t, 1.0)

        # Role-based content matching
        role = (user.role or "innovator").lower()
        if role in ["engineer", "developer", "researcher", "scientist"]:
            if "ai" in post_topics or "robotics" in post_topics or "neuroscience" in post_topics:
                boost += 15.0
        elif role in ["designer", "product"]:
            if "design" in post_topics:
                boost += 20.0
        elif role in ["founder", "entrepreneur"]:
            if "web3" in post_topics or "cleantech" in post_topics or "biotech" in post_topics:
                boost += 15.0

        # Content taste alignment (e.g. discussions/debates -> high comments boost)
        tastes = [t.lower() for t in (user.content_tastes or [])]
        stats = post_dict.get("stats", {})
        if "debates" in tastes and stats.get("comments", 0) > 5:
            boost += 15.0
        if "breakthroughs" in tastes and stats.get("fires", 0) > 10:
            boost += 20.0
        if "deep_dives" in tastes and stats.get("bulbs", 0) > 10:
            boost += 20.0

        return boost

    def _ai_personalization_hook(self, user: Optional[UserProfile], candidates: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        AI Integration: calls the semantic feed ranker.
        Re-scores candidates using user interest vector + hybrid signals.
        Falls back gracefully to original order if AI layer is unavailable.
        """
        try:
            from app.ai.config import FEED_AI_CANDIDATE_LIMIT
            from app.ai.feed_ranker import ai_feed_ranker
            uid = user.uid if user else None
            interests = user.interests if user else []
            # The first pages are the only ones users normally see. Limit the
            # expensive semantic pass there and retain the pre-ranked tail for
            # pagination instead of dropping it.
            semantic_pool = candidates[:max(1, FEED_AI_CANDIDATE_LIMIT)]
            ranked = ai_feed_ranker.rank_candidates(
                uid=uid,
                user_interests=interests,
                candidates=semantic_pool,
                top_k=len(semantic_pool),
            )
            return ranked + candidates[len(semantic_pool):]
        except Exception as e:
            # Never break the feed — always fall back silently
            import logging
            logging.getLogger(__name__).warning(f"[Feed] AI hook error (falling back): {e}")
            return candidates

    def get_feed(
        self,
        user: Optional[UserProfile] = None,
        tab: FeedTab = FeedTab.FOR_YOU,
        topic: Optional[str] = None,
        search_query: Optional[str] = None,
        offset: int = 0,
        limit: int = 10,
        refresh_seed: Optional[int] = None,   # passed from frontend on each pull-to-refresh
    ) -> FeedResponse:
        all_raw_posts = db.get_all_posts()
        user_id = user.uid if user else None
        now = datetime.now(timezone.utc)

        # Filter out hidden posts for this user
        if user_id:
            hidden_ids = db.hides.get(user_id, set())
            all_raw_posts = [p for p in all_raw_posts if p["id"] not in hidden_ids]

        # Apply topic filter if requested
        if topic:
            t_lower = topic.lower().strip()
            all_raw_posts = [p for p in all_raw_posts if any(t_lower in t.lower() for t in p.get("topics", []))]

        # Apply search query filter if requested
        if search_query:
            q = search_query.lower().strip()
            all_raw_posts = [
                p for p in all_raw_posts
                if q in p.get("title", "").lower()
                or q in p.get("text", "").lower()
                or any(q in t.lower() for t in p.get("topics", []))
            ]

        # Score every post
        scored_posts: List[Tuple[Dict[str, Any], float]] = []
        for p in all_raw_posts:
            s = self.calculate_score(p, now, user)
            # Never mutate a DB record while deriving a request-specific score.
            p_copy = {**p, "stats": {**p.get("stats", {}), "ranking_score": s}}
            scored_posts.append((p_copy, s))

        # Sort mode based on tab
        if tab == FeedTab.LATEST:
            scored_posts.sort(key=lambda x: x[0].get("created_at", ""), reverse=True)
            candidate_items = []
            for post_dict, _ in scored_posts:
                idea = db.get_post(post_dict["id"], user_id)
                if idea:
                    candidate_items.append(FeedItem(
                        idea=idea,
                        recommendation_reason="Recent Post",
                        source_type="latest",
                    ))

        elif tab == FeedTab.TRENDING:
            # Sort strictly by ranking score
            scored_posts.sort(key=lambda x: x[1], reverse=True)
            candidate_items = []
            for post_dict, _ in scored_posts:
                idea = db.get_post(post_dict["id"], user_id)
                if idea:
                    candidate_items.append(FeedItem(
                        idea=idea,
                        recommendation_reason="🔥 Trending right now",
                        source_type="trending",
                    ))

        else:
            # tab == FeedTab.FOR_YOU: 3-Stage Pipeline (Candidate Gen -> Rank -> Diversify)
            candidate_items = self._build_for_you_feed(user, scored_posts, user_id, refresh_seed)

        # Pagination
        total = len(candidate_items)
        paginated_items = candidate_items[offset : offset + limit]
        has_more = (offset + limit) < total
        next_offset = (offset + limit) if has_more else None

        return FeedResponse(
            items=paginated_items,
            total=total,
            has_more=has_more,
            next_offset=next_offset,
            tab=tab,
            filter_topic=topic
        )

    def get_following_feed(
        self,
        user: UserProfile,
        offset: int = 0,
        limit: int = 10,
    ) -> FeedResponse:
        """
        Dedicated FOLLOWING tab: shows only posts from people the user follows.
        Sorted strictly by newest-first. Always fresh per-request.
        """
        user_id = user.uid
        followed_authors = db.follows.get(user_id, set())

        if not followed_authors:
            return FeedResponse(
                items=[], total=0, has_more=False,
                next_offset=None, tab=FeedTab.FOLLOWING
            )

        # Use fan-out timeline for efficiency
        timeline_ids = db.user_timelines.get(user_id, [])
        # Fallback: scan posts if timeline empty
        if not timeline_ids:
            all_posts = db.get_all_posts()
            timeline_ids = [
                p["id"] for p in sorted(
                    [p for p in all_posts if p.get("author_id") in followed_authors],
                    key=lambda x: x.get("created_at", ""), reverse=True
                )
            ]

        items = []
        hidden_ids = db.hides.get(user_id, set())
        for pid in timeline_ids:
            if pid in hidden_ids:
                continue
            post = db.posts.get(pid)
            if not post or post.get("author_id") not in followed_authors:
                continue
            idea = db.get_post(pid, user_id)
            if idea:
                author_name = post.get("author_name", "")
                is_friend = db.is_friend(user_id, post.get("author_id", ""))
                reason = (f"🤝 Friends with {author_name}" if is_friend
                          else f"👤 From {author_name}")
                items.append(FeedItem(
                    idea=idea,
                    recommendation_reason=reason,
                    source_type="following"
                ))

        total = len(items)
        paginated = items[offset: offset + limit]
        has_more = (offset + limit) < total
        return FeedResponse(
            items=paginated,
            total=total,
            has_more=has_more,
            next_offset=(offset + limit) if has_more else None,
            tab=FeedTab.FOLLOWING,
        )

    def _build_for_you_feed(
        self,
        user: Optional[UserProfile],
        scored_posts: List[Tuple[Dict[str, Any], float]],
        user_id: Optional[str],
        refresh_seed: Optional[int] = None,
    ) -> List[FeedItem]:
        user_interests = set(t.lower() for t in (user.interests if user else []))
        followed_authors = set(db.follows.get(user.uid, set()) if user else set())
        user_role = (user.role or "").title() if user else None

        # ─── AI Re-ranking ──────────────────────────────────────────────────────
        sorted_all = sorted(scored_posts, key=lambda x: x[1], reverse=True)
        all_raw_dicts = [p for p, _ in sorted_all]
        if user:
            all_raw_dicts = self._ai_personalization_hook(user, all_raw_dicts)
            sorted_all = [
                (p, p.get("ai_feed_score", p.get("stats", {}).get("ranking_score", 0.0)))
                for p in all_raw_dicts
            ]

        # ─── Bucket classification ─────────────────────────────────────────────────
        # Followed author posts (separate bucket — inserted at TOP)
        followed_items: List[Tuple[Dict, float, str]] = []
        interest_candidates: List[Tuple[Dict, float, str]] = []
        trending_candidates: List[Tuple[Dict, float, str]] = []
        serendipity_candidates: List[Tuple[Dict, float, str]] = []

        user_interest_list = list(user_interests)
        for post_dict, score in sorted_all:
            post_topics = set(t.lower() for t in post_dict.get("topics", []))
            author_id = post_dict.get("author_id", "")
            ai_scored = post_dict.get("ai_feed_score") is not None

            if author_id in followed_authors:
                is_friend = db.is_friend(user.uid if user else "", author_id)
                reason = (
                    f"🤝 Friends • {post_dict.get('author_name', '')}"
                    if is_friend else
                    f"👤 Following • {post_dict.get('author_name', '')}"
                )
                followed_items.append((post_dict, score, reason))
            elif user_interests and (post_topics & user_interests):
                if ai_scored:
                    try:
                        from app.ai.feed_ranker import ai_feed_ranker
                        reason = ai_feed_ranker.get_recommendation_reason(post_dict, user_interest_list)
                    except Exception:
                        matched = list(post_topics & user_interests)[0]
                        reason = f"Curated for #{matched}"
                        if user_role:
                            reason += f" • {user_role} Pick"
                else:
                    matched = list(post_topics & user_interests)[0]
                    reason = f"Curated for #{matched}"
                    if user_role:
                        reason += f" • {user_role} Pick"
                interest_candidates.append((post_dict, score, reason))
            else:
                trending_candidates.append((post_dict, score, "🔥 Trending across Pulse"))
                serendipity_candidates.append((post_dict, score, "✨ Serendipity discovery"))

        if not interest_candidates and not followed_items:
            interest_candidates = [(p, s, "🔥 Top pulse idea") for p, s in sorted_all]

        # Shuffle serendipity for freshness on each refresh
        rng_seed = refresh_seed if refresh_seed is not None else int(datetime.utcnow().timestamp() // 300)  # changes every 5 min
        rng = random.Random(rng_seed)
        rng.shuffle(serendipity_candidates)

        # ─── Assemble feed ──────────────────────────────────────────────────
        seen_ids: set = set()
        final_feed_items: List[FeedItem] = []

        def _add(p_dict, reason, source):
            if p_dict["id"] in seen_ids:
                return False
            idea = db.get_post(p_dict["id"], user_id)
            if not idea:
                return False
            final_feed_items.append(FeedItem(idea=idea, recommendation_reason=reason, source_type=source))
            seen_ids.add(p_dict["id"])
            return True

        # Step 1: All followed-author posts first (preserving score order)
        for p_dict, _, reason in followed_items:
            _add(p_dict, reason, "following")

        # Step 2: Smoothly interleave interest / trending / serendipity using
        # the configured weights instead of a hard-coded 60/20/20 pattern.
        buckets = {
            "interest": interest_candidates,
            "trending": trending_candidates,
            "serendipity": serendipity_candidates,
        }
        bucket_indices = {source: 0 for source in buckets}
        total_available = len(sorted_all)

        while len(seen_ids) < total_available:
            added_this_round = 0
            for source in self._mix_schedule:
                candidates = buckets[source]
                index = bucket_indices[source]
                if index >= len(candidates):
                    continue
                p_dict, _, reason = candidates[index]
                bucket_indices[source] = index + 1
                if _add(p_dict, reason, source):
                    added_this_round += 1
            if added_this_round == 0:
                for p_dict, _ in sorted_all:
                    if _add(p_dict, "Explore", "discovery"): pass
                break

        return final_feed_items

feed_service = FeedService()
