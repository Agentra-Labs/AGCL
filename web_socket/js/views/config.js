/* Configuration — curated config tree, env editor, MAS upload, bundle import/export. */

(function () {
  const V = window.Views = window.Views || {};

  V.config = {
    mount(root) {
      const { el, loadingNode } = window.H;
      const view = {
        unmount() {},
        async refresh() { await loadAll(); },
      };

      root.appendChild(el("section", { class: "block" }, [
        el("h2", null, "Curated config"),
        el("p", { class: "small" }, "Common AGCL knobs. Saving writes to .env where applicable; some changes require a node restart."),
        el("div", { id: "cfg-curated", class: "card" }, loadingNode()),
        el("div", { class: "row", style: "margin-top:10px" }, [
          el("button", { onClick: saveCurated }, "Save curated config"),
          el("span", { class: "small dim", id: "cfg-warn" }, ""),
        ]),
      ]));

      root.appendChild(el("section", { class: "block" }, [
        el("h2", null, "Environment variables"),
        el("div", { class: "row", style: "margin-bottom:8px" }, [
          el("label", { class: "small" }, "Prefix"),
          el("input", { type: "text", id: "env-prefix", value: "AGCL_", style: "width:160px" }),
          el("button", { class: "btn-ghost", onClick: loadEnv }, "Reload"),
          el("span", { class: "spacer" }),
          el("label", { class: "toggle" }, [
            el("input", { type: "checkbox", id: "env-allowlist", checked: "" }),
            el("span", { class: "sw" }), "Use allowlist",
          ]),
          el("label", { class: "toggle" }, [
            el("input", { type: "checkbox", id: "env-persist", checked: "" }),
            el("span", { class: "sw" }), "Persist to .env",
          ]),
        ]),
        el("div", { id: "cfg-env" }, loadingNode()),
        el("div", { class: "row", style: "margin-top:8px" }, [
          el("input", { type: "text", id: "env-newkey", placeholder: "NEW_VAR_NAME" }),
          el("input", { type: "text", id: "env-newval", placeholder: "value" }),
          el("button", { onClick: addEnv }, "Add / Update"),
        ]),
      ]));

      root.appendChild(el("section", { class: "block" }, [
        el("h2", null, "MAS (mas.json)"),
        el("p", { class: "small" }, "Multi-agent topology. Upload replaces mas.json on disk and may require restart."),
        el("textarea", { id: "mas-json", style: "min-height:200px;font-family:inherit;font-size:12px" }),
        el("div", { class: "row", style: "margin-top:8px" }, [
          el("button", { class: "btn-ghost", onClick: loadMas }, "Load current"),
          el("button", { onClick: saveMas }, "Save mas.json"),
          el("span", { class: "small dim", id: "mas-warn" }, ""),
        ]),
      ]));

      root.appendChild(el("section", { class: "block" }, [
        el("h2", null, "Config bundle (export / import)"),
        el("div", { class: "card" }, [
          el("div", { class: "row" }, [
            el("button", { class: "btn-ghost", onClick: exportBundle }, "Download bundle"),
            el("input", { type: "file", id: "bundle-file", accept: ".json" }),
            el("button", { onClick: () => importBundle(false) }, "Validate (dry-run)"),
            el("button", { class: "btn-warn", onClick: () => importBundle(true) }, "Apply import"),
          ]),
          el("pre", { id: "bundle-out", class: "small mono", style: "white-space:pre-wrap;max-height:240px;overflow:auto;margin-top:10px" }),
        ]),
      ]));

      async function loadAll() {
        await Promise.all([loadCurated(), loadEnv(), loadMas()]);
      }

      async function loadCurated() {
        const wrap = document.getElementById("cfg-curated");
        wrap.innerHTML = "";
        try {
          const c = await API.get("/node/config");
          wrap.dataset.snapshot = JSON.stringify(c);
          const grid = el("div", { class: "grid" });
          renderGroup(grid, "cloud", c.cloud);
          renderGroup(grid, "local_model", c.local_model);
          renderGroup(grid, "mas", c.mas);
          renderGroup(grid, "session_defaults", c.session_defaults);
          renderGroup(grid, "storage", c.storage);
          wrap.appendChild(grid);
        } catch (e) { UI.err(e); }
      }

      function renderGroup(grid, title, data) {
        if (!data) return;
        const c = el("div", { class: "card" }, [el("h3", null, title)]);
        Object.keys(data).forEach(k => {
          const v = data[k];
          const id = `cur-${title}-${k}`;
          const isObj = v && typeof v === "object" && !Array.isArray(v);
          c.appendChild(el("div", { class: "field" }, [
            el("label", null, k),
            isObj
              ? el("textarea", { id, style: "min-height:60px;font-size:11px" }, JSON.stringify(v, null, 2))
              : el("input", { type: typeof v === "number" ? "number" : "text", id, value: v == null ? "" : String(v), step: "any" }),
          ]));
        });
        grid.appendChild(c);
      }

      async function saveCurated() {
        const snap = JSON.parse(document.getElementById("cfg-curated").dataset.snapshot || "{}");
        const patch = {};
        Object.keys(snap).forEach(group => {
          Object.keys(snap[group] || {}).forEach(k => {
            const orig = snap[group][k];
            const inp = document.getElementById(`cur-${group}-${k}`);
            if (!inp) return;
            let v = inp.value;
            if (typeof orig === "number") v = v === "" ? null : Number(v);
            else if (orig && typeof orig === "object") {
              try { v = JSON.parse(v); } catch { return; }
            }
            // Map common keys to PATCH /node/config canonical names
            patch[k] = v;
          });
        });
        try {
          const res = await API.patch("/node/config", patch);
          if (res.requires_restart) {
            document.getElementById("cfg-warn").textContent = "applied — restart node for changes to take full effect";
          } else {
            document.getElementById("cfg-warn").textContent = "applied: " + (res.applied || []).join(", ");
          }
          UI.ok("Config saved.");
          loadCurated();
        } catch (e) { UI.err(e); }
      }

      async function loadEnv() {
        const wrap = document.getElementById("cfg-env");
        wrap.innerHTML = "";
        try {
          const prefix = document.getElementById("env-prefix").value;
          const res = await API.get("/node/config/env" + (prefix ? `?prefix=${encodeURIComponent(prefix)}` : ""));
          const env = res.env || {};
          const keys = Object.keys(env).sort();
          if (!keys.length) {
            wrap.appendChild(el("div", { class: "empty" }, "No env vars match prefix."));
            return;
          }
          const tbl = el("table", null, [
            el("thead", null, el("tr", null, ["key", "value", ""].map(h => el("th", { text: h })))),
          ]);
          const tb = el("tbody");
          keys.forEach(k => {
            const id = "env-" + k;
            tb.appendChild(el("tr", null, [
              el("td", { class: "mono", text: k }),
              el("td", null, [el("input", { type: "text", id, value: env[k] == null ? "" : String(env[k]), style: "width:100%" })]),
              el("td", null, [el("button", { class: "btn-ghost", onClick: () => updEnv(k) }, "Save")]),
            ]));
          });
          tbl.appendChild(tb);
          wrap.appendChild(tbl);
        } catch (e) { UI.err(e); }
      }

      async function updEnv(key) {
        const v = document.getElementById("env-" + key).value;
        const persist = document.getElementById("env-persist").checked;
        const allow = document.getElementById("env-allowlist").checked;
        try {
          const res = await API.patch("/node/config/env", { updates: { [key]: v }, persist, allowlist_only: allow });
          if (res.rejected && res.rejected.length) UI.toast("Rejected: " + res.rejected.join(", "), "err");
          else UI.ok("Saved " + key);
        } catch (e) { UI.err(e); }
      }

      async function addEnv() {
        const k = document.getElementById("env-newkey").value.trim();
        const v = document.getElementById("env-newval").value;
        if (!k) { UI.err(new Error("Key required.")); return; }
        const persist = document.getElementById("env-persist").checked;
        const allow = document.getElementById("env-allowlist").checked;
        try {
          const res = await API.patch("/node/config/env", { updates: { [k]: v }, persist, allowlist_only: allow });
          if (res.rejected && res.rejected.length) UI.toast("Rejected: " + res.rejected.join(", "), "err");
          else UI.ok("Saved.");
          document.getElementById("env-newkey").value = "";
          document.getElementById("env-newval").value = "";
          loadEnv();
        } catch (e) { UI.err(e); }
      }

      async function loadMas() {
        try {
          const res = await API.get("/node/config/mas");
          document.getElementById("mas-json").value = JSON.stringify(res.data || {}, null, 2);
          document.getElementById("mas-warn").textContent = "source: " + (res.source || "—");
        } catch (e) { UI.err(e); }
      }

      async function saveMas() {
        let body;
        try { body = JSON.parse(document.getElementById("mas-json").value); }
        catch (e) { UI.err(new Error("Invalid JSON")); return; }
        try {
          const res = await API.put("/node/config/mas", body);
          UI.ok(`Saved ${res.agents} agents.` + (res.requires_restart ? " Restart required." : ""));
          loadMas();
        } catch (e) { UI.err(e); }
      }

      async function exportBundle() {
        try {
          const res = await API.get("/node/config/all");
          const blob = new Blob([JSON.stringify(res, null, 2)], { type: "application/json" });
          const a = document.createElement("a");
          a.href = URL.createObjectURL(blob);
          a.download = "agcl-config.json";
          a.click();
        } catch (e) { UI.err(e); }
      }

      async function importBundle(apply) {
        const file = document.getElementById("bundle-file").files[0];
        if (!file) { UI.err(new Error("Pick a bundle file first.")); return; }
        const txt = await file.text();
        let body;
        try { body = JSON.parse(txt); } catch { UI.err(new Error("Invalid JSON")); return; }
        const path = "/node/config/import" + (apply ? "?apply=1" : "");
        try {
          const res = await API.post(path, body);
          document.getElementById("bundle-out").textContent = JSON.stringify(res, null, 2);
          UI.ok(apply ? "Applied." : "Validated.");
        } catch (e) { UI.err(e); }
      }

      loadAll();
      return view;
    },
  };
})();
