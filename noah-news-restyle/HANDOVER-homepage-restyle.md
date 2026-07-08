# Homepage below-the-fold restyle — handover
### Broadsheet standardisation + Noah MCP skyscraper · 2026-07-08

Prepared and visually verified against the live built page (desktop 1440px and
mobile 375px — no sideways scroll). Two files ship with this note:

| File | What it is |
|---|---|
| `noah-hp-addon.css` | CSS block to add to `templates/homepage.html` |
| `noah-skyscraper.html` | New inner markup for the `bs-adslot` ad slot |

## What it changes

1. **Front sheet right rail (slide 1)** — the "this slot is reserved" box
   becomes a navy skyscraper house ad for the **Noah MCP** ("Supercharge your
   AI") that stretches to fill whatever height the centre column produces, so
   the rail never shows dead white space. CTA is a `contact us` link over
   `mailto:contact@noahwire.com` — no visible email (golden rule kept).

2. **Today's Risk Read (slide 2)** — out of the grey band and floating card,
   onto the sheet: white background, 2px black top rule, Baskerville flagship
   headline, hairline-ruled chart card. The decision rail becomes a navy panel
   echoing the hero data strip.

3. **Daily signal screens (slide 3)** — floating gold-topped cards become
   ruled newspaper columns: hairline verticals between screens, hairline base
   rules, Baskerville section head. Works at any column count/viewport.

4. **Sherlock rail** — beige pills become a movers board: tabular green
   percentages, ruled rows, three columns.

5. **Desk universes** — every `hp-desk-label` becomes a proper section front
   (double black rule + desk-coloured eyebrow + Baskerville title) and carries
   a `--desk` accent variable per desk:
   - `risk` → burnished gold `#6e5810`
   - `geo` → chart green `#1d5c45`
   - `poly` → market navy `#1F3D80`
   - `wires` → signal red `#E02236`
   New desks = one new `--desk-*` line + one attribute. Same skeleton, its own
   colour world.

## How to apply (on the Mac mini)

```bash
ssh ivan@100.118.93.12 && cd ~/noah-news
# pause the :17 cron first
crontab -l | sed 's|^17 \* \* \* \* /bin/bash /Users/ivan/noah-news/update.sh|#PAUSE# &|' | crontab -
ps aux | grep build.py | grep -v grep
```

**Step 1 — CSS.** Paste the whole of `noah-hp-addon.css` at the **end** of the
big `<style>` block in `templates/homepage.html`. Position matters: several
builders inject their own `<style>` blocks into the body, so the addon uses
higher-specificity selectors (`section.hp-desk-label …`, `.sherlock-rail .sr-…`)
to win regardless — but keeping it last in the template block is the intended
home.

**Step 2 — Skyscraper markup.** Find where the homepage builder emits
`<div class="bs-adslot">` (grep `bs-adslot` in `scripts/build_categories.py`)
and replace that block's HTML with the contents of `noah-skyscraper.html`.

**Step 3 — Desk attribute (one line per desk).** Where the builders emit
`<section class="hp-desk-label"`, add a `data-desk` attribute:

```html
<section class="hp-desk-label" data-desk="risk"  …>  <!-- Insurance & Underwriting -->
<section class="hp-desk-label" data-desk="geo"   …>  <!-- Geopolitical Forecast -->
<section class="hp-desk-label" data-desk="poly"  …>  <!-- Polymarket & Predictions -->
<section class="hp-desk-label" data-desk="wires" …>  <!-- News Wires -->
```

Without the attribute everything still works — labels just fall back to navy.

**Step 4 — build, preview, deploy.**

```bash
bash serve.sh                        # eyeball http://127.0.0.1:8095 at 1440px and 375px
python3 scripts/build.py
python3 scripts/build_categories.py
/usr/bin/python3 scripts/deploy_delta.py 1800
crontab -l | sed 's|^#PAUSE# 17|17|' | crontab -
curl -sS "https://noah-news.com/?cb=$(date +%s)" | grep -o "sky-ad" | head -1   # confirm live
```

## Known nits / follow-ups

- **Sherlock question text is truncated in the data** ("…reach at least 10.0% in 202").
  That's the builder cutting at ~48 chars before the HTML is written. Now that
  rows are full-width ruled entries, let the builder emit the full question and
  add `white-space:nowrap;overflow:hidden;text-overflow:ellipsis` to
  `.sherlock-rail .sr-pill span` if clamping is still wanted.
- `ffeat` ("The Reports") was left untouched — it already reads broadsheet.
  If you want it tightened to the same 2px-rule pattern, it's a 10-line variant
  of the risk-grid head treatment.
- The `promo` video bands and `pred-hp-wins` (slide 4) untouched, as requested.
