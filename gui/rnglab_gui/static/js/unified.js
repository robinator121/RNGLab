// RNGLab Unified screens: Mission, Research Atlas, Evidence (frozen studies), Provenance / Replay,
// Data & physical sources. They read /api/u/* (registry, evidence browser, index, provenance) and the
// laboratory's run API. Nothing here computes a scientific number; histograms of recorded gaps are
// display transforms and say so.
import { el, shell } from "./panels.js";
import { Plot, fmt } from "./charts.js";

// One distinct, non-ordinal glyph per class (shape identifies the class; it is not a score).
const STATUS_GLYPH = {
  "experimentally-validated": "■", "independently-verified": "●", "unit-tested": "▲", implemented: "◆",
  exploratory: "◇", planned: "○", "negative-result": "▼", superseded: "□", unbacked: "△",
};
export const badge = (status) => el("span", { class: `claim claim-${status}`, title: status }, `${STATUS_GLYPH[status] || ""} ${status}`);
const ev = (state, label) => el("span", { class: `ev ${state || "unavailable"}` }, label || state || "unavailable");
const short = (s, n = 10) => (s ? String(s).slice(0, n) : "–");
const box = (title, body, span = 6, cls) => { const sh = shell({ title, span, cls }); sh.body.classList.remove("flush"); sh.body.append(body); return sh.panel; };
const title = (h, sub) => el("div", { class: "page-title" }, el("h1", {}, h), el("span", { class: "sub" }, sub));
const empty = (text) => el("div", { class: "empty" }, text);
// matrix study ids are "study-<folder>" or "study-<folder-without-date>"; several folders map to more than one entry
const studyClass = (byStudy, id) => {
  const hits = Object.entries(byStudy).filter(([k]) => k === id || id.startsWith(k) || k.startsWith(id.replace(/-\d{8}$/, ""))).map(([, e]) => e);
  return hits.length ? el("span", {}, ...hits.map((e) => el("div", { title: e.title }, badge(e.status)))) : el("span", { class: "muted" }, "not in matrix");
};
const failBox = (err) => el("div", { class: "banner bad" }, `Could not load: ${err.message || err}`);

export function unifiedViews(ctx) {
  const { api, $view, App } = ctx;
  let registryCache = null;
  const registry = async () => (registryCache ||= await api.get("/api/u/registry"));

  // ------------------------------------------------------------------------------ Mission
  function Mission() {
    let timer = null;
    const statusBox = el("div"); const lines = el("div"); const runsBox = el("div"); const studiesBox = el("div"); const alerts = el("div"); const envBox = el("div");
    $view.append(title("Mission dashboard", "Capabilities, evidence quality, studies and runs. Status labels are evidence classes, not rankings."),
      el("div", { class: "grid" }, box("Evidence classes across all capabilities", statusBox, 7), box("Alerts and open audit findings", alerts, 5),
        box("Research lines", lines, 7), box("Environment", envBox, 5), box("Frozen studies (historical, read-only)", studiesBox, 7), box("Recent laboratory runs (newly generated)", runsBox, 5)));
    (async () => {
      try {
        const [reg, studies, env] = await Promise.all([registry(), api.get("/api/u/studies"), api.get("/api/u/env")]);
        const sum = reg.summary;
        if (!reg.matrix.available) statusBox.append(el("div", { class: "banner bad" }, "Capability matrix missing: docs/unified/capability_matrix.json"));
        statusBox.append(el("div", { class: "statusbar" }, ...Object.entries(sum.by_status).filter(([, n]) => n).map(([s, n]) =>
          el("a", { href: `#/atlas?status=${s}`, class: "statuscell" }, badge(s), el("span", { class: "v num" }, n)))),
        el("div", { class: "note" }, `${sum.total} capabilities from ${short(reg.matrix.generated_from_commit, 9)}. "negative-result" entries are findings, shown with equal weight.`));
        lines.append(el("table", { class: "tbl" }, el("tr", {}, el("th", {}, "line"), el("th", {}, "statuses")),
          ...Object.entries(sum.by_line).map(([line, counts]) => el("tr", { class: "click", onclick: () => { location.hash = `#/atlas?line=${encodeURIComponent(line)}`; } },
            el("td", {}, line), el("td", {}, ...Object.entries(counts).map(([s, n]) => el("span", { style: "margin-right:6px" }, badge(s), ` ×${n}`)))))));
        const byStudy = Object.fromEntries(reg.matrix.entries.filter((e) => e.kind === "Study").map((e) => [e.id.replace(/^study-/, ""), e]));
        studiesBox.append(el("table", { class: "tbl" }, el("tr", {}, el("th", {}, "study"), el("th", {}, "evidence class"), el("th", {}, "protocol status"), el("th", {}, "freeze"), el("th", {}, "result files")),
          ...studies.studies.map((s) => el("tr", { class: "click", onclick: () => { location.hash = `#/evidence/${s.id}`; } },
            el("td", {}, el("b", {}, s.id), el("div", { class: "muted small" }, s.title || ""), s.n_result_files === 0 ? el("div", { class: "small scope" }, "no results: not run") : null),
            el("td", {}, studyClass(byStudy, s.id)), el("td", { class: "mono" }, s.meta.status ?? "–"),
            el("td", {}, ev(s.freeze.state, s.freeze.state === "ok" ? "SHA-256 ok" : s.freeze.state)), el("td", { class: "num" }, s.n_result_files)))));
        const e = env.environment; const g = env.laboratory_git;
        envBox.append(el("table", { class: "tbl" }, ...[["Python", `${e.python} (${e.implementation})`], ["platform", e.platform], ["numpy / scipy", `${e.packages.numpy} / ${e.packages.scipy}`],
          ["laboratory commit", `${short(g.head, 12)} on ${g.branch}`], ["uncommitted", g.dirty ? `${g.dirty_paths} paths (diff ${short(g.diff_sha256, 12)})` : "none"]].map(([k, v]) => el("tr", {}, el("th", {}, k), el("td", { class: "mono" }, v)))));
        const al = [];
        for (const p of reg.matrix.problems || []) al.push(el("div", {}, ev("warning"), " registry: ", p));
        for (const s of studies.studies) if (s.freeze.state === "failed") al.push(el("div", {}, ev("failed"), ` ${s.id}: protocol.json no longer matches protocol.sha256`));
        if (g.dirty) al.push(el("div", {}, ev("warning"), ` laboratory checkout has ${g.dirty_paths} uncommitted paths; runs record only the dirty-file count, so a replay of them cannot be reported as same-code`));
        al.push(el("div", {}, ev("incomplete"), " Open audit questions are listed per capability in the Atlas (constraints column)."));
        alerts.append(...al);
      } catch (err) { statusBox.append(failBox(err)); }
    })();
    async function refreshRuns() {
      try {
        const runs = (await api.get("/api/runs")).runs.slice(0, 8);
        runsBox.replaceChildren(runs.length ? el("table", { class: "tbl" }, ...runs.map((r) => el("tr", { class: "click", onclick: () => { location.hash = `#/run/${r.run_id}`; } },
          el("td", { class: "mono" }, r.run_id.slice(9)), el("td", {}, r.model, r.pair_role ? el("span", { class: "pill" }, r.pair_role) : null),
          el("td", {}, el("span", { class: `state ${r.state}` }, r.state)), el("td", {}, r.verified ? ev(r.verified, `replay ${r.verified}`) : el("span", { class: "muted" }, "not replayed")))))
          : empty("No runs yet. Use the Laboratory or the Experiment builder to start a small one."));
      } catch (err) { runsBox.replaceChildren(failBox(err)); }
    }
    refreshRuns(); timer = setInterval(refreshRuns, 3000);
    return { destroy() { clearInterval(timer); } };
  }

  // ------------------------------------------------------------------------------ Atlas
  function Atlas(parts, query) {
    const filters = el("div", { class: "btnrow" }); const table = el("div"); const detail = el("div");
    $view.append(title("Research atlas", "Every identified capability with its source, evidence class and evidence files. Click a row for details."),
      el("div", { class: "grid" }, box("Filter", filters, 12), box("Capabilities", table, 8), box("Selected capability", detail, 4)));
    (async () => {
      let reg; try { reg = await registry(); } catch (err) { table.append(failBox(err)); return; }
      const entries = reg.matrix.entries;
      if (!entries.length) { table.append(empty("No capability matrix found (docs/unified/capability_matrix.json).")); return; }
      const state = { status: query.status || "", line: query.line || "", text: "" };
      const lines = [...new Set(entries.map((e) => e.research_line || "unassigned"))].sort();
      const sSel = el("select", { onchange: (e) => { state.status = e.target.value; draw(); } }, el("option", { value: "" }, "all statuses"), ...Object.keys(reg.statuses).map((s) => el("option", { value: s, selected: s === state.status }, s)));
      const lSel = el("select", { onchange: (e) => { state.line = e.target.value; draw(); } }, el("option", { value: "" }, "all lines"), ...lines.map((l) => el("option", { value: l, selected: l === state.line }, l)));
      const txt = el("input", { type: "search", placeholder: "search id, title, path…", oninput: (e) => { state.text = e.target.value.toLowerCase(); draw(); } });
      filters.append(sSel, lSel, txt, el("span", { class: "muted" }, "Legend: "), ...Object.keys(reg.statuses).map((s) => el("span", { title: reg.statuses[s] }, badge(s))));
      function show(e) {
        const fp = e.frozen_protocol || {};
        detail.replaceChildren(el("h3", {}, e.title || e.id), badge(e.status), el("p", { class: "muted" }, reg.statuses[e.status]),
          el("table", { class: "tbl" }, ...[["id", e.id], ["kind", e.kind], ["line", e.research_line], ["source", `${e.source?.branch || "–"} @ ${short(e.source?.commit, 9)}`],
            ["maturity", e.maturity], ["frozen protocol", `${fp.status || "none"}${fp.protocol_path ? ` (${fp.protocol_path})` : ""}`], ["reference", e.reference || "–"],
            ["integration", e.integration_method], ["reachable here", String(e.reachable_from_unified)]].map(([k, v]) => el("tr", {}, el("th", {}, k), el("td", {}, v ?? "–")))),
          el("h4", {}, "Outcome"), el("p", {}, e.outcome_summary || "–"), el("h4", {}, "Constraints"), el("p", {}, e.constraints || "–"),
          el("h4", {}, "Evidence"), ...(e.evidence || []).map((p) => el("div", {}, el("a", { href: evidenceHref(p) }, p))),
          el("h4", {}, "Code"), ...((e.source?.paths) || []).map((p) => el("div", { class: "mono" }, p)),
          el("h4", {}, "Tests"), ...(((e.test_coverage || {}).test_paths) || []).map((p) => el("div", { class: "mono" }, p)));
      }
      function draw() {
        const rows = entries.filter((e) => (!state.status || e.status === state.status) && (!state.line || (e.research_line || "unassigned") === state.line)
          && (!state.text || JSON.stringify(e).toLowerCase().includes(state.text)));
        table.replaceChildren(el("div", { class: "muted small" }, `${rows.length} of ${entries.length}`), el("table", { class: "tbl" },
          el("tr", {}, el("th", {}, "capability"), el("th", {}, "kind"), el("th", {}, "line"), el("th", {}, "status"), el("th", {}, "source")),
          ...rows.map((e) => el("tr", { class: "click", tabindex: "0", onclick: () => show(e), onkeydown: (k) => { if (k.key === "Enter") show(e); } },
            el("td", {}, el("b", {}, e.title || e.id), el("div", { class: "muted small" }, e.outcome_summary || ""),
              e.constraints ? el("div", { class: "small scope" }, "Scope / limits: ", e.constraints) : null), el("td", {}, e.kind), el("td", {}, e.research_line),
            el("td", {}, badge(e.status)), el("td", { class: "mono small" }, `${e.source?.branch || ""} ${short(e.source?.commit, 7)}`)))));
        if (rows[0] && !detail.childNodes.length) show(rows[0]);
      }
      draw();
    })();
    return null;
  }
  const evidenceHref = (p) => {
    const m = /^research\/([^/]+)/.exec(p);
    return m ? `#/evidence/${m[1]}?file=${encodeURIComponent(p)}` : `#/evidence?file=${encodeURIComponent(p)}`;
  };

  // ------------------------------------------------------------------------------ Evidence browser
  function Evidence(parts, query) {
    const list = el("div"); const files = el("div"); const viewer = el("div");
    const study = parts[0];
    $view.append(title("Evidence: frozen studies", "Historical results, read-only. Every file is shown with its SHA-256; protocol freezes and manifests are checked on load."),
      el("div", { class: "grid" }, box("Studies", list, 3, "recorded"), box(study ? `research/${study}` : "Files", files, 4, "recorded"), box("File", viewer, 5, "recorded")));
    const srcQ = App.sourceId ? `source=${App.sourceId}` : "";
    async function openFile(path) {
      viewer.replaceChildren(el("div", { class: "muted" }, "loading…"));
      try {
        const f = await api.get(`/api/u/file?path=${encodeURIComponent(path)}&${srcQ}`);
        let text = f.text;
        if (f.kind === "json" && text && !f.truncated) { try { text = JSON.stringify(JSON.parse(text), null, 2); } catch { /* keep raw */ } }
        viewer.replaceChildren(el("div", { class: "mono small" }, f.path), el("div", { class: "mono small" }, `SHA-256 ${f.sha256} · ${f.bytes.toLocaleString()} bytes${f.truncated ? " · preview truncated" : ""}`),
          f.origin === "historical" ? el("div", { class: "origin historical" }, `HISTORICAL · COMMITTED ${short(f.last_commit, 9)} · READ-ONLY`)
            : el("div", { class: "origin unavailable" }, f.git_tracked ? "MODIFIED SINCE COMMIT · NOT A HISTORICAL RECORD" : "UNTRACKED FILE · NOT A HISTORICAL RECORD"),
          f.text === null ? empty("Binary file: digest only.") : el("pre", { class: "fileview" }, text));
      } catch (err) { viewer.replaceChildren(failBox(err)); }
    }
    (async () => {
      try {
        const s = await api.get(`/api/u/studies?${srcQ}`);
        list.append(el("div", { class: "muted small" }, `code source ${s.source.branch || "(detached)"} @ ${short(s.source.head, 9)}`),
          ...s.studies.map((x) => el("a", { class: `listrow ${x.id === study ? "active" : ""}`, href: `#/evidence/${x.id}` },
            el("div", {}, el("b", {}, x.id), " ", ev(x.freeze.state, x.freeze.state === "ok" ? "frozen ✓" : x.freeze.state)),
            el("div", { class: "muted small" }, `${x.meta.status ?? "no protocol status"} · ${x.n_result_files} result files`))));
      } catch (err) { list.append(failBox(err)); }
      if (!study) { files.append(empty("Choose a study.")); if (query.file) openFile(query.file); else viewer.append(empty("Choose a file.")); return; }
      try {
        const d = await api.get(`/api/u/studies/${study}?${srcQ}`);
        const fz = d.freeze;
        files.append(el("div", {}, ev(fz.state, `protocol freeze: ${fz.state}`), fz.recorded ? el("div", { class: "mono small" }, `recorded ${fz.recorded}`) : null, el("div", { class: "mono small" }, fz.actual ? `actual   ${fz.actual}` : fz.reason || "")));
        for (const m of d.manifests) files.append(el("div", { class: "manifest" }, el("b", {}, m.file), ...m.checks.map((c) => el("div", { class: "small" }, ev(c.state), " ", el("span", { class: "mono" }, c.name)))));
        files.append(el("table", { class: "tbl" }, el("tr", {}, el("th", {}, "file"), el("th", {}, "bytes"), el("th", {}, "SHA-256")),
          ...d.files.map((f) => el("tr", { class: "click", onclick: () => openFile(f.path) }, el("td", { class: "mono small" }, f.path.replace(`research/${study}/`, "")),
            el("td", { class: "num" }, f.bytes.toLocaleString()), el("td", { class: "mono small", title: f.sha256 }, short(f.sha256, 12))))));
        const readme = d.files.find((f) => /\/(README|REPORT)\.md$/.test(f.path));
        openFile(query.file || readme?.path || d.files[0]?.path);
      } catch (err) { files.append(failBox(err)); }
    })();
    return null;
  }

  // ------------------------------------------------------------------------------ Provenance / replay
  function Provenance() {
    const branches = el("div"); const runs = el("div"); const search = el("div");
    $view.append(title("Provenance, replay and audit", "Branch lineage, run identities and bit-for-bit replay checks of laboratory runs."),
      el("div", { class: "grid" }, box("Runs: replay verification", runs, 7), box("Index search (runs and studies)", search, 5), box("Branches and lineage (read-only Git)", branches, 12)));
    async function loadRuns() {
      const all = (await api.get("/api/runs")).runs;
      runs.replaceChildren(all.length ? el("table", { class: "tbl" }, el("tr", {}, el("th", {}, "run"), el("th", {}, "model / arm"), el("th", {}, "code"), el("th", {}, "digest"), el("th", {}, "replay"), el("th", {}, "")),
        ...all.slice(0, 40).map((r) => el("tr", {}, el("td", { class: "mono" }, el("a", { href: `#/run/${r.run_id}` }, r.run_id.slice(9))), el("td", {}, r.model, r.pair_role ? el("span", { class: "pill" }, r.pair_role) : null),
          el("td", { class: "mono small" }, `${short(r.code_head, 8)}${r.code_dirty ? " (dirty)" : ""}`), el("td", { class: "mono small", title: r.digest || "" }, short(r.digest, 12)),
          el("td", {}, r.verified ? ev(r.verified, r.verified) : el("span", { class: "muted" }, "–")),
          el("td", {}, r.state === "completed" && r.digest ? el("button", { class: "btn small", onclick: async (e) => { e.target.disabled = true; try { await api.post(`/api/runs/${r.run_id}/verify`); setTimeout(loadRuns, 1500); } catch (err) { e.target.replaceWith(failBox(err)); } } }, "replay & compare") : null))))
        : empty("No runs yet."));
    }
    loadRuns().catch((err) => runs.append(failBox(err)));
    const timer = setInterval(() => loadRuns().catch(() => {}), 4000);
    const q = el("input", { type: "search", placeholder: "model, label, study…" }); const out = el("div");
    const doSearch = async () => {
      try {
        const r = await api.get(`/api/u/search?q=${encodeURIComponent(q.value)}`);
        out.replaceChildren(el("h4", {}, `studies (historical) · ${r.studies.length}`), ...r.studies.map((s) => el("div", {}, el("a", { href: `#/evidence/${s.study_id}` }, s.study_id), " ", ev(s.freeze_state))),
          el("h4", {}, `runs (generated) · ${r.runs.length}`), ...r.runs.map((x) => el("div", {}, el("a", { href: `#/run/${x.run_id}` }, x.run_id.slice(9)), ` ${x.model} ${x.pair_role || ""}`)));
      } catch (err) { out.replaceChildren(failBox(err)); }
    };
    search.append(el("div", { class: "btnrow" }, q, el("button", { class: "btn", onclick: doSearch }, "search"),
      el("button", { class: "btn", onclick: async () => { await api.post("/api/u/index/rebuild", { source: App.sourceId }); doSearch(); } }, "rebuild index")), out);
    q.addEventListener("keydown", (e) => { if (e.key === "Enter") doSearch(); });
    (async () => {
      try {
        const g = await api.get("/api/u/branches");
        branches.append(el("div", { class: "muted small" }, `relative to the laboratory checkout HEAD ${short(g.head, 12)}`), el("table", { class: "tbl" },
          el("tr", {}, el("th", {}, "ref"), el("th", {}, "commit"), el("th", {}, "date"), el("th", {}, "in HEAD?"), el("th", {}, "unique commits"), el("th", {}, "subject")),
          ...g.refs.map((r) => el("tr", { class: r.is_backup ? "dim" : "" }, el("td", { class: "mono" }, r.ref, r.is_backup ? el("span", { class: "pill" }, "backup, record only") : null),
            el("td", { class: "mono" }, short(r.commit, 9)), el("td", { class: "small" }, (r.date || "").slice(0, 10)),
            el("td", {}, r.ancestor_of_head ? ev("ok", "ancestor") : ev("warning", "diverged")), el("td", { class: "num" }, r.unique_commits ?? "–"), el("td", { class: "small" }, r.subject)))));
      } catch (err) { branches.append(failBox(err)); }
    })();
    return { destroy() { clearInterval(timer); } };
  }

  // ------------------------------------------------------------------------------ Data & physical sources
  function Sources(parts, query) {
    const head = el("div"); const wave = el("div"); const gaps = el("div"); const facts = el("div");
    const demo = query.demo === "1";
    $view.append(title("Data and physical sources", "Recorded detector exports are audited read-only. Statistical regularity is not entropy: no entropy credit is ever assigned here."),
      el("div", { class: "btnrow" }, el("a", { class: `btn ${demo ? "" : "primary"}`, href: "#/sources" }, "recorded export"), el("a", { class: `btn ${demo ? "primary" : ""}`, href: "#/sources?demo=1" }, "synthetic demo")),
      head, el("div", { class: "grid" }, box("Waveforms (first records)", wave, 8, demo ? "illustrative" : "recorded"), box("Audit", facts, 4, demo ? "illustrative" : "recorded"),
        box("Timestamp gaps (raw ticks): lattice and quantisation", gaps, 12, demo ? "illustrative" : "recorded")));
    (async () => {
      let r; try { r = await api.get(`/api/u/detector?demo=${demo ? 1 : 0}&${App.sourceId ? `source=${App.sourceId}` : ""}`); } catch (err) { head.append(failBox(err)); return; }
      if (!r.ok) { head.append(el("div", { class: "banner bad" }, r.error || "detector query failed")); return; }
      const d = r.data;
      head.append(el("div", { class: `origin ${d.data_class}` }, d.label), el("p", { class: "muted" }, d.note));
      if (d.data_class === "unavailable") { wave.append(empty("No recorded export configured. Try the synthetic demo, or restart with --detector-file.")); return; }
      if (demo) for (const c of [wave, gaps]) c.classList.add("watermark");
      const p = new Plot(wave, { xname: "sample", xlabel: "sample index", yname: "ADC code" });
      p.setData({ series: d.waveforms.map((w, i) => ({ name: `#${i} t=${d.timestamps[i]}`, x: w.map((_, k) => k), y: w, width: 1 })),
        refs: [{ y: 65535, label: "ceiling 65535 (saturation)", color: "var(--bad)" }, { y: 0, label: "floor 0", color: "var(--warn)" }] });
      // gap histogram: display transform of recorded gaps; bins of width = lattice gcd when known
      const g = d.gaps.filter((x) => x > 0); const lat = d.timing?.positive_gap_gcd_raw_units || 1;
      if (g.length) {
        const max = Math.max(...g); const bw = Math.max(lat, Math.ceil(max / 200 / lat) * lat); const nb = Math.ceil(max / bw) + 1; const h = new Array(nb).fill(0);
        for (const x of g) h[Math.floor(x / bw)] += 1;
        const gp = new Plot(gaps, { xname: "gap (ticks)", xlabel: `gap (raw ticks; bin ${bw})`, yname: "count" });
        gp.setData({ series: [{ name: "gaps", x: h.map((_, i) => i * bw), y: h, kind: "step", width: 1.5 }] });
        gaps.append(el("div", { class: "note" }, `Histogram drawn by the interface from ${g.length.toLocaleString()} recorded positive gaps (display only). Positive-gap GCD: ${lat} raw tick(s)${lat > 1 ? " — timestamps lie on a coarse lattice" : ""}.`));
      } else gaps.append(empty("No positive gaps."));
      const t = d.timing || {};
      facts.append(el("table", { class: "tbl" }, ...[["records", d.records], ["ceiling fraction (65535)", fmt(d.ceiling_fraction, 4)], ["floor fraction (0)", fmt(d.floor_fraction, 4)],
        ["records touching ceiling", fmt(d.records_with_ceiling_fraction, 4)], ["distinct codes", d.distinct_codes ?? "–"], ["gap lattice (GCD)", t.positive_gap_gcd_raw_units ?? "–"],
        ["zero / backward gaps", `${t.zero_gaps ?? "–"} / ${t.backward_gaps ?? "–"}`], ["within-record lag-1 corr.", d.within_record_raw_lag1_correlation === undefined ? "–" : fmt(d.within_record_raw_lag1_correlation, 4)],
        ["validated entropy credit", `${d.validated_entropy_credit_bits} bits`], ["source SHA-256", short(d.source_sha256, 16)]].map(([k, v]) => el("tr", {}, el("th", {}, k), el("td", { class: "mono" }, v)))),
        d.ceiling_fraction > 0 ? el("div", { class: "banner bad" }, "Saturation present: clipped waveforms carry less information than their bit width suggests.") : null,
        el("div", { class: "note" }, d.crypto_use || "Not a key source."));
    })();
    return null;
  }

  return { mission: Mission, atlas: Atlas, evidence: Evidence, provenance: Provenance, sources: Sources };
}
