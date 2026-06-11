"""Stage 5 — divergence score.

P_narr = sum over supporting feeds of
         w_f * direction_f * velocity_f * coverage_f * (1 - contradiction_f) * freshness_f
D      = P_narr - lambda * priced_z

Open a paper ticket when |D| > theta. While theta is uncalibrated, use the
top decile of recent candidate |D| scores rather than a magic constant.
"""

from __future__ import annotations

from ..config import ScoreConfig
from ..feeds.spec import FeedSpec
from ..types import Candidate, FeedState


def narrative_pressure(
    candidate: Candidate,
    latest_states: dict[str, FeedState],
    specs: dict[str, FeedSpec],
) -> float:
    """Signed narrative pressure on the candidate's instrument.

    Uses the feed's signed instrument weight, so a bearish-weight feed with
    positive narrative direction contributes negative pressure — the result
    is in *price* direction terms and comparable with candidate.direction.
    """
    total = 0.0
    for feed_id in candidate.trigger_feeds:
        state = latest_states.get(feed_id)
        spec = specs.get(feed_id)
        if state is None or spec is None:
            continue
        w = spec.weight_for(candidate.instrument)
        total += (
            w
            * state.direction
            * abs(state.velocity)
            * state.coverage
            * (1.0 - state.contradiction)
            * state.freshness
        )
    return total


def divergence_score(p_narr: float, priced_z: float, config: ScoreConfig | None = None) -> float:
    cfg = config or ScoreConfig()
    return p_narr - cfg.lambda_priced * priced_z


def calibrate_theta(recent_abs_scores: list[float], config: ScoreConfig | None = None) -> float:
    """Theta = configured constant if set, else the top-decile cut of recent
    candidate |D| scores (spec: target ~3-6 tickets/week, tune from there)."""
    cfg = config or ScoreConfig()
    if cfg.theta is not None:
        return cfg.theta
    if len(recent_abs_scores) < 20:
        return float("inf")  # not enough history to calibrate: trade nothing
    return _quantile(recent_abs_scores, cfg.theta_decile)


def _quantile(xs: list[float], q: float) -> float:
    ordered = sorted(xs)
    idx = q * (len(ordered) - 1)
    lo, hi = int(idx), min(int(idx) + 1, len(ordered) - 1)
    frac = idx - lo
    return ordered[lo] * (1 - frac) + ordered[hi] * frac


def passes_threshold(divergence: float, theta: float, threshold_mult: float = 1.0) -> bool:
    return abs(divergence) > theta * threshold_mult
