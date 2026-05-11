<template>
  <div class="login-wrap">
    <div class="login-card">
      <h1>AGCL Collab</h1>
      <p class="sub">Connect to your AGCL node</p>
      <form @submit.prevent="submit">
        <label>Node URL</label>
        <input v-model="host" placeholder="http://localhost:9876" />
        <label>Bearer Key</label>
        <input v-model="key" type="password" placeholder="paste key from node startup" />
        <p v-if="error" class="err">{{ error }}</p>
        <button type="submit" :disabled="loading">{{ loading ? "Connecting…" : "Connect" }}</button>
      </form>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref } from "vue";
import { useRouter } from "vue-router";
import { useAuthStore } from "../stores/auth";

const auth = useAuthStore();
const router = useRouter();
const host = ref(auth.host || "http://localhost:9876");
const key = ref("");
const error = ref("");
const loading = ref(false);

async function submit() {
  error.value = "";
  loading.value = true;
  try {
    auth.connect(host.value, key.value);
    await auth.client!.request("/node/auth/verify");
    router.push("/");
  } catch (e: any) {
    error.value = e?.message || "Connection failed";
    auth.disconnect();
  } finally {
    loading.value = false;
  }
}
</script>

<style scoped>
.login-wrap { display:flex; align-items:center; justify-content:center; height:100vh; }
.login-card { background:#1a1a2e; border-radius:12px; padding:2rem; width:360px; }
h1 { font-size:1.5rem; margin-bottom:.25rem; }
.sub { color:#888; font-size:.85rem; margin-bottom:1.5rem; }
label { display:block; font-size:.8rem; color:#aaa; margin:.75rem 0 .25rem; }
input { width:100%; background:#0f0f17; border:1px solid #333; border-radius:6px; padding:.5rem .75rem; color:#e2e2f0; }
button { margin-top:1.25rem; width:100%; background:#5865f2; border:none; border-radius:6px; padding:.65rem; color:#fff; font-size:.95rem; }
button:disabled { opacity:.5; }
.err { color:#f87171; font-size:.8rem; margin-top:.5rem; }
</style>
