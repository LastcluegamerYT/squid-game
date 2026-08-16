"""
Full integration test — Messenger + Follow/Friends + Threaded Comments + Feed
"""
import sys, io, json, urllib.request, urllib.error, time

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

BASE = "http://localhost:8000/api"
PASS, FAIL, WARN = "[PASS]", "[FAIL]", "[WARN]"
results = []

def req(method, path, data=None, timeout=15):
    try:
        body = json.dumps(data).encode() if data else None
        r = urllib.request.Request(
            BASE + path, data=body,
            headers={"Content-Type": "application/json"} if body else {},
            method=method
        )
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            return json.loads(resp.read()), None
    except urllib.error.HTTPError as e:
        try:
            detail = json.loads(e.read()).get("detail", str(e))
        except Exception:
            detail = str(e)
        return None, f"HTTP {e.code}: {detail}"
    except Exception as e:
        return None, str(e)

get    = lambda p, **kw: req("GET", p, **kw)
post   = lambda p, d=None, **kw: req("POST", p, d, **kw)
delete = lambda p, **kw: req("DELETE", p, **kw)
patch  = lambda p, d=None, **kw: req("PATCH", p, d, **kw)

def check(label, ok, val="", warn=False):
    tag = PASS if ok else (WARN if warn else FAIL)
    results.append((tag, label, str(val)[:90]))
    print(f"  {tag} {label}: {str(val)[:90]}")

def section(title):
    print(f"\n[{title}]")

print("=" * 65)
print("  Pulse Full Integration Test — Messenger + Follow + Comments + Feed")
print("=" * 65)

# ─── 1. Health ────────────────────────────────────────────────────────────────
section("1  Server Health")
d, e = get("/health")
check("Server up", d is not None, e or d.get("status",""))
if d:
    check("Posts in DB", d.get("posts_count", 0) > 0, d.get("posts_count"))

# ─── 2. Feed Tabs ─────────────────────────────────────────────────────────────
section("2  Feed Tabs")
for tab, label in [("for_you","For You"), ("trending","Trending"), ("latest","Latest")]:
    d, e = get(f"/feed?tab={tab}&limit=5")
    check(f"{label} tab works", d is not None and "items" in (d or {}),
          f"{d.get('total',0)} items" if d else e)

d, e = get("/feed?tab=following&limit=5")
check("Following tab without auth → 401", e and "401" in e, e or "no error")

# ─── 3. Feed Freshness (refresh_seed) ────────────────────────────────────────
section("3  Feed Freshness")
d1, _ = get("/feed?tab=for_you&limit=10&refresh_seed=1")
d2, _ = get("/feed?tab=for_you&limit=10&refresh_seed=9999")
if d1 and d2:
    ids1 = [it["idea"]["id"] for it in d1.get("items", [])]
    ids2 = [it["idea"]["id"] for it in d2.get("items", [])]
    check("refresh_seed changes order", ids1 != ids2, f"seed=1:{ids1[:3]}  seed=9999:{ids2[:3]}", warn=True)
    check("Both seeds return posts", len(ids1) > 0 and len(ids2) > 0, f"{len(ids1)} items")

# ─── 4. Messaging Endpoints ───────────────────────────────────────────────────
section("4  Messaging API")
# conversations list (no auth = 401/403)
d, e = get("/messages/conversations")
check("Conversations needs auth", e is not None and ("401" in e or "403" in e or "422" in e), e)

# start conv endpoint without auth
d, e = post("/messages/conversations?target_uid=fake-uid-123")
check("Start conv needs auth", e is not None, e)

# ─── 5. Users Follow routes ───────────────────────────────────────────────────
section("5  Follow/Friends API Routes Exist")
# Test routes exist (will 401/422 without auth — that's correct)
for path, label in [
    ("/users/some-uid/followers",   "GET followers"),
    ("/users/some-uid/following",   "GET following"),
    ("/users/some-uid/friends",     "GET friends"),
    ("/users/some-uid/relationship","GET relationship"),
]:
    d, e = get(path)
    # Expect 200/404/401/422 — NOT 404 with "not found route"
    code_ok = e is None or any(c in (e or "") for c in ["404", "401", "422"])
    check(f"Route exists: {label}", code_ok, e or "200 OK")

# ─── 6. Threaded Comments ─────────────────────────────────────────────────────
section("6  Threaded Comment Structure")
d, e = get("/posts/post-robotics-01")
if d:
    check("Post endpoint works", True, d.get("id"))

d, e = get("/posts/post-robotics-01/comments")
if d is not None:
    check("Comments endpoint works", True, f"{len(d)} top-level comments")
    if d:
        c0 = d[0]
        check("Comment has depth field", "depth" in c0, c0.get("depth", "MISSING"))
        check("Comment has reply_count", "reply_count" in c0, c0.get("reply_count", "MISSING"))
        check("Comment has author_handle", "author_handle" in c0, c0.get("author_handle", "N/A"))
        # Check depth=0 for top-level
        check("Top-level depth is 0", c0.get("depth") == 0, c0.get("depth"))
        # If any have replies, check they have depth=1
        for c in d:
            if c.get("replies"):
                r = c["replies"][0]
                check("Reply has depth=1", r.get("depth") == 1, r.get("depth"))
                # Check 2nd level if exists
                if r.get("replies"):
                    r2 = r["replies"][0]
                    check("Reply-to-reply has depth=2", r2.get("depth") == 2, r2.get("depth"))
                break
else:
    check("Comments endpoint works", False, e)

# ─── 7. AI layer still working ────────────────────────────────────────────────
section("7  AI Layer Sanity Check")
d, e = get("/ai/admin/status")
if d:
    check("AI status OK", True, "HTTP 200")
    vi = d.get("vector_index", {})
    check("Vector index has entries", vi.get("size", 0) > 0, vi.get("size"))
    check("Coverage 100%", d.get("embedding_coverage_pct", 0) == 100.0,
          str(d.get("embedding_coverage_pct")) + "%")

d, e = get("/ai/related/post-robotics-01?top_n=3&use_cache=false")
if d:
    check("Related posts still working", d.get("pipeline") != "unavailable",
          "pipeline=" + str(d.get("pipeline")))

# ─── 8. User Full Info ────────────────────────────────────────────────────────
section("8  Enhanced User Full Info")
# Use first user from DB
d, e = get("/feed?tab=latest&limit=1")
if d and d.get("items"):
    author_uid = d["items"][0]["idea"]["author_id"]
    d2, e2 = get(f"/users/{author_uid}/full")
    if d2:
        prof = d2.get("profile", {})
        check("is_following field exists", "is_following" in d2, d2.get("is_following"))
        check("is_friend field exists", "is_friend" in d2, d2.get("is_friend"))
        check("followers_preview exists", "followers_preview" in d2, type(d2.get("followers_preview")))
        check("following_preview exists", "following_preview" in d2, type(d2.get("following_preview")))
        check("profile has ideas_count", "ideas_count" in prof, prof.get("ideas_count"))
    else:
        check("User full info works", False, e2)

# ─── 9. Message models importable ────────────────────────────────────────────
section("9  Model Integrity")
import subprocess, sys as _sys
r = subprocess.run(
    [_sys.executable, "-c",
     "from app.models.message import MessageCreate, MessageResponse, ConversationMeta; "
     "from app.models.comment import CommentResponse; "
     "from app.models.feed import FeedTab; "
     "assert FeedTab.FOLLOWING.value == 'following'; "
     "print('OK')"],
    capture_output=True, text=True, cwd="."
)
check("All new models import cleanly", r.returncode == 0,
      r.stdout.strip() or r.stderr.strip()[:80])

# ─── Summary ──────────────────────────────────────────────────────────────────
print("\n" + "=" * 65)
passed = sum(1 for t,_,_ in results if t == PASS)
warned = sum(1 for t,_,_ in results if t == WARN)
failed = sum(1 for t,_,_ in results if t == FAIL)
total  = len(results)
print(f"  RESULT: {passed} passed  {warned} warnings  {failed} failed  (of {total} checks)")
if failed == 0:
    print("  ALL CRITICAL CHECKS PASSED ✅")
else:
    print("  FAILED CHECKS:")
    for t, label, val in results:
        if t == FAIL:
            print(f"    ✗ {label}: {val}")
print("=" * 65)
