"""
Static HTML dashboard served at /node/dashboard.

Single-file SPA. No external runtime deps. Inline SVG charts, no
external CDN. Bearer auth handled by a paste-once form that stores the
key in localStorage and uses it for every subsequent fetch.

Two main panes:
    1. Overview — provider cards (status / model / quota / running totals),
                  sessions table, global timeseries graph (chat vs
                  knowledge tokens).
    2. Session — per-session breakdown, two graphs (chat tokens, knowledge
                 tokens), quick links to /halt /pause /resume on the
                 underlying recursive session.

Plus:
    - Quota editor per provider (max_tokens or max_credit_usd, period).
    - Custom-provider registry (add OpenAI-compatible name+base_url+key+model).
    - API status panel (per-provider configured + reachable).

The HTML is intentionally a single Python string so we can ship it
without docs vs docs-site coordination, and so a `pip install agcl`
gets you a working dashboard out of the box.
"""

HTML = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>AGCL — dashboard</title>
<style>
  :root {
    --fg: #f7f7f7;  --bg: #0a0a0a;
    --muted: #888;  --accent: #f7f7f7;  --warn: #ffb84d;
  }
  @media (prefers-color-scheme: light) {
    :root { --fg: #111; --bg: #fff; --muted: #666; --accent: #111; --warn: #c45a00; }
  }
  * { box-sizing: border-box; }
  html, body { margin: 0; padding: 0; background: var(--bg); color: var(--fg);
    font: 14px/1.4 ui-monospace, "SF Mono", Menlo, monospace; }
  header { padding: 14px 20px; border-bottom: 1px solid var(--fg); display: flex;
    align-items: center; gap: 16px; }
  header h1 { font-size: 14px; margin: 0; letter-spacing: .04em; }
  header nav { margin-left: auto; display: flex; gap: 14px; }
  header nav a { color: var(--fg); text-decoration: none; opacity: .65; cursor: pointer; }
  header nav a.active { opacity: 1; border-bottom: 1px solid var(--fg); }
  main { padding: 20px; max-width: 1200px; margin: 0 auto; }
  section { margin-bottom: 28px; }
  h2 { font-size: 13px; letter-spacing: .08em; text-transform: uppercase;
    margin: 0 0 10px 0; color: var(--muted); }
  table { width: 100%; border-collapse: collapse; }
  th, td { text-align: left; padding: 6px 10px; border-bottom: 1px solid #2a2a2a; }
  th { color: var(--muted); font-weight: 400; }
  .grid { display: grid; gap: 14px; grid-template-columns: repeat(auto-fill,minmax(260px,1fr)); }
  .card { border: 1px solid var(--fg); padding: 12px 14px; }
  .card h3 { margin: 0 0 6px 0; font-size: 13px; }
  .card .row { display: flex; justify-content: space-between; gap: 10px; margin: 2px 0; }
  .card .row .k { color: var(--muted); }
  .pill { display: inline-block; padding: 1px 8px; border: 1px solid var(--fg);
    font-size: 11px; }
  .pill.warn { color: var(--warn); border-color: var(--warn); }
  .pill.dim  { opacity: .55; }
  button, input, select {
    background: transparent; color: var(--fg); border: 1px solid var(--fg);
    padding: 4px 10px; font: inherit; }
  button { cursor: pointer; }
  button:hover { background: var(--fg); color: var(--bg); }
  input[type=text], input[type=number], input[type=password] { min-width: 0; }
  form { display: flex; gap: 6px; flex-wrap: wrap; align-items: center; }
  .auth-pad { padding: 30px; max-width: 480px; margin: 80px auto;
    border: 1px solid var(--fg); }
  .auth-pad input { width: 100%; padding: 10px; }
  .bar { fill: var(--fg); }
  .bar.knowledge { fill: var(--fg); fill-opacity: .35; }
  .axis { stroke: var(--muted); }
  .gridline { stroke: var(--muted); stroke-opacity: .2; stroke-dasharray: 2 3; }
  .small { color: var(--muted); font-size: 12px; }
  .row-flex { display: flex; gap: 12px; flex-wrap: wrap; }
  .ok { color: var(--fg); }
  .off { color: var(--muted); }
  .err { color: var(--warn); }
</style>
</head>
<body>

<div id="auth" class="auth-pad" hidden>
  <h2 style="color:var(--fg)">AGCL — paste your bearer key</h2>
  <p class="small">Your AGCL node printed a key when you ran
    <code>python main.py node</code>. Paste it here. Stays in this tab's
    localStorage; never leaves the browser.</p>
  <form id="auth-form">
    <input type="password" id="auth-key" placeholder="bearer key" autofocus />
    <button type="submit">Connect</button>
  </form>
  <p class="small" id="auth-err" style="color:var(--warn)"></p>
</div>

<div id="app" hidden>
<header>
  <h1>AGCL · dashboard</h1>
  <nav>
    <a id="nav-overview"  class="active">overview</a>
    <a id="nav-sessions">sessions</a>
    <a id="nav-providers">api management</a>
    <a id="nav-logout" style="margin-left:18px;opacity:.5">logout</a>
  </nav>
</header>

<main>

<section id="view-overview">
  <h2>API status</h2>
  <div id="provider-cards" class="grid"></div>

  <h2 style="margin-top:24px">Token usage — chat vs knowledge formation (last hour)</h2>
  <div id="global-chart"></div>
  <p class="small">Solid: chat-path tokens (your turns).
    Hollow: knowledge-formation tokens (cloud-teacher bootstrap, reformulations,
    topic-switch judgements, summary compression).</p>

  <h2 style="margin-top:24px">Sessions</h2>
  <div style="overflow-x:auto">
    <table id="sessions-table">
      <thead><tr>
        <th>session</th><th>chat tokens</th><th>knowledge tokens</th>
        <th>cost</th><th>calls</th><th>last seen</th><th></th>
      </tr></thead>
      <tbody></tbody>
    </table>
  </div>
</section>

<section id="view-sessions" hidden>
  <h2>Session detail</h2>
  <p class="small">Pick a session id from the overview, or paste one:</p>
  <form id="sess-form">
    <input type="text" id="sess-id" placeholder="default" />
    <button type="submit">Load</button>
  </form>
  <div id="sess-detail"></div>
</section>

<section id="view-providers" hidden>
  <h2>API management</h2>
  <p class="small">Set quotas (token cap or credit cap) per provider, and
    register custom OpenAI-compatible providers.</p>

  <h2 style="margin-top:18px">Set quota</h2>
  <form id="quota-form">
    <select id="quota-provider"></select>
    <input type="number" id="quota-tokens"  placeholder="max tokens" min="0" />
    <input type="number" id="quota-credit" step="0.01" placeholder="max USD" min="0" />
    <select id="quota-period">
      <option value="lifetime">lifetime</option>
      <option value="daily">daily</option>
      <option value="monthly">monthly</option>
    </select>
    <button type="submit">Save</button>
    <button type="button" id="quota-clear">Clear</button>
  </form>

  <h2 style="margin-top:24px">Custom OpenAI-compatible provider</h2>
  <form id="custom-form">
    <input type="text" id="cf-name"      placeholder="name (e.g. groq)" required />
    <input type="text" id="cf-base"      placeholder="base_url (e.g. https://api.groq.com/openai/v1)" required style="flex:2" />
    <input type="text" id="cf-keyenv"    placeholder="env var (e.g. GROQ_API_KEY)" required />
    <input type="text" id="cf-model"     placeholder="default model" required />
    <button type="submit">Add</button>
  </form>
  <div style="overflow-x:auto;margin-top:10px">
    <table id="custom-table">
      <thead><tr>
        <th>name</th><th>base_url</th><th>api key env</th><th>model</th><th></th>
      </tr></thead>
      <tbody></tbody>
    </table>
  </div>
</section>

</main>
</div>

<script>
const KEY = "agcl_bearer";
let bearer = localStorage.getItem(KEY) || "";

const $ = q => document.querySelector(q);
const $$ = q => document.querySelectorAll(q);

async function api(path, init = {}) {
  const r = await fetch(path, {
    ...init,
    headers: {
      "Authorization": "Bearer " + bearer,
      "Content-Type": "application/json",
      ...(init.headers || {})
    }
  });
  if (!r.ok) throw new Error(r.status + " " + (await r.text()).slice(0, 160));
  return r.json();
}

async function ensureAuth() {
  if (!bearer) return false;
  try { await api("/node/auth/verify", { method: "POST" }); return true; }
  catch { return false; }
}

function show(id) {
  ["overview", "sessions", "providers"].forEach(v => {
    $("#view-" + v).hidden = v !== id;
    $("#nav-" + v).classList.toggle("active", v === id);
  });
}

function fmt(n) {
  if (n == null) return "—";
  if (n >= 1e6) return (n/1e6).toFixed(1) + "M";
  if (n >= 1e3) return (n/1e3).toFixed(1) + "k";
  return String(n);
}
function fmtCost(usd) { return usd == null ? "—" : "$" + Number(usd).toFixed(4); }
function fmtTime(ts) {
  if (!ts) return "—";
  const d = new Date(ts * 1000);
  return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }) +
    " " + d.toLocaleDateString([], { month: "short", day: "numeric" });
}

// ── Overview ────────────────────────────────────────────────────
async function renderOverview() {
  const summary = await api("/node/usage/summary");
  renderProviderCards(summary.providers);
  renderSessionsTable(summary.sessions);
  await renderGlobalChart();
}

function renderProviderCards(providers) {
  const root = $("#provider-cards");
  root.innerHTML = "";
  for (const [name, p] of Object.entries(providers)) {
    const card = document.createElement("div");
    card.className = "card";
    const cls = p.configured ? "ok" : "off";
    const def = p.default ? `<span class="pill">default</span>` : "";
    const kind = `<span class="pill dim">${p.kind}</span>`;
    const q = p.quota || {};
    let quotaRow = `<div class="row"><span class="k">quota</span><span class="off">none</span></div>`;
    if (q.max_tokens != null) {
      const used = q.used_tokens ?? 0, max = q.max_tokens;
      const pct = max ? Math.min(100, Math.round((used/max)*100)) : 0;
      quotaRow = `<div class="row"><span class="k">tokens (${q.period})</span>
        <span>${fmt(used)}/${fmt(max)} <span class="${pct>=90?"err":""}">${pct}%</span></span></div>`;
    } else if (q.max_credit_usd != null) {
      const used = q.used_cost_usd ?? 0, max = q.max_credit_usd;
      const pct = max ? Math.min(100, Math.round((used/max)*100)) : 0;
      quotaRow = `<div class="row"><span class="k">credit (${q.period})</span>
        <span>${fmtCost(used)}/${fmtCost(max)} <span class="${pct>=90?"err":""}">${pct}%</span></span></div>`;
    }
    card.innerHTML = `
      <h3>${name} ${def} ${kind}</h3>
      <div class="row"><span class="k">model</span><span>${p.model || "—"}</span></div>
      <div class="row"><span class="k">status</span><span class="${cls}">${p.configured ? "configured" : "no key"}</span></div>
      <div class="row"><span class="k">tokens (lifetime)</span><span>${fmt(p.lifetime?.tokens ?? 0)}</span></div>
      <div class="row"><span class="k">cost (lifetime)</span><span>${fmtCost(p.lifetime?.cost_usd ?? 0)}</span></div>
      ${quotaRow}
    `;
    root.appendChild(card);
  }
  // Populate quota provider select on management view too.
  const sel = $("#quota-provider");
  sel.innerHTML = "";
  for (const name of Object.keys(providers)) {
    const o = document.createElement("option");
    o.value = name; o.textContent = name;
    sel.appendChild(o);
  }
}

function renderSessionsTable(sessions) {
  const tbody = $("#sessions-table tbody");
  tbody.innerHTML = "";
  for (const s of sessions) {
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td><a class="link" data-sid="${s.session_id}" style="cursor:pointer;text-decoration:underline">${s.session_id}</a></td>
      <td>${fmt(s.chat_tokens)}</td>
      <td>${fmt(s.knowledge_tokens)}</td>
      <td>${fmtCost(s.cost_usd)}</td>
      <td>${s.calls}</td>
      <td>${fmtTime(s.last_ts)}</td>
      <td><button data-sid="${s.session_id}" class="open">open →</button></td>
    `;
    tbody.appendChild(tr);
  }
  $$("#sessions-table button.open, #sessions-table a.link").forEach(b =>
    b.addEventListener("click", () => {
      $("#sess-id").value = b.dataset.sid;
      show("sessions");
      loadSession(b.dataset.sid);
    }));
}

async function renderGlobalChart() {
  const chat = await api("/node/usage/timeseries?bucket_sec=60&lookback_sec=3600&kind=chat");
  const know = await api("/node/usage/timeseries?bucket_sec=60&lookback_sec=3600&kind=knowledge");
  $("#global-chart").innerHTML = svgBars(chat.buckets, know.buckets, 880, 200);
}

function svgBars(chatBuckets, knowBuckets, w, h) {
  const all = [...chatBuckets, ...knowBuckets];
  if (!all.length) return `<p class="small">No usage data yet.</p>`;
  const tMin = Math.min(...all.map(b => b.ts));
  const tMax = Math.max(...all.map(b => b.ts));
  const span = Math.max(1, tMax - tMin);
  const yMax = Math.max(1, ...all.map(b => b.tokens));
  const padL = 36, padB = 22, padT = 6, padR = 6;
  const ix = t => padL + ((t - tMin) / span) * (w - padL - padR);
  const iy = v => h - padB - (v / yMax) * (h - padB - padT);
  const bw = Math.max(2, (w - padL - padR) / Math.max(20, all.length));

  const bars = (arr, cls) => arr.map(b => {
    const x = ix(b.ts), y = iy(b.tokens);
    return `<rect class="bar ${cls}" x="${x.toFixed(1)}" y="${y.toFixed(1)}"
              width="${bw.toFixed(1)}" height="${(h - padB - y).toFixed(1)}" />`;
  }).join("");

  const ticks = [0, .25, .5, .75, 1].map(p => {
    const y = padT + p * (h - padB - padT);
    const v = Math.round(yMax * (1 - p));
    return `<line class="gridline" x1="${padL}" x2="${w - padR}" y1="${y}" y2="${y}" />
            <text class="small" x="2" y="${y + 4}" fill="currentColor">${fmt(v)}</text>`;
  }).join("");

  return `<svg viewBox="0 0 ${w} ${h}" width="100%" height="${h}" preserveAspectRatio="none">
    ${ticks}
    ${bars(knowBuckets, "knowledge")}
    ${bars(chatBuckets, "")}
    <line class="axis" x1="${padL}" x2="${padL}" y1="${padT}" y2="${h - padB}" />
    <line class="axis" x1="${padL}" x2="${w - padR}" y1="${h - padB}" y2="${h - padB}" />
  </svg>`;
}

// ── Session detail ─────────────────────────────────────────────
async function loadSession(sid) {
  const detail = await api(`/node/usage/sessions/${encodeURIComponent(sid)}`);
  const ts = await api(`/node/usage/timeseries?session_id=${encodeURIComponent(sid)}&bucket_sec=60&lookback_sec=86400`);
  const chat = ts.buckets.filter(b => true); // already aggregated; we'll split by re-querying kind
  const chatTs = await api(`/node/usage/timeseries?session_id=${encodeURIComponent(sid)}&kind=chat&bucket_sec=60&lookback_sec=86400`);
  const knowTs = await api(`/node/usage/timeseries?session_id=${encodeURIComponent(sid)}&kind=knowledge&bucket_sec=60&lookback_sec=86400`);

  let kindRows = "";
  for (const [k, b] of Object.entries(detail.by_kind || {})) {
    kindRows += `<tr><td>${k}</td><td>${fmt(b.in_tokens)}</td>
      <td>${fmt(b.out_tokens)}</td><td>${fmt(b.tokens)}</td>
      <td>${b.calls}</td><td>${fmtCost(b.cost_usd)}</td></tr>`;
  }

  $("#sess-detail").innerHTML = `
    <div class="card" style="margin-top:14px">
      <h3>${sid}</h3>
      <div class="row"><span class="k">total cost</span><span>${fmtCost(detail.total_cost_usd)}</span></div>
    </div>
    <h2 style="margin-top:18px">Chat tokens (last 24h)</h2>
    ${svgBars(chatTs.buckets, [], 880, 160)}
    <h2 style="margin-top:18px">Knowledge-formation tokens (last 24h)</h2>
    ${svgBars([], knowTs.buckets, 880, 160)}
    <h2 style="margin-top:18px">Per-kind breakdown</h2>
    <table>
      <thead><tr><th>kind</th><th>in</th><th>out</th><th>total</th><th>calls</th><th>cost</th></tr></thead>
      <tbody>${kindRows || `<tr><td colspan="6" class="small">no data yet</td></tr>`}</tbody>
    </table>
  `;
}

// ── API management ─────────────────────────────────────────────
async function renderProvidersView() {
  await renderOverview();   // reuses provider list + populates select
  const customs = await api("/node/usage/providers/custom");
  const tbody = $("#custom-table tbody");
  tbody.innerHTML = "";
  for (const p of customs.providers) {
    const tr = document.createElement("tr");
    tr.innerHTML = `<td>${p.name}</td><td>${p.base_url}</td>
      <td>${p.api_key_env}</td><td>${p.model}</td>
      <td><button data-name="${p.name}" class="del">remove</button></td>`;
    tbody.appendChild(tr);
  }
  $$("#custom-table button.del").forEach(b => b.addEventListener("click", async () => {
    await api(`/node/usage/providers/custom/${encodeURIComponent(b.dataset.name)}`, { method: "DELETE" });
    renderProvidersView();
  }));
}

// ── Init ───────────────────────────────────────────────────────
document.getElementById("auth-form").addEventListener("submit", async e => {
  e.preventDefault();
  const v = $("#auth-key").value.trim();
  if (!v) return;
  bearer = v; localStorage.setItem(KEY, v);
  if (await ensureAuth()) {
    $("#auth").hidden = true;
    $("#app").hidden = false;
    renderOverview();
  } else {
    $("#auth-err").textContent = "invalid key";
  }
});

document.getElementById("nav-overview").addEventListener("click",  () => { show("overview"); renderOverview(); });
document.getElementById("nav-sessions").addEventListener("click",  () => show("sessions"));
document.getElementById("nav-providers").addEventListener("click", () => { show("providers"); renderProvidersView(); });
document.getElementById("nav-logout").addEventListener("click", () => {
  localStorage.removeItem(KEY); bearer = "";
  $("#app").hidden = true; $("#auth").hidden = false;
  $("#auth-key").value = "";
});
document.getElementById("sess-form").addEventListener("submit", e => {
  e.preventDefault(); loadSession($("#sess-id").value.trim() || "default");
});
document.getElementById("quota-form").addEventListener("submit", async e => {
  e.preventDefault();
  const provider = $("#quota-provider").value;
  const body = {
    period: $("#quota-period").value,
    max_tokens:     $("#quota-tokens").value  ? Number($("#quota-tokens").value)  : null,
    max_credit_usd: $("#quota-credit").value  ? Number($("#quota-credit").value)  : null,
  };
  await api(`/node/usage/quota/${encodeURIComponent(provider)}`, {
    method: "PUT", body: JSON.stringify(body),
  });
  renderOverview();
});
document.getElementById("quota-clear").addEventListener("click", async () => {
  const provider = $("#quota-provider").value;
  await api(`/node/usage/quota/${encodeURIComponent(provider)}`, { method: "DELETE" });
  renderOverview();
});
document.getElementById("custom-form").addEventListener("submit", async e => {
  e.preventDefault();
  const body = {
    name:        $("#cf-name").value.trim(),
    base_url:    $("#cf-base").value.trim(),
    api_key_env: $("#cf-keyenv").value.trim(),
    model:       $("#cf-model").value.trim(),
  };
  await api("/node/usage/providers/custom", {
    method: "POST", body: JSON.stringify(body),
  });
  e.target.reset();
  renderProvidersView();
});

// boot
(async () => {
  if (await ensureAuth()) {
    $("#app").hidden = false;
    renderOverview();
  } else {
    $("#auth").hidden = false;
  }
})();
</script>
</body>
</html>
"""
