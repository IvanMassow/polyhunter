# Divergence Engine

A narrative-vs-price paper-trading system on Noah signal + Kronos.
Implements [the build spec v0.1](docs/divergence_engine_build_spec_v0.1.md) — read that first.

**The one trade idea:** enter where narrative pressure has moved and price
structure has not yet followed; exit when price catches up, the narrative
fades, or an invalidator fires. Horizon 1–15 days, scanned hourly.
**Paper trading only** until the §11 promotion gate passes.

## Layout

```
schema/schema.sql        Append-only Postgres ledger (feeds, feed_state,
                         kronos_forecasts, candidates, tickets, story_clusters,
                         decisions, ohlcv)
feeds/*.yaml             The 12 feed specs (E1–E8 energy, C1–C4 crypto)
src/divergence_engine/
  feeds/                 FeedSpec loader; FeedSource interface with
                         NoahInternalFeedAPI (preferred) and NoahReportPolling
                         (Phase-0 fallback)
  marketdata/            ccxt crypto ingestion; continuous front-month futures
                         series (volume-crossover roll, additive back-adjust,
                         raw + adjusted both kept); instrument universe
  kronos/                KronosPredictor wrapper -> mu_K, vol cone, quantiles
  funnel/                Stages 1–6: triggers, spawn, corroboration, Kronos
                         gates (too_late kill, structure conflict), divergence
                         score, lifecycle exits
  risk/                  Story clustering (weighted-feed overlap >= 50%),
                         governor (per-trade/instrument/theatre/cluster caps,
                         kill switch), vol-aware sizing
  tracker/               Static HTML tracker, honesty stats up front
  jobs/                  de-hourly-scan (funnel) and de-forecast-cycle
                         (OHLCV + Kronos, every 4h)
tests/                   57 tests; futures roll behaviour is pinned against
                         hand-computed prices
```

## Setup

```bash
pip install -e '.[dev]'                    # core + tests
pip install -e '.[market,db]'              # ccxt + psycopg for live runs
# Kronos: clone https://github.com/shiyu-coder/Kronos onto PYTHONPATH,
# then pip install -e '.[kronos]' (models pull from Hugging Face on first use).

createdb divergence
psql divergence < schema/schema.sql
```

Configuration: `EngineConfig.load("config.yaml")` — every spec constant
(theta, lambda, risk fractions, kill-switch limits) is overridable there;
defaults match the build spec. Environment:

| Variable | Purpose |
|---|---|
| `NOAH_API_URL` / `NOAH_API_TOKEN` | switches feeds to NoahInternalFeedAPI |
| `NOAH_PACKAGES_DIR` | report-polling fallback package directory |
| `FUTURES_DATA_DIR` | per-contract CSV drops (`{INST}/{CONTRACT}.csv`) |
| `DE_ALT_BASKET` | comma-separated alt basket (monthly rebalance) |
| `DE_OPEN_TICKETS=1` | Phase 1: write ticket entries automatically |

## Scheduling

Run on a separate box/namespace — **not** the Predict production cluster:

```cron
0 * * * *    de-hourly-scan    --config /etc/divergence/config.yaml
0 */4 * * *  de-forecast-cycle --config /etc/divergence/config.yaml
30 8 * * *   de-tracker --out /var/www/divergence/index.html
```

## Phase 0 status

- [x] Ledger schema, append-only with `ingested_at` + `backfilled` discipline
- [x] 12 feed specs with invalidators; loader fails loudly on malformed specs
- [x] Continuous futures series (tested against known prices) + ccxt ingestion
- [x] Kronos wrapper: S=64 paths → mu_K / sigma cone / q05–q95, persisted
- [x] Funnel stages 1–5 producing candidate cards into the ledger
- [x] Lifecycle exit logic written (read-only in Phase 0; tickets by hand)
- [ ] Phase 1: exit automation on (`DE_OPEN_TICKETS=1`), θ calibration from
      live candidate flow, switch to NoahInternalFeedAPI when access lands

## Invariants that must not soften

- Kronos alone never opens a trade (no narrative pressure, no position).
- One thin lane never trades (corroboration stage 3).
- One story cluster, one risk budget — however many instruments express it.
- Ledger rows are never overwritten; backfills carry `backfilled=true`.
- Promotion gate (§11): ≥40 closed tickets, ≥8 independent clusters,
  PF ≥ 1.8, max DD ≤ 15%, `too_late` rate stable/falling, top cluster ≤ 40%
  of P&L. All of them, on paper, before any real capital.
