/* Toolkit — per-toolkit deployment + operations hub.
 *
 * EVERY action in this view goes through an HTTP endpoint on the AGCL
 * node — there is NO direct embedded engine logic. The web UI is
 * optional; the same operations are available from the CLI
 * (`agcl deploy <name>`, `agcl toolkit ping <adapter>`, etc.).
 *
 * Layout:
 *   1. Per-toolkit integration cards — every adapter from
 *      /node/toolkit/discover, plus wizard-only services (Discord,
 *      Slack, AWS, GitHub, HuggingFace, …). Each card has:
 *         - configured / importable / reachable pills
 *         - Ping button (-> /node/toolkit/ping/{name})
 *         - Login / Deploy button (-> launches matching wizard)
 *   2. Gateway chat (POST /node/toolkit/chat)
 *   3. Distributed state CRUD (/node/toolkit/state/*)
 *   4. Checkpoint store (/node/toolkit/checkpoint*)
 */

(function () {
  const V = window.Views = window.Views || {};

  // ----- adapter discover entry  -> wizard name -----
  // What the GUI calls "logging in to" each toolkit.
  const ADAPTER_TO_WIZARD = {
    litellm:    "litellm",
    ollama:     "ollama",
    vllm:       "vllm",
    tgi:        "tgi",
    redis:      "redis",
    s3:         "minio",        // simplest path is MinIO; AWS S3 = `aws` wizard
    cloudflare: "cloudflare",
    discord:    "discord",
    webrtc:     "webrtc",
    gcp:        "gcp",
    k8s:        "k8s",
    docker:     "github",       // github wizard does the GHCR docker-login
  };

  // Wizards that don't have a discover entry but deserve their own card.
  const EXTRA_TOOLKITS = [
    { name: "discord",      wizard: "discord",      category: "bot",      title: "Discord bot",            summary: "Create a bot, paste token, generate invite URL." },
    { name: "slack",        wizard: "slack",        category: "bot",      title: "Slack bot",              summary: "Paste Bolt token + signing secret." },
    { name: "aws",          wizard: "aws",          category: "cloud",    title: "Amazon Web Services",     summary: "Access keys + STS validation. Pairs with S3 store." },
    { name: "github",       wizard: "github",       category: "cloud",    title: "GitHub / ghcr.io",       summary: "PAT + docker login for the container registry." },
    { name: "huggingface",  wizard: "huggingface",  category: "cloud",    title: "HuggingFace",            summary: "Token for gated models + private repos." },
    { name: "openai",       wizard: "openai",       category: "provider", title: "OpenAI",                 summary: "Validate + save your OpenAI key." },
    { name: "anthropic",    wizard: "anthropic",    category: "provider", title: "Anthropic (Claude)",     summary: "Validate + save your Anthropic key." },
    { name: "deepseek",     wizard: "deepseek",     category: "provider", title: "DeepSeek",               summary: "Validate + save; balance auto-surfaces." },
    { name: "openai_compat",wizard: "openai_compat",category: "provider", title: "Custom OpenAI-compat",   summary: "Together / Fireworks / Groq / any /v1." },
    { name: "minio",        wizard: "minio",        category: "store",    title: "MinIO (local S3)",       summary: "Self-hosted S3 backend for checkpoints." },
    { name: "postgres",     wizard: "postgres",     category: "store",    title: "Postgres (LiteLLM DB)",  summary: "Spend tracking + admin UI for LiteLLM." },
    { name: "litellm",      wizard: "litellm",      category: "infra",    title: "LiteLLM proxy (Docker)", summary: "Local gateway with routing + fallback." },
  ];

  const CATEGORY_LABEL = {
    provider: "Provider keys",
    bot:      "Bots",
    cloud:    "Cloud accounts",
    model:    "Local model servers",
    store:    "Stores",
    infra:    "Infrastructure",
  };

  V.toolkit = {
    mount(root) {
      const { el, loadingNode } = window.H;
      const cards = {};        // name -> card element

      const view = {
        unmount() {},
        async refresh() { await loadDiscover(); },
      };

      root.appendChild(el("p", { class: "small dim" },
        "Per-toolkit hub. Each card here is fronted by an HTTP endpoint — ",
        el("code", null, "GET /node/toolkit/discover"), ", ",
        el("code", null, "GET /node/toolkit/ping/{name}"), ", ",
        el("code", null, "POST /node/setup/wizards/{name}/start"), ". ",
        "The web UI is optional; the same operations are also in the CLI as ",
        el("code", null, "agcl deploy"), ", ",
        el("code", null, "agcl toolkit ping"), ", and friends."));

      // -------------------- Integration cards --------------------
      root.appendChild(el("section", { class: "block" }, [
        el("div", { class: "row" }, [
          el("h2", { style: "margin:0" }, "Integrations"),
          el("span", { class: "spacer" }),
          el("button", { class: "btn-ghost", onClick: () => loadDiscover() }, "Rescan"),
        ]),
        el("div", { id: "tk-cards", class: "grid", style: "grid-template-columns:repeat(auto-fill,minmax(280px,1fr))" }, loadingNode()),
        el("div", { id: "tk-wizard-runner", style: "margin-top:14px" }),
      ]));

      // -------------------- Gateway chat -------------------------
      root.appendChild(el("section", { class: "block" }, [
        el("h2", null, "Chat through gateway (LiteLLM / vLLM / Ollama / custom)"),
        el("div", { class: "card" }, [
          el("div", { class: "field" }, [
            el("label", null, "Model (blank = gateway default)"),
            el("input", { type: "text", id: "tk-model", placeholder: "e.g. gpt-4o, llama3, mixtral" }),
          ]),
          el("div", { class: "field" }, [
            el("label", null, "Messages (JSON array of {role, content})"),
            el("textarea", { id: "tk-messages", style: "min-height:100px",
              text: '[\n  {"role": "user", "content": "Say hi in one word."}\n]' }),
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

      // -------------------- Distributed state --------------------
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

      // -------------------- Checkpoint store ---------------------
      root.appendChild(el("section", { class: "block" }, [
        el("h2", null, "Checkpoints (S3 or local FS)"),
        el("div", { id: "ck-list" }, loadingNode()),
        el("div", { class: "row", style: "margin-top:8px" }, [
          el("input", { type: "text", id: "ck-topic", placeholder: "topic_id to delete" }),
          el("button", { class: "btn-err", onClick: ckDel }, "Delete topic checkpoints"),
          el("button", { class: "btn-ghost", onClick: ckList }, "Refresh"),
        ]),
      ]));

      // ===========================================================
      // INTEGRATIONS — render and refresh
      // ===========================================================

      async function loadDiscover() {
        const wrap = document.getElementById("tk-cards");
        wrap.innerHTML = "";
        let adapters = {};
        try {
          const res = await API.get("/node/toolkit/discover");
          adapters = res.adapters || {};
        } catch (e) { UI.err(e); }

        // Merge: discover-listed adapters first, then any extras
        const all = [];
        const seen = new Set();
        Object.keys(adapters).sort().forEach(name => {
          const a = adapters[name] || {};
          all.push({
            name,
            wizard: ADAPTER_TO_WIZARD[name] || null,
            category: inferCategory(name),
            title: prettyName(name),
            summary: summarize(name, a),
            configured: !!a.configured,
            importable: !!a.importable,
            import_err: a.import_err || "",
            env: a.env || {},
          });
          seen.add(name);
        });
        EXTRA_TOOLKITS.forEach(e => {
          if (!seen.has(e.name)) all.push({ ...e, configured: false, importable: true, env: {} });
        });

        // Render
        all.forEach(t => wrap.appendChild(renderCard(t)));
      }

      function inferCategory(name) {
        const m = {
          litellm: "infra", ollama: "model", vllm: "model", tgi: "model",
          redis: "store", s3: "store", cloudflare: "cloud", discord: "bot",
          webrtc: "infra", gcp: "cloud", k8s: "infra", docker: "infra",
        };
        return m[name] || "infra";
      }
      function prettyName(name) {
        const m = {
          litellm: "LiteLLM gateway", ollama: "Ollama", vllm: "vLLM",
          tgi: "TGI (HF Text-Gen-Inference)", redis: "Redis", s3: "S3 / R2 / B2",
          cloudflare: "Cloudflare relay", discord: "Discord bot", webrtc: "WebRTC signaling",
          gcp: "Google Cloud", k8s: "Kubernetes", docker: "Docker / container registry",
        };
        return m[name] || name;
      }
      function summarize(name, a) {
        if (a.import_err) return "import failed — see error";
        return ({
          litellm: "Routes chat through a multi-provider proxy.",
          ollama:  "Local OpenAI-compatible LLM server.",
          vllm:    "Throughput-focused GPU LLM server.",
          tgi:     "Maintenance-mode HF serving engine.",
          redis:   "Distributed state for multi-node deploys.",
          s3:      "Trained-topic checkpoint store.",
          cloudflare: "Edge relay for many GUI clients.",
          discord: "Wrap AGCL as a Discord bot.",
          webrtc:  "Peer-to-peer GUI <-> node DataChannel.",
          gcp:     "Google Cloud auth (service account JSON).",
          k8s:     "Kubernetes cluster pointer + emit manifests.",
          docker:  "Docker registry login (GHCR / Docker Hub / etc.).",
        })[name] || "";
      }

      function renderCard(t) {
        const card = el("div", { class: "card" });
        cards[t.name] = card;
        card.appendChild(el("div", { class: "row" }, [
          el("span", { class: "pill dim" }, CATEGORY_LABEL[t.category] || t.category),
          el("span", { class: "spacer" }),
          el("span", { class: "small mono dim", title: t.name }, t.name),
        ]));
        card.appendChild(el("h3", { style: "margin:8px 0 4px 0" }, t.title));
        card.appendChild(el("p", { class: "small dim", style: "margin:0 0 8px 0" }, t.summary));

        // Pills
        const pillsRow = el("div", { class: "row", style: "gap:6px;flex-wrap:wrap" });
        pillsRow.appendChild(t.configured
          ? el("span", { class: "pill ok" }, "configured")
          : el("span", { class: "pill dim" }, "not configured"));
        if ("importable" in t) {
          pillsRow.appendChild(t.importable
            ? el("span", { class: "pill ok" }, "importable")
            : el("span", { class: "pill err", title: t.import_err }, "import failed"));
        }
        card.appendChild(pillsRow);

        // Env hints
        const envKeys = Object.keys(t.env || {});
        if (envKeys.length) {
          card.appendChild(el("div", { class: "small dim mono", style: "margin-top:6px;font-size:10px" },
            envKeys.map(k => `${k}=${t.env[k] ? "set" : "—"}`).join("  ")));
        }

        // Actions
        const actions = el("div", { class: "row", style: "margin-top:10px;gap:6px" });
        // Ping (only for adapters with an importable status)
        if ("importable" in t && t.importable !== false && ADAPTER_TO_WIZARD[t.name] !== undefined) {
          actions.appendChild(el("button", { class: "btn-ghost", onClick: () => ping(t.name) }, "Ping"));
        }
        // Login / Deploy via wizard
        if (t.wizard) {
          const label = t.category === "bot" ? "Deploy"
                      : t.category === "model" ? "Set up"
                      : t.category === "store" ? "Set up"
                      : t.category === "infra" ? "Configure"
                      : "Connect";
          actions.appendChild(el("button", { onClick: () => launchWizard(t.wizard) }, label));
        }
        card.appendChild(actions);
        return card;
      }

      async function ping(name) {
        try {
          const r = await API.get("/node/toolkit/ping/" + encodeURIComponent(name));
          const kind = r.ok ? "ok" : "err";
          UI.toast(name + " — " + JSON.stringify(r), kind);
        } catch (e) { UI.err(e); }
      }

      // ===========================================================
      // INLINE WIZARD RUNNER — drives /node/setup/wizards/*
      // ===========================================================

      async function launchWizard(name) {
        const run = document.getElementById("tk-wizard-runner");
        run.innerHTML = "";
        run.appendChild(el("div", { class: "small dim" }, [
          el("span", { class: "spinner" }), " starting " + name + " wizard…"
        ]));
        try {
          const state = await API.post(`/node/setup/wizards/${encodeURIComponent(name)}/start`, {});
          renderWizardState(state);
        } catch (e) {
          UI.err(e);
          run.innerHTML = "";
        }
      }

      function renderWizardState(state) {
        const run = document.getElementById("tk-wizard-runner");
        run.innerHTML = "";
        if (!state) return;
        const step = state.step || {};
        const kind = step.type || "";
        const card = el("div", { class: "card", style: "max-width:760px" });

        card.appendChild(el("div", { class: "row" }, [
          el("span", { class: "pill " + (state.state === "error" ? "err" : state.state === "done" ? "ok" : "warn") },
            state.state || "running"),
          el("span", { class: "spacer" }),
          el("span", { class: "small mono dim" }, state.wizard + " · " + state.id),
          el("button", { class: "btn-ghost", onClick: () => cancelWizard(state.id) }, "Cancel"),
        ]));

        if (state.state === "error") {
          card.appendChild(el("h3", null, "Wizard failed"));
          card.appendChild(el("pre", { class: "small mono", style: "color:var(--err);white-space:pre-wrap" },
            state.error || "unknown error"));
          run.appendChild(card);
          return;
        }
        if (state.state === "done") {
          card.appendChild(el("h3", null, step.title || "Done"));
          if (step.body) {
            const md = el("div", { class: "md", style: "margin-top:6px" });
            md.innerHTML = window.MD.render(step.body);
            card.appendChild(md);
          }
          card.appendChild(el("div", { class: "row", style: "margin-top:10px" }, [
            el("button", { class: "btn-ghost", onClick: () => { run.innerHTML = ""; loadDiscover(); } }, "Close"),
          ]));
          run.appendChild(card);
          return;
        }

        // Active step
        card.appendChild(el("h3", null, step.title || step.id));
        if (step.body) {
          const md = el("div", { class: "md", style: "margin:6px 0 10px" });
          md.innerHTML = window.MD.render(step.body);
          card.appendChild(md);
        }

        if (kind === "info") {
          card.appendChild(el("button", { onClick: () => sendAnswer(state.id, null) }, "Continue"));
        } else if (kind === "open_url") {
          const url = step.url || "";
          card.appendChild(el("div", { class: "row" }, [
            el("button", { onClick: () => window.open(url, "_blank", "noopener,noreferrer") }, "Open in new tab"),
            el("input", { type: "text", value: url, readonly: true, style: "flex:1;min-width:200px" }),
          ]));
          card.appendChild(el("div", { class: "row", style: "margin-top:8px" }, [
            el("button", { onClick: () => sendAnswer(state.id, null) }, "I've done that — continue"),
          ]));
        } else if (kind === "input") {
          const inputId = "tk-wiz-in-" + state.id;
          card.appendChild(el("div", { class: "field" }, [
            el("input", {
              id: inputId,
              type: step.secret ? "password" : "text",
              placeholder: step.placeholder || "",
              value: step.default || "",
            }),
          ]));
          card.appendChild(el("button", { onClick: () => {
            const v = document.getElementById(inputId).value;
            sendAnswer(state.id, v);
          } }, "Continue"));
        } else if (kind === "choice") {
          (step.options || []).forEach(opt => {
            card.appendChild(el("div", { class: "row", style: "margin:4px 0" }, [
              el("button", { onClick: () => sendAnswer(state.id, opt.value),
                style: "min-width:240px;justify-content:flex-start" }, opt.label),
            ]));
          });
        } else if (kind === "confirm") {
          card.appendChild(el("div", { class: "row" }, [
            el("button", { class: "btn-ok",  onClick: () => sendAnswer(state.id, true) },  "Yes"),
            el("button", { class: "btn-err", onClick: () => sendAnswer(state.id, false) }, "No"),
          ]));
        } else {
          card.appendChild(el("div", { class: "small dim" }, "unknown step kind: " + kind));
        }

        if (Array.isArray(state.log) && state.log.length) {
          card.appendChild(el("pre", { class: "small mono dim",
            style: "white-space:pre-wrap;margin-top:10px;background:var(--bg);padding:6px;border:1px solid var(--line);max-height:120px;overflow:auto" },
            state.log.slice(-8).join("\n")));
        }

        run.appendChild(card);
      }

      async function sendAnswer(sid, value) {
        try {
          const state = await API.post(`/node/setup/wizards/sessions/${encodeURIComponent(sid)}/answer`,
            { value });
          renderWizardState(state);
        } catch (e) { UI.err(e); }
      }

      async function cancelWizard(sid) {
        try {
          await API.post(`/node/setup/wizards/sessions/${encodeURIComponent(sid)}/cancel`, {});
          UI.toast("Wizard cancelled.");
          document.getElementById("tk-wizard-runner").innerHTML = "";
        } catch (e) { UI.err(e); }
      }

      // ===========================================================
      // CHAT through gateway
      // ===========================================================
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

      // ===========================================================
      // STATE
      // ===========================================================
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

      // ===========================================================
      // CHECKPOINTS
      // ===========================================================
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

      loadDiscover();
      ckList();
      return view;
    },
  };
})();
