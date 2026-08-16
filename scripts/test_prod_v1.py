"""
Production Readiness + Delete/Update API Test
Tests: delete post, edit post, delete comment, delete account,
       multi-image, avatar priority, global error handler, security headers
"""
import sys, io, json, urllib.request, urllib.error
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

BASE = "http://localhost:8000/api"
PASS, FAIL, WARN = "[PASS]", "[FAIL]", "[WARN]"
results = []

def req(method, path, data=None, headers=None, timeout=15):
    try:
        body = json.dumps(data).encode() if data else None
        h = {"Content-Type": "application/json"} if body else {}
        if headers:
            h.update(headers)
        r = urllib.request.Request(BASE + path, data=body, headers=h, method=method)
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            raw = resp.read()
            resp_headers = dict(resp.headers)
            return json.loads(raw), None, resp.status, resp_headers
    except urllib.error.HTTPError as e:
        try:
            detail = json.loads(e.read()).get("detail", str(e))
        except Exception:
            detail = str(e)
        return None, f"HTTP {e.code}: {detail}", e.code, {}
    except Exception as e:
        return None, str(e), 0, {}

def get(p, **kw):    return req("GET", p, **kw)
def post(p, d=None, **kw): return req("POST", p, d, **kw)
def patch(p, d=None, **kw): return req("PATCH", p, d, **kw)
def delete(p, **kw): return req("DELETE", p, **kw)

# Mock auth headers (dev token)
AUTH = {"Authorization": "Bearer mock-test-user-prod-1"}

def check(label, ok, val="", warn=False):
    tag = PASS if ok else (WARN if warn else FAIL)
    results.append((tag, label, str(val)[:90]))
    print(f"  {tag} {label}: {str(val)[:90]}")

def section(title):
    print(f"\n[{title}]")

print("=" * 65)
print("  Pulse Production Readiness Test v2")
print("=" * 65)

# ─── 1. Server + Security Headers ────────────────────────────────────────────
section("1  Server + Security Headers")
d, e, code, hdr = get("/health")
check("Server healthy", d is not None, d.get("status","") if d else e)
check("X-Response-Time header present", "x-response-time" in {k.lower(): v for k,v in hdr.items()},
      hdr.get("X-Response-Time", "MISSING"))
check("X-Content-Type-Options header", "x-content-type-options" in {k.lower(): v for k,v in hdr.items()},
      hdr.get("X-Content-Type-Options", "MISSING"))

if d:
    check("messages_count field in health", "messages_count" in d, d.get("messages_count"))
    check("conversations_count field", "conversations_count" in d, d.get("conversations_count"))
    check("ai_vector_index_size field", "ai_vector_index_size" in d, d.get("ai_vector_index_size"))

# ─── 2. Global Error Handler (no 500 crash) ──────────────────────────────────
section("2  Global Error Handlers (no crashes)")
# Bad route
d, e, code, _ = get("/totally-nonexistent-endpoint-xyz")
check("404 returns JSON not crash", code == 404 or (e and "404" in e), f"code={code}")

# Validation error
d, e, code, _ = post("/posts", {"title": "x"})  # missing required fields
check("422 validation error is JSON", code == 422 or (e and "422" in e),
      f"code={code} {str(e)[:40]}")

# ─── 3. Create Post (multi-image) ─────────────────────────────────────────────
section("3  Post Create with Multi-Image")
d, e, code, _ = post("/posts", {
    "title": "Test Post for Prod Test",
    "text": "This is a test post created during production readiness testing. Full emoji 🚀💡🔥.",
    "topics": ["testing", "ai"],
    "summary": "A test post",
    "image_urls": [
        "https://picsum.photos/800/600?1",
        "https://picsum.photos/800/600?2",
        "https://picsum.photos/800/600?3",
        "https://picsum.photos/800/600?4",
    ]
}, headers=AUTH)
check("Create post with 4 images succeeds", d is not None, f"id={d.get('id','')}" if d else e)
if d:
    test_post_id = d["id"]
    check("image_urls returns 4 items", len(d.get("image_urls", [])) == 4,
          f"{len(d.get('image_urls', []))} images")
    check("image_url = first of image_urls", d.get("image_url") == d.get("image_urls", [None])[0],
          d.get("image_url"))
    check("is_own_post = True for author", d.get("is_own_post") == True, d.get("is_own_post"))
else:
    test_post_id = "post-robotics-01"  # fallback to seed post

# ─── 4. Edit Post ─────────────────────────────────────────────────────────────
section("4  Edit Post (PATCH)")
d, e, code, _ = patch(f"/posts/{test_post_id}", {
    "title": "Updated: Test Post for Prod Test",
    "image_urls": ["https://picsum.photos/800/600?99"],
}, headers=AUTH)
check("Edit post title succeeds", d is not None, f"title={d.get('title','')[:40]}" if d else e)
if d:
    check("Title was updated", "Updated:" in d.get("title",""), d.get("title","")[:40])
    check("image_urls updated to 1 item", len(d.get("image_urls",[])) == 1,
          len(d.get("image_urls",[])))
    check("updated_at changed", d.get("updated_at") != d.get("created_at"), "timestamps differ")

# Editing someone else's post → 403
d2, e2, code2, _ = patch("/posts/post-robotics-01", {"title": "Hacked!"}, headers=AUTH)
check("Edit other's post → 403/404", code2 in (403, 404), f"code={code2}")

# ─── 5. Delete Comment ────────────────────────────────────────────────────────
section("5  Delete Comment")
# First add a comment
d, e, code, _ = post("/posts/post-robotics-01/comments", {
    "text": "Test comment to be deleted soon",
    "comment_type": "general"
}, headers=AUTH)
if d and d.get("id"):
    cid = d["id"]
    check("Comment created for delete test", True, cid)

    d2, e2, code2, _ = delete(f"/posts/comments/{cid}", headers=AUTH)
    check("Delete own comment succeeds", d2 is not None and d2.get("success"),
          f"deleted_count={d2.get('deleted_count',0)}" if d2 else e2)

    # Verify it's gone
    d3, e3, code3, _ = get("/posts/post-robotics-01/comments")
    deleted_ids = [c.get("id") for c in (d3 or [])]
    check("Comment removed from list", cid not in deleted_ids, "removed" if cid not in deleted_ids else "STILL THERE")
else:
    check("Comment created for delete test", False, e or "no id")

# ─── 6. Delete Post ───────────────────────────────────────────────────────────
section("6  Delete Post (permanent)")
if test_post_id != "post-robotics-01":
    d, e, code, _ = delete(f"/posts/{test_post_id}", headers=AUTH)
    check("Delete own post → success", d is not None and d.get("success"),
          f"deleted={d.get('deleted_post_id','')}" if d else e)

    # Verify it's gone from feed
    d2, e2, _, _ = get(f"/posts/{test_post_id}")
    check("Post 404 after delete", e2 is not None and "404" in e2, e2 or "STILL EXISTS")
else:
    check("Delete post test (skipped — no own post created)", True, "SKIP", warn=True)

# Try deleting seed post (not yours) → 403
d, e, code, _ = delete("/posts/post-robotics-01", headers=AUTH)
check("Delete other's post → 403", code == 403, f"code={code}")

# ─── 7. User Full Profile (avatar priority) ───────────────────────────────────
section("7  Avatar Priority & User Info")
d, e, code, _ = get("/users/me", headers=AUTH)
check("GET /me returns profile", d is not None, f"uid={d.get('uid','')}" if d else e)
if d:
    # Google photo should be set (from mock token)
    check("photo_url is set", bool(d.get("photo_url")), d.get("photo_url","MISSING"))
    # avatar_url starts as None for new user
    check("avatar_url starts None (user can change)", d.get("avatar_url") is None,
          str(d.get("avatar_url")))

# ─── 8. Delete Account (soft test — don't delete the main test user) ──────────
section("8  Delete Account API")
# Create a throwaway user
THROW_AUTH = {"Authorization": "Bearer mock-throwaway-del-test-99"}
d, e, code, _ = get("/users/me", headers=THROW_AUTH)
if d:
    throwaway_uid = d.get("uid")
    check("Throwaway user created", True, throwaway_uid)

    # Delete it
    d2, e2, code2, _ = delete("/users/me", headers=THROW_AUTH)
    check("Delete own account → success", d2 is not None and d2.get("success"),
          f"uid={d2.get('uid','')}" if d2 else e2)

    # Verify they can't login again (user gone from DB)
    d3, e3, code3, _ = get(f"/users/{throwaway_uid}/full")
    check("Deleted user returns 404", code3 == 404 or (e3 and "404" in e3), e3 or "STILL EXISTS")
else:
    check("Throwaway user setup", False, e)

# ─── 9. Config Limits Reflected ───────────────────────────────────────────────
section("9  Content Limits")
# Username 30 chars (31 should fail)
d, e, code, _ = get("/users/check-username?username=" + "a" * 31)
check("Username 31 chars → invalid", d is not None and not d.get("valid"), str(d))

d, e, code, _ = get("/users/check-username?username=valid_name_ok")
check("Username 14 chars → valid format", d is not None and d.get("valid"), str(d))

# Post text > 50000 chars (should fail validation)
d, e, code, _ = post("/posts", {
    "title": "X" * 5,
    "text": "A" * 55_000,
    "topics": ["test"]
}, headers=AUTH)
check("Post text 55k chars → 422", code == 422 or (e and "422" in e), f"code={code}")

# 5 images should fail (max 4)
d, e, code, _ = post("/posts", {
    "title": "Too many images",
    "text": "Test text for image limit check.",
    "topics": ["test"],
    "image_urls": ["https://x.com/1","https://x.com/2","https://x.com/3","https://x.com/4","https://x.com/5"]
}, headers=AUTH)
check("5 images → 422 validation error", code == 422 or (e and "422" in e), f"code={code}")

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
