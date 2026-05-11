import { defineStore } from "pinia";
import { ref, computed } from "vue";
import { AgclClient } from "@agcl/client";

export const useAuthStore = defineStore("auth", () => {
  const host = ref(localStorage.getItem("agcl.host") || "http://localhost:9876");
  const bearerKey = ref(localStorage.getItem("agcl.key") || "");
  const client = ref<AgclClient | null>(null);

  const isAuthed = computed(() => !!bearerKey.value && !!client.value);

  function connect(h: string, key: string) {
    host.value = h;
    bearerKey.value = key;
    localStorage.setItem("agcl.host", h);
    localStorage.setItem("agcl.key", key);
    client.value = new AgclClient({ baseUrl: h, authKey: key });
  }

  function disconnect() {
    host.value = "";
    bearerKey.value = "";
    client.value = null;
    localStorage.removeItem("agcl.host");
    localStorage.removeItem("agcl.key");
  }

  // restore on load
  if (host.value && bearerKey.value) {
    client.value = new AgclClient({ baseUrl: host.value, authKey: bearerKey.value });
  }

  return { host, bearerKey, client, isAuthed, connect, disconnect };
});
