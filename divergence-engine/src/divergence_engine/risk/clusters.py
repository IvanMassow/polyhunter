"""Story clustering — the PolyHunter lesson.

PolyHunter's 73 positions were 40 markets in ~2 themes. Candidates sharing
>=50% of weighted trigger feeds belong to one story_cluster, and a cluster
gets a single risk budget no matter how many instruments express it.

Overlap metric: weighted Jaccard on the trigger-feed weight vectors,
  overlap(a, b) = sum_f min(w_a[f], w_b[f]) / sum_f max(w_a[f], w_b[f])
Clusters are connected components under overlap >= threshold (union-find).
"""

from __future__ import annotations

import hashlib

from ..types import Candidate


def weighted_overlap(a: dict[str, float], b: dict[str, float]) -> float:
    if not a or not b:
        return 0.0
    feeds = set(a) | set(b)
    num = sum(min(a.get(f, 0.0), b.get(f, 0.0)) for f in feeds)
    den = sum(max(a.get(f, 0.0), b.get(f, 0.0)) for f in feeds)
    return num / den if den > 0 else 0.0


def _cluster_id(feed_ids: list[str]) -> str:
    key = "|".join(sorted(feed_ids))
    return "cl_" + hashlib.sha256(key.encode()).hexdigest()[:12]


def assign_clusters(
    candidates: list[Candidate],
    threshold: float = 0.5,
) -> dict[str, list[Candidate]]:
    """Group candidates into story clusters and stamp candidate.cluster_id.

    Returns {cluster_id: members}. Cluster id is derived from the union of
    member trigger feeds, so the same story re-clustered later keeps its id.
    """
    n = len(candidates)
    parent = list(range(n))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(i: int, j: int) -> None:
        parent[find(i)] = find(j)

    for i in range(n):
        for j in range(i + 1, n):
            if weighted_overlap(candidates[i].trigger_feeds, candidates[j].trigger_feeds) >= threshold:
                union(i, j)

    groups: dict[int, list[Candidate]] = {}
    for i, cand in enumerate(candidates):
        groups.setdefault(find(i), []).append(cand)

    out: dict[str, list[Candidate]] = {}
    for members in groups.values():
        feed_union = sorted({f for m in members for f in m.trigger_feeds})
        cid = _cluster_id(feed_union)
        for m in members:
            m.cluster_id = cid
        out[cid] = members
    return out
