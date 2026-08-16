import os
import json
import uuid
import re
import threading
import tempfile
from typing import Dict, List, Optional, Any, Set
from datetime import datetime, timedelta, timezone

from app.models.user import UserProfile, UserPublicProfile, UserFullInfo
from app.models.post import IdeaResponse, PostStats, ReactionType
from app.models.comment import CommentResponse, CommentType
from app.models.feed import CategoryInfo
from app.models.event import UserEvent
from app.models.message import MessageResponse, ConversationMeta, UserSummary
from app.database.seed_data import SEED_CATEGORIES

DB_FILE_PATH = os.getenv("DB_FILE_PATH", os.path.join(os.path.dirname(__file__), "platform_data.json"))

# Valid username pattern: 3-30 chars, lowercase alphanumeric + underscore
USERNAME_RE = re.compile(r"^[a-z0-9_]{3,30}$")

# A mention must begin at a natural text boundary.  This deliberately avoids
# interpreting email addresses (``name@example.com``) and URL paths
# (``https://example.com/@name``) as tags.  Usernames themselves remain
# case-insensitive at input time and are resolved through the canonical
# username index.
MENTION_RE = re.compile(r"(?<![\w@/])@([a-z0-9_]{3,30})(?![\w@])", re.IGNORECASE)
MENTION_URL_RE = re.compile(r"(?:https?://|www\.)[^\s<>()]+", re.IGNORECASE)
MENTION_NOTIFICATION_LIMIT_PER_HOUR = 12


class Database:
    def __init__(self):
        self._lock = threading.RLock()
        # Core data stores
        self.users: Dict[str, Dict[str, Any]] = {}          # uid -> user_dict
        self.usernames: Dict[str, str] = {}                 # username -> uid  (uniqueness index)
        self.user_ids: Dict[str, str] = {}                  # user_id -> uid   (platform ID index)
        self.posts: Dict[str, Dict[str, Any]] = {}
        self.comments: Dict[str, Dict[str, Any]] = {}
        self.reactions: Dict[str, Set[str]] = {}            # "{post_id}:{user_id}" -> set of reaction_types
        self.comment_likes: Dict[str, Set[str]] = {}        # comment_id -> set of user_ids
        self.hides: Dict[str, Set[str]] = {}                # user_id -> set of post_ids
        self.follows: Dict[str, Set[str]] = {}              # user_id -> set of target_user_ids
        self.followers: Dict[str, Set[str]] = {}            # user_id -> set of follower_user_ids
        self.user_timelines: Dict[str, List[str]] = {}      # user_id -> list of post_ids (fan-out)
        self.categories: Dict[str, Dict[str, Any]] = {}
        self.events: List[Dict[str, Any]] = []

        # ─── Messaging store ──────────────────────────────────────────────────
        # Each conversation is keyed by canonical ID: "_".join(sorted([uid_a, uid_b]))
        self.conversations: Dict[str, Dict[str, Any]] = {}  # conv_id -> meta
        self.messages: Dict[str, List[Dict[str, Any]]] = {} # conv_id -> [msg, ...]
        self.user_conversations: Dict[str, List[str]] = {}  # uid -> [conv_id, ...]

        # ─── Activity notifications ──────────────────────────────────────────
        # recipient_uid -> newest-first notification records. These are stored
        # separately from generic interaction events so they are safe to show to
        # an individual user and survive server restarts.
        self.notifications: Dict[str, List[Dict[str, Any]]] = {}
        # recipient_uid -> post_id -> ISO timestamps. Keeping the rolling
        # window separately means trimming old notification history can never
        # accidentally bypass the per-post hourly delivery cap.
        self.notification_reply_windows: Dict[str, Dict[str, List[str]]] = {}
        # recipient_uid -> timestamps.  Mention alerts use an independent,
        # recipient-level window so someone cannot cause unbounded alerts by
        # tagging a person across many unrelated ideas or comments.
        self.notification_mention_windows: Dict[str, List[str]] = {}

        # Per-WebSocket per-user registry for targeted DM delivery
        # uid -> list of WebSocket connections for that user
        self.ws_user_connections: Dict[str, List[Any]] = {}

        # WebSocket broadcast registry (populated by main.py)
        self.ws_connections: List[Any] = []

        self._load_or_seed()

    # ─── Persistence ─────────────────────────────────────────────────────────

    def _load_or_seed(self):
        with self._lock:
            if os.path.exists(DB_FILE_PATH):
                try:
                    with open(DB_FILE_PATH, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    self.users = data.get("users", {})
                    self.usernames = data.get("usernames", {})
                    self.user_ids = data.get("user_ids", {})
                    self.posts = data.get("posts", {})
                    self.comments = data.get("comments", {})
                    self.reactions = {k: set(v) for k, v in data.get("reactions", {}).items()}
                    self.comment_likes = {k: set(v) for k, v in data.get("comment_likes", {}).items()}
                    self.hides = {k: set(v) for k, v in data.get("hides", {}).items()}
                    self.follows = {k: set(v) for k, v in data.get("follows", {}).items()}
                    self.followers = {k: set(v) for k, v in data.get("followers", {}).items()}
                    self.user_timelines = data.get("user_timelines", {})
                    self.categories = data.get("categories", {})
                    self.events = data.get("events", [])
                    self.conversations = data.get("conversations", {})
                    self.messages = data.get("messages", {})
                    self.user_conversations = data.get("user_conversations", {})
                    self.notifications = {
                        str(uid): [item for item in items if isinstance(item, dict)]
                        for uid, items in data.get("notifications", {}).items()
                        if isinstance(items, list)
                    }
                    self.notification_reply_windows = {
                        str(uid): {
                            str(post_id): [timestamp for timestamp in timestamps if isinstance(timestamp, str)]
                            for post_id, timestamps in post_windows.items()
                            if isinstance(timestamps, list)
                        }
                        for uid, post_windows in data.get("notification_reply_windows", {}).items()
                        if isinstance(post_windows, dict)
                    }
                    self.notification_mention_windows = {
                        str(uid): [timestamp for timestamp in timestamps if isinstance(timestamp, str)]
                        for uid, timestamps in data.get("notification_mention_windows", {}).items()
                        if isinstance(timestamps, list)
                    }
                    # Rebuild indexes from loaded users if missing
                    self._rebuild_indexes()
                    # Always merge in any new seed categories without overwriting existing
                    self._merge_seed_categories()
                    return
                except Exception as e:
                    print(f"Warning: Failed to load DB ({e}), re-seeding...")

            # A fresh development instance begins with the supported category
            # catalogue only. Content and engagement are always user-created.
            for cat in SEED_CATEGORIES:
                fresh_category = cat.copy()
                fresh_category["posts_count"] = 0
                fresh_category["followers_count"] = 0
                self.categories[cat["id"]] = fresh_category
            self._save()

    def _merge_seed_categories(self):
        """Add newly defined seed categories that don't yet exist in the loaded DB."""
        for cat in SEED_CATEGORIES:
            if cat["id"] not in self.categories:
                new_category = cat.copy()
                new_category["posts_count"] = 0
                new_category["followers_count"] = 0
                self.categories[cat["id"]] = new_category

    def _rebuild_indexes(self):
        """Rebuild username→uid and user_id→uid reverse indexes from user records."""
        for uid, u in self.users.items():
            uname = u.get("username")
            if uname:
                self.usernames[uname.lower()] = uid
            uid_val = u.get("user_id")
            if uid_val:
                self.user_ids[uid_val] = uid

    def _save(self):
        with self._lock:
            temp_path = None
            try:
                data = {
                    "users": self.users,
                    "usernames": self.usernames,
                    "user_ids": self.user_ids,
                    "posts": self.posts,
                    "comments": self.comments,
                    "reactions": {k: list(v) for k, v in self.reactions.items()},
                    "comment_likes": {k: list(v) for k, v in self.comment_likes.items()},
                    "hides": {k: list(v) for k, v in self.hides.items()},
                    "follows": {k: list(v) for k, v in self.follows.items()},
                    "followers": {k: list(v) for k, v in self.followers.items()},
                    "user_timelines": self.user_timelines,
                    "categories": self.categories,
                    "events": self.events[-1000:],
                    "conversations": self.conversations,
                    "messages": self.messages,
                    "user_conversations": self.user_conversations,
                    "notifications": self.notifications,
                    "notification_reply_windows": self.notification_reply_windows,
                    "notification_mention_windows": self.notification_mention_windows,
                }
                database_dir = os.path.dirname(os.path.abspath(DB_FILE_PATH))
                os.makedirs(database_dir, exist_ok=True)
                file_descriptor, temp_path = tempfile.mkstemp(
                    prefix=".platform-data-",
                    suffix=".json",
                    dir=database_dir,
                )
                with os.fdopen(file_descriptor, "w", encoding="utf-8") as f:
                    json.dump(data, f, indent=2, ensure_ascii=False)
                    f.flush()
                    os.fsync(f.fileno())
                # Atomic replacement means a crash can leave either the prior
                # valid state or the new valid state, never half-written JSON.
                os.replace(temp_path, DB_FILE_PATH)
                temp_path = None
            except Exception as e:
                print(f"Error saving DB: {e}")
            finally:
                if temp_path and os.path.exists(temp_path):
                    try:
                        os.unlink(temp_path)
                    except OSError:
                        pass

    # ─── Username Management ──────────────────────────────────────────────────

    def is_username_available(self, username: str) -> bool:
        """Returns True if username is not taken. Case-insensitive."""
        key = username.lower().strip()
        with self._lock:
            return key not in self.usernames

    def is_valid_username(self, username: str) -> bool:
        """Validates format: 3-30 lowercase alphanumeric + underscore."""
        return bool(USERNAME_RE.match(username.lower().strip()))

    def _reserve_username(self, uid: str, username: str) -> bool:
        """
        Atomically reserve a username for a uid.
        Returns True on success, False if already taken by another user.
        """
        key = username.lower().strip()
        existing_uid = self.usernames.get(key)
        if existing_uid and existing_uid != uid:
            return False
        # Release old username if user had one
        old_uname = self.users.get(uid, {}).get("username")
        if old_uname and old_uname.lower() != key:
            self.usernames.pop(old_uname.lower(), None)
        self.usernames[key] = uid
        return True

    # ─── User Management ─────────────────────────────────────────────────────

    def get_user(self, uid: str) -> Optional[UserProfile]:
        with self._lock:
            data = self.users.get(uid)
            if data:
                return UserProfile(**data)
            return None

    def get_user_by_username(self, username: str) -> Optional[UserProfile]:
        with self._lock:
            uid = self.usernames.get(username.lower().strip())
            if uid:
                return self.get_user(uid)
            return None

    def get_or_create_user(self, uid: str, email: Optional[str] = None,
                           display_name: Optional[str] = None,
                           photo_url: Optional[str] = None) -> UserProfile:
        with self._lock:
            if uid in self.users:
                user_dict = self.users[uid]

                # Always keep Google display_name as fallback (only if user hasn't set their own)
                if display_name and user_dict.get("display_name") in (None, "Pulse Explorer", ""):
                    user_dict["display_name"] = display_name

                # Store the latest Google photo as photo_url (raw Google URL) — never touch avatar_url
                # avatar_url = user's own uploaded/chosen avatar (takes priority in UI)
                if photo_url:
                    user_dict["photo_url"] = photo_url  # update Google pic silently

                # Fill missing email
                if email and not user_dict.get("email"):
                    user_dict["email"] = email

                self._save()
                return UserProfile(**user_dict)

            # ── Brand-new user ────────────────────────────────────────────────
            # Google photo becomes the default avatar until user uploads their own
            default_avatar = photo_url or f"https://api.dicebear.com/7.x/bottts/svg?seed={uid}"
            new_user = UserProfile(
                uid=uid,
                email=email,
                display_name=display_name or "Pulse Explorer",
                photo_url=default_avatar,   # Google photo stored here
                avatar_url=None,            # user-uploaded avatar — starts empty
                interests=[],
                onboarding_completed=False,
                created_at=datetime.utcnow().isoformat(),
                updated_at=datetime.utcnow().isoformat(),
            )
            # Ensure user_id is unique
            while new_user.user_id in self.user_ids:
                new_user.user_id = f"usr-{uuid.uuid4().hex[:12]}"

            self.users[uid] = new_user.model_dump()
            self.user_ids[new_user.user_id] = uid
            self._save()
            return new_user

    def save_user(self, user: UserProfile, new_username: Optional[str] = None) -> UserProfile:
        """
        Persist user. If new_username provided, atomically validates and reserves it.
        Raises ValueError if username taken or invalid.
        """
        with self._lock:
            if new_username is not None:
                clean = new_username.lower().strip()
                if not self.is_valid_username(clean):
                    raise ValueError("Username must be 3-30 characters: lowercase letters, numbers, underscores only.")
                if not self._reserve_username(user.uid, clean):
                    raise ValueError(f"Username '@{clean}' is already taken. Please choose another.")
                user.username = clean

            user.updated_at = datetime.utcnow().isoformat()
            user_dict = user.model_dump()
            self.users[user.uid] = user_dict

            # Keep reverse indexes in sync
            if user.username:
                self.usernames[user.username.lower()] = user.uid
            if user.user_id:
                self.user_ids[user.user_id] = user.uid

            self._save()

        # Keep CPU work outside the database lock so profile updates do not
        # block concurrent feed, message, or WebSocket operations.
        try:
            from app.services.search_service import search_service
            search_service.index_new_user(user.uid, user_dict)
        except Exception:
            pass

        return user

    def _user_summary(self, uid: str, requesting_uid: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """Build a compact UserSummary dict for a user."""
        u = self.users.get(uid)
        if not u:
            return None
        avatar = u.get("avatar_url") or u.get("photo_url")
        requester_following = self.follows.get(requesting_uid or "", set())
        target_following = self.follows.get(uid, set())
        is_following = uid in requester_following
        is_friend = is_following and (requesting_uid in target_following if requesting_uid else False)
        return {
            "uid": uid,
            "user_id": u.get("user_id", ""),
            "username": u.get("username"),
            "display_name": u.get("display_name"),
            "avatar_url": avatar,
            "bio": u.get("bio", "")[:120],
            "followers_count": len(self.followers.get(uid, set())),
            "following_count": len(self.follows.get(uid, set())),
            "is_following": is_following,
            "is_friend": is_friend,
        }

    def get_user_full_info(self, uid: str, requesting_uid: Optional[str] = None) -> Optional[UserFullInfo]:
        """Returns comprehensive user info: profile + recent posts + follower/following counts + friend previews."""
        with self._lock:
            user = self.get_user(uid)
            if not user:
                return None

            # Build public profile
            avatar = user.avatar_url or user.photo_url
            pub = UserPublicProfile(
                uid=user.uid,
                user_id=user.user_id,
                username=user.username,
                display_name=user.display_name,
                avatar_url=avatar,
                photo_url=user.photo_url,
                role=user.role,
                bio=user.bio,
                things_i_love=user.things_i_love,
                location=user.location,
                contact_email=user.contact_email,
                social_links=user.social_links,
                interests=user.interests,
                ideas_count=user.ideas_count,
                followers_count=len(self.followers.get(uid, set())),
                following_count=len(self.follows.get(uid, set())),
                likes_received_count=user.likes_received_count,
                created_at=user.created_at,
            )

            # Recent posts by this user (last 10, newest first)
            user_posts_raw = sorted(
                [p for p in self.posts.values() if p.get("author_id") == uid],
                key=lambda x: x.get("created_at", ""), reverse=True
            )
            recent_posts = []
            for p in user_posts_raw[:10]:
                idea = self.get_post(p["id"], requesting_uid)
                if idea:
                    recent_posts.append(idea.model_dump())

            requester_follows_target = uid in self.follows.get(requesting_uid or "", set())
            target_follows_requester = (requesting_uid in self.follows.get(uid, set())) if requesting_uid else False
            is_friend = requester_follows_target and target_follows_requester

            # Followers preview (first 6)
            follower_ids = list(self.followers.get(uid, set()))[:6]
            followers_preview = [s for fid in follower_ids for s in [self._user_summary(fid, requesting_uid)] if s]

            # Following preview (first 6)
            following_ids = list(self.follows.get(uid, set()))[:6]
            following_preview = [s for fid in following_ids for s in [self._user_summary(fid, requesting_uid)] if s]

            return UserFullInfo(
                profile=pub,
                recent_posts=recent_posts,
                is_following=requester_follows_target,
                is_friend=is_friend,
                followers_preview=followers_preview,
                following_preview=following_preview,
            )

    # ─── Categories ───────────────────────────────────────────────────────────

    def get_categories(self) -> List[CategoryInfo]:
        with self._lock:
            cats = list(self.categories.values())
            cats.sort(key=lambda c: (-c.get("followers_count", 0), c.get("name", "")))
            return [CategoryInfo(**cat) for cat in cats]

    # ─── Posts ────────────────────────────────────────────────────────────────

    def get_post(self, post_id: str, requesting_user_id: Optional[str] = None) -> Optional[IdeaResponse]:
        with self._lock:
            data = self.posts.get(post_id)
            if not data:
                return None

            # Skip soft-deleted posts
            if data.get("deleted"):
                return None

            user_reactions = []
            user_hidden = False
            is_following = False
            is_own = False

            if requesting_user_id:
                key = f"{post_id}:{requesting_user_id}"
                user_reactions = list(self.reactions.get(key, set()))
                user_hidden = post_id in self.hides.get(requesting_user_id, set())
                is_following = data["author_id"] in self.follows.get(requesting_user_id, set())
                is_own = data["author_id"] == requesting_user_id

            # Resolve author's effective avatar:
            # Priority: user's uploaded avatar_url > Google photo_url > post-time snapshot
            author_uid = data.get("author_id", "")
            author_user = self.users.get(author_uid, {})
            author_avatar = (
                author_user.get("avatar_url")           # user-uploaded (highest priority)
                or author_user.get("photo_url")         # Google / default photo
                or data.get("author_photo")             # snapshot at post-creation time
                or f"https://api.dicebear.com/7.x/bottts/svg?seed={author_uid}"
            )

            # Build image_urls list (backward compat with legacy single image_url)
            raw_urls = data.get("image_urls") or []
            if not raw_urls and data.get("image_url"):
                raw_urls = [data["image_url"]]

            return IdeaResponse(
                id=data["id"],
                title=data["title"],
                text=data["text"],
                summary=data.get("summary") or (data["text"][:140] + "..."),
                author_id=data["author_id"],
                author_name=data["author_name"],
                author_handle=data.get("author_handle") or author_user.get("username"),
                author_photo=data.get("author_photo"),
                author_avatar_url=author_avatar,
                topics=data.get("topics", []),
                stats=PostStats(**data.get("stats", {})),
                image_url=raw_urls[0] if raw_urls else None,
                image_urls=raw_urls,
                user_reactions=user_reactions,
                user_hidden=user_hidden,
                is_following_author=is_following,
                is_own_post=is_own,
                created_at=data["created_at"],
                updated_at=data["updated_at"],
            )

    def get_all_posts(self) -> List[Dict[str, Any]]:
        with self._lock:
            return list(self.posts.values())

    def create_post(self, author: UserProfile, title: str, text: str,
                    topics: List[str], summary: Optional[str] = None,
                    image_url: Optional[str] = None,
                    image_path: Optional[str] = None,
                    image_urls: Optional[List[str]] = None) -> IdeaResponse:
        with self._lock:
            post_id = f"idea-{uuid.uuid4().hex[:8]}"
            now_iso = datetime.utcnow().isoformat()

            author_avatar = author.avatar_url or author.photo_url

            post_dict = {
                "id": post_id,
                "title": title,
                "text": text,
                "summary": summary or (text[:140] + ("..." if len(text) > 140 else "")),
                "author_id": author.uid,
                "author_name": author.display_name or "Anonymous Thinker",
                "author_handle": author.username,
                "author_photo": author_avatar,
                "topics": [t.lower().strip() for t in topics],
                "image_url": image_url,
                "image_path": image_path,
                "image_urls": image_urls or ([image_url] if image_url else []),
                "deleted": False,
                "stats": {
                    "likes": 0, "fires": 0, "bulbs": 0,
                    "comments": 0, "shares": 0, "views": 0,
                    "hides": 0, "ranking_score": 0.0
                },
                "created_at": now_iso,
                "updated_at": now_iso,
            }
            self.posts[post_id] = post_dict

            # Update user stats
            if author.uid in self.users:
                self.users[author.uid]["ideas_count"] = self.users[author.uid].get("ideas_count", 0) + 1

            # Update category counts
            for topic in post_dict["topics"]:
                if topic in self.categories:
                    self.categories[topic]["posts_count"] = self.categories[topic].get("posts_count", 0) + 1

            # Fan-out to followers' timeline
            self._fan_out_post(post_id, author.uid)
            self._save()
            post_for_index = dict(post_dict)
            created_post = self.get_post(post_id, author.uid)

        # Search indexing is deliberately outside the write lock. It is an
        # in-memory optimization; persistence has already completed above.
        try:
            from app.services.search_service import search_service
            search_service.index_new_post(post_for_index)
        except Exception:
            pass

        return created_post

    def _fan_out_post(self, post_id: str, author_id: str):
        """Push post_id into each follower's precomputed timeline."""
        for follower_id in self.followers.get(author_id, set()):
            tl = self.user_timelines.setdefault(follower_id, [])
            tl.insert(0, post_id)

    # ─── Reactions ────────────────────────────────────────────────────────────

    def toggle_reaction(self, post_id: str, user_id: str, reaction_type: ReactionType) -> Dict[str, Any]:
        with self._lock:
            post = self.posts.get(post_id)
            if not post:
                return {"success": False, "error": "Post not found"}

            key = f"{post_id}:{user_id}"
            user_reacts = self.reactions.setdefault(key, set())
            r_val = reaction_type.value
            stats = post.setdefault("stats", {})
            added = False

            if r_val in user_reacts:
                user_reacts.remove(r_val)
                stat_key = f"{r_val}s"
                stats[stat_key] = max(0, stats.get(stat_key, 0) - 1)
            else:
                user_reacts.add(r_val)
                stat_key = f"{r_val}s"
                stats[stat_key] = stats.get(stat_key, 0) + 1
                added = True
                # Boost author received likes count
                author_id = post.get("author_id")
                if author_id and author_id in self.users:
                    self.users[author_id]["likes_received_count"] = \
                        self.users[author_id].get("likes_received_count", 0) + 1

            self._save()
            return {
                "success": True,
                "added": added,
                "reaction_type": r_val,
                "stats": stats,
                "user_reactions": list(user_reacts)
            }

    # ─── Shares & Views ───────────────────────────────────────────────────────

    def increment_share(self, post_id: str) -> int:
        with self._lock:
            post = self.posts.get(post_id)
            if post:
                stats = post.setdefault("stats", {})
                stats["shares"] = stats.get("shares", 0) + 1
                self._save()
                return stats["shares"]
            return 0

    def increment_view(self, post_id: str):
        with self._lock:
            post = self.posts.get(post_id)
            if post:
                stats = post.setdefault("stats", {})
                stats["views"] = stats.get("views", 0) + 1
                # Note: saving on every view is expensive — batch this in production

    # ─── Comments ─────────────────────────────────────────────────────────────

    def add_comment(self, post_id: str, author: UserProfile, text: str,
                    comment_type: CommentType, parent_id: Optional[str] = None) -> Optional[CommentResponse]:
        with self._lock:
            post = self.posts.get(post_id)
            if not post:
                return None

            comment_id = f"cmt-{uuid.uuid4().hex[:8]}"
            now_iso = datetime.utcnow().isoformat()

            author_avatar = author.avatar_url or author.photo_url

            comment_dict = {
                "id": comment_id,
                "post_id": post_id,
                "author_id": author.uid,
                "author_name": author.display_name or "Pulse Member",
                "author_handle": author.username,
                "author_photo": author_avatar,
                "text": text,
                "comment_type": comment_type.value,
                "parent_id": parent_id,
                "likes_count": 0,
                "created_at": now_iso,
            }
            self.comments[comment_id] = comment_dict
            stats = post.setdefault("stats", {})
            stats["comments"] = stats.get("comments", 0) + 1
            self._save()

            return CommentResponse(
                id=comment_id,
                post_id=post_id,
                author_id=author.uid,
                author_name=author.display_name or "Pulse Member",
                author_photo=author_avatar,
                text=text,
                comment_type=comment_type,
                parent_id=parent_id,
                likes_count=0,
                user_liked=False,
                created_at=now_iso,
                replies=[],
            )

    def get_comments_for_post(self, post_id: str, requesting_user_id: Optional[str] = None) -> List[CommentResponse]:
        """Returns threaded comment tree with infinite-depth replies, depth tracking, and reply_count."""
        with self._lock:
            all_cmts = [c for c in self.comments.values() if c["post_id"] == post_id]
            all_cmts.sort(key=lambda x: x["created_at"])  # oldest first so parent always before child

            # Count direct children for reply_count (denormalized display hint)
            child_count: Dict[str, int] = {}
            for c in all_cmts:
                pid = c.get("parent_id")
                if pid:
                    child_count[pid] = child_count.get(pid, 0) + 1

            cmt_map: Dict[str, CommentResponse] = {}
            top_level: List[CommentResponse] = []

            for c in all_cmts:
                user_liked = (
                    requesting_user_id in self.comment_likes.get(c["id"], set())
                    if requesting_user_id else False
                )
                author_uid = c.get("author_id", "")
                author_user = self.users.get(author_uid, {})
                author_avatar = author_user.get("avatar_url") or c.get("author_photo")

                # Compute depth from parent
                parent_depth = cmt_map[c["parent_id"]].depth if c.get("parent_id") and c["parent_id"] in cmt_map else -1
                depth = parent_depth + 1 if parent_depth >= 0 else 0

                resp = CommentResponse(
                    id=c["id"],
                    post_id=c["post_id"],
                    author_id=c["author_id"],
                    author_name=c["author_name"],
                    author_handle=c.get("author_handle") or author_user.get("username"),
                    author_photo=author_avatar,
                    text=c["text"],
                    comment_type=CommentType(c.get("comment_type", "general")),
                    parent_id=c.get("parent_id"),
                    depth=depth,
                    likes_count=c.get("likes_count", 0),
                    reply_count=child_count.get(c["id"], 0),
                    user_liked=user_liked,
                    created_at=c["created_at"],
                    replies=[],
                )
                cmt_map[c["id"]] = resp

            # Wire up parent→child links
            for c in all_cmts:
                pid = c.get("parent_id")
                current = cmt_map[c["id"]]
                if pid and pid in cmt_map:
                    cmt_map[pid].replies.append(current)
                else:
                    top_level.append(current)

            return top_level

    def toggle_comment_like(self, comment_id: str, user_id: str) -> Dict[str, Any]:
        with self._lock:
            cmt = self.comments.get(comment_id)
            if not cmt:
                return {"success": False, "error": "Comment not found"}
            liked_set = self.comment_likes.setdefault(comment_id, set())
            if user_id in liked_set:
                liked_set.remove(user_id)
                cmt["likes_count"] = max(0, cmt.get("likes_count", 0) - 1)
                user_liked = False
            else:
                liked_set.add(user_id)
                cmt["likes_count"] = cmt.get("likes_count", 0) + 1
                user_liked = True
            self._save()
            return {"success": True, "user_liked": user_liked, "likes_count": cmt["likes_count"]}

    # ─── Hides & Follows ──────────────────────────────────────────────────────

    def toggle_hide_post(self, user_id: str, post_id: str) -> bool:
        with self._lock:
            hidden_set = self.hides.setdefault(user_id, set())
            if post_id in hidden_set:
                hidden_set.remove(post_id)
                hidden = False
            else:
                hidden_set.add(post_id)
                hidden = True
                post = self.posts.get(post_id)
                if post:
                    post.setdefault("stats", {})["hides"] = post["stats"].get("hides", 0) + 1
            self._save()
            return hidden

    def toggle_follow_author(self, user_id: str, target_author_id: str) -> bool:
        """Follow/unfollow. Pushes/removes existing posts from followed user's timeline."""
        with self._lock:
            following_set = self.follows.setdefault(user_id, set())
            follower_set = self.followers.setdefault(target_author_id, set())

            if target_author_id in following_set:
                following_set.remove(target_author_id)
                follower_set.discard(user_id)
                is_following = False
                # Remove target's posts from follower's timeline
                tl = self.user_timelines.get(user_id, [])
                target_posts = {p for p, d in self.posts.items() if d.get("author_id") == target_author_id}
                self.user_timelines[user_id] = [pid for pid in tl if pid not in target_posts]
            else:
                following_set.add(target_author_id)
                follower_set.add(user_id)
                is_following = True
                # Fan-in target's recent posts (last 50) into follower's timeline
                target_posts = sorted(
                    [p for p, d in self.posts.items() if d.get("author_id") == target_author_id],
                    key=lambda pid: self.posts[pid].get("created_at", ""), reverse=True
                )[:50]
                tl = self.user_timelines.setdefault(user_id, [])
                existing = set(tl)
                for pid in target_posts:
                    if pid not in existing:
                        tl.insert(0, pid)
                # Also add target to followed_authors list (for feed service)
                if user_id in self.users:
                    fa = self.users[user_id].setdefault("followed_authors", [])
                    if target_author_id not in fa:
                        fa.append(target_author_id)

            if not is_following:
                # Remove from followed_authors
                if user_id in self.users:
                    fa = self.users[user_id].get("followed_authors", [])
                    if target_author_id in fa:
                        fa.remove(target_author_id)
                    self.users[user_id]["followed_authors"] = fa

            # Update denormalized counts
            if user_id in self.users:
                self.users[user_id]["following_count"] = len(following_set)
            if target_author_id in self.users:
                self.users[target_author_id]["followers_count"] = len(follower_set)

            self._save()
            return is_following

    def is_friend(self, uid_a: str, uid_b: str) -> bool:
        """Returns True if both users follow each other (mutual follow = friend)."""
        return (
            uid_b in self.follows.get(uid_a, set()) and
            uid_a in self.follows.get(uid_b, set())
        )

    def get_followers_list(self, uid: str, requesting_uid: Optional[str] = None,
                           limit: int = 50, offset: int = 0) -> List[Dict[str, Any]]:
        """Returns paginated follower list with relationship info."""
        with self._lock:
            ids = list(self.followers.get(uid, set()))
            page = ids[offset: offset + limit]
            return [s for fid in page for s in [self._user_summary(fid, requesting_uid)] if s]

    def get_following_list(self, uid: str, requesting_uid: Optional[str] = None,
                           limit: int = 50, offset: int = 0) -> List[Dict[str, Any]]:
        """Returns paginated following list with relationship info."""
        with self._lock:
            ids = list(self.follows.get(uid, set()))
            page = ids[offset: offset + limit]
            return [s for fid in page for s in [self._user_summary(fid, requesting_uid)] if s]

    def get_friends_list(self, uid: str, limit: int = 50, offset: int = 0) -> List[Dict[str, Any]]:
        """Returns users that mutually follow uid (friends)."""
        with self._lock:
            following = self.follows.get(uid, set())
            friends = [fid for fid in following if uid in self.follows.get(fid, set())]
            page = friends[offset: offset + limit]
            return [s for fid in page for s in [self._user_summary(fid, uid)] if s]

    # ─── Messaging ────────────────────────────────────────────────────────────

    def _conv_id(self, uid_a: str, uid_b: str) -> str:
        """Canonical conversation ID — always the same regardless of who initiates."""
        return "_".join(sorted([uid_a, uid_b]))

    def get_or_create_conversation(self, uid_a: str, uid_b: str) -> Dict[str, Any]:
        """Get or create a 1-to-1 conversation between two users."""
        with self._lock:
            conv_id = self._conv_id(uid_a, uid_b)
            if conv_id not in self.conversations:
                now = datetime.utcnow().isoformat()
                self.conversations[conv_id] = {
                    "conv_id": conv_id,
                    "participants": sorted([uid_a, uid_b]),
                    "created_at": now,
                    "last_message_at": None,
                    "last_message_text": None,
                    "unread": {uid_a: 0, uid_b: 0},
                }
                self.messages[conv_id] = []
                for uid in [uid_a, uid_b]:
                    convs = self.user_conversations.setdefault(uid, [])
                    if conv_id not in convs:
                        convs.insert(0, conv_id)
                self._save()
            return self.conversations[conv_id]

    def send_message(self, conv_id: str, sender_uid: str,
                     text: str, image_url: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """Append a message to a conversation. Updates last_message and unread counters."""
        with self._lock:
            conv = self.conversations.get(conv_id)
            if not conv:
                return None
            if sender_uid not in conv["participants"]:
                return None

            msg_id = f"msg-{uuid.uuid4().hex[:12]}"
            now = datetime.utcnow().isoformat()
            sender = self.users.get(sender_uid, {})

            msg = {
                "id": msg_id,
                "conv_id": conv_id,
                "sender_uid": sender_uid,
                "sender_name": sender.get("display_name", "Pulse Member"),
                "sender_avatar": sender.get("avatar_url") or sender.get("photo_url"),
                "text": text,
                "image_url": image_url,
                "read_by": [sender_uid],
                "edited": False,
                "deleted": False,
                "created_at": now,
                "edited_at": None,
            }
            self.messages.setdefault(conv_id, []).append(msg)

            # Update conversation meta
            conv["last_message_at"] = now
            conv["last_message_text"] = text[:80] + ("..." if len(text) > 80 else "")
            # Increment unread for other participants
            for uid in conv["participants"]:
                if uid != sender_uid:
                    conv.setdefault("unread", {})[uid] = conv.get("unread", {}).get(uid, 0) + 1

            # Bump conversation to top of each participant's list
            for uid in conv["participants"]:
                cl = self.user_conversations.setdefault(uid, [])
                if conv_id in cl:
                    cl.remove(conv_id)
                cl.insert(0, conv_id)

            self._save()
            return msg

    def get_messages(self, conv_id: str, limit: int = 50, before_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """Return messages in a conversation, newest-last. Paginates before a given message ID."""
        with self._lock:
            msgs = list(self.messages.get(conv_id, []))
            if before_id:
                ids = [m["id"] for m in msgs]
                if before_id in ids:
                    idx = ids.index(before_id)
                    msgs = msgs[:idx]
            return msgs[-limit:]  # return last N (oldest-first within the page)

    def mark_conv_read(self, conv_id: str, uid: str):
        """Clear unread counter for a user in a conversation. Mark all messages as read by them."""
        with self._lock:
            conv = self.conversations.get(conv_id)
            if conv and uid in conv.get("participants", []):
                conv.setdefault("unread", {})[uid] = 0
                for msg in self.messages.get(conv_id, []):
                    if uid not in msg.get("read_by", []):
                        msg.setdefault("read_by", []).append(uid)
                self._save()

    def edit_message(self, conv_id: str, msg_id: str, sender_uid: str, new_text: str) -> Optional[Dict[str, Any]]:
        """Edit own message text."""
        with self._lock:
            for msg in self.messages.get(conv_id, []):
                if msg["id"] == msg_id and msg["sender_uid"] == sender_uid and not msg.get("deleted"):
                    msg["text"] = new_text
                    msg["edited"] = True
                    msg["edited_at"] = datetime.utcnow().isoformat()
                    # Keep the inbox preview truthful when the most recent
                    # message was edited, without touching its chronology.
                    history = self.messages.get(conv_id, [])
                    if history and history[-1].get("id") == msg_id:
                        self.conversations.get(conv_id, {})["last_message_text"] = new_text[:80] + ("..." if len(new_text) > 80 else "")
                    self._save()
                    return msg
            return None

    def delete_message(self, conv_id: str, msg_id: str, sender_uid: str) -> bool:
        """Soft-delete own message."""
        with self._lock:
            for msg in self.messages.get(conv_id, []):
                if msg["id"] == msg_id and msg["sender_uid"] == sender_uid:
                    msg["deleted"] = True
                    msg["text"] = "[Message deleted]"
                    msg["image_url"] = None
                    history = self.messages.get(conv_id, [])
                    if history and history[-1].get("id") == msg_id:
                        self.conversations.get(conv_id, {})["last_message_text"] = "[Message deleted]"
                    self._save()
                    return True
            return False

    def get_user_conversations(self, uid: str) -> List[Dict[str, Any]]:
        """Return conversation summaries for a user's inbox, newest-first."""
        with self._lock:
            conv_ids = self.user_conversations.get(uid, [])
            result = []
            for conv_id in conv_ids:
                conv = self.conversations.get(conv_id)
                if not conv:
                    continue
                other_uid = next((p for p in conv["participants"] if p != uid), None)
                if not other_uid:
                    continue
                other = self.users.get(other_uid, {})
                unread = conv.get("unread", {}).get(uid, 0)
                result.append({
                    "conv_id": conv_id,
                    "other_uid": other_uid,
                    "other_display_name": other.get("display_name", "Pulse Member"),
                    "other_avatar": other.get("avatar_url") or other.get("photo_url"),
                    "other_username": other.get("username"),
                    "last_message_text": conv.get("last_message_text"),
                    "last_message_at": conv.get("last_message_at"),
                    "unread_count": unread,
                    "is_friend": self.is_friend(uid, other_uid),
                })
            return result

    # ─── Activity notifications ─────────────────────────────────────────────

    @staticmethod
    def _notification_time(value: str) -> Optional[datetime]:
        """Parse both historical naive UTC values and current timezone-aware ones."""
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone(timezone.utc)
        except (TypeError, ValueError):
            return None

    def _reserve_notification_slot(self, recipient_uid: str, post_id: str, now: datetime) -> bool:
        """Reserve one of a recipient's three rolling hourly alerts for a post.

        This runs while ``self._lock`` is held.  The separate rolling window is
        intentional: trimming notification history must never reset the cap.
        """
        recipient_windows = self.notification_reply_windows.setdefault(recipient_uid, {})
        cutoff = now - timedelta(hours=1)
        timestamps = [
            timestamp
            for timestamp in recipient_windows.get(post_id, [])
            if (parsed := self._notification_time(timestamp)) is not None and parsed >= cutoff
        ]
        if len(timestamps) >= 3:
            recipient_windows[post_id] = timestamps
            return False
        timestamps.append(now.isoformat())
        recipient_windows[post_id] = timestamps
        return True

    def _reserve_mention_notification_slot(self, recipient_uid: str, now: datetime) -> bool:
        """Reserve a bounded, recipient-level rolling window for mention alerts.

        Mentions are independent from reply alerts: a busy post must not use up
        the three-per-post reply allowance merely by tagging somebody.  The
        global recipient window prevents a malicious author from producing an
        unbounded number of browser/in-app alerts across many posts.
        """
        cutoff = now - timedelta(hours=1)
        timestamps = [
            timestamp
            for timestamp in self.notification_mention_windows.get(recipient_uid, [])
            if (parsed := self._notification_time(timestamp)) is not None and parsed >= cutoff
        ]
        if len(timestamps) >= MENTION_NOTIFICATION_LIMIT_PER_HOUR:
            self.notification_mention_windows[recipient_uid] = timestamps
            return False
        timestamps.append(now.isoformat())
        self.notification_mention_windows[recipient_uid] = timestamps
        return True

    def _mentioned_user_ids(self, text: str, actor_uid: str) -> List[str]:
        """Resolve each valid ``@username`` in text to one distinct user ID.

        This helper is called while the database lock is held.  It only looks
        up canonical usernames in the in-memory index, never trusts a client
        supplied UID, and suppresses self-mentions.  Unknown handles are
        intentionally harmless plain text.
        """
        recipient_uids: List[str] = []
        seen_handles: Set[str] = set()
        seen_uids: Set[str] = set()
        # Do not turn a handle-like value in a link query/path into an alert.
        # Keeping ranges is more robust than trying to encode every URL shape
        # into the mention lookbehind expression.
        url_ranges = [(match.start(), match.end()) for match in MENTION_URL_RE.finditer(text or "")]
        for match in MENTION_RE.finditer(text or ""):
            if any(start <= match.start() < end for start, end in url_ranges):
                continue
            handle = match.group(1).lower()
            if handle in seen_handles:
                continue
            seen_handles.add(handle)
            recipient_uid = self.usernames.get(handle)
            if not recipient_uid or recipient_uid == actor_uid or recipient_uid in seen_uids:
                continue
            seen_uids.add(recipient_uid)
            recipient_uids.append(recipient_uid)
        return recipient_uids

    @staticmethod
    def _notification_excerpt(text: str, limit: int = 220) -> str:
        compact_text = " ".join(str(text or "").split())
        if len(compact_text) > limit:
            return f"{compact_text[:limit - 1].rstrip()}…"
        return compact_text

    def _create_mention_notifications_locked(
        self,
        *,
        text: str,
        actor_uid: str,
        actor_name: str,
        actor_avatar: Optional[str],
        post_id: str,
        comment_id: Optional[str],
        parent_comment_id: Optional[str],
        title: str,
        context_label: str,
    ) -> List[Dict[str, Any]]:
        """Persist one safe, deduplicated mention alert per resolved recipient.

        The caller must hold ``self._lock``.  A single person tagged repeatedly
        in the same content receives exactly one notification.
        """
        recipients = self._mentioned_user_ids(text, actor_uid)
        if not recipients:
            return []

        now = datetime.now(timezone.utc)
        excerpt = self._notification_excerpt(text)
        body = (
            f"{actor_name} mentioned you: {excerpt}"
            if excerpt
            else f"{actor_name} mentioned you in {context_label}."
        )
        created: List[Dict[str, Any]] = []
        for recipient_uid in recipients:
            if not self._reserve_mention_notification_slot(recipient_uid, now):
                continue
            notification = {
                "id": f"ntf-{uuid.uuid4().hex[:12]}",
                "type": "mention",
                "recipient_uid": recipient_uid,
                "actor_uid": actor_uid,
                "actor_name": actor_name,
                "actor_avatar": actor_avatar,
                "post_id": post_id,
                "comment_id": comment_id,
                "parent_comment_id": parent_comment_id,
                "title": title,
                "body": body,
                "created_at": now.isoformat(),
                "read_at": None,
                "is_read": False,
            }
            notifications = self.notifications.setdefault(recipient_uid, [])
            notifications.insert(0, notification)
            # Bound each recipient's persisted notification history.  Rolling
            # rate-limit state is intentionally stored separately above.
            if len(notifications) > 500:
                del notifications[500:]
            created.append(notification)

        if created:
            self._save()
        return created

    def create_post_mention_notifications(self, post_id: str) -> List[Dict[str, Any]]:
        """Create alerts for valid handles tagged in a newly published idea."""
        with self._lock:
            post = self.posts.get(post_id)
            if not post or post.get("deleted"):
                return []
            actor_uid = post.get("author_id", "")
            actor = self.users.get(actor_uid, {})
            actor_name = post.get("author_name") or actor.get("display_name") or "A member"
            actor_avatar = actor.get("avatar_url") or actor.get("photo_url") or post.get("author_photo")
            # A handle can be intentionally placed in either the idea title or
            # body, but the recipient gets at most one alert for this post.
            mention_text = f"{post.get('title', '')}\n{post.get('text', '')}"
            return self._create_mention_notifications_locked(
                text=mention_text,
                actor_uid=actor_uid,
                actor_name=actor_name,
                actor_avatar=actor_avatar,
                post_id=post_id,
                comment_id=None,
                parent_comment_id=None,
                title="You were mentioned in an idea",
                context_label="an idea",
            )

    def create_comment_mention_notifications(self, comment_id: str) -> List[Dict[str, Any]]:
        """Create alerts for valid handles tagged in a newly published comment."""
        with self._lock:
            comment = self.comments.get(comment_id)
            if not comment:
                return []
            actor_uid = comment.get("author_id", "")
            actor = self.users.get(actor_uid, {})
            actor_name = comment.get("author_name") or actor.get("display_name") or "A member"
            actor_avatar = actor.get("avatar_url") or actor.get("photo_url") or comment.get("author_photo")
            return self._create_mention_notifications_locked(
                text=comment.get("text", ""),
                actor_uid=actor_uid,
                actor_name=actor_name,
                actor_avatar=actor_avatar,
                post_id=comment.get("post_id", ""),
                comment_id=comment_id,
                parent_comment_id=comment.get("parent_id"),
                title="You were mentioned in a response",
                context_label="a response",
            )

    def create_comment_notifications(
        self,
        comment_id: str,
        exclude_recipient_uids: Optional[Set[str]] = None,
    ) -> List[Dict[str, Any]]:
        """Create targeted alerts for a comment without notifying the author.

        A recipient can receive at most three activity notifications for the
        same post in any rolling hour.  A reply can alert both the idea owner
        and the parent-comment author when those are different people.  A
        recipient who already received a mention alert for this exact comment
        can be excluded to avoid duplicate alerts for one user action.
        """
        with self._lock:
            comment = self.comments.get(comment_id)
            if not comment:
                return []
            post = self.posts.get(comment.get("post_id", ""))
            if not post:
                return []

            actor_uid = comment.get("author_id", "")
            actor = self.users.get(actor_uid, {})
            actor_name = comment.get("author_name") or actor.get("display_name") or "A member"
            actor_avatar = actor.get("avatar_url") or actor.get("photo_url") or comment.get("author_photo")
            post_id = comment["post_id"]
            parent_id = comment.get("parent_id")
            compact_text = " ".join(str(comment.get("text", "")).split())
            if len(compact_text) > 220:
                compact_text = f"{compact_text[:219].rstrip()}…"
            body = f"{actor_name}: {compact_text}" if compact_text else f"{actor_name} joined the discussion."

            candidates: List[tuple[str, str, str]] = []
            post_owner_uid = post.get("author_id")
            if post_owner_uid and post_owner_uid != actor_uid:
                candidates.append((post_owner_uid, "post_comment", "New response to your idea"))

            parent = self.comments.get(parent_id) if parent_id else None
            parent_owner_uid = parent.get("author_id") if parent else None
            if parent_owner_uid and parent_owner_uid != actor_uid and parent_owner_uid != post_owner_uid:
                candidates.append((parent_owner_uid, "comment_reply", "New reply to your comment"))

            now = datetime.now(timezone.utc)
            created: List[Dict[str, Any]] = []
            for recipient_uid, notification_type, title in candidates:
                if recipient_uid in (exclude_recipient_uids or set()):
                    continue
                if not self._reserve_notification_slot(recipient_uid, post_id, now):
                    continue
                notification = {
                    "id": f"ntf-{uuid.uuid4().hex[:12]}",
                    "type": notification_type,
                    "recipient_uid": recipient_uid,
                    "actor_uid": actor_uid,
                    "actor_name": actor_name,
                    "actor_avatar": actor_avatar,
                    "post_id": post_id,
                    "comment_id": comment_id,
                    "parent_comment_id": parent_id,
                    "title": title,
                    "body": body,
                    "created_at": now.isoformat(),
                    "read_at": None,
                    "is_read": False,
                }
                notifications = self.notifications.setdefault(recipient_uid, [])
                notifications.insert(0, notification)
                # Retain a useful local history while bounding the JSON store.
                if len(notifications) > 500:
                    del notifications[500:]
                created.append(notification)

            if created:
                self._save()
            return created

    def get_notifications(
        self,
        uid: str,
        limit: int = 30,
        before_id: Optional[str] = None,
        unread_only: bool = False,
    ) -> Dict[str, Any]:
        """Return a stable, newest-first page of only this user's alerts."""
        with self._lock:
            entries = list(self.notifications.get(uid, []))
            if unread_only:
                entries = [item for item in entries if not item.get("is_read", False)]
            start = 0
            if before_id:
                for index, item in enumerate(entries):
                    if item.get("id") == before_id:
                        start = index + 1
                        break
            page = entries[start:start + limit]
            next_before_id = page[-1].get("id") if start + len(page) < len(entries) and page else None
            unread_count = sum(1 for item in self.notifications.get(uid, []) if not item.get("is_read", False))
            return {
                "notifications": [dict(item) for item in page],
                "unread_count": unread_count,
                "next_before_id": next_before_id,
            }

    def get_unread_notification_count(self, uid: str) -> int:
        with self._lock:
            return sum(1 for item in self.notifications.get(uid, []) if not item.get("is_read", False))

    def mark_notification_read(self, uid: str, notification_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            for item in self.notifications.get(uid, []):
                if item.get("id") == notification_id:
                    if not item.get("is_read", False):
                        item["is_read"] = True
                        item["read_at"] = datetime.now(timezone.utc).isoformat()
                        self._save()
                    return dict(item)
            return None

    def mark_all_notifications_read(self, uid: str) -> int:
        with self._lock:
            pending = [item for item in self.notifications.get(uid, []) if not item.get("is_read", False)]
            if not pending:
                return 0
            read_at = datetime.now(timezone.utc).isoformat()
            for item in pending:
                item["is_read"] = True
                item["read_at"] = read_at
            self._save()
            return len(pending)

    # ─── Events Logging ───────────────────────────────────────────────────────

    def log_event(self, event: UserEvent):
        with self._lock:
            ev_dict = event.model_dump()
            ev_dict["id"] = f"ev-{uuid.uuid4().hex[:8]}"
            self.events.append(ev_dict)
            if len(self.events) > 2000:
                self.events = self.events[-1000:]
            self._save()

    # ─── WebSocket Broadcast ─────────────────────────────────────────────────

    async def broadcast_ws(self, message: Dict[str, Any]):
        """Broadcast a JSON message to all active WebSocket connections."""
        import json as _json
        dead: List[Any] = []
        for ws in list(self.ws_connections):
            try:
                await ws.send_text(_json.dumps(message, ensure_ascii=False))
            except Exception:
                dead.append(ws)
        for ws in dead:
            try:
                self.ws_connections.remove(ws)
            except ValueError:
                pass

    async def broadcast_to_user(self, uid: str, message: Dict[str, Any]):
        """Send a private event (DM or notification) to a user's live sockets."""
        import json as _json

        with self._lock:
            sockets = list(self.ws_user_connections.get(uid, []))
        if not sockets:
            return
        encoded = _json.dumps(message, ensure_ascii=False)
        dead: List[Any] = []
        for websocket in sockets:
            try:
                await websocket.send_text(encoded)
            except Exception:
                dead.append(websocket)
        if dead:
            with self._lock:
                registered = self.ws_user_connections.get(uid, [])
                for websocket in dead:
                    try:
                        registered.remove(websocket)
                    except ValueError:
                        pass


    # ─── Delete / Update Post ─────────────────────────────────────────────────

    def delete_post(self, post_id: str, requesting_uid: str) -> Dict[str, Any]:
        """
        Permanently delete a post.
        Only the author can delete their own post.
        Cascades: removes reactions, comments, AI embedding, timeline fan-out entries.
        Returns {"success": True} or {"success": False, "error": "..."}.
        """
        with self._lock:
            post = self.posts.get(post_id)
            if not post:
                return {"success": False, "error": "Post not found"}
            if post["author_id"] != requesting_uid:
                return {"success": False, "error": "Not authorised — only the author can delete this post"}

            # Remove from posts dict
            del self.posts[post_id]

            # Remove reactions (all keys starting with post_id:)
            dead_keys = [k for k in self.reactions if k.startswith(f"{post_id}:")]
            for k in dead_keys:
                del self.reactions[k]

            # Remove comments for this post
            dead_comments = [cid for cid, c in self.comments.items() if c.get("post_id") == post_id]
            for cid in dead_comments:
                self.comments.pop(cid, None)
                self.comment_likes.pop(cid, None)

            # Remove from all hides sets
            for uid_hides in self.hides.values():
                uid_hides.discard(post_id)

            # Remove from all user timelines (fan-out reverse)
            for timeline in self.user_timelines.values():
                try:
                    timeline.remove(post_id)
                except ValueError:
                    pass

            # Decrement author's ideas_count
            author_data = self.users.get(requesting_uid, {})
            count = author_data.get("ideas_count", 1)
            author_data["ideas_count"] = max(0, count - 1)

            # Keep category counters consistent with the live content rather
            # than allowing deleted ideas to remain in Explore counts.
            for topic in set(post.get("topics", [])):
                category = self.categories.get(topic)
                if category:
                    category["posts_count"] = max(0, category.get("posts_count", 0) - 1)

            self._save()

        # These secondary indexes are intentionally updated after persistence
        # so disk/AI work never holds the core request lock.
        try:
            from app.ai.embedding_store import embedding_store
            from app.ai.vector_index import vector_index
            from app.ai.similarity import similarity_service
            embedding_store.remove(post_id)
            remaining_matrix, remaining_ids = embedding_store.get_all()
            vector_index.rebuild(remaining_matrix, remaining_ids)
            similarity_service.invalidate_cache()
        except Exception:
            pass

        try:
            from app.services.search_service import search_service
            search_service.remove_post(post_id)
        except Exception:
            pass

        return {"success": True, "deleted_post_id": post_id}

    def update_post(self, post_id: str, requesting_uid: str,
                    title: Optional[str] = None,
                    text: Optional[str] = None,
                    summary: Optional[str] = None,
                    topics: Optional[List[str]] = None,
                    image_urls: Optional[List[str]] = None) -> Optional[IdeaResponse]:
        """
        Edit a post. Only the author can update.
        Only provided (non-None) fields are changed.
        Re-queues AI embedding after edit.
        """
        with self._lock:
            post = self.posts.get(post_id)
            if not post:
                return None
            if post["author_id"] != requesting_uid:
                return None  # caller raises 403

            now_iso = datetime.utcnow().isoformat()
            if title is not None:
                post["title"] = title.strip()
            if text is not None:
                post["text"] = text
                # Auto-refresh summary if not explicitly set
                if summary is None:
                    post["summary"] = text[:140] + ("..." if len(text) > 140 else "")
            if summary is not None:
                post["summary"] = summary
            previous_topics = set(post.get("topics", []))
            if topics is not None:
                normalized_topics = [t.lower().strip() for t in topics if t and t.strip()]
                # Preserve request order while preventing duplicated tags from
                # skewing category counters and relevance scores.
                post["topics"] = list(dict.fromkeys(normalized_topics))
                current_topics = set(post["topics"])
                for topic in previous_topics - current_topics:
                    category = self.categories.get(topic)
                    if category:
                        category["posts_count"] = max(0, category.get("posts_count", 0) - 1)
                for topic in current_topics - previous_topics:
                    category = self.categories.get(topic)
                    if category:
                        category["posts_count"] = category.get("posts_count", 0) + 1
            if image_urls is not None:
                post["image_urls"] = image_urls[:4]          # enforce 4-image cap
                post["image_url"] = image_urls[0] if image_urls else None
            post["updated_at"] = now_iso

            self._save()
            post_for_index = dict(post)

        # Re-embed in background (non-blocking)
        try:
            from app.ai.background_worker import background_worker
            background_worker.embed_new_post(post_id, post_for_index)
        except Exception:
            pass

        try:
            from app.services.search_service import search_service
            search_service.index_new_post(post_for_index)
        except Exception:
            pass

        return self.get_post(post_id, requesting_uid)

    # ─── Delete Comment ───────────────────────────────────────────────────────

    def delete_comment(self, comment_id: str, requesting_uid: str) -> Dict[str, Any]:
        """
        Permanently delete a comment.
        Author or post-author can delete.
        Cascades: removes all child replies recursively.
        """
        with self._lock:
            comment = self.comments.get(comment_id)
            if not comment:
                return {"success": False, "error": "Comment not found"}

            post = self.posts.get(comment.get("post_id", ""), {})
            is_author = comment.get("author_id") == requesting_uid
            is_post_owner = post.get("author_id") == requesting_uid
            if not (is_author or is_post_owner):
                return {"success": False, "error": "Not authorised"}

            # Recursively collect all descendant comment IDs
            def _collect(cid):
                ids = [cid]
                for c in list(self.comments.values()):
                    if c.get("parent_id") == cid:
                        ids.extend(_collect(c["id"]))
                return ids

            to_delete = _collect(comment_id)
            for cid in to_delete:
                self.comments.pop(cid, None)

            # Decrement post comment count
            if comment.get("post_id") in self.posts:
                stats = self.posts[comment["post_id"]].get("stats", {})
                stats["comments"] = max(0, stats.get("comments", 0) - 1)

            self._save()
            return {"success": True, "deleted_count": len(to_delete)}

    # ─── Delete Account (full purge) ──────────────────────────────────────────

    def delete_user(self, uid: str) -> Dict[str, Any]:
        """
        Permanently delete a user account and ALL their data:
        - All posts (cascade: reactions, comments, timelines)
        - Username reservation
        - Follows / followers
        - Conversations & messages
        - Events
        - User record
        Returns {"success": True, "deleted_posts": N}
        """
        with self._lock:
            if uid not in self.users:
                return {"success": False, "error": "User not found"}

            # 1. Delete all their posts (and cascade within)
            user_post_ids = [
                pid for pid, p in list(self.posts.items())
                if p.get("author_id") == uid
            ]
            for pid in user_post_ids:
                # Remove reactions
                dead_rxn = [k for k in self.reactions if k.startswith(f"{pid}:")]
                for k in dead_rxn:
                    del self.reactions[k]
                # Remove comments
                dead_cmt = [cid for cid, c in self.comments.items() if c.get("post_id") == pid]
                for cid in dead_cmt:
                    self.comments.pop(cid, None)
                # Remove from timelines
                for tl in self.user_timelines.values():
                    try:
                        tl.remove(pid)
                    except ValueError:
                        pass
                del self.posts[pid]

            # 2. Release username
            udata = self.users.get(uid, {})
            uname = udata.get("username")
            if uname and uname in self.usernames:
                del self.usernames[uname]

            # 3. Remove follow graph edges
            following = set(self.follows.pop(uid, set()))
            self.followers.pop(uid, None)
            for followed_uid in following:
                self.followers.get(followed_uid, set()).discard(uid)
            # Remove uid from others' follows/followers
            for other_follows in self.follows.values():
                other_follows.discard(uid)
            for other_followers in self.followers.values():
                other_followers.discard(uid)

            # 4. Delete user's conversations & messages
            conv_ids = list(self.user_conversations.pop(uid, []))
            for conv_id in conv_ids:
                conv = self.conversations.pop(conv_id, None)
                if conv:
                    self.messages.pop(conv_id, None)
                    # Remove from other participant's inbox
                    for p_uid in conv.get("participants", []):
                        if p_uid != uid:
                            try:
                                self.user_conversations.get(p_uid, []).remove(conv_id)
                            except ValueError:
                                pass

            # 5. Clean user from hides / reactions
            self.hides.pop(uid, None)

            # 6. Clean user timeline
            self.user_timelines.pop(uid, None)

            # 7. Clean WS connections
            self.ws_user_connections.pop(uid, None)

            # 8. Delete user record
            del self.users[uid]

            self._save()
            return {"success": True, "uid": uid, "deleted_posts": len(user_post_ids)}


# Global singleton database instance
db = Database()
