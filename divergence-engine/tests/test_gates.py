from datetime import datetime, timezone

import pytest

from divergence_engine.funnel.gates import apply_kronos_gates, priced_z
from divergence_engine.types import Candidate, ForecastSummary, KillReason

NOW = datetime(2026, 6, 10, tzinfo=timezone.utc)


def _forecast(q05=-0.06, q25=-0.02, q75=0.02, q95=0.06, mu=0.0, cone=None):
    return ForecastSummary(
        instrument="BRENT", ts=NOW, horizon_days=5, granularity="1d",
        model="kronos-small", n_paths=64, last_price=80.0, mu_k=mu,
        q05=q05, q25=q25, q75=q75, q95=q95,
        sigma_cone=cone or {1.0: 0.01, 3.0: 0.02, 5.0: 0.03},
    )


def _candidate(direction=1):
    return Candidate(
        candidate_id="c1", instrument="BRENT", direction=direction, horizon_days=5,
        t0=NOW, spawned_at=NOW, trigger_feeds={"E1": 0.9},
    )


def test_priced_z_arithmetic():
    f = _forecast()
    # +2% move over 3 days, sigma(3d)=0.02 -> z = 1.0 for a long signal.
    assert priced_z(81.6, 80.0, 3.0, 1, f) == pytest.approx(1.0)
    # Same move against a short signal is negative (story NOT priced).
    assert priced_z(81.6, 80.0, 3.0, -1, f) == pytest.approx(-1.0)


def test_sigma_cone_interpolation():
    f = _forecast()
    assert f.sigma_at(2.0) == pytest.approx(0.015)        # midpoint of 1d/3d knots
    assert f.sigma_at(10.0) == pytest.approx(0.03)        # beyond the cone: last knot
    assert f.sigma_at(0.25) == pytest.approx(0.005)       # sqrt-time below first knot


def test_already_priced_kill():
    f = _forecast()
    # +4% in 3 days vs sigma 0.02 -> z = 2.0 > 1.5 -> too_late.
    res = apply_kronos_gates(_candidate(1), 83.2, 80.0, 3.0, f)
    assert res.killed and res.kill_reason == KillReason.TOO_LATE
    assert res.priced_z == pytest.approx(2.0)


def test_structure_conflict_raises_bar_and_halves_size():
    bearish = _forecast(q05=-0.10, q25=-0.06, q75=-0.01, q95=0.02)
    res = apply_kronos_gates(_candidate(1), 80.0, 80.0, 1.0, bearish)
    assert not res.killed
    assert res.threshold_mult == pytest.approx(1.5)
    assert res.size_factor == pytest.approx(0.5)

    # Symmetric for shorts: q25 > 0 means structure strongly long.
    bullish = _forecast(q05=-0.02, q25=0.01, q75=0.06, q95=0.10)
    res = apply_kronos_gates(_candidate(-1), 80.0, 80.0, 1.0, bullish)
    assert res.threshold_mult == pytest.approx(1.5)
    assert res.size_factor == pytest.approx(0.5)


def test_no_conflict_no_adjustment():
    res = apply_kronos_gates(_candidate(1), 80.0, 80.0, 1.0, _forecast())
    assert (res.killed, res.threshold_mult, res.size_factor) == (False, 1.0, 1.0)
