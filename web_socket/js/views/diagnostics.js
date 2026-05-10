/* Diagnostics — health, pressure, patterns, sessions live count. */

(function () {
  const V = window.Views = window.Views || {};
  const { el, fmtSec, loadingNode } = window.H;

  V.diagnostics = {
    mount(root) {
      let timer = null;

      const view = {
        unmount() { clearInterval(timer); },
        async refresh() { await load(); },
      };

      root.appendChild(el("div", { class: "row", style: "margin-bottom:12px" }, [
        el("p", { class: "small", style: "margin:0;flex:1" },
          "Live system health and learned active-hours pattern. Auto-refreshes every 5s."),
        el("label", { class: "toggle" }, [
          el("input", { type: "checkbox", id: "diag-poll", checked: "" }),
          el("span", { class: "sw" }), "Auto-refresh",
        ]),
      ]));

      root.appendChild(el("section", { class: "block" }, [
        el("h2", null, "Health"),
        el("div", { id: "diag-health", class: "grid" }, [loadingNode()]),
      ]));

      root.appendChild(el("section", { class: "block" }, [
        el("h2", null, "Active hours pattern"),
        el("div", { id: "diag-patterns", class: "card" }, loadingNode()),
      ]));

      async function load() {
        try {
          const [health, pressure, patterns] = await Promise.all([
            API.get("/health").catch(() => null),
            API.get("/pressure").catch(() => null),
            API.get("/patterns").catch(() => null),
          ]);
          renderHealth(health, pressure);
          renderPatterns(patterns);
          UI.tick();
        } catch (e) { UI.err(e); }
      }

      function renderHealth(h, p) {
        const wrap = document.getElementById("diag-health");
        wrap.innerHTML = "";
        if (h) {
          wrap.appendChild(card("System", [
            ["idle (sec)", fmtSec(h.idle_sec)],
            ["is idle", h.is_idle ? badge("ok", "yes") : badge("dim", "no")],
            ["live sessions", h.sessions_live],
          ]));
        }
        const press = (p) || (h && h.pressure) || null;
        if (press) {
          wrap.appendChild(card("Pressure", [
            ["rate ratio", press.rate_ratio != null ? Number(press.rate_ratio).toFixed(3) : "—"],
            ["avg latency", press.avg_latency_sec != null ? Number(press.avg_latency_sec).toFixed(3) + "s" : "—"],
            ["timestamp", press.timestamp ? new Date(press.timestamp * 1000).toLocaleTimeString() : "—"],
          ]));
          // pressure bar
          const ratio = Math.min(1, Math.max(0, Number(press.rate_ratio) || 0));
          const bar = el("div", { class: "card", style: "grid-column:1 / -1" }, [
            el("h3", null, "Load"),
            el("div", { style: `border:1px solid var(--fg); height:14px; position:relative` }, [
              el("div", { style: `background:var(--fg); width:${(ratio * 100).toFixed(1)}%; height:100%;` }),
            ]),
            el("div", { class: "small dim", style: "margin-top:4px" }, `${(ratio * 100).toFixed(1)}% of pressure limit`),
          ]);
          wrap.appendChild(bar);
        }
        if (!h && !press) {
          wrap.appendChild(el("div", { class: "empty" }, "Health endpoints not reachable."));
        }
      }

      function renderPatterns(p) {
        const wrap = document.getElementById("diag-patterns");
        wrap.innerHTML = "";
        if (!p) { wrap.appendChild(el("div", { class: "small dim" }, "Patterns endpoint unavailable.")); return; }
        const all = p.active_hours || [];
        const upcoming = p.upcoming_active_hours || [];
        // 24-bin bar
        const set = new Set(all.map(x => Number(x)));
        const ns = "http://www.w3.org/2000/svg";
        const W = 720, H = 90, P = 18;
        const svg = document.createElementNS(ns, "svg");
        svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
        svg.setAttribute("width", "100%");
        for (let h = 0; h < 24; h++) {
          const w = (W - 2*P) / 24;
          const r = document.createElementNS(ns, "rect");
          r.setAttribute("x", P + h*w + 1);
          r.setAttribute("y", 12);
          r.setAttribute("width", Math.max(1, w-2));
          r.setAttribute("height", H - 32);
          r.setAttribute("class", "bar" + (set.has(h) ? "" : " alt"));
          const t = document.createElementNS(ns, "title");
          t.textContent = String(h).padStart(2, "0") + ":00 — " + (set.has(h) ? "active" : "quiet");
          r.appendChild(t);
          svg.appendChild(r);
        }
        wrap.appendChild(svg);
        wrap.appendChild(el("div", { class: "small dim" },
          `Active hours: ${all.join(", ") || "(none yet)"}. Upcoming: ${upcoming.join(", ") || "(none)"}.`));
      }

      function card(title, rows) {
        const c = el("div", { class: "card" }, [el("h3", null, title)]);
        rows.forEach(([k, v]) => {
          c.appendChild(el("div", { class: "row kv" }, [
            el("span", { class: "k" }, k),
            el("span", { class: "v" }, [v && v.nodeType ? v : document.createTextNode(String(v ?? "—"))]),
          ]));
        });
        return c;
      }
      function badge(kind, txt) { return el("span", { class: "pill " + kind }, txt); }

      timer = setInterval(() => {
        if (document.getElementById("diag-poll") && document.getElementById("diag-poll").checked) load();
      }, 5000);

      load();
      return view;
    },
  };
})();
