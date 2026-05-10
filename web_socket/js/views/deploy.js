/* Deploy Manifests — Docker / Kubernetes / GCP Cloud Run.
 *
 * Refresh re-emits the last manifest you generated, useful when toggles
 * change and you want a quick re-run. */

(function () {
  const V = window.Views = window.Views || {};
  const { el } = window.H;

  V.deploy = {
    mount(root) {
      let lastKind = null;

      const view = {
        unmount() {},
        async refresh() {
          if (lastKind) await emit(lastKind);
          else UI.toast("Pick a target first.");
        },
      };

      root.appendChild(el("p", { class: "small" },
        "Generate deployment manifests directly from the running node. Output is a JSON-encoded manifest set — copy and apply on the target host. The same logic ships in the CLI as ",
        el("code", null, "agcl toolkit emit-{docker,k8s,gcp}"), "."));

      root.appendChild(el("section", { class: "block" }, [
        el("h2", null, "Common options"),
        el("div", { class: "card" }, [
          el("div", { class: "row" }, [
            el("div", { class: "field" }, [
              el("label", null, "out_dir (server-relative, optional)"),
              el("input", { type: "text", id: "dep-out", placeholder: "deploy/" }),
            ]),
            toggle("dep-gpu", "GPU"),
            toggle("dep-ollama", "+ Ollama"),
            toggle("dep-litellm", "+ LiteLLM proxy"),
            toggle("dep-redis", "+ Redis"),
          ]),
          el("div", { class: "row", style: "margin-top:8px" }, [
            el("button", { onClick: () => emit("docker") }, "Emit Docker"),
            el("button", { onClick: () => emit("k8s") },    "Emit Kubernetes"),
            el("button", { onClick: () => emit("gcp") },    "Emit Cloud Run (GCP)"),
            el("span", { class: "spacer" }),
            el("button", { class: "btn-ghost", onClick: () => copyOut() }, "Copy output"),
            el("button", { class: "btn-ghost", onClick: () => downloadOut() }, "Download .json"),
          ]),
        ]),
      ]));

      root.appendChild(el("section", { class: "block" }, [
        el("h2", null, [
          "Output ",
          el("span", { id: "dep-kind", class: "pill dim" }, "—"),
        ]),
        el("pre", { id: "dep-out-pre", class: "card mono small", style: "white-space:pre-wrap;max-height:60vh;overflow:auto;background:var(--bg)" }),
      ]));

      function toggle(id, label) {
        return el("label", { class: "toggle" }, [
          el("input", { type: "checkbox", id }),
          el("span", { class: "sw" }), label,
        ]);
      }

      function buildBody() {
        const out = document.getElementById("dep-out").value.trim();
        const body = {
          gpu: document.getElementById("dep-gpu").checked,
          with_ollama: document.getElementById("dep-ollama").checked,
          with_litellm: document.getElementById("dep-litellm").checked,
          with_redis: document.getElementById("dep-redis").checked,
        };
        if (out) body.out_dir = out;
        return body;
      }

      async function emit(kind) {
        lastKind = kind;
        const out = document.getElementById("dep-out-pre");
        const lbl = document.getElementById("dep-kind");
        lbl.textContent = kind;
        lbl.classList.remove("dim");
        out.textContent = "generating…";
        try {
          const res = await API.post(`/node/toolkit/manifest/${kind}`, buildBody());
          out.textContent = JSON.stringify(res, null, 2);
          UI.ok(kind + " manifests generated.");
        } catch (e) { out.textContent = ""; UI.err(e); }
      }

      function copyOut() {
        const t = document.getElementById("dep-out-pre").textContent || "";
        if (!t) return;
        navigator.clipboard.writeText(t).then(() => UI.ok("Copied."), () => UI.err(new Error("Copy failed")));
      }

      function downloadOut() {
        const t = document.getElementById("dep-out-pre").textContent || "";
        if (!t || !lastKind) { UI.err(new Error("Nothing to download.")); return; }
        const blob = new Blob([t], { type: "application/json" });
        const a = document.createElement("a");
        a.href = URL.createObjectURL(blob);
        a.download = `agcl-${lastKind}-manifest.json`;
        a.click();
      }

      return view;
    },
  };
})();
