/* Recursive MAS — list sessions, run a turn (one-shot or streaming),
 * pause/resume/halt, latent inspection. */

(function () {
  const V = window.Views = window.Views || {};
  const { el, fmtNum, loadingNode } = window.H;

  V.mas = {
    mount(root) {
      let stream = null;
      let selectedSid = localStorage.getItem("agcl.mas.sid") || "";

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
                  el("label", null, "User message"),
                  el("textarea", { id: "mas-msg", placeholder: "What should the agent reason about?" }),
                ]),
                el("div", { class: "row" }, [
                  toggle("mas-stream", "Stream events", true),
                  toggle("mas-force-cloud", "Force cloud only", false),
                  toggle("mas-force-continue", "Local prefix + cloud", false),
                  toggle("mas-persist", "Persist topic", true),
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
            el("div", { id: "mas-answer", class: "mono", style: "white-space:pre-wrap;min-height:60px" }),
          ]),
          el("h2", { style: "margin-top:14px" }, "Stream events"),
          el("div", { id: "mas-events", class: "card", style: "max-height:280px;overflow:auto" }),
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
          ]),
        ]),
      ]);
      root.appendChild(layout);

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
          el("span", { class: "sw" }),
          label,
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

      async function runTurn() {
        const body = buildBody();
        if (!body) return;
        document.getElementById("mas-answer").textContent = "";
        document.getElementById("mas-events").innerHTML = "";
        document.getElementById("btn-run").disabled = true;
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
            document.getElementById("mas-answer").textContent = res.answer || "(empty)";
            renderEvent({ event: "answer", text: res.answer, topic_id: res.topic_id, session_id: res.session_id });
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
          if (kind === "training_step")
            return `stage=${ev.stage} step=${ev.step} loss=${ev.loss != null ? Number(ev.loss).toFixed(4) : "—"}`;
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

        if (kind === "answer" && ev.text) {
          document.getElementById("mas-answer").textContent = ev.text;
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
          const snap = res.snapshot || res;
          document.getElementById("ctl-out").innerHTML = "";
          document.getElementById("ctl-out").appendChild(el("pre", { class: "mono small", style: "white-space:pre-wrap" },
            JSON.stringify(snap, null, 2)));
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
