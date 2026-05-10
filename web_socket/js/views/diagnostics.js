/* Diagnostics — health, pressure (gauge + sparkline), patterns (24h heatbar). */

(function () {
  const V = window.Views = window.Views || {};
  const { el, fmtSec, loadingNode } = window.H;

  V.diagnostics = {
    mount(root) {
      let timer = null;
      const pressureHist = [];      // rolling [{t, ratio, lat}]
      const MAX_POINTS = 120;

      const view = {
        unmount() { clearInterval(timer); },
        async refresh() { await load(); },
      };

      root.appendChild(el("div", { class: "row", style: "margin-bottom:12px" }, [
        el("p", { class: "small", style: "margin:0;flex:1" },
          "Live system health and learned active-hours pattern. Auto-refresh every 5s."),
        el("label", { class: "toggle" }, [
          el("input", { type: "checkbox", id: "diag-poll", checked: "" }),
          el("span", { class: "sw" }), "Auto-refresh",
        ]),
      ]));

      root.appendChild(el("section", { class: "block" }, [
        el("h2", null, "Health"),
        el("div", { class: "split-2" }, [
          el("div", { id: "diag-cards", class: "grid", style: "grid-template-columns:repeat(auto-fill,minmax(180px,1fr))" }, [loadingNode()]),
          el("div", { class: "card" }, [
            el("h3", null, "Pressure"),
            el("div", { id: "diag-gauge", style: "display:flex;justify-content:center" }),
            el("div", { id: "diag-trend", style: "margin-top:8px" }),
          ]),
        ]),
      ]));

      root.appendChild(el("section", { class: "block" }, [
        el("h2", null, "Latency trend"),
        el("div", { id: "diag-lat", class: "card" }),
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
        const press = (p) || (h && h.pressure) || null;

        // Cards
        const cards = document.getElementById("diag-cards");
        cards.innerHTML = "";
        if (h) {
          cards.appendChild(card("System", [
            ["idle (sec)", fmtSec(h.idle_sec)],
            ["is idle", h.is_idle ? badge("ok", "yes") : badge("dim", "no")],
            ["live sessions", h.sessions_live],
          ]));
        }
        if (press) {
          cards.appendChild(card("Rate", [
            ["rate ratio", press.rate_ratio != null ? Number(press.rate_ratio).toFixed(3) : "—"],
            ["avg latency", press.avg_latency_sec != null ? Number(press.avg_latency_sec).toFixed(3) + "s" : "—"],
            ["timestamp", press.timestamp ? new Date(press.timestamp * 1000).toLocaleTimeString() : "—"],
          ]));
        }
        if (!h && !press) {
          cards.appendChild(el("div", { class: "empty" }, "Health endpoints not reachable."));
        }

        // Gauge + trend
        const gaugeEl = document.getElementById("diag-gauge");
        const trendEl = document.getElementById("diag-trend");
        const latEl   = document.getElementById("diag-lat");
        gaugeEl.innerHTML = ""; trendEl.innerHTML = ""; latEl.innerHTML = "";
        if (press) {
          const ratio = Math.min(1, Math.max(0, Number(press.rate_ratio) || 0));
          gaugeEl.appendChild(window.Chart.gauge(ratio, { size: 160, label: "of pressure limit" }));
          pressureHist.push({
            t: press.timestamp || Date.now() / 1000,
            ratio,
            lat: Number(press.avg_latency_sec) || 0,
          });
          if (pressureHist.length > MAX_POINTS) pressureHist.shift();
          if (pressureHist.length > 1) {
            trendEl.appendChild(el("div", { class: "small dim", style: "margin-bottom:4px" }, "rate ratio (last " + pressureHist.length + " ticks)"));
            trendEl.appendChild(window.Chart.sparkline(pressureHist.map(p => p.ratio), { width: 280, height: 40 }));
            latEl.appendChild(el("div", { class: "small dim", style: "margin-bottom:4px" }, "avg latency (sec)"));
            latEl.appendChild(window.Chart.line(pressureHist.map(p => p.lat), { height: 160 }));
          } else {
            trendEl.appendChild(el("div", { class: "small dim" }, "trend will appear after a couple of ticks"));
            latEl.appendChild(el("div", { class: "small dim" }, "latency curve will appear after a couple of ticks"));
          }
        } else {
          gaugeEl.appendChild(el("div", { class: "small dim" }, "no pressure data"));
        }
      }

      function renderPatterns(p) {
        const wrap = document.getElementById("diag-patterns");
        wrap.innerHTML = "";
        if (!p) {
          wrap.appendChild(el("div", { class: "small dim" }, "Patterns endpoint unavailable."));
          return;
        }
        const all = p.active_hours || [];
        const upcoming = p.upcoming_active_hours || [];
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
          // hour ticks
          if (h % 3 === 0) {
            const tx = document.createElementNS(ns, "text");
            tx.setAttribute("x", P + h*w + w/2);
            tx.setAttribute("y", H - 4);
            tx.setAttribute("text-anchor", "middle");
            tx.setAttribute("class", "axis");
            tx.setAttribute("font-size", "9");
            tx.textContent = String(h).padStart(2, "0");
            svg.appendChild(tx);
          }
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
