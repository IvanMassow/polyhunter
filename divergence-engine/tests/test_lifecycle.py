from datetime import datetime, timedelta, timezone

from divergence_engine.funnel.lifecycle import check_exit
from divergence_engine.types import ExitReason, FeedState, Ticket

NOW = datetime(2026, 6, 10, tzinfo=timezone.utc)


def _ticket(direction=1):
    return Ticket(
        ticket_id="t", candidate_id="c", cluster_id="cl", instrument="BRENT",
        direction=direction, entry_ts=NOW, entry_price=100.0, size=10.0,
        risk_amount=500.0,
        stop_price=94.0 if direction > 0 else 106.0,
        target_q75=105.0 if direction > 0 else 95.0,
        target_q95=109.0 if direction > 0 else 91.0,
        time_stop_at=NOW + timedelta(days=7.5),
    )


def _state(direction, velocity):
    return FeedState(feed_id="E1", ts=NOW, direction=direction, velocity=velocity,
                     coverage=0.8, contradiction=0.1, freshness=1.0)


def test_holds_inside_band():
    assert check_exit(_ticket(), 101.0, NOW + timedelta(days=1)) is None


def test_target_and_stop_long():
    assert check_exit(_ticket(), 105.5, NOW) == ExitReason.TARGET
    assert check_exit(_ticket(), 93.0, NOW) == ExitReason.STOP


def test_target_and_stop_short():
    assert check_exit(_ticket(-1), 94.0, NOW) == ExitReason.TARGET
    assert check_exit(_ticket(-1), 107.0, NOW) == ExitReason.STOP


def test_time_stop():
    assert check_exit(_ticket(), 101.0, NOW + timedelta(days=8)) == ExitReason.TIME_STOP


def test_invalidator_outranks_everything():
    assert check_exit(_ticket(), 105.5, NOW, invalidator_hit=True) == ExitReason.INVALIDATOR


def test_narrative_fade_only_in_profit():
    # Long ticket, story velocity now pointing down (weight +0.9 feed turned
    # negative), price in profit -> take it.
    states = {"E1": _state(direction=-0.6, velocity=1.5)}
    weights = {"E1": 0.9}
    assert (
        check_exit(_ticket(), 103.0, NOW + timedelta(days=1), states, weights)
        == ExitReason.NARRATIVE_FADE
    )
    # Same fade under water: hold for stop/time-stop instead.
    assert check_exit(_ticket(), 98.0, NOW + timedelta(days=1), states, weights) is None
