/* CLI Actions — orchestrates existing endpoints into one-click flows that
 * mirror common CLI sequences. Everything here drives endpoints that
 * already exist; nothing is web-only. */

(function () {
  const V = window.Views = window.Views || {};

  V.actions = {
    mount(root) {
      const { el, fmtNum, fmtUsd, loadingNode } = window.H;
      const view = {
        unmount() {},
        async refresh() { await loadStrict(); },
      };

      root.appendChild(el("p", { class: "small" },
        "Quick actions that wrap the same endpoints the CLI uses. Use these instead of dropping to a terminal — every change here is also visible to ",
        el("code", null, "agcl"), " on the same host."));

      // --- DIAGNOSTIC SNAPSHOT
      root.appendChild(el("section", { class: "block" }, [
        el("h2", null, "Full diagnostic snapshot"),
        el("p", { class: "small dim" },
          "Calls /node/info, /node/runtime/info, /health, /pressure, /node/toolkit/discover, /node/usage/summary, /node/mas/sessions, /node/topics in parallel and dumps the merged JSON."),
        el("div", { class: "row" }, [
          el("button", { onClick: () => snapshot() }, "Run snapshot"),
          el("button", { class: "btn-ghost", onClick: () => downloadSnap() }, "Download last as .json"),
          el("button", { class: "btn-ghost", onClick: () => copySnap() }, "Copy"),
        ]),
        el("pre", { id: "snap-out", class: "card mono small", style: "white-space:pre-wrap;max-height:280px;overflow:auto;background:var(--bg);margin-top:10px" }, ""),
      ]));

      // --- PING ALL ADAPTERS
      root.appendChild(el("section", { class: "block" }, [
        el("h2", null, "Ping every toolkit adapter"),
        el("p", { class: "small dim" },
          "Calls /node/toolkit/discover, then /node/toolkit/ping/{adapter} for each adapter the discover step lists. Equivalent to running ",
          el("code", null, "agcl toolkit ping <each>"), " in a loop."),
        el("div", { class: "row" }, [
          el("button", { onClick: () => pingAll() }, "Run ping-all"),
        ]),
        el("div", { id: "ping-out", style: "margin-top:10px" }),
      ]));

      // --- STRICT MODE TOGGLE
      root.appendChild(el("section", { class: "block" }, [
        el("h2", null, "Strict mode (anti-hallucination)"),
        el("p", { class: "small dim" },
          "When enabled, local MAS output is always handed to the cloud continuator before being shown — never served raw. Persists to .env via /node/config/env."),
        el("div", { class: "row" }, [
          el("label", { class: "toggle" }, [
            el("input", { type: "checkbox", id: "strict-tog" }),
            el("span", { class: "sw" }), "Strict mode",
          ]),
          el("span", { class: "small dim", id: "strict-state" }, "—"),
        ]),
      ]));

      // --- SMOKE TEST
      root.appendChild(el("section", { class: "block" }, [
        el("h2", null, "Cloud smoke test"),
        el("p", { class: "small dim" },
          "Sends a one-shot prompt through /node/toolkit/chat (gateway-routed). Verifies the configured cloud / gateway path actually replies."),
        el("div", { class: "row" }, [
          el("input", { type: "text", id: "smoke-prompt", value: "Say hello in five words.", style: "flex:1" }),
          el("button", { onClick: () => smoke() }, "Smoke test"),
        ]),
        el("pre", { id: "smoke-out", class: "card mono small", style: "white-space:pre-wrap;background:var(--bg);min-height:50px;margin-top:10px" }, ""),
      ]));

      // --- BUNDLE EXPORT
      root.appendChild(el("section", { class: "block" }, [
        el("h2", null, "Config bundle export"),
        el("p", { class: "small dim" },
          "Calls /node/config/all and offers the JSON for download. Same payload as ",
          el("code", null, "agcl config export agcl-config.json"), "."),
        el("div", { class: "row" }, [
          el("button", { onClick: () => exportBundle() }, "Export bundle"),
        ]),
      ]));

      // --- CLEAR SESSIONS
      root.appendChild(el("section", { class: "block" }, [
        el("h2", null, "Clear in-memory sessions"),
        el("p", { class: "small dim" },
          "Flushes every chat session and rebuilds the MAS singleton. Equivalent to a soft restart without dropping the process."),
        el("div", { class: "row" }, [
          el("button", { class: "btn-warn", onClick: () => clearAll() }, "Flush + rebuild"),
        ]),
      ]));

      // --- HEAVY URL TARGET (CORS-aware ping for remote node)
      root.appendChild(el("section", { class: "block" }, [
        el("h2", null, "Verify a remote node URL"),
        el("p", { class: "small dim" },
          "Calls GET <url>/node/health on a different node, using your CURRENT bearer key. Useful before pointing the orchestrator at it."),
        el("div", { class: "row" }, [
          el("input", { type: "text", id: "rt-url", placeholder: "https://lab.local:9876", style: "flex:1" }),
          el("button", { onClick: () => verifyRemote() }, "Probe"),
        ]),
        el("pre", { id: "rt-out", class: "card mono small", style: "white-space:pre-wrap;background:var(--bg);min-height:30px;margin-top:10px" }, ""),
      ]));

      let lastSnap = null;

      async function snapshot() {
        const out = document.getElementById("snap-out");
        out.textContent = "running…";
        try {
          const [info, runtime, health, pressure, discover, usage, masSes, topics] = await Promise.all([
            API.get("/node/info").catch(e => ({ _error: String(e) })),
            API.get("/node/runtime/info").catch(e => ({ _error: String(e) })),
            API.get("/health").catch(e => ({ _error: String(e) })),
            API.get("/pressure").catch(e => ({ _error: String(e) })),
            API.get("/node/toolkit/discover").catch(e => ({ _error: String(e) })),
            API.get("/node/usage/summary").catch(e => ({ _error: String(e) })),
            API.get("/node/mas/sessions").catch(e => ({ _error: String(e) })),
            API.get("/node/topics").catch(e => ({ _error: String(e) })),
          ]);
          lastSnap = {
            captured_at: new Date().toISOString(),
            host: API.host,
            info, runtime, health, pressure, discover, usage,
            mas_sessions: masSes, topics,
          };
          out.textContent = JSON.stringify(lastSnap, null, 2);
          UI.ok("Snapshot captured.");
        } catch (e) { UI.err(e); out.textContent = ""; }
      }

      function copySnap() {
        const t = document.getElementById("snap-out").textContent || "";
        if (!t) return;
        navigator.clipboard.writeText(t).then(() => UI.ok("Copied."), () => UI.err(new Error("Copy failed")));
      }

      function downloadSnap() {
        if (!lastSnap) { UI.err(new Error("Run a snapshot first.")); return; }
        const blob = new Blob([JSON.stringify(lastSnap, null, 2)], { type: "application/json" });
        const a = document.createElement("a");
        a.href = URL.createObjectURL(blob);
        a.download = "agcl-snapshot-" + Date.now() + ".json";
        a.click();
      }

      async function pingAll() {
        const out = document.getElementById("ping-out");
        out.innerHTML = "<div class='small dim'>discovering adapters…</div>";
        try {
          const disc = await API.get("/node/toolkit/discover");
          const adapters = Object.keys(disc.adapters || {});
          if (!adapters.length) {
            out.innerHTML = "";
            out.appendChild(el("div", { class: "empty" }, "No adapters reported."));
            return;
          }
          out.innerHTML = "";
          const tbl = el("table", null, [
            el("thead", null, el("tr", null, ["adapter", "ok", "reachable", "details"].map(h => el("th", { text: h })))),
          ]);
          const tb = el("tbody");
          tbl.appendChild(tb);
          out.appendChild(tbl);

          for (const a of adapters) {
            const row = el("tr", null, [
              el("td", { class: "mono", text: a }),
              el("td", null, [el("span", { class: "spinner" })]),
              el("td", { text: "—" }),
              el("td", { class: "small mono" }, "…"),
            ]);
            tb.appendChild(row);
            try {
              const r = await API.get("/node/toolkit/ping/" + encodeURIComponent(a));
              row.children[1].innerHTML = "";
              row.children[1].appendChild(r.ok ? badge("ok", "yes") : badge("err", "no"));
              row.children[2].textContent = r.reachable === true ? "yes" : (r.reachable === false ? "no" : "—");
              row.children[3].textContent = JSON.stringify(r);
            } catch (e) {
              row.children[1].innerHTML = "";
              row.children[1].appendChild(badge("err", "err"));
              row.children[3].textContent = String(e.message || e);
            }
          }
        } catch (e) { UI.err(e); out.innerHTML = ""; }
      }

      async function loadStrict() {
        const lbl = document.getElementById("strict-state");
        const tog = document.getElementById("strict-tog");
        if (!lbl || !tog) return;
        try {
          const res = await API.get("/node/config/env?prefix=AGCL_STRICT");
          const env = res.env || {};
          const v = (env.AGCL_STRICT || "").toString().toLowerCase();
          const on = ["1", "true", "yes", "on"].includes(v);
          tog.checked = on;
          lbl.textContent = "AGCL_STRICT=" + (env.AGCL_STRICT ?? "(unset)");
        } catch (e) { lbl.textContent = "(could not read)"; }
      }

      // bind toggle once after layout exists
      setTimeout(() => {
        const tog = document.getElementById("strict-tog");
        tog?.addEventListener("change", async () => {
          const desired = tog.checked ? "1" : "0";
          try {
            await API.patch("/node/config/env", {
              updates: { AGCL_STRICT: desired },
              persist: true,
              allowlist_only: false,
            });
            UI.ok("Strict mode " + (tog.checked ? "enabled" : "disabled"));
            loadStrict();
          } catch (e) { UI.err(e); }
        });
      }, 0);

      async function smoke() {
        const out = document.getElementById("smoke-out");
        const prompt = document.getElementById("smoke-prompt").value.trim();
        if (!prompt) return;
        out.textContent = "calling…";
        try {
          const res = await API.post("/node/toolkit/chat", {
            messages: [{ role: "user", content: prompt }],
            max_tokens: 64,
            temperature: 0.5,
            stream: false,
          });
          const reply = res.choices?.[0]?.message?.content || res.content || JSON.stringify(res, null, 2);
          out.textContent = reply;
          UI.ok("Smoke test passed.");
        } catch (e) { out.textContent = ""; UI.err(e); }
      }

      async function exportBundle() {
        try {
          const res = await API.get("/node/config/all");
          const blob = new Blob([JSON.stringify(res, null, 2)], { type: "application/json" });
          const a = document.createElement("a");
          a.href = URL.createObjectURL(blob);
          a.download = "agcl-config.json";
          a.click();
          UI.ok("Bundle exported.");
        } catch (e) { UI.err(e); }
      }

      async function clearAll() {
        if (!confirm("Flush every chat session and rebuild the MAS singleton?")) return;
        try {
          const sess = await API.get("/node/sessions");
          for (const sid of (sess.sessions || [])) {
            await API.del("/node/sessions/" + encodeURIComponent(sid)).catch(() => {});
          }
          await API.post("/node/mas/rebuild", {});
          UI.ok("Cleared.");
        } catch (e) { UI.err(e); }
      }

      async function verifyRemote() {
        const url = document.getElementById("rt-url").value.trim().replace(/\/+$/, "");
        const out = document.getElementById("rt-out");
        if (!url) return;
        out.textContent = "probing…";
        try {
          const res = await fetch(url + "/node/health", {
            method: "GET",
            headers: { "Authorization": "Bearer " + API.key },
          });
          const txt = await res.text();
          out.textContent = `HTTP ${res.status} ${res.statusText}\n${txt}`;
          if (res.ok) UI.ok("Remote node is reachable.");
        } catch (e) { out.textContent = "ERROR: " + (e.message || e); UI.err(e); }
      }

      function badge(kind, txt) { return el("span", { class: "pill " + kind }, txt); }

      loadStrict();
      return view;
    },
  };
})();
