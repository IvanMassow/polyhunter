"""Feed spec schema and loader.

A feed is a standing orchestration on the Noah database: keyword universe
reduction -> semantic tunnel -> lane physics, emitting an hourly state vector.
Specs live as YAML files in the repo (one per feed) so they are versioned and
reviewable; the registry table stores the exact YAML that was live.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from ..types import Theatre

REQUIRED_KEYS = {
    "feed_id",
    "theatre",
    "mechanism",
    "keyword_universe",
    "semantic_brief",
    "lanes_expected",
    "instruments",
    "horizon_days_typical",
}


@dataclass(frozen=True)
class FeedSpec:
    feed_id: str
    theatre: Theatre
    mechanism: str
    keyword_universe: tuple[str, ...]
    semantic_brief: str
    lanes_expected: tuple[str, ...]
    instruments: dict[str, float]       # instrument -> signed weight
    horizon_days_typical: int
    invalidators: tuple[str, ...] = ()  # conditions that immediately void the story
    spec_yaml: str = ""
    spec_hash: str = ""

    def weight_for(self, instrument: str) -> float:
        return self.instruments.get(instrument, 0.0)


def _parse_spec(text: str, origin: str) -> FeedSpec:
    raw = yaml.safe_load(text)
    if not isinstance(raw, dict):
        raise ValueError(f"{origin}: feed spec must be a mapping")
    missing = REQUIRED_KEYS - raw.keys()
    if missing:
        raise ValueError(f"{origin}: missing keys {sorted(missing)}")
    instruments = {str(k): float(v) for k, v in raw["instruments"].items()}
    if not instruments:
        raise ValueError(f"{origin}: instruments map is empty")
    for inst, w in instruments.items():
        if not -1.0 <= w <= 1.0:
            raise ValueError(f"{origin}: weight for {inst} out of [-1, 1]: {w}")
    return FeedSpec(
        feed_id=str(raw["feed_id"]),
        theatre=Theatre(raw["theatre"]),
        mechanism=str(raw["mechanism"]).strip(),
        keyword_universe=tuple(str(k) for k in raw["keyword_universe"]),
        semantic_brief=str(raw["semantic_brief"]).strip(),
        lanes_expected=tuple(str(l) for l in raw["lanes_expected"]),
        instruments=instruments,
        horizon_days_typical=int(raw["horizon_days_typical"]),
        invalidators=tuple(str(i) for i in raw.get("invalidators", ())),
        spec_yaml=text,
        spec_hash=hashlib.sha256(text.encode()).hexdigest(),
    )


def load_feed_specs(feeds_dir: str | Path) -> dict[str, FeedSpec]:
    """Load every *.yaml in feeds_dir, keyed by feed_id. Fails loudly on
    duplicates or malformed specs — a silently dropped feed is a silent hole
    in the sensor mesh."""
    specs: dict[str, FeedSpec] = {}
    paths = sorted(Path(feeds_dir).glob("*.yaml"))
    if not paths:
        raise FileNotFoundError(f"no feed specs found in {feeds_dir}")
    for path in paths:
        spec = _parse_spec(path.read_text(), origin=str(path))
        if spec.feed_id in specs:
            raise ValueError(f"duplicate feed_id {spec.feed_id} in {path}")
        specs[spec.feed_id] = spec
    return specs
