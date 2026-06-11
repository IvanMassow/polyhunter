"""The continuous-series construction is the classic data bug: these tests pin
roll timing and back-adjustment against hand-computed prices."""

import pandas as pd
import pytest

from divergence_engine.marketdata.futures import build_continuous_series


def _df(days, closes, volumes):
    idx = pd.to_datetime([f"2026-01-{d:02d}" for d in days])
    return pd.DataFrame(
        {
            "open": closes,
            "high": [c + 1 for c in closes],
            "low": [c - 1 for c in closes],
            "close": closes,
            "volume": volumes,
        },
        index=idx,
    )


def test_volume_crossover_roll_and_back_adjustment():
    # M1 trades days 1-5, M2 days 3-8. M2 volume exceeds M1 on day 4.
    m1 = _df([1, 2, 3, 4, 5], closes=[100, 101, 102, 103, 104], volumes=[1000, 1000, 900, 400, 100])
    m2 = _df([3, 4, 5, 6, 7, 8], closes=[105, 107, 108, 109, 110, 111], volumes=[100, 800, 1200, 1500, 1500, 1500])

    out = build_continuous_series({"M1": m1, "M2": m2}, ["M1", "M2"])

    # Roll fires on day 4 (m2 vol 800 > m1 vol 400): day 4 bar comes from M2.
    assert list(out["contract"]) == ["M1", "M1", "M1", "M2", "M2", "M2", "M2", "M2"]
    # Raw stitched closes: M1 days 1-3, then M2 from day 4.
    assert list(out["close"]) == [100, 101, 102, 107, 108, 109, 110, 111]

    # Offset at roll = m2.close(day4) - m1.close(day4) = 107 - 103 = 4.
    # All bars BEFORE the roll are shifted up by 4; bars from M2 unchanged.
    assert list(out["adj_close"]) == [104, 105, 106, 107, 108, 109, 110, 111]
    # No artificial jump at the roll: day3->day4 adjusted move is 107-106=1,
    # matching M1's own 102->103 move plus M2's basis... i.e. continuous.
    assert out["adj_close"].iloc[3] - out["adj_close"].iloc[2] == pytest.approx(1.0)


def test_forced_roll_on_expiry_without_crossover():
    # M1 dies after day 3 while still out-voluming M2: forced roll on day 4.
    m1 = _df([1, 2, 3], closes=[50, 51, 52], volumes=[1000, 1000, 1000])
    m2 = _df([2, 3, 4, 5], closes=[55, 56, 57, 58], volumes=[10, 20, 30, 40])

    out = build_continuous_series({"M1": m1, "M2": m2}, ["M1", "M2"])
    assert list(out["contract"]) == ["M1", "M1", "M1", "M2", "M2"]
    # Offset measured at last overlap (day 3): 56 - 52 = 4.
    assert list(out["adj_close"]) == [54, 55, 56, 57, 58]
    assert list(out["close"]) == [50, 51, 52, 57, 58]


def test_two_rolls_accumulate_offsets():
    m1 = _df([1, 2], closes=[10, 10], volumes=[100, 10])
    m2 = _df([2, 3, 4], closes=[12, 12, 12], volumes=[100, 10, 10])
    m3 = _df([3, 4, 5], closes=[15, 15, 15], volumes=[100, 100, 100])

    out = build_continuous_series({"M1": m1, "M2": m2, "M3": m3}, ["M1", "M2", "M3"])
    # Day1 bar gets both offsets: +2 (M1->M2) then +3 (M2->M3) = 15.
    assert list(out["close"]) == [10, 12, 15, 15, 15]
    assert list(out["adj_close"]) == [15, 15, 15, 15, 15]


def test_roll_confirmation_bars():
    # One-bar volume blip on day 2 must NOT roll when confirmation = 2 bars.
    m1 = _df([1, 2, 3, 4], closes=[100, 100, 100, 100], volumes=[1000, 500, 1000, 400])
    m2 = _df([2, 3, 4, 5], closes=[110, 110, 110, 110], volumes=[600, 900, 500, 800])

    out = build_continuous_series({"M1": m1, "M2": m2}, ["M1", "M2"], roll_confirm_bars=2)
    # Crossovers: day2 yes, day3 no (resets), day4 yes -> still only 1 in a row
    # at day4... wait: day3 m2=900 < m1=1000 resets; day4 m2=500 > m1=400 is 1.
    assert list(out["contract"]) == ["M1", "M1", "M1", "M1", "M2"]


def test_validation_errors():
    good = _df([1], [1], [1])
    with pytest.raises(ValueError, match="missing data"):
        build_continuous_series({"M1": good}, ["M1", "M2"])
    with pytest.raises(ValueError, match="missing columns"):
        build_continuous_series({"M1": good.drop(columns=["volume"])}, ["M1"])
