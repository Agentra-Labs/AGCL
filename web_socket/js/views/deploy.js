/* Deploy Manifests — Docker / Kubernetes / GCP Cloud Run. */

(function () {
  const V = window.Views = window.Views || {};
  const { el, loadingNode } = window.H;

  V.deploy = {
    mount(root) {
      const view = { unmount() {}, refresh() {} };

      root.appendChild(el("p", { class: "small" },
        "Generate deployment manifests directly from the running node. Output is JSON describing files / chart / deploy command — copy and run on the target host."));

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
            el("button", { class: "btn-ghost", onClick: () => copyOut() }, "Copy output"),
          ]),
        ]),
      ]));

      root.appendChild(el("section", { class: "block" }, [
        el("h2", null, "Output"),
        el("pre", { id: "dep-out-pre", class: "card mono small", style: "white-space:pre-wrap;max-height:60vh;overflow:auto" }),
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
        const out = document.getElementById("dep-out-pre");
        out.textContent = "generating…";
        try {
          const res = await API.post(`/node/toolkit/manifest/${kind}`, buildBody());
          out.textContent = JSON.stringify(res, null, 2);
          UI.ok("Manifests generated.");
        } catch (e) { out.textContent = ""; UI.err(e); }
      }

      function copyOut() {
        const t = document.getElementById("dep-out-pre").textContent || "";
        if (!t) return;
        navigator.clipboard.writeText(t).then(() => UI.ok("Copied."), () => UI.err(new Error("Copy failed")));
      }

      return view;
    },
  };
})();
