-- Divergence Engine ledger schema (Postgres).
-- Append-only discipline: rows are inserted, never updated or deleted.
-- Every table carries ingested_at (write time) distinct from any event time,
-- and a backfilled flag for the rare sanctioned backfill.

CREATE SCHEMA IF NOT EXISTS divergence;
SET search_path TO divergence;

-- Feed registry: one row per feed spec *version* (re-register on spec change).
CREATE TABLE IF NOT EXISTS feeds (
    id              BIGSERIAL PRIMARY KEY,
    feed_id         TEXT        NOT NULL,
    spec_version    INTEGER     NOT NULL DEFAULT 1,
    mechanism       TEXT        NOT NULL,
    spec_yaml       TEXT        NOT NULL,           -- full spec as registered
    spec_hash       TEXT        NOT NULL,           -- sha256 of spec_yaml
    theatre         TEXT        NOT NULL,           -- 'energy' | 'crypto'
    ingested_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    backfilled      BOOLEAN     NOT NULL DEFAULT FALSE,
    UNIQUE (feed_id, spec_version)
);

-- Hourly state vector per feed (Noah lane physics, persisted on a clock).
CREATE TABLE IF NOT EXISTS feed_state (
    id              BIGSERIAL PRIMARY KEY,
    feed_id         TEXT        NOT NULL,
    ts              TIMESTAMPTZ NOT NULL,           -- emission hour
    direction       DOUBLE PRECISION NOT NULL,      -- [-1, +1]
    velocity        DOUBLE PRECISION NOT NULL,      -- z-scored delta-pressure
    coverage        DOUBLE PRECISION NOT NULL,
    contradiction   DOUBLE PRECISION NOT NULL,      -- [0, 1]
    freshness       DOUBLE PRECISION NOT NULL,
    source          TEXT        NOT NULL,           -- 'internal_api' | 'report_polling'
    raw             JSONB,                          -- full upstream payload
    ingested_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    backfilled      BOOLEAN     NOT NULL DEFAULT FALSE
);
CREATE INDEX IF NOT EXISTS feed_state_feed_ts ON feed_state (feed_id, ts);

-- Kronos forecast summaries, refreshed every 4h per (instrument, horizon).
CREATE TABLE IF NOT EXISTS kronos_forecasts (
    id              BIGSERIAL PRIMARY KEY,
    instrument      TEXT        NOT NULL,
    ts              TIMESTAMPTZ NOT NULL,           -- forecast generation time
    horizon_days    INTEGER     NOT NULL,           -- 1 | 3 | 7 | 15
    granularity     TEXT        NOT NULL,           -- input candle granularity
    model           TEXT        NOT NULL,           -- e.g. 'kronos-small'
    n_paths         INTEGER     NOT NULL,
    last_price      DOUBLE PRECISION NOT NULL,
    mu_k            DOUBLE PRECISION NOT NULL,      -- median terminal return
    q05             DOUBLE PRECISION NOT NULL,
    q25             DOUBLE PRECISION NOT NULL,
    q75             DOUBLE PRECISION NOT NULL,
    q95             DOUBLE PRECISION NOT NULL,
    sigma_cone      JSONB       NOT NULL,           -- {days: sigma_of_return}
    ingested_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    backfilled      BOOLEAN     NOT NULL DEFAULT FALSE
);
CREATE INDEX IF NOT EXISTS kronos_inst_ts ON kronos_forecasts (instrument, ts);

-- Story clusters (groups of candidates sharing weighted trigger feeds).
CREATE TABLE IF NOT EXISTS story_clusters (
    id              BIGSERIAL PRIMARY KEY,
    cluster_id      TEXT        NOT NULL,
    ts              TIMESTAMPTZ NOT NULL,
    feed_weights    JSONB       NOT NULL,           -- {feed_id: weight}
    label           TEXT,
    ingested_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    backfilled      BOOLEAN     NOT NULL DEFAULT FALSE
);

-- Every candidate ever spawned, including every kill and its reason.
CREATE TABLE IF NOT EXISTS candidates (
    id              BIGSERIAL PRIMARY KEY,
    candidate_id    TEXT        NOT NULL UNIQUE,
    instrument      TEXT        NOT NULL,
    direction       INTEGER     NOT NULL,           -- +1 long, -1 short
    horizon_days    INTEGER     NOT NULL,
    t0              TIMESTAMPTZ NOT NULL,           -- story onset (trigger time)
    spawned_at      TIMESTAMPTZ NOT NULL,
    cluster_id      TEXT,
    trigger_feeds   JSONB       NOT NULL,           -- {feed_id: weight}
    p_narr          DOUBLE PRECISION,
    priced_z        DOUBLE PRECISION,
    divergence      DOUBLE PRECISION,               -- D = P_narr - lambda * priced_z
    threshold_used  DOUBLE PRECISION,
    size_factor     DOUBLE PRECISION,               -- 1.0, or 0.5 on structure conflict
    status          TEXT        NOT NULL,           -- 'open_ticket'|'killed'|'below_threshold'
    kill_reason     TEXT,                           -- 'too_late'|'no_corroboration'|...
    ingested_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    backfilled      BOOLEAN     NOT NULL DEFAULT FALSE
);
CREATE INDEX IF NOT EXISTS candidates_inst ON candidates (instrument, spawned_at);

-- Paper tickets. Exit fields are written as a *new* row (state='exit') keyed
-- by ticket_id; the entry row is never updated.
CREATE TABLE IF NOT EXISTS tickets (
    id              BIGSERIAL PRIMARY KEY,
    ticket_id       TEXT        NOT NULL,
    state           TEXT        NOT NULL,           -- 'entry' | 'exit'
    candidate_id    TEXT        NOT NULL,
    cluster_id      TEXT,
    instrument      TEXT        NOT NULL,
    direction       INTEGER     NOT NULL,
    ts              TIMESTAMPTZ NOT NULL,           -- entry or exit time
    price           DOUBLE PRECISION NOT NULL,
    size            DOUBLE PRECISION,               -- units (entry rows)
    risk_amount     DOUBLE PRECISION,               -- $ at risk (entry rows)
    stop_price      DOUBLE PRECISION,
    target_q75      DOUBLE PRECISION,
    target_q95      DOUBLE PRECISION,
    time_stop_at    TIMESTAMPTZ,
    exit_reason     TEXT,                           -- exit rows: 'target'|'stop'|'time_stop'|'narrative_fade'|'invalidator'|'kill_switch'
    realised_pnl    DOUBLE PRECISION,               -- exit rows
    ingested_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    backfilled      BOOLEAN     NOT NULL DEFAULT FALSE
);
CREATE INDEX IF NOT EXISTS tickets_ticket ON tickets (ticket_id, state);

-- One row per funnel decision, with a hash of its full input snapshot so any
-- decision can be audited and replayed.
CREATE TABLE IF NOT EXISTS decisions (
    id              BIGSERIAL PRIMARY KEY,
    ts              TIMESTAMPTZ NOT NULL,
    stage           TEXT        NOT NULL,           -- 'trigger'|'spawn'|'corroborate'|'gate'|'score'|'risk'|'lifecycle'
    candidate_id    TEXT,
    decision        TEXT        NOT NULL,           -- 'pass'|'kill'|'open'|'exit'|...
    detail          JSONB,
    input_hash      TEXT        NOT NULL,           -- sha256 of canonical input snapshot
    input_snapshot  JSONB       NOT NULL,
    ingested_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    backfilled      BOOLEAN     NOT NULL DEFAULT FALSE
);
CREATE INDEX IF NOT EXISTS decisions_ts ON decisions (ts, stage);

-- OHLCV store: raw per-contract / per-symbol candles, plus continuous
-- back-adjusted futures series (kept separately, both retained).
CREATE TABLE IF NOT EXISTS ohlcv (
    id              BIGSERIAL PRIMARY KEY,
    instrument      TEXT        NOT NULL,           -- e.g. 'BTC/USDT' or 'BRENT_2026M7'
    series          TEXT        NOT NULL,           -- 'raw' | 'continuous_raw' | 'continuous_adjusted'
    granularity     TEXT        NOT NULL,           -- '1h' | '4h' | '1d'
    ts              TIMESTAMPTZ NOT NULL,
    open            DOUBLE PRECISION NOT NULL,
    high            DOUBLE PRECISION NOT NULL,
    low             DOUBLE PRECISION NOT NULL,
    close           DOUBLE PRECISION NOT NULL,
    volume          DOUBLE PRECISION NOT NULL,
    contract        TEXT,                           -- underlying contract for continuous rows
    ingested_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    backfilled      BOOLEAN     NOT NULL DEFAULT FALSE,
    UNIQUE (instrument, series, granularity, ts)
);
