/**
 * @agcl/client/toolkit — bindings for the /node/toolkit/* surface.
 *
 * Lets a JS app drive the same infra/inference adapters that the
 * Python CLI exposes via `python main.py toolkit ...`:
 *   - discover what's configured
 *   - ping each adapter
 *   - send chat through the active gateway (LiteLLM / vLLM / Ollama)
 *   - read/write distributed state and S3-compatible checkpoints
 *   - emit Dockerfile / Helm chart from the running config
 *   - WebRTC signaling helpers
 */

import { AgclClient } from "./index.js";

export type AdapterName =
  | "litellm" | "ollama" | "vllm" | "tgi"
  | "redis"   | "s3"
  | "cloudflare" | "discord"
  | "docker"  | "k8s"
  | "webrtc";

export interface DiscoverResult {
  adapters: Record<AdapterName, {
    configured: boolean;
    env: Record<string, boolean>;
    importable: boolean;
    import_err: string | null;
  }>;
}

export interface ChatRequest {
  messages:    { role: string; content: string }[];
  model?:      string;
  stream?:     boolean;
  max_tokens?: number;
  temperature?: number;
}

export class ToolkitClient {
  constructor(private readonly agcl: AgclClient) {}

  discover() {
    return this.agcl.request<DiscoverResult>("/node/toolkit/discover");
  }

  ping(adapter: AdapterName) {
    return this.agcl.request(`/node/toolkit/ping/${adapter}`);
  }

  // ── chat (gateway-aware) ────────────────────────────────────────

  chat(body: ChatRequest) {
    return this.agcl.request("/node/toolkit/chat", {
      method: "POST",
      body: JSON.stringify({ ...body, stream: false }),
    });
  }

  chatStream(body: Omit<ChatRequest, "stream">) {
    return this.agcl.stream<{ delta: string }>("/node/toolkit/chat", {
      method: "POST",
      body: JSON.stringify({ ...body, stream: true }),
    });
  }

  // ── distributed state ───────────────────────────────────────────

  saveState(agent_id: string, state: Record<string, unknown>, ttl = 86400) {
    return this.agcl.request("/node/toolkit/state", {
      method: "PUT",
      body: JSON.stringify({ agent_id, state, ttl }),
    });
  }
  loadState(agent_id: string) {
    return this.agcl.request(`/node/toolkit/state/${agent_id}`);
  }
  deleteState(agent_id: string) {
    return this.agcl.request(`/node/toolkit/state/${agent_id}`, { method: "DELETE" });
  }

  // ── checkpoint store ────────────────────────────────────────────

  async saveCheckpoint(topic_id: string, name: string, data: Uint8Array) {
    const data_b64 = btoa(String.fromCharCode(...data));
    return this.agcl.request("/node/toolkit/checkpoint", {
      method: "PUT",
      body: JSON.stringify({ topic_id, name, data_b64 }),
    });
  }
  async loadCheckpoint(topic_id: string, name: string): Promise<Uint8Array> {
    const r = await this.agcl.request<{ data_b64: string }>(
      `/node/toolkit/checkpoint/${topic_id}/${name}`,
    );
    const bin = atob(r.data_b64);
    const out = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
    return out;
  }
  deleteTopicCheckpoint(topic_id: string) {
    return this.agcl.request(`/node/toolkit/checkpoint/${topic_id}`, { method: "DELETE" });
  }
  listCheckpoints() { return this.agcl.request("/node/toolkit/checkpoints"); }

  // ── Cloudflare relay ────────────────────────────────────────────

  relayPublish(session_id: string, event: Record<string, unknown>) {
    return this.agcl.request("/node/toolkit/relay/publish", {
      method: "POST",
      body: JSON.stringify({ session_id, event }),
    });
  }

  // ── manifest emitters ───────────────────────────────────────────

  emitDocker(opts: {
    out_dir?: string; gpu?: boolean;
    with_ollama?: boolean; with_litellm?: boolean; with_redis?: boolean;
  } = {}) {
    return this.agcl.request("/node/toolkit/manifest/docker", {
      method: "POST",
      body: JSON.stringify(opts),
    });
  }

  emitK8s(opts: { out_dir?: string } = {}) {
    return this.agcl.request("/node/toolkit/manifest/k8s", {
      method: "POST",
      body: JSON.stringify(opts),
    });
  }

  // ── WebRTC signaling ────────────────────────────────────────────

  webrtcStatus() { return this.agcl.request("/node/toolkit/webrtc/status"); }

  webrtcOffer(session_id: string, sdp: RTCSessionDescriptionInit) {
    return this.agcl.request("/node/toolkit/webrtc/offer", {
      method: "POST",
      body: JSON.stringify({ session_id, sdp }),
    });
  }
  webrtcGetOffer(session_id: string) {
    return this.agcl.request<{ sdp: RTCSessionDescriptionInit }>(
      `/node/toolkit/webrtc/offer/${session_id}`,
    );
  }
  webrtcAnswer(session_id: string, sdp: RTCSessionDescriptionInit) {
    return this.agcl.request("/node/toolkit/webrtc/answer", {
      method: "POST",
      body: JSON.stringify({ session_id, sdp }),
    });
  }
  webrtcGetAnswer(session_id: string) {
    return this.agcl.request<{ sdp: RTCSessionDescriptionInit }>(
      `/node/toolkit/webrtc/answer/${session_id}`,
    );
  }
  webrtcAddIce(session_id: string, side: "a" | "b", candidate: RTCIceCandidateInit) {
    return this.agcl.request("/node/toolkit/webrtc/ice", {
      method: "POST",
      body: JSON.stringify({ session_id, side, candidate }),
    });
  }
  webrtcPullIce(session_id: string, side: "a" | "b") {
    return this.agcl.request<{ candidates: RTCIceCandidateInit[] }>(
      `/node/toolkit/webrtc/ice/${session_id}/${side}`,
    );
  }
}

export default ToolkitClient;
