import urllib.request, json

def get(url):
    try:
        with urllib.request.urlopen(url, timeout=10) as r:
            return json.loads(r.read())
    except Exception as e:
        return {'error': str(e)}

h = get('http://localhost:8000/api/health')
print('HEALTH:', h.get('status'), '| posts:', h.get('posts_count'))

ai = get('http://localhost:8000/api/ai/admin/status')
print('AI STATUS:')
print('  platform posts:', ai.get('platform', {}).get('total_posts'))
print('  embeddings:', ai.get('embedding_store', {}).get('total_embeddings'))
print('  model_available:', ai.get('embedding_model', {}).get('available'))
print('  reranker_available:', ai.get('reranker_model', {}).get('available'))
vi = ai.get('vector_index', {})
print('  vector_index mode:', vi.get('mode'), 'size:', vi.get('size'))
bw = ai.get('background_worker', {})
print('  worker jobs submitted:', bw.get('jobs_submitted'), 'completed:', bw.get('jobs_completed'))

q = get('http://localhost:8000/api/ai/admin/quality-scan?limit=5')
posts = q.get('posts', [])
print('QUALITY SCAN top 5 worst:')
for p in posts:
    print('  ' + p['post_id'] + ': ' + str(p['quality_score']) + ' | ' + str(p['title'])[:50])

cl = get('http://localhost:8000/api/ai/clusters')
print('CLUSTERS:', cl.get('total'), 'clusters')

fb = get('http://localhost:8000/api/ai/admin/feedback-stats')
print('FEEDBACK:', fb)

# Related posts for first available post
posts_list = get('http://localhost:8000/api/ai/admin/quality-scan?limit=1')
pids = [p['post_id'] for p in posts_list.get('posts', [])]
if pids:
    rel = get('http://localhost:8000/api/ai/related/' + pids[0] + '?top_n=5')
    print('RELATED for', pids[0], ':', rel.get('pipeline'), '| related:', len(rel.get('related_posts', [])))
