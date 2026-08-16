"""
rebuild_embeddings.py — CLI to regenerate embeddings for all posts.

Usage:
    python scripts/rebuild_embeddings.py
    python scripts/rebuild_embeddings.py --model intfloat/multilingual-e5-small
    python scripts/rebuild_embeddings.py --batch-size 16 --dry-run

Use this when:
  - You change the embedding model (PULSE_EMBEDDING_MODEL env var or ai/config.py)
  - Embeddings are corrupted or missing
  - You want to verify embedding coverage
"""
import sys
import os
import argparse

# Add backend to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))


def main():
    parser = argparse.ArgumentParser(description="Rebuild Pulse post embeddings")
    parser.add_argument("--model", default=None, help="Override embedding model name")
    parser.add_argument("--batch-size", type=int, default=32, help="Batch size (default 32)")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be embedded without running")
    parser.add_argument("--force", action="store_true", help="Re-embed all posts, not just missing/stale ones")
    args = parser.parse_args()

    if args.model:
        os.environ["PULSE_EMBEDDING_MODEL"] = args.model

    # Import after setting env vars
    from app.database.db import db
    from app.ai.embedding_store import embedding_store
    from app.ai.embedding_model import embedding_model
    from app.ai.vector_index import vector_index
    from app.ai.quality_scorer import score_post_quality

    total_posts = len(db.posts)
    print(f"\n[Rebuild] Total posts in DB: {total_posts}")
    print(f"[Rebuild] Embedding model: {embedding_model.model_version}")
    print(f"[Rebuild] Store path: {embedding_store.status()['store_path']}")

    if args.force:
        missing = list(db.posts.keys())
        print(f"[Rebuild] --force: re-embedding all {len(missing)} posts")
    else:
        missing = embedding_store.get_missing_post_ids(list(db.posts.keys()))
        print(f"[Rebuild] Missing/stale embeddings: {len(missing)}")

    if not missing:
        print("[Rebuild] Nothing to do — all embeddings are up to date.")
        return

    if args.dry_run:
        print(f"\n[DRY RUN] Would embed {len(missing)} posts:")
        for pid in missing[:10]:
            pd = db.posts.get(pid, {})
            print(f"  - {pid}: {pd.get('title', '')[:60]}")
        if len(missing) > 10:
            print(f"  ... and {len(missing) - 10} more")
        return

    # Ensure model is loaded
    if not embedding_model._ensure_loaded():
        print(f"[Rebuild] ERROR: Could not load model. Check your internet connection or model name.")
        sys.exit(1)

    print(f"\n[Rebuild] Starting embedding — batch_size={args.batch_size}...")
    batch_size = args.batch_size
    success = 0
    failed = 0

    for i in range(0, len(missing), batch_size):
        batch_ids   = missing[i:i+batch_size]
        batch_posts = [db.posts[pid] for pid in batch_ids if pid in db.posts]
        if not batch_posts:
            continue

        # Compute quality scores
        for pd in batch_posts:
            pd["ai_quality_score"] = score_post_quality(pd)

        vecs = embedding_model.encode_posts(batch_posts)
        for pid, vec in zip(batch_ids, vecs):
            embedding_store.add_or_update(pid, vec)
            success += 1

        pct = round(100 * (i + len(batch_ids)) / len(missing))
        print(f"  [{pct:3d}%] Batch {i//batch_size + 1} done ({success} embedded, {failed} failed)")

    # Rebuild vector index
    print("\n[Rebuild] Rebuilding vector index...")
    from app.ai.embedding_store import embedding_store as es
    matrix, post_ids = es.get_all()
    vector_index.rebuild(matrix, post_ids)
    print(f"[Rebuild] Vector index: {vector_index.size} posts, mode={vector_index.mode}")

    print(f"\n[Rebuild] Complete! {success} embedded, {failed} failed.")
    print(f"[Rebuild] Coverage: {es.count}/{total_posts} posts ({round(100*es.count/max(1,total_posts),1)}%)")


if __name__ == "__main__":
    main()
