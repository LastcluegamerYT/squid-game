"""
Full AI integration test — runs all checks and prints a clear pass/fail report.
"""
import sys, io, time, urllib.request, json, os

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

BASE = 'http://localhost:8000/api'
PASS = '[PASS]'
FAIL = '[FAIL]'
WARN = '[WARN]'

results = []

def get(path, timeout=20):
    try:
        with urllib.request.urlopen(BASE + path, timeout=timeout) as r:
            return json.loads(r.read()), None
    except Exception as e:
        return None, str(e)

def post_req(path, data, timeout=20):
    try:
        body = json.dumps(data).encode()
        req = urllib.request.Request(BASE + path, data=body,
              headers={'Content-Type': 'application/json'}, method='POST')
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read()), None
    except Exception as e:
        return None, str(e)

def check(label, condition, value='', warn=False):
    tag = PASS if condition else (WARN if warn else FAIL)
    results.append((tag, label, str(value)[:80]))
    print(f"  {tag} {label}: {str(value)[:80]}")

print("=" * 60)
print("  Pulse AI Integration Test")
print("=" * 60)

# ── 1. Health ──────────────────────────────────────────────────
print("\n[1] Server Health")
d, e = get('/health')
check("Server running", d is not None, e or d.get('status'))
if d:
    check("Posts in DB", (d.get('posts_count', 0) > 0), d.get('posts_count'))

# ── 2. AI Admin Status ─────────────────────────────────────────
print("\n[2] AI System Status")
d, e = get('/ai/admin/status')
if d:
    em = d.get('embedding_model', {})
    vi = d.get('vector_index', {})
    es = d.get('embedding_store', {})
    bw = d.get('background_worker', {})
    check("Admin status endpoint", True, "HTTP 200")
    check("Embedding model available", em.get('available'), em.get('name'))
    check("No load_error", not em.get('load_error'), em.get('load_error') or 'none')
    check("Vector index has entries", vi.get('size', 0) > 0,
          "mode=" + str(vi.get('mode')) + " size=" + str(vi.get('size')))
    check("Embeddings stored", es.get('total_embeddings', 0) > 0, es.get('total_embeddings'))
    check("Coverage 100%", d.get('embedding_coverage_pct', 0) == 100.0,
          str(d.get('embedding_coverage_pct')) + "%")
    check("Worker completed jobs", bw.get('jobs_completed', 0) > 0,
          "submitted=" + str(bw.get('jobs_submitted')) + " completed=" + str(bw.get('jobs_completed')), warn=True)
else:
    check("Admin status endpoint", False, e)

# ── 3. Quality Scan ────────────────────────────────────────────
print("\n[3] Quality Scoring")
d, e = get('/ai/admin/quality-scan?limit=10')
if d:
    posts = d.get('posts', [])
    check("Quality scan works", True, str(d.get('total')) + " posts scored")
    if posts:
        scores = [p['quality_score'] for p in posts]
        check("All scores in [0,1]", all(0 <= s <= 1 for s in scores),
              "min=" + str(min(scores)) + " max=" + str(max(scores)))
        check("Scores vary (not all same)", len(set(scores)) > 1, str(len(set(scores))) + " distinct scores")
else:
    check("Quality scan works", False, e)

# ── 4. Similar Check ───────────────────────────────────────────
print("\n[4] Duplicate Detection")
path = '/ai/similar-check?title=AI+robotics+autonomous+systems&text=Autonomous+robotic+systems+powered+by+artificial+intelligence+are+going+to+transform+manufacturing+and+logistics'
d, e = get(path)
if d:
    check("Similar check works", True, "is_near_duplicate=" + str(d.get('is_near_duplicate')))
    check("max_score is float", isinstance(d.get('max_score'), float), d.get('max_score'))
else:
    check("Similar check works", False, e)

# ── 5. Related Posts ───────────────────────────────────────────
print("\n[5] Related Posts (needs model loaded)")
d, e = get('/ai/related/post-robotics-01?top_n=5&use_cache=false')
if d:
    pipeline = d.get('pipeline')
    related  = d.get('related_posts', [])
    candidates = d.get('total_candidates', 0)
    check("Related endpoint works", True, "pipeline=" + str(pipeline))
    check("Pipeline is NOT unavailable", pipeline != "unavailable", pipeline, warn=True)
    check("Has candidates", candidates > 0, str(candidates) + " candidates", warn=True)
    check("Has related posts", len(related) > 0, str(len(related)) + " related", warn=True)
    if related:
        r = related[0]
        check("Related has score", 'similarity_score' in r, r.get('similarity_score'))
        check("Related has relationship label", 'relationship' in r, r.get('relationship'))
else:
    check("Related endpoint works", False, e)

# ── 6. User Feedback ───────────────────────────────────────────
print("\n[6] User Feedback & Profile")
d, e = post_req('/ai/user-profile/feedback', {
    "uid": "test-user-001",
    "post_id": "post-ai-01",
    "signal_type": "like",
    "context_post_ids": ["post-robotics-01", "post-design-01"]
})
check("Feedback recording works", d is not None and d.get('status') == 'recorded',
      d.get('status') if d else e)

d, e = get('/ai/user-profile/test-user-001')
if d:
    check("User profile endpoint works", True, "interactions=" + str(d.get('interaction_count')))
else:
    check("User profile endpoint works", False, e)

# ── 7. Clusters ────────────────────────────────────────────────
print("\n[7] Clustering")
d, e = get('/ai/clusters')
if d:
    check("Clusters endpoint works", True, str(d.get('total')) + " clusters")
else:
    check("Clusters endpoint works", False, e)

# ── 8. Feed with AI Scoring ───────────────────────────────────
print("\n[8] Feed with AI Ranker")
d, e = get('/feed?tab=for_you&offset=0&limit=10')
if d:
    items = d.get('items', [])
    check("Feed works", True, str(d.get('total')) + " total items")
    if items:
        first = items[0].get('idea', {})
        has_ai_score = first.get('ai_feed_score') is not None
        check("Feed has AI scores", has_ai_score, str(first.get('ai_feed_score')), warn=True)
        reasons = [it.get('recommendation_reason', '') for it in items]
        check("Feed has recommendation reasons", all(reasons), reasons[0][:50] if reasons else "")
else:
    check("Feed works", False, e)

# ── 9. Feedback Stats ─────────────────────────────────────────
print("\n[9] Feedback Pipeline Stats")
d, e = get('/ai/admin/feedback-stats')
if d:
    check("Feedback stats works", True, "total_pairs=" + str(d.get('total_pairs')))
    check("Feedback recorded", d.get('total_pairs', 0) > 0, d.get('total_pairs'))
else:
    check("Feedback stats works", False, e)

# ── Summary ───────────────────────────────────────────────────
print("\n" + "=" * 60)
passed = sum(1 for t, _, _ in results if t == PASS)
warned = sum(1 for t, _, _ in results if t == WARN)
failed = sum(1 for t, _, _ in results if t == FAIL)
total  = len(results)
print(f"  RESULT: {passed} passed, {warned} warnings, {failed} failed  (of {total} checks)")
if failed == 0:
    print("  ALL CRITICAL CHECKS PASSED")
else:
    print("  FAILED CHECKS:")
    for t, label, val in results:
        if t == FAIL:
            print("    - " + label + ": " + val)
print("=" * 60)
