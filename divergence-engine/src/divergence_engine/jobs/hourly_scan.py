"""Hourly scan: feeds -> triggers -> spawn -> corroborate -> gates -> score.

Phase 0 scope: produces candidate cards (and would-be tickets) into the
ledger. Ticket entries are written only when DE_OPEN_TICKETS=1; exits stay
manual until Phase 1.

Run from cron, NOT on the Predict production cluster:
    0 * * * *  de-hourly-scan --config /etc/divergence/config.yaml
"""

from __future__ import annotations

import argparse
import logging
import os
import uuid
from datetime import datetime, timedelta, timezone

from ..config import EngineConfig
from ..feeds import NoahInternalFeedAPI, NoahReportPolling, load_feed_specs
from ..funnel import (
    apply_kronos_gates,
    calibrate_theta,
    divergence_score,
    is_corroborated,
    narrative_pressure,
    scan_triggers,
    spawn_candidates,
)
from ..funnel.score import passes_threshold
from ..ledger import Ledger
from ..marketdata.universe import theatre_of
from ..risk import RiskGovernor, assign_clusters, size_position
from ..types import Candidate, KillReason, Ticket

log = logging.getLogger("divergence.hourly_scan")


def _feed_source(cfg: EngineConfig):
    base_url = os.environ.get("NOAH_API_URL")
    token = os.environ.get("NOAH_API_TOKEN")
    if base_url and token:
        return NoahInternalFeedAPI(base_url, token)
    packages_dir = os.environ.get("NOAH_PACKAGES_DIR", "noah_packages")
    return NoahReportPolling(packages_dir)


def run_scan(cfg: EngineConfig, now: datetime | None = None) -> list[Candidate]:
    now = now or datetime.now(timezone.utc)
    specs = load_feed_specs(cfg.feeds_dir)
    ledger = Ledger(cfg.db_dsn)
    source = _feed_source(cfg)

    # 0. Ingest the hour's feed emissions.
    latest_states = {}
    for spec in specs.values():
        try:
            state = source.fetch(spec, now)
        except Exception:
            log.exception("feed fetch failed: %s", spec.feed_id)
            continue
        if state is not None:
            ledger.write_feed_state(state)
            latest_states[spec.feed_id] = state

    # 1. Trigger scan over each feed's trailing history.
    triggers = []
    since = now - timedelta(days=cfg.triggers.velocity_lookback_days + 1)
    for feed_id in latest_states:
        history = ledger.feed_history(feed_id, since)
        fired = scan_triggers(history, cfg.triggers)
        for t in fired:
            ledger.write_decision(now, "trigger", t.kind, {"feed": feed_id, "state": t.state}, detail={"direction": t.direction})
        triggers.extend(fired)

    # 2. Spawn candidate cards.
    candidates = spawn_candidates(triggers, specs, now)

    # 3. Corroboration. 4. Kronos gates. 5. Score.
    survivors: list[Candidate] = []
    for cand in candidates:
        ok, supporting = is_corroborated(cand, latest_states, specs)
        cand.trigger_feeds = supporting
        if not ok:
            cand.status, cand.kill_reason = "killed", KillReason.NO_CORROBORATION
            ledger.write_candidate(cand)
            ledger.write_decision(now, "corroborate", "kill", cand, candidate_id=cand.candidate_id)
            continue

        forecast = ledger.latest_forecast(cand.instrument, cand.horizon_days)
        p_now = ledger.close_at(cand.instrument, now)
        p_t0 = ledger.close_at(cand.instrument, cand.t0)
        if forecast is None or p_now is None or p_t0 is None:
            log.warning("missing forecast/price for %s; skipping", cand.instrument)
            continue
        elapsed_days = max((now - cand.t0).total_seconds() / 86400.0, 1e-3)
        gate = apply_kronos_gates(cand, p_now, p_t0, elapsed_days, forecast, cfg.gates)
        cand.priced_z = gate.priced_z
        cand.size_factor = gate.size_factor
        if gate.killed:
            cand.status, cand.kill_reason = "killed", gate.kill_reason
            ledger.write_candidate(cand)
            ledger.write_decision(now, "gate", "kill", cand, candidate_id=cand.candidate_id,
                                  detail={"priced_z": gate.priced_z})
            continue

        cand.p_narr = narrative_pressure(cand, latest_states, specs)
        cand.divergence = divergence_score(cand.p_narr, cand.priced_z, cfg.score)
        theta = calibrate_theta(
            ledger.recent_abs_divergences(now - timedelta(days=30)), cfg.score
        )
        cand.threshold_used = theta * gate.threshold_mult
        if not passes_threshold(cand.divergence, theta, gate.threshold_mult):
            cand.status, cand.kill_reason = "below_threshold", KillReason.BELOW_THRESHOLD
            ledger.write_candidate(cand)
            ledger.write_decision(now, "score", "below_threshold", cand, candidate_id=cand.candidate_id)
            continue
        survivors.append(cand)

    # Cluster survivors, then run the governor over each would-be ticket.
    assign_clusters(survivors, cfg.risk.cluster_overlap_min)
    governor = RiskGovernor(cfg.risk)
    open_tickets: list[Ticket] = []      # Phase 0: hand-managed book, start empty
    cluster_risk_used: dict[str, float] = {}
    theatres = {c.instrument: theatre_of(c.instrument, alt_basket=[c.instrument]) for c in survivors}

    for cand in sorted(survivors, key=lambda c: -abs(c.divergence or 0)):
        forecast = ledger.latest_forecast(cand.instrument, cand.horizon_days)
        p_now = ledger.close_at(cand.instrument, now)
        plan = size_position(
            cand, p_now, now, forecast, cand.p_narr, cfg.risk.paper_nav, cfg.risk,
            cfg.score.narrative_shift_kappa,
        )
        verdict = governor.approve(cand, plan.risk_amount, open_tickets, theatres, cluster_risk_used)
        ledger.write_decision(now, "risk", "open" if verdict.approved else "kill", cand,
                              candidate_id=cand.candidate_id, detail={"reason": verdict.reason})
        if not verdict.approved:
            cand.status, cand.kill_reason = "killed", KillReason.RISK_CAP
            ledger.write_candidate(cand)
            continue

        cand.status = "open_ticket"
        ledger.write_candidate(cand)
        ticket = Ticket(
            ticket_id=uuid.uuid4().hex[:12],
            candidate_id=cand.candidate_id,
            cluster_id=cand.cluster_id,
            instrument=cand.instrument,
            direction=cand.direction,
            entry_ts=now,
            entry_price=p_now,
            size=plan.size,
            risk_amount=verdict.risk_amount,
            stop_price=plan.stop_price,
            target_q75=plan.target_q75,
            target_q95=plan.target_q95,
            time_stop_at=plan.time_stop_at,
        )
        cluster_risk_used[cand.cluster_id] = cluster_risk_used.get(cand.cluster_id, 0.0) + verdict.risk_amount
        open_tickets.append(ticket)
        if os.environ.get("DE_OPEN_TICKETS") == "1":
            ledger.write_ticket_entry(ticket)
        else:
            log.info("candidate card ready (tickets manual in Phase 0): %s %s D=%.3f",
                     cand.instrument, "long" if cand.direction > 0 else "short", cand.divergence)
    return survivors


def main() -> None:
    parser = argparse.ArgumentParser(description="Divergence Engine hourly scan")
    parser.add_argument("--config", default=None)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    cfg = EngineConfig.load(args.config)
    survivors = run_scan(cfg)
    log.info("scan complete: %d candidates passed the funnel", len(survivors))


if __name__ == "__main__":
    main()
