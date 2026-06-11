from datetime import datetime, timedelta, timezone

import pytest

from divergence_engine.config import RiskConfig
from divergence_engine.risk.governor import RiskGovernor, size_position
from divergence_engine.types import Candidate, ForecastSummary, Ticket

NOW = datetime(2026, 6, 10, tzinfo=timezone.utc)


def _forecast(sigma5=0.05):
    return ForecastSummary(
        instrument="BRENT", ts=NOW, horizon_days=5, granularity="1d",
        model="kronos-small", n_paths=64, last_price=100.0, mu_k=0.01,
        q05=-0.08, q25=-0.03, q75=0.04, q95=0.09,
        sigma_cone={1.0: 0.02, 5.0: sigma5},
    )


def _cand(instrument="BRENT", direction=1, size_factor=1.0):
    c = Candidate(candidate_id="c", instrument=instrument, direction=direction,
                  horizon_days=5, t0=NOW, spawned_at=NOW, trigger_feeds={"E1": 0.9})
    c.size_factor = size_factor
    c.cluster_id = "cl_x"
    return c


def _ticket(instrument="BRENT", risk=500.0):
    return Ticket(ticket_id="t", candidate_id="c", cluster_id="cl_x",
                  instrument=instrument, direction=1, entry_ts=NOW, entry_price=100.0,
                  size=1.0, risk_amount=risk, stop_price=94.0, target_q75=104.0,
                  target_q95=109.0, time_stop_at=NOW + timedelta(days=8))


def test_sizing_math():
    cfg = RiskConfig(paper_nav=100_000.0)
    plan = size_position(_cand(), 100.0, NOW, _forecast(), p_narr=0.5, nav=100_000.0, config=cfg)
    # stop = 1.2 * sigma(5d)=0.05 -> 6% -> stop at 94; risk = 0.5% NAV = 500.
    assert plan.stop_price == pytest.approx(94.0)
    assert plan.risk_amount == pytest.approx(500.0)
    assert plan.size == pytest.approx(500.0 / 6.0)
    # time-stop = 1.5 * 5d = 7.5d
    assert plan.time_stop_at == NOW + timedelta(days=7.5)
    # target = q75 + kappa * P_narr = 0.04 + 0.02*0.5 = 0.05 -> 105.
    assert plan.target_q75 == pytest.approx(105.0)


def test_structure_conflict_halves_risk():
    plan = size_position(_cand(size_factor=0.5), 100.0, NOW, _forecast(),
                         p_narr=0.0, nav=100_000.0)
    assert plan.risk_amount == pytest.approx(250.0)


def test_short_side_stops_above_entry():
    plan = size_position(_cand(direction=-1), 100.0, NOW, _forecast(),
                         p_narr=0.0, nav=100_000.0)
    assert plan.stop_price == pytest.approx(106.0)
    assert plan.target_q75 < 100.0


def test_per_instrument_cap():
    gov = RiskGovernor(RiskConfig(per_instrument_max_tickets=2))
    book = [_ticket(), _ticket()]
    verdict = gov.approve(_cand(), 500.0, book, {"BRENT": "energy"}, {})
    assert not verdict.approved and "instrument cap" in verdict.reason


def test_cluster_budget_is_shared():
    gov = RiskGovernor(RiskConfig(paper_nav=100_000.0))
    # Cluster has already used its full 500 budget via another instrument.
    verdict = gov.approve(_cand(instrument="WTI"), 500.0, [], {"WTI": "energy"}, {"cl_x": 500.0})
    assert not verdict.approved and "cluster budget" in verdict.reason

    # Partially used: approve but clamp to the remainder.
    verdict = gov.approve(_cand(instrument="WTI"), 500.0, [], {"WTI": "energy"}, {"cl_x": 300.0})
    assert verdict.approved and verdict.risk_amount == pytest.approx(200.0)


def test_theatre_cap():
    gov = RiskGovernor(RiskConfig(theatre_risk_cap_frac=0.60))
    book = [_ticket("BRENT", 500.0), _ticket("WTI", 500.0)]
    theatres = {"BRENT": "energy", "WTI": "energy", "TTF": "energy", "BTC": "crypto"}
    c = _cand("TTF")
    c.cluster_id = "cl_other"
    verdict = gov.approve(c, 500.0, book, theatres, {})
    assert not verdict.approved and "theatre cap" in verdict.reason

    b = _cand("BTC")
    b.cluster_id = "cl_btc"
    verdict = gov.approve(b, 500.0, book, theatres, {})
    assert verdict.approved


def test_first_ticket_always_passes_theatre_cap():
    gov = RiskGovernor(RiskConfig())
    verdict = gov.approve(_cand(), 500.0, [], {"BRENT": "energy"}, {})
    assert verdict.approved


def test_kill_switch_drawdown():
    gov = RiskGovernor(RiskConfig(kill_switch_drawdown_frac=0.10))
    assert gov.kill_switch_active([], nav_peak=100_000, nav_now=89_000) is not None
    assert gov.kill_switch_active([], nav_peak=100_000, nav_now=95_000) is None


def test_kill_switch_consecutive_cluster_losses():
    gov = RiskGovernor(RiskConfig(kill_switch_consecutive_cluster_losses=3))
    losses = [("a", -100.0), ("b", -50.0), ("c", -75.0)]
    assert gov.kill_switch_active(losses, 100_000, 100_000) is not None
    mixed = [("a", -100.0), ("b", 200.0), ("c", -75.0), ("d", -10.0)]
    assert gov.kill_switch_active(mixed, 100_000, 100_000) is None
    # Two tickets in one cluster netting positive is not a cluster loss.
    netted = [("a", -100.0), ("a", 300.0), ("b", -50.0), ("c", -10.0)]
    assert gov.kill_switch_active(netted, 100_000, 100_000) is None
