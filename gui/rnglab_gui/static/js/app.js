// RNGLab Laboratory: application shell, router and views.
import { RunStore, Player } from "./store.js";
import { makePanel, el, shell, CLASS_HELP } from "./panels.js";
import { Plot, Heat, fmt, PALETTE, color } from "./charts.js";
import { unifiedViews } from "./unified.js";
import { experimentViews } from "./experiments.js";

// ------------------------------------------------------------------------------------ API
const api = {
  async get(path) { const r = await fetch(path); const d = await r.json(); if (!r.ok) throw Object.assign(new Error(d.error || r.statusText), { data: d }); return d; },
  async post(path, body = {}) {
    const r = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json", "X-RNGLab-Lab": "1" }, body: JSON.stringify(body) });
    const d = await r.json(); if (!r.ok) throw Object.assign(new Error(d.error || r.statusText), { data: d }); return d;
  },
};
const LS = { get(k, d) { try { const v = localStorage.getItem(`rnglab-lab:${k}`); return v === null ? d : JSON.parse(v); } catch { return d; } },
  set(k, v) { try { localStorage.setItem(`rnglab-lab:${k}`, JSON.stringify(v)); } catch { /* storage unavailable */ } } };

const App = { catalog: null, sources: [], sourceId: null, view: null, models: {} };
const $view = document.getElementById("view");
const short = (s, n = 10) => (s ? String(s).slice(0, n) : "–");
const ev = (state, label) => el("span", { class: `ev ${state || "unavailable"}` }, label || state || "unavailable");
const wallFmt = (s) => { if (!Number.isFinite(s)) return "–"; const m = Math.floor(s / 60); return `${String(m).padStart(2, "0")}:${(s - 60 * m).toFixed(1).padStart(4, "0")}`; };
const dateFmt = (iso) => iso ? new Date(iso).toLocaleString(undefined, { month: "short", day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit" }) : "–";
function integrityBadges(states) { if (!states) return el("span", { class: "pill" }, "–"); return el("span", { class: "btnrow" }, ...Object.entries(states).map(([s, n]) => ev(s, `${n} ${s}`))); }
function sourceOf(id) { return App.sources.find((s) => s.id === id) || App.sources[0]; }
function toast(msg, bad) { const b = el("div", { class: `banner ${bad ? "bad" : "info"}` }, msg); $view.prepend(b); setTimeout(() => b.remove(), 6000); }

// ------------------------------------------------------------------------------------ boot
async function boot() {
  const theme = LS.get("theme", "dark"); document.documentElement.dataset.theme = theme;
  document.getElementById("theme").addEventListener("click", () => { const t = document.documentElement.dataset.theme === "light" ? "dark" : "light"; document.documentElement.dataset.theme = t; LS.set("theme", t); App.view?.refresh?.(); });
  setInterval(() => { document.getElementById("clock").textContent = new Date().toLocaleTimeString(); }, 1000);
  App.catalog = await api.get("/api/catalog");
  for (const m of App.catalog.models) App.models[m.id] = m;
  await loadSources();
  window.addEventListener("hashchange", route);
  // keyboard: "g" then a letter switches screen (ignored while typing in a field)
  let gPending = 0;
  const KEYS = { x: "#/experiments", d: "#/mission", a: "#/atlas", b: "#/lab/pimc-harmonic", c: "#/overview", r: "#/compare", m: "#/models", e: "#/evidence", s: "#/sources", p: "#/provenance" };
  window.addEventListener("keydown", (e) => {
    if (e.target.closest("input, select, textarea") || e.metaKey || e.ctrlKey || e.altKey) return;
    if (e.key === "g") { gPending = Date.now(); return; }
    if (gPending && Date.now() - gPending < 1200 && KEYS[e.key]) { location.hash = KEYS[e.key]; e.preventDefault(); }
    gPending = 0;
  });
  route();
}
async function loadSources(refresh = false) {
  App.sources = (await api.get(`/api/sources${refresh ? "?refresh=1" : ""}`)).sources;
  App.sourceId = LS.get("source", null);
  if (!App.sources.find((s) => s.id === App.sourceId)) App.sourceId = App.sources[0]?.id;
}

function route() {
  App.view?.destroy?.(); App.view = null; $view.replaceChildren();
  const [path, q] = location.hash.replace(/^#\/?/, "").split("?");
  const parts = path.split("/").filter(Boolean); const query = Object.fromEntries(new URLSearchParams(q || ""));
  const tab = parts[0] || "mission";
  document.querySelectorAll("nav#tabs a").forEach((a) => a.classList.toggle("active", a.dataset.tab === (tab === "run" ? "archive" : tab)));
  const U = { ...unifiedViews({ api, $view, App }), ...experimentViews({ api, $view, App }) };
  const views = { overview: Overview, models: Models, lab: Lab, run: RunPage, rng: RngAnalysis, compare: Compare, archive: Archive, studies: Studies, ...U };
  App.view = (views[tab] || U.mission)(parts.slice(1), query) || null;
}

// ------------------------------------------------------------------------------------ Overview
function Overview() {
  let timer = null;
  const head = el("div", { class: "page-title" }, el("h1", {}, "Overview"), el("span", { class: "sub" }, "Experiments, models, code sources and results at a glance"));
  const stats = el("div", { class: "stats" }); const active = el("div"); const recent = el("div"); const srcs = el("div"); const warn = el("div");
  const grid = el("div", { class: "grid" },
    panelBox("Laboratory state", stats, 12), panelBox("Active experiments", active, 7), panelBox("Code sources (checkouts)", srcs, 5),
    panelBox("Recent runs", recent, 8), panelBox("Warnings", warn, 4));
  $view.append(head, grid);
  async function refresh() {
    const runs = (await api.get("/api/runs")).runs; const live = runs.filter((r) => r.live && !["completed", "failed", "stopped", "ended"].includes(r.live.state));
    const failedIntegrity = runs.filter((r) => r.integrity?.failed);
    stats.replaceChildren(...[["models", App.catalog.models.length], ["code sources", App.sources.length], ["active runs", live.length], ["archived runs", runs.length],
      ["completed", runs.filter((r) => r.state === "completed").length], ["verified bit-for-bit", runs.filter((r) => r.verified === "ok").length]].map(([l, v]) => el("div", { class: "stat" }, el("span", { class: "v" }, v), el("span", { class: "l" }, l))));
    active.replaceChildren(live.length ? runTable(live, true) : el("div", { class: "empty" }, "No run is active. Open the Laboratory to start one."));
    recent.replaceChildren(runs.length ? runTable(runs.slice(0, 10)) : el("div", { class: "empty" }, "No runs yet."));
    srcs.replaceChildren(el("table", { class: "tbl" }, el("tr", {}, el("th", {}, "branch"), el("th", {}, "commit"), el("th", {}, "state"), el("th", {}, "models")),
      ...App.sources.map((s) => el("tr", {}, el("td", {}, s.branch || "(detached)", s.own ? el("span", { class: "pill", style: "margin-left:6px" }, "laboratory") : null), el("td", { class: "mono" }, short(s.head, 9)),
        el("td", {}, s.dirty_science ? ev("warning", `${s.dirty_science} changed in src/research`) : ev("ok", "clean src/research")),
        el("td", {}, `${App.catalog.models.filter((m) => !(s.available[m.id] || []).length).length}/${App.catalog.models.length}`)))));
    const w = [];
    for (const s of App.sources) if (s.dirty_science) w.push(el("div", {}, ev("warning"), ` ${s.branch}: ${s.dirty_science} uncommitted files under src/ or research/ — runs from it are not reproducible from the commit alone.`));
    for (const r of failedIntegrity.slice(0, 5)) w.push(el("div", {}, ev("failed"), " ", el("a", { href: `#/run/${r.run_id}` }, r.run_id), " has failed integrity checks."));
    warn.replaceChildren(...(w.length ? w : [el("div", { class: "empty" }, "None.")]));
  }
  refresh(); timer = setInterval(refresh, 2000);
  return { destroy() { clearInterval(timer); } };
}
function panelBox(title, body, span = 6, cls) { const sh = shell({ title, span, cls }); sh.body.classList.remove("flush"); sh.body.append(body); return sh.panel; }
function runTable(runs, withProgress) {
  return el("table", { class: "tbl" }, el("tr", {}, el("th", {}, "run"), el("th", {}, "model"), el("th", {}, "label"), el("th", {}, "state"), el("th", {}, withProgress ? "progress" : "integrity"), el("th", {}, "digest")),
    ...runs.map((r) => el("tr", { class: "click", onclick: () => { location.hash = `#/run/${r.run_id}`; } },
      el("td", { class: "mono" }, r.run_id.slice(9)), el("td", {}, App.models[r.model]?.title || r.model, r.pair_role ? el("span", { class: "pill", style: "margin-left:6px" }, r.pair_role) : null),
      el("td", { class: "mono" }, r.label || "–"), el("td", {}, el("span", { class: `state ${r.live?.controller === "paused" ? "paused" : r.state}` }, r.live?.controller === "paused" ? "paused" : r.state)),
      el("td", {}, withProgress ? progressBar(r.live?.progress) : integrityBadges(r.integrity)), el("td", { class: "mono" }, short(r.digest, 12), r.verified === "ok" ? el("span", { class: "ev ok", title: "an unobserved re-run gave the same digest" }, " ✓") : null))));
}
function progressBar(p) { const d = el("div", { class: "progress" }, el("div", { style: `width:${Math.round((p || 0) * 100)}%` })); return d; }

// ------------------------------------------------------------------------------------ Models
function Models() {
  const src = sourceOf(App.sourceId);
  const head = el("div", { class: "page-title" }, el("h1", {}, "Model explorer"), el("span", { class: "sub" }, "Each model runs RNGLab's own function unchanged; the laboratory observes it."), sourceSelect(() => route()));
  const cards = App.catalog.models.map((m) => {
    const missing = src.available[m.id] || [];
    return el("div", { class: `card ${missing.length ? "disabled" : ""}` }, el("div", { class: "domain" }, m.domain), el("h3", {}, m.title), el("p", {}, m.summary),
      el("div", { class: "call" }, m.call), el("div", { class: "btnrow" }, el("span", { class: "pill" }, `step = ${m.unit}`), ...Object.entries(m.controls).filter(([, v]) => v).map(([k]) => el("span", { class: "pill" }, k))),
      el("p", { style: "font-size:12px" }, el("b", {}, "Observation: "), m.observation),
      missing.length ? el("div", { class: "banner" }, `Not in this code source: ${missing.join(", ")}`) : el("div", { class: "btnrow" }, el("a", { class: "btn primary", href: `#/lab/${m.id}` }, "Open in laboratory")));
  });
  $view.append(head, el("div", { class: "grid" }, ...cards.map((c) => { c.classList.add("span-4"); return c; })));
}
function sourceSelect(onChange) {
  const s = el("select", { class: "pill", title: "code source: the checkout RNGLab is imported from" }, ...App.sources.map((x) => el("option", { value: x.id }, `${x.branch || "detached"} @ ${short(x.head, 8)}${x.own ? " (laboratory)" : ""}${x.dirty_science ? " · modified" : ""}`)));
  s.value = App.sourceId; s.addEventListener("change", () => { App.sourceId = s.value; LS.set("source", s.value); onChange?.(); });
  return el("label", { class: "sub" }, "code source ", s);
}

// ------------------------------------------------------------------------------------ Laboratory
function Lab(parts) {
  const model = App.models[parts[0]] || App.catalog.models[0];
  const formBox = el("div", { class: "form" }); const runBox = el("div");
  const head = el("div", { class: "page-title" }, el("h1", {}, model.title), el("span", { class: "sub" }, model.call));
  $view.append(head, el("div", { class: "split" }, el("div", { class: "sticky-side" }, panelBox("Configuration", formBox, 12)), runBox));
  let runView = null;
  const saved = LS.get(`params:${model.id}`, {});
  const values = {}; const fields = {};
  const src = () => sourceOf(App.sourceId);
  formBox.append(sourceSelect(() => { updateAvailability(); }));
  const avail = el("div"); formBox.append(avail);
  const groups = {};
  for (const p of model.params) {
    const g = (groups[p.group] ||= el("fieldset", {}, el("legend", {}, p.group)));
    let input;
    if (p.kind === "enum") input = el("select", {}, ...p.choices.map((c) => Array.isArray(c) ? el("option", { value: c[0], title: c[1] }, c[1]) : el("option", { value: c }, c)));
    else if (p.kind === "bool") input = el("input", { type: "checkbox" });
    else input = el("input", { type: p.kind === "str" ? "text" : "number", step: p.step ?? (p.kind === "int" ? 1 : "any"), min: p.minimum, max: p.maximum });
    const v = saved[p.name] ?? p.default;
    if (p.kind === "bool") input.checked = !!v; else input.value = v;
    const msg = el("div", { class: "msg" });
    const field = el("div", { class: "field", title: p.help || "" }, el("label", {}, p.label, p.unit ? ` (${p.unit})` : "", p.scientific ? null : el("span", { class: "exe", title: "execution setting: does not change what is computed" }, "exec")), input, msg);
    fields[p.name] = { p, input, field, msg }; g.append(field);
    input.addEventListener("change", () => { read(); visibility(); seedPreview(); });
  }
  const seedBox = el("div", { class: "note", style: "padding:4px 0" });
  for (const g of Object.values(groups)) formBox.append(g);
  if (groups["Random source"]) groups["Random source"].append(seedBox);
  const runBtn = el("button", { class: "btn primary" }, "Run");
  const pairBtn = el("button", { class: "btn", title: "Start this run and the same configuration without the fault (same seeds) side by side" }, "Run with healthy baseline");
  const resetBtn = el("button", { class: "btn", title: "restore the defaults" }, "Defaults");
  const err = el("div");
  formBox.append(el("div", { class: "btnrow" }, runBtn, pairBtn, resetBtn), err);
  function read() {
    for (const [name, { p, input }] of Object.entries(fields)) values[name] = p.kind === "bool" ? input.checked : p.kind === "int" ? (input.value === "" ? null : Number(input.value)) : p.kind === "float" ? Number(input.value) : input.value;
    LS.set(`params:${model.id}`, values); return values;
  }
  function visibility() { for (const { p, field } of Object.values(fields)) { const vis = Object.entries(p.visible_if || {}).every(([k, allowed]) => allowed.includes(values[k])); field.classList.toggle("hidden", !vis); } }
  async function seedPreview() {
    if (!("namespace" in values)) return;
    const roles = model.id === "h1-detection" ? ["(h1lib.seeds_for)"] : roleList(model, values);
    try { const r = await api.get(`/api/seeds?namespace=${encodeURIComponent(values.namespace)}&label=${encodeURIComponent(values.label)}&roles=${roles.join(",")}`);
      seedBox.replaceChildren(r.allowed ? ev("ok", "namespace allowed") : ev("failed", "refused"), " ", r.allowed ? "" : r.reason, el("br"),
        model.id === "h1-detection" ? `seeds: h1lib.seeds_for(namespace, label, replica) → local, centroid, start, fault, gfsr` : `seeds (namespace_seed(ns, label, role)): ${Object.entries(r.seeds).map(([k, v]) => `${k} ${v}`).join(" · ")}`);
    } catch (e) { seedBox.textContent = e.message; }
  }
  function updateAvailability() {
    const missing = src().available[model.id] || []; const gen = values.generator ? src().available[`generator:${values.generator}`] || [] : [];
    avail.replaceChildren(missing.length ? el("div", { class: "banner bad" }, `This code source lacks ${missing.join(", ")}`) : gen.length ? el("div", { class: "banner" }, `Generator ${values.generator} needs ${gen.join(", ")}`) : el("div"));
    runBtn.disabled = pairBtn.disabled = missing.length > 0;
  }
  async function launch(pair) {
    read(); err.replaceChildren(); for (const f of Object.values(fields)) { f.field.classList.remove("err"); f.msg.textContent = ""; }
    try {
      const r = await api.post("/api/runs", { model: model.id, params: values, source: App.sourceId, pair });
      LS.set(`lastrun:${model.id}`, r.run_ids);
      if (pair) { location.hash = `#/compare?ids=${r.run_ids.join(",")}&live=1`; return; }
      showRun(r.run_ids[0]);
    } catch (e) {
      const errors = e.data?.errors || {}; for (const [k, m] of Object.entries(errors)) if (fields[k]) { fields[k].field.classList.add("err"); fields[k].msg.textContent = m; }
      err.replaceChildren(el("div", { class: "banner bad" }, e.message));
    }
  }
  function showRun(id) { runView?.destroy(); runBox.replaceChildren(); runView = RunView(runBox, id); }
  runBtn.addEventListener("click", () => launch(false)); pairBtn.addEventListener("click", () => launch(true));
  resetBtn.addEventListener("click", () => { for (const { p, input } of Object.values(fields)) { if (p.kind === "bool") input.checked = !!p.default; else input.value = p.default; } read(); visibility(); seedPreview(); });
  const hasFault = model.params.some((p) => p.name === "fault" || p.name === "family") && model.id !== "decision-support";
  pairBtn.classList.toggle("hidden", !hasFault);
  read(); visibility(); seedPreview(); updateAvailability();
  const last = LS.get(`lastrun:${model.id}`, null);
  if (last?.length) showRun(last[0]); else runBox.append(modelIntro(model));
  return { destroy() { runView?.destroy(); } };
}
function roleList(model, v) {
  if (model.id === "pimc-harmonic") return [...(v.channels === "separate" ? ["proposal", "acceptance", "initialization"] : ["local"]), ...(v.centroid_refresh ? ["centroid"] : [])];
  if (model.id === "ising-2d") return v.channels === "separate" ? ["proposal", "acceptance", "initialization"] : ["local"];
  if (model.id === "decision-support") return ["replicate-0"];
  return ["local"];
}
function modelIntro(model) {
  return panelBox("About this model", el("div", {}, el("p", {}, model.summary), el("dl", { class: "kv" },
    el("dt", {}, "scientific call"), el("dd", {}, model.call), el("dt", {}, "observation"), el("dd", { style: "font-family:inherit" }, model.observation),
    el("dt", {}, "step"), el("dd", {}, model.unit), el("dt", {}, "references"), el("dd", { style: "font-family:inherit" }, (model.references || []).join("; ") || "–"),
    el("dt", {}, "panels"), el("dd", { style: "font-family:inherit" }, model.panels.map((p) => `${p.title} [${p.cls}]`).join(" · "))),
    el("p", { class: "footnote" }, "Configure on the left and press Run. Execution settings (marked exec) change how the run is shown, never what it computes.")), 12);
}

// ------------------------------------------------------------------------------------ Run view
function RunView(container, runId, opts = {}) {
  const store = new RunStore(runId); let model = null, player = null, es = null, raf = null, panels = [], detail = null, destroyed = false;
  const bar = el("div", { class: "controlbar" }); const grid = el("div", { class: "grid" }); const side = el("div", { class: "grid", style: "margin-top:10px" });
  container.append(bar, grid, side);
  const sync = `run-${runId}`;
  const scalarsBox = el("div"), resultBox = el("div"), checksBox = el("div"), provBox = el("div"), logBox = el("div", { class: "scroll", style: "max-height:220px" });
  (async () => {
    detail = await api.get(`/api/runs/${runId}`);
    model = App.models[detail.config.model];
    buildPanels();
    side.append(panelBox("Live values", scalarsBox, 3, "simulated"), panelBox("Official result", resultBox, 5), panelBox("Integrity", checksBox, 4),
      panelBox("Provenance and configuration", provBox, 8), panelBox("Events and messages", logBox, 4));
    renderProvenance();
    const live = detail.live && !["completed", "failed", "stopped", "ended"].includes(detail.live.state);
    if (live) startLive(); else await startReplay();
    loop();
  })().catch((e) => container.prepend(el("div", { class: "banner bad" }, `Could not open run ${runId}: ${e.message}`)));

  function buildPanels() {
    const popts = { sync, _store: store };
    panels = model.panels.map((def) => { const p = makePanel({ ...def, span: def.span || spanFor(def) }, popts); grid.append(p.el); return p; });
  }
  function spanFor(def) { return ({ ring: 6, scatter: 5, lattice: 5, chain: 6, raster: 12, pmatrix: 6, matrix: 6, populations: 6, interval: 6, monitor: 6 })[def.type] || (def.type === "hist" || def.type === "density" || def.type === "bars" ? 4 : 6); }
  function startLive() {
    es = new EventSource(`/api/runs/${runId}/events`);
    es.onmessage = (e) => { const m = JSON.parse(e.data); store.ingest(m); if (m.t === "closed") { es.close(); es = null; refreshDetail(); } if (m.t === "done") refreshDetail(); };
    es.onerror = () => { /* the browser reconnects; duplicates are dropped by sequence number */ };
    renderLiveBar();
  }
  async function startReplay() {
    const tel = await api.get(`/api/runs/${runId}/telemetry`);
    player = new Player(tel.messages, store, () => { if (player) renderReplayBar(); });  // the constructor calls back before assignment
    if (opts.autoEnd !== false) player.end();
    renderReplayBar(true);
  }
  async function refreshDetail() { detail = await api.get(`/api/runs/${runId}`); renderProvenance(); renderLiveBar(); }
  // ---- control bars
  const ctl = {};
  function renderLiveBar() {
    bar.replaceChildren();
    const unit = model?.unit || "step";
    ctl.state = el("span", { class: "state running" }, "running");
    ctl.pause = el("button", { class: "btn", onclick: () => send(store.controller === "paused" ? "resume" : "pause") }, "⏸ pause");
    ctl.step1 = el("button", { class: "btn", title: `advance one ${unit}, then pause`, onclick: () => send("step", { n: 1 }) }, `step 1`);
    ctl.step10 = el("button", { class: "btn", onclick: () => send("step", { n: 10 }) }, "10");
    ctl.step100 = el("button", { class: "btn", onclick: () => send("step", { n: 100 }) }, "100");
    const delay = el("input", { type: "range", min: 0, max: 100, value: 0, title: "wall-clock delay per step (simulation results are unaffected)" });
    const delayLbl = el("span", { class: "mono", style: "min-width:64px" }, "0 ms");
    delay.addEventListener("input", () => { const ms = delay.value === "0" ? 0 : Math.round(0.05 * Math.pow(10, delay.value / 25) * 100) / 100; delayLbl.textContent = `${ms} ms`; send("speed", { delay_ms: ms }); });
    ctl.stop = el("button", { class: "btn danger", onclick: () => { if (confirm("Stop this run? It ends without an official result.")) send("stop"); } }, "■ stop");
    ctl.clocks = el("div", { class: "clocks" }); ctl.progress = progressBar(0);
    bar.append(ctl.state, el("div", { class: "group" }, ctl.pause, ctl.step1, ctl.step10, ctl.step100), el("div", { class: "group", title: "delay per step" }, el("span", { class: "sub" }, "slow"), delay, delayLbl), ctl.stop,
      el("div", { class: "sep" }), ctl.clocks, ctl.progress, el("div", { class: "sep" }), ...runActions());
    if (store.done || (detail?.live && ["completed", "failed", "stopped", "ended"].includes(detail.live.state))) [ctl.pause, ctl.step1, ctl.step10, ctl.step100, ctl.stop, delay].forEach((b) => { b.disabled = true; });
  }
  function renderReplayBar(rebuild) {
    if (rebuild || !ctl.scrub) {
      bar.replaceChildren();
      ctl.state = el("span", { class: "state replay", title: "recorded telemetry played back from the archive" }, "replay");
      ctl.play = el("button", { class: "btn", onclick: () => (player.playing ? player.pause() : (player.frameAt >= player.frames && player.seekFrame(0), player.play())) }, "▶ play");
      const back = el("button", { class: "btn", onclick: () => player.step(-1) }, "◀ frame");
      const fwd = el("button", { class: "btn", onclick: () => player.step(1) }, "frame ▶");
      const first = el("button", { class: "btn", onclick: () => player.seekFrame(0) }, "⏮"); const last = el("button", { class: "btn", onclick: () => player.end() }, "⏭");
      ctl.scrub = el("input", { type: "range", min: 0, max: player.frames, value: player.frameAt, style: "flex:1;min-width:160px" });
      ctl.scrub.addEventListener("input", () => player.seekFrame(Number(ctl.scrub.value)));
      const fps = el("select", { class: "pill", title: "playback rate (recorded frames per second; ~10 is the recording rate)" }, ...[2, 5, 12, 25, 60].map((f) => el("option", { value: f }, `${f} fps`)));
      fps.value = 12; fps.addEventListener("change", () => player.setFps(Number(fps.value)));
      ctl.clocks = el("div", { class: "clocks" });
      bar.append(ctl.state, el("div", { class: "group" }, first, back, ctl.play, fwd, last), ctl.scrub, fps, ctl.clocks, el("div", { class: "sep" }), ...runActions());
    }
    ctl.scrub.value = player.frameAt; ctl.play.textContent = player.playing ? "⏸ pause" : "▶ play";
  }
  function runActions() {
    const restart = el("button", { class: "btn", title: "run the identical configuration again (same seeds)", onclick: async () => { const r = await api.post(`/api/runs/${runId}/restart`); location.hash = `#/run/${r.run_ids[0]}`; } }, "↻ restart");
    const verify = el("button", { class: "btn", title: "re-run unobserved in a fresh process and compare the official result digests", onclick: async () => { try { await api.post(`/api/runs/${runId}/verify`); toast("Verification started: an unobserved re-run in a fresh process."); setTimeout(refreshDetail, 1500); } catch (e) { toast(e.message, true); } } }, "✓ verify");
    const exp = el("a", { class: "btn", href: `/api/runs/${runId}/export.zip`, title: "configuration, telemetry, result, integrity and arrays" }, "⤓ export");
    const cmp = detail?.config?.pair_id ? el("a", { class: "btn", href: `#/compare?ids=${pairIds().join(",")}` }, "⇄ pair") : null;
    const open = el("a", { class: "btn", href: `#/run/${runId}`, title: "open on its own page" }, "⧉");
    return [restart, verify, exp, cmp, opts.inLab === false ? null : open].filter(Boolean);
  }
  function pairIds() { const pid = detail.config.pair_id; return [pid, runId].filter((v, i, a) => a.indexOf(v) === i); }
  async function send(cmd, extra = {}) { try { await api.post(`/api/runs/${runId}/control`, { cmd, ...extra }); } catch (e) { toast(e.message, true); } }
  // ---- rendering loop
  let lastV = -1, lastT = 0;
  function loop(t = 0) {
    if (destroyed) return; raf = requestAnimationFrame(loop);
    if (store.version === lastV || t - lastT < 50) return; lastV = store.version; lastT = t;
    for (const p of panels) p.update(store);
    renderStatus(); renderScalars(); renderResult(); renderChecks(); renderLog();
  }
  function renderStatus() {
    const sim = store.sim; const unit = sim?.unit || model?.unit || "step";
    ctl.clocks.replaceChildren(el("span", {}, el("span", { class: "k", title: "simulation position" }, "sim"), sim ? `${unit} ${Number(sim.value).toLocaleString()} / ${Number(sim.total).toLocaleString()}` : "–"),
      el("span", {}, el("span", { class: "k", title: "wall-clock time since the worker started" }, "wall"), wallFmt(store.wall)));
    if (player) return;
    const state = store.done ? store.done.status : store.controller; ctl.state.className = `state ${state}`; ctl.state.textContent = state;
    ctl.progress.firstChild.style.width = `${Math.round((store.progress || 0) * 100)}%`;
    if (ctl.pause) ctl.pause.textContent = store.controller === "paused" ? "▶ resume" : "⏸ pause";
    if (store.done && !ctl.pause.disabled) renderLiveBar();
  }
  function renderScalars() {
    const s = store.scalars; scalarsBox.replaceChildren(el("dl", { class: "kv" }, ...Object.entries(s).filter(([, v]) => typeof v !== "object" || v === null).flatMap(([k, v]) => [el("dt", {}, k.replaceAll("_", " ")), el("dd", {}, typeof v === "number" ? fmt(v, 8) : String(v))])));
  }
  function renderResult() {
    const r = store.result; if (!r) { resultBox.replaceChildren(el("div", { class: "empty" }, store.done ? "No official result (the run did not complete)." : "Appears when the scientific call returns.")); return; }
    const ver = detail?.verification;
    resultBox.replaceChildren(el("dl", { class: "kv" },
      el("dt", {}, "digest"), el("dd", {}, r.digest),
      el("dt", {}, "verified"), el("dd", {}, ver ? ev(ver.state, ver.state === "ok" ? "same digest unobserved" : ver.state) : el("span", { class: "sub" }, "not yet (✓ verify)")),
      ...Object.entries(r.summary || {}).flatMap(([k, v]) => [el("dt", {}, k.replaceAll("_", " ")), el("dd", {}, Array.isArray(v) ? `[${v.map((x) => fmt(x, 7)).join(", ")}]` : typeof v === "number" ? fmt(v, 8) : String(v))]),
      el("dt", {}, "arrays"), el("dd", {}, (r.arrays || []).join(", ") || "–"),
      el("dt", {}, "excluded from digest"), el("dd", {}, (r.volatile_excluded || []).join(", ") || "none")),
      el("details", {}, el("summary", { class: "sub" }, "full official payload (JSON)"), el("pre", { class: "mono", style: "max-height:300px;overflow:auto;font-size:11px" }, JSON.stringify(r.payload, null, 1).slice(0, 60000))));
  }
  function renderChecks() {
    const checks = store.integrity || detail?.integrity;
    if (!checks) { checksBox.replaceChildren(el("div", { class: "empty" }, store.error ? "Run failed: see messages." : "Checks run when the result exists.")); return; }
    checksBox.replaceChildren(el("div", { class: "checks" }, ...checks.map((c) => el("div", { class: "check", title: c.data ? JSON.stringify(c.data) : "" }, ev(c.state), el("div", {}, el("div", { class: "name" }, c.name), el("div", { class: "detail" }, c.detail))))));
  }
  function renderLog() {
    const items = [];
    if (store.error) items.push(el("div", {}, ev("failed", "error"), " ", store.error.message, el("pre", { class: "tb" }, store.error.traceback)));
    for (const e of store.events.slice(-60)) items.push(el("div", { class: "mono", style: "font-size:11px" }, `${e.kind} @ ${e.sim?.unit || ""} ${e.sim?.value ?? ""}: ${JSON.stringify(e.data)}`));
    for (const s of store.statusLog.slice(-12)) items.push(el("div", { class: "mono", style: "font-size:11px;color:var(--muted)" }, `[wall ${wallFmt(s.wall)}] controller ${s.state}${s.at_step !== undefined ? ` at ${s.at_step}` : ""}`));
    if (store.done) items.push(el("div", {}, `done: ${store.done.status} after ${wallFmt(store.done.elapsed_wall)} wall, ${store.done.frames} frames`));
    logBox.replaceChildren(...(items.length ? items : [el("div", { class: "empty" }, "No events.")]));
  }
  function renderProvenance() {
    if (!detail) return; const c = detail.config; const cs = c.code_source;
    const sources = store.result?.sources || detail.result?.sources || [];
    provBox.replaceChildren(el("div", { class: "grid" },
      el("div", { class: "span-6" }, el("dl", { class: "kv" },
        el("dt", {}, "run"), el("dd", {}, c.run_id), el("dt", {}, "model"), el("dd", {}, `${c.model} — ${c.call}`),
        el("dt", {}, "code source"), el("dd", {}, `${cs.branch || "detached"} @ ${cs.head}`), el("dt", {}, "path"), el("dd", {}, cs.path),
        el("dt", {}, "src/research"), el("dd", {}, cs.dirty_science ? ev("warning", `${cs.dirty_science} uncommitted`) : ev("ok", "clean")),
        el("dt", {}, "interpreter"), el("dd", {}, `${c.interpreter} · ${store.hello?.python?.split(" ")[0] || ""} · numpy ${store.hello?.numpy || ""}`),
        el("dt", {}, "laboratory"), el("dd", {}, `v${c.laboratory.version} @ ${short(c.laboratory.head, 12)}`),
        el("dt", {}, "created"), el("dd", {}, c.created), c.parent_run ? el("dt", {}, "restart of") : null, c.parent_run ? el("dd", {}, el("a", { href: `#/run/${c.parent_run}` }, c.parent_run)) : null,
        c.pair_id ? el("dt", {}, "pair") : null, c.pair_id ? el("dd", {}, `${c.pair_role} · ${c.pair_id}`) : null)),
      el("div", { class: "span-6" }, el("table", { class: "tbl" }, el("tr", {}, el("th", {}, "parameter"), el("th", {}, "value"), el("th", {}, "kind")),
        ...Object.entries(c.params).map(([k, v]) => el("tr", {}, el("td", {}, k), el("td", { class: "mono" }, String(v)), el("td", {}, k in c.execution_params ? el("span", { class: "pill" }, "exec") : "scientific"))))),
      el("div", { class: "span-12" }, el("table", { class: "tbl" }, el("tr", {}, el("th", {}, "role"), el("th", {}, "source"), el("th", {}, "seed / derivation")),
        ...sources.map((s) => el("tr", {}, el("td", {}, s.role), el("td", { class: "mono" }, s.source_id || s.adapter || ""), el("td", { class: "mono" }, s.seed !== undefined ? `${s.seed ?? "–"} · ${s.seed_label || ""}` : JSON.stringify(s).slice(0, 160))))))));
  }
  return { store, destroy() { destroyed = true; cancelAnimationFrame(raf); es?.close(); player?.pause(); panels.forEach((p) => p.destroy()); } };
}

function RunPage(parts) {
  const id = parts[0];
  $view.append(el("div", { class: "page-title" }, el("h1", {}, "Run"), el("span", { class: "mono sub" }, id), el("a", { href: "#/archive", class: "sub" }, "← archive")));
  const box = el("div"); $view.append(box); const v = RunView(box, id, { inLab: false });
  return v;
}

// ------------------------------------------------------------------------------------ RNG analysis
function RngAnalysis(parts, query) {
  const head = el("div", { class: "page-title" }, el("h1", {}, "RNG analysis"), el("span", { class: "sub" }, "The consumed streams of a run, role by role: what the model actually read, and the healthy words under a fault adapter."));
  const pick = el("select", { class: "pill" }); const body = el("div"); $view.append(head, el("div", { class: "btnrow", style: "margin-bottom:10px" }, el("span", { class: "sub" }, "run"), pick), body);
  let panels = [], store = null, raf = null, destroyed = false;
  (async () => {
    const runs = (await api.get("/api/runs")).runs.filter((r) => r.state !== "failed");
    if (!runs.length) { body.append(el("div", { class: "empty" }, "No runs yet. Every run's consumed streams appear here.")); return; }
    pick.replaceChildren(...runs.slice(0, 80).map((r) => el("option", { value: r.run_id }, `${r.run_id.slice(9)} · ${App.models[r.model]?.title || r.model} · ${r.label || ""}${r.pair_role ? ` · ${r.pair_role}` : ""}`)));
    pick.value = query.run && runs.find((r) => r.run_id === query.run) ? query.run : runs[0].run_id;
    pick.addEventListener("change", () => show(pick.value)); show(pick.value);
  })();
  async function show(id) {
    panels.forEach((p) => p.destroy()); panels = []; body.replaceChildren();
    store = new RunStore(id); const tel = await api.get(`/api/runs/${id}/telemetry`); for (const m of tel.messages) store.ingest(m);
    const roles = Object.keys(store.streams);
    if (!roles.length) { body.append(el("div", { class: "empty" }, "This run has no tapped stream summaries.")); return; }
    const table = el("table", { class: "tbl" }, el("tr", {}, ...["role", "bytes", "reads", "words", "mean u", "var u", "lag-1 corr", "χ² (49)", "p"].map((h) => el("th", {}, h))),
      ...roles.map((r) => { const s = store.streams[r]; return el("tr", {}, el("td", {}, r), el("td", { class: "num" }, s.bytes.toLocaleString()), el("td", { class: "num" }, s.reads.toLocaleString()), el("td", { class: "num" }, s.words.toLocaleString()),
        el("td", { class: "num" }, fmt(s.uniform_mean, 6)), el("td", { class: "num" }, fmt(s.uniform_var, 6)), el("td", { class: "num" }, fmt(s.lag1_corr, 4)), el("td", { class: "num" }, fmt(s.uniform_chi2, 5)), el("td", { class: "num" }, fmt(s.uniform_chi2_p, 3))); }));
    body.append(el("div", { class: "banner info" }, "Derived from the bytes that passed the audited boundary RandomSource.read_bytes during the run (a tap forwards them unchanged). Roles ending in /base are the healthy words a fault adapter read; the role without /base is what the model consumed. These diagnostics are descriptive; they are not the frozen H1 battery."),
      el("div", { class: "grid" }, panelBox("Stream summary", table, 12, "derived")));
    const grid = el("div", { class: "grid", style: "margin-top:10px" }); body.append(grid);
    for (const r of roles) for (const t of [["hist", "hist:uniform", "Uniforms"], ["density", "density:lag1", "Lag-1 pairs"], ["bars", "bars:bits", "Bit frequency"]]) {
      const p = makePanel({ id: `${t[0]}-${r}`, type: t[0], source: t[1], title: `${t[2]} — ${r}`, cls: "derived", role: r, span: 4 }, { _store: store }); grid.append(p.el); panels.push(p);
    }
    for (const p of panels) p.update(store, true);
  }
  return { destroy() { destroyed = true; panels.forEach((p) => p.destroy()); } };
}

// ------------------------------------------------------------------------------------ Compare
function Compare(parts, query) {
  const ids = (query.ids || "").split(",").filter(Boolean);
  const head = el("div", { class: "page-title" }, el("h1", {}, "Comparison"), el("span", { class: "sub" }, "Runs side by side: parameters that differ, results, and the model's key series overlaid."));
  $view.append(head);
  if (ids.length < 2) { $view.append(el("div", { class: "empty" }, "Select two or more runs in the Archive (or launch a run with its healthy baseline).")); return; }
  const stores = ids.map((id) => new RunStore(id)); const sources = []; let plots = [], raf = null, destroyed = false, cmp = null;
  const top = el("div", { class: "grid" }); const overlay = el("div", { class: "grid", style: "margin-top:10px" }); const below = el("div", { class: "grid", style: "margin-top:10px" });
  $view.append(top, overlay, below);
  const colors = ids.map((_, i) => PALETTE[i % 8]);
  (async () => {
    cmp = await api.get(`/api/compare?ids=${ids.join(",")}`);
    const model = App.models[cmp.runs[0].config.model];
    const diff = el("table", { class: "tbl" }, el("tr", {}, el("th", {}, "parameter"), ...ids.map((id, i) => el("th", { style: `color:${color(colors[i])}` }, id.slice(9)))),
      ...Object.entries(cmp.differing_params).map(([k, vals]) => el("tr", {}, el("td", {}, k), ...vals.map((v) => el("td", { class: "mono" }, String(v))))));
    const sums = el("table", { class: "tbl" }, el("tr", {}, el("th", {}, ""), ...ids.map((id, i) => el("th", { style: `color:${color(colors[i])}` }, cmp.runs[i].config.pair_role || id.slice(9)))),
      el("tr", {}, el("td", {}, "state"), ...cmp.runs.map((r) => el("td", {}, r.status?.state || "–"))),
      el("tr", {}, el("td", {}, "digest"), ...cmp.runs.map((r) => el("td", { class: "mono" }, short(r.digest, 16)))),
      ...Object.keys(cmp.runs[0].summary || {}).map((k) => el("tr", {}, el("td", {}, k.replaceAll("_", " ")), ...cmp.runs.map((r) => { const v = r.summary?.[k]; return el("td", { class: "mono" }, Array.isArray(v) ? `[${v.map((x) => fmt(x, 6)).join(", ")}]` : typeof v === "number" ? fmt(v, 7) : v && typeof v === "object" ? Object.entries(v).map(([a, b]) => `${a}: ${typeof b === "number" ? fmt(b, 7) : JSON.stringify(b)}`).join(" · ") : String(v)); }))));
    top.append(panelBox("Parameters that differ", Object.keys(cmp.differing_params).length ? diff : el("div", { class: "empty" }, "None: identical configurations."), 5),
      panelBox("Results", el("div", {}, sums, cmp.identical_results ? el("div", { class: "banner info", style: "margin-top:8px" }, "Identical official result digests: these runs reproduce each other bit for bit.") : null), 7));
    const names = cmp.same_model ? (model.compare_series || []) : [];
    for (const n of names) { const box = shell({ title: `${n === "ci_mean" ? "running mean (interval panel)" : n} — overlay`, span: 6, cls: "simulated" }); overlay.append(box.panel);
      const plot = new Plot(box.body, { sync: "compare", xname: model.unit, xlabel: model.unit }); plots.push({ n, plot }); }
    // live or archived telemetry per run
    cmp.runs.forEach((r, i) => {
      if (r.status?.state === "running") { const es = new EventSource(`/api/runs/${r.run_id}/events`); es.onmessage = (e) => { const m = JSON.parse(e.data); stores[i].ingest(m); if (m.t === "closed") es.close(); }; sources.push(es); }
      else api.get(`/api/runs/${r.run_id}/telemetry`).then((t) => t.messages.forEach((m) => stores[i].ingest(m)));
    });
    const links = el("div", { class: "btnrow" }, ...ids.map((id, i) => el("a", { class: "btn", href: `#/run/${id}`, style: `border-color:${color(colors[i])}` }, `open ${cmp.runs[i].config.pair_role || id.slice(9)}`)));
    below.append(panelBox("Runs", links, 12));
    loop();
  })();
  let lastSig = "";
  function loop() {
    if (destroyed) return; raf = requestAnimationFrame(loop);
    const sig = stores.map((s) => s.version).join(); if (sig === lastSig) return; lastSig = sig;
    for (const { n, plot } of plots) {
      const series = []; const refs = [];
      stores.forEach((s, i) => {
        const label = cmp.runs[i].config.pair_role || ids[i].slice(9);
        if (n === "ci_mean") { const pts = s.series.ci || []; series.push({ name: `${label} band`, kind: "band", x: pts.map((p) => p.x), y: pts.map((p) => p.lo ?? NaN), y2: pts.map((p) => p.hi ?? NaN), color: colors[i], alpha: 0.13, noLegend: true });
          series.push({ name: label, x: pts.map((p) => p.x), y: pts.map((p) => p.mean), color: colors[i], width: 1.6 }); }
        else { const v = s.series[n]; if (v) series.push({ name: label, x: v.x, y: v.y, color: colors[i], kind: n.startsWith("stat") ? "step" : "line" }); }
        for (const [k, y] of Object.entries(s.meta.references || {})) if (i === 0 && (n === "ci_mean" || n === "cv")) refs.push({ y, label: k.replaceAll("_", " "), color: "var(--reference)" });
        if (s.meta.thresholds && n === "statC") refs.push({ y: s.meta.thresholds.tau_C, label: "τ_C", color: "var(--bad)" });
        if (s.meta.thresholds && n === "statA") refs.push({ y: s.meta.thresholds.tau_A, label: "τ_A", color: "var(--bad)" });
      });
      const vlines = stores.flatMap((s, i) => s.events.filter((e) => e.kind === "onset").map((e) => ({ x: e.sim?.value, label: "onset", color: colors[i] })));
      plot.setData({ series, refs: refs.filter((r, k, a) => a.findIndex((q) => q.label === r.label) === k), vlines });
    }
  }
  return { destroy() { destroyed = true; cancelAnimationFrame(raf); sources.forEach((s) => s.close()); plots.forEach((p) => p.plot.destroy()); } };
}

// ------------------------------------------------------------------------------------ Archive
function Archive() {
  const head = el("div", { class: "page-title" }, el("h1", {}, "Experiment archive"), el("span", { class: "sub" }, "Every run with its configuration, telemetry, official result, integrity checks and provenance."));
  const filterModel = el("select", { class: "pill" }, el("option", { value: "" }, "all models"), ...App.catalog.models.map((m) => el("option", { value: m.id }, m.title)));
  const filterState = el("select", { class: "pill" }, ...["", "completed", "running", "stopped", "failed"].map((s) => el("option", { value: s }, s || "all states")));
  const cmpBtn = el("button", { class: "btn" }, "Compare selected"); const tableBox = el("div", { class: "scroll", style: "max-height:none" });
  $view.append(head, el("div", { class: "btnrow", style: "margin-bottom:10px" }, filterModel, filterState, cmpBtn, el("span", { class: "sub" }, "Click a row to replay it; tick rows to compare.")), panelBox("Runs", tableBox, 12));
  const selected = new Set(); let runs = [];
  async function load() { runs = (await api.get("/api/runs")).runs; render(); }
  function render() {
    const rows = runs.filter((r) => (!filterModel.value || r.model === filterModel.value) && (!filterState.value || r.state === filterState.value));
    tableBox.replaceChildren(el("table", { class: "tbl" }, el("tr", {}, ...["", "run", "model", "label", "state", "summary", "integrity", "verified", "digest", "code"].map((h) => el("th", {}, h))),
      ...rows.map((r) => { const cb = el("input", { type: "checkbox", onclick: (e) => { e.stopPropagation(); cb.checked ? selected.add(r.run_id) : selected.delete(r.run_id); } }); cb.checked = selected.has(r.run_id);
        return el("tr", { class: "click", onclick: () => { location.hash = `#/run/${r.run_id}`; } }, el("td", {}, cb), el("td", { class: "mono" }, r.run_id.slice(9)),
          el("td", {}, App.models[r.model]?.title || r.model, r.pair_role ? el("span", { class: "pill", style: "margin-left:6px" }, r.pair_role) : null, r.parent_run ? el("span", { class: "pill", style: "margin-left:6px" }, "restart") : null),
          el("td", { class: "mono" }, r.label || "–"), el("td", {}, el("span", { class: `state ${r.state}` }, r.state)),
          el("td", { class: "mono", style: "font-size:11px;max-width:320px" }, summaryText(r.summary)), el("td", {}, integrityBadges(r.integrity)),
          el("td", {}, r.verified ? ev(r.verified) : "–"), el("td", { class: "mono" }, short(r.digest, 12)), el("td", { class: "mono" }, `${r.code_branch || ""}@${short(r.code_head, 7)}`)); })));
  }
  function summaryText(s) { if (!s) return "–"; return Object.entries(s).slice(0, 4).map(([k, v]) => `${k}=${Array.isArray(v) ? `[${v.map((x) => fmt(x, 4)).join(",")}]` : typeof v === "number" ? fmt(v, 5) : v}`).join(" "); }
  filterModel.addEventListener("change", render); filterState.addEventListener("change", render);
  cmpBtn.addEventListener("click", () => { if (selected.size < 2) return toast("Select at least two runs.", true); location.hash = `#/compare?ids=${[...selected].join(",")}`; });
  load(); const t = setInterval(load, 4000);
  return { destroy() { clearInterval(t); } };
}

// ------------------------------------------------------------------------------------ Studies (recorded)
function Studies(parts, query) {
  const head = el("div", { class: "page-title" }, el("h1", {}, "Frozen studies"), el("span", { class: "sub" }, "Read-only: protocols, freeze checks and recorded results of research/ in the selected code source. Nothing is regenerated."), sourceSelect(() => route()));
  const inv = el("div"); const h1 = el("div"); $view.append(head, el("div", { class: "grid" }, panelBox("Study inventory", inv, 12, "recorded")), h1);
  inv.append(el("div", { class: "empty" }, "Reading the code source…"));
  let plots = [];
  (async () => {
    const r = await api.get(`/api/studies/inventory?source=${App.sourceId}`);
    if (!r.ok) { inv.replaceChildren(el("div", { class: "banner bad" }, r.error)); return; }
    inv.replaceChildren(el("table", { class: "tbl" }, el("tr", {}, ...["study", "protocol", "status", "freeze", "namespace", "results", ""].map((h) => el("th", {}, h))),
      ...r.data.studies.map((s) => el("tr", {}, el("td", {}, el("b", {}, s.id), el("div", { class: "sub", style: "font-size:11px" }, (s.title || "").slice(0, 110))), el("td", { class: "mono" }, s.protocol_id || "–"),
        el("td", { class: "mono" }, s.status || "–"), el("td", { title: s.freeze?.detail }, ev(s.freeze?.state)), el("td", { class: "mono" }, s.namespace || "–"),
        el("td", { class: "num" }, `${s.result_files} files · ${(s.result_bytes / 1e6).toFixed(1)} MB`), el("td", {}, s.viewer === "h1" ? el("a", { class: "btn", href: "#/studies/h1" }, "open viewer") : "")))),
      el("p", { class: "footnote" }, `Code source ${r.source.branch} @ ${short(r.source.head, 10)}. Freeze: SHA-256 of protocol.json against protocol.sha256. Generation in these namespaces is refused by the laboratory.`));
    if (parts[0] === "h1") H1Viewer(h1, query);
  })();
  return { destroy() { plots.forEach((p) => p.destroy?.()); } };
}

async function H1Viewer(box, query) {
  const metricSel = el("select", { class: "pill" }, ...[["wrong", "wrong-interval rate (harm)"], ["C_alarm", "conventional battery alarm rate"], ["A_alarm", "application monitor alarm rate"]].map(([v, t]) => el("option", { value: v }, t)));
  const heatBox = el("div"); const runsBox = el("div", { class: "scroll" }); const runBox = el("div", { class: "grid", style: "margin-top:10px" });
  box.append(el("div", { class: "page-title", style: "margin-top:16px" }, el("h1", {}, "H1 detection campaign (v0.18 Stage C), recorded"), metricSel),
    el("div", { class: "grid" }, panelBox("Cells: family × severity level", heatBox, 7, "recorded"), panelBox("Runs of the selected cell", runsBox, 5, "recorded")), runBox);
  const r = await api.get(`/api/studies/h1/cells?source=${App.sourceId}`);
  if (!r.ok) { heatBox.append(el("div", { class: "banner bad" }, r.error)); return; }
  const d = r.data; const fams = [...new Set(d.cells.map((c) => c.family))]; const levels = [0, 1, 2, 3, 4, 5];
  const heat = new Heat(heatBox, { size: "tall" });
  const cellAt = (f, l) => d.cells.find((c) => c.cell === `${f}-${l}`);
  function draw() {
    const k = metricSel.value;
    heat.setData({ rows: fams, cols: levels.map((l) => `level ${l}`), values: fams.map((f) => levels.map((l) => cellAt(f, l)?.[k]?.rate ?? NaN)), vmin: 0, vmax: 1,
      text: fams.map((f) => levels.map((l) => { const c = cellAt(f, l); return c ? `${(c[k].rate * 100).toFixed(0)}%` : ""; })),
      hint: (ri, ci) => { const c = cellAt(fams[ri], levels[ci]); return c ? `severity ${typeof c.severity === "number" ? fmt(c.severity, 5) : JSON.stringify(c.severity)} · ${c[k].k}/${c[k].n} · class ${c.class}` : ""; } });
  }
  metricSel.addEventListener("change", draw); draw();
  heatBox.append(el("p", { class: "footnote" }, `Thresholds (frozen): τ_C = ${fmt(d.thresholds.C, 7)}, τ_A = ${fmt(d.thresholds.A, 7)} · gates.json SHA-256 ${short(d.files["gates.json"], 16)} · click a cell`));
  heat.canvas.addEventListener("click", (e) => { const b = heat.canvas.getBoundingClientRect(); const { w, h } = heat.size(); const L = heat.layout(w, h);
    const ci = Math.floor((e.clientX - b.left - L.left) / L.cw), ri = Math.floor((e.clientY - b.top - L.top) / L.ch); if (fams[ri] !== undefined && levels[ci] !== undefined) openCell(`${fams[ri]}-${levels[ci]}`); });
  async function openCell(cell) {
    runsBox.replaceChildren(el("div", { class: "empty" }, `Reading ${cell}…`));
    const rr = await api.get(`/api/studies/h1/runs?source=${App.sourceId}&cell=${cell}`);
    if (!rr.ok) { runsBox.replaceChildren(el("div", { class: "banner bad" }, rr.error)); return; }
    runsBox.replaceChildren(el("div", { class: "sub" }, `${cell}: ${rr.data.runs.length} runs · ${Object.entries(rr.data.files).map(([f, s]) => `${f} ${short(s, 12)}`).join(" ")}`),
      el("table", { class: "tbl" }, el("tr", {}, ...["replica", "onset sweep", "interval", "RCT/APT post", "acceptance"].map((x) => el("th", {}, x))),
        ...rr.data.runs.slice(0, 256).map((x) => el("tr", { class: "click", onclick: () => openRun(cell, x.replica) }, el("td", { class: "num" }, x.replica), el("td", { class: "num" }, x.onset_sweep ?? "–"),
          el("td", {}, x.wrong === undefined || x.wrong === null ? "–" : x.wrong ? ev("failed", "wrong") : ev("ok", "covers")), el("td", { class: "num" }, `${x.health_tests?.rct_post ?? "–"}/${x.health_tests?.apt_post ?? "–"}`), el("td", { class: "num" }, x.acceptance_rate_4dp)))));
  }
  async function openRun(cell, replica) {
    runBox.replaceChildren(el("div", { class: "empty span-12" }, `Recomputing statistics of ${cell} replica ${replica} with h1lib…`));
    const x = await api.get(`/api/studies/h1/run?source=${App.sourceId}&cell=${cell}&replica=${replica}`);
    if (!x.ok) { runBox.replaceChildren(el("div", { class: "banner bad span-12" }, x.error)); return; }
    const d = x.data; runBox.replaceChildren();
    const mk = (title, end, stat, tau, colr, det) => { const sh = shell({ title, span: 6, cls: "recorded" }); runBox.append(sh.panel);
      const p = new Plot(sh.body, { sync: "h1rec", xname: "sweep", xlabel: "sweep (window end)" });
      p.setData({ series: [{ name: "statistic", x: end, y: stat, color: colr, kind: "step", width: 1.5 }], refs: [{ y: tau, label: `τ = ${fmt(tau, 6)}`, color: "var(--bad)" }],
        vlines: d.record?.onset_sweep ? [{ x: d.record.onset_sweep, label: "onset", color: "var(--warn)" }] : [], marks: det.alarm_end ? [{ x: det.alarm_end, y: stat[end.indexOf(det.alarm_end)], color: "var(--bad)" }] : [] });
      sh.panel.append(el("div", { class: "note" }, `post-onset alarm: ${det.post_onset_alarm ? `yes, window ending at sweep ${det.alarm_end} (latency ${det.latency_sweeps} sweeps)` : "no"} · whole-run alarm: ${det.whole_run_alarm ? "yes" : "no"}`)); };
    mk(`Conventional battery −log₁₀ min p — ${d.cell} #${d.replica}`, d.conv.end, d.conv.stat, d.thresholds.C, "var(--s2)", d.detection.C);
    mk(`Application monitor max |z| — ${d.cell} #${d.replica}`, d.app.end, d.app.stat, d.thresholds.A, "var(--s1)", d.detection.A);
    const sh = shell({ title: "Battery p-values per window (−log₁₀ p)", span: 8, cls: "recorded" }); runBox.append(sh.panel);
    new Heat(sh.body, { size: "tall" }).setData({ rows: d.conv.keys, cols: d.conv.end, values: d.conv.keys.map((_, k) => d.conv.p.map((row) => row[k] === null ? NaN : -Math.log10(Math.max(row[k], 1e-300)))), vmin: 0, vmax: 8, fmt: (v) => `−log₁₀ p = ${fmt(v, 4)}` });
    const harm = d.record?.harm; runBox.append(panelBox("Record", el("dl", { class: "kv" }, el("dt", {}, "family / severity"), el("dd", {}, `${d.record?.family} / ${JSON.stringify(d.record?.severity)}`),
      el("dt", {}, "harm interval"), el("dd", {}, harm ? `${harm.interval.map((v) => fmt(v, 6)).join(" … ")} · ${harm.wrong ? "wrong" : "covers"} the exact energy` : "–"),
      el("dt", {}, "onset sweep"), el("dd", {}, d.record?.onset_sweep ?? "–"), el("dt", {}, "local bytes"), el("dd", {}, d.record?.local_bytes),
      el("dt", {}, "stream SHA-256"), el("dd", {}, d.record?.local_sha256), el("dt", {}, "computed with"), el("dd", {}, d.computed_with),
      el("dt", {}, "files"), el("dd", {}, Object.entries(d.files).map(([f, s]) => `${f} ${s}`).join(" · "))), 4, "recorded"));
  }
}

boot().catch((e) => { $view.replaceChildren(el("div", { class: "banner bad" }, `The laboratory could not start: ${e.message}`)); });
