/* Usage & Cost — per-provider stats, sessions, quota editor, custom providers,
 * timeseries chart. */

(function () {
  const V = window.Views = window.Views || {};
  const { el, fmtNum, fmtUsd, loadingNode } = window.H;

  V.usage = {
    mount(root) {
      const view = {
        unmount() {},
        async refresh() { await loadAll(); },
      };

      root.appendChild(el("section", { class: "block" }, [
        el("h2", null, "Per-provider summary"),
        el("div", { id: "u-providers", class: "grid" }, [loadingNode()]),
      ]));

      root.appendChild(el("section", { class: "block" }, [
        el("h2", null, "Token timeseries (last hour)"),
        el("div", { class: "row", style: "margin-bottom:8px" }, [
          el("label", { class: "small" }, "Bucket sec"),
          el("input", { type: "number", id: "ts-bucket", value: 60, min: 10, max: 3600, style: "width:80px" }),
          el("label", { class: "small" }, "Lookback sec"),
          el("input", { type: "number", id: "ts-lookback", value: 3600, min: 60, max: 86400, style: "width:100px" }),
          el("button", { class: "btn-ghost", onClick: loadTimeseries }, "Update"),
        ]),
        el("div", { id: "u-ts", class: "card" }, loadingNode()),
      ]));

      root.appendChild(el("section", { class: "block" }, [
        el("h2", null, "Sessions"),
        el("div", { id: "u-sessions" }, loadingNode()),
      ]));

      root.appendChild(el("section", { class: "block" }, [
        el("h2", null, "Quotas"),
        el("div", { class: "card" }, [
          el("div", { class: "row" }, [
            el("label", { class: "small" }, "Provider"),
            el("input", { type: "text", id: "q-provider", placeholder: "openai / claude / custom-name" }),
            el("label", { class: "small" }, "Max tokens"),
            el("input", { type: "number", id: "q-tokens", placeholder: "blank=none", style: "width:120px" }),
            el("label", { class: "small" }, "Max USD"),
            el("input", { type: "number", id: "q-usd", placeholder: "blank=none", step: "0.01", style: "width:100px" }),
            el("label", { class: "small" }, "Period"),
            el("select", { id: "q-period" }, [
              el("option", { value: "lifetime" }, "lifetime"),
              el("option", { value: "monthly" }, "monthly"),
              el("option", { value: "daily" }, "daily"),
            ]),
            el("button", { onClick: setQuota }, "Apply quota"),
            el("button", { class: "btn-err", onClick: clearQuota }, "Clear"),
          ]),
          el("div", { id: "u-quotas-list", style: "margin-top:10px" }, loadingNode()),
        ]),
      ]));

      root.appendChild(el("section", { class: "block" }, [
        el("h2", null, "Custom OpenAI-compatible providers"),
        el("div", { class: "card" }, [
          el("div", { class: "row" }, [
            el("input", { type: "text", id: "cp-name", placeholder: "name" }),
            el("input", { type: "text", id: "cp-base", placeholder: "https://api.example.com/v1" }),
            el("input", { type: "text", id: "cp-keyenv", placeholder: "API_KEY_ENV_VAR" }),
            el("input", { type: "text", id: "cp-model", placeholder: "model id" }),
            el("select", { id: "cp-kind" }, [
              el("option", { value: "chat" }, "chat"),
              el("option", { value: "knowledge" }, "knowledge"),
            ]),
            el("button", { onClick: addCustom }, "Add"),
          ]),
          el("div", { id: "u-custom", style: "margin-top:10px" }, loadingNode()),
        ]),
      ]));

      async function loadAll() {
        await Promise.all([loadProviders(), loadSessions(), loadQuotas(), loadCustom(), loadTimeseries()]);
      }

      async function loadProviders() {
        const wrap = document.getElementById("u-providers");
        wrap.innerHTML = "";
        try {
          const sum = await API.get("/node/usage/summary");
          const pp = sum.per_provider || {};
          const names = Object.keys(pp);
          if (!names.length) {
            wrap.appendChild(el("div", { class: "empty" }, "No usage recorded yet."));
            return;
          }
          names.forEach(name => {
            const p = pp[name] || {};
            const card = el("div", { class: "card" }, [
              el("h3", null, name),
              row("model", p.model || "—"),
              row("tokens in",  fmtNum(p.tokens_in)),
              row("tokens out", fmtNum(p.tokens_out)),
              row("est cost",   fmtUsd(p.estimated_cost_usd)),
              row("quota", p.quota
                ? el("span", { class: "small mono" },
                    `${p.quota.max_tokens ? "tok≤"+fmtNum(p.quota.max_tokens) : ""} ${p.quota.max_credit_usd ? "$≤"+p.quota.max_credit_usd : ""} ${p.quota.period||""}`.trim())
                : el("span", { class: "pill dim" }, "none")),
            ]);
            wrap.appendChild(card);
          });

          const total = sum.global || {};
          if (total.total_tokens != null || total.total_cost_usd != null) {
            wrap.appendChild(el("div", { class: "card" }, [
              el("h3", null, "TOTAL"),
              row("tokens", fmtNum(total.total_tokens)),
              row("cost",   fmtUsd(total.total_cost_usd)),
            ]));
          }
        } catch (e) { UI.err(e); }
      }

      async function loadSessions() {
        const wrap = document.getElementById("u-sessions");
        wrap.innerHTML = "";
        try {
          const res = await API.get("/node/usage/sessions");
          const sessions = res.sessions || [];
          if (!sessions.length) {
            wrap.appendChild(el("div", { class: "empty" }, "No session usage."));
            return;
          }
          const tbl = el("table", null, [
            el("thead", null, el("tr", null,
              ["session", "kind", "provider", "tok in", "tok out", "cost"].map(h => el("th", { text: h })))),
          ]);
          const tb = el("tbody");
          sessions.forEach(s => {
            tb.appendChild(el("tr", null, [
              el("td", { class: "mono clip", title: s.session_id || "" }, s.session_id || "—"),
              el("td", { text: s.kind || "—" }),
              el("td", { text: s.provider || "—" }),
              el("td", { class: "right", text: fmtNum(s.tokens_in) }),
              el("td", { class: "right", text: fmtNum(s.tokens_out) }),
              el("td", { class: "right", text: fmtUsd(s.cost_usd) }),
            ]));
          });
          tbl.appendChild(tb);
          wrap.appendChild(tbl);
        } catch (e) { UI.err(e); }
      }

      async function loadQuotas() {
        const wrap = document.getElementById("u-quotas-list");
        wrap.innerHTML = "";
        try {
          const res = await API.get("/node/usage/quotas");
          const qs = res.quotas || [];
          if (!qs.length) {
            wrap.appendChild(el("div", { class: "small dim" }, "No quotas set."));
            return;
          }
          const tbl = el("table", null, [
            el("thead", null, el("tr", null,
              ["provider", "max tokens", "max usd", "period", "notes", ""].map(h => el("th", { text: h })))),
          ]);
          const tb = el("tbody");
          qs.forEach(q => {
            tb.appendChild(el("tr", null, [
              el("td", { text: q.provider }),
              el("td", { text: q.max_tokens != null ? fmtNum(q.max_tokens) : "—" }),
              el("td", { text: q.max_credit_usd != null ? fmtUsd(q.max_credit_usd) : "—" }),
              el("td", { text: q.period || "lifetime" }),
              el("td", { class: "small clip", text: q.notes || "" }),
              el("td", null, [el("button", { class: "btn-err", onClick: () => clearOne(q.provider) }, "Clear")]),
            ]));
          });
          tbl.appendChild(tb);
          wrap.appendChild(tbl);
        } catch (e) { UI.err(e); }
      }

      async function loadCustom() {
        const wrap = document.getElementById("u-custom");
        wrap.innerHTML = "";
        try {
          const res = await API.get("/node/usage/providers/custom");
          const ps = res.providers || [];
          if (!ps.length) {
            wrap.appendChild(el("div", { class: "small dim" }, "No custom providers."));
            return;
          }
          const tbl = el("table", null, [
            el("thead", null, el("tr", null,
              ["name", "base url", "model", "kind", ""].map(h => el("th", { text: h })))),
          ]);
          const tb = el("tbody");
          ps.forEach(p => {
            tb.appendChild(el("tr", null, [
              el("td", { class: "mono", text: p.name }),
              el("td", { class: "clip", title: p.base_url || "" }, p.base_url || "—"),
              el("td", { text: p.model || "—" }),
              el("td", { text: p.kind || "—" }),
              el("td", null, [el("button", { class: "btn-err", onClick: () => delCustom(p.name) }, "Delete")]),
            ]));
          });
          tbl.appendChild(tb);
          wrap.appendChild(tbl);
        } catch (e) { UI.err(e); }
      }

      async function loadTimeseries() {
        const wrap = document.getElementById("u-ts");
        wrap.innerHTML = "";
        try {
          const bucket = Number(document.getElementById("ts-bucket").value) || 60;
          const lookback = Number(document.getElementById("ts-lookback").value) || 3600;
          const res = await API.get(`/node/usage/timeseries?bucket_sec=${bucket}&lookback_sec=${lookback}`);
          const buckets = res.buckets || [];
          if (!buckets.length) {
            wrap.appendChild(el("div", { class: "small dim" }, "No data in window."));
            return;
          }
          wrap.appendChild(svgBars(buckets));
        } catch (e) { UI.err(e); }
      }

      function svgBars(buckets) {
        const W = 800, H = 160, P = 24;
        const ns = "http://www.w3.org/2000/svg";
        const svg = document.createElementNS(ns, "svg");
        svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
        svg.setAttribute("width", "100%");
        const max = Math.max(1, ...buckets.map(b => Number(b.tokens) || 0));
        const bw = (W - 2*P) / buckets.length;
        // baseline
        const ax = document.createElementNS(ns, "line");
        ax.setAttribute("x1", P); ax.setAttribute("x2", W-P);
        ax.setAttribute("y1", H-P); ax.setAttribute("y2", H-P);
        ax.setAttribute("class", "axis");
        svg.appendChild(ax);
        buckets.forEach((b, i) => {
          const v = Number(b.tokens) || 0;
          const h = ((H - 2*P) * v) / max;
          const r = document.createElementNS(ns, "rect");
          r.setAttribute("x", P + i*bw + 1);
          r.setAttribute("y", H - P - h);
          r.setAttribute("width", Math.max(1, bw - 2));
          r.setAttribute("height", h);
          r.setAttribute("class", "bar");
          const t = document.createElementNS(ns, "title");
          t.textContent = `${new Date((b.timestamp||0)*1000).toLocaleTimeString()} — ${v} tok ${b.cost ? "$"+b.cost.toFixed(4) : ""}`;
          r.appendChild(t);
          svg.appendChild(r);
        });
        return svg;
      }

      function row(k, v) {
        return el("div", { class: "row kv" }, [
          el("span", { class: "k" }, k),
          el("span", { class: "v" }, [v && v.nodeType ? v : document.createTextNode(String(v))]),
        ]);
      }

      async function setQuota() {
        const provider = document.getElementById("q-provider").value.trim();
        if (!provider) { UI.err(new Error("Provider required.")); return; }
        const tok = document.getElementById("q-tokens").value;
        const usd = document.getElementById("q-usd").value;
        const period = document.getElementById("q-period").value;
        const body = { period };
        if (tok) body.max_tokens = Number(tok);
        if (usd) body.max_credit_usd = Number(usd);
        try {
          await API.put("/node/usage/quota/" + encodeURIComponent(provider), body);
          UI.ok("Quota set.");
          loadQuotas(); loadProviders();
        } catch (e) { UI.err(e); }
      }

      async function clearQuota() {
        const provider = document.getElementById("q-provider").value.trim();
        if (!provider) { UI.err(new Error("Provider required.")); return; }
        await clearOne(provider);
      }

      async function clearOne(provider) {
        if (!confirm("Clear quota for " + provider + "?")) return;
        try {
          await API.del("/node/usage/quota/" + encodeURIComponent(provider));
          UI.ok("Quota cleared.");
          loadQuotas(); loadProviders();
        } catch (e) { UI.err(e); }
      }

      async function addCustom() {
        const name    = document.getElementById("cp-name").value.trim();
        const base    = document.getElementById("cp-base").value.trim();
        const keyenv  = document.getElementById("cp-keyenv").value.trim();
        const model   = document.getElementById("cp-model").value.trim();
        const kind    = document.getElementById("cp-kind").value;
        if (!name || !base || !keyenv || !model) {
          UI.err(new Error("All fields required.")); return;
        }
        try {
          await API.post("/node/usage/providers/custom", {
            name, base_url: base, api_key_env: keyenv, model, kind,
          });
          UI.ok("Custom provider added.");
          ["cp-name","cp-base","cp-keyenv","cp-model"].forEach(id => document.getElementById(id).value = "");
          loadCustom();
        } catch (e) { UI.err(e); }
      }

      async function delCustom(name) {
        if (!confirm("Delete custom provider " + name + "?")) return;
        try {
          await API.del("/node/usage/providers/custom/" + encodeURIComponent(name));
          UI.ok("Deleted.");
          loadCustom();
        } catch (e) { UI.err(e); }
      }

      loadAll();
      return view;
    },
  };
})();
