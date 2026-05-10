# AGCL Console — assets

This directory holds the supporting assets (CSS, JS, images, icon
sprite components) for the **AGCL Console** static UI. The actual
entry point — `index.html` — lives at the **repo root** so the page
can be served as the GitHub Pages root or opened with `file://`
without an extra path segment.

## Where to look

| Path                       | What it is                                                              |
|----------------------------|-------------------------------------------------------------------------|
| `../index.html`            | Entry point. Contains the inline SVG icon sprite and script tags.       |
| `styles.css`               | Monochrome dark/light theme, monospace, chart and Markdown styles.      |
| `js/api.js`                | `API` global — fetch wrapper, bearer auth, SSE reader.                   |
| `js/app.js`                | View router, connection panel, toasts.                                   |
| `js/lib/markdown.js`       | `MD.render(text)` — minimal Markdown renderer (no deps).                |
| `js/lib/charts.js`         | `Chart.line / bars / sparkline / donut / heatmap / gauge`.              |
| `js/views/*.js`            | One file per tab. Each registers `Views.<name>.mount(root)`.            |
| `assets/icon.png`          | Local copy of the repo icon.                                            |

## How to host

Full guide in [`../docs/web-console.md`](../docs/web-console.md).
Quick version: open `index.html` directly, run
`python -m http.server 8088` from the repo root, or push to GitHub
Pages.

## Adding a tab

1. Create `js/views/<name>.js`. Register on
   `window.Views.<name> = { mount(root) { ... return view; } }`.
2. Add a `<script src="web_socket/js/views/<name>.js"></script>` tag
   to `../index.html` above `js/app.js`.
3. Add a sidebar `<a data-view="<name>">…</a>` link inside `<nav>`,
   along with a new `<symbol>` in the SVG sprite if you want a custom
   icon.
4. Add `<name>: "Display Title"` to `VIEW_TITLES` in
   `js/app.js`.

The view's `mount(root)` should return an object with optional
`unmount()` (cleanup, abort streams, clear timers) and `refresh()`
(re-fetch when the topbar Refresh button is clicked).
