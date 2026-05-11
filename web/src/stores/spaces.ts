import { defineStore } from "pinia";
import { ref } from "vue";
import { useAuthStore } from "./auth";

export interface Space {
  id: string; name: string; description: string;
  invite_code: string; member_ids: string[]; agent_ids: string[];
  ambient_enabled: boolean; ambient_agent_id: string; ambient_trigger_every_n: number;
}

export const useSpacesStore = defineStore("spaces", () => {
  const spaces = ref<Space[]>([]);
  const current = ref<Space | null>(null);

  async function fetchSpaces() {
    const auth = useAuthStore();
    if (!auth.client) return;
    const res = await auth.client.request<{ spaces: Space[] }>("/node/collab/spaces");
    spaces.value = res.spaces;
  }

  async function createSpace(name: string, description = "") {
    const auth = useAuthStore();
    if (!auth.client) return;
    const space = await auth.client.request<Space>("/node/collab/spaces", {
      method: "POST",
      body: JSON.stringify({ name, description }),
    });
    spaces.value.push(space);
    return space;
  }

  async function joinSpace(spaceId: string, inviteCode: string, userId = "me", displayName = "Me") {
    const auth = useAuthStore();
    if (!auth.client) return;
    await auth.client.request(`/node/collab/spaces/${spaceId}/join`, {
      method: "POST",
      body: JSON.stringify({ invite_code: inviteCode, user_id: userId, display_name: displayName }),
    });
    await fetchSpaces();
  }

  function setCurrent(space: Space) { current.value = space; }

  return { spaces, current, fetchSpaces, createSpace, joinSpace, setCurrent };
});
