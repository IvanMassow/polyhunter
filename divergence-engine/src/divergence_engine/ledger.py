"""Append-only ledger over Postgres (psycopg, lazy import).

Insert-only by construction: this module exposes no UPDATE or DELETE. Ticket
exits are new rows (state='exit'); spec changes are new feed registry rows.
Every decision row carries a sha256 of its canonical input snapshot.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, is_dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from .feeds.spec import FeedSpec
from .types import Candidate, FeedState, ForecastSummary, Ticket


def _canonical(obj: Any) -> Any:
    if is_dataclass(obj) and not isinstance(obj, type):
        return _canonical(asdict(obj))
    if isinstance(obj, dict):
        return {str(k): _canonical(v) for k, v in sorted(obj.items(), key=lambda kv: str(kv[0]))}
    if isinstance(obj, (list, tuple)):
        return [_canonical(x) for x in obj]
    if isinstance(obj, datetime):
        return obj.isoformat()
    if hasattr(obj, "value"):  # enums
        return obj.value
    return obj


def snapshot_hash(snapshot: Any) -> tuple[str, str]:
    """Returns (sha256 hex, canonical json) for a decision input snapshot."""
    blob = json.dumps(_canonical(snapshot), separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(blob.encode()).hexdigest(), blob


class Ledger:
    def __init__(self, dsn: str):
        try:
            import psycopg
        except ImportError as e:  # pragma: no cover
            raise ImportError("psycopg required: pip install 'divergence-engine[db]'") from e
        self._conn = psycopg.connect(dsn, autocommit=True)
        self._conn.execute("SET search_path TO divergence")

    @staticmethod
    def apply_schema(dsn: str, schema_path: str | Path) -> None:
        import psycopg

        with psycopg.connect(dsn, autocommit=True) as conn:
            conn.execute(Path(schema_path).read_text())

    def register_feed(self, spec: FeedSpec, version: int = 1) -> None:
        self._conn.execute(
            "INSERT INTO feeds (feed_id, spec_version, mechanism, spec_yaml, spec_hash, theatre)"
            " VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT (feed_id, spec_version) DO NOTHING",
            (spec.feed_id, version, spec.mechanism, spec.spec_yaml, spec.spec_hash, spec.theatre.value),
        )

    def write_feed_state(self, state: FeedState, raw: dict | None = None) -> None:
        self._conn.execute(
            "INSERT INTO feed_state (feed_id, ts, direction, velocity, coverage,"
            " contradiction, freshness, source, raw)"
            " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (
                state.feed_id, state.ts, state.direction, state.velocity, state.coverage,
                state.contradiction, state.freshness, state.source,
                json.dumps(raw) if raw is not None else None,
            ),
        )

    def write_forecast(self, f: ForecastSummary) -> None:
        self._conn.execute(
            "INSERT INTO kronos_forecasts (instrument, ts, horizon_days, granularity,"
            " model, n_paths, last_price, mu_k, q05, q25, q75, q95, sigma_cone)"
            " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (
                f.instrument, f.ts, f.horizon_days, f.granularity, f.model, f.n_paths,
                f.last_price, f.mu_k, f.q05, f.q25, f.q75, f.q95,
                json.dumps({str(k): v for k, v in f.sigma_cone.items()}),
            ),
        )

    def write_candidate(self, c: Candidate) -> None:
        self._conn.execute(
            "INSERT INTO candidates (candidate_id, instrument, direction, horizon_days,"
            " t0, spawned_at, cluster_id, trigger_feeds, p_narr, priced_z, divergence,"
            " threshold_used, size_factor, status, kill_reason)"
            " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"
            " ON CONFLICT (candidate_id) DO NOTHING",
            (
                c.candidate_id, c.instrument, c.direction, c.horizon_days, c.t0,
                c.spawned_at, c.cluster_id, json.dumps(c.trigger_feeds), c.p_narr,
                c.priced_z, c.divergence, c.threshold_used, c.size_factor, c.status,
                c.kill_reason.value if c.kill_reason else None,
            ),
        )

    def write_ticket_entry(self, t: Ticket) -> None:
        self._conn.execute(
            "INSERT INTO tickets (ticket_id, state, candidate_id, cluster_id, instrument,"
            " direction, ts, price, size, risk_amount, stop_price, target_q75, target_q95,"
            " time_stop_at)"
            " VALUES (%s, 'entry', %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (
                t.ticket_id, t.candidate_id, t.cluster_id, t.instrument, t.direction,
                t.entry_ts, t.entry_price, t.size, t.risk_amount, t.stop_price,
                t.target_q75, t.target_q95, t.time_stop_at,
            ),
        )

    def write_ticket_exit(self, t: Ticket) -> None:
        if t.exit_ts is None or t.exit_reason is None:
            raise ValueError("ticket has no exit to record")
        self._conn.execute(
            "INSERT INTO tickets (ticket_id, state, candidate_id, cluster_id, instrument,"
            " direction, ts, price, exit_reason, realised_pnl)"
            " VALUES (%s, 'exit', %s, %s, %s, %s, %s, %s, %s, %s)",
            (
                t.ticket_id, t.candidate_id, t.cluster_id, t.instrument, t.direction,
                t.exit_ts, t.exit_price, t.exit_reason.value, t.realised_pnl,
            ),
        )

    # --- reads (queries only; nothing here mutates) ---

    def feed_history(self, feed_id: str, since: datetime) -> list[FeedState]:
        rows = self._conn.execute(
            "SELECT feed_id, ts, direction, velocity, coverage, contradiction, freshness, source"
            " FROM feed_state WHERE feed_id = %s AND ts >= %s ORDER BY ts",
            (feed_id, since),
        ).fetchall()
        return [FeedState(*row) for row in rows]

    def latest_forecast(self, instrument: str, horizon_days: int) -> ForecastSummary | None:
        row = self._conn.execute(
            "SELECT instrument, ts, horizon_days, granularity, model, n_paths, last_price,"
            " mu_k, q05, q25, q75, q95, sigma_cone FROM kronos_forecasts"
            " WHERE instrument = %s AND horizon_days = %s ORDER BY ts DESC LIMIT 1",
            (instrument, horizon_days),
        ).fetchone()
        if row is None:
            return None
        *head, cone = row
        cone = cone if isinstance(cone, dict) else json.loads(cone)
        return ForecastSummary(*head, sigma_cone={float(k): float(v) for k, v in cone.items()})

    def close_at(self, instrument: str, ts: datetime) -> float | None:
        """Latest close at or before ts. Prefers the back-adjusted continuous
        series (futures) so priced_z never spans a roll jump; falls back to raw."""
        for series in ("continuous_adjusted", "raw"):
            row = self._conn.execute(
                "SELECT close FROM ohlcv WHERE instrument = %s AND series = %s AND ts <= %s"
                " ORDER BY ts DESC LIMIT 1",
                (instrument, series, ts),
            ).fetchone()
            if row is not None:
                return float(row[0])
        return None

    def write_ohlcv(
        self,
        instrument: str,
        series: str,
        granularity: str,
        bars,  # DataFrame indexed by ts with open/high/low/close/volume [, contract]
    ) -> None:
        with self._conn.cursor() as cur:
            for ts, bar in bars.iterrows():
                cur.execute(
                    "INSERT INTO ohlcv (instrument, series, granularity, ts, open, high,"
                    " low, close, volume, contract) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"
                    " ON CONFLICT (instrument, series, granularity, ts) DO NOTHING",
                    (
                        instrument, series, granularity, ts,
                        float(bar["open"]), float(bar["high"]), float(bar["low"]),
                        float(bar["close"]), float(bar["volume"]),
                        bar.get("contract") if hasattr(bar, "get") else None,
                    ),
                )

    def recent_abs_divergences(self, since: datetime) -> list[float]:
        rows = self._conn.execute(
            "SELECT abs(divergence) FROM candidates"
            " WHERE spawned_at >= %s AND divergence IS NOT NULL",
            (since,),
        ).fetchall()
        return [r[0] for r in rows]

    def write_decision(
        self,
        ts: datetime,
        stage: str,
        decision: str,
        snapshot: Any,
        candidate_id: str | None = None,
        detail: dict | None = None,
    ) -> str:
        h, blob = snapshot_hash(snapshot)
        self._conn.execute(
            "INSERT INTO decisions (ts, stage, candidate_id, decision, detail, input_hash, input_snapshot)"
            " VALUES (%s, %s, %s, %s, %s, %s, %s)",
            (ts, stage, candidate_id, decision, json.dumps(detail) if detail else None, h, blob),
        )
        return h
