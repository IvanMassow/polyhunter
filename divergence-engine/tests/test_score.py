from datetime import datetime, timezone

import pytest

from divergence_engine.config import ScoreConfig
from divergence_engine.feeds.spec import FeedSpec
from divergence_engine.funnel.score import (
    calibrate_theta,
    divergence_score,
    narrative_pressure,
    passes_threshold,
)
from divergence_engine.types import Candidate, FeedState, Theatre

NOW = datetime(2026, 6, 10, tzinfo=timezone.utc)


def _spec(feed_id, weight):
    return FeedSpec(
        feed_id=feed_id, theatre=Theatre.ENERGY, mechanism="m",
        keyword_universe=("k",), semantic_brief="b", lanes_expected=("l",),
        instruments={"BRENT": weight}, horizon_days_typical=5,
    )


def _state(feed_id, direction, velocity, coverage, contradiction, freshness):
    return FeedState(feed_id=feed_id, ts=NOW, direction=direction, velocity=velocity,
                     coverage=coverage, contradiction=contradiction, freshness=freshness)


def test_narrative_pressure_formula():
    cand = Candidate(candidate_id="c", instrument="BRENT", direction=1, horizon_days=5,
                     t0=NOW, spawned_at=NOW, trigger_feeds={"E1": 0.9, "E5": 0.8})
    specs = {"E1": _spec("E1", 0.9), "E5": _spec("E5", -0.8)}
    states = {
        "E1": _state("E1", 0.5, 2.0, 0.8, 0.25, 1.0),   # 0.9*0.5*2*0.8*0.75*1 = 0.54
        "E5": _state("E5", -0.5, 1.0, 0.5, 0.0, 0.8),   # -0.8*-0.5*1*0.5*1*0.8 = 0.16
    }
    assert narrative_pressure(cand, states, specs) == pytest.approx(0.70)


def test_divergence_subtracts_priced_z():
    cfg = ScoreConfig(lambda_priced=0.5)
    assert divergence_score(0.7, 1.0, cfg) == pytest.approx(0.2)
    assert divergence_score(-0.7, -1.0, cfg) == pytest.approx(-0.2)


def test_theta_uncalibrated_trades_nothing():
    assert calibrate_theta([0.5] * 10) == float("inf")
    assert not passes_threshold(99.0, float("inf"))


def test_theta_top_decile():
    scores = [float(i) for i in range(1, 101)]  # 1..100
    theta = calibrate_theta(scores)
    assert theta == pytest.approx(90.1)
    assert passes_threshold(95.0, theta)
    assert not passes_threshold(50.0, theta)


def test_theta_explicit_constant_wins():
    assert calibrate_theta([1.0] * 50, ScoreConfig(theta=0.3)) == 0.3


def test_structure_conflict_multiplier_raises_bar():
    assert passes_threshold(1.0, 0.9, threshold_mult=1.0)
    assert not passes_threshold(1.0, 0.9, threshold_mult=1.5)
