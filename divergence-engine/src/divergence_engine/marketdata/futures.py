"""Continuous front-month futures series with explicit roll handling.

Rules (build spec §5):
- Roll on volume crossover: switch to the next contract on the first bar where
  the next contract's volume exceeds the current front's volume.
- Back-adjust additively (Panama method): at each roll, all *earlier* bars are
  shifted by (next.close - front.close) at the roll bar, so the adjusted series
  has no artificial jump at the roll.
- Store both raw (stitched, unadjusted) and adjusted prices — and which
  contract each bar came from.

This is the classic data bug; tests pin the behaviour against hand-computed
prices (tests/test_futures_roll.py).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import pandas as pd

OHLC = ("open", "high", "low", "close")


@dataclass(frozen=True)
class ContractBar:
    ts: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float


def build_continuous_series(
    contracts: dict[str, pd.DataFrame],
    contract_order: list[str],
    roll_confirm_bars: int = 1,
) -> pd.DataFrame:
    """Stitch per-contract OHLCV frames into one continuous front-month series.

    contracts: contract symbol -> DataFrame indexed by ts with columns
               open/high/low/close/volume. Bars must be aligned on the same
               timestamps where contracts overlap.
    contract_order: symbols in expiry order (earliest first).
    roll_confirm_bars: number of consecutive bars the next contract's volume
               must exceed the front's before rolling (1 = roll immediately).

    Returns a DataFrame indexed by ts with columns:
      open/high/low/close/volume  -- raw stitched prices from the active contract
      adj_open/adj_high/adj_low/adj_close -- back-adjusted prices
      contract -- the contract each bar came from
    """
    if not contract_order:
        raise ValueError("contract_order is empty")
    for sym in contract_order:
        if sym not in contracts:
            raise ValueError(f"missing data for contract {sym}")
        df = contracts[sym]
        missing = [c for c in (*OHLC, "volume") if c not in df.columns]
        if missing:
            raise ValueError(f"{sym}: missing columns {missing}")
        if not df.index.is_monotonic_increasing:
            raise ValueError(f"{sym}: bars not sorted by ts")

    all_ts = sorted(set().union(*(set(contracts[s].index) for s in contract_order)))

    rows: list[dict] = []
    roll_offsets: list[tuple[int, float]] = []  # (row index of first bar on new contract, offset)
    active_i = 0
    confirm_count = 0

    for ts in all_ts:
        active = contract_order[active_i]
        nxt = contract_order[active_i + 1] if active_i + 1 < len(contract_order) else None

        have_active = ts in contracts[active].index
        have_next = nxt is not None and ts in contracts[nxt].index

        rolled = False
        if have_next:
            if have_active:
                front_vol = contracts[active].at[ts, "volume"]
                next_vol = contracts[nxt].at[ts, "volume"]
                confirm_count = confirm_count + 1 if next_vol > front_vol else 0
                if confirm_count >= roll_confirm_bars:
                    rolled = True
            else:
                # Front contract stopped trading (expiry) before a volume
                # crossover: forced roll.
                rolled = True

        if rolled:
            if have_active:
                offset = (
                    contracts[nxt].at[ts, "close"] - contracts[active].at[ts, "close"]
                )
            else:
                # No overlapping bar to measure the gap: fall back to the last
                # bar where both traded, else 0 (and the gap stays visible).
                offset = _last_overlap_offset(contracts[active], contracts[nxt])
            roll_offsets.append((len(rows), offset))
            active_i += 1
            active = contract_order[active_i]
            nxt = contract_order[active_i + 1] if active_i + 1 < len(contract_order) else None
            confirm_count = 0
            have_active = ts in contracts[active].index

        if not have_active:
            continue
        bar = contracts[active].loc[ts]
        rows.append(
            {
                "ts": ts,
                "open": float(bar["open"]),
                "high": float(bar["high"]),
                "low": float(bar["low"]),
                "close": float(bar["close"]),
                "volume": float(bar["volume"]),
                "contract": active,
            }
        )

    out = pd.DataFrame(rows).set_index("ts")

    # Back-adjust: every bar before each roll point is shifted by that roll's
    # offset; offsets accumulate when there are multiple rolls.
    for col in OHLC:
        out[f"adj_{col}"] = out[col].astype(float)
    for row_idx, offset in roll_offsets:
        for col in OHLC:
            out.iloc[:row_idx, out.columns.get_loc(f"adj_{col}")] += offset
    return out


def _last_overlap_offset(front: pd.DataFrame, nxt: pd.DataFrame) -> float:
    common = front.index.intersection(nxt.index)
    if len(common) == 0:
        return 0.0
    ts = common[-1]
    return float(nxt.at[ts, "close"] - front.at[ts, "close"])
