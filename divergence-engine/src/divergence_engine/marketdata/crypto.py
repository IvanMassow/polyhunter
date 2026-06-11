"""Crypto OHLCV ingestion via ccxt (lazy import — optional dependency).

Granularities per spec: 4h + 1h for crypto. Exchange picked per listing;
defaults to Binance with Kraken/Coinbase fallbacks.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd

EXCHANGE_PRIORITY = ["binance", "kraken", "coinbase"]
COLUMNS = ["ts", "open", "high", "low", "close", "volume"]


def _get_exchange(name: str):
    try:
        import ccxt
    except ImportError as e:  # pragma: no cover
        raise ImportError("ccxt is required for crypto ingestion: pip install 'divergence-engine[market]'") from e
    return getattr(ccxt, name)({"enableRateLimit": True})


def fetch_ohlcv(
    symbol: str,
    timeframe: str = "1h",
    since: datetime | None = None,
    limit: int = 1000,
    exchanges: list[str] | None = None,
) -> pd.DataFrame:
    """Fetch OHLCV for e.g. 'BTC/USDT', trying exchanges in priority order.

    Returns a DataFrame indexed by tz-aware ts with open/high/low/close/volume.
    """
    last_err: Exception | None = None
    for name in exchanges or EXCHANGE_PRIORITY:
        try:
            ex = _get_exchange(name)
            since_ms = int(since.timestamp() * 1000) if since else None
            raw = ex.fetch_ohlcv(symbol, timeframe=timeframe, since=since_ms, limit=limit)
            if not raw:
                continue
            df = pd.DataFrame(raw, columns=COLUMNS)
            df["ts"] = pd.to_datetime(df["ts"], unit="ms", utc=True)
            return df.set_index("ts").astype(float)
        except Exception as e:  # noqa: BLE001 — try next venue, keep the cause
            last_err = e
    raise RuntimeError(f"all exchanges failed for {symbol} {timeframe}") from last_err


def fetch_history(
    symbol: str,
    timeframe: str,
    start: datetime,
    end: datetime | None = None,
    exchanges: list[str] | None = None,
) -> pd.DataFrame:
    """Page through fetch_ohlcv to cover [start, end]."""
    end = end or datetime.now(timezone.utc)
    frames: list[pd.DataFrame] = []
    cursor = start
    while cursor < end:
        df = fetch_ohlcv(symbol, timeframe, since=cursor, exchanges=exchanges)
        if df.empty:
            break
        frames.append(df)
        last_ts = df.index[-1].to_pydatetime()
        if last_ts <= cursor:
            break
        cursor = last_ts
    if not frames:
        return pd.DataFrame(columns=COLUMNS[1:])
    out = pd.concat(frames)
    return out[~out.index.duplicated(keep="last")].sort_index().loc[:end]
