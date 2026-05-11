<template>
  <div class="modal-overlay" @click.self="$emit('close')">
    <div class="modal">
      <h3>📌 Promote to Task</h3>
      <label>Title</label>
      <input v-model="title" />
      <label>Description</label>
      <textarea v-model="description" rows="2" />
      <label>Assign to</label>
      <select v-model="assigneeId">
        <option value="">Unassigned</option>
        <optgroup label="Agents">
          <option v-for="a in agents" :key="a.id" :value="a.id" data-type="agent">{{ a.name }}</option>
        </optgroup>
        <optgroup label="Members">
          <option v-for="m in members" :key="m.user_id" :value="m.user_id" data-type="human">{{ m.display_name }}</option>
        </optgroup>
      </select>
      <label>Deadline (optional)</label>
      <input type="datetime-local" v-model="deadline" />
      <p v-if="err" class="err">{{ err }}</p>
      <div class="actions">
        <button @click="$emit('close')">Cancel</button>
        <button class="primary" @click="submit">Create Task</button>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted, defineProps, defineEmits } from "vue";
import { useAuthStore } from "../stores/auth";
import type { Message } from "../composables/useSpaceStream";

const props = defineProps<{ spaceId: string; message: Message }>();
const emit = defineEmits<{ (e: "close"): void; (e: "created"): void }>();

const auth = useAuthStore();
const title = ref(props.message.content.slice(0, 80));
const description = ref("");
const assigneeId = ref("");
const deadline = ref("");
const err = ref("");
const agents = ref<any[]>([]);
const members = ref<any[]>([]);

onMounted(async () => {
  if (!auth.client) return;
  try {
    const [ar, sr] = await Promise.all([
      auth.client.request<{ agents: any[] }>("/node/collab/agents"),
      auth.client.request<{ members: any[] }>(`/node/collab/spaces/${props.spaceId}`),
    ]);
    agents.value = ar.agents;
    members.value = (sr as any).members ?? [];
  } catch {}
});

async function submit() {
  err.value = "";
  if (!auth.client) return;
  // determine assignee type
  const isAgent = agents.value.some(a => a.id === assigneeId.value);
  try {
    await auth.client.request(`/node/collab/spaces/${props.spaceId}/tasks`, {
      method: "POST",
      body: JSON.stringify({
        title: title.value,
        description: description.value,
        assignee_id: assigneeId.value || null,
        assignee_type: assigneeId.value ? (isAgent ? "agent" : "human") : null,
        source_message_id: props.message.id,
        deadline: deadline.value || null,
      }),
    });
    emit("created");
    emit("close");
  } catch (e: any) { err.value = e?.message || "Failed"; }
}
</script>

<style scoped>
.modal-overlay { position:fixed; inset:0; background:rgba(0,0,0,.6); display:flex; align-items:center; justify-content:center; z-index:200; }
.modal { background:#1a1a2e; border-radius:12px; padding:1.5rem; width:380px; }
h3 { margin-bottom:1rem; }
label { display:block; font-size:.8rem; color:#aaa; margin:.6rem 0 .2rem; }
input, textarea, select { width:100%; background:#0f0f17; border:1px solid #333; border-radius:6px; padding:.45rem .7rem; color:#e2e2f0; font-family:inherit; }
.actions { display:flex; justify-content:flex-end; gap:.5rem; margin-top:1rem; }
.actions button { background:#2a2a4e; border:none; border-radius:6px; padding:.4rem .9rem; color:#ccc; }
.actions .primary { background:#5865f2; color:#fff; }
.err { color:#f87171; font-size:.8rem; margin-top:.4rem; }
</style>
