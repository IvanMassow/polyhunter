from datetime import datetime, timezone

from divergence_engine.feeds.spec import FeedSpec
from divergence_engine.funnel.corroborate import is_corroborated
from divergence_engine.funnel.spawn import spawn_candidates
from divergence_engine.types import FeedState, Theatre, Trigger

NOW = datetime(2026, 6, 10, tzinfo=timezone.utc)


def _spec(feed_id, instruments, lanes=("direct_incident",)):
    return FeedSpec(
        feed_id=feed_id, theatre=Theatre.ENERGY, mechanism="m",
        keyword_universe=("k",), semantic_brief="b", lanes_expected=lanes,
        instruments=instruments, horizon_days_typical=5,
    )


def _state(feed_id, direction, coverage=0.8, lanes=None):
    return FeedState(feed_id=feed_id, ts=NOW, direction=direction, velocity=1.0,
                     coverage=coverage, contradiction=0.1, freshness=1.0,
                     lanes=lanes or {})


def _trigger(feed_id, direction=1):
    return Trigger(feed_id=feed_id, ts=NOW, kind="velocity", direction=direction,
                   state=_state(feed_id, 0.5))


def test_spawn_respects_weight_sign():
    # E5 (OPEC discipline) carries negative weights: bullish-discipline
    # narrative -> short crude.
    specs = {"E5": _spec("E5", {"BRENT": -0.8, "WTI": -0.8})}
    cands = spawn_candidates([_trigger("E5", direction=1)], specs, NOW)
    assert {c.instrument for c in cands} == {"BRENT", "WTI"}
    assert all(c.direction == -1 for c in cands)
    assert all(c.trigger_feeds == {"E5": 0.8} for c in cands)
    assert all(c.t0 == NOW for c in cands)


def test_spawn_dedupes_multiple_triggers_same_feed():
    specs = {"E1": _spec("E1", {"BRENT": 0.9})}
    trigs = [_trigger("E1"), Trigger("E1", NOW, "direction_flip", 1, _state("E1", 0.5))]
    cands = spawn_candidates(trigs, specs, NOW)
    assert len(cands) == 1


def test_two_agreeing_feeds_corroborate():
    specs = {"E1": _spec("E1", {"BRENT": 0.9}), "E4": _spec("E4", {"BRENT": 0.5})}
    cands = spawn_candidates([_trigger("E1")], specs, NOW)
    states = {"E1": _state("E1", 0.5), "E4": _state("E4", 0.7)}
    ok, supporting = is_corroborated(cands[0], states, specs)
    assert ok
    assert set(supporting) == {"E1", "E4"}


def test_single_thin_lane_never_trades():
    specs = {"E1": _spec("E1", {"BRENT": 0.9})}
    cands = spawn_candidates([_trigger("E1")], specs, NOW)
    # Weak coverage, no direct lane.
    states = {"E1": _state("E1", 0.5, coverage=0.4)}
    ok, _ = is_corroborated(cands[0], states, specs)
    assert not ok


def test_single_direct_lane_at_strong_coverage_trades():
    specs = {"E1": _spec("E1", {"BRENT": 0.9})}
    cands = spawn_candidates([_trigger("E1")], specs, NOW)
    states = {"E1": _state("E1", 0.5, coverage=0.9, lanes={"direct_incident": 0.8})}
    ok, _ = is_corroborated(cands[0], states, specs)
    assert ok


def test_disagreeing_feed_does_not_corroborate():
    specs = {"E1": _spec("E1", {"BRENT": 0.9}), "E5": _spec("E5", {"BRENT": -0.8})}
    cands = spawn_candidates([_trigger("E1")], specs, NOW)  # long BRENT
    # E5 direction +0.5 with weight -0.8 implies short BRENT: disagrees.
    states = {"E1": _state("E1", 0.5, coverage=0.4), "E5": _state("E5", 0.5)}
    ok, supporting = is_corroborated(cands[0], states, specs)
    assert not ok
    assert set(supporting) == {"E1"}
