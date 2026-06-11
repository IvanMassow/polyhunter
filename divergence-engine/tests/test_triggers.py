from datetime import datetime, timedelta, timezone

from divergence_engine.funnel.triggers import scan_triggers
from divergence_engine.types import FeedState

T0 = datetime(2026, 6, 1, tzinfo=timezone.utc)


def _state(hours, velocity=0.1, direction=0.5, coverage=0.6, contradiction=0.1, freshness=1.0):
    return FeedState(
        feed_id="E1",
        ts=T0 + timedelta(hours=hours),
        direction=direction,
        velocity=velocity,
        coverage=coverage,
        contradiction=contradiction,
        freshness=freshness,
    )


def _quiet_history(n=100, **kw):
    # Small velocity wiggle so the trailing distribution has nonzero variance.
    return [_state(i, **{"velocity": 0.1 + 0.02 * (i % 5 - 2), **kw}) for i in range(n)]


def test_velocity_spike_fires():
    history = _quiet_history() + [_state(100, velocity=5.0)]
    fired = scan_triggers(history)
    assert [t.kind for t in fired] == ["velocity"]
    assert fired[0].direction == 1


def test_no_trigger_on_quiet_feed():
    assert scan_triggers(_quiet_history()) == []


def test_direction_flip_needs_coverage():
    history = _quiet_history(direction=0.5)
    flipped_thin = history + [_state(100, direction=-0.5, coverage=0.2)]
    assert all(t.kind != "direction_flip" for t in scan_triggers(flipped_thin))

    flipped_covered = history + [_state(100, direction=-0.5, coverage=0.8)]
    kinds = [t.kind for t in scan_triggers(flipped_covered)]
    assert "direction_flip" in kinds
    assert next(t for t in scan_triggers(flipped_covered) if t.kind == "direction_flip").direction == -1


def test_contradiction_collapse():
    history = _quiet_history(contradiction=0.7)
    consolidated = history + [_state(100, contradiction=0.1)]
    kinds = [t.kind for t in scan_triggers(consolidated)]
    assert "contradiction_collapse" in kinds

    # No collapse if the story was never contested.
    calm = _quiet_history(contradiction=0.3) + [_state(100, contradiction=0.1)]
    assert all(t.kind != "contradiction_collapse" for t in scan_triggers(calm))


def test_needs_a_day_of_history_for_velocity_z():
    short = [_state(i) for i in range(10)] + [_state(10, velocity=5.0)]
    assert all(t.kind != "velocity" for t in scan_triggers(short))
