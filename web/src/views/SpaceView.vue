<template>
  <div class="space-layout">
    <!-- Left sidebar: space list -->
    <aside class="sidebar">
      <div class="sidebar-header">
        <span class="logo">⬡ AGCL</span>
        <router-link to="/" class="icon-btn" title="All spaces">⌂</router-link>
      </div>
      <nav>
        <div
          v-for="s in spaces.spaces" :key="s.id"
          class="space-item" :class="{ active: s.id === spaceId }"
          @click="router.push(`/spaces/${s.id}`)"
        >
          <span class="space-avatar">{{ s.name[0].toUpperCase() }}</span>
          <span class="space-name">{{ s.name }}</span>
        </div>
      </nav>
    </aside>

    <!-- Main: messages -->
    <div class="main-col">
      <!-- Header -->
      <div class="space-header">
        <div class="space-title">
          <span class="hash">#</span>
          <span>{{ currentSpace?.name ?? spaceId }}</span>
        </div>
        <div class="header-actions">
          <div class="presence-bar">
            <span v-for="(status, uid) in stream.presence.value" :key="uid" class="presence-pill" :class="status">
              {{ uid }}
            </span>
          </div>
          <button class="icon-btn" @click="showKanban = !showKanban" title="Toggle kanban">📋</button>
        </div>
      </div>

      <!-- Messages -->
      <MessageList
        :messages="stream.messages.value"
        @promote="openPromote"
      />

      <!-- Composer -->
      <MessageComposer
        :spaceId="spaceId"
        :spaceName="currentSpace?.name ?? spaceId"
        :senderId="senderId"
        @sent="() => {}"
      />
    </div>

    <!-- Right panel: kanban (toggled) -->
    <div v-if="showKanban" class="kanban-panel">
      <div class="kanban-header">
        Tasks
        <button class="icon-btn" @click="showKanban = false">✕</button>
      </div>
      <TaskKanban :tasks="stream.taskUpdates.value" :spaceId="spaceId" @updated="loadTasks" />
    </div>

    <!-- Task promote modal -->
    <TaskPromoteModal
      v-if="promoteMsg"
      :spaceId="spaceId"
      :message="promoteMsg"
      @close="promoteMsg = null"
      @created="loadTasks"
    />
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import { useSpacesStore } from "../stores/spaces";
import { useAuthStore } from "../stores/auth";
import { useSpaceStream } from "../composables/useSpaceStream";
import MessageList from "../components/MessageList.vue";
import MessageComposer from "../components/MessageComposer.vue";
import TaskKanban from "../components/TaskKanban.vue";
import TaskPromoteModal from "../components/TaskPromoteModal.vue";
import type { Message } from "../composables/useSpaceStream";

const route = useRoute();
const router = useRouter();
const spaces = useSpacesStore();
const auth = useAuthStore();

const spaceId = computed(() => route.params.id as string);
const currentSpace = computed(() => spaces.spaces.find(s => s.id === spaceId.value) ?? null);
const senderId = ref(localStorage.getItem("agcl.user_id") || "me");
const showKanban = ref(false);
const promoteMsg = ref<Message | null>(null);

const stream = useSpaceStream(spaceId.value);

onMounted(async () => {
  await spaces.fetchSpaces();
  await stream.loadHistory();
  stream.connect();
  stream.startHeartbeat(senderId.value);
  await loadTasks();
});

watch(spaceId, async (id) => {
  stream.messages.value = [];
  stream.taskUpdates.value = [];
  await stream.loadHistory();
  stream.connect();
  await loadTasks();
});

async function loadTasks() {
  if (!auth.client) return;
  try {
    const res = await auth.client.request<{ tasks: any[] }>(
      `/node/collab/spaces/${spaceId.value}/tasks`
    );
    stream.taskUpdates.value = res.tasks;
  } catch {}
}

function openPromote(msg: Message) { promoteMsg.value = msg; }
</script>

<style scoped>
.space-layout { display:flex; height:100vh; overflow:hidden; }
.sidebar { width:200px; background:#1a1a2e; display:flex; flex-direction:column; border-right:1px solid #2a2a3e; flex-shrink:0; }
.sidebar-header { display:flex; align-items:center; justify-content:space-between; padding:.75rem 1rem; border-bottom:1px solid #2a2a3e; }
.logo { font-weight:600; }
.icon-btn { background:none; border:none; color:#888; font-size:1rem; cursor:pointer; }
nav { flex:1; overflow-y:auto; padding:.5rem 0; }
.space-item { display:flex; align-items:center; gap:.5rem; padding:.45rem .75rem; cursor:pointer; border-radius:6px; margin:0 .4rem; }
.space-item:hover, .space-item.active { background:#2a2a4e; }
.space-avatar { width:26px; height:26px; border-radius:6px; background:#5865f2; display:flex; align-items:center; justify-content:center; font-size:.75rem; font-weight:600; flex-shrink:0; }
.space-name { font-size:.85rem; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.main-col { flex:1; display:flex; flex-direction:column; min-width:0; }
.space-header { display:flex; align-items:center; justify-content:space-between; padding:.6rem 1rem; border-bottom:1px solid #2a2a3e; }
.space-title { display:flex; align-items:center; gap:.3rem; font-weight:600; }
.hash { color:#888; }
.header-actions { display:flex; align-items:center; gap:.75rem; }
.presence-bar { display:flex; gap:.3rem; flex-wrap:wrap; }
.presence-pill { font-size:.7rem; background:#2a2a4e; border-radius:10px; padding:.1rem .5rem; }
.presence-pill.typing { background:#2a4e2a; color:#86efac; }
.kanban-panel { width:680px; border-left:1px solid #2a2a3e; display:flex; flex-direction:column; flex-shrink:0; }
.kanban-header { display:flex; align-items:center; justify-content:space-between; padding:.6rem 1rem; border-bottom:1px solid #2a2a3e; font-weight:600; }
</style>
