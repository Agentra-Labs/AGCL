import { createRouter, createWebHashHistory } from "vue-router";
import { useAuthStore } from "../stores/auth";

const router = createRouter({
  history: createWebHashHistory(),
  routes: [
    { path: "/login", component: () => import("../views/LoginView.vue") },
    { path: "/", component: () => import("../views/SpaceDirectory.vue"), meta: { requiresAuth: true } },
    { path: "/spaces/:id", component: () => import("../views/SpaceView.vue"), meta: { requiresAuth: true } },
  ],
});

router.beforeEach((to) => {
  const auth = useAuthStore();
  if (to.meta.requiresAuth && !auth.isAuthed) return "/login";
});

export default router;
