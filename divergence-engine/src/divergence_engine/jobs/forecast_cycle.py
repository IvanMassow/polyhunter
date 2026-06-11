"""4-hourly forecast cycle: OHLCV ingestion + Kronos forecasts -> ledger.

Crypto: live via ccxt (1h + 4h candles). Futures: Phase 0 takes per-contract
CSV drops (no licensed live futures feed yet) from FUTURES_DATA_DIR, layout
    {dir}/{INSTRUMENT}/{CONTRACT}.csv  with columns ts,open,high,low,close,volume
in expiry order by name; the continuous series is rebuilt on every cycle and
both raw and adjusted series are persisted.

Cron (separate box/namespace from Predict production):
    0 */4 * * *  de-forecast-cycle --config /etc/divergence/config.yaml
"""

from __future__ import annotations

import argparse
import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

from ..config import EngineConfig
from ..kronos import KronosForecaster
from ..ledger import Ledger
from ..marketdata.crypto import fetch_history
from ..marketdata.futures import build_continuous_series
from ..marketdata.universe import ENERGY_INSTRUMENTS, CRYPTO_CORE

log = logging.getLogger("divergence.forecast_cycle")

CRYPTO_SYMBOLS = {inst: f"{inst}/USDT" for inst in CRYPTO_CORE}


def ingest_crypto(ledger: Ledger, now: datetime, alt_basket: list[str]) -> dict[str, pd.DataFrame]:
    frames: dict[str, pd.DataFrame] = {}
    symbols = dict(CRYPTO_SYMBOLS, **{a: f"{a}/USDT" for a in alt_basket})
    start = now - timedelta(days=120)
    for inst, symbol in symbols.items():
        for timeframe in ("1h", "4h"):
            try:
                df = fetch_history(symbol, timeframe, start, now)
            except Exception:
                log.exception("crypto ingest failed: %s %s", symbol, timeframe)
                continue
            if df.empty:
                continue
            ledger.write_ohlcv(inst, "raw", timeframe, df)
            if timeframe == "4h":
                frames[inst] = df
    return frames


def ingest_futures(ledger: Ledger, data_dir: str | Path) -> dict[str, pd.DataFrame]:
    frames: dict[str, pd.DataFrame] = {}
    root = Path(data_dir)
    for inst in ENERGY_INSTRUMENTS:
        inst_dir = root / inst
        if not inst_dir.is_dir():
            continue
        contract_files = sorted(inst_dir.glob("*.csv"))
        if not contract_files:
            continue
        contracts = {}
        for f in contract_files:
            df = pd.read_csv(f, parse_dates=["ts"]).set_index("ts")
            contracts[f.stem] = df
        order = [f.stem for f in contract_files]
        if len(order) == 1:
            # Single series (e.g. an equity proxy like FRO/STNG): no roll.
            cont = contracts[order[0]].copy()
            cont["contract"] = order[0]
            for col in ("open", "high", "low", "close"):
                cont[f"adj_{col}"] = cont[col]
        else:
            cont = build_continuous_series(contracts, order)
        granularity = "1d"
        raw_cols = ["open", "high", "low", "close", "volume", "contract"]
        ledger.write_ohlcv(inst, "continuous_raw", granularity, cont[raw_cols])
        adj = cont[["adj_open", "adj_high", "adj_low", "adj_close", "volume", "contract"]].rename(
            columns=lambda c: c.removeprefix("adj_")
        )
        ledger.write_ohlcv(inst, "continuous_adjusted", granularity, adj)
        frames[inst] = adj
    return frames


def run_cycle(cfg: EngineConfig, now: datetime | None = None) -> int:
    now = now or datetime.now(timezone.utc)
    ledger = Ledger(cfg.db_dsn)
    forecaster = KronosForecaster(cfg.kronos)

    alt_basket = [a for a in os.environ.get("DE_ALT_BASKET", "").split(",") if a]
    crypto = ingest_crypto(ledger, now, alt_basket)
    futures = ingest_futures(ledger, os.environ.get("FUTURES_DATA_DIR", "futures_data"))

    n = 0
    for inst, df in {**crypto, **futures}.items():
        granularity = "4h" if inst in crypto else "1d"
        try:
            summaries = forecaster.forecast(inst, df, granularity, now)
        except Exception:
            log.exception("kronos forecast failed: %s", inst)
            continue
        for s in summaries:
            ledger.write_forecast(s)
            n += 1
    return n


def main() -> None:
    parser = argparse.ArgumentParser(description="Divergence Engine forecast cycle")
    parser.add_argument("--config", default=None)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    cfg = EngineConfig.load(args.config)
    n = run_cycle(cfg)
    log.info("forecast cycle complete: %d summaries persisted", n)


if __name__ == "__main__":
    main()
