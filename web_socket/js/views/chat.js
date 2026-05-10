/* Chat — streaming chat against /node/chat/{session_id}.
 *
 * Renders assistant turns with Markdown, keeps the local prefix as a
 * dim sidebar attached to the same logical message, exposes provider /
 * recovery toggles, and supports cancel + copy. */

(function () {
  const V = window.Views = window.Views || {};

  V.chat = {
    mount(root) {
      const { el, fmtTs, loadingNode } = window.H;
      let currentSid = localStorage.getItem("agcl.chat.sid") || "default";
      let activeStream = null;
      let mdEnabled = (localStorage.getItem("agcl.chat.md") ?? "1") === "1";

      const view = {
        unmount() { if (activeStream) { try { activeStream.abort(); } catch {} } },
        async refresh() { await loadSessions(); await loadHistory(); },
      };

      const layout = el("div", { class: "split-2" }, [
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
                el("option", { value: "natural" }, "natural"),
                el("option", { value: "humor" }, "humor"),
                el("option", { value: "explicit" }, "explicit"),
              ]),
              el("label", { class: "toggle", title: "Render assistant turns as Markdown" }, [
                el("input", { type: "checkbox", id: "chat-md", ...(mdEnabled ? { checked: "" } : {}) }),
                el("span", { class: "sw" }), "Markdown",
              ]),
              el("span", { class: "spacer" }),
              el("button", { class: "btn-ghost", onClick: () => clearLog() }, "Clear log"),
              el("button", { class: "btn-err", onClick: () => deleteSession() }, "Delete session"),
            ]),
            el("div", { class: "chat-wrap" }, [
              el("div", { class: "chat-log", id: "chat-log" }),
              el("div", { class: "chat-input" }, [
                el("textarea", { id: "chat-input", placeholder: "Type a message. Cmd/Ctrl+Enter to send. Markdown supported.", onKeydown: onKey }),
                el("button", { id: "chat-send", onClick: send }, "Send"),
                el("button", { class: "btn-warn", id: "chat-cancel", onClick: cancel, hidden: true }, "Cancel"),
              ]),
            ]),
          ]),
        ]),
      ]);
      root.appendChild(layout);

      document.getElementById("chat-md").addEventListener("change", e => {
        mdEnabled = e.target.checked;
        localStorage.setItem("agcl.chat.md", mdEnabled ? "1" : "0");
        loadHistory();
      });

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
            const role = m.role || "?";
            const txt = m.content ?? m.text ?? "";
            const kind = role === "assistant" ? "cloud" : (role === "system" ? "local" : "user");
            const bubble = appendMsg(role, "", kind);
            setBody(bubble, txt, role === "assistant");
          });
          log.scrollTop = log.scrollHeight;
        } catch (e) {
          // Session may not exist yet — that's fine.
        }
      }

      function appendMsg(who, text, kind) {
        const log = document.getElementById("chat-log");
        const m = el("div", { class: "chat-msg " + (kind || "user") }, [
          el("div", { class: "row", style: "justify-content:space-between;align-items:baseline" }, [
            el("span", { class: "who" }, who),
            el("button", { class: "btn-ghost", style: "padding:2px 6px;font-size:10px",
              onClick: (e) => copyTextOf(e.currentTarget) }, "Copy"),
          ]),
          el("div", { class: "body" }, text || ""),
        ]);
        log.appendChild(m);
        log.scrollTop = log.scrollHeight;
        return m;
      }

      function setBody(bubble, text, asAssistant) {
        const body = bubble.querySelector(".body");
        body.dataset.raw = text;
        if (asAssistant && mdEnabled) {
          body.classList.add("md");
          body.innerHTML = window.MD.render(text);
        } else {
          body.classList.remove("md");
          body.textContent = text;
        }
      }

      function appendChunk(bubble, text, asAssistant) {
        const body = bubble.querySelector(".body");
        const cur = (body.dataset.raw || "") + text;
        setBody(bubble, cur, asAssistant);
      }

      function copyTextOf(btn) {
        const bubble = btn.closest(".chat-msg");
        const raw = bubble.querySelector(".body").dataset.raw || bubble.querySelector(".body").textContent || "";
        navigator.clipboard.writeText(raw).then(() => UI.ok("Copied."), () => UI.err(new Error("Copy failed")));
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
        const userBubble = appendMsg("you", "", "user");
        setBody(userBubble, msg, mdEnabled);

        const provider = document.getElementById("chat-provider").value || undefined;
        const recovery = document.getElementById("chat-recovery").value || undefined;

        const localBubble = appendMsg("local prefix", "", "local");
        const cloudBubble = appendMsg("assistant", "", "cloud");

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
                const ms = ev.local_ms != null ? ` <span class="small dim">(${ev.local_ms}ms)</span>` : "";
                localBubble.querySelector(".who").innerHTML = "local prefix" + ms;
                setBody(localBubble, ev.text || "", false);
              } else if (ev.type === "chunk") {
                appendChunk(cloudBubble, ev.text || "", true);
              } else if (ev.type === "done") {
                if (ev.pressure && ev.pressure.rate_ratio != null) {
                  const ratio = (ev.pressure.rate_ratio * 100).toFixed(0);
                  cloudBubble.querySelector(".who").textContent = `assistant · pressure ${ratio}%`;
                }
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
