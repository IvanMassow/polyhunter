"""Static tracker page, polyhunter-style. Private during paper trading.

Honesty stats up front: n trades, n independent clusters, profit factor,
max drawdown. Then equity curve (inline SVG, no JS), open tickets, closed
trades with story labels and exit reasons, per-cluster and per-theatre
breakdowns. Pure render functions; main() pulls from the ledger.
"""

from __future__ import annotations

import argparse
import html
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path


@dataclass
class ClosedTrade:
    instrument: str
    direction: int
    cluster_id: str
    story_label: str
    theatre: str
    entry_ts: datetime
    exit_ts: datetime
    entry_price: float
    exit_price: float
    pnl: float
    exit_reason: str


@dataclass
class OpenTicketView:
    instrument: str
    direction: int
    story_label: str
    entry_ts: datetime
    entry_price: float
    current_price: float
    unrealised_pnl: float


@dataclass
class TrackerData:
    nav_start: float
    closed: list[ClosedTrade] = field(default_factory=list)
    open_tickets: list[OpenTicketView] = field(default_factory=list)
    generated_at: datetime | None = None


def honesty_stats(data: TrackerData) -> dict:
    closed = sorted(data.closed, key=lambda t: t.exit_ts)
    wins = [t.pnl for t in closed if t.pnl > 0]
    losses = [t.pnl for t in closed if t.pnl < 0]
    pf = (sum(wins) / abs(sum(losses))) if losses else float("inf") if wins else 0.0

    equity, peak, max_dd = data.nav_start, data.nav_start, 0.0
    curve = [equity]
    for t in closed:
        equity += t.pnl
        curve.append(equity)
        peak = max(peak, equity)
        if peak > 0:
            max_dd = max(max_dd, (peak - equity) / peak)

    return {
        "n_trades": len(closed),
        "n_clusters": len({t.cluster_id for t in closed}),
        "profit_factor": round(pf, 2) if pf != float("inf") else None,
        "max_drawdown_pct": round(max_dd * 100, 1),
        "total_pnl": round(sum(t.pnl for t in closed), 2),
        "equity_curve": curve,
    }


def _svg_equity(curve: list[float], w: int = 720, h: int = 180) -> str:
    if len(curve) < 2:
        return "<p class='muted'>No closed trades yet.</p>"
    lo, hi = min(curve), max(curve)
    span = (hi - lo) or 1.0
    pts = " ".join(
        f"{i * w / (len(curve) - 1):.1f},{h - (v - lo) / span * (h - 10) - 5:.1f}"
        for i, v in enumerate(curve)
    )
    return (
        f"<svg viewBox='0 0 {w} {h}' width='100%' height='{h}'>"
        f"<polyline fill='none' stroke='#2a7' stroke-width='2' points='{pts}'/></svg>"
    )


def _table(headers: list[str], rows: list[list[str]]) -> str:
    head = "".join(f"<th>{html.escape(c)}</th>" for c in headers)
    body = "".join(
        "<tr>" + "".join(f"<td>{html.escape(str(c))}</td>" for c in row) + "</tr>"
        for row in rows
    )
    return f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"


def render(data: TrackerData) -> str:
    stats = honesty_stats(data)
    dirn = lambda d: "LONG" if d > 0 else "SHORT"

    by_cluster: dict[str, float] = {}
    by_theatre: dict[str, float] = {}
    for t in data.closed:
        by_cluster[t.story_label or t.cluster_id] = by_cluster.get(t.story_label or t.cluster_id, 0.0) + t.pnl
        by_theatre[t.theatre] = by_theatre.get(t.theatre, 0.0) + t.pnl

    closed_rows = [
        [t.exit_ts.strftime("%Y-%m-%d"), t.instrument, dirn(t.direction), t.story_label,
         f"{t.entry_price:.4g}", f"{t.exit_price:.4g}", f"{t.pnl:+.2f}", t.exit_reason]
        for t in sorted(data.closed, key=lambda t: t.exit_ts, reverse=True)
    ]
    open_rows = [
        [t.entry_ts.strftime("%Y-%m-%d %H:%M"), t.instrument, dirn(t.direction),
         t.story_label, f"{t.entry_price:.4g}", f"{t.current_price:.4g}", f"{t.unrealised_pnl:+.2f}"]
        for t in data.open_tickets
    ]

    pf = stats["profit_factor"]
    gen = (data.generated_at or datetime.utcnow()).strftime("%Y-%m-%d %H:%M UTC")
    return f"""<!doctype html>
<html><head><meta charset="utf-8"><title>Divergence Engine — Paper Tracker</title>
<style>
 body{{font-family:-apple-system,Segoe UI,sans-serif;max-width:860px;margin:2rem auto;padding:0 1rem;color:#1a1a2e}}
 h1{{font-size:1.4rem}} h2{{font-size:1.1rem;margin-top:2rem}}
 .stats{{display:flex;gap:1.5rem;flex-wrap:wrap;background:#f5f6fa;padding:1rem;border-radius:8px}}
 .stat b{{display:block;font-size:1.3rem}}
 table{{border-collapse:collapse;width:100%;font-size:.85rem}}
 th,td{{text-align:left;padding:.35rem .5rem;border-bottom:1px solid #e2e2ee}}
 .muted{{color:#888}} .note{{font-size:.8rem;color:#888}}
</style></head><body>
<h1>Divergence Engine — Paper Tracker</h1>
<p class="note">PAPER TRADING. Fills are simulated; haircut everything you see by 20% mentally. Generated {gen}.</p>
<div class="stats">
 <div class="stat"><b>{stats['n_trades']}</b>closed trades</div>
 <div class="stat"><b>{stats['n_clusters']}</b>independent clusters</div>
 <div class="stat"><b>{pf if pf is not None else '∞'}</b>profit factor</div>
 <div class="stat"><b>{stats['max_drawdown_pct']}%</b>max drawdown</div>
 <div class="stat"><b>{stats['total_pnl']:+.2f}</b>total P&amp;L</div>
</div>
<h2>Equity curve</h2>
{_svg_equity(stats['equity_curve'])}
<h2>Open tickets ({len(data.open_tickets)})</h2>
{_table(['Entry', 'Instrument', 'Dir', 'Story', 'Entry px', 'Now', 'Unrealised'], open_rows) if open_rows else "<p class='muted'>None.</p>"}
<h2>Closed trades</h2>
{_table(['Exit', 'Instrument', 'Dir', 'Story', 'Entry', 'Exit', 'P&L', 'Exit reason'], closed_rows) if closed_rows else "<p class='muted'>None yet.</p>"}
<h2>P&amp;L by story cluster</h2>
{_table(['Story', 'P&L'], [[k, f'{v:+.2f}'] for k, v in sorted(by_cluster.items(), key=lambda kv: -kv[1])]) if by_cluster else "<p class='muted'>None yet.</p>"}
<h2>P&amp;L by theatre</h2>
{_table(['Theatre', 'P&L'], [[k, f'{v:+.2f}'] for k, v in sorted(by_theatre.items(), key=lambda kv: -kv[1])]) if by_theatre else "<p class='muted'>None yet.</p>"}
</body></html>"""


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate static tracker page")
    parser.add_argument("--out", default="tracker.html")
    parser.add_argument("--config", default=None)
    args = parser.parse_args()

    from ..config import EngineConfig
    from ..ledger import Ledger

    cfg = EngineConfig.load(args.config)
    ledger = Ledger(cfg.db_dsn)
    rows = ledger._conn.execute(
        "SELECT e.instrument, e.direction, e.cluster_id, e.ts, x.ts, e.price, x.price,"
        " x.realised_pnl, x.exit_reason FROM tickets e"
        " JOIN tickets x ON x.ticket_id = e.ticket_id AND x.state = 'exit'"
        " WHERE e.state = 'entry'"
    ).fetchall()
    from ..marketdata.universe import theatre_of

    closed = [
        ClosedTrade(
            instrument=r[0], direction=r[1], cluster_id=r[2] or "?", story_label=r[2] or "?",
            theatre=_safe_theatre(theatre_of, r[0]), entry_ts=r[3], exit_ts=r[4],
            entry_price=r[5], exit_price=r[6], pnl=r[7] or 0.0, exit_reason=r[8],
        )
        for r in rows
    ]
    data = TrackerData(nav_start=cfg.risk.paper_nav, closed=closed)
    Path(args.out).write_text(render(data))
    print(f"wrote {args.out}")


def _safe_theatre(fn, instrument: str) -> str:
    try:
        return fn(instrument)
    except KeyError:
        return "crypto"


if __name__ == "__main__":
    main()
