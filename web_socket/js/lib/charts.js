/* SVG chart primitives — pure DOM, no external deps.
 *
 * Each helper returns an <svg> element ready to drop into the DOM.
 * Charts inherit color from CSS variables (--fg, --muted, --warn, --ok, --err)
 * via the `currentColor`-friendly classes in styles.css. */

(function () {
  "use strict";
  const NS = "http://www.w3.org/2000/svg";

  function el(tag, attrs, parent) {
    const e = document.createElementNS(NS, tag);
    if (attrs) for (const k in attrs) e.setAttribute(k, attrs[k]);
    if (parent) parent.appendChild(e);
    return e;
  }
  function txt(parent, x, y, str, attrs) {
    const t = el("text", Object.assign({ x, y }, attrs || {}), parent);
    t.textContent = str;
    return t;
  }

  /* ---------- LINE CHART ----------
   * data: array of numbers, OR array of {t, v} pairs.
   * opts: { width, height, padding, label, yLabel, smooth, showDots, color, fillOpacity } */
  function line(data, opts) {
    opts = Object.assign({ width: 720, height: 180, padding: 28, smooth: false, showDots: false, fillOpacity: 0.12 }, opts || {});
    const W = opts.width, H = opts.height, P = opts.padding;
    const svg = el("svg", { viewBox: `0 0 ${W} ${H}`, width: "100%", class: "chart-line" });
    if (!data || !data.length) {
      txt(svg, W/2, H/2, "no data", { class: "axis", "text-anchor": "middle", "font-size": "11" });
      return svg;
    }
    const vals = data.map(d => typeof d === "number" ? d : Number(d.v));
    const max = Math.max(1e-9, ...vals);
    const min = Math.min(0, ...vals);
    const range = Math.max(1e-9, max - min);
    const x = (i) => P + (i / Math.max(1, vals.length - 1)) * (W - 2*P);
    const y = (v) => H - P - ((v - min) / range) * (H - 2*P);

    // gridlines (4)
    for (let g = 0; g <= 4; g++) {
      const yy = P + g * (H - 2*P) / 4;
      el("line", { x1: P, x2: W - P, y1: yy, y2: yy, class: "gridline" }, svg);
      const v = max - g * range / 4;
      txt(svg, P - 4, yy + 3, fmtCompact(v), { class: "axis", "text-anchor": "end", "font-size": "10" });
    }
    // axis baseline
    el("line", { x1: P, x2: W - P, y1: H - P, y2: H - P, class: "axis" }, svg);

    // path
    const cmds = vals.map((v, i) => `${i === 0 ? "M" : "L"} ${x(i).toFixed(1)} ${y(v).toFixed(1)}`);
    el("path", { d: cmds.join(" "), class: "line-path", fill: "none" }, svg);
    // fill under
    if (opts.fillOpacity > 0) {
      const fillCmds = cmds.slice();
      fillCmds.push(`L ${x(vals.length-1).toFixed(1)} ${(H-P).toFixed(1)}`);
      fillCmds.push(`L ${x(0).toFixed(1)} ${(H-P).toFixed(1)} Z`);
      el("path", { d: fillCmds.join(" "), class: "line-fill", "fill-opacity": opts.fillOpacity }, svg);
    }
    if (opts.showDots) {
      vals.forEach((v, i) => el("circle", { cx: x(i), cy: y(v), r: 2, class: "dot-mark" }, svg));
    }
    if (opts.label) txt(svg, P, 14, opts.label, { class: "axis", "font-size": "11" });
    return svg;
  }

  /* ---------- BAR CHART ----------
   * data: array of numbers OR array of {label, v}.
   * opts: { width, height, padding, label, color } */
  function bars(data, opts) {
    opts = Object.assign({ width: 720, height: 180, padding: 28 }, opts || {});
    const W = opts.width, H = opts.height, P = opts.padding;
    const svg = el("svg", { viewBox: `0 0 ${W} ${H}`, width: "100%", class: "chart-bars" });
    if (!data || !data.length) {
      txt(svg, W/2, H/2, "no data", { class: "axis", "text-anchor": "middle", "font-size": "11" });
      return svg;
    }
    const vals = data.map(d => typeof d === "number" ? d : Number(d.v));
    const max = Math.max(1, ...vals);
    const bw = (W - 2*P) / vals.length;
    el("line", { x1: P, x2: W - P, y1: H - P, y2: H - P, class: "axis" }, svg);
    // 3 horizontal gridlines
    for (let g = 1; g <= 3; g++) {
      const yy = P + g * (H - 2*P) / 3;
      el("line", { x1: P, x2: W - P, y1: yy, y2: yy, class: "gridline" }, svg);
      const v = max * (1 - g / 3);
      txt(svg, P - 4, yy + 3, fmtCompact(v), { class: "axis", "text-anchor": "end", "font-size": "10" });
    }
    vals.forEach((v, i) => {
      const h = ((H - 2*P) * v) / max;
      const r = el("rect", {
        x: P + i*bw + 1,
        y: H - P - h,
        width: Math.max(1, bw - 2),
        height: Math.max(0, h),
        class: "bar",
      }, svg);
      const meta = (typeof data[i] === "object" && data[i] !== null) ? data[i] : { v };
      const tt = el("title", null, r);
      tt.textContent = (meta.label !== undefined ? meta.label + " — " : "") + fmtCompact(v) + (meta.tooltip ? " · " + meta.tooltip : "");
    });
    if (opts.label) txt(svg, P, 14, opts.label, { class: "axis", "font-size": "11" });
    return svg;
  }

  /* ---------- SPARKLINE ----------
   * Compact line, no axis. opts: { width, height } */
  function sparkline(data, opts) {
    opts = Object.assign({ width: 160, height: 32 }, opts || {});
    const W = opts.width, H = opts.height;
    const svg = el("svg", { viewBox: `0 0 ${W} ${H}`, width: W, height: H, class: "chart-spark" });
    const vals = (data || []).map(d => typeof d === "number" ? d : Number(d.v));
    if (!vals.length) return svg;
    const max = Math.max(1e-9, ...vals);
    const min = Math.min(0, ...vals);
    const range = Math.max(1e-9, max - min);
    const x = (i) => (i / Math.max(1, vals.length - 1)) * W;
    const y = (v) => H - 2 - ((v - min) / range) * (H - 4);
    const cmds = vals.map((v, i) => `${i === 0 ? "M" : "L"} ${x(i).toFixed(1)} ${y(v).toFixed(1)}`);
    el("path", { d: cmds.join(" "), class: "line-path", fill: "none" }, svg);
    return svg;
  }

  /* ---------- DONUT CHART ----------
   * slices: array of {label, v, color?}.
   * opts: { size, thickness, label } */
  function donut(slices, opts) {
    opts = Object.assign({ size: 160, thickness: 22 }, opts || {});
    const S = opts.size, R = S/2, T = opts.thickness, IR = R - T;
    const svg = el("svg", { viewBox: `0 0 ${S} ${S}`, width: S, height: S, class: "chart-donut" });
    const total = (slices || []).reduce((a, b) => a + Number(b.v || 0), 0);
    if (!total) {
      el("circle", { cx: R, cy: R, r: R - T/2, fill: "none", "stroke-width": T, class: "axis" }, svg);
      txt(svg, R, R + 3, "—", { class: "axis", "text-anchor": "middle", "font-size": "12" });
      return svg;
    }
    let a0 = -Math.PI/2;
    slices.forEach((s, i) => {
      const frac = Number(s.v) / total;
      const a1 = a0 + frac * Math.PI * 2;
      const big = (a1 - a0) > Math.PI ? 1 : 0;
      const x0 = R + Math.cos(a0) * (R - T/2);
      const y0 = R + Math.sin(a0) * (R - T/2);
      const x1 = R + Math.cos(a1) * (R - T/2);
      const y1 = R + Math.sin(a1) * (R - T/2);
      const path = el("path", {
        d: `M ${x0} ${y0} A ${R - T/2} ${R - T/2} 0 ${big} 1 ${x1} ${y1}`,
        fill: "none",
        "stroke-width": T,
        "stroke-opacity": 0.35 + 0.65 * (1 - i / Math.max(1, slices.length - 1)),
        class: "donut-slice",
      }, svg);
      const tt = el("title", null, path);
      tt.textContent = `${s.label || ""} — ${fmtCompact(s.v)} (${(frac*100).toFixed(1)}%)`;
      a0 = a1;
    });
    txt(svg, R, R - 4, fmtCompact(total), { class: "axis", "text-anchor": "middle", "font-size": "13", "font-weight": "600" });
    if (opts.label) txt(svg, R, R + 12, opts.label, { class: "axis", "text-anchor": "middle", "font-size": "10" });
    return svg;
  }

  /* ---------- DONUT LEGEND ---------- */
  function donutLegend(slices) {
    const total = (slices || []).reduce((a, b) => a + Number(b.v || 0), 0);
    const wrap = document.createElement("div");
    wrap.className = "chart-legend";
    (slices || []).forEach((s, i) => {
      const op = (0.35 + 0.65 * (1 - i / Math.max(1, slices.length - 1))).toFixed(2);
      const row = document.createElement("div");
      row.className = "legend-row";
      row.innerHTML =
        `<span class="legend-dot" style="opacity:${op}"></span>` +
        `<span class="legend-lbl">${escapeHtml(s.label || "—")}</span>` +
        `<span class="legend-val">${fmtCompact(s.v)} ${total ? "("+(s.v/total*100).toFixed(1)+"%)" : ""}</span>`;
      wrap.appendChild(row);
    });
    return wrap;
  }

  /* ---------- HEATMAP ----------
   * values: 1-D array (auto reshape) OR 2-D array [rows][cols].
   * opts: { width, height, label, cols } */
  function heatmap(values, opts) {
    opts = Object.assign({ width: 720, height: 120, padding: 20 }, opts || {});
    const W = opts.width, H = opts.height, P = opts.padding;
    const svg = el("svg", { viewBox: `0 0 ${W} ${H}`, width: "100%", class: "chart-heat" });
    if (!values || (Array.isArray(values) && !values.length)) {
      txt(svg, W/2, H/2, "no data", { class: "axis", "text-anchor": "middle", "font-size": "11" });
      return svg;
    }
    let grid;
    if (Array.isArray(values[0])) grid = values;
    else {
      const cols = opts.cols || Math.ceil(Math.sqrt(values.length));
      const rows = Math.ceil(values.length / cols);
      grid = [];
      for (let r = 0; r < rows; r++) grid.push(values.slice(r*cols, (r+1)*cols));
    }
    const flat = [];
    grid.forEach(r => r.forEach(v => flat.push(Number(v))));
    let min = Math.min(...flat), max = Math.max(...flat);
    if (max === min) max = min + 1;
    const rows = grid.length, cols = grid[0].length;
    const cw = (W - 2*P) / cols;
    const ch = (H - 2*P) / rows;
    grid.forEach((row, r) => row.forEach((v, c) => {
      const t = (Number(v) - min) / (max - min);   // 0..1
      const op = 0.08 + 0.92 * t;
      const cell = el("rect", {
        x: P + c*cw,
        y: P + r*ch,
        width: cw - 0.5,
        height: ch - 0.5,
        class: "heat-cell",
        "fill-opacity": op.toFixed(3),
      }, svg);
      const tt = el("title", null, cell);
      tt.textContent = `[${r},${c}] = ${Number(v).toFixed(4)}`;
    }));
    txt(svg, P, 12, `${rows}×${cols} · range [${min.toFixed(3)}, ${max.toFixed(3)}]`, { class: "axis", "font-size": "10" });
    return svg;
  }

  /* ---------- GAUGE ----------
   * value 0..1. opts: { size, label, color } */
  function gauge(value, opts) {
    opts = Object.assign({ size: 140, label: "" }, opts || {});
    const S = opts.size, R = S/2 - 8, CX = S/2, CY = S/2 + 6;
    const svg = el("svg", { viewBox: `0 0 ${S} ${S/1.5}`, width: S, height: S/1.5, class: "chart-gauge" });
    const v = Math.min(1, Math.max(0, Number(value) || 0));
    // semicircle background
    el("path", {
      d: `M ${CX-R} ${CY} A ${R} ${R} 0 0 1 ${CX+R} ${CY}`,
      fill: "none", "stroke-width": 8, class: "axis",
    }, svg);
    // value arc
    const a = Math.PI * (1 - v);
    const x = CX - R * Math.cos(Math.PI - a);
    const y = CY - R * Math.sin(Math.PI - a);
    const big = v > 0.5 ? 1 : 0;
    el("path", {
      d: `M ${CX-R} ${CY} A ${R} ${R} 0 ${big} 1 ${x.toFixed(1)} ${y.toFixed(1)}`,
      fill: "none", "stroke-width": 8, class: "line-path",
    }, svg);
    txt(svg, CX, CY - 4, (v * 100).toFixed(1) + "%", { class: "axis", "text-anchor": "middle", "font-size": "16", "font-weight": "600" });
    if (opts.label) txt(svg, CX, CY + 14, opts.label, { class: "axis", "text-anchor": "middle", "font-size": "10" });
    return svg;
  }

  function fmtCompact(n) {
    if (n === null || n === undefined || isNaN(n)) return "—";
    const abs = Math.abs(n);
    if (abs >= 1e9) return (n / 1e9).toFixed(2) + "B";
    if (abs >= 1e6) return (n / 1e6).toFixed(2) + "M";
    if (abs >= 1e3) return (n / 1e3).toFixed(1) + "k";
    if (abs < 0.01 && abs > 0) return n.toExponential(1);
    return Math.round(n * 100) / 100 + "";
  }
  function escapeHtml(s) {
    return String(s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  }

  window.Chart = { line, bars, sparkline, donut, donutLegend, heatmap, gauge, fmtCompact };
})();
