# Hector — Proposition Design

The compliance co-pilot for small FCA-regulated firms. This document records the
product decisions embodied in the landing page (`index.html`), and the evidence
behind them, so the page and the product stay honest with each other.

## One-line proposition

**The compliance file that keeps itself.** Consumer Duty and SM&CR evidence,
collected continuously; the annual board report drafted in an afternoon.

## Who it's for (ICP)

- Directly-authorised retail advice firms with 1–5 advisers (~87% of the
  5,500 UK advice firms, per the FCA's 2025 advice market survey)
- Small insurance and mortgage intermediaries
- Principals/networks overseeing appointed representatives (channel play)

The buyer is the user: the principal/SMF16 who currently does compliance
admin on evenings and weekends.

## Why now (forced demand)

- Consumer Duty requires an annual board report, ongoing outcomes monitoring
  and an annual compliance confirmation — for every firm, including
  one-person firms.
- The FCA reviewed 180 firms' board reports, found data quality the most
  common failing, and updated its guidance in Feb 2026 specifically for
  smaller firms — suggesting they find a "critical friend."
- PS26/6 (Apr 2026) concedes SM&CR is disproportionate for small firms; the
  burden is acknowledged, not removed.

## What it does (scope for v1)

1. **Guided intake (~40 min):** plain-English questionnaire + document upload
   (policies, complaints log, file reviews). Builds the firm's compliance
   profile against the four Duty outcomes and SM&CR responsibilities.
2. **Continuous file-keeping (10 min/month):** monthly evidence prompts;
   complaints/FOS/file-review MI tracked per outcome; FCA publications
   monitored and translated ("what this means for a firm your size").
3. **One-button outputs:** annual Consumer Duty board report drafted from the
   firm's own evidence (every assertion footnoted to a source document);
   outcomes MI pack; SM&CR responsibilities map and Statements of
   Responsibilities; annual F&P certification records; audit-trailed
   evidence vault.

Explicitly **not** in scope: regulated advice, suitability judgements,
compliance sign-off. Hector is evidence assembly — "the world's most diligent
compliance administrator, not a compliance officer." This is both the legal
position and the positioning vs. consultancies (partner, don't fight).

## Pricing

| Tier | Price | For |
|---|---|---|
| Solo | £149/mo | One regulated individual / SMF holder |
| Firm | £249/mo | Up to 5 regulated individuals (anchor tier) |
| Principal | from £499/mo | Networks/principals, per-AR pricing, white-label |

- Anchored against the consultancy floor: small firms pay £190–£654/mo + VAT
  (SimplyBiz: 2.5% of turnover, min £190.81, annual uplift of 5% or
  inflation, whichever is higher).
- Annual = 2 months free. VAT at checkout. Self-serve only — no sales calls.
- **Price-lock promise:** no mid-term increases; renewal capped at CPI,
  contractually. Direct strike at the incumbents' most-cited complaint
  (30–50% renewal hikes at Vanta/Drata; consultancy annual uplifts).

## Go-to-market

1. **Lead magnet:** free Consumer Duty readiness score (15 questions, scored
   against the FCA's board-report findings). Captures email; validates
   demand pre-build.
2. **SEO on panic queries:** "Consumer Duty board report template small
   firm", "SM&CR responsibilities map example", etc.
3. **Trade press & community:** Money Marketing, FT Adviser, adviser forums.
4. **Channel:** principals and networks (one deal = hundreds of ARs);
   compliance consultancies as partners (Hector produces the file they
   review).
5. **Founder story:** former regulated principal who built his first firm for
   people the industry overlooked — now for the firms the compliance
   industry overlooks.

## Known risks

- Directly-authorised firm base shrinking (~15% since 2021, consolidation).
- Small firms value a human "critical friend" (FCA endorses this) —
  mitigated by positioning alongside consultants, not against them.
- Liability sensitivity around "AI-written" compliance output — mitigated by
  evidence-assembly framing, footnoted sources, human approval step.
- RegTechPRO is the nearest small-firm competitor (entry pricing ambiguous:
  listed ~£1,200/yr but modules £250–£500/mo) — watch closely.

## Validation before building software

15 conversations with small-firm principals. One question: "Show me how you
produced last year's Consumer Duty board report, and what it cost you."
Build if the answer is "painfully, in Word, plus my £400/month consultant."
The readiness-score waitlist conversion is the quantitative signal.
