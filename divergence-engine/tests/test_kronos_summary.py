from datetime import datetime, timezone

import numpy as np
import pytest

from divergence_engine.kronos.forecaster import summarize_paths

NOW = datetime(2026, 6, 10, tzinfo=timezone.utc)


def _summary(paths, last_price=100.0, bar_hours=24.0):
    return summarize_paths(
        np.asarray(paths, dtype=float), last_price, bar_hours,
        instrument="BRENT", ts=NOW, horizon_days=5, granularity="1d",
        model="kronos-small",
    )


def test_quantiles_and_median():
    # 64 paths whose terminal prices are 100 * (1 + i/100), i = 0..63.
    paths = [[100.0, 100.0 * (1 + i / 100)] for i in range(64)]
    s = _summary(paths)
    assert s.mu_k == pytest.approx(0.315, abs=1e-3)   # median of 0..0.63
    assert s.q05 < s.q25 < s.q75 < s.q95
    assert s.n_paths == 64


def test_sigma_cone_grows_with_time():
    rng = np.random.default_rng(7)
    # Random walk: dispersion must widen with each step.
    steps = rng.normal(0, 1.0, size=(64, 15))
    paths = 100.0 + np.cumsum(steps, axis=1)
    s = _summary(paths.tolist())
    cone = sorted(s.sigma_cone.items())
    sigmas = [v for _, v in cone]
    assert sigmas[0] < sigmas[-1]
    assert all(v > 0 for v in sigmas)
    # Terminal knot is at the full horizon: 15 daily bars = 15 days.
    assert cone[-1][0] == pytest.approx(15.0)


def test_input_validation():
    with pytest.raises(ValueError, match="last_price"):
        _summary([[100.0, 101.0]], last_price=0.0)
    with pytest.raises(ValueError, match="shape"):
        summarize_paths(np.zeros(5), 100.0, 24.0, instrument="X", ts=NOW,
                        horizon_days=1, granularity="1d", model="m")
