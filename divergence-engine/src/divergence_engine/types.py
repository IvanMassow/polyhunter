"""Core value types shared across the funnel. Pure data, no I/O."""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from datetime import datetime


class Theatre(str, enum.Enum):
    ENERGY = "energy"
    CRYPTO = "crypto"


class KillReason(str, enum.Enum):
    TOO_LATE = "too_late"                  # priced_z > limit: chart already ate the story
    NO_CORROBORATION = "no_corroboration"  # stage 3 failed
    BELOW_THRESHOLD = "below_threshold"    # |D| <= theta
    RISK_CAP = "risk_cap"                  # governor refused the ticket
    KILL_SWITCH = "kill_switch"            # global flat


class ExitReason(str, enum.Enum):
    TARGET = "target"
    STOP = "stop"
    TIME_STOP = "time_stop"
    NARRATIVE_FADE = "narrative_fade"
    INVALIDATOR = "invalidator"
    KILL_SWITCH = "kill_switch"


@dataclass(frozen=True)
class FeedState:
    """One hourly emission from a feed."""

    feed_id: str
    ts: datetime
    direction: float        # [-1, +1]
    velocity: float         # z-scored delta-pressure
    coverage: float
    contradiction: float    # [0, 1]
    freshness: float
    source: str = "report_polling"
    lanes: dict[str, float] = field(default_factory=dict)  # lane -> coverage


@dataclass(frozen=True)
class ForecastSummary:
    """Reduced Kronos output for one (instrument, horizon)."""

    instrument: str
    ts: datetime
    horizon_days: int
    granularity: str
    model: str
    n_paths: int
    last_price: float
    mu_k: float                     # median terminal return
    q05: float
    q25: float
    q75: float
    q95: float
    sigma_cone: dict[float, float]  # days elapsed -> sigma of return

    def sigma_at(self, days: float) -> float:
        """Interpolate the vol cone at an arbitrary elapsed time."""
        if not self.sigma_cone:
            raise ValueError("empty sigma cone")
        pts = sorted(self.sigma_cone.items())
        if days <= pts[0][0]:
            # Scale down by sqrt-time below the first knot rather than flat-line.
            d0, s0 = pts[0]
            return s0 * (max(days, 1e-9) / d0) ** 0.5
        for (d0, s0), (d1, s1) in zip(pts, pts[1:]):
            if days <= d1:
                w = (days - d0) / (d1 - d0)
                return s0 + w * (s1 - s0)
        return pts[-1][1]


@dataclass(frozen=True)
class Trigger:
    feed_id: str
    ts: datetime
    kind: str               # 'velocity' | 'direction_flip' | 'contradiction_collapse'
    direction: int          # sign of the narrative move, +1 / -1
    state: FeedState


@dataclass
class Candidate:
    """(instrument, story, direction, horizon) tuple moving through the funnel."""

    candidate_id: str
    instrument: str
    direction: int          # +1 long, -1 short
    horizon_days: int
    t0: datetime            # story onset = trigger time
    spawned_at: datetime
    trigger_feeds: dict[str, float]  # feed_id -> instrument weight at spawn
    cluster_id: str | None = None
    p_narr: float | None = None
    priced_z: float | None = None
    divergence: float | None = None
    threshold_used: float | None = None
    size_factor: float = 1.0
    status: str = "pending"
    kill_reason: KillReason | None = None


@dataclass
class Ticket:
    ticket_id: str
    candidate_id: str
    cluster_id: str | None
    instrument: str
    direction: int
    entry_ts: datetime
    entry_price: float
    size: float
    risk_amount: float
    stop_price: float
    target_q75: float
    target_q95: float
    time_stop_at: datetime
    exit_ts: datetime | None = None
    exit_price: float | None = None
    exit_reason: ExitReason | None = None
    realised_pnl: float | None = None

    @property
    def is_open(self) -> bool:
        return self.exit_ts is None
