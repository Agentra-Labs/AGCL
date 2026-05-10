/* Mini-Trainer — background mini-model trainer.
 * status, start/stop/pause/resume, test prompt, config, presets, checkpoint. */

(function () {
  const V = window.Views = window.Views || {};
  const { el, fmtNum, loadingNode } = window.H;

  V.mini = {
    mount(root) {
      let pollTimer = null;

      const view = {
        unmount() { clearInterval(pollTimer); },
        async refresh() { await loadAll(); },
      };

      root.appendChild(el("section", { class: "block" }, [
        el("h2", null, "Status"),
        el("div", { id: "mini-status", class: "card" }, loadingNode()),
        el("div", { class: "row", style: "margin-top:10px" }, [
          el("button", { class: "btn-ok",   onClick: () => act("start") },  "Start"),
          el("button", { class: "btn-warn", onClick: () => act("pause") },  "Pause"),
          el("button", { class: "btn-ok",   onClick: () => act("resume") }, "Resume"),
          el("button", { class: "btn-err",  onClick: () => act("stop") },   "Stop"),
          el("button", { class: "btn-ghost", onClick: () => act("checkpoint") }, "Force checkpoint"),
          el("span", { class: "spacer" }),
          el("label", { class: "toggle" }, [
            el("input", { type: "checkbox", id: "mini-poll", checked: "" }),
            el("span", { class: "sw" }), "Auto-refresh",
          ]),
        ]),
      ]));

      root.appendChild(el("section", { class: "block" }, [
        el("h2", null, "Test sample (generate from current weights)"),
        el("div", { class: "card" }, [
          el("div", { class: "field" }, [
            el("label", null, "Prompt"),
            el("textarea", { id: "mini-prompt", placeholder: "Prompt for the mini-model…" }),
          ]),
          el("div", { class: "row" }, [
            numFieldInline("mini-max-new", "max_new", 80, 1, 1024, 1),
            numFieldInline("mini-temp", "temperature", 0.8, 0, 2, 0.05),
            el("button", { onClick: testGen }, "Generate"),
          ]),
          el("div", { id: "mini-out", class: "card", style: "margin-top:10px;background:var(--bg);min-height:60px;white-space:pre-wrap" }),
        ]),
      ]));

      root.appendChild(el("section", { class: "block" }, [
        el("h2", null, "Config"),
        el("div", { id: "mini-config", class: "card" }, loadingNode()),
        el("div", { class: "row", style: "margin-top:10px" }, [
          el("button", { onClick: saveConfig }, "Save config"),
          el("span", { class: "small dim", id: "mini-cfg-warn" }, ""),
        ]),
      ]));

      root.appendChild(el("section", { class: "block" }, [
        el("h2", null, "Presets / Strategies"),
        el("div", { id: "mini-presets" }, loadingNode()),
      ]));

      function numFieldInline(id, label, value, min, max, step) {
        return el("label", { class: "small", style: "display:flex;gap:4px;align-items:center" }, [
          label,
          el("input", { type: "number", id, value, min, max, step, style: "width:80px" }),
        ]);
      }

      async function loadAll() {
        await Promise.all([loadStatus(), loadConfig(), loadPresets()]);
        UI.tick();
      }

      async function loadStatus() {
        const wrap = document.getElementById("mini-status");
        wrap.innerHTML = "";
        try {
          const s = await API.get("/node/mini/status");
          const rows = [
            ["enabled", s.enabled ? badge("ok", "yes") : badge("dim", "no")],
            ["state", s.state || "—"],
            ["arch", s.arch || "—"],
            ["step", s.step ?? 0],
            ["loss", s.loss != null ? Number(s.loss).toFixed(4) : "—"],
            ["throughput", s.throughput != null ? Number(s.throughput).toFixed(2) + " tok/s" : "—"],
            ["last checkpoint", s.last_checkpoint || s.last_ckpt || "—"],
          ];
          rows.forEach(([k, v]) => {
            wrap.appendChild(el("div", { class: "row kv" }, [
              el("span", { class: "k" }, k),
              el("span", { class: "v" }, [v && v.nodeType ? v : document.createTextNode(String(v ?? "—"))]),
            ]));
          });
        } catch (e) { UI.err(e); }
      }

      async function loadConfig() {
        const wrap = document.getElementById("mini-config");
        wrap.innerHTML = "";
        try {
          const c = await API.get("/node/mini/config");
          const keys = Object.keys(c).sort();
          if (!keys.length) {
            wrap.appendChild(el("div", { class: "empty" }, "No config returned."));
            return;
          }
          const grid = el("div", { class: "grid" });
          keys.forEach(k => {
            const v = c[k];
            const id = "cfg-" + k;
            grid.appendChild(el("div", { class: "field" }, [
              el("label", null, k),
              el("input", { type: typeof v === "number" ? "number" : "text", id, value: v == null ? "" : String(v), step: "any" }),
            ]));
          });
          wrap.appendChild(grid);
          wrap.dataset.keys = JSON.stringify(keys);
          wrap.dataset.types = JSON.stringify(keys.map(k => typeof c[k]));
        } catch (e) { UI.err(e); }
      }

      async function loadPresets() {
        const wrap = document.getElementById("mini-presets");
        wrap.innerHTML = "";
        try {
          const p = await API.get("/node/mini/presets");
          const grid = el("div", { class: "grid" });
          ["strategies", "attention", "archs"].forEach(group => {
            const v = p[group];
            const c = el("div", { class: "card" }, [el("h3", null, group)]);
            if (!v) {
              c.appendChild(el("div", { class: "small dim" }, "—"));
            } else if (Array.isArray(v)) {
              v.forEach(item => c.appendChild(el("div", { class: "small mono" }, "• " + item)));
            } else {
              Object.keys(v).forEach(name => {
                c.appendChild(el("div", { class: "row kv" }, [
                  el("span", { class: "k mono" }, name),
                  el("span", { class: "v small dim", title: JSON.stringify(v[name]) },
                    typeof v[name] === "object" ? "{ … }" : String(v[name])),
                ]));
              });
            }
            grid.appendChild(c);
          });
          wrap.appendChild(grid);
        } catch (e) { UI.err(e); }
      }

      async function act(op) {
        try {
          await API.post("/node/mini/" + op, {});
          UI.ok(op + " done.");
          loadStatus();
        } catch (e) { UI.err(e); }
      }

      async function testGen() {
        const prompt = document.getElementById("mini-prompt").value.trim();
        const max_new = Number(document.getElementById("mini-max-new").value);
        const temperature = Number(document.getElementById("mini-temp").value);
        if (!prompt) { UI.err(new Error("Prompt required.")); return; }
        const out = document.getElementById("mini-out");
        out.textContent = "generating…";
        try {
          const res = await API.post("/node/mini/test", { prompt, max_new, temperature });
          out.textContent = res.decoded || JSON.stringify(res, null, 2);
        } catch (e) { out.textContent = ""; UI.err(e); }
      }

      async function saveConfig() {
        const wrap = document.getElementById("mini-config");
        const keys = JSON.parse(wrap.dataset.keys || "[]");
        const types = JSON.parse(wrap.dataset.types || "[]");
        const patch = {};
        keys.forEach((k, i) => {
          const v = document.getElementById("cfg-" + k).value;
          if (v === "") { patch[k] = null; return; }
          if (types[i] === "number") patch[k] = Number(v);
          else patch[k] = v;
        });
        try {
          const res = await API.patch("/node/mini/config", patch);
          if (res.requires_rebuild) {
            document.getElementById("mini-cfg-warn").textContent = "requires rebuild — restart trainer for changes to take effect";
          } else {
            document.getElementById("mini-cfg-warn").textContent = "applied: " + (res.applied || []).join(", ");
          }
          UI.ok("Config saved.");
        } catch (e) { UI.err(e); }
      }

      function badge(kind, txt) { return el("span", { class: "pill " + kind }, txt); }

      pollTimer = setInterval(() => {
        if (document.getElementById("mini-poll") && document.getElementById("mini-poll").checked) {
          loadStatus();
        }
      }, 4000);

      loadAll();
      return view;
    },
  };
})();
