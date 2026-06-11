"""Stage 1 — hourly trigger scan over feed_state history.

Fire if any of:
 (a) velocity z-score > 2 over the feed's trailing 30-day distribution;
 (b) direction flips sign with coverage >= moderate;
 (c) contradiction collapses below 0.2 having been above 0.5 in the lookback
     (a contested story consolidating is often the tradeable moment).
"""

from __future__ import annotations

import statistics
from datetime import timedelta

from ..config import TriggerConfig
from ..types import FeedState, Trigger


def _sign(x: float) -> int:
    return 1 if x > 0 else (-1 if x < 0 else 0)


def scan_triggers(
    history: list[FeedState],
    config: TriggerConfig | None = None,
) -> list[Trigger]:
    """Evaluate the latest emission of one feed against its history.

    history: chronologically ordered states for a single feed, latest last.
    Returns zero or more triggers for the latest emission (a single hour can
    legitimately fire on more than one condition; dedup happens at spawn).
    """
    cfg = config or TriggerConfig()
    if len(history) < 2:
        return []
    now_state = history[-1]
    triggers: list[Trigger] = []

    # (a) velocity z-score over trailing 30 days (excluding the current bar)
    window_start = now_state.ts - timedelta(days=cfg.velocity_lookback_days)
    window = [s.velocity for s in history[:-1] if s.ts >= window_start]
    if len(window) >= 24:  # need at least a day of hourly history for a sane z
        mean = statistics.fmean(window)
        sd = statistics.stdev(window)
        if sd > 1e-12:
            z = (now_state.velocity - mean) / sd
            if abs(z) > cfg.velocity_z_min:
                triggers.append(
                    Trigger(
                        feed_id=now_state.feed_id,
                        ts=now_state.ts,
                        kind="velocity",
                        direction=_sign(now_state.velocity) or _sign(now_state.direction),
                        state=now_state,
                    )
                )

    # (b) direction sign flip with at least moderate coverage
    prev = history[-2]
    if (
        _sign(now_state.direction) != 0
        and _sign(prev.direction) != 0
        and _sign(now_state.direction) != _sign(prev.direction)
        and now_state.coverage >= cfg.flip_coverage_min
    ):
        triggers.append(
            Trigger(
                feed_id=now_state.feed_id,
                ts=now_state.ts,
                kind="direction_flip",
                direction=_sign(now_state.direction),
                state=now_state,
            )
        )

    # (c) contradiction collapse: contested story consolidating
    c_start = now_state.ts - timedelta(days=cfg.contradiction_lookback_days)
    was_contested = any(
        s.contradiction > cfg.contradiction_high for s in history[:-1] if s.ts >= c_start
    )
    if was_contested and now_state.contradiction < cfg.contradiction_low:
        triggers.append(
            Trigger(
                feed_id=now_state.feed_id,
                ts=now_state.ts,
                kind="contradiction_collapse",
                direction=_sign(now_state.direction),
                state=now_state,
            )
        )

    return [t for t in triggers if t.direction != 0]
