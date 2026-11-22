// Panel renderers. Each catalog panel definition {id, type, title, cls, ...} becomes one panel
// that reads a RunStore. Panels never compute scientific quantities beyond display transforms
// (a log scale, a normalisation for colour); every number shown comes from the telemetry.
import { Plot, Heat, Sketch, fmt, css, color, PALETTE, seqColor } from "./charts.js";

const el = (tag, attrs = {}, ...kids) => {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") e.className = v; else if (k === "html") e.innerHTML = v; else if (k.startsWith("on")) e.addEventListener(k.slice(2), v);
    else if (v !== undefined && v !== null && v !== false) e.setAttribute(k, v === true ? "" : v);
  }
  for (const kid of kids.flat()) if (kid !== null && kid !== undefined) e.append(kid.nodeType ? kid : document.createTextNode(String(kid)));
  return e;
};
export { el };

export function shell(def, extraTools = []) {
  const tools = el("div", { class: "tools" }, ...extraTools);
  const header = el("header", {}, el("h3", { title: def.title }, def.title), def.cls ? el("span", { class: `cls ${def.cls}`, title: CLASS_HELP[def.cls] || "" }, def.cls) : null, tools);
  const body = el("div", { class: "body flush" });
  const panel = el("section", { class: `panel span-${def.span || 6}` }, header, body);
  if (def.note) panel.append(el("div", { class: "note" }, def.note));
  return { panel, body, tools, header };
}
export const CLASS_HELP = {
  simulated: "state or output of the scientific computation itself, read without changing it",
  derived: "computed by the laboratory from simulated data with RNGLab's own functions",
  reference: "analytic or exact value; draws no random numbers",
  recorded: "read from a stored study or run file",
  illustrative: "visual aid only; carries no measurement",
};
const toolBtn = (label, title, fn) => el("button", { title, onclick: fn }, label);

function csvDownload(name, rows) {
  const blob = new Blob([rows.map((r) => r.join(",")).join("\n")], { type: "text/csv" });
  const a = document.createElement("a"); a.href = URL.createObjectURL(blob); a.download = `${name}.csv`; a.click(); setTimeout(() => URL.revokeObjectURL(a.href), 2000);
}

function plotPanel(def, opts, build) {
  const plot = { current: null };
  const sh = shell(def, [toolBtn("⤢", "reset zoom (double-click)", () => plot.current.reset()),
    toolBtn("csv", "export the plotted series as CSV", () => { const rows = [["series", "x", "y", "y2"]]; for (const s of plot.current.series) s.x.forEach((x, i) => rows.push([s.name, x, s.y[i], s.y2?.[i] ?? ""])); csvDownload(def.id, rows); }),
    toolBtn("png", "save image", () => plot.current.toPNG(def.id))]);
  const legend = el("div", { class: "legend" });
  plot.current = new Plot(sh.body, { sync: opts.sync, size: def.size || "", xname: def.x || "x", xlabel: def.xlabel || def.x, ylabel: def.ylabel, logx: def.logx, logy: def.logy, ylim: def.ylim });
  sh.panel.insertBefore(legend, sh.panel.querySelector(".note"));
  let lastVersion = -1;
  return {
    el: sh.panel, plot: plot.current,
    update(store, force) {
      if (!force && store.version === lastVersion) return; lastVersion = store.version;
      const out = build(store, plot.current);
      if (!out) return;
      plot.current.setData(out);
      const names = (out.series || []).filter((s) => !s.noLegend).map((s) => `<span style="--c:${color(s.color)}">${s.name}</span>`);
      for (const r of out.refs || []) if (r.label) names.push(`<span style="--c:${color(r.color || "var(--reference)")}">${r.label}</span>`);
      legend.innerHTML = names.join("");
    },
    destroy() { plot.current.destroy(); },
  };
}

const refLines = (def, store) => (def.refs || []).map((name, i) => ({ y: store.meta.references?.[name], label: `${name.replaceAll("_", " ")} ${fmt(store.meta.references?.[name], 7)}`, color: i ? "var(--s5)" : "var(--reference)", noRange: false })).filter((r) => Number.isFinite(r.y));

function seriesNames(def, store) {
  const out = [];
  for (const name of def.series || []) {
    if (name.endsWith("*")) { const p = name.slice(0, -1); for (const k of Object.keys(store.series).sort()) if (k.startsWith(p)) out.push(k); }
    else out.push(name);
  }
  return out;
}
const LABELS = { cv: "E_CV", d: "E_TD − E_CV", acc: "acceptance", centroid: "centroid x̄", rg: "R_g", e: "E/N", absm: "|m|",
  abs_err: "|π̂ − π|", se: "standard error", statC: "conventional", statA: "application", uphill: "uphill fraction", cons: "Σn − N",
  chunk_events: "events", live_a: "option A", live_b: "option B", live_c: "option C" };

// ---------------------------------------------------------------------------------------------
export const PANELS = {
  timeseries(def, opts) {
    return plotPanel(def, opts, (store) => {
      const names = seriesNames(def, store);
      const series = names.map((n, i) => ({ name: store.meta.series_labels?.[n] || LABELS[n] || n, x: store.series[n]?.x || [], y: store.series[n]?.y || [], color: PALETTE[i % PALETTE.length], kind: def.kind || "line" }));
      return { series, refs: refLines(def, store), vlines: eventLines(store) };
    });
  },
  interval(def, opts) {
    return plotPanel(def, opts, (store) => {
      const pts = store.series.ci || [];
      const x = pts.map((p) => p.x), mean = pts.map((p) => p.mean);
      const series = [
        { name: "95% interval", kind: "band", x, y: pts.map((p) => p.lo ?? NaN), y2: pts.map((p) => p.hi ?? NaN), color: "var(--s1)", noLegend: false },
      ];
      if (pts.some((p) => Number.isFinite(p.glo))) series.push({ name: "Geyer interval", kind: "band", x, y: pts.map((p) => p.glo ?? NaN), y2: pts.map((p) => p.ghi ?? NaN), color: "var(--s3)", alpha: 0.12 });
      series.push({ name: "running mean", x, y: mean, color: "var(--s1)", width: 1.6 });
      const off = pts.filter((p) => p.official);
      if (off.length) series.push({ name: "official final interval", kind: "errbar", x: off.map((p) => p.x), y: off.map((p) => p.mean), lo: off.map((p) => p.lo), hi: off.map((p) => p.hi), color: "var(--s4)", radius: 4, width: 2 });
      return { series, refs: refLines(def, store), vlines: eventLines(store) };
    });
  },
  monitor(def, opts) {
    return plotPanel(def, opts, (store) => {
      const n = def.series[0]; const s = store.series[n] || { x: [], y: [] };
      const tau = store.meta.thresholds?.[def.threshold];
      const arm = def.threshold === "tau_C" ? "C" : "A";
      const alarms = store.events.filter((e) => e.kind === "alarm" && e.data?.arm === arm);
      return { series: [{ name: LABELS[n] || n, x: s.x, y: s.y, color: arm === "C" ? "var(--s2)" : "var(--s1)", kind: "step", width: 1.5 }],
        refs: Number.isFinite(tau) ? [{ y: tau, label: `τ_${arm} = ${fmt(tau, 6)} (frozen calibration)`, color: "var(--bad)" }] : [],
        vlines: eventLines(store), marks: alarms.map((a) => ({ x: a.sim?.value, y: a.data?.statistic, color: "var(--bad)" })) };
    });
  },
  pmatrix(def, opts) { return heatPanel(def, opts, (store) => {
    const m = store.matrix.conv_p; if (!m) return null;
    return { rows: m.rows, cols: m.cols, values: m.values, vmin: 0, vmax: Math.max(4, ...m.values.flat().filter(Number.isFinite)), fmt: (v) => `−log₁₀ p = ${fmt(v, 4)}` };
  }); },
  matrix(def, opts) { return heatPanel(def, opts, (store) => {
    const key = def.source.split(":")[1]; const m = store.matrix[key]; if (!m) return null;
    return { rows: m.rows, cols: m.cols, values: m.values, vmin: 0, vmax: 1, text: m.values.map((r) => r.map((v) => Number.isFinite(v) ? v.toFixed(2) : "")), fmt: (v) => fmt(v, 4), hint: m.counts ? (r, c) => `${m.counts[r][c]} of ${m.n?.[r] ?? "?"}` : null };
  }); },
  ring(def, opts) { return sketchPanel(def, opts, drawRing, (store) => store.state.path ? { path: store.state.path, beta: store.meta.beta, sweep: store.scalars.sweep } : null, "square"); },
  scatter(def, opts) { return sketchPanel(def, opts, drawScatter, (store) => ({ points: store.append.points || [], scalars: store.scalars }), "square"); },
  lattice(def, opts) { return sketchPanel(def, opts, drawLattice, (store) => store.state.spins ? { spins: store.state.spins, sweep: store.scalars.sweep } : null, "square"); },
  hist(def, opts) {
    const key = def.source.split(":")[1];
    if (key === "uniform") return streamPanel(def, opts, "hist");
    return plotPanel({ ...def }, opts, (store) => {
      const h = store.hist[key]; if (!h) return null;
      const series = []; let i = 0;
      for (const [name, v] of Object.entries(h)) {
        const centers = v.edges.slice(0, -1).map((e, k) => (e + v.edges[k + 1]) / 2);
        series.push({ name, x: centers, y: v.counts, color: PALETTE[i % 8], kind: "step", width: 1.5 });
        if (v.expected) series.push({ name: `${name} expected`, x: centers, y: v.expected, color: PALETTE[i % 8], dash: [4, 3], width: 1 });
        i++;
      }
      return { series };
    });
  },
  density(def, opts) { return streamPanel(def, opts, "density"); },
  bars(def, opts) { return streamPanel(def, opts, "bits"); },
  estimates(def, opts) {
    return plotPanel({ ...def, x: "option" }, opts, (store) => {
      const t = store.table.estimates; if (!t) return null;
      const series = t.conditions.map((c, k) => ({ name: c.name, kind: "errbar", x: t.options.map((_, i) => i + 1 + (k - (t.conditions.length - 1) / 2) * 0.12), y: c.mean, lo: c.mean.map((m, i) => m - 2 * c.sd[i]), hi: c.mean.map((m, i) => m + 2 * c.sd[i]), color: PALETTE[k % 8], radius: 3.5 }));
      series.push({ name: "true mean", kind: "points", x: t.options.map((_, i) => i + 1), y: t.true_means, color: "var(--reference)", radius: 2 });
      return { series };
    });
  },
  rate(def, opts) {
    return plotPanel({ ...def, x: "level", ylim: [0, 1.02] }, opts, (store) => {
      const t = store.table.rates; if (!t) return null;
      return { series: [{ name: "P(correct)", kind: "errbar", x: t.x, y: t.rate, lo: t.lo, hi: t.hi, color: "var(--s1)", radius: 3.5 }, { name: "", kind: "line", x: t.x, y: t.rate, color: "var(--s1)", noLegend: true, noTip: true, alpha: 0.5 }] };
    });
  },
  populations(def, opts) {
    return plotPanel({ ...def, logy: def.logy }, opts, (store) => {
      const names = (store.meta.scheme?.nuclides || []).map((n) => n.name);
      const series = []; const t = store.meta.ref_times || [];
      names.forEach((n, i) => {
        const c = PALETTE[i % 8]; const exp = store.meta.expected?.[n];
        if (store.meta.band_lo?.[n]) series.push({ name: `${n} 95% band`, kind: "band", x: t, y: store.meta.band_lo[n], y2: store.meta.band_hi[n], color: c, alpha: 0.13, noLegend: true });
        if (exp) series.push({ name: `${n} expected`, x: t, y: exp, color: c, dash: [5, 3], width: 1, noLegend: true });
        const s = store.series[`pop_${n}`]; if (s) series.push({ name: n, x: s.x, y: s.y, color: c, kind: s.x.length > 300 ? "line" : "points", radius: 2.3, width: 1.4 });
      });
      return { series, vlines: store.state.playhead !== undefined ? [{ x: store.state.playhead, color: "var(--muted)" }] : [] };
    });
  },
  stack(def, opts) {
    return plotPanel(def, opts, (store) => {
      const names = store.meta.channel_names || [];
      return { series: names.map((n, i) => ({ name: n, x: store.series[`dec_${i}`]?.x || [], y: store.series[`dec_${i}`]?.y || [], color: PALETTE[i % 8], kind: "step" })) };
    });
  },
  chain(def) { return chainPanel(def); },
  raster(def, opts) { return rasterPanel(def, opts); },
};

function eventLines(store) {
  const out = [];
  for (const e of store.events) {
    if (e.kind === "onset") out.push({ x: e.sim?.value, label: "onset", color: "var(--warn)" });
  }
  return out;
}

function heatPanel(def, opts, build) {
  const sh = shell(def);
  const heat = new Heat(sh.body, { size: def.size || "" });
  let lastVersion = -1;
  return { el: sh.panel, update(store, force) { if (!force && store.version === lastVersion) return; lastVersion = store.version; const m = build(store); if (m) heat.setData(m); }, destroy() { heat.destroy(); } };
}

function sketchPanel(def, opts, painter, pick, size) {
  const sk = { current: null };
  const sh = shell(def, [toolBtn("png", "save image", () => sk.current.toPNG(def.id))]);
  sk.current = new Sketch(sh.body, painter, { size: size || "" });
  let lastVersion = -1;
  return { el: sh.panel, update(store, force) { if (!force && store.version === lastVersion) return; lastVersion = store.version; const d = pick(store); if (d) sk.current.setData(d); }, destroy() { sk.current.destroy(); } };
}

// --- RNG stream panels: one role at a time, chosen in the header ------------------------------
function streamPanel(def, opts, kind) {
  const select = el("select", { class: "pill", title: "role (stream) shown" });
  const sh = shell(def, [select]);
  let role = null, lastVersion = -1, painterData = null;
  const info = el("div", { class: "note" });
  sh.panel.append(info);
  let view;
  if (kind === "hist") view = new Plot(sh.body, { size: "short", xname: "u", xlabel: "u", ylim: undefined });
  else if (kind === "density") view = new Heat(sh.body, { size: "short" });
  else view = new Plot(sh.body, { size: "short", xname: "bit", xlabel: "bit (0 = LSB)", ylim: [0.3, 0.7] });
  if (def.role) { role = def.role; select.classList.add("hidden"); }
  select.addEventListener("change", () => { role = select.value; lastVersion = -1; update(opts._store, true); });
  function update(store, force) {
    if (!store || (!force && store.version === lastVersion)) return; lastVersion = store.version;
    const roles = Object.keys(store.streams || {});
    if (roles.join() !== [...select.options].map((o) => o.value).join()) { select.replaceChildren(...roles.map((r) => el("option", { value: r }, r))); }
    if (!def.role && (!role || !roles.includes(role))) role = roles.find((r) => !r.endsWith("/base")) || roles[0];
    select.value = role || "";
    const s = store.streams?.[role]; if (!s) return;
    if (kind === "hist") {
      const n = s.uniform_hist.length; const x = s.uniform_hist.map((_, i) => (i + 0.5) / n); const expected = s.words / n;
      const sd = Math.sqrt(expected * (1 - 1 / n));
      view.setData({ series: [{ name: "count", x, y: s.uniform_hist, kind: "step", color: "var(--s1)", width: 1.5 },
        { name: "±3σ", kind: "band", x, y: x.map(() => expected - 3 * sd), y2: x.map(() => expected + 3 * sd), color: "var(--reference)", alpha: 0.12 }],
        refs: [{ y: expected, label: "uniform expectation", color: "var(--reference)" }] });
      info.textContent = `${role}: ${s.words.toLocaleString()} words · mean ${fmt(s.uniform_mean, 6)} (½) · var ${fmt(s.uniform_var, 6)} (1/12 = 0.0833333) · χ²(${s.uniform_dof}) = ${fmt(s.uniform_chi2, 5)}${Number.isFinite(s.uniform_chi2_p) ? ` · p = ${fmt(s.uniform_chi2_p, 3)}` : ""}`;
    } else if (kind === "density") {
      if (!s.lag_hist) return; const total = s.lag_hist.flat().reduce((a, b) => a + b, 0); const exp = total / (s.lag_hist.length ** 2);
      const rows = s.lag_hist.map((_, i) => (i === 0 || i === s.lag_hist.length - 1) ? (i ? "1" : "0") : ""); const cols = rows;
      view.setData({ rows: rows.slice().reverse(), cols, values: s.lag_hist.slice().reverse().map((r) => r.map((v) => exp > 0 ? (v - exp) / Math.sqrt(exp) : 0)), diverging: true, vmin: -4, vmax: 4, fmt: (v) => `standardised count ${fmt(v, 3)}` });
      info.textContent = `${role}: lag-1 correlation of consecutive uniforms ${fmt(s.lag1_corr, 4)} · colour = (count − expected)/√expected`;
    } else {
      if (!s.bit_fraction) return; const n = s.bytes; const sd = Math.sqrt(0.25 / Math.max(1, n));
      const x = s.bit_fraction.map((_, i) => i);
      view.setData({ series: [{ name: "fraction of ones", kind: "errbar", x, y: s.bit_fraction, lo: s.bit_fraction, hi: s.bit_fraction, color: "var(--s2)", radius: 4 },
        { name: "±3σ (binomial)", kind: "band", x: [-0.5, 7.5], y: [0.5 - 3 * sd, 0.5 - 3 * sd], y2: [0.5 + 3 * sd, 0.5 + 3 * sd], color: "var(--reference)", alpha: 0.15 }],
        refs: [{ y: 0.5, label: "½", color: "var(--reference)" }] });
      info.textContent = `${role}: ${n.toLocaleString()} bytes; a stuck bit sits at 0 or 1`;
    }
  }
  return { el: sh.panel, update, destroy() { view.destroy(); } };
}

// --- painters ---------------------------------------------------------------------------------
function drawRing(ctx, w, h, d) {
  const path = d.path; const M = path.length; if (!M) return;
  const half = Math.min(w * 0.55, h); const cx = half / 2 + 4, cy = h / 2, R = Math.min(half, h) * 0.30;
  const ext = Math.max(1.5, ...path.map(Math.abs)); const k = (Math.min(half, h) * 0.17) / ext;
  ctx.strokeStyle = css("--line2"); ctx.setLineDash([3, 4]); ctx.beginPath(); ctx.arc(cx, cy, R, 0, 2 * Math.PI); ctx.stroke(); ctx.setLineDash([]);
  const pts = path.map((x, j) => { const a = -Math.PI / 2 + (2 * Math.PI * j) / M; const r = R + x * k; return [cx + r * Math.cos(a), cy + r * Math.sin(a)]; });
  ctx.strokeStyle = css("--s1"); ctx.lineWidth = 1.4; ctx.beginPath(); pts.forEach(([x, y], i) => i ? ctx.lineTo(x, y) : ctx.moveTo(x, y)); ctx.closePath(); ctx.stroke();
  pts.forEach(([x, y], j) => { ctx.fillStyle = seqColor(0.15 + 0.8 * j / M); ctx.beginPath(); ctx.arc(x, y, 3.2, 0, 2 * Math.PI); ctx.fill(); });
  ctx.fillStyle = css("--faint"); ctx.font = `10px ${css("--mono")}`; ctx.textAlign = "center";
  ctx.fillText("imaginary time τ around the ring", cx, h - 8); ctx.fillText("radius offset = x_j", cx, 12);
  // right: beads in the harmonic well V(x) = x²/2
  const x0 = half + 18, x1 = w - 10, yb = h - 26, yt = 24; const X = (x) => x0 + (x + ext) / (2 * ext) * (x1 - x0);
  const vmax = ext * ext / 2; const Y = (v) => yb - (v / vmax) * (yb - yt);
  ctx.strokeStyle = css("--reference"); ctx.lineWidth = 1.2; ctx.beginPath();
  for (let i = 0; i <= 80; i++) { const x = -ext + (2 * ext * i) / 80; i ? ctx.lineTo(X(x), Y(x * x / 2)) : ctx.moveTo(X(x), Y(x * x / 2)); } ctx.stroke();
  const mean = path.reduce((a, b) => a + b, 0) / M;
  path.forEach((x, j) => { ctx.fillStyle = seqColor(0.15 + 0.8 * j / M); ctx.beginPath(); ctx.arc(X(x), Y(x * x / 2), 3, 0, 2 * Math.PI); ctx.fill(); });
  ctx.strokeStyle = css("--s2"); ctx.setLineDash([4, 3]); ctx.beginPath(); ctx.moveTo(X(mean), yt); ctx.lineTo(X(mean), yb); ctx.stroke(); ctx.setLineDash([]);
  ctx.fillStyle = css("--faint"); ctx.textAlign = "center"; ctx.fillText("V(x) = x²/2 (reference); dashed: centroid", (x0 + x1) / 2, h - 8);
  ctx.textAlign = "left"; ctx.fillStyle = css("--muted"); ctx.fillText(`M = ${M}${d.sweep !== undefined ? ` · after sweep ${d.sweep}` : ""}`, x0, 12);
}
function drawScatter(ctx, w, h, d) {
  const s = Math.min(w, h) - 30, ox = (w - s) / 2, oy = 8;
  ctx.strokeStyle = css("--line2"); ctx.strokeRect(ox + 0.5, oy + 0.5, s, s);
  ctx.strokeStyle = css("--reference"); ctx.lineWidth = 1.3; ctx.beginPath(); ctx.arc(ox, oy + s, s, -Math.PI / 2, 0); ctx.stroke();
  const inC = css("--s1"), outC = css("--s2"); const pts = d.points; const n = pts.length;
  for (let i = 0; i < n; i++) { const [x, y, ins] = pts[i]; ctx.fillStyle = ins ? inC : outC; ctx.globalAlpha = 0.25 + 0.75 * (i / n); ctx.fillRect(ox + x * s - 1, oy + (1 - y) * s - 1, 2, 2); }
  ctx.globalAlpha = 1; ctx.fillStyle = css("--muted"); ctx.font = `11px ${css("--mono")}`; ctx.textAlign = "left";
  const sc = d.scalars || {}; ctx.fillText(`last ${n.toLocaleString()} points · π̂ = ${fmt(sc.estimate, 7)} · n = ${(sc.samples ?? 0).toLocaleString()}`, ox, oy + s + 16);
}
function drawLattice(ctx, w, h, d) {
  const sp = d.spins; const L = sp.length; const s = Math.min(w, h - 18); const c = s / L; const ox = (w - s) / 2;
  const up = css("--s1"), dn = css("--panel2");
  for (let i = 0; i < L; i++) for (let j = 0; j < L; j++) { ctx.fillStyle = sp[i][j] > 0 ? up : dn; ctx.fillRect(ox + j * c, i * c, Math.ceil(c), Math.ceil(c)); }
  ctx.strokeStyle = css("--line2"); ctx.strokeRect(ox + 0.5, 0.5, s, s);
  const m = sp.flat().reduce((a, b) => a + b, 0) / (L * L);
  ctx.fillStyle = css("--muted"); ctx.font = `11px ${css("--mono")}`; ctx.fillText(`L = ${L} · m = ${fmt(m, 4)}${d.sweep !== undefined ? ` · after sweep ${d.sweep}` : ""} · light = +1`, ox, h - 4);
}

// --- decay chain diagram (SVG) ----------------------------------------------------------------
const MODE_COLORS = { alpha: "var(--s2)", "beta-": "var(--s1)", "beta+": "var(--s5)", ec: "var(--s4)", it: "var(--s3)", "beta-n": "var(--s6)" };
function chainPanel(def) {
  const sh = shell(def); sh.body.classList.remove("flush"); sh.body.classList.add("chain");
  let lastKey = null;
  return { el: sh.panel, destroy() {}, update(store) {
    const scheme = store.meta.scheme; if (!scheme) return; const key = JSON.stringify(scheme) + JSON.stringify(store.state.final_counts || store.scalars.populations || {});
    if (key === lastKey) return; lastKey = key;
    const nuc = scheme.nuclides; const zs = nuc.map((n) => n.z), as = nuc.map((n) => n.a);
    const zmin = Math.min(...zs), zmax = Math.max(...zs), amin = Math.min(...as), amax = Math.max(...as);
    const W = 520, H = 260, bw = 76, bh = 34;
    const pos = {}; const used = {};
    nuc.forEach((n) => { const x = 20 + ((n.z - zmin) / Math.max(1, zmax - zmin)) * (W - bw - 40); let y = 16 + ((amax - n.a) / Math.max(1, amax - amin)) * (H - bh - 40);
      const k = `${Math.round(x)}:${Math.round(y)}`; used[k] = (used[k] || 0) + 1; if (used[k] > 1) y += (used[k] - 1) * (bh + 6); pos[n.name] = [x + (n.level ? 10 : 0), y + (n.level ? -10 : 0)]; });
    const counts = store.scalars.populations || store.state.final_counts || {};
    let svg = `<svg viewBox="0 0 ${W} ${H}" width="100%" height="${H}"><defs><marker id="ah" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse"><path d="M0,0L10,5L0,10z" fill="${css("--muted")}"/></marker></defs>`;
    nuc.forEach((n) => (n.channels || []).forEach((c) => { const [x1, y1] = pos[n.name], [x2, y2] = pos[c.daughter] || [x1, y1];
      const col = color(MODE_COLORS[c.mode] || "var(--muted)"); const mx = (x1 + x2) / 2 + bw / 2, my = (y1 + y2) / 2 + bh / 2;
      svg += `<line x1="${x1 + bw / 2}" y1="${y1 + bh / 2}" x2="${x2 + bw / 2}" y2="${y2 + bh / 2}" stroke="${col}" stroke-width="1.6" marker-end="url(#ah)" opacity=".85"/>`;
      svg += `<text x="${mx}" y="${my - 3}" font-size="9" fill="${col}" text-anchor="middle">${c.mode} ${c.branching}</text>`; }));
    nuc.forEach((n) => { const [x, y] = pos[n.name]; const stable = !n.channels || !n.channels.length;
      svg += `<g><rect x="${x}" y="${y}" width="${bw}" height="${bh}" rx="5" fill="${css("--panel2")}" stroke="${stable ? css("--line2") : css("--accent")}"/>`;
      svg += `<text x="${x + bw / 2}" y="${y + 13}" font-size="10.5" fill="${css("--text")}" text-anchor="middle">${n.name}</text>`;
      svg += `<text x="${x + bw / 2}" y="${y + 26}" font-size="9" fill="${css("--muted")}" text-anchor="middle">${stable ? "stable" : `T½ ${fmt(n.half_life, 4)}`}${counts[n.name] !== undefined ? ` · ${fmt(counts[n.name], 4)}` : ""}</text></g>`; });
    svg += `</svg>`; sh.body.innerHTML = svg + `<div class="note" style="padding:4px 0 0">x: Z, y: A (synthetic nuclides, Z ≥ 140). Arrow label: mode and branching. Counts: latest populations.</div>`;
  } };
}

// --- emission raster with a playback cursor in simulated time ---------------------------------
const PARTICLES = ["alpha", "electron", "positron", "gamma", "xray", "neutron"];
function rasterPanel(def, opts) {
  const slider = el("input", { type: "range", min: 0, max: 1000, value: 1000, style: "width:160px" });
  const play = el("button", { title: "play the emissions in simulated time" }, "▶");
  const sh = shell(def, [play, slider]);
  let data = null, cursor = 1, timer = null;
  const sk = new Sketch(sh.body, (ctx, w, h, d) => {
    const em = d.em; const T = d.horizon || 1; const tc = cursor * T; const rows = PARTICLES.length; const top = 6, left = 64, rh = (h - top - 22) / rows;
    ctx.font = `10px ${css("--mono")}`; ctx.textAlign = "right"; ctx.textBaseline = "middle";
    const counts = new Array(rows).fill(0);
    PARTICLES.forEach((p, i) => { ctx.fillStyle = css("--muted"); ctx.fillText(p, left - 6, top + (i + 0.5) * rh); ctx.strokeStyle = css("--grid"); ctx.beginPath(); ctx.moveTo(left, top + (i + 1) * rh); ctx.lineTo(w - 6, top + (i + 1) * rh); ctx.stroke(); });
    for (let i = 0; i < em.time.length; i++) { const t = em.time[i]; if (t > tc) break; const p = em.particle_index[i]; counts[p]++;
      const x = left + (t / T) * (w - left - 8); const y = top + p * rh + 2 + ((em.nucleus[i] * 2654435761) % 1000) / 1000 * (rh - 4);
      ctx.fillStyle = color(PALETTE[p]); ctx.globalAlpha = 0.55; ctx.fillRect(x, y, 1.6, 1.6); }
    ctx.globalAlpha = 1; const X = left + cursor * (w - left - 8); ctx.strokeStyle = css("--warn"); ctx.beginPath(); ctx.moveTo(X, top); ctx.lineTo(X, h - 20); ctx.stroke();
    ctx.textAlign = "left"; ctx.fillStyle = css("--muted"); ctx.fillText(`t = ${fmt(tc, 5)} of ${fmt(T, 5)} (simulated time units) · shown ${em.time.length.toLocaleString()} of ${(d.total ?? em.time.length).toLocaleString()} emissions${d.stride > 1 ? ` (every ${d.stride}th)` : ""}`, left, h - 8);
    ctx.textAlign = "right"; PARTICLES.forEach((p, i) => { ctx.fillStyle = css("--faint"); ctx.fillText(counts[i].toLocaleString(), w - 8, top + (i + 0.5) * rh); });
  }, { size: "tall" });
  sh.panel.append(el("div", { class: "note" }, "Row height inside a lane is a display offset derived from the nucleus index (illustrative); time and particle type are the run's own."));
  slider.addEventListener("input", () => { cursor = slider.value / 1000; if (opts._store) opts._store.state.playhead = cursor * (data?.horizon || 1); sk.requestDraw(); opts.onPlayhead?.(); });
  play.addEventListener("click", () => { if (timer) { clearInterval(timer); timer = null; play.textContent = "▶"; return; } if (cursor >= 1) cursor = 0; play.textContent = "⏸";
    timer = setInterval(() => { cursor = Math.min(1, cursor + 0.004); slider.value = Math.round(cursor * 1000); if (opts._store) opts._store.state.playhead = cursor * (data?.horizon || 1); sk.requestDraw(); opts.onPlayhead?.(); if (cursor >= 1) { clearInterval(timer); timer = null; play.textContent = "▶"; } }, 40); });
  let lastVersion = -1;
  return { el: sh.panel, destroy() { clearInterval(timer); sk.destroy(); }, update(store, force) {
    if (!force && store.version === lastVersion) return; lastVersion = store.version;
    const em = store.state.emissions; if (!em) return;
    if (data?.em !== em) { em.particle_index = em.particle.map((code) => PARTICLES.indexOf(em.names[String(code)])); data = { em, horizon: store.meta.horizon, total: em.total, stride: em.stride }; sk.setData(data); }
  } };
}

export function makePanel(def, opts) {
  const factory = PANELS[def.type];
  if (!factory) { const sh = shell(def); sh.body.append(el("div", { class: "empty" }, `no renderer for ${def.type}`)); return { el: sh.panel, update() {}, destroy() {} }; }
  return factory(def, opts);
}
