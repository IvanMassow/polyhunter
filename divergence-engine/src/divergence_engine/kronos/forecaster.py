"""Kronos integration (https://github.com/shiyu-coder/Kronos).

Uses the repo's KronosPredictor API with Kronos-Tokenizer + Kronos-small from
Hugging Face. Sampling S=64 future paths per (instrument, horizon), reduced to
mu_K / sigma cone / quantiles and persisted. Kronos alone never opens a trade
(spec §5.5) — this module only ever produces ForecastSummary rows.
"""

from __future__ import annotations

from datetime import datetime

import numpy as np
import pandas as pd

from ..config import HORIZONS_DAYS, KronosConfig
from ..types import ForecastSummary

MODEL_REPOS = {
    "kronos-small": ("NeoQuasar/Kronos-Tokenizer-base", "NeoQuasar/Kronos-small"),
    "kronos-base": ("NeoQuasar/Kronos-Tokenizer-base", "NeoQuasar/Kronos-base"),
}

GRANULARITY_HOURS = {"1h": 1, "4h": 4, "1d": 24}


def summarize_paths(
    paths: np.ndarray,
    last_price: float,
    bar_hours: float,
    *,
    instrument: str,
    ts: datetime,
    horizon_days: int,
    granularity: str,
    model: str,
) -> ForecastSummary:
    """Reduce S sampled close-price paths (shape [S, T]) to a ForecastSummary.

    Returns are computed against last_price. The sigma cone is the std of
    path returns at a handful of elapsed-time knots, so priced_z lookups can
    interpolate sigma_K(Δt) for any story age.
    """
    if paths.ndim != 2:
        raise ValueError(f"paths must be [S, T], got shape {paths.shape}")
    if last_price <= 0:
        raise ValueError("last_price must be positive")
    returns = paths / last_price - 1.0
    terminal = returns[:, -1]

    n_steps = paths.shape[1]
    sigma_cone: dict[float, float] = {}
    # ~8 knots across the horizon, always including the terminal step.
    knot_steps = sorted({max(1, round(n_steps * f)) for f in (0.125, 0.25, 0.375, 0.5, 0.625, 0.75, 0.875, 1.0)})
    for step in knot_steps:
        days = step * bar_hours / 24.0
        sigma_cone[round(days, 4)] = float(np.std(returns[:, step - 1], ddof=1))

    q05, q25, q50, q75, q95 = np.quantile(terminal, [0.05, 0.25, 0.5, 0.75, 0.95])
    return ForecastSummary(
        instrument=instrument,
        ts=ts,
        horizon_days=horizon_days,
        granularity=granularity,
        model=model,
        n_paths=paths.shape[0],
        last_price=float(last_price),
        mu_k=float(q50),
        q05=float(q05),
        q25=float(q25),
        q75=float(q75),
        q95=float(q95),
        sigma_cone=sigma_cone,
    )


class KronosForecaster:
    """Thin wrapper around KronosPredictor. Heavy deps load on first use."""

    def __init__(self, config: KronosConfig | None = None):
        self.config = config or KronosConfig()
        self._predictor = None

    def _load(self):
        if self._predictor is not None:
            return self._predictor
        try:
            from model import Kronos, KronosPredictor, KronosTokenizer  # Kronos repo package
        except ImportError as e:  # pragma: no cover
            raise ImportError(
                "Kronos is not installed. Clone https://github.com/shiyu-coder/Kronos "
                "and add it to PYTHONPATH (plus pip install 'divergence-engine[kronos]')."
            ) from e
        tok_repo, model_repo = MODEL_REPOS[self.config.model]
        tokenizer = KronosTokenizer.from_pretrained(tok_repo)
        model = Kronos.from_pretrained(model_repo)
        self._predictor = KronosPredictor(
            model, tokenizer, device=self.config.device, max_context=self.config.context_len
        )
        return self._predictor

    def forecast(
        self,
        instrument: str,
        ohlcv: pd.DataFrame,
        granularity: str,
        now: datetime,
        horizons: tuple[int, ...] = HORIZONS_DAYS,
    ) -> list[ForecastSummary]:
        """Sample S paths per horizon and return one summary per horizon.

        ohlcv: tz-aware indexed open/high/low/close/volume frame, most recent
        bar last; for futures this must be the *adjusted* continuous series.
        """
        predictor = self._load()
        bar_hours = GRANULARITY_HOURS[granularity]
        ctx = ohlcv.tail(self.config.context_len)
        last_price = float(ctx["close"].iloc[-1])
        out: list[ForecastSummary] = []
        for horizon_days in horizons:
            pred_len = max(1, int(horizon_days * 24 / bar_hours))
            timestamps = pd.Series(ctx.index)
            future_index = pd.date_range(
                ctx.index[-1], periods=pred_len + 1, freq=f"{bar_hours}h"
            )[1:]
            sample_paths = []
            for _ in range(self.config.n_paths):
                pred = predictor.predict(
                    df=ctx[["open", "high", "low", "close", "volume"]].reset_index(drop=True),
                    x_timestamp=timestamps,
                    y_timestamp=pd.Series(future_index),
                    pred_len=pred_len,
                    T=1.0,           # repo default temperature
                    top_p=0.9,
                    sample_count=1,
                )
                sample_paths.append(pred["close"].to_numpy())
            paths = np.stack(sample_paths)
            out.append(
                summarize_paths(
                    paths,
                    last_price,
                    bar_hours,
                    instrument=instrument,
                    ts=now,
                    horizon_days=horizon_days,
                    granularity=granularity,
                    model=self.config.model,
                )
            )
        return out
