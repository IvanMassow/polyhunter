from datetime import datetime, timedelta, timezone

from divergence_engine.tracker.generate import ClosedTrade, TrackerData, honesty_stats, render

NOW = datetime(2026, 6, 10, tzinfo=timezone.utc)


def _trade(pnl, cluster="cl_a", theatre="energy", days=0):
    return ClosedTrade(
        instrument="BRENT", direction=1, cluster_id=cluster, story_label=cluster,
        theatre=theatre, entry_ts=NOW + timedelta(days=days),
        exit_ts=NOW + timedelta(days=days + 2), entry_price=100.0,
        exit_price=100.0 + pnl, pnl=pnl, exit_reason="target",
    )


def test_honesty_stats():
    data = TrackerData(
        nav_start=100_000.0,
        closed=[_trade(300, "cl_a"), _trade(-100, "cl_b", days=1), _trade(200, "cl_a", days=2)],
    )
    stats = honesty_stats(data)
    assert stats["n_trades"] == 3
    assert stats["n_clusters"] == 2
    assert stats["profit_factor"] == 5.0          # 500 / 100
    assert stats["total_pnl"] == 400.0
    assert stats["max_drawdown_pct"] > 0


def test_render_smoke():
    page = render(TrackerData(nav_start=100_000.0, closed=[_trade(300)], generated_at=NOW))
    assert "PAPER TRADING" in page
    assert "profit factor" in page
    assert "BRENT" in page
    # Empty book renders too.
    assert "No closed trades" in render(TrackerData(nav_start=100_000.0, generated_at=NOW))
