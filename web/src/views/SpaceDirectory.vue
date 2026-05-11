<template>
  <div class="dir-layout">
    <aside class="sidebar">
      <div class="sidebar-header">
        <span class="logo">⬡ AGCL</span>
        <button class="icon-btn" @click="auth.disconnect(); router.push('/login')" title="Disconnect">⏻</button>
      </div>
      <nav>
        <div
          v-for="s in spaces.spaces" :key="s.id"
          class="space-item" :class="{ active: spaces.current?.id === s.id }"
          @click="open(s)"
        >
          <span class="space-avatar">{{ s.name[0].toUpperCase() }}</span>
          <span class="space-name">{{ s.name }}</span>
        </div>
      </nav>
      <div class="sidebar-footer">
        <button @click="showCreate = true">+ New Space</button>
        <button @click="showJoin = true">Join via code</button>
      </div>
    </aside>

    <main class="main-area">
      <div v-if="!spaces.current" class="empty-state">
        <h2>Select or create a space</h2>
        <p>Spaces are where humans and agents collaborate.</p>
      </div>
    </main>

    <!-- Create modal -->
    <div v-if="showCreate" class="modal-overlay" @click.self="showCreate = false">
      <div class="modal">
        <h3>New Space</h3>
        <label>Name</label>
        <input v-model="newName" placeholder="e.g. product-team" />
        <label>Description</label>
        <input v-model="newDesc" placeholder="optional" />
        <p v-if="createErr" class="err">{{ createErr }}</p>
        <div class="modal-actions">
          <button @click="showCreate = false">Cancel</button>
          <button class="primary" @click="doCreate">Create</button>
        </div>
      </div>
    </div>

    <!-- Join modal -->
    <div v-if="showJoin" class="modal-overlay" @click.self="showJoin = false">
      <div class="modal">
        <h3>Join a Space</h3>
        <label>Space ID</label>
        <input v-model="joinSpaceId" placeholder="space id" />
        <label>Invite Code</label>
        <input v-model="joinCode" placeholder="8-char code" />
        <p v-if="joinErr" class="err">{{ joinErr }}</p>
        <div class="modal-actions">
          <button @click="showJoin = false">Cancel</button>
          <button class="primary" @click="doJoin">Join</button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted } from "vue";
import { useRouter } from "vue-router";
import { useAuthStore } from "../stores/auth";
import { useSpacesStore, type Space } from "../stores/spaces";

const auth = useAuthStore();
const spaces = useSpacesStore();
const router = useRouter();

const showCreate = ref(false);
const showJoin = ref(false);
const newName = ref(""); const newDesc = ref(""); const createErr = ref("");
const joinSpaceId = ref(""); const joinCode = ref(""); const joinErr = ref("");

onMounted(() => spaces.fetchSpaces());

function open(s: Space) {
  spaces.setCurrent(s);
  router.push(`/spaces/${s.id}`);
}

async function doCreate() {
  createErr.value = "";
  if (!newName.value.trim()) { createErr.value = "Name required"; return; }
  try {
    const s = await spaces.createSpace(newName.value.trim(), newDesc.value.trim());
    showCreate.value = false; newName.value = ""; newDesc.value = "";
    if (s) open(s);
  } catch (e: any) { createErr.value = e?.message || "Failed"; }
}

async function doJoin() {
  joinErr.value = "";
  if (!joinSpaceId.value || !joinCode.value) { joinErr.value = "Both fields required"; return; }
  try {
    await spaces.joinSpace(joinSpaceId.value.trim(), joinCode.value.trim());
    showJoin.value = false; joinSpaceId.value = ""; joinCode.value = "";
  } catch (e: any) { joinErr.value = e?.message || "Failed"; }
}
</script>

<style scoped>
.dir-layout { display:flex; height:100vh; }
.sidebar { width:220px; background:#1a1a2e; display:flex; flex-direction:column; border-right:1px solid #2a2a3e; }
.sidebar-header { display:flex; align-items:center; justify-content:space-between; padding:.75rem 1rem; border-bottom:1px solid #2a2a3e; }
.logo { font-weight:600; font-size:1rem; }
.icon-btn { background:none; border:none; color:#888; font-size:1rem; }
nav { flex:1; overflow-y:auto; padding:.5rem 0; }
.space-item { display:flex; align-items:center; gap:.6rem; padding:.5rem 1rem; cursor:pointer; border-radius:6px; margin:0 .5rem; }
.space-item:hover, .space-item.active { background:#2a2a4e; }
.space-avatar { width:28px; height:28px; border-radius:8px; background:#5865f2; display:flex; align-items:center; justify-content:center; font-size:.8rem; font-weight:600; flex-shrink:0; }
.space-name { font-size:.9rem; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.sidebar-footer { padding:.75rem; display:flex; flex-direction:column; gap:.5rem; border-top:1px solid #2a2a3e; }
.sidebar-footer button { background:#2a2a4e; border:none; border-radius:6px; padding:.4rem .75rem; color:#ccc; font-size:.8rem; }
.main-area { flex:1; display:flex; align-items:center; justify-content:center; }
.empty-state { text-align:center; color:#666; }
.empty-state h2 { margin-bottom:.5rem; }
.modal-overlay { position:fixed; inset:0; background:rgba(0,0,0,.6); display:flex; align-items:center; justify-content:center; z-index:100; }
.modal { background:#1a1a2e; border-radius:12px; padding:1.5rem; width:340px; }
.modal h3 { margin-bottom:1rem; }
.modal label { display:block; font-size:.8rem; color:#aaa; margin:.6rem 0 .2rem; }
.modal input { width:100%; background:#0f0f17; border:1px solid #333; border-radius:6px; padding:.45rem .7rem; color:#e2e2f0; }
.modal-actions { display:flex; justify-content:flex-end; gap:.5rem; margin-top:1rem; }
.modal-actions button { background:#2a2a4e; border:none; border-radius:6px; padding:.4rem .9rem; color:#ccc; }
.modal-actions .primary { background:#5865f2; color:#fff; }
.err { color:#f87171; font-size:.8rem; margin-top:.4rem; }
</style>
