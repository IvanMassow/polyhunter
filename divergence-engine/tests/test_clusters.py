from datetime import datetime, timezone

import pytest

from divergence_engine.risk.clusters import assign_clusters, weighted_overlap
from divergence_engine.types import Candidate

NOW = datetime(2026, 6, 10, tzinfo=timezone.utc)


def _cand(cid, instrument, feeds):
    return Candidate(candidate_id=cid, instrument=instrument, direction=1, horizon_days=5,
                     t0=NOW, spawned_at=NOW, trigger_feeds=feeds)


def test_weighted_overlap():
    a = {"E1": 0.9, "E2": 0.6}
    b = {"E1": 0.9, "E3": 0.5}
    # min-sum = 0.9, max-sum = 0.9 + 0.6 + 0.5 = 2.0
    assert weighted_overlap(a, b) == pytest.approx(0.45)
    assert weighted_overlap(a, a) == 1.0
    assert weighted_overlap(a, {}) == 0.0


def test_same_story_many_instruments_is_one_cluster():
    # The PolyHunter failure mode: one theme expressed through many instruments.
    feeds = {"E1": 0.9, "E4": 0.8}
    cands = [_cand(f"c{i}", inst, dict(feeds)) for i, inst in
             enumerate(["BRENT", "WTI", "GASOIL", "FRO", "STNG"])]
    clusters = assign_clusters(cands)
    assert len(clusters) == 1
    cid = next(iter(clusters))
    assert all(c.cluster_id == cid for c in cands)


def test_distinct_stories_stay_separate():
    a = _cand("a", "BRENT", {"E1": 0.9})
    b = _cand("b", "BTC", {"C1": 0.6})
    clusters = assign_clusters([a, b])
    assert len(clusters) == 2
    assert a.cluster_id != b.cluster_id


def test_transitive_chaining():
    # a~b and b~c clear the threshold but a~c share nothing: union-find still
    # merges all three into one story.
    a = _cand("a", "BRENT", {"E1": 1.0, "E2": 1.0})
    b = _cand("b", "WTI", {"E2": 1.0, "E3": 1.0})
    c = _cand("c", "TTF", {"E3": 1.0, "E4": 1.0})
    assert weighted_overlap(a.trigger_feeds, b.trigger_feeds) == pytest.approx(1 / 3)
    assert weighted_overlap(a.trigger_feeds, c.trigger_feeds) == 0.0
    clusters = assign_clusters([a, b, c], threshold=0.3)
    assert len(clusters) == 1


def test_cluster_id_stable_across_runs():
    a1 = _cand("a", "BRENT", {"E1": 0.9})
    a2 = _cand("a", "BRENT", {"E1": 0.9})
    (cid1,) = assign_clusters([a1])
    (cid2,) = assign_clusters([a2])
    assert cid1 == cid2
