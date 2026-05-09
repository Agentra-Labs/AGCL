# npm — JavaScript / TypeScript client

<p align="left">
  <img src="../assets/npm.svg" width="22" alt="npm" />&nbsp;
  <img src="../assets/typescript.svg" width="22" alt="TypeScript" />&nbsp;
  <img src="../assets/nodedotjs.svg" width="22" alt="Node.js" />&nbsp;
  <a href="https://img.shields.io/badge/runtime-node%20%7C%20bun%20%7C%20deno%20%7C%20browser-3C873A"><img alt="Runtime" src="https://img.shields.io/badge/runtime-node%20%7C%20bun%20%7C%20deno%20%7C%20browser-3C873A"></a>
  <a href="https://img.shields.io/badge/status-source%20only-yellow"><img alt="Status" src="https://img.shields.io/badge/status-source%20only-yellow"></a>
</p>

A TypeScript client for the AGCL node server. Mirrors every `/node/*`
route plus the `/node/toolkit/*` surface. Source lives at
[`clients/npm/`](../../clients/npm/) — runs on Node 18+, Bun, Deno,
Electron, and the browser.

> Part of the **[Agent-platform integrations](../integrations.md)**.
> If you only need a one-file client, see the inline
> [TypeScript sample](../gui/typescript-client.md).

> **Status:** the package is **not yet published** to the public npm
> registry. Use one of the build-from-source paths below until it is.

---

## Install (build from source)

```bash
# from the AGCL checkout
cd clients/npm
npm install
npm run build           # -> dist/{esm,cjs,types}
npm pack                # produces agcl-client-0.1.0.tgz

# in your app
npm install /path/to/AGCL/clients/npm/agcl-client-0.1.0.tgz
```

Or, for active development, link directly:

```bash
cd clients/npm && npm link
cd /path/to/your-app && npm link @agcl/client
```

Once it's published, `npm install @agcl/client` (or the equivalent
`pnpm add` / `yarn add` / `bun add`) will work as expected.

---

## Quick start

```ts
import { AgclClient } from "@agcl/client";

const agcl = new AgclClient({
  host: "localhost",
  port: 9876,
  authKey: process.env.AGCL_KEY!,
});

if (!(await agcl.verify())) throw new Error("invalid key");

for await (const ev of agcl.streamMas({ message: "explain entanglement" })) {
  if (ev.event === "stage1_step") console.log(`A ${ev.step}/${ev.total_steps}`);
  if (ev.event === "answer")      console.log(ev.text);
}
```

---

## API surface

Method names match [`clients/npm/src/index.ts`](../../clients/npm/src/index.ts)
exactly.

| Method | Maps to |
|---|---|
| `verify()` | `POST /node/auth/verify` |
| `health()` / `info()` | `GET /node/health` / `GET /node/info` |
| `getConfig()` / `patchConfig(p)` | `GET / PATCH /node/config` |
| `getMasJson()` / `putMasJson(spec)` | `GET / PUT /node/config/mas` |
| `runMas(body)` | `POST /node/mas/run` (blocking) |
| `streamMas(body)` | `POST /node/mas/stream` (async iterator of SSE events) |
| `rebuildMas()` | `POST /node/mas/rebuild` |
| `listMasSessions()` / `getMasSession(sid)` / `deleteMasSession(sid)` | session list / view / drop |
| `pause(sid)` / `resume(sid)` / `halt(sid)` / `status(sid)` / `latent(sid)` | training control |
| `listTopics()` / `deleteTopic(id)` | trained-state index |
| `listPlugins()` | `GET /node/plugins` |
| `miniStatus()` / `miniGetConfig()` / `miniPatchConfig(p)` / `miniStart()` / `miniStop()` / `miniPause()` / `miniResume()` / `miniTest({prompt})` / `miniCheckpoint()` / `miniPresets()` | mini-model trainer |
| `chatStream(sid, body)` | `POST /node/chat/{sid}` (SSE) |
| `listChatSessions()` / `getChatSession(sid)` / `deleteChatSession(sid)` | main-agent sessions |
| `usageSummary()` / `usageProviders()` / `usageSessions()` / `usageSessionDetail(sid)` | dashboard data |
| `usageTimeseries({session_id?, provider?, kind?, bucket_sec?, lookback_sec?})` | bucketed token + cost series |
| `usageQuotas()` / `setUsageQuota(provider, body)` / `deleteUsageQuota(provider)` | per-provider caps |
| `listCustomProviders()` / `registerCustomProvider(body)` / `deleteCustomProvider(name)` | OpenAI-compatible custom providers |

Plus the lower-level escape hatches `request<T>(path, init)` and
`stream<T>(path, init)` for any route the wrapper doesn't surface yet.

Full endpoint reference: [../gui/endpoints.md](../gui/endpoints.md).

---

## Toolkit subpath (`@agcl/client/toolkit`)

```ts
import { AgclClient, ToolkitClient } from "@agcl/client";

const agcl = new AgclClient({ host: "localhost", port: 9876, authKey });
const tk   = new ToolkitClient(agcl);

await tk.discover();                              // what's wired up
await tk.ping("ollama");                          // reachability
const r = await tk.chat({ messages: [{ role: "user", content: "hi" }] });
for await (const { delta } of tk.chatStream({
  messages: [{ role: "user", content: "explain entanglement" }],
})) process.stdout.write(delta);

await tk.emitDocker({ gpu: true });               // POST /node/toolkit/manifest/docker
await tk.emitK8s();                               // POST /node/toolkit/manifest/k8s
```

Full toolkit method list: [`clients/npm/src/toolkit.ts`](../../clients/npm/src/toolkit.ts).

---

## Browser usage notes

- The node runs on `http://localhost:<port>`. Browsers treat that as a
  non-secure context — `fetch` works, but Service Workers / Web Crypto
  APIs that require HTTPS may not. For production, put the node behind
  a TLS reverse proxy (see [../gui/remote.md](../gui/remote.md)).
- The bearer key should never be hardcoded in a shipped browser bundle.
  Have the user paste it into a `localStorage`-backed input.
- The node defaults to `Access-Control-Allow-Origin: *`; lock it down
  with `--cors https://your-app.example.com` in production.

---

## Vue composables (`@agcl/client/vue`)

Vue 3 composables ship as a subpath export. Vue itself is a peer
dependency — install it in your app, not here.

```ts
import { AgclClient } from "@agcl/client";
import { useAgclMas, useAgclChat, useAgclUsage, useAgclTimeseries }
  from "@agcl/client/vue";

const agcl = new AgclClient({ host: "localhost", port: 9876, authKey });

// recursive-MAS turn — `events` is a reactive array
const { events, running, answer, run } = useAgclMas(agcl);
await run({ message: "explain entanglement" });

// chat-path with prefix continuation
const { prefix, text, send, done } = useAgclChat(agcl, "default");
await send({ message: "hello" });

// dashboard data — auto-poll
const { summary, sessions, providers, start, stop } = useAgclUsage(agcl);
start(5000);     // refresh every 5s
// stop() on unmount

// bucketed graph data — for chat-tokens vs knowledge-formation graph
const chat   = useAgclTimeseries(agcl, { kind: "chat",      bucket_sec: 60, lookback_sec: 3600 });
const know   = useAgclTimeseries(agcl, { kind: "knowledge", bucket_sec: 60, lookback_sec: 3600 });
await chat.refresh(); await know.refresh();
// chat.buckets / know.buckets are reactive refs
```

Source: [`clients/npm/src/vue.ts`](../../clients/npm/src/vue.ts).

---

## React / Svelte / Solid

The package doesn't ship framework-specific subpath exports for these
yet. The async-iterator surface (`streamMas`, `chatStream`,
`usageTimeseries`) plays well with `useEffect` / `onMount` / Svelte
stores — wrap it in your own hook in five lines:

```tsx
import { useState } from "react";
import { AgclClient, type MasEvent } from "@agcl/client";

export function useAgclMas(client: AgclClient) {
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
