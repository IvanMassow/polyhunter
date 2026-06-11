"""Stage 3 — corroboration. One thin lane never trades.

Require >=2 feeds in the same story cluster agreeing in sign for the
instrument, OR a single feed with a direct lane at strong coverage.
"""

from __future__ import annotations

from ..feeds.spec import FeedSpec
from ..types import Candidate, FeedState

# Lane names that count as 'direct' evidence (incidents, not commentary).
DIRECT_LANE_PREFIXES = ("direct", "incident", "outage_event", "enforcement_action", "military_action")
STRONG_COVERAGE = 0.7


def _is_direct_lane(lane: str) -> bool:
    return any(lane.startswith(p) or p in lane for p in DIRECT_LANE_PREFIXES)


def _feed_agrees(candidate: Candidate, spec: FeedSpec, state: FeedState) -> bool:
    weight = spec.weight_for(candidate.instrument)
    if weight == 0.0 or state.direction == 0.0:
        return False
    implied = (1 if state.direction > 0 else -1) * (1 if weight > 0 else -1)
    return implied == candidate.direction


def is_corroborated(
    candidate: Candidate,
    latest_states: dict[str, FeedState],
    specs: dict[str, FeedSpec],
) -> tuple[bool, dict[str, float]]:
    """Returns (passed, supporting feed weights for the candidate's instrument).

    Supporting feeds get folded into candidate.trigger_feeds by the caller so
    corroborators count toward story clustering and P_narr.
    """
    supporting: dict[str, float] = dict(candidate.trigger_feeds)
    for feed_id, state in latest_states.items():
        if feed_id in supporting:
            continue
        spec = specs.get(feed_id)
        if spec is None:
            continue
        if _feed_agrees(candidate, spec, state):
            supporting[feed_id] = abs(spec.weight_for(candidate.instrument))

    if len(supporting) >= 2:
        return True, supporting

    # Single-feed path: direct lane at strong coverage.
    (feed_id,) = supporting
    state = latest_states.get(feed_id)
    if state is not None and state.coverage >= STRONG_COVERAGE:
        if any(_is_direct_lane(lane) and cov > 0 for lane, cov in state.lanes.items()):
            return True, supporting
    return False, supporting
