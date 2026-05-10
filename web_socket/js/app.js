/* AGCL Console — main shell.
 *
 * Connection panel, sidebar nav, view router, refresh + toast.
 * Each view is registered as window.Views.<name> = { mount(root), unmount() }. */

(function () {
  "use strict";

  const $ = (sel, root) => (root || document).querySelector(sel);
  const $$ = (sel, root) => Array.from((root || document).querySelectorAll(sel));

  const VIEW_TITLES = {
    overview: "Overview",
    chat: "Chat",
    mas: "Recursive MAS",
    topics: "Topics",
    mini: "Mini-Trainer",
    usage: "Usage & Cost",
    config: "Configuration",
    toolkit: "Toolkit",
    plugins: "Plugins",
    diagnostics: "Diagnostics",
    deploy: "Deploy Manifests",
  };

  const App = {
    currentView: null,
    currentInstance: null,
    refreshTimer: null,

    init() {
      window.UI = this;
      this.bindAuth();
      this.bindNav();
      $("#logout").addEventListener("click", () => this.logout());
      $("#btn-refresh").addEventListener("click", () => this.refresh());

      if (API.isConfigured()) {
        this.tryConnect();
      } else {
        this.showAuth();
      }
    },

    /* ---------- AUTH FLOW ---------- */
    showAuth() {
      $("#auth").hidden = false;
      $("#app").hidden = true;
      const hostInput = $("#auth-host");
      const keyInput = $("#auth-key");
      hostInput.value = API.host || (window.location.protocol === "https:"
        ? "https://localhost:9876"
        : "http://localhost:9876");
      keyInput.value = "";
      keyInput.focus();
    },

    bindAuth() {
      $("#auth-form").addEventListener("submit", async (e) => {
        e.preventDefault();
        const host = $("#auth-host").value.trim();
        const key  = $("#auth-key").value.trim();
        const err  = $("#auth-err");
        err.textContent = "";
        if (!host || !key) { err.textContent = "Host and key are required."; return; }
        API.save(host, key);
        try {
          await API.verify();
          this.enterApp();
        } catch (e) {
          err.textContent = "Connect failed: " + (e.message || e);
        }
      });
    },

    async tryConnect() {
      try {
        await API.verify();
        this.enterApp();
      } catch (e) {
        this.showAuth();
        $("#auth-err").textContent = "Saved key rejected: " + (e.message || e);
      }
    },

    enterApp() {
      $("#auth").hidden = true;
      $("#app").hidden = false;
      this.updateConnPill(true);
      const hash = (location.hash || "#overview").replace(/^#/, "");
      this.go(VIEW_TITLES[hash] ? hash : "overview");
    },

    updateConnPill(ok) {
      $("#conn-host").textContent = API.host || "—";
      const dot = $("#conn-dot");
      dot.classList.toggle("ok", ok);
      dot.classList.toggle("err", !ok);
    },

    logout() {
      this.unmount();
      API.clear();
      this.showAuth();
    },

    /* ---------- VIEW ROUTER ---------- */
    bindNav() {
      $$("#nav a").forEach(a => {
        a.addEventListener("click", (e) => {
          e.preventDefault();
          this.go(a.dataset.view);
        });
      });
      window.addEventListener("hashchange", () => {
        const v = (location.hash || "#overview").replace(/^#/, "");
        if (v !== this.currentView && VIEW_TITLES[v]) this.go(v);
      });
    },

    go(name) {
      if (!VIEW_TITLES[name]) name = "overview";
      this.unmount();
      this.currentView = name;
      location.hash = name;
      $$("#nav a").forEach(a => a.classList.toggle("active", a.dataset.view === name));
      $("#view-title").textContent = VIEW_TITLES[name];
      const root = $("#view-root");
      root.innerHTML = "";

      const view = (window.Views || {})[name];
      if (!view) {
        root.innerHTML = `<div class="empty">View "${name}" not implemented.</div>`;
        return;
      }
      try {
        this.currentInstance = view.mount(root) || view;
      } catch (e) {
        console.error(e);
        root.innerHTML = `<div class="empty">Mount error: ${escapeHtml(e.message || String(e))}</div>`;
      }
    },

    unmount() {
      if (this.currentInstance && typeof this.currentInstance.unmount === "function") {
        try { this.currentInstance.unmount(); } catch (e) { console.warn(e); }
      }
      this.currentInstance = null;
    },

    refresh() {
      if (this.currentInstance && typeof this.currentInstance.refresh === "function") {
        this.currentInstance.refresh();
        this.tick();
      } else {
        this.go(this.currentView);
      }
    },

    tick() {
      $("#last-tick").textContent = "updated " + new Date().toLocaleTimeString();
    },

    /* ---------- TOAST ---------- */
    toast(msg, kind) {
      const t = $("#toast");
      t.textContent = msg;
      t.className = "toast" + (kind ? " " + kind : "");
      t.hidden = false;
      clearTimeout(this._toastTimer);
      this._toastTimer = setTimeout(() => { t.hidden = true; }, 3500);
    },

    err(e) {
      const msg = (e && e.message) ? e.message : String(e);
      this.toast(msg, "err");
      console.error(e);
    },

    ok(msg) { this.toast(msg, "ok"); },
  };

  /* ---------- HELPERS ---------- */
  function escapeHtml(s) {
    return String(s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
  }
  function fmtNum(n) {
    if (n === null || n === undefined) return "—";
    if (typeof n !== "number") return String(n);
    if (Math.abs(n) >= 1e6) return (n/1e6).toFixed(2) + "M";
    if (Math.abs(n) >= 1e3) return (n/1e3).toFixed(1) + "k";
    return Math.round(n * 100) / 100 + "";
  }
  function fmtUsd(n) {
    if (n === null || n === undefined) return "—";
    return "$" + (Math.round(n * 10000) / 10000).toFixed(4);
  }
  function fmtSec(s) {
    if (s === null || s === undefined) return "—";
    if (s < 60) return s.toFixed(0) + "s";
    if (s < 3600) return (s/60).toFixed(1) + "m";
    return (s/3600).toFixed(1) + "h";
  }
  function fmtTs(t) {
    if (!t) return "—";
    return new Date(t * 1000).toLocaleString();
  }
  function el(tag, attrs, children) {
    const e = document.createElement(tag);
    if (attrs) for (const k in attrs) {
      if (k === "class") e.className = attrs[k];
      else if (k === "html") e.innerHTML = attrs[k];
      else if (k === "text") e.textContent = attrs[k];
      else if (k.startsWith("on")) e.addEventListener(k.slice(2).toLowerCase(), attrs[k]);
      else if (attrs[k] === false || attrs[k] === null || attrs[k] === undefined) {}
      else e.setAttribute(k, attrs[k]);
    }
    if (children) (Array.isArray(children) ? children : [children]).forEach(c => {
      if (c == null) return;
      e.appendChild(typeof c === "string" ? document.createTextNode(c) : c);
    });
    return e;
  }
  function loadingNode(text) {
    return el("div", { class: "small" }, [el("span", { class: "spinner" }), " " + (text || "loading…")]);
  }

  window.H = { escapeHtml, fmtNum, fmtUsd, fmtSec, fmtTs, el, loadingNode, $, $$ };
  window.UI = App;

  document.addEventListener("DOMContentLoaded", () => App.init());
})();
