<template>
  <div class="msg-list" ref="listEl">
    <div
      v-for="msg in messages" :key="msg.id"
      class="msg-row"
      :class="{ 'is-human': msg.sender_type === 'human', 'is-agent': msg.sender_type === 'agent', 'is-ambient': msg.ambient }"
      @mouseenter="hoverId = msg.id"
      @mouseleave="hoverId = null"
    >
      <div v-if="msg.sender_type === 'agent'" class="avatar">🤖</div>
      <div class="bubble-wrap">
        <div class="meta">
          <span class="sender">{{ msg.sender_id }}</span>
          <span v-if="msg.ambient" class="ambient-badge">💡 suggestion</span>
        </div>
        <div class="bubble" :class="{ partial: msg.partial }">
          <span v-if="msg.partial" class="typing-dots"><span/><span/><span/></span>
          <span v-else>{{ msg.content }}</span>
        </div>
        <div v-if="hoverId === msg.id && !msg.partial" class="action-bar">
          <button @click="$emit('promote', msg)">📌 Task</button>
        </div>
      </div>
      <div v-if="msg.sender_type === 'human'" class="avatar human-av">{{ msg.sender_id[0].toUpperCase() }}</div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, watch, nextTick, defineProps, defineEmits } from "vue";
import type { Message } from "../composables/useSpaceStream";

const props = defineProps<{ messages: Message[] }>();
defineEmits<{ (e: "promote", msg: Message): void }>();
const hoverId = ref<string | null>(null);
const listEl = ref<HTMLElement | null>(null);

watch(() => props.messages.length, async () => {
  await nextTick();
  if (listEl.value) listEl.value.scrollTop = listEl.value.scrollHeight;
});
</script>

<style scoped>
.msg-list { flex:1; overflow-y:auto; padding:1rem; display:flex; flex-direction:column; gap:.5rem; }
.msg-row { display:flex; align-items:flex-end; gap:.5rem; }
.msg-row.is-human { flex-direction:row-reverse; }
.avatar { width:32px; height:32px; border-radius:50%; background:#5865f2; display:flex; align-items:center; justify-content:center; font-size:.85rem; flex-shrink:0; }
.human-av { background:#22c55e; font-weight:600; }
.bubble-wrap { max-width:65%; position:relative; }
.meta { font-size:.7rem; color:#888; margin-bottom:.2rem; display:flex; gap:.5rem; align-items:center; }
.is-human .meta { justify-content:flex-end; }
.ambient-badge { background:#2a2a1e; color:#facc15; border-radius:4px; padding:0 .4rem; font-size:.7rem; }
.bubble { background:#1e1e30; border-radius:12px; padding:.5rem .75rem; font-size:.9rem; line-height:1.5; word-break:break-word; }
.is-human .bubble { background:#5865f2; }
.is-ambient .bubble { background:#1e1e20; border:1px solid #facc1540; }
.partial .typing-dots { display:inline-flex; gap:3px; align-items:center; }
.typing-dots span { width:6px; height:6px; border-radius:50%; background:#888; animation:blink 1.2s infinite; }
.typing-dots span:nth-child(2) { animation-delay:.2s; }
.typing-dots span:nth-child(3) { animation-delay:.4s; }
@keyframes blink { 0%,80%,100%{opacity:.2} 40%{opacity:1} }
.action-bar { position:absolute; top:-28px; right:0; background:#1a1a2e; border:1px solid #333; border-radius:6px; display:flex; }
.action-bar button { background:none; border:none; color:#ccc; padding:.2rem .5rem; font-size:.75rem; }
.action-bar button:hover { color:#fff; }
</style>
