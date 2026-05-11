<template>
  <div class="kanban">
    <div v-for="col in columns" :key="col.status" class="col"
      @dragover.prevent @drop="onDrop($event, col.status)">
      <div class="col-header">{{ col.label }} <span class="count">{{ byStatus(col.status).length }}</span></div>
      <div
        v-for="task in byStatus(col.status)" :key="task.id"
        class="card" :class="{ pulsing: task.status === 'in_progress' && task.assignee_type === 'agent' }"
        draggable="true"
        @dragstart="onDragStart($event, task)"
      >
        <div class="card-title">{{ task.title }}</div>
        <div class="card-meta">
          <span v-if="task.assignee_id" class="assignee">
            {{ task.assignee_type === 'agent' ? '🤖' : '👤' }} {{ task.assignee_id }}
          </span>
          <span v-if="task.deadline" class="deadline">📅 {{ fmtDeadline(task.deadline) }}</span>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { defineProps, defineEmits } from "vue";
import type { TaskUpdate } from "../composables/useSpaceStream";
import { useAuthStore } from "../stores/auth";

const props = defineProps<{ tasks: TaskUpdate[]; spaceId: string }>();
const emit = defineEmits<{ (e: "updated"): void }>();
const auth = useAuthStore();

const columns = [
  { status: "open", label: "Open" },
  { status: "in_progress", label: "In Progress" },
  { status: "blocked", label: "Blocked" },
  { status: "done", label: "Done" },
];

function byStatus(s: string) { return props.tasks.filter(t => t.status === s); }

let dragging: TaskUpdate | null = null;
function onDragStart(_e: DragEvent, task: TaskUpdate) { dragging = task; }

async function onDrop(_e: DragEvent, status: string) {
  if (!dragging || dragging.status === status || !auth.client) return;
  await auth.client.request(`/node/collab/tasks/${dragging.id}?space_id=${props.spaceId}`, {
    method: "PATCH",
    body: JSON.stringify({ status }),
  });
  emit("updated");
  dragging = null;
}

function fmtDeadline(d: string) {
  return new Date(d).toLocaleDateString(undefined, { month: "short", day: "numeric" });
}
</script>

<style scoped>
.kanban { display:flex; gap:.75rem; height:100%; overflow-x:auto; padding:.75rem; }
.col { min-width:200px; background:#1a1a2e; border-radius:10px; padding:.6rem; display:flex; flex-direction:column; gap:.4rem; }
.col-header { font-size:.8rem; font-weight:600; color:#aaa; padding:.2rem .4rem; display:flex; justify-content:space-between; }
.count { background:#2a2a4e; border-radius:10px; padding:0 .4rem; font-size:.7rem; }
.card { background:#0f0f17; border-radius:8px; padding:.6rem .75rem; cursor:grab; border:1px solid #2a2a3e; }
.card:hover { border-color:#5865f2; }
.card-title { font-size:.85rem; margin-bottom:.3rem; }
.card-meta { display:flex; flex-wrap:wrap; gap:.4rem; }
.assignee, .deadline { font-size:.7rem; color:#888; }
.pulsing { animation:pulse 2s infinite; }
@keyframes pulse { 0%,100%{border-color:#2a2a3e} 50%{border-color:#5865f2} }
</style>
