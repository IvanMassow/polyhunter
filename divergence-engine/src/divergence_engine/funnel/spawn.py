"""Stage 2 — spawn candidate cards from triggers via the feed's instrument map.

A candidate is (instrument, story, direction, horizon), not an instrument.
Trade direction = trigger direction x sign of the feed's instrument weight
(e.g. OPEC quota *discipline* news is bearish-supply -> negative weights).
"""

from __future__ import annotations

import hashlib
from datetime import datetime

from ..feeds.spec import FeedSpec
from ..types import Candidate, Trigger


def _candidate_id(feed_id: str, instrument: str, direction: int, t0: datetime) -> str:
    key = f"{feed_id}|{instrument}|{direction}|{t0.isoformat()}"
    return hashlib.sha256(key.encode()).hexdigest()[:16]


def spawn_candidates(
    triggers: list[Trigger],
    specs: dict[str, FeedSpec],
    now: datetime,
) -> list[Candidate]:
    """One candidate per (instrument, direction); multiple triggers on the
    same feed in the same hour collapse into one card with t0 = earliest."""
    seen: dict[tuple[str, str, int], Candidate] = {}
    for trig in triggers:
        spec = specs[trig.feed_id]
        for instrument, weight in spec.instruments.items():
            if weight == 0.0:
                continue
            direction = trig.direction * (1 if weight > 0 else -1)
            key = (trig.feed_id, instrument, direction)
            if key in seen:
                if trig.ts < seen[key].t0:
                    seen[key].t0 = trig.ts
                continue
            seen[key] = Candidate(
                candidate_id=_candidate_id(trig.feed_id, instrument, direction, trig.ts),
                instrument=instrument,
                direction=direction,
                horizon_days=spec.horizon_days_typical,
                t0=trig.ts,
                spawned_at=now,
                trigger_feeds={trig.feed_id: abs(weight)},
            )
    return list(seen.values())
