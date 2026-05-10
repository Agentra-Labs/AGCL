/* Overview — system snapshot, runtime info, MAS config, local model. */

(function () {
  const V = window.Views = window.Views || {};
  const { el, fmtNum, fmtTs, loadingNode } = window.H;

  V.overview = {
    mount(root) {
      const view = {
        unmount() {},
        async refresh() { await load(); },
      };

      root.appendChild(el("div", { class: "row", style: "margin-bottom:14px" }, [
        el("span", { class: "small dim" }, "Live snapshot of the connected node — what it knows about itself."),
      ]));

      const grid = el("div", { class: "grid", id: "ov-grid" });
      root.appendChild(grid);
      grid.appendChild(loadingNode("loading system info…"));

      const masSec = el("section", { class: "block" }, [
        el("h2", null, "Multi-Agent Configuration"),
        el("div", { id: "ov-mas" }, loadingNode()),
      ]);
      root.appendChild(masSec);

      const lmSec = el("section", { class: "block" }, [
        el("h2", null, "Local Model"),
        el("div", { id: "ov-lm" }, loadingNode()),
      ]);
      root.appendChild(lmSec);

      const gpuSec = el("section", { class: "block" }, [
        el("h2", null, "Runtime / GPUs"),
        el("div", { id: "ov-runtime" }, loadingNode()),
      ]);
      root.appendChild(gpuSec);

      async function load() {
        try {
          const [info, runtime] = await Promise.all([
            API.get("/node/info"),
            API.get("/node/runtime/info").catch(() => null),
          ]);
          renderInfo(info);
          renderRuntime(runtime);
          UI.tick();
        } catch (e) { UI.err(e); }
      }

      function renderInfo(info) {
        const g = document.getElementById("ov-grid");
        g.innerHTML = "";
        const platform = info.platform || {};
        const mas = info.mas || {};
        const lm = info.local_model || {};

        g.appendChild(card("Service", [
          ["service", info.service],
          ["version", info.version || "—"],
          ["state dir", platform.state_dir],
          ["topics", info.topics_count],
        ]));

        g.appendChild(card("Cloud Providers", [
          ["default", platform.default_cloud],
          ["openai key", platform.openai_configured ? badge("ok", "set") : badge("dim", "missing")],
          ["claude key", platform.claude_configured ? badge("ok", "set") : badge("dim", "missing")],
          ["openai model", platform.openai_model || "—"],
          ["claude model", platform.claude_model || "—"],
        ]));

        g.appendChild(card("MAS", [
          ["pattern", mas.pattern || "—"],
          ["agents", (mas.agents || []).length],
          ["device", mas.device || "—"],
          ["dtype", mas.dtype || "—"],
          ["rounds", mas.rounds],
        ]));

        // MAS detail
        const masEl = document.getElementById("ov-mas");
        masEl.innerHTML = "";
        if (!mas.agents || mas.agents.length === 0) {
          masEl.appendChild(el("div", { class: "empty" }, "No MAS agents configured."));
        } else {
          const tbl = el("table", null, [
            el("thead", null, el("tr", null, ["name","kind","model","role","backend"].map(h => el("th", { text: h })))),
          ]);
          const tb = el("tbody");
          mas.agents.forEach(a => {
            tb.appendChild(el("tr", null, [
              el("td", { text: a.name || "—" }),
              el("td", { text: a.kind || "—" }),
              el("td", { class: "clip", title: a.model || "" }, a.model || "—"),
              el("td", { text: a.role || "—" }),
              el("td", { text: a.backend || "—" }),
            ]));
          });
          tbl.appendChild(tb);
          masEl.appendChild(tbl);
        }

        // Local model
        const lmEl = document.getElementById("ov-lm");
        lmEl.innerHTML = "";
        const lmRows = [
          ["path", lm.path || "—"],
          ["type", lm.type || "—"],
          ["context", lm.n_ctx],
          ["GPU layers", lm.n_gpu_layers],
          ["threads", lm.n_threads],
          ["loaded", lm.loaded ? badge("ok", "yes") : badge("dim", "lazy")],
        ];
        lmEl.appendChild(card("Local model", lmRows, true));
      }

      function renderRuntime(rt) {
        const r = document.getElementById("ov-runtime");
        r.innerHTML = "";
        if (!rt) {
          r.appendChild(el("div", { class: "empty" }, "Runtime endpoint not available."));
          return;
        }
        const grid = el("div", { class: "grid" });
        grid.appendChild(card("Process", [
          ["python", rt.python || "—"],
          ["platform", rt.platform || "—"],
          ["machine", rt.machine || "—"],
          ["pid", rt.pid],
          ["cwd", rt.cwd || "—"],
          ["env vars", rt.env_count],
        ]));
        const tk = rt.toolkit || {};
        grid.appendChild(card("Toolkit Snapshot", Object.keys(tk).map(k => [
          k, (tk[k] && tk[k].configured) ? badge("ok", "configured") : badge("dim", "off")
        ])));

        const gpus = rt.gpus || [];
        if (gpus.length) {
          const tbl = el("table", null, [
            el("thead", null, el("tr", null, ["index","name","mem total","mem free","util %"].map(h => el("th", { text: h })))),
          ]);
          const tb = el("tbody");
          gpus.forEach((g, i) => {
            tb.appendChild(el("tr", null, [
              el("td", { text: String(g.index ?? i) }),
              el("td", { text: g.name || "—" }),
              el("td", { text: g.memory_total || "—" }),
              el("td", { text: g.memory_free || "—" }),
              el("td", { text: g.utilization || "—" }),
            ]));
          });
          tbl.appendChild(tb);
          r.appendChild(grid);
          r.appendChild(el("h3", { class: "small", style: "margin-top:18px" }, "GPUs"));
          r.appendChild(tbl);
        } else {
          r.appendChild(grid);
          r.appendChild(el("p", { class: "small dim" }, "No GPUs detected (or nvidia-smi unavailable)."));
        }
      }

      function card(title, rows, full) {
        const c = el("div", { class: "card" + (full ? "" : "") }, [el("h3", { text: title })]);
        rows.forEach(([k, v]) => {
          const valNode = (v && v.nodeType) ? v : document.createTextNode(v == null ? "—" : String(v));
          c.appendChild(el("div", { class: "row kv" }, [
            el("span", { class: "k" }, k),
            el("span", { class: "v" }, [valNode]),
          ]));
        });
        return c;
      }
      function badge(kind, text) { return el("span", { class: "pill " + kind }, text); }

      load();
      return view;
    },
  };
})();
