/**
 * @agcl/client — TypeScript client for the AGCL node server.
 *
 * Talks HTTP + SSE with bearer-token auth. Mirrors every /node/*
 * route: auth, info, config, mas/run, mas/stream, sessions, topics,
 * training control, plugins, mini-model trainer, chat, and the
 * toolkit subsystem (re-exported from "./toolkit").
 *
 * Runtime: Node 18+, Bun, Deno, browser (modern fetch + ReadableStream).
 */

export interface NodeConfig {
  /** Hostname or IP of the AGCL node (default: "localhost"). */
  host?: string;
  /** Port the node listens on (default: 9876). */
  port?: number;
  /** Full base URL — overrides host/port if set, e.g. "https://agcl.example.com". */
  baseUrl?: string;
  /** Bearer auth token printed by `python main.py node`. */
  authKey: string;
  /** Optional fetch implementation (e.g. for tests / older runtimes). */
  fetchImpl?: typeof fetch;
}

/** One SSE event from /node/mas/stream. */
export interface MasEvent {
  event: string;
  [k: string]: unknown;
}

export interface ChatEvent {
  type: "prefix" | "chunk" | "done";
  text?: string;
  local_ms?: number;
  pressure?: { rate_ratio?: number; avg_latency_sec?: number };
}

export class AgclError extends Error {
  constructor(public status: number, public body: string, public path: string) {
    super(`[AGCL ${status}] ${path}: ${body.slice(0, 200)}`);
    this.name = "AgclError";
  }
}

function resolveBase(cfg: NodeConfig): string {
  if (cfg.baseUrl) return cfg.baseUrl.replace(/\/$/, "");
  const host = cfg.host ?? "localhost";
  const port = cfg.port ?? 9876;
  return `http://${host}:${port}`;
}

export class AgclClient {
  private readonly base: string;
  private readonly auth: string;
  private readonly fetchImpl: typeof fetch;

  constructor(cfg: NodeConfig) {
    if (!cfg.authKey) {
      throw new Error("AgclClient: authKey is required");
    }
    this.base = resolveBase(cfg);
    this.auth = `Bearer ${cfg.authKey}`;
    this.fetchImpl = cfg.fetchImpl ?? fetch;
  }

  // ────────────────────────────────────────────────────────────────
  // low-level
  // ────────────────────────────────────────────────────────────────

  /** Fire one request. Throws AgclError on non-2xx. */
  async request<T = unknown>(
    path: string,
    init: RequestInit = {},
  ): Promise<T> {
    const res = await this.fetchImpl(`${this.base}${path}`, {
      ...init,
      headers: {
        Authorization: this.auth,
        "Content-Type": "application/json",
        ...(init.headers ?? {}),
      },
    });
    if (!res.ok) {
      throw new AgclError(res.status, await res.text(), path);
    }
    if (res.headers.get("content-type")?.includes("application/json")) {
      return (await res.json()) as T;
    }
    return (await res.text()) as unknown as T;
  }

  /**
   * Open an SSE stream and yield each `data: {...}` payload as a
   * parsed JSON object. Caller may break out of the loop to cancel.
   */
  async *stream<T = unknown>(
    path: string,
    init: RequestInit = {},
  ): AsyncGenerator<T, void, unknown> {
    const res = await this.fetchImpl(`${this.base}${path}`, {
      ...init,
      headers: {
        Authorization: this.auth,
        "Content-Type": "application/json",
        Accept: "text/event-stream",
        ...(init.headers ?? {}),
      },
    });
    if (!res.ok) {
      throw new AgclError(res.status, await res.text(), path);
    }
    if (!res.body) {
      throw new AgclError(0, "no body on stream response", path);
    }
    const reader = res.body.getReader();
    const dec = new TextDecoder();
    let buf = "";
    try {
      while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        buf += dec.decode(value, { stream: true });
        const parts = buf.split("\n\n");
        buf = parts.pop() ?? "";
        for (const p of parts) {
          for (const line of p.split("\n")) {
            if (!line.startsWith("data:")) continue;
            const payload = line.slice(5).trim();
            if (!payload || payload === "[DONE]") continue;
            try {
              yield JSON.parse(payload) as T;
            } catch {
              /* malformed line — skip */
            }
          }
        }
      }
    } finally {
      try { await reader.cancel(); } catch { /* nothing */ }
    }
  }

  // ────────────────────────────────────────────────────────────────
  // auth + info
  // ────────────────────────────────────────────────────────────────

  /** True if the auth key is currently valid. */
  async verify(): Promise<boolean> {
    try {
      const r = await this.request<{ ok: boolean }>(
        "/node/auth/verify",
        { method: "POST" },
      );
      return r.ok === true;
    } catch {
      return false;
    }
  }

  health() { return this.request("/node/health"); }
  info()   { return this.request("/node/info"); }

  // ────────────────────────────────────────────────────────────────
  // editable config
  // ────────────────────────────────────────────────────────────────

  getConfig() { return this.request("/node/config"); }

  patchConfig(patch: Record<string, unknown>) {
    return this.request("/node/config", {
      method: "PATCH",
      body: JSON.stringify(patch),
    });
  }

  getMasJson() { return this.request("/node/config/mas"); }

  putMasJson(spec: { agents: unknown[] }) {
    return this.request("/node/config/mas", {
      method: "PUT",
      body: JSON.stringify(spec),
    });
  }

  rebuildMas() {
    return this.request("/node/mas/rebuild", { method: "POST" });
  }

  // ────────────────────────────────────────────────────────────────
  // recursive MAS — one-shot + streaming
  // ────────────────────────────────────────────────────────────────

  runMas(body: { message: string; session_id?: string } & Record<string, unknown>) {
    return this.request("/node/mas/run", {
      method: "POST",
      body: JSON.stringify(body),
    });
  }

  streamMas(body: { message: string; session_id?: string } & Record<string, unknown>) {
    return this.stream<MasEvent>("/node/mas/stream", {
      method: "POST",
      body: JSON.stringify(body),
    });
  }

  // ────────────────────────────────────────────────────────────────
  // sessions + topics + control
  // ────────────────────────────────────────────────────────────────

  listMasSessions()           { return this.request("/node/mas/sessions"); }
  getMasSession(sid: string)  { return this.request(`/node/mas/sessions/${sid}`); }
  deleteMasSession(sid: string) {
    return this.request(`/node/mas/sessions/${sid}`, { method: "DELETE" });
  }

  pause(sid: string)  { return this.request(`/node/mas/sessions/${sid}/pause`, { method: "POST" }); }
  resume(sid: string) { return this.request(`/node/mas/sessions/${sid}/resume`, { method: "POST" }); }
  halt(sid: string)   { return this.request(`/node/mas/sessions/${sid}/halt`, { method: "POST" }); }
  status(sid: string) { return this.request(`/node/mas/sessions/${sid}/status`); }
  latent(sid: string) { return this.request(`/node/mas/sessions/${sid}/latent`); }

  listTopics() { return this.request("/node/topics"); }
  deleteTopic(id: string) {
    return this.request(`/node/topics/${id}`, { method: "DELETE" });
  }

  listPlugins() { return this.request("/node/plugins"); }

  // ────────────────────────────────────────────────────────────────
  // mini-model trainer
  // ────────────────────────────────────────────────────────────────

  miniStatus()      { return this.request("/node/mini/status"); }
  miniGetConfig()   { return this.request("/node/mini/config"); }
  miniPatchConfig(p: Record<string, unknown>) {
    return this.request("/node/mini/config", { method: "PATCH", body: JSON.stringify(p) });
  }
  miniStart()  { return this.request("/node/mini/start",  { method: "POST" }); }
  miniStop()   { return this.request("/node/mini/stop",   { method: "POST" }); }
  miniPause()  { return this.request("/node/mini/pause",  { method: "POST" }); }
  miniResume() { return this.request("/node/mini/resume", { method: "POST" }); }
  miniTest(body: { prompt: string; max_new?: number; temperature?: number }) {
    return this.request("/node/mini/test", { method: "POST", body: JSON.stringify(body) });
  }
  miniCheckpoint() { return this.request("/node/mini/checkpoint", { method: "POST" }); }
  miniPresets()    { return this.request("/node/mini/presets"); }

  // ────────────────────────────────────────────────────────────────
  // main-agent chat (local prefix + cloud continuation)
  // ────────────────────────────────────────────────────────────────

  chatStream(sid: string, body: {
    message: string;
    provider?: "openai" | "claude";
    recovery_mode?: "natural" | "humor" | "explicit";
  }) {
    return this.stream<ChatEvent>(`/node/chat/${sid}`, {
      method: "POST",
      body: JSON.stringify({ session_id: sid, ...body }),
    });
  }

  listChatSessions()           { return this.request("/node/sessions"); }
  getChatSession(sid: string)  { return this.request(`/node/sessions/${sid}`); }
  deleteChatSession(sid: string) {
    return this.request(`/node/sessions/${sid}`, { method: "DELETE" });
  }

  // ────────────────────────────────────────────────────────────────
  // usage / quotas / custom providers (powers the dashboard)
  // ────────────────────────────────────────────────────────────────

  usageSummary()      { return this.request("/node/usage/summary"); }
  usageProviders()    { return this.request("/node/usage/providers"); }
  usageSessions()     { return this.request("/node/usage/sessions"); }
  usageSessionDetail(sid: string) {
    return this.request(`/node/usage/sessions/${sid}`);
  }
  usageTimeseries(opts: {
    session_id?: string; provider?: string; kind?: string;
    bucket_sec?: number; lookback_sec?: number;
  } = {}) {
    const q = new URLSearchParams();
    for (const [k, v] of Object.entries(opts)) {
      if (v != null) q.set(k, String(v));
    }
    return this.request("/node/usage/timeseries?" + q.toString());
  }

  usageQuotas() { return this.request("/node/usage/quotas"); }
  setUsageQuota(provider: string, body: {
    max_tokens?: number; max_credit_usd?: number;
    period?: "lifetime" | "daily" | "monthly"; notes?: string;
  }) {
    return this.request(`/node/usage/quota/${provider}`, {
      method: "PUT", body: JSON.stringify(body),
    });
  }
  deleteUsageQuota(provider: string) {
    return this.request(`/node/usage/quota/${provider}`, { method: "DELETE" });
  }

  listCustomProviders() {
    return this.request("/node/usage/providers/custom");
  }
  registerCustomProvider(body: {
    name: string; base_url: string;
    api_key_env: string; model: string;
    kind?: string; notes?: string;
  }) {
    return this.request("/node/usage/providers/custom", {
      method: "POST", body: JSON.stringify(body),
    });
  }
  deleteCustomProvider(name: string) {
    return this.request(`/node/usage/providers/custom/${name}`, { method: "DELETE" });
  }
}

export { ToolkitClient } from "./toolkit.js";
export default AgclClient;
