# Cloudflare Workers + Durable Objects — edge relay

<p align="left">
  <img src="../../assets/cloudflare.svg" width="22" alt="Cloudflare" />&nbsp;
  <img src="../../assets/cloudflareworkers.svg" width="22" alt="Workers" />&nbsp;
  <a href="https://img.shields.io/badge/PoPs-300%2B-F38020"><img alt="PoPs" src="https://img.shields.io/badge/PoPs-300%2B-F38020"></a>
  <a href="https://img.shields.io/badge/Durable_Objects-stateful-F38020"><img alt="DO" src="https://img.shields.io/badge/Durable_Objects-stateful-F38020"></a>
</p>

**What it does:** Thin edge layer for relay nodes, auth brokers, SSE
fanout, GUI sync, and distributed session routing. Workers run at 300+
edge PoPs globally; Durable Objects give per-session stateful compute.

**AGCL use cases:**

- Remote GUI ↔ local AGCL node relay (no NAT punching)
- SSE token fanout to multiple GUI clients
- Lightweight auth broker sitting in front of vLLM/Ollama
- Distributed session routing across AGCL node cluster

> Part of the **[Cloud / infra integrations](../cloud.md)**.

---

## Install

```bash
npm install -g wrangler
wrangler login
wrangler init agcl-edge-relay
```

---

## `wrangler.jsonc`

```json
{
  "name": "agcl-edge-relay",
  "main": "src/index.ts",
  "compatibility_date": "2025-01-01",
  "durable_objects": {
    "bindings": [
      { "name": "AGCL_SESSION", "class_name": "AgclSession" }
    ]
  },
  "kv_namespaces": [
    { "binding": "AUTH_TOKENS", "id": "your-kv-id" }
  ]
}
```

---

## SSE fanout relay (`src/index.ts`)

```typescript
import { DurableObject } from "cloudflare:workers";

export class AgclSession extends DurableObject {
  private clients: Set<ReadableStreamDefaultController> = new Set();

  async fetch(req: Request): Promise<Response> {
    const url = new URL(req.url);

    if (url.pathname === "/subscribe") {
      // GUI connects here for SSE stream
      const { readable, writable } = new TransformStream();
      const writer = writable.getWriter();
      const encoder = new TextEncoder();

      const ctrl = {
        enqueue: (data: string) =>
          writer.write(encoder.encode(`data: ${data}\n\n`)),
      } as any;
      this.clients.add(ctrl);

      req.signal.addEventListener("abort", () => {
        this.clients.delete(ctrl);
        writer.close();
      });

      return new Response(readable, {
        headers: {
          "Content-Type": "text/event-stream",
          "Cache-Control": "no-cache",
          "Connection": "keep-alive",
        },
      });
    }

    if (url.pathname === "/publish" && req.method === "POST") {
      // AGCL node pushes tokens here
      const { token } = await req.json<{ token: string }>();
      for (const client of this.clients) {
        client.enqueue(token);
      }
      return new Response("ok");
    }

    return new Response("not found", { status: 404 });
  }
}

export default {
  async fetch(req: Request, env: any): Promise<Response> {
    const sessionId = new URL(req.url).searchParams.get("session");
    const id = env.AGCL_SESSION.idFromName(sessionId ?? "default");
    const stub = env.AGCL_SESSION.get(id);
    return stub.fetch(req);
  },
};
```

```bash
wrangler deploy
```

---

## AGCL node → relay integration

```python
import httpx

async def push_token_to_relay(session_id: str, token: str, relay_url: str):
    async with httpx.AsyncClient() as client:
        await client.post(
            f"{relay_url}/publish?session={session_id}",
            json={"token": token},
        )
```

---

## Env vars

```env
AGCL_RELAY_URL=https://agcl-edge-relay.your-subdomain.workers.dev
```
