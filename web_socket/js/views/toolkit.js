/* Toolkit — adapter discover, ping, chat-route, state, checkpoint, relay. */

(function () {
  const V = window.Views = window.Views || {};
  const { el, loadingNode } = window.H;

  V.toolkit = {
    mount(root) {
      const view = {
        unmount() {},
        async refresh() { await loadDiscover(); },
      };

      root.appendChild(el("section", { class: "block" }, [
        el("h2", null, "Adapter discovery"),
        el("p", { class: "small" }, "What's configured (env vars present) and importable (libs installed). Click Ping to live-check."),
        el("div", { id: "tk-adapters" }, loadingNode()),
      ]));

      root.appendChild(el("section", { class: "block" }, [
        el("h2", null, "Chat through gateway (LiteLLM / vLLM / Ollama)"),
        el("div", { class: "card" }, [
          el("div", { class: "field" }, [
            el("label", null, "Model (blank = gateway default)"),
            el("input", { type: "text", id: "tk-model", placeholder: "e.g. gpt-4o, llama3, mixtral" }),
          ]),
          el("div", { class: "field" }, [
            el("label", null, "Messages (JSON array of {role, content})"),
            el("textarea", { id: "tk-messages", style: "min-height:100px",
              text: '[\n  {"role": "user", "content": "Say hi in one word."}\n]'
            }),
          ]),
          el("div", { class: "row" }, [
            el("label", { class: "small" }, "max_tokens"),
            el("input", { type: "number", id: "tk-maxtok", value: 64, style: "width:90px" }),
            el("label", { class: "small" }, "temperature"),
            el("input", { type: "number", id: "tk-temp", value: 0.7, step: 0.05, style: "width:80px" }),
            el("label", { class: "toggle" }, [
              el("input", { type: "checkbox", id: "tk-stream" }),
              el("span", { class: "sw" }), "Stream",
            ]),
            el("button", { onClick: doChat }, "Send"),
          ]),
          el("div", { class: "row", style: "margin-top:10px" }, [
            el("label", { class: "toggle" }, [
              el("input", { type: "checkbox", id: "tk-md", checked: "" }),
              el("span", { class: "sw" }), "Render reply as Markdown",
            ]),
          ]),
          el("div", { id: "tk-chat-out", class: "card md", style: "background:var(--bg);padding:10px;margin-top:10px;min-height:60px" }),
        ]),
      ]));

      root.appendChild(el("section", { class: "block" }, [
        el("h2", null, "Distributed state (Redis or local FS)"),
        el("div", { class: "card" }, [
          el("div", { class: "row" }, [
            el("input", { type: "text", id: "st-agent", placeholder: "agent_id" }),
            el("textarea", { id: "st-state", placeholder: '{"any": "json"}', style: "flex:1;min-height:60px" }),
            el("input", { type: "number", id: "st-ttl", placeholder: "ttl sec", style: "width:90px" }),
          ]),
          el("div", { class: "row", style: "margin-top:8px" }, [
            el("button", { onClick: stPut }, "Save"),
            el("button", { class: "btn-ghost", onClick: stGet }, "Load"),
            el("button", { class: "btn-err", onClick: stDel }, "Delete"),
            el("span", { class: "small dim", id: "st-out" }, ""),
          ]),
        ]),
      ]));

      root.appendChild(el("section", { class: "block" }, [
        el("h2", null, "Checkpoints (S3 or local FS)"),
        el("div", { id: "ck-list" }, loadingNode()),
        el("div", { class: "row", style: "margin-top:8px" }, [
          el("input", { type: "text", id: "ck-topic", placeholder: "topic_id to delete" }),
          el("button", { class: "btn-err", onClick: ckDel }, "Delete topic checkpoints"),
          el("button", { class: "btn-ghost", onClick: ckList }, "Refresh"),
        ]),
      ]));

      async function loadDiscover() {
        const wrap = document.getElementById("tk-adapters");
        wrap.innerHTML = "";
        try {
          const res = await API.get("/node/toolkit/discover");
          const adapters = res.adapters || {};
          const names = Object.keys(adapters);
          if (!names.length) {
            wrap.appendChild(el("div", { class: "empty" }, "No adapters reported."));
            return;
          }
          const tbl = el("table", null, [
            el("thead", null, el("tr", null,
              ["adapter", "configured", "importable", "env hints", "ping"].map(h => el("th", { text: h })))),
          ]);
          const tb = el("tbody");
          names.forEach(name => {
            const a = adapters[name] || {};
            const envHints = Object.keys(a.env || {}).map(k =>
              `${k}=${a.env[k] ? "set" : "—"}`
            ).join(" ");
            tb.appendChild(el("tr", null, [
              el("td", { class: "mono", text: name }),
              el("td", null, [a.configured ? badge("ok", "yes") : badge("dim", "no")]),
              el("td", null, [a.importable
                ? badge("ok", "yes")
                : el("span", { class: "pill err", title: a.import_err || "" }, "no")]),
              el("td", { class: "small mono", text: envHints }),
              el("td", null, [el("button", { class: "btn-ghost", onClick: () => ping(name) }, "Ping")]),
            ]));
          });
          tbl.appendChild(tb);
          wrap.appendChild(tbl);
        } catch (e) { UI.err(e); }
      }

      async function ping(name) {
        try {
          const r = await API.get("/node/toolkit/ping/" + encodeURIComponent(name));
          UI.toast(name + " — " + JSON.stringify(r), r.ok ? "ok" : "err");
        } catch (e) { UI.err(e); }
      }

      async function doChat() {
        const out = document.getElementById("tk-chat-out");
        out.innerHTML = "";
        out.dataset.raw = "";
        const stream = document.getElementById("tk-stream").checked;
        const md = document.getElementById("tk-md").checked;
        const model = document.getElementById("tk-model").value.trim() || undefined;
        let messages;
        try { messages = JSON.parse(document.getElementById("tk-messages").value); }
        catch { UI.err(new Error("Messages must be valid JSON")); return; }
        const body = {
          messages, model,
          max_tokens: Number(document.getElementById("tk-maxtok").value) || undefined,
          temperature: Number(document.getElementById("tk-temp").value),
          stream,
        };
        const apply = (t) => {
          out.dataset.raw = t;
          if (md) { out.classList.add("md"); out.innerHTML = window.MD.render(t); }
          else    { out.classList.remove("md"); out.style.whiteSpace = "pre-wrap"; out.textContent = t; }
        };
        if (stream) {
          let buf = "";
          API.sse("/node/toolkit/chat", body, {
            onEvent(ev) {
              const txt = ev.delta || ev.text || ev.content || "";
              if (typeof txt === "string") { buf += txt; apply(buf); }
            },
            onError(e) { UI.err(e); },
            onDone() { apply(buf + "\n\n_[done]_"); },
          });
        } else {
          try {
            const res = await API.post("/node/toolkit/chat", body);
            const reply = res.choices?.[0]?.message?.content || res.content || JSON.stringify(res, null, 2);
            apply(reply);
          } catch (e) { UI.err(e); }
        }
      }

      async function stPut() {
        const agent = document.getElementById("st-agent").value.trim();
        if (!agent) { UI.err(new Error("agent_id required")); return; }
        let state;
        try { state = JSON.parse(document.getElementById("st-state").value || "{}"); }
        catch { UI.err(new Error("State must be JSON")); return; }
        const ttl = Number(document.getElementById("st-ttl").value) || undefined;
        try {
          const res = await API.put("/node/toolkit/state", { agent_id: agent, state, ttl });
          document.getElementById("st-out").textContent = "saved (" + (res.kind || "?") + ")";
          UI.ok("State saved.");
        } catch (e) { UI.err(e); }
      }
      async function stGet() {
        const agent = document.getElementById("st-agent").value.trim();
        if (!agent) { UI.err(new Error("agent_id required")); return; }
        try {
          const res = await API.get("/node/toolkit/state/" + encodeURIComponent(agent));
          document.getElementById("st-state").value = JSON.stringify(res.state || {}, null, 2);
          document.getElementById("st-out").textContent = "loaded (" + (res.kind || "?") + ")";
        } catch (e) { UI.err(e); }
      }
      async function stDel() {
        const agent = document.getElementById("st-agent").value.trim();
        if (!agent || !confirm("Delete state for " + agent + "?")) return;
        try {
          await API.del("/node/toolkit/state/" + encodeURIComponent(agent));
          document.getElementById("st-out").textContent = "deleted";
          UI.ok("Deleted.");
        } catch (e) { UI.err(e); }
      }

      async function ckList() {
        const wrap = document.getElementById("ck-list");
        wrap.innerHTML = "";
        try {
          const res = await API.get("/node/toolkit/checkpoints");
          const topics = res.topics || [];
          wrap.appendChild(el("div", { class: "small dim" }, "store: " + (res.kind || "?")));
          if (!topics.length) {
            wrap.appendChild(el("div", { class: "empty" }, "No checkpoint topics."));
            return;
          }
          const ul = el("ul", { class: "mono small", style: "padding-left:18px" });
          topics.forEach(t => ul.appendChild(el("li", { text: t })));
          wrap.appendChild(ul);
        } catch (e) { UI.err(e); }
      }
      async function ckDel() {
        const t = document.getElementById("ck-topic").value.trim();
        if (!t || !confirm("Delete all checkpoints for " + t + "?")) return;
        try {
          await API.del("/node/toolkit/checkpoint/" + encodeURIComponent(t));
          UI.ok("Deleted.");
          ckList();
        } catch (e) { UI.err(e); }
      }

      function badge(kind, txt) { return el("span", { class: "pill " + kind }, txt); }

      loadDiscover();
      ckList();
      return view;
    },
  };
})();
