"""FeedSource interface and its two implementations.

NoahInternalFeedAPI — preferred: the keyword+semantic analytics API. The
client is written against the expected endpoint shape and activates as soon
as base_url/token are configured.

NoahReportPolling — Phase 0 fallback: scheduled saved-chat refreshes land as
report packages (JSON files) in a directory; we parse the latest package per
feed. Slower and costlier, but requires no API access.
"""

from __future__ import annotations

import abc
import json
from datetime import datetime, timezone
from pathlib import Path

from ..types import FeedState
from .spec import FeedSpec


class FeedSource(abc.ABC):
    """Fetches the current hourly state vector for a feed."""

    @abc.abstractmethod
    def fetch(self, spec: FeedSpec, now: datetime) -> FeedState | None:
        """Return the freshest FeedState for `spec`, or None if nothing new."""


def _validate_state(feed_id: str, raw: dict, ts: datetime, source: str) -> FeedState:
    direction = float(raw["direction"])
    contradiction = float(raw["contradiction"])
    if not -1.0 <= direction <= 1.0:
        raise ValueError(f"{feed_id}: direction out of [-1,1]: {direction}")
    if not 0.0 <= contradiction <= 1.0:
        raise ValueError(f"{feed_id}: contradiction out of [0,1]: {contradiction}")
    return FeedState(
        feed_id=feed_id,
        ts=ts,
        direction=direction,
        velocity=float(raw["velocity"]),
        coverage=float(raw["coverage"]),
        contradiction=contradiction,
        freshness=float(raw["freshness"]),
        source=source,
        lanes={str(k): float(v) for k, v in raw.get("lanes", {}).items()},
    )


class NoahInternalFeedAPI(FeedSource):
    """Client for Noah's internal keyword+semantic analytics API.

    Expected endpoint: GET {base_url}/feeds/{feed_id}/state
    -> {"ts": iso8601, "direction": .., "velocity": .., "coverage": ..,
        "contradiction": .., "freshness": .., "lanes": {...}}
    """

    def __init__(self, base_url: str, token: str, timeout_s: float = 30.0):
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.timeout_s = timeout_s

    def fetch(self, spec: FeedSpec, now: datetime) -> FeedState | None:
        import urllib.request

        req = urllib.request.Request(
            f"{self.base_url}/feeds/{spec.feed_id}/state",
            headers={"Authorization": f"Bearer {self.token}"},
        )
        with urllib.request.urlopen(req, timeout=self.timeout_s) as resp:
            raw = json.loads(resp.read())
        ts = datetime.fromisoformat(raw["ts"])
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        return _validate_state(spec.feed_id, raw, ts, source="internal_api")


class NoahReportPolling(FeedSource):
    """Parses report packages dropped by scheduled saved-chat refreshes.

    Layout: {packages_dir}/{feed_id}/*.json, one file per refresh, named or
    keyed by emission time. Each package must contain the state-vector keys;
    anything else in the file is preserved upstream in feed_state.raw.
    """

    def __init__(self, packages_dir: str | Path, max_age_hours: float = 3.0):
        self.packages_dir = Path(packages_dir)
        self.max_age_hours = max_age_hours

    def fetch(self, spec: FeedSpec, now: datetime) -> FeedState | None:
        feed_dir = self.packages_dir / spec.feed_id
        if not feed_dir.is_dir():
            return None
        packages = sorted(feed_dir.glob("*.json"))
        if not packages:
            return None
        latest = packages[-1]
        raw = json.loads(latest.read_text())
        ts = datetime.fromisoformat(raw["ts"])
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        age_h = (now - ts).total_seconds() / 3600.0
        if age_h > self.max_age_hours:
            return None  # stale package: treat as no emission, not as data
        return _validate_state(spec.feed_id, raw, ts, source="report_polling")
