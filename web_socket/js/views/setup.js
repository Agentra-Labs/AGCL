/* Setup — auto-setup wizards + HF / GGUF model downloaders.
 *
 * Drives /node/setup/* endpoints. Long-running tasks (autoconfig with
 * --download, HF / GGUF downloads) become server-side jobs streamed
 * over SSE; quick edits (chat-quickstart) are synchronous. */

(function () {
  const V = window.Views = window.Views || {};

  V.setup = {
    mount(root) {
      const { el, fmtNum, loadingNode } = window.H;
      const activeStreams = new Map();   // jobId -> AbortController

      const view = {
        unmount() {
          for (const ctrl of activeStreams.values()) {
            try { ctrl.abort(); } catch {}
          }
          activeStreams.clear();
        },
        async refresh() { await loadAll(); },
      };

      root.appendChild(el("p", { class: "small" },
        "Set up the project from the browser: run autoconfig, fix the chat defaults, download HuggingFace snapshots and single GGUF files. Same operations are available from the CLI as ",
        el("code", null, "agcl autoconfig"), ", ",
        el("code", null, "agcl download hf|gguf"), ", ",
        el("code", null, "agcl setup-chat"), "."));

      // --- AUTOCONFIG ---------------------------------------------------
      root.appendChild(el("section", { class: "block" }, [
        el("h2", null, "Auto-setup recursive MAS"),
        el("div", { class: "card" }, [
          el("p", { class: "small dim", style: "margin-top:0" },
            "Writes the canonical mas.json (Qwen2.5-0.5B + TinyLlama-1.1B), patches .env with the MAS keys, creates models/hf/, and runs the 21-test self-check. Idempotent."),
          el("div", { class: "row" }, [
            toggle("ac-force", "Force overwrite mas.json"),
            toggle("ac-deps", "Install transformers if missing"),
            toggle("ac-dl", "Download both HF models (~3 GB)"),
          ]),
          el("div", { class: "row", style: "margin-top:8px" }, [
            el("button", { onClick: runAutoconfig }, "Run autoconfig"),
            el("span", { class: "small dim", id: "ac-hint" },
              "Without download, finishes in ~1s. With download, expect a few minutes."),
          ]),
        ]),
      ]));

      // --- CHAT QUICKSTART ---------------------------------------------
      root.appendChild(el("section", { class: "block" }, [
        el("h2", null, "Auto-setup chat"),
        el("div", { class: "card" }, [
          el("p", { class: "small dim", style: "margin-top:0" },
            "Patches .env with the chat-section defaults. Leave a field blank to skip it. Synchronous; no job."),
          el("div", { class: "split-2" }, [
            el("div", null, [
              el("div", { class: "field" }, [
                el("label", null, "Local model path (.gguf)"),
                el("input", { type: "text", id: "ch-lm", placeholder: "models/SmolLM2-135M.Q2_K.gguf" }),
              ]),
              el("div", { class: "field" }, [
                el("label", null, "Default cloud"),
                el("select", { id: "ch-cloud" }, [
                  el("option", { value: "" }, "(skip)"),
                  el("option", { value: "claude" }, "claude"),
                  el("option", { value: "openai" }, "openai"),
                ]),
              ]),
            ]),
            el("div", null, [
              el("div", { class: "field" }, [
                el("label", null, "Prefix word count (1-32)"),
                el("input", { type: "number", id: "ch-pwc", min: 1, max: 32, placeholder: "blank = skip" }),
              ]),
              el("div", { class: "field" }, [
                el("label", null, "Prompt format"),
                el("select", { id: "ch-pf" }, [
                  el("option", { value: "" }, "(skip)"),
                  el("option", { value: "chatml" }, "chatml"),
                  el("option", { value: "plain" }, "plain (Q: ... A:)"),
                ]),
              ]),
            ]),
          ]),
          el("div", { class: "row", style: "margin-top:8px" }, [
            el("button", { onClick: runChatQuickstart }, "Apply chat defaults"),
            el("span", { class: "small dim", id: "ch-out" }),
          ]),
        ]),
      ]));

      // --- HF DOWNLOADER -----------------------------------------------
      root.appendChild(el("section", { class: "block" }, [
        el("h2", null, "HuggingFace snapshot downloader"),
        el("div", { class: "card" }, [
          el("p", { class: "small dim", style: "margin-top:0" },
            "Pulls a full HF repo into models/hf_local/<slug>/. Skips TF / Flax weight files."),
          el("div", { class: "row" }, [
            el("input", { type: "text", id: "hf-repo", placeholder: "owner/name (e.g. Qwen/Qwen2.5-0.5B-Instruct)", style: "flex:1;min-width:280px" }),
            el("input", { type: "text", id: "hf-rev", placeholder: "(revision, optional)", style: "width:160px" }),
            el("input", { type: "text", id: "hf-dest", placeholder: "(dest dir, optional)", style: "width:200px" }),
            el("button", { onClick: runHFDownload }, "Download"),
          ]),
        ]),
      ]));

      // --- GGUF DOWNLOADER ---------------------------------------------
      root.appendChild(el("section", { class: "block" }, [
        el("h2", null, "GGUF file downloader"),
        el("div", { class: "card" }, [
          el("p", { class: "small dim", style: "margin-top:0" },
            "Source can be a full URL, an HF repo (",
            el("code", null, "owner/repo"),
            " plus a filename), or the combined form ",
            el("code", null, "owner/repo:filename.gguf"),
            ". Saves to models/ by default."),
          el("div", { class: "row" }, [
            el("input", { type: "text", id: "gg-source", placeholder: "TheBloke/TinyLlama-1.1B-Chat-v1.0-GGUF:tinyllama-1.1b-chat-v1.0.Q2_K.gguf",
              style: "flex:1;min-width:320px" }),
            el("input", { type: "text", id: "gg-fname", placeholder: "(filename, if not in source)", style: "width:240px" }),
            el("input", { type: "text", id: "gg-dest", placeholder: "(dest, optional)", style: "width:160px" }),
            el("button", { onClick: runGGUFDownload }, "Download"),
          ]),
        ]),
      ]));

      // --- RECOMMENDED MODELS ------------------------------------------
      root.appendChild(el("section", { class: "block" }, [
        el("h2", null, "Recommended models"),
        el("div", { id: "registry-wrap" }, loadingNode()),
      ]));

      // --- LOCAL INVENTORY ---------------------------------------------
      root.appendChild(el("section", { class: "block" }, [
        el("div", { class: "row" }, [
          el("h2", { style: "margin:0" }, "Locally cached models"),
          el("span", { class: "spacer" }),
          el("button", { class: "btn-ghost", onClick: loadInventory }, "Rescan"),
        ]),
        el("div", { id: "inv-wrap" }, loadingNode()),
      ]));

      // --- ACTIVE / RECENT JOBS ----------------------------------------
      root.appendChild(el("section", { class: "block" }, [
        el("div", { class: "row" }, [
          el("h2", { style: "margin:0" }, "Setup jobs"),
          el("span", { class: "spacer" }),
          el("button", { class: "btn-ghost", onClick: loadJobs }, "Refresh"),
        ]),
        el("div", { id: "jobs-wrap" }, loadingNode()),
      ]));

      // ----- helpers -----
      function toggle(id, label, checked) {
        return el("label", { class: "toggle" }, [
          el("input", { type: "checkbox", id, ...(checked ? { checked: "" } : {}) }),
          el("span", { class: "sw" }), label,
        ]);
      }

      // ----- actions -----
      async function runAutoconfig() {
        try {
          const res = await API.post("/node/setup/autoconfig", {
            force: document.getElementById("ac-force").checked,
            install_deps: document.getElementById("ac-deps").checked,
            download: document.getElementById("ac-dl").checked,
          });
          UI.ok("Autoconfig job started: " + res.job_id);
          followJob(res.job_id);
          loadJobs();
        } catch (e) { UI.err(e); }
      }

      async function runChatQuickstart() {
        const out = document.getElementById("ch-out");
        out.textContent = "applying…";
        const body = {};
        const lm = document.getElementById("ch-lm").value.trim();
        const cloud = document.getElementById("ch-cloud").value;
        const pwc = document.getElementById("ch-pwc").value;
        const pf  = document.getElementById("ch-pf").value;
        if (lm) body.local_model_path = lm;
        if (cloud) body.default_cloud = cloud;
        if (pwc) body.prefix_word_count = Number(pwc);
        if (pf) body.prompt_format = pf;
        try {
          const res = await API.post("/node/setup/chat-quickstart", body);
          if (!res.applied || !res.applied.length) {
            out.textContent = res.note || "no changes";
          } else {
            out.textContent = "applied: " + res.applied.join(", ")
              + (res.warnings && res.warnings.length ? "  ·  warnings: " + res.warnings.join("; ") : "");
            UI.ok("Chat defaults patched.");
          }
          if (res.warnings) res.warnings.forEach(w => UI.toast(w, "warn"));
        } catch (e) { out.textContent = ""; UI.err(e); }
      }

      async function runHFDownload(repoOverride) {
        const repo = (typeof repoOverride === "string" ? repoOverride : document.getElementById("hf-repo").value).trim();
        if (!repo || !repo.includes("/")) { UI.err(new Error("repo_id must look like owner/name")); return; }
        const body = { repo_id: repo };
        const rev = document.getElementById("hf-rev").value.trim();
        const dest = document.getElementById("hf-dest").value.trim();
        if (rev) body.revision = rev;
        if (dest) body.dest = dest;
        try {
          const res = await API.post("/node/setup/download/hf", body);
          UI.ok("HF download started: " + repo);
          followJob(res.job_id);
          loadJobs();
        } catch (e) { UI.err(e); }
      }

      async function runGGUFDownload(prefilled) {
        let source, filename;
        if (prefilled && typeof prefilled === "object") {
          source = prefilled.source;
          filename = prefilled.filename;
        } else {
          source = document.getElementById("gg-source").value.trim();
          filename = document.getElementById("gg-fname").value.trim();
        }
        if (!source) { UI.err(new Error("source is required")); return; }
        const body = { source };
        if (filename) body.filename = filename;
        const dest = document.getElementById("gg-dest").value.trim();
        if (dest) body.dest = dest;
        try {
          const res = await API.post("/node/setup/download/gguf", body);
          UI.ok("GGUF download started: " + source);
          followJob(res.job_id);
          loadJobs();
        } catch (e) { UI.err(e); }
      }

      // ----- jobs -----
      async function loadJobs() {
        const wrap = document.getElementById("jobs-wrap");
        wrap.innerHTML = "";
        try {
          const res = await API.get("/node/setup/jobs?limit=20");
          const jobs = res.jobs || [];
          if (!jobs.length) {
            wrap.appendChild(el("div", { class: "empty" }, "No setup jobs yet."));
            return;
          }
          jobs.forEach(j => wrap.appendChild(renderJobCard(j)));
        } catch (e) { UI.err(e); }
      }

      function renderJobCard(j) {
        const card = el("div", { class: "card", id: "job-" + j.id });
        const stateClass = ({
          running: "warn", done: "ok", error: "err", cancelled: "dim", pending: "dim",
        })[j.state] || "dim";
        const head = el("div", { class: "row", style: "align-items:center;gap:10px" }, [
          el("span", { class: "pill " + stateClass }, j.state),
          el("span", { class: "mono small" }, j.kind + " · " + j.id),
          el("span", { class: "spacer" }),
          el("span", { class: "small dim", id: "job-prog-" + j.id }, (j.progress || 0).toFixed(0) + "%"),
          el("button", { class: "btn-ghost", onClick: () => followJob(j.id) }, "Stream"),
          j.state === "running"
            ? el("button", { class: "btn-warn", onClick: () => cancelJob(j.id) }, "Cancel")
            : null,
        ]);
        card.appendChild(head);
        const bar = el("div", { style: "border:1px solid var(--line);height:6px;margin:6px 0;position:relative" });
        bar.appendChild(el("div", { id: "job-bar-" + j.id, style: `background:var(--fg);height:100%;width:${j.progress || 0}%` }));
        card.appendChild(bar);
        const log = el("pre", { id: "job-log-" + j.id, class: "small mono",
          style: "background:var(--bg);border:1px solid var(--line);padding:6px 8px;max-height:160px;overflow:auto;white-space:pre-wrap;margin:0" });
        card.appendChild(log);
        if (j.result) {
          card.appendChild(el("pre", { class: "small mono dim",
            style: "background:var(--bg);padding:6px 8px;margin:6px 0 0 0;max-height:140px;overflow:auto;white-space:pre-wrap" },
            JSON.stringify(j.result, null, 2)));
        }
        return card;
      }

      function followJob(jid) {
        if (activeStreams.has(jid)) return;
        const ctrl = API.sse(`/node/setup/jobs/${encodeURIComponent(jid)}/stream`, undefined, {
          onEvent(ev) {
            // Note: the stream endpoint is GET (SSE) — but our API.sse uses POST.
            // We work around that with fetch+ReadableStream below if needed.
          },
          onError(e) { console.warn(e); },
          onDone() { activeStreams.delete(jid); loadJobs(); },
        });
        // API.sse posts; fall back to native EventSource for GET.
        try { ctrl.abort(); } catch {}
        followJobViaEventSource(jid);
      }

      function followJobViaEventSource(jid) {
        // EventSource doesn't support custom headers, so we use fetch + manual SSE parsing.
        const ctrl = new AbortController();
        activeStreams.set(jid, ctrl);
        const url = API.url(`/node/setup/jobs/${encodeURIComponent(jid)}/stream`);
        fetch(url, {
          method: "GET",
          headers: { "Authorization": "Bearer " + API.key, "Accept": "text/event-stream" },
          signal: ctrl.signal,
        }).then(async (res) => {
          if (!res.ok || !res.body) {
            const t = await res.text().catch(() => "");
            throw new Error("stream failed " + res.status + ": " + t);
          }
          const reader = res.body.getReader();
          const dec = new TextDecoder();
          let buf = "";
          while (true) {
            const { value, done } = await reader.read();
            if (done) break;
            buf += dec.decode(value, { stream: true });
            let idx;
            while ((idx = buf.indexOf("\n\n")) !== -1) {
              const block = buf.slice(0, idx); buf = buf.slice(idx + 2);
              const data = block.split("\n").filter(l => l.startsWith("data:"))
                .map(l => l.slice(5).trim()).join("\n");
              if (!data || data === "[DONE]") continue;
              try { onJobEvent(jid, JSON.parse(data)); }
              catch (e) { /* ignore */ }
            }
          }
        }).catch(e => {
          if (e.name !== "AbortError") console.warn("stream err:", e);
        }).finally(() => {
          activeStreams.delete(jid);
          loadJobs();
        });
      }

      function onJobEvent(jid, ev) {
        const log = document.getElementById("job-log-" + jid);
        const prog = document.getElementById("job-prog-" + jid);
        const bar = document.getElementById("job-bar-" + jid);
        if (ev.event === "log") {
          if (log) {
            const ts = ev.ts ? new Date(ev.ts * 1000).toLocaleTimeString() : "";
            log.textContent += `[${ts}] ${ev.level || "info"}: ${ev.message || ""}\n`;
            log.scrollTop = log.scrollHeight;
          }
          if (ev.progress != null && bar && prog) {
            bar.style.width = ev.progress + "%";
            prog.textContent = ev.progress.toFixed(0) + "%";
          }
        } else if (ev.event === "snapshot") {
          if (prog) prog.textContent = (ev.progress || 0).toFixed(0) + "%";
          if (bar) bar.style.width = (ev.progress || 0) + "%";
        } else if (ev.event === "final") {
          if (prog) prog.textContent = (ev.progress || 100).toFixed(0) + "%";
          if (bar) bar.style.width = (ev.progress || 100) + "%";
          if (ev.state === "done") UI.ok("Job " + jid + " done.");
          if (ev.state === "error") UI.toast("Job " + jid + " errored: " + (ev.error || ""), "err");
          if (ev.state === "cancelled") UI.toast("Job " + jid + " cancelled.");
        }
      }

      async function cancelJob(jid) {
        try {
          await API.post(`/node/setup/jobs/${encodeURIComponent(jid)}/cancel`, {});
          UI.toast("Cancel requested.");
          loadJobs();
        } catch (e) { UI.err(e); }
      }

      // ----- registry + inventory -----
      async function loadRegistry() {
        const wrap = document.getElementById("registry-wrap");
        wrap.innerHTML = "";
        try {
          const reg = await API.get("/node/setup/registry");
          const ggufBox = el("div", { class: "card" }, [el("h3", null, "Recommended GGUF (local prefix / chat)")]);
          (reg.local_chat_gguf || []).forEach(m => {
            const row = el("div", { class: "row", style: "padding:4px 0;border-bottom:1px dashed var(--line)" }, [
              el("div", { style: "flex:1;min-width:0" }, [
                el("div", null, m.label),
                el("div", { class: "small dim mono", title: m.source + ":" + m.filename },
                  m.source + ":" + m.filename),
              ]),
              el("span", { class: "pill dim" }, m.size_human || "?"),
              el("button", { class: "btn-ghost",
                onClick: () => runGGUFDownload({ source: m.source + ":" + m.filename }) }, "Download"),
            ]);
            ggufBox.appendChild(row);
          });
          wrap.appendChild(ggufBox);

          const hfBox = el("div", { class: "card", style: "margin-top:14px" }, [el("h3", null, "Recommended HF (Recursive MAS agents)")]);
          (reg.mas_hf || []).forEach(m => {
            const row = el("div", { class: "row", style: "padding:4px 0;border-bottom:1px dashed var(--line)" }, [
              el("div", { style: "flex:1;min-width:0" }, [
                el("div", null, m.label),
                el("div", { class: "small dim mono" }, m.repo_id),
              ]),
              el("span", { class: "pill dim" }, m.size_human || "?"),
              el("button", { class: "btn-ghost",
                onClick: () => runHFDownload(m.repo_id) }, "Download"),
            ]);
            hfBox.appendChild(row);
          });
          wrap.appendChild(hfBox);

          if (reg.canonical_mas_pair) {
            const c = reg.canonical_mas_pair;
            wrap.appendChild(el("div", { class: "small dim", style: "margin-top:10px" },
              `Canonical MAS pair: ${c.planner} + ${c.solver} (${c.total_size_human}). One-click via "Run autoconfig" with download enabled.`));
          }
        } catch (e) { UI.err(e); }
      }

      async function loadInventory() {
        const wrap = document.getElementById("inv-wrap");
        wrap.innerHTML = loadingNode().outerHTML || "";
        try {
          const inv = await API.get("/node/setup/local-models");
          wrap.innerHTML = "";
          const split = el("div", { class: "split-2" }, [
            ggufTable(inv.gguf || []),
            hfTable(inv.hf || []),
          ]);
          wrap.appendChild(split);
          wrap.appendChild(el("div", { class: "small dim", style: "margin-top:6px" },
            "Scanned: " + (inv.scanned || []).join(", ")));
        } catch (e) { UI.err(e); }
      }

      function ggufTable(items) {
        const card = el("div", { class: "card" }, [el("h3", null, "GGUF (local prefix)")]);
        if (!items.length) {
          card.appendChild(el("div", { class: "empty" }, "No GGUF files found in models/."));
          return card;
        }
        const tbl = el("table", null, [
          el("thead", null, el("tr", null, ["size", "path", ""].map(h => el("th", { text: h })))),
        ]);
        const tb = el("tbody");
        items.forEach(m => {
          tb.appendChild(el("tr", null, [
            el("td", { class: "right small mono", text: m.size_human || "?" }),
            el("td", { class: "small mono clip", title: m.path }, m.rel || m.path),
            el("td", null, [el("button", { class: "btn-ghost",
              onClick: () => useAsLocalModel(m.path) }, "Use as LOCAL_MODEL_PATH")]),
          ]));
        });
        tbl.appendChild(tb);
        card.appendChild(tbl);
        return card;
      }

      function hfTable(items) {
        const card = el("div", { class: "card" }, [el("h3", null, "HF snapshots")]);
        if (!items.length) {
          card.appendChild(el("div", { class: "empty" }, "No HF snapshots found."));
          return card;
        }
        const tbl = el("table", null, [
          el("thead", null, el("tr", null, ["size", "name", "source"].map(h => el("th", { text: h })))),
        ]);
        const tb = el("tbody");
        items.forEach(m => {
          tb.appendChild(el("tr", null, [
            el("td", { class: "right small mono", text: m.size_human || "?" }),
            el("td", { class: "small mono clip", title: m.path }, m.name),
            el("td", { class: "small dim clip", title: m.source }, m.source.replace(/^.*\//, "")),
          ]));
        });
        tbl.appendChild(tb);
        card.appendChild(tbl);
        return card;
      }

      async function useAsLocalModel(path) {
        try {
          const res = await API.post("/node/setup/chat-quickstart", { local_model_path: path });
          UI.ok("LOCAL_MODEL_PATH set to " + path);
          (res.warnings || []).forEach(w => UI.toast(w, "warn"));
        } catch (e) { UI.err(e); }
      }

      async function loadAll() {
        await Promise.all([loadRegistry(), loadInventory(), loadJobs()]);
      }

      loadAll();
      return view;
    },
  };
})();
