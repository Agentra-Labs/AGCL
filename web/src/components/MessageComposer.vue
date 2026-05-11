<template>
  <div class="composer">
    <div class="input-wrap">
      <textarea
        ref="ta"
        v-model="text"
        :placeholder="`Message #${spaceName} — @ to mention an agent`"
        @keydown="onKey"
        @input="onInput"
        rows="1"
      />
      <MentionDropdown
        :agents="agents"
        :query="mentionQuery"
        :visible="showMention"
        @select="insertMention"
      />
    </div>
    <button class="send-btn" @click="send" :disabled="!text.trim()">Send</button>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted, defineProps, defineEmits } from "vue";
import MentionDropdown from "./MentionDropdown.vue";
import { useAuthStore } from "../stores/auth";

const props = defineProps<{ spaceId: string; spaceName: string; senderId: string }>();
const emit = defineEmits<{ (e: "sent"): void }>();

const auth = useAuthStore();
const text = ref("");
const ta = ref<HTMLTextAreaElement | null>(null);
const agents = ref<any[]>([]);
const showMention = ref(false);
const mentionQuery = ref("");

onMounted(async () => {
  if (!auth.client) return;
  try {
    const res = await auth.client.request<{ agents: any[] }>("/node/collab/agents");
    agents.value = res.agents;
  } catch {}
});

function onKey(e: KeyboardEvent) {
  if ((e.ctrlKey || e.metaKey) && e.key === "Enter") { e.preventDefault(); send(); }
  if (e.key === "Escape") showMention.value = false;
}

function onInput() {
  // auto-resize
  if (ta.value) { ta.value.style.height = "auto"; ta.value.style.height = ta.value.scrollHeight + "px"; }
  // detect @mention
  const val = text.value;
  const cursor = ta.value?.selectionStart ?? val.length;
  const before = val.slice(0, cursor);
  const match = before.match(/@([\w\-]*)$/);
  if (match) { mentionQuery.value = match[1]; showMention.value = true; }
  else showMention.value = false;
}

function insertMention(agent: any) {
  const val = text.value;
  const cursor = ta.value?.selectionStart ?? val.length;
  const before = val.slice(0, cursor).replace(/@[\w\-]*$/, `@${agent.name} `);
  text.value = before + val.slice(cursor);
  showMention.value = false;
  ta.value?.focus();
}

async function send() {
  const content = text.value.trim();
  if (!content || !auth.client) return;
  text.value = "";
  if (ta.value) ta.value.style.height = "auto";
  await auth.client.request(`/node/collab/spaces/${props.spaceId}/messages`, {
    method: "POST",
    body: JSON.stringify({ sender_id: props.senderId, sender_type: "human", content }),
  });
  emit("sent");
}
</script>

<style scoped>
.composer { display:flex; gap:.5rem; padding:.75rem 1rem; border-top:1px solid #2a2a3e; align-items:flex-end; }
.input-wrap { flex:1; position:relative; }
textarea { width:100%; background:#1e1e30; border:1px solid #333; border-radius:8px; padding:.5rem .75rem; color:#e2e2f0; resize:none; max-height:160px; overflow-y:auto; font-size:.9rem; line-height:1.5; }
textarea:focus { outline:none; border-color:#5865f2; }
.send-btn { background:#5865f2; border:none; border-radius:8px; padding:.5rem 1rem; color:#fff; font-size:.9rem; }
.send-btn:disabled { opacity:.4; }
</style>
