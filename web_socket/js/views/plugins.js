/* Plugins — discovered plugins, commands, and tools. Reload button. */

(function () {
  const V = window.Views = window.Views || {};
  const { el, loadingNode } = window.H;

  V.plugins = {
    mount(root) {
      const view = {
        unmount() {},
        async refresh() { await load(); },
      };

      root.appendChild(el("div", { class: "row", style: "margin-bottom:12px" }, [
        el("p", { class: "small", style: "margin:0;flex:1" },
          "Plugins discovered from Python entry points or local plugins/ directory. Reload to pick up new ones without restarting the node."),
        el("button", { onClick: reload }, "Reload plugins"),
      ]));

      root.appendChild(el("section", { class: "block" }, [
        el("h2", null, "Plugins"),
        el("div", { id: "p-plugins" }, loadingNode()),
      ]));
      root.appendChild(el("section", { class: "block" }, [
        el("h2", null, "Commands"),
        el("div", { id: "p-commands" }, loadingNode()),
      ]));
      root.appendChild(el("section", { class: "block" }, [
        el("h2", null, "Tools"),
        el("div", { id: "p-tools" }, loadingNode()),
      ]));
      root.appendChild(el("section", { class: "block" }, [
        el("h2", null, "Config keys"),
        el("div", { id: "p-config" }, loadingNode()),
      ]));

      async function load() {
        try {
          const res = await API.get("/node/plugins");
          renderPlugins(res.plugins || []);
          renderCommands(res.commands || []);
          renderTools(res.tools || []);
          renderConfig(res.config || []);
        } catch (e) { UI.err(e); }
      }

      function renderPlugins(plugins) {
        const wrap = document.getElementById("p-plugins");
        wrap.innerHTML = "";
        if (!plugins.length) { wrap.appendChild(el("div", { class: "empty" }, "No plugins found.")); return; }
        const tbl = el("table", null, [
          el("thead", null, el("tr", null, ["name","version","module","status"].map(h => el("th", { text: h })))),
        ]);
        const tb = el("tbody");
        plugins.forEach(p => {
          tb.appendChild(el("tr", null, [
            el("td", { class: "mono", text: p.name || "—" }),
            el("td", { text: p.version || "—" }),
            el("td", { class: "mono small", text: p.module || "—" }),
            el("td", null, [p.error ? el("span", { class: "pill err", title: p.error }, "err") : el("span", { class: "pill ok" }, "ok")]),
          ]));
        });
        tbl.appendChild(tb);
        wrap.appendChild(tbl);
      }

      function renderCommands(cmds) {
        const wrap = document.getElementById("p-commands");
        wrap.innerHTML = "";
        if (!cmds.length) { wrap.appendChild(el("div", { class: "empty" }, "No commands.")); return; }
        const tbl = el("table", null, [
          el("thead", null, el("tr", null, ["name","aliases","help"].map(h => el("th", { text: h })))),
        ]);
        const tb = el("tbody");
        cmds.forEach(c => {
          tb.appendChild(el("tr", null, [
            el("td", { class: "mono", text: c.name || "—" }),
            el("td", { class: "small mono", text: (c.aliases || []).join(", ") }),
            el("td", { class: "small", text: c.help || "—" }),
          ]));
        });
        tbl.appendChild(tb);
        wrap.appendChild(tbl);
      }

      function renderTools(tools) {
        const wrap = document.getElementById("p-tools");
        wrap.innerHTML = "";
        if (!tools.length) { wrap.appendChild(el("div", { class: "empty" }, "No tools.")); return; }
        const tbl = el("table", null, [
          el("thead", null, el("tr", null, ["name","description"].map(h => el("th", { text: h })))),
        ]);
        const tb = el("tbody");
        tools.forEach(t => {
          tb.appendChild(el("tr", null, [
            el("td", { class: "mono", text: t.name || "—" }),
            el("td", { class: "small", text: t.description || t.help || "—" }),
          ]));
        });
        tbl.appendChild(tb);
        wrap.appendChild(tbl);
      }

      function renderConfig(cfg) {
        const wrap = document.getElementById("p-config");
        wrap.innerHTML = "";
        if (!cfg.length) { wrap.appendChild(el("div", { class: "empty" }, "No plugin config keys.")); return; }
        const ul = el("ul", { class: "small mono", style: "padding-left:18px" });
        cfg.forEach(k => ul.appendChild(el("li", { text: typeof k === "string" ? k : JSON.stringify(k) })));
        wrap.appendChild(ul);
      }

      async function reload() {
        try {
          await API.post("/node/plugins/reload", {});
          UI.ok("Plugins reloaded.");
          load();
        } catch (e) { UI.err(e); }
      }

      load();
      return view;
    },
  };
})();
