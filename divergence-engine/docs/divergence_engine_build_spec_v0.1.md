# Divergence Engine — Build Specification v0.1
**A narrative-vs-price trading system on Noah signal + Kronos**
*11 June 2026 · For the builder session. Self-contained: read this top to bottom before writing code.*

---

## 1. The one-paragraph thesis

Noah's archive measures **narrative pressure** (direction × velocity × coverage, contradiction-penalised) with ~1-hour latency. Kronos, an open-source foundation model for candlesticks, gives us **what the chart already believes** — a forecast distribution from price structure alone. Markets we care about (energy futures, crypto) reprice narrative over **days**, not microseconds. The system holds exactly one trade idea: *enter where narrative pressure has moved and price structure has not yet followed; exit when price catches up, the narrative fades, or an invalidator fires.* Horizon 1–15 days, scanned hourly. Everything else in this document is plumbing around that sentence.

## 2. What we trade

**Theatre A — Energy (6 instruments):** Brent front-month continuous (and 2nd month for roll), WTI, EU TTF natural gas, low-sulphur gasoil; optionally 2 liquid tanker equities (FRO, STNG) as shipping-rate proxies.
**Theatre B — Crypto (12 instruments):** BTC, ETH plus 10 liquid alts chosen by 90-day median volume (rebalanced monthly).
**Explicitly out:** US large-cap single names (news priced in ms; our latency loses), rates/Fed instruments (engine demonstrably weakest there — verified on FOMC ladders), prediction markets (jurisdiction call), illiquid microcaps.

Paper trading first. Nothing in this spec touches real money until the Phase-2 gate (§11).

## 3. Components at a glance

```
[Noah feeds (sensor mesh)] ──hourly──▶ [feed_state ledger]
                                            │
[OHLCV ingestion] ──▶ [Kronos forecaster] ──▶ [kronos_forecasts ledger]
                                            │
                                   [Candidate funnel]
                                            │
                                   [Divergence scorer]
                                            │
                                [Risk / portfolio governor]
                                            │
                              [Paper execution + tracker page]
                                            │
                                 [Append-only decision ledger]
```

One Python monorepo (`divergence-engine`), Postgres for state, scheduled jobs (cron or k8s CronJob — keep it OFF the Predict production cluster; separate namespace or separate box). No web framework needed beyond a static tracker generator.

## 4. The sensor mesh (Noah side)

A **feed** is a standing orchestration on the Noah database: keyword universe reduction → semantic tunnel → lane physics, emitting an hourly state vector. Feed spec schema:

```yaml
feed_id: E1_hormuz_friction
mechanism: "Physical/insurance friction on Strait of Hormuz transits"
keyword_universe: [Hormuz, tanker, transit, IRGC, seizure, escort, AIS, war risk premium, ...]
semantic_brief: >
  Paragraphs describing actual or threatened interference with Gulf shipping:
  seizures, drills, escorts, insurance loadings, rerouting decisions, AIS gaps.
  NOT general Iran diplomacy (that is E3's tunnel).
lanes_expected: [direct_incident, capability, insurance_pricing, counter_deescalation]
instruments: {BRENT: +0.9, WTI: +0.7, TTF: +0.3, GASOIL: +0.6, FRO: +0.8, STNG: +0.8}
horizon_days_typical: 5
```

Hourly emission per feed: `{ts, direction ∈ [-1,+1], velocity (z-scored Δpressure), coverage, contradiction ∈ [0,1], freshness}` — all of which already exist in Noah's lane physics; the feed layer just persists them on a clock.

**Integration note:** build behind an interface `FeedSource` with two implementations: `NoahInternalFeedAPI` (preferred — the keyword+semantic analytics API; wire when access lands) and `NoahReportPolling` (fallback — scheduled saved-chat refreshes parsed from packages; slower and costlier, fine for Phase 0).

**Initial 12 feeds.** Energy: E1 Hormuz transit friction · E2 Houthi/Red Sea maritime attacks · E3 US–Iran military/diplomatic posture · E4 war-risk insurance pricing chatter (JWC, AP rates — insurance chatter is itself a leading indicator) · E5 OPEC+ quota discipline & cheating · E6 Libya/Nigeria supply disruption · E7 EU gas storage & LNG diversion · E8 Russia sanctions/shadow-fleet enforcement. Crypto: C1 US regulatory temperature (SEC/CFTC/legislation) · C2 ETF & institutional flow narrative · C3 exchange/stablecoin stress (custody, depegs, hacks) · C4 crypto crime & security wave.

## 5. Kronos — exactly how it is used

Models: `Kronos-Tokenizer` + `Kronos-small` (24.7M, ctx 512) to start; `Kronos-base` (102M) if inference budget allows. Both on Hugging Face, MIT licence. CPU is fine at this scale; GPU optional. Repo: https://github.com/shiyu-coder/Kronos (use its `KronosPredictor` API; fine-tuning pipeline exists for later).

**Inputs:** per instrument, rolling OHLCV — crypto: 1h and 4h candles via ccxt (Binance/Kraken/Coinbase, pick per listing); futures: build a **continuous front-month series with explicit roll handling** (roll on volume crossover; back-adjust; store both raw and adjusted — this is the classic data bug, do it carefully). Daily + 4h granularity for futures, 4h + 1h for crypto.

**Output per (instrument, horizon h ∈ {1d, 3d, 7d, 15d}), refreshed every 4h:** sample S=64 future paths (temperature per repo defaults), reduce to: `mu_K` (median terminal return), `sigma_K(Δt)` (path-implied vol cone), quantiles q05/q25/q75/q95. Persist all of it.

**Four uses — and one explicit non-use:**
1. **Market-belief baseline.** `mu_K` = what price structure expects absent new narrative. The divergence score is computed against it, not against zero.
2. **"Already-priced" estimator.** When a candidate spawns from a story with onset time t₀: `priced_z = (P_now − P_t0) / sigma_K(now − t0)`, signed by the signal direction. If `priced_z > 1.5`, the chart has already eaten the story — kill the candidate (label `too_late`, keep in ledger; these labels train future timing).
3. **Structure-conflict gate.** If narrative says long but the Kronos distribution is strongly skewed against (q75 < 0), raise the entry threshold ~50% and halve size — we don't fight strong structure on equal terms. Symmetric for shorts.
4. **Vol-aware sizing and stops.** Stop distance = 1.2 × sigma_K(h); position size = per-trade risk budget / stop distance; time-stop = 1.5 × horizon. Targets at q75/q95 of the *narrative-shifted* distribution.
5. **Non-use:** Kronos alone never opens a trade. No narrative pressure, no position — its standalone alpha is not the edge claim and we don't pretend otherwise.

Later (Phase 2+): fine-tune Kronos on our instruments' history, and eventually condition on ledger outcomes — but not before the base loop works.

## 6. Candidate selection — how the funnel begins

Candidates are **(instrument, story, direction, horizon)** tuples, not instruments. The funnel:

**Stage 1 — Trigger scan (hourly).** For every feed: fire a trigger if (a) velocity z-score > 2 over its trailing 30-day distribution, or (b) direction flips sign with coverage ≥ moderate, or (c) contradiction collapses below 0.2 having been above 0.5 (a contested story consolidating is often the tradeable moment — the crowd's uncertainty is what you're being paid for).
**Stage 2 — Spawn.** Trigger × the feed's instrument map → one candidate card per (instrument, direction), with expected horizon from the feed spec and `t0` = trigger time.
**Stage 3 — Corroboration.** Require ≥2 feeds in the same story cluster agreeing in sign, OR a single feed with a `direct` lane at strong coverage. One thin lane never trades.
**Stage 4 — Kronos gates.** Apply the already-priced kill (§5.2) and the structure-conflict adjustment (§5.3).
**Stage 5 — Divergence score.**
`P_narr = Σ_feeds w_f · direction_f · velocity_f · coverage_f · (1 − contradiction_f) · freshness_f`
`D = P_narr − λ · priced_z`
Open a paper ticket when `|D| > θ` (θ calibrated in Phase 0 to yield ~3–6 tickets/week total; start with the top decile of all candidate scores rather than a magic constant).
**Stage 6 — Lifecycle (hourly).** Exit on: target · stop · time-stop · **narrative fade** (velocity sign-flip while in profit → take it) · **invalidator hit** (each feed spec lists its invalidators — port Predict's watchpoint/invalidator concept directly). Every exit records `exit_reason`.

## 7. Risk governor

- Per-trade risk: 0.5% of paper NAV. Per-instrument cap: 2 concurrent tickets. Theatre cap: 60% of risk in one theatre.
- **Same-story limiter (the PolyHunter lesson — its 73 positions were 40 markets in ~2 themes):** candidates sharing ≥50% of weighted trigger feeds belong to one `story_cluster`; a cluster gets a single risk budget no matter how many instruments express it.
- Global kill switch: 3 consecutive cluster-level losses or 10% paper drawdown → flat, post-mortem before restart.

## 8. The ledger (the long-term asset)

Append-only Postgres, every row timestamped at write (`ingested_at` distinct from any event time — point-in-time discipline from row one):
`feeds`, `feed_state` (hourly vectors), `kronos_forecasts`, `candidates` (including every kill and its reason), `tickets` (entry/exit/realised), `story_clusters`, `decisions` (one row per funnel decision with a hash of its full input snapshot).
In six months this is a labelled narrative→price dataset nobody else owns. Never overwrite; never backfill without a `backfilled=true` flag.

## 9. Tracker

Static HTML generator in the polyhunter style (same repo pattern): equity curve, open tickets, closed-trade table with story labels and exit reasons, per-cluster and per-theatre breakdowns, and the honesty stats up front (n trades, n independent clusters, PF, max DD). Publishable later; private during paper.

## 10. Build order

**Phase 0 (≈2 weeks):** OHLCV ingestion (ccxt + futures continuous series) · Kronos-small running with forecasts persisted · 12 feed specs stood up via `NoahReportPolling` fallback · funnel Stages 1–5 producing candidate cards into the ledger · no automation of exits yet, tickets managed by hand.
**Phase 1 (≈2–4 weeks):** full lifecycle automation · risk governor · tracker page · θ calibration · switch feeds to `NoahInternalFeedAPI` when access is wired.
**Phase 2 (3–6 months):** run. Touch nothing structural; log everything; weekly review of `too_late` and `exit_reason` distributions (they tell you whether latency or selection is the binding constraint).

## 11. Promotion gate (paper → small real capital)

ALL of: ≥40 closed tickets · across ≥8 independent story clusters · profit factor ≥ 1.8 · max drawdown ≤ 15% · `too_late` kill-rate stable or falling · results not dominated by one cluster (top cluster ≤ 40% of P&L). If any fails, iterate on paper. This gate is the whole credibility of the project; do not soften it mid-run.

## 12. Known risks, named

Narrative latency vs crypto weekends (news flows when futures are shut — handle Monday gaps in stops) · futures roll bugs (test the continuous series against known prices) · feed overfitting to the 2026 Iran cycle (the mesh must include feeds that are currently boring) · regime change in crypto correlation (alts collapse to one beta in risk-off; the story-cluster limiter must catch this) · and the standing one: paper fills are not real fills — haircut paper P&L 20% mentally throughout.
