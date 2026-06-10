# FeedHunter — a lightweight, always-on RSS feed discovery system

Design proposal for Noah Wire. Goal: continuously and cheaply discover new
RSS/Atom/JSON feeds worldwide — news, government, exchange/regulator, and the
fast-growing crop of AI news sites that Google indexes poorly — validate them,
score them, and hand vetted candidates to the Noah Wire ingestion pipeline.

---

## 1. Principles

- **Cheap by default.** One small VM (or a couple of scheduled workers),
  SQLite/Postgres, Python. No headless browsers in the hot path — plain HTTP
  with conditional GETs. LLM calls only at the final classification step, on
  the small trickle of *new* candidates, never on the firehose.
- **Discovery is a funnel, not a crawler.** We never crawl the open web. We
  generate *domain leads* from high-yield sources, then run a cheap, bounded
  probe against each domain. A domain is touched a handful of times, then
  remembered forever.
- **Everything is idempotent and resumable.** Every lead, probe, and verdict
  lives in one database; any worker can die and restart.
- **Polite.** robots.txt respected, per-domain rate limits, real User-Agent
  with contact email, ETag/Last-Modified caching.

## 2. Architecture overview

```
            ┌──────────────────────────────────────────────┐
            │                LEAD GENERATORS               │
            │  (each is a small independent scheduled job)  │
            │                                              │
            │  A. Known-feed expansion (links, OPML)        │
            │  B. Feed directories & aggregator sites       │
            │  C. Search APIs (Bing/Brave/DDG dorks)        │
            │  D. New-domain watch (CT logs / zone files)   │
            │  E. Curated registries (gov/exchange lists)   │
            │  F. Social & citation mining (HN, Reddit,     │
            │     newsletters, links inside ingested news)  │
            │  G. GitHub OPML / awesome-list hunting        │
            │  H. Agent-assisted logged-in directory pulls  │
            └───────────────┬──────────────────────────────┘
                            │  domains / candidate URLs
                            ▼
                  ┌──────────────────┐
                  │   LEAD QUEUE     │  dedupe vs. known + seen
                  └────────┬─────────┘
                           ▼
                  ┌──────────────────┐
                  │     PROBER       │  well-known paths + HTML
                  │                  │  <link rel=alternate> autodiscovery
                  └────────┬─────────┘
                           ▼
                  ┌──────────────────┐
                  │    VALIDATOR     │  parses feed, freshness, language,
                  │                  │  post frequency, full-text vs stub
                  └────────┬─────────┘
                           ▼
                  ┌──────────────────┐
                  │ SCORER/CLASSIFIER│  rules first, cheap LLM (Haiku-class)
                  │                  │  for category + quality on survivors
                  └────────┬─────────┘
                           ▼
              ┌────────────────────────────┐
              │  REVIEW QUEUE + DAILY      │ → auto-accept above threshold,
              │  DIGEST (email/Slack)      │   one-click approve the rest
              └────────────┬───────────────┘
                           ▼
                Noah Wire feed registry (export: OPML / JSON / API)
```

A single `feeds.db` (SQLite to start; Postgres when it outgrows it) holds four
tables: `domains` (every domain ever seen + status + next_check), `leads`
(where a domain came from, for source-effectiveness stats), `feeds`
(validated feeds + metadata + health), and `verdicts` (scores, category,
human/auto decision).

## 3. Lead generators (the interesting part)

Ranked roughly by yield-per-pound. Each runs on its own cadence.

### A. Known-feed expansion — *highest yield, zero cost*
The supplied list of known feeds is itself the best lead source:
- Fetch each known feed's recent items; extract **outbound links** in article
  bodies. News sites cite other news sites constantly. Every linked domain we
  haven't seen becomes a lead. (If Noah Wire already stores article HTML, mine
  that corpus instead — no extra fetching at all.)
- Crawl known sites' **blogrolls, footer link pages, and "network/partners"
  pages** (one page deep, once).
- Pull **OPML exports** wherever sites publish them.
Run: continuously, throttled. This alone compounds: every accepted feed
becomes a new expansion source.

### B. Feed directories & aggregator sites — *the "leads" sites you mentioned*
Feedspot, FeedSpot category pages, RSS.app gallery, Inoreader/Feedly public
collections, blogarama, Feeder.co discover, Awesome-RSS lists, podcast-style
directories with news sections. Strategy:
- Scrape public category/listing pages on a weekly cadence; diff against the
  last snapshot so we only process *new* entries.
- **Feedly Cloud Search API** deserves a special mention: it's effectively a
  pre-built feed search engine (`/v3/search/feeds?query=...`). Free tier is
  enough for a daily keyword sweep ("AI news", "artificial intelligence
  daily", exchange names, ministry names in 20 languages).
Run: weekly per directory, daily for Feedly search.

### C. Search-engine dorking — *finds the unindexed long tail*
Use a cheap search API (Brave Search API ~$3/1k queries, or Bing). Rotating
query templates, a few hundred queries/day:
- `inurl:feed "artificial intelligence" news`, `inurl:rss.xml "AI"`,
  `"powered by ghost" AI news` (Ghost/Substack/Hugo sites all have
  predictable feed URLs),
- platform-targeted: `site:substack.com AI` (every Substack has `/feed`),
  `site:*.ghost.io`, `site:medium.com` publications (`/feed/<name>`),
  beehiiv/buttondown patterns — new AI outlets overwhelmingly launch on
  these platforms, and **the platform tells us the feed URL for free**.
- non-English equivalents on a rotation (gov press-release phrasing in
  FR/DE/ES/PT/AR/ZH/JA…).
Run: daily, budget-capped.

### D. New-domain watch — *catches AI news sites the week they launch*
Subscribe to **Certificate Transparency logs** (certstream, free) and/or the
ICANN CZDS zone files (free, application required). Filter the firehose with
a cheap keyword/regex pass (`ai`, `news`, `wire`, `daily`, `brief`, `press`,
exchange tickers…) down to a few hundred domains/day, hold them for ~2 weeks
(so the site exists), then probe. This is how you find sites *before* Google
does. Run: continuous filter, delayed probe.

### E. Curated registries — *gov & exchange completeness*
These are enumerable, so enumerate them:
- Government: the public **.gov registry CSV** (US), **gov.uk organisations
  API**, EU institution list, UN member-state government portals, parliament
  and central-bank lists (BIS publishes all central banks).
- Exchanges/regulators: WFE member list, Wikipedia's list of stock exchanges,
  IOSCO member regulators.
Probe every domain on these lists quarterly — feeds get added/moved and
nobody announces it. Run: quarterly full sweep, monthly for the top tier.

### F. Social & citation mining
Hacker News (Algolia API, free), relevant subreddits (r/artificial, r/ML…),
and the newsletters we already receive: extract every linked domain, lead-queue
the unseen ones. Cheap and surprisingly good for brand-new AI outlets.
Run: daily.

### G. GitHub OPML hunting
`filename:*.opml` and "awesome RSS/news" repo searches via the GitHub API.
People publish their entire curated subscription lists. Diff weekly.

### H. Agent-assisted logged-in pulls — *only where it pays*
Some directories (Feedspot full lists, Inoreader discovery, X lists) show
much more behind a login. Rather than fighting that with scraping code:
- Run a **scheduled browser-agent session** (e.g. Claude with browser use, or
  Playwright with a stored session cookie you provide) once a week per site:
  log in, walk the "new in category" pages, dump candidate URLs to the lead
  queue.
- Keep this in a separate, low-frequency worker. It's the only component
  needing a browser, credentials, or meaningful compute — and it degrades
  gracefully if a site changes.
- Caveat: check each site's ToS; keep volume low and human-ish; you hold the
  accounts.

## 4. Prober

For each lead domain (bounded: ≤ ~15 requests, then never again until rotation):
1. `GET /` → parse `<link rel="alternate" type="application/(rss|atom)+xml">`
   (the standard autodiscovery that covers ~70% of sites).
2. If none: try the well-known path list — `/feed`, `/rss`, `/rss.xml`,
   `/atom.xml`, `/feed.xml`, `/index.xml`, `/?feed=rss2`, `/blog/feed`,
   `/news/rss`, `/feeds/posts/default` — plus platform-specific guesses once
   we fingerprint the generator (WordPress/Ghost/Substack/Hugo/Drupal all
   have fixed conventions; gov CMSs like GovCMS/Drupal too).
3. Check `/sitemap.xml` and `robots.txt` (feeds are often listed there).
4. Record outcome on the domain row (`feeds_found` / `no_feed` /
   `unreachable` + `next_check`). `no_feed` domains get re-probed on a slow
   rotation (~6 months) since sites add feeds later.

## 5. Validator & scorer

- **Validate:** parse with `feedparser`; require ≥1 item; record last-post
  date, items/week over the visible window, language (fast langid on titles),
  full-text vs. snippet, generator, TLS/redirect canonical URL.
- **Dedupe:** canonical-URL match against known + discovered feeds, then
  content fingerprint (simhash over recent titles) to collapse mirrors and
  www/non-www/feedburner duplicates.
- **Rules first:** dead >90 days → reject; <1 item/month → park; obvious
  spam/SEO-farm patterns → reject. This removes ~80% before any LLM sees it.
- **LLM classification (the only AI spend):** for survivors, send title +
  description + 5 recent headlines to a cheap model. Output: category
  (government / exchange-regulator / AI-news / tech / general-news / other),
  language, junk-probability, one-line summary. At the expected volume
  (tens–low hundreds of new validated feeds/day) this is **pennies per day**.

Score = freshness × frequency × category match × source reputation (which
lead generator found it — tracked, so we learn which sources yield keepers).

## 6. Review & handoff

- Score ≥ auto-accept threshold → straight into the Noah Wire registry,
  flagged `auto`.
- Middle band → **daily digest** (email or Slack): name, category, sample
  headlines, score, Approve/Reject links (signed one-click URLs hitting a
  tiny endpoint). Ten minutes of human time a day.
- Registry exports as OPML + JSON; Noah Wire pulls it however it prefers.
- **Health loop:** every registered feed re-checked weekly (conditional GET);
  3 consecutive failures or 60 days silent → flagged for pruning. Dead-feed
  domains go back to the prober (sites move their feeds constantly).

## 7. Footprint & cost

| Item | Estimate |
|---|---|
| 1 small VM (or fly.io/Hetzner box) running all workers | $5–15/mo |
| Brave/Bing search API | $10–30/mo |
| CT-log / CZDS / HN / GitHub / Feedly free tiers | $0 |
| LLM classification (Haiku-class, survivors only) | <$10/mo |
| Optional weekly browser-agent sessions | small, bounded |

Stack: Python, `httpx` + `feedparser` + `selectolax`, APScheduler (or plain
cron), SQLite → Postgres, ~2–3k lines total. No queues/brokers needed at this
scale; the DB *is* the queue.

## 8. Phased build

1. **Phase 1 (week 1):** DB schema, prober, validator, dedupe; run the
   supplied known list through it (validates plumbing + immediately finds
   feeds the known sites have but Noah Wire doesn't track). Lead generator A.
2. **Phase 2 (week 2):** Directories (B), search dorking (C), curated
   gov/exchange registries (E), LLM scorer, daily digest with approve links.
3. **Phase 3 (week 3+):** CT-log new-domain watch (D), social mining (F),
   GitHub OPML (G), health/pruning loop, source-effectiveness dashboard.
4. **Phase 4 (when justified by Phase 2 data):** agent-assisted logged-in
   directory pulls (H).

## 9. Open questions

- Where should this live/deploy (existing Noah Wire infra vs. standalone box)?
- Format/location of the known-feed list, and does Noah Wire store fetched
  article HTML we can mine for citations (big free win for generator A)?
- Preferred digest channel (email vs. Slack) and auto-accept appetite?
- Which directory sites do you already hold accounts on, for Phase 4?
