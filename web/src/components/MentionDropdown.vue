<template>
  <div v-if="visible" class="mention-drop">
    <div
      v-for="a in filtered" :key="a.id"
      class="mention-item"
      @mousedown.prevent="select(a)"
    >
      <span class="avatar">🤖</span>
      <span class="name">{{ a.name }}</span>
      <span class="desc">{{ a.description }}</span>
    </div>
    <div v-if="!filtered.length" class="mention-empty">No agents found</div>
  </div>
</template>

<script setup lang="ts">
import { computed, defineProps, defineEmits } from "vue";

interface Agent { id: string; name: string; description: string; }
const props = defineProps<{ agents: Agent[]; query: string; visible: boolean }>();
const emit = defineEmits<{ (e: "select", agent: Agent): void }>();

const filtered = computed(() =>
  props.agents.filter(a => a.name.toLowerCase().startsWith(props.query.toLowerCase()))
);
function select(a: Agent) { emit("select", a); }
</script>

<style scoped>
.mention-drop { position:absolute; bottom:100%; left:0; background:#1e1e30; border:1px solid #333; border-radius:8px; min-width:220px; max-height:200px; overflow-y:auto; z-index:50; }
.mention-item { display:flex; align-items:center; gap:.5rem; padding:.5rem .75rem; cursor:pointer; }
.mention-item:hover { background:#2a2a4e; }
.avatar { font-size:1rem; }
.name { font-weight:500; font-size:.9rem; }
.desc { color:#888; font-size:.75rem; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.mention-empty { padding:.5rem .75rem; color:#666; font-size:.85rem; }
</style>
