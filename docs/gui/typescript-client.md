# TypeScript sample client

<img src="../assets/typescript.svg" width="20" align="absmiddle" alt="TypeScript" />&nbsp;
A minimal client that handles auth, info, and streaming MAS turns.
Drop straight into a Vite/Next/Electron app.

> Part of the **[GUI integration guide](../gui.md)**.
> See also: [Connect](connect.md) · [Endpoints](endpoints.md) · [SSE events](sse-events.md)
>
> Looking for a published JS package instead of writing your own?
> See **[../integrations/npm.md](../integrations/npm.md)**.

---

## Code

```ts
type NodeConfig = { host: string; port: number; key: string };

async function nodeFetch(cfg: NodeConfig, path: string, init: RequestInit = {}) {
  const res = await fetch(`http://${cfg.host}:${cfg.port}${path}`, {
    ...init,
    headers: {
      ...(init.headers || {}),
      "Authorization": `Bearer ${cfg.key}`,
      "Content-Type": "application/json",
    },
  });
  if (!res.ok) throw new Error(`${res.status} ${await res.text()}`);
  return res;
}

export async function verifyKey(cfg: NodeConfig): Promise<boolean> {
  try {
    const r = await nodeFetch(cfg, "/node/auth/verify", { method: "POST" });
    return (await r.json()).ok === true;
  } catch { return false; }
}

export async function getInfo(cfg: NodeConfig) {
  return (await nodeFetch(cfg, "/node/info")).json();
}

export async function getConfig(cfg: NodeConfig) {
  return (await nodeFetch(cfg, "/node/config")).json();
}

export async function patchConfig(cfg: NodeConfig, patch: Record<string, any>) {
  const r = await nodeFetch(cfg, "/node/config", {
    method: "PATCH", body: JSON.stringify(patch),
  });
  return r.json();
}

export async function* runMasStream(cfg: NodeConfig, body: {
  message: string; session_id?: string; force_cloud?: boolean;
  force_continue?: boolean;
}) {
  const res = await nodeFetch(cfg, "/node/mas/stream", {
    method: "POST", body: JSON.stringify(body),
  });
  const reader = res.body!.getReader();
  const decoder = new TextDecoder();
  let buf = "";
  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });
    const parts = buf.split("\n\n");
    buf = parts.pop() ?? "";
    for (const p of parts) {
      if (!p.startsWith("data: ")) continue;
      yield JSON.parse(p.slice(6));
    }
  }
}
```

---

## Use

```ts
const cfg: NodeConfig = { host: "localhost", port: 9876, key: pastedKey };

if (!(await verifyKey(cfg))) {
  // show "invalid key" UI
}

for await (const ev of runMasStream(cfg, { message: "explain entanglement" })) {
  if (ev.event === "stage1_step") updateProgressBar("A", ev.step, ev.total_steps);
  if (ev.event === "answer")       displayAnswer(ev.text);
  if (ev.event === "done")         closeStream();
}
```

The full event reference is in [sse-events.md](sse-events.md).
