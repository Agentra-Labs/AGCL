/* Topics — saved trained-state checkpoints. List + delete. */

(function () {
  const V = window.Views = window.Views || {};
  const { el, loadingNode } = window.H;

  V.topics = {
    mount(root) {
      const view = {
        unmount() {},
        async refresh() { await load(); },
      };

      root.appendChild(el("p", { class: "small" },
        "Trained topic checkpoints saved to STATE_DIR/topics. Each one is a learned latent + token-CE state for a particular topic seed; subsequent turns on the same topic skip retraining."));

      const wrap = el("div", { id: "topics-wrap" });
      wrap.appendChild(loadingNode());
      root.appendChild(wrap);

      async function load() {
        const w = document.getElementById("topics-wrap");
        w.innerHTML = "";
        try {
          const res = await API.get("/node/topics");
          const topics = res.topics || [];
          if (!topics.length) {
            w.appendChild(el("div", { class: "empty" }, "No saved topics yet."));
            return;
          }
          const tbl = el("table", null, [
            el("thead", null, el("tr", null,
              ["topic id", "seed question", "signature", ""].map(h => el("th", { text: h })))),
          ]);
          const tb = el("tbody");
          topics.forEach(t => {
            const sig = t.signature || {};
            const sigStr = Object.keys(sig).map(k => `${k}=${sig[k]}`).join(" · ") || "—";
            tb.appendChild(el("tr", null, [
              el("td", { class: "mono clip", title: t.topic_id }, t.topic_id),
              el("td", { class: "clip", title: t.seed_question || "" }, t.seed_question || "—"),
              el("td", { class: "small mono" }, sigStr),
              el("td", null, [el("button", { class: "btn-err", onClick: () => del(t.topic_id) }, "Delete")]),
            ]));
          });
          tbl.appendChild(tb);
          w.appendChild(tbl);
        } catch (e) { UI.err(e); }
      }

      async function del(id) {
        if (!confirm("Delete topic " + id + "? Future turns on this topic will retrain.")) return;
        try {
          await API.del("/node/topics/" + encodeURIComponent(id));
          UI.ok("Topic deleted.");
          load();
        } catch (e) { UI.err(e); }
      }

      load();
      return view;
    },
  };
})();
