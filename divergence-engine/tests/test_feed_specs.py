"""The 12 shipped feed specs must load, validate, and cover the spec's mesh."""

from pathlib import Path

import pytest

from divergence_engine.feeds.spec import load_feed_specs
from divergence_engine.types import Theatre

FEEDS_DIR = Path(__file__).resolve().parents[1] / "feeds"


def test_all_twelve_feeds_load():
    specs = load_feed_specs(FEEDS_DIR)
    assert len(specs) == 12
    energy = [s for s in specs.values() if s.theatre == Theatre.ENERGY]
    crypto = [s for s in specs.values() if s.theatre == Theatre.CRYPTO]
    assert len(energy) == 8 and len(crypto) == 4


def test_every_feed_has_invalidators():
    # Port of Predict's watchpoint/invalidator concept: a feed with no
    # invalidator can never tell us its story is dead.
    for spec in load_feed_specs(FEEDS_DIR).values():
        assert spec.invalidators, f"{spec.feed_id} has no invalidators"


def test_weights_are_bounded_and_horizons_sane():
    for spec in load_feed_specs(FEEDS_DIR).values():
        assert all(-1.0 <= w <= 1.0 for w in spec.instruments.values())
        assert 1 <= spec.horizon_days_typical <= 15


def test_malformed_spec_fails_loudly(tmp_path):
    (tmp_path / "bad.yaml").write_text("feed_id: X\ntheatre: energy\n")
    with pytest.raises(ValueError, match="missing keys"):
        load_feed_specs(tmp_path)


def test_duplicate_feed_id_rejected(tmp_path):
    src = (FEEDS_DIR / "E1_hormuz_friction.yaml").read_text()
    (tmp_path / "a.yaml").write_text(src)
    (tmp_path / "b.yaml").write_text(src)
    with pytest.raises(ValueError, match="duplicate feed_id"):
        load_feed_specs(tmp_path)
