"""Stage 4 — Kronos gates: already-priced kill and structure-conflict adjustment."""

from __future__ import annotations

from dataclasses import dataclass

from ..config import GateConfig
from ..types import Candidate, ForecastSummary, KillReason


def priced_z(
    p_now: float,
    p_t0: float,
    elapsed_days: float,
    direction: int,
    forecast: ForecastSummary,
) -> float:
    """How much of the story the chart has already eaten, in Kronos vol units.

    Computed in return space: signed move since story onset divided by the
    path-implied vol cone at the story's age. Positive = price has already
    moved the way the signal points.
    """
    if p_t0 <= 0:
        raise ValueError("p_t0 must be positive")
    sigma = forecast.sigma_at(elapsed_days)
    if sigma <= 1e-12:
        raise ValueError(f"degenerate sigma cone for {forecast.instrument}")
    move = p_now / p_t0 - 1.0
    return direction * move / sigma


@dataclass(frozen=True)
class GateResult:
    killed: bool
    kill_reason: KillReason | None
    priced_z: float
    threshold_mult: float   # 1.0 normally, x1.5 on structure conflict
    size_factor: float      # 1.0 normally, x0.5 on structure conflict


def apply_kronos_gates(
    candidate: Candidate,
    p_now: float,
    p_t0: float,
    elapsed_days: float,
    forecast: ForecastSummary,
    config: GateConfig | None = None,
) -> GateResult:
    cfg = config or GateConfig()

    z = priced_z(p_now, p_t0, elapsed_days, candidate.direction, forecast)
    if z > cfg.priced_z_kill:
        # The chart already ate the story. Kill, keep in ledger; these labels
        # train future timing.
        return GateResult(True, KillReason.TOO_LATE, z, 1.0, 1.0)

    # Structure conflict: narrative long into a distribution skewed short
    # (q75 < 0), or narrative short into one skewed long (q25 > 0). We don't
    # fight strong structure on equal terms: raise the bar, halve the size.
    conflicted = (candidate.direction > 0 and forecast.q75 < 0) or (
        candidate.direction < 0 and forecast.q25 > 0
    )
    if conflicted:
        return GateResult(
            False, None, z, cfg.structure_conflict_threshold_mult, cfg.structure_conflict_size_mult
        )
    return GateResult(False, None, z, 1.0, 1.0)
