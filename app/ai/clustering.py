"""
Post Clustering — HDBSCAN on post embeddings.

Automatically discovers natural groups of semantically related posts.
No fixed cluster count — HDBSCAN finds the structure in the data.
Cluster assignments stored in store/clusters.json and written to post dicts.

Usage:
  clustering_service.rebuild_clusters(db)  — full rebuild (background job)
  clustering_service.get_clusters()         — list all clusters with labels
  clustering_service.get_cluster_posts(id)  — posts in a cluster
"""
import os
import json
import logging
from typing import Dict, List, Any, Optional
from datetime import datetime
from collections import Counter

import numpy as np

from app.ai.config import (
    CLUSTERS_PATH, HDBSCAN_MIN_CLUSTER_SIZE, HDBSCAN_MIN_SAMPLES,
    EMBEDDING_MODEL_VERSION,
)
from app.ai.embedding_store import embedding_store

logger = logging.getLogger(__name__)

# Try importing HDBSCAN — falls back gracefully
try:
    import hdbscan  # type: ignore
    HDBSCAN_AVAILABLE = True
except ImportError:
    try:
        from sklearn.cluster import DBSCAN  # type: ignore
        HDBSCAN_AVAILABLE = False
        logger.warning("[Clustering] hdbscan not available — will use sklearn DBSCAN as fallback")
    except ImportError:
        HDBSCAN_AVAILABLE = False
        logger.warning("[Clustering] Neither hdbscan nor sklearn available — clustering disabled")


def _auto_label_cluster(post_ids: List[str], all_posts: Dict[str, Dict[str, Any]]) -> str:
    """
    Generate a human-readable cluster label from the most common topics in the cluster.
    """
    topic_counter: Counter = Counter()
    for pid in post_ids:
        pd = all_posts.get(pid, {})
        for t in (pd.get("topics") or []):
            topic_counter[t.lower()] += 1
    top = [t for t, _ in topic_counter.most_common(3)]
    return " + ".join(top) if top else f"Cluster ({len(post_ids)} posts)"


class ClusteringService:
    def __init__(self):
        self._clusters: Dict[str, Any] = {}   # cluster_id → {label, post_ids, size}
        self._post_cluster: Dict[str, str] = {}  # post_id → cluster_id
        self._load()

    def _load(self):
        try:
            if os.path.exists(CLUSTERS_PATH):
                with open(CLUSTERS_PATH, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if data.get("model_version") == EMBEDDING_MODEL_VERSION:
                    self._clusters = data.get("clusters", {})
                    self._post_cluster = data.get("post_cluster", {})
                    logger.info(f"[Clustering] Loaded {len(self._clusters)} clusters")
        except Exception as e:
            logger.warning(f"[Clustering] Load error: {e}")

    def _save(self):
        try:
            os.makedirs(os.path.dirname(CLUSTERS_PATH), exist_ok=True)
            with open(CLUSTERS_PATH, "w", encoding="utf-8") as f:
                json.dump({
                    "model_version": EMBEDDING_MODEL_VERSION,
                    "clusters": self._clusters,
                    "post_cluster": self._post_cluster,
                    "built_at": datetime.utcnow().isoformat(),
                }, f, indent=2)
        except Exception as e:
            logger.error(f"[Clustering] Save error: {e}")

    def rebuild_clusters(self, db_posts: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
        """
        Run HDBSCAN on all post embeddings.
        Writes cluster_id back to post dicts in db_posts.
        Returns summary of clusters found.
        """
        if not HDBSCAN_AVAILABLE:
            logger.warning("[Clustering] hdbscan unavailable — skipping rebuild")
            return {"status": "unavailable", "clusters": 0}

        matrix, post_ids = embedding_store.get_all()
        if len(post_ids) < HDBSCAN_MIN_CLUSTER_SIZE * 2:
            logger.info(f"[Clustering] Not enough posts ({len(post_ids)}) to cluster")
            return {"status": "insufficient_data", "post_count": len(post_ids)}

        logger.info(f"[Clustering] Running HDBSCAN on {len(post_ids)} posts...")

        try:
            clusterer = hdbscan.HDBSCAN(
                min_cluster_size=HDBSCAN_MIN_CLUSTER_SIZE,
                min_samples=HDBSCAN_MIN_SAMPLES,
                metric="euclidean",
                cluster_selection_method="eom",
            )
            labels = clusterer.fit_predict(matrix.astype(np.float64))
        except Exception as e:
            logger.error(f"[Clustering] HDBSCAN failed: {e}")
            return {"status": "error", "error": str(e)}

        # Build cluster map
        cluster_map: Dict[int, List[str]] = {}
        for pid, label in zip(post_ids, labels):
            if label == -1:
                continue   # noise point — not assigned to any cluster
            cluster_map.setdefault(label, []).append(pid)

        self._clusters = {}
        self._post_cluster = {}

        for label_int, pids in cluster_map.items():
            cluster_id = f"cluster-{label_int}"
            auto_label = _auto_label_cluster(pids, db_posts)
            self._clusters[cluster_id] = {
                "id": cluster_id,
                "label": auto_label,
                "post_ids": pids,
                "size": len(pids),
            }
            for pid in pids:
                self._post_cluster[pid] = cluster_id
                # Write back to post dict
                if pid in db_posts:
                    db_posts[pid]["ai_cluster_id"] = cluster_id

        self._save()

        n_clustered = sum(1 for l in labels if l != -1)
        n_noise     = sum(1 for l in labels if l == -1)
        logger.info(
            f"[Clustering] Done: {len(self._clusters)} clusters, "
            f"{n_clustered} clustered, {n_noise} noise"
        )
        return {
            "status": "ok",
            "clusters": len(self._clusters),
            "clustered_posts": n_clustered,
            "noise_posts": n_noise,
        }

    def get_clusters(self) -> List[Dict[str, Any]]:
        """Return list of all clusters sorted by size."""
        return sorted(self._clusters.values(), key=lambda c: -c["size"])

    def get_cluster_posts(self, cluster_id: str, all_posts: Dict[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Return post dicts for all posts in a cluster."""
        cluster = self._clusters.get(cluster_id)
        if not cluster:
            return []
        return [all_posts[pid] for pid in cluster["post_ids"] if pid in all_posts]

    def get_post_cluster(self, post_id: str) -> Optional[str]:
        return self._post_cluster.get(post_id)

    def status(self) -> Dict[str, Any]:
        return {
            "total_clusters": len(self._clusters),
            "total_assigned_posts": len(self._post_cluster),
            "hdbscan_available": HDBSCAN_AVAILABLE,
        }


# Singleton
clustering_service = ClusteringService()
