"""
benchmark.py — AI system evaluation with synthetic 1000-post dataset.

Measures:
  Precision@5, Precision@10, Recall@20, MRR, nDCG
  for related-post retrieval across 4 systems:
    1. Random baseline
    2. Keyword (BM25) baseline
    3. Embedding-only
    4. Embedding + Reranker + MMR

Run:
    python scripts/benchmark.py
    python scripts/benchmark.py --posts 1000 --queries 50
"""
import sys
import os
import time
import math
import random
import argparse

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))


# ─── Synthetic Post Generator ────────────────────────────────────────────────

TOPICS = [
    ("AI", ["artificial intelligence", "machine learning", "neural networks", "deep learning", "AI ethics"]),
    ("Robotics", ["robots", "autonomous systems", "mechatronics", "robotic arms", "drone technology"]),
    ("Philosophy", ["consciousness", "ethics", "epistemology", "free will", "metaphysics"]),
    ("Science", ["quantum physics", "biology", "research", "discovery", "experiments"]),
    ("Technology", ["software", "hardware", "innovation", "digital transformation", "computing"]),
    ("Society", ["culture", "social change", "community", "human rights", "democracy"]),
    ("Environment", ["climate change", "sustainability", "renewable energy", "ecology", "conservation"]),
    ("Health", ["medicine", "mental health", "nutrition", "wellness", "healthcare"]),
    ("Education", ["learning", "schools", "knowledge", "pedagogy", "teaching"]),
    ("Economics", ["markets", "finance", "trade", "inequality", "growth"]),
]

IDEA_TEMPLATES = [
    "{subject} will fundamentally reshape {field} within the next decade.",
    "We should rethink how {subject} interacts with {field} in modern society.",
    "The relationship between {subject} and {field} is more complex than we think.",
    "{subject} can solve the biggest challenges facing {field} today.",
    "Open {subject} is the key to democratizing {field} for everyone.",
    "Without regulating {subject}, {field} will face serious risks.",
    "{subject} and {field} must collaborate for true progress.",
    "The future of {field} depends on how we develop {subject}.",
    "Current approaches to {subject} are holding back {field}.",
    "Combining {subject} with {field} creates unprecedented opportunities.",
]


def gen_posts(n: int) -> dict:
    posts = {}
    for i in range(n):
        topic_cat, keywords = random.choice(TOPICS)
        subject = random.choice(keywords)
        topic_cat2, keywords2 = random.choice(TOPICS)
        field = random.choice(keywords2)
        template = random.choice(IDEA_TEMPLATES)
        title = template.format(subject=subject.title(), field=field)
        body_sentences = []
        for _ in range(random.randint(2, 6)):
            kw = random.choice(keywords + keywords2)
            body_sentences.append(f"This relates deeply to {kw} and its implications for society.")
        body = " ".join(body_sentences)
        pid = f"bench-{i:04d}"
        posts[pid] = {
            "id": pid,
            "title": title,
            "text": body,
            "summary": title,
            "topics": [topic_cat.lower(), topic_cat2.lower()],
            "author_id": f"user-{i % 50}",
            "author_name": f"User {i % 50}",
            "stats": {
                "likes": random.randint(0, 100),
                "fires": random.randint(0, 50),
                "bulbs": random.randint(0, 50),
                "comments": random.randint(0, 30),
                "shares": random.randint(0, 20),
                "views": random.randint(0, 500),
            },
            "created_at": "2024-01-01T00:00:00",
        }
    return posts


# ─── Evaluation Metrics ───────────────────────────────────────────────────────

def precision_at_k(relevant: set, retrieved: list, k: int) -> float:
    retrieved_k = retrieved[:k]
    hits = sum(1 for pid in retrieved_k if pid in relevant)
    return hits / k if k else 0.0


def recall_at_k(relevant: set, retrieved: list, k: int) -> float:
    retrieved_k = retrieved[:k]
    hits = sum(1 for pid in retrieved_k if pid in relevant)
    return hits / len(relevant) if relevant else 0.0


def mrr(relevant: set, retrieved: list) -> float:
    for i, pid in enumerate(retrieved):
        if pid in relevant:
            return 1.0 / (i + 1)
    return 0.0


def ndcg_at_k(relevant: set, retrieved: list, k: int) -> float:
    dcg = 0.0
    for i, pid in enumerate(retrieved[:k]):
        if pid in relevant:
            dcg += 1.0 / math.log2(i + 2)
    idcg = sum(1.0 / math.log2(i + 2) for i in range(min(len(relevant), k)))
    return dcg / idcg if idcg > 0 else 0.0


def build_ground_truth(posts: dict) -> dict:
    """
    Build ground truth: posts sharing the same primary topic are considered relevant.
    This is a heuristic for synthetic eval — in production use human labels.
    """
    gt = {}
    for pid, pd in posts.items():
        primary_topic = pd["topics"][0]
        gt[pid] = {
            other_pid for other_pid, opd in posts.items()
            if other_pid != pid and opd["topics"][0] == primary_topic
        }
    return gt


# ─── Retrieval Systems ────────────────────────────────────────────────────────

def random_retrieval(query_id: str, all_ids: list, top_k: int) -> list:
    candidates = [pid for pid in all_ids if pid != query_id]
    random.shuffle(candidates)
    return candidates[:top_k]


def keyword_retrieval(query_post: dict, all_posts: dict, top_k: int) -> list:
    """Simple topic-overlap keyword retrieval."""
    query_topics = set(query_post["topics"])
    scored = []
    for pid, pd in all_posts.items():
        if pid == query_post["id"]:
            continue
        overlap = len(set(pd["topics"]) & query_topics) / max(1, len(query_topics))
        scored.append((pid, overlap))
    scored.sort(key=lambda x: -x[1])
    return [pid for pid, _ in scored[:top_k]]


def embedding_retrieval(query_id: str, query_vec, vector_index, top_k: int) -> list:
    results = vector_index.search(query_vec, top_k=top_k, exclude_ids=[query_id])
    return [pid for pid, _ in results]


# ─── Main Benchmark ───────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Benchmark AI retrieval systems")
    parser.add_argument("--posts", type=int, default=200, help="Number of synthetic posts (default 200; use 1000 for full benchmark)")
    parser.add_argument("--queries", type=int, default=20, help="Number of query posts to evaluate (default 20)")
    parser.add_argument("--top-k", type=int, default=20, help="Retrieval depth (default 20)")
    args = parser.parse_args()

    print(f"\n{'='*60}")
    print(f"  Pulse AI Benchmark")
    print(f"  Posts: {args.posts}  |  Queries: {args.queries}  |  Top-K: {args.top_k}")
    print(f"{'='*60}\n")

    print("Generating synthetic posts...")
    posts = gen_posts(args.posts)
    all_ids = list(posts.keys())
    ground_truth = build_ground_truth(posts)

    query_ids = random.sample(all_ids, min(args.queries, len(all_ids)))

    # Build embedding index
    print("Loading embedding model and indexing posts...")
    t0 = time.time()
    from app.ai.embedding_model import embedding_model
    from app.ai.vector_index import vector_index

    if not embedding_model._ensure_loaded():
        print("WARNING: Embedding model unavailable — skipping embedding/reranker tests")
        embed_ok = False
    else:
        embed_ok = True

    if embed_ok:
        post_list = list(posts.values())
        import numpy as np
        vecs = embedding_model.encode_posts(post_list)
        post_id_list = [p["id"] for p in post_list]
        vector_index.rebuild(vecs, post_id_list)
        # Store vecs for lookup
        vec_map = {pid: vecs[i] for i, pid in enumerate(post_id_list)}
        print(f"  Indexed {len(post_list)} posts in {time.time()-t0:.1f}s, mode={vector_index.mode}")

    systems = {
        "Random": [],
        "Keyword": [],
    }
    if embed_ok:
        systems["Embedding"] = []
        systems["Embedding+MMR"] = []

    from app.ai.similarity import similarity_service

    print(f"\nEvaluating {len(query_ids)} queries...")
    for q_idx, qid in enumerate(query_ids):
        qpost = posts[qid]
        relevant = ground_truth.get(qid, set())
        if not relevant:
            continue

        K = args.top_k

        # Random
        r = random_retrieval(qid, all_ids, K)
        systems["Random"].append({
            "p@5": precision_at_k(relevant, r, 5),
            "p@10": precision_at_k(relevant, r, 10),
            "r@20": recall_at_k(relevant, r, K),
            "mrr": mrr(relevant, r),
            "ndcg": ndcg_at_k(relevant, r, K),
        })

        # Keyword
        r = keyword_retrieval(qpost, posts, K)
        systems["Keyword"].append({
            "p@5": precision_at_k(relevant, r, 5),
            "p@10": precision_at_k(relevant, r, 10),
            "r@20": recall_at_k(relevant, r, K),
            "mrr": mrr(relevant, r),
            "ndcg": ndcg_at_k(relevant, r, K),
        })

        if embed_ok:
            qvec = vec_map[qid]
            # Embedding only
            r = embedding_retrieval(qid, qvec, vector_index, K)
            systems["Embedding"].append({
                "p@5": precision_at_k(relevant, r, 5),
                "p@10": precision_at_k(relevant, r, 10),
                "r@20": recall_at_k(relevant, r, K),
                "mrr": mrr(relevant, r),
                "ndcg": ndcg_at_k(relevant, r, K),
            })

            # Embedding + MMR (via similarity service)
            result = similarity_service._compute_related(qid, qpost, posts, K)
            r = [item["post_id"] for item in result.get("related_posts", [])]
            systems["Embedding+MMR"].append({
                "p@5": precision_at_k(relevant, r, 5),
                "p@10": precision_at_k(relevant, r, 10),
                "r@20": recall_at_k(relevant, r, K),
                "mrr": mrr(relevant, r),
                "ndcg": ndcg_at_k(relevant, r, K),
            })

        if (q_idx + 1) % 5 == 0:
            print(f"  {q_idx+1}/{len(query_ids)} queries done")

    # Print results table
    print(f"\n{'='*60}")
    print(f"  Results (averaged over {len(query_ids)} queries)")
    print(f"{'='*60}")
    print(f"{'System':<20} {'P@5':>7} {'P@10':>7} {'R@20':>7} {'MRR':>7} {'nDCG@20':>9}")
    print("-" * 60)

    for name, scores in systems.items():
        if not scores:
            continue
        def avg(k): return sum(s[k] for s in scores) / len(scores)
        print(f"{name:<20} {avg('p@5'):>7.3f} {avg('p@10'):>7.3f} {avg('r@20'):>7.3f} {avg('mrr'):>7.3f} {avg('ndcg'):>9.3f}")

    print(f"\n[Benchmark] Done. Use --posts 1000 for a full-scale evaluation.")


if __name__ == "__main__":
    main()
