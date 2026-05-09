/**
 * @agcl/client/vue — Vue 3 composables.
 *
 * Thin wrappers over the AgclClient async-iterator surface so Vue users
 * don't have to write the loop. Composition API only (Vue 3+); Options
 * API users can call AgclClient directly.
 *
 * Vue is declared as a peerDependency in package.json — install it in
 * your app, not here.
 */

import { ref, shallowRef, type Ref } from "vue";
import { AgclClient, type MasEvent, type ChatEvent } from "./index.js";

/**
 * Run a single recursive-MAS turn and stream events into a reactive
 * array. Auto-marks `running` true while in flight.
 */
export function useAgclMas(client: AgclClient) {
  const events = shallowRef<MasEvent[]>([]);
  const running = ref(false);
  const error = ref<unknown>(null);
  const answer = ref<string>("");

  async function run(body: { message: string; session_id?: string } & Record<string, unknown>) {
    events.value = []; answer.value = ""; error.value = null;
    running.value = true;
    try {
      for await (const ev of client.streamMas(body)) {
        events.value = [...events.value, ev];
        if (ev.event === "answer" && typeof ev.text === "string") {
          answer.value = ev.text;
        }
        if (ev.event === "done") break;
      }
    } catch (e) { error.value = e; }
    finally { running.value = false; }
  }

  return { events, running, error, answer, run };
}

/**
 * Chat-path stream (local prefix + cloud continuation). Reactive prefix
 * + accumulated answer + done flag.
 */
export function useAgclChat(client: AgclClient, sessionId = "default") {
  const prefix = ref<string>("");
  const text   = ref<string>("");
  const done   = ref<boolean>(false);
  const running = ref<boolean>(false);
  const error = ref<unknown>(null);

  async function send(body: {
    message: string;
    provider?: "openai" | "claude";
    recovery_mode?: "natural" | "humor" | "explicit";
  }) {
    prefix.value = ""; text.value = ""; done.value = false; error.value = null;
    running.value = true;
    try {
      for await (const ev of client.chatStream(sessionId, body) as AsyncGenerator<ChatEvent>) {
        if (ev.type === "prefix" && ev.text) prefix.value = ev.text;
        if (ev.type === "chunk"  && ev.text) text.value += ev.text;
        if (ev.type === "done")              done.value = true;
      }
    } catch (e) { error.value = e; }
    finally { running.value = false; }
  }

  return { prefix, text, done, running, error, send };
}

/**
 * Live dashboard data. `refresh()` re-fetches; `start(intervalMs)` polls.
 * Returns reactive refs to plug straight into a Vue template.
 */
export function useAgclUsage(client: AgclClient) {
  const summary = shallowRef<any>(null);
  const sessions = shallowRef<any[]>([]);
  const providers = shallowRef<any>({});
  const error = ref<unknown>(null);
  let timer: ReturnType<typeof setInterval> | null = null;

  async function refresh() {
    try {
      const s = await client.usageSummary() as any;
      summary.value   = s;
      sessions.value  = s.sessions ?? [];
      providers.value = s.providers ?? {};
    } catch (e) { error.value = e; }
  }
  function start(intervalMs = 5000) {
    refresh();
    if (timer) clearInterval(timer);
    timer = setInterval(refresh, intervalMs);
  }
  function stop() {
    if (timer) { clearInterval(timer); timer = null; }
  }
  return { summary, sessions, providers, error, refresh, start, stop };
}

/**
 * Bucketed token timeseries for charts. Returns a reactive ref of
 * buckets and a refresh function. Pass `kind: "chat"` for the
 * session-tokens graph and `kind: "knowledge"` for the
 * knowledge-formation graph.
 */
export function useAgclTimeseries(
  client: AgclClient,
  opts: {
    session_id?: string; provider?: string; kind?: string;
    bucket_sec?: number; lookback_sec?: number;
  } = {},
) {
  const buckets = shallowRef<any[]>([]);
  const error = ref<unknown>(null);
  async function refresh() {
    try {
      const r = await client.usageTimeseries(opts) as any;
      buckets.value = r.buckets ?? [];
    } catch (e) { error.value = e; }
  }
  return { buckets, error, refresh };
}

export type {
  Ref,            // re-export for ergonomic typing in apps
  MasEvent,
  ChatEvent,
};
