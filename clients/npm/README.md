# @agcl/client

<p align="left">
  <img src="../../docs/assets/typescript.svg" width="22" alt="TypeScript" />&nbsp;
  <img src="../../docs/assets/npm.svg" width="22" alt="npm" />&nbsp;
  <img src="../../docs/assets/nodedotjs.svg" width="22" alt="Node.js" />&nbsp;
  <a href="https://img.shields.io/badge/runtime-node%2018%2B%20%7C%20bun%20%7C%20deno%20%7C%20browser-3C873A"><img alt="Runtime" src="https://img.shields.io/badge/runtime-node%2018%2B%20%7C%20bun%20%7C%20deno%20%7C%20browser-3C873A"></a>
</p>

TypeScript client for the AGCL node server.

Mirrors every `/node/*` route, plus the toolkit layer
(`/node/toolkit/*`) that drives LiteLLM, Ollama, vLLM, Redis, S3,
Cloudflare relay, Docker / Helm emitters, and WebRTC signaling.

---

## Install

```bash
npm install @agcl/client
# or: pnpm add / yarn add / bun add
```

Deno (no install — direct npm import):

```ts
import { AgclClient } from "npm:@agcl/client";
```

---

## Quick start

```ts
import { AgclClient } from "@agcl/client";

const agcl = new AgclClient({
  host: "localhost",
  port: 9876,
  authKey: process.env.AGCL_KEY!,
});

if (!(await agcl.verify())) throw new Error("invalid auth key");

for await (const ev of agcl.streamMas({ message: "explain entanglement" })) {
  if (ev.event === "stage1_step") console.log(`A ${ev.step}/${ev.total_steps}`);
  if (ev.event === "answer")      console.log(ev.text);
}
```

---

## Core surface

| Method | Endpoint |
|---|---|
| `verify()` | `POST /node/auth/verify` |
| `info()` / `health()` | `GET /node/info` / `GET /node/health` |
| `getConfig()` / `patchConfig(p)` | `GET / PATCH /node/config` |
| `getMasJson()` / `putMasJson(spec)` | `GET / PUT /node/config/mas` |
| `runMas(body)` | `POST /node/mas/run` (blocking) |
| `streamMas(body)` | `POST /node/mas/stream` (async iterator) |
| `rebuildMas()` | `POST /node/mas/rebuild` |
| `pause(sid)` / `resume(sid)` / `halt(sid)` | session control |
| `latent(sid)` | `GET /node/mas/sessions/{sid}/latent` |
| `listTopics()` / `deleteTopic(id)` | trained-state index |
| `chatStream(sid, body)` | `POST /node/chat/{sid}` (SSE) |

---

## Toolkit surface (`@agcl/client/toolkit`)

```ts
import { AgclClient, ToolkitClient } from "@agcl/client";

const agcl = new AgclClient({ host: "localhost", port: 9876, authKey });
const tk = new ToolkitClient(agcl);

// What's wired up?
console.log(await tk.discover());

// Reachability
console.log(await tk.ping("ollama"));

// Chat through whichever backend the server picks
const r = await tk.chat({
  messages: [{ role: "user", content: "hello" }],
});
console.log(r);

// Stream
for await (const { delta } of tk.chatStream({
  messages: [{ role: "user", content: "explain entanglement" }],
})) process.stdout.write(delta);

// Deploy artifacts straight from the running config
await tk.emitDocker({ gpu: true });
await tk.emitK8s();
```

---

## React hook (optional)

The package exposes a tiny React adapter (zero deps beyond React):

```tsx
import { useEffect, useState } from "react";
import { AgclClient, type MasEvent } from "@agcl/client";

export function useAgclMasStream(client: AgclClient) {
  const [events, setEvents] = useState<MasEvent[]>([]);
  const [running, setRunning] = useState(false);

  async function run(body: { message: string; session_id?: string }) {
    setEvents([]); setRunning(true);
    try {
      for await (const ev of client.streamMas(body)) {
        setEvents(es => [...es, ev]);
        if (ev.event === "done") break;
      }
    } finally { setRunning(false); }
  }
  return { events, running, run };
}
```

---

## Building from source

```bash
cd clients/npm
npm install
npm run build       # → dist/{esm,cjs,types}
npm pack            # produces a .tgz you can install elsewhere
npm publish         # if you own the @agcl scope
```

---

## License

See the [project LICENSE](../../LICENSE) at the repo root.
