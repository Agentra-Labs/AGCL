/* Chat — streaming chat against /node/chat/{session_id}.
 *
 * Picks/creates a session, lists sessions, sends user message, renders
 * SSE events (prefix → chunk → done). Provider override + recovery_mode
 * exposed as toggles. */

(function () {
  const V = window.Views = window.Views || {};
  const { el, fmtTs, loadingNode } = window.H;

  V.chat = {
    mount(root) {
      let currentSid = localStorage.getItem("agcl.chat.sid") || "default";
      let activeStream = null;

      const view = {
        unmount() { if (activeStream) { try { activeStream.abort(); } catch {} } },
        async refresh() { await loadSessions(); await loadHistory(); },
      };

      const layout = el("div", { class: "split-2" }, [
        // LEFT — sessions
        el("div", { class: "col" }, [
          el("section", { class: "block" }, [
            el("h2", null, "Sessions"),
            el("div", { class: "row", style: "margin-bottom:8px" }, [
              el("input", { type: "text", id: "new-sid", placeholder: "new session id", style: "flex:1" }),
              el("button", { onClick: () => createSession() }, "Open"),
            ]),
            el("div", { id: "sess-list" }, loadingNode()),
          ]),
        ]),
        // RIGHT — chat
        el("div", { class: "col" }, [
          el("section", { class: "block" }, [
            el("h2", null, [
              "Chat — ",
              el("span", { id: "chat-title", class: "mono" }, currentSid),
            ]),
            el("div", { class: "row", style: "margin-bottom:8px" }, [
              el("label", { class: "small" }, "Provider"),
              el("select", { id: "chat-provider" }, [
                el("option", { value: "" }, "(default)"),
                el("option", { value: "openai" }, "openai"),
                el("option", { value: "claude" }, "claude"),
              ]),
              el("label", { class: "small" }, "Recovery"),
              el("select", { id: "chat-recovery" }, [
                el("option", { value: "" }, "(none)"),
                el("option", { value: "retry" }, "retry"),
                el("option", { value: "fallback" }, "fallback"),
              ]),
              el("span", { class: "spacer" }),
              el("button", { class: "btn-ghost", onClick: () => clearLog() }, "Clear log"),
              el("button", { class: "btn-err", onClick: () => deleteSession() }, "Delete session"),
            ]),
            el("div", { class: "chat-wrap" }, [
              el("div", { class: "chat-log", id: "chat-log" }),
              el("div", { class: "chat-input" }, [
                el("textarea", { id: "chat-input", placeholder: "Type a message. Cmd/Ctrl+Enter to send.", onKeydown: onKey }),
                el("button", { id: "chat-send", onClick: send }, "Send"),
                el("button", { class: "btn-warn", id: "chat-cancel", onClick: cancel, hidden: true }, "Cancel"),
              ]),
            ]),
          ]),
        ]),
      ]);
      root.appendChild(layout);

      function onKey(e) {
        if ((e.metaKey || e.ctrlKey) && e.key === "Enter") { e.preventDefault(); send(); }
      }

      async function loadSessions() {
        const list = document.getElementById("sess-list");
        list.innerHTML = "";
        try {
          const res = await API.get("/node/sessions");
          const sids = res.sessions || [];
          if (!sids.length) {
            list.appendChild(el("div", { class: "empty" }, "No sessions yet."));
            return;
          }
          const tbl = el("table");
          const tb = el("tbody");
          sids.forEach(sid => {
            const row = el("tr", null, [
              el("td", null, [
                el("a", { onClick: () => switchSession(sid) }, sid),
                sid === currentSid ? el("span", { class: "pill ok", style: "margin-left:8px" }, "active") : null,
              ]),
            ]);
            tb.appendChild(row);
          });
          tbl.appendChild(tb);
          list.appendChild(tbl);
        } catch (e) { UI.err(e); }
      }

      async function loadHistory() {
        const log = document.getElementById("chat-log");
        log.innerHTML = "";
        try {
          const data = await API.get("/node/sessions/" + encodeURIComponent(currentSid));
          (data.messages || []).forEach(m => {
            appendMsg(m.role || "?", m.content || m.text || "", m.role === "assistant" ? "cloud" : "user");
          });
          log.scrollTop = log.scrollHeight;
        } catch (e) {
          // Session may not exist yet — that's fine.
        }
      }

      function appendMsg(who, text, kind) {
        const log = document.getElementById("chat-log");
        const m = el("div", { class: "chat-msg " + (kind || "user") }, [
          el("div", { class: "who" }, who),
          el("div", { class: "body", text: text }),
        ]);
        log.appendChild(m);
        log.scrollTop = log.scrollHeight;
        return m;
      }

      function clearLog() { document.getElementById("chat-log").innerHTML = ""; }

      async function createSession() {
        const inp = document.getElementById("new-sid");
        const sid = (inp.value || "").trim();
        if (!sid) return;
        switchSession(sid);
        inp.value = "";
        await loadSessions();
      }

      function switchSession(sid) {
        currentSid = sid;
        localStorage.setItem("agcl.chat.sid", sid);
        document.getElementById("chat-title").textContent = sid;
        loadSessions();
        loadHistory();
      }

      async function deleteSession() {
        if (!confirm("Flush session " + currentSid + "?")) return;
        try {
          await API.del("/node/sessions/" + encodeURIComponent(currentSid));
          UI.ok("Session flushed.");
          clearLog();
          loadSessions();
        } catch (e) { UI.err(e); }
      }

      function cancel() {
        if (activeStream) { try { activeStream.abort(); } catch {} }
        document.getElementById("chat-cancel").hidden = true;
        document.getElementById("chat-send").disabled = false;
      }

      async function send() {
        const ta = document.getElementById("chat-input");
        const msg = ta.value.trim();
        if (!msg) return;
        ta.value = "";
        appendMsg("you", msg, "user");
        const provider = document.getElementById("chat-provider").value || undefined;
        const recovery = document.getElementById("chat-recovery").value || undefined;

        const localBubble = appendMsg("local prefix", "", "local");
        const cloudBubble = appendMsg("assistant", "", "cloud");
        const localBody = localBubble.querySelector(".body");
        const cloudBody = cloudBubble.querySelector(".body");

        document.getElementById("chat-send").disabled = true;
        document.getElementById("chat-cancel").hidden = false;

        const body = { message: msg };
        if (provider) body.provider = provider;
        if (recovery) body.recovery_mode = recovery;

        activeStream = API.sse(
          "/node/chat/" + encodeURIComponent(currentSid),
          body,
          {
            onEvent(ev) {
              if (ev.type === "prefix") {
                localBody.textContent = ev.text || "";
              } else if (ev.type === "chunk") {
                cloudBody.textContent += ev.text || "";
              } else if (ev.type === "done") {
                if (ev.pressure) UI.toast("Pressure: " + JSON.stringify(ev.pressure));
              }
            },
            onError(e) { UI.err(e); cancel(); },
            onDone() {
              const log = document.getElementById("chat-log");
              log.scrollTop = log.scrollHeight;
              cancel();
            },
          }
        );
      }

      loadSessions();
      loadHistory();
      return view;
    },
  };
})();
