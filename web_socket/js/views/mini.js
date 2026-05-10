/* Mini-Trainer — background mini-model trainer.
 * status, start/stop/pause/resume, test prompt, config, presets, checkpoint,
 * live loss + throughput sparklines built from polling. */

(function () {
  const V = window.Views = window.Views || {};

  V.mini = {
    mount(root) {
      const { el, fmtNum, loadingNode } = window.H;
      let pollTimer = null;
      const lossHist = [];
      const tputHist = [];
      const MAX = 120;

      const view = {
        unmount() { clearInterval(pollTimer); },
        async refresh() { await loadAll(); },
      };

      root.appendChild(el("section", { class: "block" }, [
        el("h2", null, "Status"),
        el("div", { class: "split-2" }, [
          el("div", { id: "mini-status", class: "card" }, loadingNode()),
          el("div", { class: "card" }, [
            el("h3", null, "Loss curve"),
            el("div", { id: "mini-loss" }),
            el("h3", { style: "margin-top:14px" }, "Throughput"),
            el("div", { id: "mini-tput" }),
          ]),
        ]),
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
          el("div", { id: "mini-out", class: "card md", style: "margin-top:10px;background:var(--bg);min-height:60px" }),
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

          // Update history
          if (s.loss != null && !isNaN(Number(s.loss))) {
            lossHist.push(Number(s.loss));
            if (lossHist.length > MAX) lossHist.shift();
          }
          if (s.throughput != null && !isNaN(Number(s.throughput))) {
            tputHist.push(Number(s.throughput));
            if (tputHist.length > MAX) tputHist.shift();
          }
          renderCharts();
        } catch (e) { UI.err(e); }
      }

      function renderCharts() {
        const lossEl = document.getElementById("mini-loss");
        const tputEl = document.getElementById("mini-tput");
        lossEl.innerHTML = "";
        tputEl.innerHTML = "";
        if (lossHist.length > 1) {
          lossEl.appendChild(window.Chart.line(lossHist, { height: 140 }));
        } else {
          lossEl.appendChild(el("div", { class: "small dim" }, "loss curve will appear after a couple of polls"));
        }
        if (tputHist.length > 1) {
          tputEl.appendChild(window.Chart.line(tputHist, { height: 140 }));
        } else {
          tputEl.appendChild(el("div", { class: "small dim" }, "throughput curve will appear after a couple of polls"));
        }
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
        out.innerHTML = "<div class='small dim'>generating…</div>";
        try {
          const res = await API.post("/node/mini/test", { prompt, max_new, temperature });
          const text = res.decoded || JSON.stringify(res, null, 2);
          out.innerHTML = "<div class='small dim' style='margin-bottom:6px'>"
                        + `step=${res.step ?? "?"} arch=${res.arch ?? "?"} strategy=${res.strategy ?? "?"}</div>`
                        + window.MD.render(text);
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
