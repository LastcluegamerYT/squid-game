import sys, io, time, urllib.request, json
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

def get(url):
    try:
        with urllib.request.urlopen(url, timeout=10) as r:
            return json.loads(r.read())
    except Exception:
        return {}

# Poll every 15s for up to 3 minutes
for attempt in range(12):
    time.sleep(15)
    ai = get('http://localhost:8000/api/ai/admin/status')
    em = ai.get('embedding_model', {})
    vi = ai.get('vector_index', {})
    es = ai.get('embedding_store', {})
    bw = ai.get('background_worker', {})
    elapsed = (attempt + 1) * 15
    avail   = em.get('available')
    ix_size = vi.get('size', 0)
    n_emb   = es.get('total_embeddings', 0)
    j_done  = bw.get('jobs_completed', 0)
    j_fail  = bw.get('jobs_failed', 0)
    err     = em.get('load_error')
    print(str(elapsed) + "s | model=" + str(avail) + " index=" + str(ix_size) + " embeddings=" + str(n_emb) + " jobs_done=" + str(j_done) + " jobs_failed=" + str(j_fail))
    if err:
        print("  load_error: " + str(err))
    if avail and ix_size > 0:
        print("SUCCESS - model loaded and index populated!")
        break
print("Done polling")
