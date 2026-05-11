# Deploy the AGCL Cloudflare edge relay

A Cloudflare Worker that fronts your AGCL node so multiple GUI clients
can subscribe to the same session through a public, NAT-traversable
URL. Useful when you want to share a chat across browsers, devices,
or co-workers without exposing your home IP.

> Source: [agcl/toolkit/cloudflare.py](../../agcl/toolkit/cloudflare.py)
> · reference: [docs/integrations/cloud/cloudflare.md](../integrations/cloud/cloudflare.md)

---

## What you're building

```
   Browsers (anywhere)
   ────────────────────
              ▲
              │  GET /subscribe?session=X   (SSE — receives events)
              │
   ┌──────────┴───────────────┐
   │  Cloudflare Worker        │
   │  + Durable Object         │
   │  (the "relay")             │
   └──────────┬───────────────┘
              ▲
              │  POST /publish?session=X    (token / event push)
              │
   Your AGCL node (anywhere — laptop, VPS, Cloud Run, …)
```

Your node doesn't need a public IP; it just `POST`s to the Worker
whenever it has a new token. Browsers subscribe to the Worker's SSE
endpoint to receive the stream. The Worker enforces a bearer token
on both publish + subscribe.

## Prerequisites

| Need                                                  | How                                              |
|-------------------------------------------------------|--------------------------------------------------|
| A Cloudflare account                                  | dash.cloudflare.com (free tier is enough)       |
| `wrangler` CLI installed                              | `npm i -g wrangler` (or `npx wrangler …`)        |
| A workers.dev subdomain or a custom domain on CF      | auto on signup / activate in dashboard           |

Worker free tier: **100,000 requests/day**, **10ms CPU/request**.
Durable Objects: paid feature (~$0.20/M requests) but you only pay
when you publish.

---

## Step 1 — Initialize a Worker project

The Worker source isn't shipped in this repo — you scaffold it
separately because it lives on Cloudflare's infrastructure. A minimal
template:

```bash
mkdir agcl-relay && cd agcl-relay
wrangler init . -y
```

Replace `src/index.js` with:

```js
// Minimal AGCL edge relay.
// Two routes:
//   POST /publish?session=ID   accept event, fan out to subscribers
//   GET  /subscribe?session=ID return SSE stream
// Each session gets its own Durable Object instance for ordering.

export default {
  async fetch(req, env) {
    const url = new URL(req.url);
    const session = url.searchParams.get("session") || "default";

    // Auth (skip for OPTIONS preflight)
    if (req.method !== "OPTIONS") {
      const expected = `Bearer ${env.RELAY_TOKEN}`;
      const got = req.headers.get("authorization") || "";
      if (env.RELAY_TOKEN && got !== expected) {
        return new Response("unauthorized", { status: 401, headers: cors() });
      }
    }

    if (req.method === "OPTIONS") return new Response(null, { headers: cors() });
    if (url.pathname === "/" && req.method === "GET")
      return new Response("AGCL relay", { headers: { ...cors(), "content-type": "text/plain" } });

    // Forward to the Durable Object for this session
    const id = env.RELAY.idFromName(session);
    const stub = env.RELAY.get(id);
    return stub.fetch(req);
  },
};

export class Relay {
  constructor(state, env) { this.state = state; this.subs = []; }

  async fetch(req) {
    const url = new URL(req.url);

    if (url.pathname === "/publish" && req.method === "POST") {
      const event = await req.json();
      const data = `data: ${JSON.stringify(event)}\n\n`;
      for (const c of this.subs) {
        try { c.controller.enqueue(c.enc.encode(data)); } catch (_) {}
      }
      return new Response(JSON.stringify({ ok: true, subs: this.subs.length }),
        { headers: { ...cors(), "content-type": "application/json" } });
    }

    if (url.pathname === "/subscribe" && req.method === "GET") {
      const enc = new TextEncoder();
      let controller;
      const stream = new ReadableStream({
        start(c) { controller = c; },
        cancel() {
          this.subs = this.subs.filter(x => x.controller !== controller);
        },
      });
      const sub = { controller, enc };
      this.subs.push(sub);
      // Keepalive every 15s
      const ka = setInterval(() => {
        try { controller.enqueue(enc.encode(": keepalive\n\n")); }
        catch (_) { clearInterval(ka); }
      }, 15000);
      return new Response(stream, {
        headers: { ...cors(),
          "content-type": "text/event-stream",
          "cache-control": "no-cache, no-transform",
          "connection": "keep-alive" },
      });
    }

    return new Response("not found", { status: 404, headers: cors() });
  }
}

function cors() {
  return {
    "access-control-allow-origin": "*",
    "access-control-allow-headers": "Authorization, Content-Type",
    "access-control-allow-methods": "GET, POST, OPTIONS",
  };
}
```

And `wrangler.toml`:

```toml
name = "agcl-relay"
main = "src/index.js"
compatibility_date = "2024-11-01"

[[durable_objects.bindings]]
name = "RELAY"
class_name = "Relay"

[[migrations]]
tag = "v1"
new_classes = ["Relay"]

# secret bound via `wrangler secret put RELAY_TOKEN`
```

---

## Step 2 — Set the relay token

The Worker enforces a shared bearer between AGCL and the GUI clients.
Pick a strong random value:

```bash
wrangler secret put RELAY_TOKEN
# paste a value generated with: openssl rand -hex 32
```

Save the same value somewhere local — you'll put it in AGCL's `.env`
in step 4.

---

## Step 3 — Deploy

```bash
wrangler deploy
```

You'll get a URL like `https://agcl-relay.<your-subdomain>.workers.dev`.

Smoke-test:

```bash
TOKEN=…the value from step 2…
URL=https://agcl-relay.<sub>.workers.dev

# 1. Subscribe in one terminal
curl -N -H "Authorization: Bearer $TOKEN" "$URL/subscribe?session=demo"

# 2. Publish in another
curl -X POST -H "Authorization: Bearer $TOKEN" \
     -H "Content-Type: application/json" \
     -d '{"hello": "world"}' \
     "$URL/publish?session=demo"
```

The subscribe terminal should print:

```
data: {"hello":"world"}
```

---

## Step 4 — Wire AGCL to the relay

Add the relay URL + token to your `.env`:

```bash
echo "AGCL_RELAY_URL=https://agcl-relay.<sub>.workers.dev" >> .env
echo "AGCL_RELAY_TOKEN=…the value from step 2…"           >> .env
```

Or hot-load via the GUI: Setup → Cloud providers → **Reload .env**.

Now `agcl.toolkit.cloudflare.publish()` is live — every
`POST /node/toolkit/relay/publish` request from your node fans out
through the Worker to all subscribers.

---

## Step 5 — Use it from the GUI

The bundled web console doesn't subscribe to the relay by default —
it talks directly to your node. To consume relay events instead, in
a custom client:

```js
const ev = new EventSource(
  `https://agcl-relay.<sub>.workers.dev/subscribe?session=${sessionId}`,
  { withCredentials: false }
);
// EventSource doesn't allow custom headers; for an auth-gated relay,
// switch to fetch+ReadableStream like web_socket/js/views/setup.js does.
ev.onmessage = (e) => {
  const data = JSON.parse(e.data);
  console.log("relay event:", data);
};
```

For a worked example see the SSE follower in
[web_socket/js/views/setup.js](../../web_socket/js/views/setup.js#L240).

---

## Verify it works

| Check                                              | Expected                                                    |
|----------------------------------------------------|--------------------------------------------------------------|
| `curl https://…workers.dev/`                        | `AGCL relay` (plain-text 200)                                |
| `curl /subscribe` without `Authorization` header   | `401 unauthorized`                                           |
| AGCL `GET /node/toolkit/ping/cloudflare`           | `{"ok": true, "status": 200, "url": "https://…"}`             |
| AGCL `POST /node/toolkit/relay/publish` with body  | `{"ok": true}` and the data appears on the subscribe stream  |

---

## Cost-control notes

- The free tier is generous, but `/subscribe` holds connections open
  — Workers count each million wall-clock-seconds against your quota.
- Durable Objects bill per request and per active wall-clock time.
  For 24/7 streaming, expect ~$1–$5/month for a personal node.
- If you don't need persistence across Worker restarts, you can
  remove Durable Objects entirely and keep the subscriber set in
  the global scope — see the simpler topology in
  [docs/integrations/cloud/cloudflare.md](../integrations/cloud/cloudflare.md).

---

## Common problems

| Symptom                                          | Fix                                                                                                |
|--------------------------------------------------|----------------------------------------------------------------------------------------------------|
| `wrangler deploy` → "RELAY class not found"      | You skipped the `[[migrations]]` block in `wrangler.toml`. Add it and re-deploy.                    |
| Worker returns 200 but events never arrive       | Subscribers connected BEFORE publishers — that's fine; check that both used the same `?session=…`. |
| `curl /subscribe` hangs forever (good!) but Ctrl-C never returns | EventSource is meant to be long-lived; the hang IS the stream. Use `curl -N` and Ctrl-C. |
| Browser CORS error                                | Worker only allows `*` for now. Lock down `access-control-allow-origin` in `src/index.js`.          |
| 100k requests/day exceeded                       | Upgrade to the paid Workers plan (~$5/mo) or split sessions across multiple Workers.                |
| Subscribers slowly drift out of sync             | The Worker doesn't replay — if a subscriber missed events, they're gone. For replay, use a queue (Cloudflare Queues / Kafka) instead. |

---

## What to read next

- The deeper reference (Durable Object internals, sharding): [docs/integrations/cloud/cloudflare.md](../integrations/cloud/cloudflare.md)
- WebRTC alternative (peer-to-peer, no relay): [webrtc.md](webrtc.md)
- Putting AGCL itself behind Cloudflare's network for DDoS protection: [docs/security.md](../security.md#2-transport-security)
