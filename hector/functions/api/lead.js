// Cloudflare Pages Function: POST /api/lead
// Stores readiness-score leads in the HECTOR_LEADS KV namespace.
// Binding setup: Cloudflare dashboard → Pages project → Settings →
// Functions → KV namespace bindings → add HECTOR_LEADS.

export async function onRequestPost(context) {
  const { request, env } = context;

  let body;
  try {
    body = await request.json();
  } catch {
    return json({ ok: false, error: "invalid json" }, 400);
  }

  const email = String(body.email || "").trim().toLowerCase();
  if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email) || email.length > 254) {
    return json({ ok: false, error: "invalid email" }, 400);
  }

  const record = {
    email,
    score: Number(body.score) || null,
    areas: Array.isArray(body.areas) ? body.areas.slice(0, 10) : null,
    ts: new Date().toISOString(),
    ua: request.headers.get("user-agent") || null,
    country: request.cf?.country || null,
  };

  if (env.HECTOR_LEADS) {
    // One key per submission so repeat takers update their gap profile.
    const key = `lead:${email}:${Date.now()}`;
    await env.HECTOR_LEADS.put(key, JSON.stringify(record));
  } else {
    // Binding not configured yet — log so submissions aren't silently lost.
    console.log("HECTOR_LEADS binding missing; lead:", JSON.stringify(record));
  }

  return json({ ok: true });
}

function json(obj, status = 200) {
  return new Response(JSON.stringify(obj), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}
