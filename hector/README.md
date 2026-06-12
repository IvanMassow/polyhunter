# Hector — site & readiness score

Marketing site, product mockups and the readiness-score questionnaire for
Hector, the compliance co-pilot for small FCA-regulated firms.
See `PROPOSITION.md` for the product thinking.

## Structure

- `index.html` — marketing page (static, self-contained)
- `score/index.html` — Consumer Duty readiness questionnaire (client-side
  scoring; only email + headline score leave the browser)
- `functions/api/lead.js` — Cloudflare Pages Function that stores email
  captures in KV
- `mockups/` — high-fidelity product UI mockups (double as the v1 spec)
- `assets/` — screenshots of the mockups used on the marketing page

## Deploy to Cloudflare Pages (~5 minutes)

1. Cloudflare dashboard → **Workers & Pages → Create → Pages →
   Connect to Git** → select this repository.
2. Build settings: framework preset **None**, build command **(empty)**,
   **root directory: `hector`**, output directory **(empty / root)**.
   Functions in `hector/functions/` are picked up automatically.
3. Create a KV namespace (**Storage & Databases → KV**) called
   `hector-leads`, then bind it to the Pages project:
   **Settings → Functions → KV namespace bindings** →
   variable name `HECTOR_LEADS`.
4. **Custom domains** → add the purchased domain. If it was bought via
   Cloudflare Registrar, DNS is configured automatically.
5. Every push to the production branch redeploys the site.

Reading captured leads: **Storage & Databases → KV → hector-leads** in the
dashboard, or `npx wrangler kv key list --namespace-id <id>`.

## Local preview

Static pages open directly in a browser. To exercise the lead endpoint
locally: `npx wrangler pages dev hector`.
