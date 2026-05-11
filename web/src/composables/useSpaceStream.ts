import { ref, onUnmounted } from "vue";
import { useAuthStore } from "../stores/auth";

export interface Message {
  id: string; space_id: string; sender_id: string; sender_type: string;
  content: string; created_at: number; mentions: string[];
  ambient: boolean; partial: boolean; thread_id?: string;
}
export interface PresenceEvent { user_id: string; status: string; }
export interface TaskUpdate { id: string; space_id: string; title: string; status: string; assignee_id?: string; assignee_type?: string; deadline?: string; }

export function useSpaceStream(spaceId: string) {
  const messages = ref<Message[]>([]);
  const presence = ref<Record<string, string>>({});
  const taskUpdates = ref<TaskUpdate[]>([]);
  const auth = useAuthStore();
  let es: EventSource | null = null;
  let heartbeat: ReturnType<typeof setInterval> | null = null;

  function connect() {
    if (!auth.client) return;
    const url = `${auth.host}/node/collab/spaces/${spaceId}/stream`;
    es = new EventSource(url, { withCredentials: false });
    // EventSource doesn't support custom headers; pass key as query param
    // (node.py PUBLIC_PATHS bypass or we use a token approach)
    // For now connect via fetch-based SSE using the client
    es.close();
    _fetchSSE();
  }

  async function _fetchSSE() {
    if (!auth.bearerKey) return;
    const url = `${auth.host}/node/collab/spaces/${spaceId}/stream`;
    try {
      const resp = await fetch(url, {
        headers: { Authorization: `Bearer ${auth.bearerKey}` },
      });
      if (!resp.body) return;
      const reader = resp.body.getReader();
      const decoder = new TextDecoder();
      let buf = "";
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buf += decoder.decode(value, { stream: true });
        const parts = buf.split("\n\n");
        buf = parts.pop() ?? "";
        for (const part of parts) {
          const line = part.trim();
          if (!line || line.startsWith(":")) continue;
          const data = line.replace(/^data:\s*/, "");
          try {
            const event = JSON.parse(data);
            handleEvent(event);
          } catch {}
        }
      }
    } catch {
      // reconnect after 3s
      setTimeout(_fetchSSE, 3000);
    }
  }

  function handleEvent(event: { type: string; data: any }) {
    if (event.type === "message") {
      const msg: Message = event.data;
      const idx = messages.value.findIndex(m => m.id === msg.id);
      if (idx >= 0) messages.value[idx] = msg; // update partial
      else messages.value.push(msg);
    } else if (event.type === "presence") {
      const p: PresenceEvent = event.data;
      if (p.status === "offline") delete presence.value[p.user_id];
      else presence.value[p.user_id] = p.status;
    } else if (event.type === "task_update") {
      const t: TaskUpdate = event.data;
      const idx = taskUpdates.value.findIndex(x => x.id === t.id);
      if (idx >= 0) taskUpdates.value[idx] = t;
      else taskUpdates.value.push(t);
    }
  }

  async function loadHistory() {
    if (!auth.client) return;
    const res = await auth.client.request<{ messages: Message[] }>(
      `/node/collab/spaces/${spaceId}/messages?limit=50`
    );
    messages.value = res.messages;
  }

  async function sendHeartbeat(userId: string) {
    if (!auth.client) return;
    await auth.client.request(`/node/collab/spaces/${spaceId}/presence`, {
      method: "POST", body: JSON.stringify({ user_id: userId, status: "online" }),
    });
  }

  function startHeartbeat(userId: string) {
    heartbeat = setInterval(() => sendHeartbeat(userId), 20_000);
  }

  onUnmounted(() => {
    es?.close();
    if (heartbeat) clearInterval(heartbeat);
  });

  return { messages, presence, taskUpdates, connect, loadHistory, startHeartbeat, handleEvent };
}
