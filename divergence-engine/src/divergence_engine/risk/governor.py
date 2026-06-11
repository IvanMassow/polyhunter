"""Risk / portfolio governor (spec §7) and vol-aware sizing (spec §5.4).

- Per-trade risk: 0.5% of paper NAV.
- Per-instrument cap: 2 concurrent tickets.
- Theatre cap: 60% of open risk in one theatre.
- Same-story limiter: one risk budget per story cluster.
- Global kill switch: 3 consecutive cluster-level losses or 10% paper
  drawdown -> flat, post-mortem before restart.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from ..config import RiskConfig
from ..types import Candidate, ForecastSummary, Ticket


@dataclass(frozen=True)
class PositionPlan:
    size: float
    risk_amount: float
    stop_price: float
    target_q75: float
    target_q95: float
    time_stop_at: datetime


def size_position(
    candidate: Candidate,
    entry_price: float,
    entry_ts: datetime,
    forecast: ForecastSummary,
    p_narr: float,
    nav: float,
    config: RiskConfig | None = None,
    narrative_shift_kappa: float = 0.02,
) -> PositionPlan:
    """Stop = 1.2 x sigma_K(h); size = risk budget / stop distance;
    time-stop = 1.5 x horizon; targets at q75/q95 of the narrative-shifted
    distribution (Kronos quantiles shifted by kappa x P_narr in return space)."""
    cfg = config or RiskConfig()
    sigma_h = forecast.sigma_at(float(candidate.horizon_days))
    stop_return = cfg.stop_sigma_mult * sigma_h
    if stop_return <= 0:
        raise ValueError("non-positive stop distance")
    stop_distance = entry_price * stop_return
    risk_amount = nav * cfg.per_trade_risk_frac * candidate.size_factor
    size = risk_amount / stop_distance

    shift = narrative_shift_kappa * p_narr  # signed, in return space
    if candidate.direction > 0:
        stop_price = entry_price * (1 - stop_return)
        target_q75 = entry_price * (1 + max(forecast.q75 + shift, stop_return * 0.5))
        target_q95 = entry_price * (1 + max(forecast.q95 + shift, stop_return))
    else:
        stop_price = entry_price * (1 + stop_return)
        target_q75 = entry_price * (1 + min(forecast.q25 + shift, -stop_return * 0.5))
        target_q95 = entry_price * (1 + min(forecast.q05 + shift, -stop_return))

    return PositionPlan(
        size=size,
        risk_amount=risk_amount,
        stop_price=stop_price,
        target_q75=target_q75,
        target_q95=target_q95,
        time_stop_at=entry_ts + timedelta(days=cfg.time_stop_horizon_mult * candidate.horizon_days),
    )


@dataclass(frozen=True)
class GovernorDecision:
    approved: bool
    reason: str
    risk_amount: float = 0.0


class RiskGovernor:
    """Stateless checks over the current open book + realised history."""

    def __init__(self, config: RiskConfig | None = None):
        self.cfg = config or RiskConfig()

    def kill_switch_active(
        self,
        cluster_realised: list[tuple[str, float]],  # (cluster_id, pnl) in close order
        nav_peak: float,
        nav_now: float,
    ) -> str | None:
        """Returns the tripped condition, or None. Once tripped: flat, and a
        human post-mortem before restart — there is no auto-reset here."""
        if nav_peak > 0 and (nav_peak - nav_now) / nav_peak >= self.cfg.kill_switch_drawdown_frac:
            return f"drawdown >= {self.cfg.kill_switch_drawdown_frac:.0%}"
        # Consecutive *cluster-level* losses: aggregate per cluster in close
        # order, then look at the tail.
        seen: dict[str, float] = {}
        order: list[str] = []
        for cid, pnl in cluster_realised:
            if cid not in seen:
                order.append(cid)
                seen[cid] = 0.0
            seen[cid] += pnl
        tail = [seen[cid] for cid in order][-self.cfg.kill_switch_consecutive_cluster_losses:]
        if (
            len(tail) == self.cfg.kill_switch_consecutive_cluster_losses
            and all(p < 0 for p in tail)
        ):
            return f"{self.cfg.kill_switch_consecutive_cluster_losses} consecutive cluster losses"
        return None

    def approve(
        self,
        candidate: Candidate,
        proposed_risk: float,
        open_tickets: list[Ticket],
        theatre_of: dict[str, str],
        cluster_risk_used: dict[str, float],
    ) -> GovernorDecision:
        cfg = self.cfg
        nav_risk_budget = cfg.paper_nav * cfg.per_trade_risk_frac

        # Per-instrument concurrent ticket cap.
        n_inst = sum(1 for t in open_tickets if t.instrument == candidate.instrument and t.is_open)
        if n_inst >= cfg.per_instrument_max_tickets:
            return GovernorDecision(False, f"instrument cap: {n_inst} open on {candidate.instrument}")

        # Same-story limiter: the cluster's budget is one per-trade budget,
        # shared across every instrument expressing the story.
        used = cluster_risk_used.get(candidate.cluster_id or "", 0.0)
        remaining = nav_risk_budget - used
        if remaining <= 0:
            return GovernorDecision(False, f"cluster budget exhausted: {candidate.cluster_id}")
        risk = min(proposed_risk, remaining)

        # Theatre cap: 60% of total open risk in one theatre.
        theatre = theatre_of.get(candidate.instrument)
        open_risk = sum(t.risk_amount for t in open_tickets if t.is_open)
        theatre_risk = sum(
            t.risk_amount
            for t in open_tickets
            if t.is_open and theatre_of.get(t.instrument) == theatre
        )
        total_after = open_risk + risk
        if total_after > 0 and (theatre_risk + risk) / total_after > cfg.theatre_risk_cap_frac:
            # A first ticket always passes (one ticket is 100% of its own
            # theatre); the cap binds once a real book exists.
            if open_risk > 0:
                return GovernorDecision(False, f"theatre cap: {theatre}")

        return GovernorDecision(True, "ok", risk_amount=risk)
