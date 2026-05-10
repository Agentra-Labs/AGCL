/* Recursive MAS — list sessions, run a turn (one-shot or streaming),
 * pause/resume/halt, latent inspection (rendered as a heatmap). */

(function () {
  const V = window.Views = window.Views || {};

  V.mas = {
    mount(root) {
      const { el, fmtNum, loadingNode } = window.H;
      let stream = null;
      let selectedSid = localStorage.getItem("agcl.mas.sid") || "";
      let mdEnabled = (localStorage.getItem("agcl.mas.md") ?? "1") === "1";
      const lossHistory = [];   // step -> {step, loss, stage} from streaming events

      const view = {
        unmount() { if (stream) { try { stream.abort(); } catch {} } },
        async refresh() { await loadSessions(); },
      };

      const layout = el("div", null, [
        el("section", { class: "block" }, [
          el("h2", null, "Run a MAS turn"),
          el("div", { class: "card" }, [
            el("div", { class: "split-2" }, [
              el("div", null, [
                el("div", { class: "field" }, [
                  el("label", null, "Session id (blank = auto)"),
                  el("input", { type: "text", id: "mas-sid", value: selectedSid }),
                ]),
                el("div", { class: "field" }, [
                  el("label", null, "User message (Markdown supported)"),
                  el("textarea", { id: "mas-msg", placeholder: "What should the agent reason about?" }),
                ]),
                el("div", { class: "row" }, [
                  toggle("mas-stream", "Stream events", true),
                  toggle("mas-force-cloud", "Force cloud only", false),
                  toggle("mas-force-continue", "Local prefix + cloud", false),
                  toggle("mas-persist", "Persist topic", true),
                  toggle("mas-md", "Render answer as Markdown", mdEnabled),
                ]),
              ]),
              el("div", null, [
                numField("mas-switch", "Switch threshold", 0.6, 0, 1, 0.05),
                numField("mas-retrieval", "Retrieval threshold", 0.75, 0, 1, 0.05),
                numField("mas-stage1", "Stage 1 steps (latent)", 30, 0, 1000, 1),
                numField("mas-stage2", "Stage 2 steps (token CE)", 20, 0, 1000, 1),
                numField("mas-reform", "Reformulations", 6, 0, 64, 1),
                numField("mas-maxtok", "Max new tokens", 128, 1, 4096, 1),
                numField("mas-prefixtok", "Prefix tokens", 12, 1, 256, 1),
              ]),
            ]),
            el("div", { class: "row", style: "margin-top:10px" }, [
              el("button", { id: "btn-run", onClick: () => runTurn() }, "Run turn"),
              el("button", { class: "btn-warn", id: "btn-cancel", onClick: cancel, hidden: true }, "Cancel"),
              el("span", { class: "spacer" }),
              el("span", { class: "small dim", id: "mas-status" }, ""),
            ]),
          ]),
        ]),

        el("section", { class: "block" }, [
          el("h2", null, "Live answer"),
          el("div", { class: "card" }, [
            el("div", { id: "mas-answer", class: "md", style: "min-height:60px" }),
            el("div", { class: "row", style: "margin-top:8px" }, [
              el("button", { class: "btn-ghost", style: "padding:2px 8px;font-size:11px",
                onClick: copyAnswer }, "Copy answer"),
              el("span", { class: "small dim", id: "mas-meta" }, ""),
            ]),
          ]),
          el("div", { class: "split-2", style: "margin-top:14px" }, [
            el("div", null, [
              el("h2", null, "Stream events"),
              el("div", { id: "mas-events", class: "card", style: "max-height:280px;overflow:auto" }),
            ]),
            el("div", null, [
              el("h2", null, "Training loss"),
              el("div", { id: "mas-loss", class: "card" }, el("div", { class: "small dim" }, "loss curve will appear once streaming starts")),
            ]),
          ]),
        ]),

        el("section", { class: "block" }, [
          el("div", { class: "row" }, [
            el("h2", { style: "margin:0" }, "Active sessions"),
            el("span", { class: "spacer" }),
            el("button", { class: "btn-warn", onClick: rebuild }, "Rebuild MAS singleton"),
          ]),
          el("div", { id: "mas-list" }, loadingNode()),
        ]),

        el("section", { class: "block" }, [
          el("h2", null, "Selected session controls"),
          el("div", { class: "card" }, [
            el("div", { class: "row" }, [
              el("span", { class: "small" }, "Session: "),
              el("span", { class: "mono", id: "sel-sid" }, "(none)"),
              el("span", { class: "spacer" }),
              el("button", { class: "btn-ghost", onClick: ctlStatus }, "Status"),
              el("button", { class: "btn-warn", onClick: () => ctl("pause") }, [svg("i-pause"), "Pause"]),
              el("button", { class: "btn-ok", onClick: () => ctl("resume") }, [svg("i-play"), "Resume"]),
              el("button", { class: "btn-err", onClick: () => ctl("halt") }, [svg("i-stop"), "Halt"]),
              el("button", { class: "btn-ghost", onClick: ctlLatent }, "Latent snapshot"),
            ]),
            el("div", { id: "ctl-out", class: "small", style: "margin-top:8px" }),
            el("div", { id: "ctl-latent", style: "margin-top:8px" }),
          ]),
        ]),
      ]);
      root.appendChild(layout);

      document.getElementById("mas-md").addEventListener("change", e => {
        mdEnabled = e.target.checked;
        localStorage.setItem("agcl.mas.md", mdEnabled ? "1" : "0");
        renderAnswer(currentAnswer);
      });

      let currentAnswer = "";

      function svg(id) {
        const s = document.createElementNS("http://www.w3.org/2000/svg", "svg");
        s.classList.add("ico");
        const u = document.createElementNS("http://www.w3.org/2000/svg", "use");
        u.setAttribute("href", "#" + id);
        s.appendChild(u);
        return s;
      }

      function toggle(id, label, checked) {
        return el("label", { class: "toggle" }, [
          el("input", { type: "checkbox", id, ...(checked ? { checked: "" } : {}) }),
          el("span", { class: "sw" }), label,
        ]);
      }
      function numField(id, label, value, min, max, step) {
        return el("div", { class: "field" }, [
          el("label", null, label),
          el("input", { type: "number", id, value, min, max, step }),
        ]);
      }

      function getBool(id) { return document.getElementById(id).checked; }
      function getNum(id) { return Number(document.getElementById(id).value); }
      function getStr(id) { return document.getElementById(id).value.trim(); }

      function buildBody() {
        const msg = getStr("mas-msg");
        if (!msg) { UI.err(new Error("Message required.")); return null; }
        const body = {
          message: msg,
          force_cloud: getBool("mas-force-cloud"),
          force_continue: getBool("mas-force-continue"),
          persist: getBool("mas-persist"),
          switch_threshold: getNum("mas-switch"),
          retrieval_threshold: getNum("mas-retrieval"),
          stage1_steps: getNum("mas-stage1"),
          stage2_steps: getNum("mas-stage2"),
          n_reformulations: getNum("mas-reform"),
          max_new_tokens: getNum("mas-maxtok"),
          prefix_tokens: getNum("mas-prefixtok"),
        };
        const sid = getStr("mas-sid");
        if (sid) body.session_id = sid;
        return body;
      }

      function setStatus(t) { document.getElementById("mas-status").textContent = t; }

      function renderAnswer(text) {
        currentAnswer = text || "";
        const wrap = document.getElementById("mas-answer");
        if (!currentAnswer) { wrap.innerHTML = ""; return; }
        if (mdEnabled) {
          wrap.classList.add("md");
          wrap.innerHTML = window.MD.render(currentAnswer);
        } else {
          wrap.classList.remove("md");
          wrap.style.whiteSpace = "pre-wrap";
          wrap.textContent = currentAnswer;
        }
      }

      function copyAnswer() {
        if (!currentAnswer) return;
        navigator.clipboard.writeText(currentAnswer).then(
          () => UI.ok("Copied."),
          () => UI.err(new Error("Copy failed"))
        );
      }

      function renderLoss() {
        const wrap = document.getElementById("mas-loss");
        wrap.innerHTML = "";
        if (!lossHistory.length) {
          wrap.appendChild(el("div", { class: "small dim" }, "no training events yet"));
          return;
        }
        const data = lossHistory.map(p => p.loss);
        wrap.appendChild(window.Chart.line(data, { height: 180, label: "loss (per training step)" }));
        const last = lossHistory[lossHistory.length - 1];
        wrap.appendChild(el("div", { class: "small dim", style: "margin-top:6px" },
          `${lossHistory.length} steps · last: stage=${last.stage ?? "?"} loss=${Number(last.loss).toFixed(4)}`));
      }

      async function runTurn() {
        const body = buildBody();
        if (!body) return;
        renderAnswer("");
        document.getElementById("mas-meta").textContent = "";
        document.getElementById("mas-events").innerHTML = "";
        document.getElementById("btn-run").disabled = true;
        lossHistory.length = 0;
        renderLoss();
        setStatus("running…");

        if (getBool("mas-stream")) {
          document.getElementById("btn-cancel").hidden = false;
          stream = API.sse("/node/mas/stream", body, {
            onEvent(ev) { renderEvent(ev); },
            onError(e) { UI.err(e); cancel(); },
            onDone() { cancel(); loadSessions(); },
          });
        } else {
          try {
            const res = await API.post("/node/mas/run", body);
            renderAnswer(res.answer || "");
            document.getElementById("mas-meta").textContent =
              `topic=${res.topic_id || "—"} · session=${res.session_id || "—"}`;
            renderEvent({ event: "done" });
            UI.ok("Turn complete.");
          } catch (e) { UI.err(e); }
          finally {
            document.getElementById("btn-run").disabled = false;
            setStatus("");
            loadSessions();
          }
        }
      }

      function cancel() {
        if (stream) { try { stream.abort(); } catch {} stream = null; }
        document.getElementById("btn-cancel").hidden = true;
        document.getElementById("btn-run").disabled = false;
        setStatus("");
      }

      function renderEvent(ev) {
        const evs = document.getElementById("mas-events");
        const kind = ev.event || "evt";
        const summary = (() => {
          if (kind === "answer") return (ev.text || "").slice(0, 200);
          if (kind === "training_step") {
            return `stage=${ev.stage ?? "?"} step=${ev.step ?? "?"} loss=${ev.loss != null ? Number(ev.loss).toFixed(4) : "—"}`;
          }
          if (kind === "session_ready") return "session_id=" + ev.session_id;
          if (kind === "halted_done") return ev.message || "halted";
          if (kind === "error") return (ev.type || "") + " — " + (ev.message || "");
          if (kind === "done") return "stream complete";
          if (kind === "model_loading") return "loading model…";
          if (kind === "start") return "session_id=" + ev.session_id;
          return JSON.stringify(ev);
        })();
        evs.appendChild(el("div", { class: "evt " + kind }, [
          el("span", { class: "lbl" }, kind),
          summary,
        ]));
        evs.scrollTop = evs.scrollHeight;

        if (kind === "training_step" && ev.loss != null) {
          lossHistory.push({ step: ev.step, loss: Number(ev.loss), stage: ev.stage });
          if (lossHistory.length % 1 === 0) renderLoss();
        }
        if (kind === "answer" && ev.text) {
          renderAnswer(ev.text);
          if (ev.topic_id || ev.session_id) {
            document.getElementById("mas-meta").textContent =
              `topic=${ev.topic_id || "—"} · session=${ev.session_id || "—"}`;
          }
        }
        if (kind === "session_ready" && ev.session_id) {
          selectedSid = ev.session_id;
          localStorage.setItem("agcl.mas.sid", ev.session_id);
          document.getElementById("sel-sid").textContent = ev.session_id;
          document.getElementById("mas-sid").value = ev.session_id;
        }
      }

      async function loadSessions() {
        const wrap = document.getElementById("mas-list");
        wrap.innerHTML = "";
        try {
          const res = await API.get("/node/mas/sessions");
          const sessions = res.sessions || [];
          if (!sessions.length) {
            wrap.appendChild(el("div", { class: "empty" }, "No active MAS sessions."));
            return;
          }
          const tbl = el("table", null, [
            el("thead", null, el("tr", null, ["", "session id", "topic id", "seed", "trained", "turns", ""].map(h => el("th", { text: h })))),
          ]);
          const tb = el("tbody");
          sessions.forEach(s => {
            const sid = s.session_id;
            const radio = el("input", { type: "radio", name: "selsid", value: sid, onChange: () => selectSession(sid) });
            if (sid === selectedSid) radio.checked = true;
            tb.appendChild(el("tr", null, [
              el("td", null, [radio]),
              el("td", { class: "mono clip", title: sid }, sid),
              el("td", { class: "mono clip", title: s.topic_id || "" }, s.topic_id || "—"),
              el("td", { class: "clip", title: s.topic_seed || "" }, s.topic_seed || "—"),
              el("td", null, [s.trained ? el("span", { class: "pill ok" }, "yes") : el("span", { class: "pill dim" }, "no")]),
              el("td", { text: s.n_turns ?? 0 }),
              el("td", null, [el("button", { class: "btn-err", onClick: () => deleteSess(sid) }, "Delete")]),
            ]));
          });
          tbl.appendChild(tb);
          wrap.appendChild(tbl);
          if (selectedSid) document.getElementById("sel-sid").textContent = selectedSid;
        } catch (e) { UI.err(e); }
      }

      function selectSession(sid) {
        selectedSid = sid;
        localStorage.setItem("agcl.mas.sid", sid);
        document.getElementById("sel-sid").textContent = sid;
        document.getElementById("mas-sid").value = sid;
      }

      async function deleteSess(sid) {
        if (!confirm("Delete MAS session " + sid + "?")) return;
        try {
          await API.del("/node/mas/sessions/" + encodeURIComponent(sid));
          UI.ok("Deleted.");
          loadSessions();
        } catch (e) { UI.err(e); }
      }

      async function rebuild() {
        if (!confirm("Rebuild MAS singleton (drops all sessions)?")) return;
        try {
          await API.post("/node/mas/rebuild", {});
          UI.ok("Rebuilt.");
          loadSessions();
        } catch (e) { UI.err(e); }
      }

      async function ctl(op) {
        if (!selectedSid) { UI.err(new Error("Select a session first.")); return; }
        try {
          const res = await API.post(`/node/mas/sessions/${encodeURIComponent(selectedSid)}/${op}`, {});
          ctlOut(op, res);
        } catch (e) { UI.err(e); }
      }
      async function ctlStatus() {
        if (!selectedSid) { UI.err(new Error("Select a session first.")); return; }
        try {
          const res = await API.get(`/node/mas/sessions/${encodeURIComponent(selectedSid)}/status`);
          ctlOut("status", res);
        } catch (e) { UI.err(e); }
      }
      async function ctlLatent() {
        if (!selectedSid) { UI.err(new Error("Select a session first.")); return; }
        try {
          const res = await API.get(`/node/mas/sessions/${encodeURIComponent(selectedSid)}/latent`);
          const snap = res.snapshot || res || {};
          const out = document.getElementById("ctl-latent");
          out.innerHTML = "";
          out.appendChild(el("div", { class: "small dim", style: "margin-bottom:4px" },
            `shape=${JSON.stringify(snap.shape)} stage=${snap.stage ?? "?"} step=${snap.step ?? "?"}`));
          const values = snap.values || [];
          if (Array.isArray(values) && values.length) {
            const flat = Array.isArray(values[0]) ? values.flat() : values;
            const cols = Array.isArray(snap.shape) && snap.shape.length === 2 ? snap.shape[1] : 32;
            out.appendChild(window.Chart.heatmap(flat, { cols, height: 140 }));
          } else {
            out.appendChild(el("div", { class: "small dim" }, "no latent values returned"));
          }
        } catch (e) { UI.err(e); }
      }
      function ctlOut(op, res) {
        const out = document.getElementById("ctl-out");
        out.innerHTML = "";
        out.appendChild(el("div", null, [
          el("span", { class: "pill" }, op),
          " ",
          el("span", { class: "small mono" }, JSON.stringify(res)),
        ]));
      }

      loadSessions();
      return view;
    },
  };
})();
