// Compound experiments: build a spec, validate it against the compatibility contracts, run it through
// the shared engine (rnglab_core, in a worker process), watch it, and read one integrated record.
// Every number shown comes from the record; the interface computes nothing scientific.
import { el, shell } from "./panels.js";
import { Plot, fmt } from "./charts.js";

const short = (s, n = 10) => (s ? String(s).slice(0, n) : "–");
const ev = (state, label) => el("span", { class: `ev ${state || "unavailable"}` }, label || state || "unavailable");
const box = (title, body, span = 6, cls) => { const sh = shell({ title, span, cls }); sh.body.classList.remove("flush"); sh.body.append(body); return sh.panel; };
const pct = (x) => (x === null || x === undefined ? "–" : `${(100 * x).toFixed(0)} %`);
const num = (x, d = 3) => (x === null || x === undefined || !Number.isFinite(x) ? "–" : fmt(x, d));
const COND_COLOR = { healthy: "var(--s3)", faulted: "var(--s2)", intervened: "var(--s1)" };

export function experimentViews(ctx) {
  const { api, $view, App } = ctx;
  let catalog = null;
  const getCatalog = async () => (catalog ||= await api.get(`/api/x/catalog${App.sourceId ? `?source=${App.sourceId}` : ""}`));

  // ------------------------------------------------------------------------------ list + builder
  function Experiments(parts) {
    if (parts[0]) return Detail(parts[0]);
    const form = el("div"); const check = el("div"); const list = el("div");
    $view.append(el("div", { class: "page-title" }, el("h1", {}, "Compound experiments"),
      el("span", { class: "sub" }, "One spec: one source, one fault family, several models, matched healthy and faulted arms, shared measurements, one record.")),
      el("div", { class: "grid" }, box("Experiment builder", form, 7), box("Validation against the compatibility contracts", check, 5),
        box("Experiments (generated records)", list, 12)));
    let timer = null;
    const refreshList = async () => {
      try {
        const xs = (await api.get("/api/x")).experiments;
        list.replaceChildren(xs.length ? el("table", { class: "tbl" }, el("tr", {}, ...["experiment", "title", "models", "fault", "state", "units", "result digest"].map((h) => el("th", {}, h))),
          ...xs.map((x) => el("tr", { class: "click", onclick: () => { location.hash = `#/experiments/${x.id}`; } },
            el("td", { class: "mono" }, x.id, x.replay_of ? el("span", { class: "pill" }, "replay") : null), el("td", {}, x.title || "–"),
            el("td", {}, (x.models || []).join(", ")), el("td", {}, x.fault || "none"),
            el("td", {}, el("span", { class: `state ${x.running ? "running" : x.state}` }, x.running ? "running" : x.state || "–")),
            el("td", { class: "num" }, `${x.units_done ?? "–"} / ${x.units_total ?? "–"}`), el("td", { class: "mono" }, short(x.result_digest, 12)))))
          : el("div", { class: "empty" }, "No experiments yet."));
      } catch (err) { list.replaceChildren(el("div", { class: "banner bad" }, err.message)); }
    };
    refreshList(); timer = setInterval(refreshList, 3000);
    (async () => {
      let cat; try { cat = await getCatalog(); } catch (err) { form.append(el("div", { class: "banner bad" }, `catalog: ${err.message}`)); return; }
      const shared = cat.faults.filter((f) => f.shared);
      const state = { title: "cross-domain SHIFT", namespace: "rnglab-unified-explore", source: "pcg64", fault: "SHIFT", replicates: 8, pairing: "common",
        fault_params: { severity: 0.02, onset_bytes: 4096 }, intervention: false, models: {} };
      const defaults = { "pimc-harmonic": ["separate", "acceptance"], "vmc-harmonic": ["separate", "acceptance"], "ising-2d": ["separate", "acceptance"],
        "nuclear-b2": ["single", "decay", 0], "nuclear-a": ["single", "decay", 0] };
      for (const m of cat.models) state.models[m.id] = { on: ["vmc-harmonic", "ising-2d", "nuclear-a"].includes(m.id), layout: (defaults[m.id] || [Object.keys(m.inputs)[0]])[0],
        role: (defaults[m.id] || [])[1] || "", onset: (defaults[m.id] || [])[2] ?? "" };
      const inp = (key, attrs = {}) => { const i = el("input", { value: state[key], ...attrs }); i.addEventListener("input", () => { state[key] = attrs.type === "number" ? Number(i.value) : i.value; validate(); }); return i; };
      const sel = (options, value, on) => { const s = el("select", {}, ...options.map(([v, t]) => el("option", { value: v, selected: v === value }, t))); s.addEventListener("change", () => { on(s.value); validate(); }); return s; };
      const faultBox = el("div");
      const renderFaultParams = () => {
        const f = cat.faults.find((x) => x.family === state.fault);
        faultBox.replaceChildren(...(f ? Object.entries(f.parameters).map(([name, meta]) => {
          const i = el("input", { value: state.fault_params[name] ?? "", placeholder: meta.unit || "" });
          i.addEventListener("input", () => { const v = i.value.includes(",") ? i.value.split(",").map(Number) : (i.value === "" ? undefined : (isNaN(Number(i.value)) ? i.value : Number(i.value))); if (v === undefined) delete state.fault_params[name]; else state.fault_params[name] = v; validate(); });
          return el("label", { class: "field" }, el("span", {}, `${name} `, el("small", { class: "muted" }, meta.unit || "")), i);
        }) : []), f ? el("div", { class: "note" }, `${f.semantics} — acts on ${f.levels.join(", ")}${f.requires_aligned_words ? "; needs single aligned 8-byte reads" : ""}. Source: ${f.origin}`) : null);
      };
      const modelRows = cat.models.map((m) => {
        const s = state.models[m.id];
        const on = el("input", { type: "checkbox", checked: s.on }); on.addEventListener("change", () => { s.on = on.checked; validate(); });
        const layout = sel(Object.keys(m.inputs).map((k) => [k, k]), s.layout, (v) => { s.layout = v; });
        const role = el("input", { value: s.role, size: 11 }); role.addEventListener("input", () => { s.role = role.value; validate(); });
        const onset = el("input", { value: s.onset, size: 7, placeholder: "spec" }); onset.addEventListener("input", () => { s.onset = onset.value; validate(); });
        return el("tr", {}, el("td", {}, on), el("td", {}, el("b", {}, m.title), el("div", { class: "small muted" }, `${m.implementation} · clock: ${m.clock} · roles: ${Object.values(m.inputs).map((x) => x.roles.join("/")).join(" | ")}`)),
          el("td", {}, layout), el("td", {}, role), el("td", {}, onset));
      });
      const ivToggle = el("input", { type: "checkbox" }); ivToggle.addEventListener("change", () => { state.intervention = ivToggle.checked; validate(); });
      form.append(
        el("div", { class: "formgrid" },
          el("label", { class: "field" }, el("span", {}, "Title"), inp("title")),
          el("label", { class: "field" }, el("span", {}, "Seed namespace"), inp("namespace")),
          el("label", { class: "field" }, el("span", {}, "Healthy source"), sel(cat.sources.filter((s) => s.id !== "recorded-file").map((s) => [s.id, `${s.id} — ${s.kind}`]), state.source, (v) => { state.source = v; })),
          el("label", { class: "field" }, el("span", {}, "Fault family"), sel([["", "none (healthy only)"], ...shared.map((f) => [f.family, `${f.family} (${f.levels.join("/")})`])], state.fault, (v) => { state.fault = v || null; state.fault_params = {}; renderFaultParams(); })),
          el("label", { class: "field" }, el("span", {}, "Replicates"), inp("replicates", { type: "number", min: 1, max: 512 })),
          el("label", { class: "field" }, el("span", {}, "Pairing"), sel([["common", "common random numbers (paired)"], ["independent", "independent streams per condition"]], state.pairing, (v) => { state.pairing = v; }))),
        faultBox,
        el("h4", {}, "Models (each keeps its own endpoints and clock)"),
        el("table", { class: "tbl" }, el("tr", {}, ...["", "model", "layout", "faulted role(s)", "onset override (bytes)"].map((h) => el("th", {}, h))), ...modelRows),
        el("label", { class: "field" }, ivToggle, el("span", {}, " Authorised intervention: switch the faulted role to an independent source after the first alarming monitor window (4096 bytes, α = 0.01)")),
        el("div", { class: "note" }, `Not shareable, listed for completeness: ${cat.faults.filter((f) => !f.shared).map((f) => f.family).join(", ")}. On other source interfaces: ${Object.keys(cat.not_integrated).join(", ")}.`),
        el("div", { class: "btnrow" }, el("button", { class: "btn primary", id: "runbtn", onclick: run }, "Run experiment"), el("span", { class: "muted small", id: "runmsg" })));
      renderFaultParams();
      function spec() {
        const models = Object.entries(state.models).filter(([, s]) => s.on).map(([id, s]) => ({ model: id, layout: s.layout,
          faulted_roles: s.role ? s.role.split(",").map((x) => x.trim()).filter(Boolean) : [], params: {},
          fault_overrides: s.onset === "" || s.onset === undefined ? {} : { onset_bytes: Number(s.onset) } }));
        const out = { title: state.title, namespace: state.namespace, source: state.source, fault: state.fault, fault_params: state.fault_params,
          models, replicates: state.replicates, pairing: state.pairing };
        if (state.intervention) out.intervention = { kind: "switch-to-independent-source", authorized: true, window_bytes: 4096, alpha: 0.01 };
        return out;
      }
      let vt = null;
      function validate() {
        clearTimeout(vt);
        vt = setTimeout(async () => {
          try {
            const r = await api.post("/api/x/validate", { spec: spec(), source: App.sourceId });
            check.replaceChildren(r.ok ? ev("ok", `valid · ${r.units} units`) : ev("failed", "not valid"),
              ...(r.errors || []).map((e) => el("div", { class: "banner bad" }, e)),
              ...(r.decisions || []).map((d) => el("div", { class: "small" }, ev(d.compatible ? "ok" : "failed", d.compatible ? "compatible" : "refused"), ` ${d.model}: ${d.reason}`)),
              r.spec_digest ? el("div", { class: "mono small" }, `spec digest ${short(r.spec_digest, 16)}`) : null);
            document.getElementById("runbtn").disabled = !r.ok;
          } catch (err) { check.replaceChildren(el("div", { class: "banner bad" }, err.message)); }
        }, 250);
      }
      async function run() {
        try { const r = await api.post("/api/x/run", { spec: spec(), source: App.sourceId }); location.hash = `#/experiments/${r.experiment_id}`; }
        catch (err) { document.getElementById("runmsg").textContent = err.message; }
      }
      validate();
    })();
    return { destroy() { clearInterval(timer); } };
  }

  // ------------------------------------------------------------------------------ detail
  function Detail(id) {
    const head = el("div"); const prog = el("div"); const table = el("div"); const models = el("div", { class: "grid" });
    const iv = el("div"); const acc = el("div"); const prov = el("div"); const limits = el("div");
    $view.append(el("div", { class: "page-title" }, el("h1", {}, "Experiment"), el("span", { class: "mono sub" }, id), el("a", { href: "#/experiments", style: "margin-left:12px" }, "← all experiments")),
      head, el("div", { class: "grid" }, box("Progress (live)", prog, 12), box("Cross-domain comparison (one row per model, each in its own units and clock)", table, 12, "derived")),
      models, el("div", { class: "grid" }, box("Detection → forecast → intervention → recovery", iv, 12, "derived"),
        box("Random-stream accounting (consumed bytes and SHA-256 per role)", acc, 7, "recorded"), box("Provenance, seal and replay", prov, 5), box("Interpretation limits", limits, 12)));
    let es = null; let done = 0;
    const units = [];
    const renderProgress = (total, last) => prog.replaceChildren(el("div", { class: "progress" }, el("div", { style: `width:${total ? Math.round(100 * done / total) : 0}%` })),
      el("div", { class: "small" }, `${done} / ${total || "?"} units`, last ? ` · last: ${last.model} ${last.condition} #${last.replicate}${last.error ? ` — error: ${last.error}` : ""}` : ""),
      el("div", { class: "btnrow" }, el("button", { class: "btn", onclick: async () => { try { await api.post(`/api/x/${id}/cancel`); } catch (e) { prog.append(el("div", { class: "banner bad" }, e.message)); } } }, "cancel")));
    async function load() {
      const d = await api.get(`/api/x/${id}`);
      const s = d.spec?.spec || {};
      head.replaceChildren(el("div", { class: "origin historical" }, "GENERATED · STORED RECORD"),
        el("div", {}, el("b", {}, s.title || id), ` · source ${s.source} · fault ${s.fault || "none"} ${JSON.stringify(s.fault_params || {})} · ${s.replicates} replicates · pairing ${s.pairing} · namespace `, el("span", { class: "mono" }, s.namespace)));
      if (d.running) {
        renderProgress(d.status?.units_total, null);
        if (!es) {
          es = new EventSource(`/api/x/${id}/events`);
          es.onmessage = (e) => { const m = JSON.parse(e.data); if (m.t === "unit") { done = m.done; units.push(m); renderProgress(m.total, m); }
            if (m.t === "closed" || m.t === "done") { es.close(); es = null; setTimeout(load, 300); } };
        }
        return;
      }
      prog.replaceChildren(el("div", {}, ev(d.status?.state === "completed" ? "ok" : d.status?.state === "cancelled" ? "warning" : "failed", d.status?.state), ` ${d.status?.units_done} / ${d.status?.units_total} units`));
      render(d);
    }
    function render(d) {
      const a = d.analysis;
      if (!a) { table.replaceChildren(el("div", { class: "empty" }, "No analysis (cancelled or failed). Units recorded so far are kept.")); }
      else {
        const dvh = (x) => x ? `${x.alarm_and_harm} / ${x.harm_no_alarm} / ${x.alarm_no_harm} / ${x.neither}` : "–";
        const H = ["model", "primary endpoint (unit)", "faulted role → what its uniforms drive", "clock · onset at", "paired mean difference",
          "paired test, p (min attainable)", "Holm p", "harm H → F (excess)", "battery alarm H → F (window share)",
          "alarm&harm / harm only / alarm only / neither — H | F", "warnings"];
        table.replaceChildren(el("div", { class: "banner info" }, a.evidence_status || "evidence status not recorded"),
          el("table", { class: "tbl" }, el("tr", {}, ...H.map((h) => el("th", {}, h))),
          ...a.cross_domain.map((r) => el("tr", {}, el("td", {}, el("b", {}, r.model)), el("td", {}, `${r.primary_endpoint} (${r.unit})`),
            el("td", { class: "small" }, ...Object.entries(r.role_semantics || {}).map(([k, v]) => el("div", {}, el("b", {}, k), ` → ${v}`))),
            el("td", { class: "small" }, `${r.clock} · ${r.onset_fraction_of_run === null || r.onset_fraction_of_run === undefined ? "–" : pct(r.onset_fraction_of_run)} of run`),
            el("td", { class: "num" }, num(r.paired_mean_difference, 4)),
            el("td", { class: "small" }, `${r.paired_test || "–"}${r.paired_p === null || r.paired_p === undefined ? "" : `, p = ${num(r.paired_p, 3)}`}${r.min_attainable_p ? ` (≥ ${num(r.min_attainable_p, 3)})` : ""}`),
            el("td", {}, r.holm_adjusted_p === null || r.holm_adjusted_p === undefined ? "–" : el("span", { class: r.effect_detected_at_alpha ? "ev failed" : "ev ok" }, num(r.holm_adjusted_p, 3))),
            el("td", { class: "num", title: r.harm_rule }, `${pct(r.harm_healthy)} → ${pct(r.harm_faulted)} (${r.excess_harm === null || r.excess_harm === undefined ? "–" : (r.excess_harm >= 0 ? "+" : "") + pct(r.excess_harm)})`),
            el("td", { class: "num" }, `${pct(r.battery_alarm_healthy)} → ${pct(r.battery_alarm_faulted)} (${pct(r.battery_window_fraction_of_run)})`),
            el("td", { class: "num" }, `${dvh(r.detection_vs_harm?.healthy)} | ${dvh(r.detection_vs_harm?.faulted)}`),
            el("td", { class: "small" }, ...(r.warnings || []).map((w) => el("div", { class: "scope" }, w)))))),
          el("div", { class: "note" }, `Holm family: ${a.multiplicity.family_size} tests (${a.multiplicity.family}), α = ${a.multiplicity.alpha}. Harm rule per model is shown on hover; H = healthy arm, F = faulted arm.`));
        limits.replaceChildren(...a.interpretation_limits.map((t) => el("div", { class: "small" }, "• ", t)));
      }
      // per-model panels: estimates with the model's own interval vs the exact reference, per arm
      models.replaceChildren();
      const conds = [...new Set(d.units.map((u) => u.condition))];
      for (const model of [...new Set(d.units.map((u) => u.model))]) {
        const mu = d.units.filter((u) => u.model === model);
        const obs = mu.find((u) => u.endpoints?.length)?.endpoints[0];
        if (!obs) continue;
        const sh = shell({ title: `${model}: ${obs.observable} (${obs.unit}) vs exact reference`, span: 6, cls: "simulated" });
        const p = new Plot(sh.body, { xname: "replicate", xlabel: "replicate (arms offset)", yname: obs.observable });
        p.setData({ series: conds.map((c, k) => {
          const us = mu.filter((u) => u.condition === c).sort((x, y) => x.replicate - y.replicate);
          const e = us.map((u) => u.endpoints?.find((x) => x.observable === obs.observable));
          return { name: c, kind: "errbar", color: COND_COLOR[c], x: us.map((u) => u.replicate + (k - 1) * 0.2), y: e.map((x) => x?.estimate ?? null),
            lo: e.map((x) => x?.interval?.[0] ?? (x?.standard_error ? x.estimate - 1.96 * x.standard_error : null)), hi: e.map((x) => x?.interval?.[1] ?? (x?.standard_error ? x.estimate + 1.96 * x.standard_error : null)) };
        }), refs: obs.reference !== null ? [{ y: obs.reference, label: `exact ${fmt(obs.reference, 6)} (${obs.reference_source})`, color: "var(--reference)" }] : [] });
        sh.panel.append(el("div", { class: "note" }, `${obs.meaning}. Bars: ${obs.interval_method || "±1.96 SE"}. ${(mu[0].notes || []).join(" ")}`));
        models.append(sh.panel);
      }
      // intervention
      const ivs = a ? Object.entries(a.per_model).filter(([, pm]) => pm.intervention) : [];
      const rateTxt = (x) => (x ? `${x.events}/${x.n}` : "–");
      iv.replaceChildren(...(ivs.length ? [el("table", { class: "tbl" }, el("tr", {}, ...["model", "detected after onset", "median latency (bytes after onset)", "pre-onset alarms", "healthy arm: runs with alarm · per-window false alarms",
          "effect without → with intervention (paired mean difference)", "recovery (95 % bootstrap)", "counterfactual exposure"].map((h) => el("th", {}, h))),
        ...ivs.map(([m, pm]) => { const x = pm.intervention; return el("tr", {}, el("td", {}, m), el("td", { class: "num" }, rateTxt(x.detected_post_onset)),
          el("td", { class: "num" }, x.detection_latency_bytes?.median ?? "–"), el("td", { class: "num" }, x.pre_onset_alarms),
          el("td", { class: "num" }, `${rateTxt(x.healthy_arm?.runs_with_any_alarm)} · ${rateTxt(x.healthy_arm?.per_window_false_alarm_rate)}`),
          el("td", { class: "num" }, `${num(x.effect_faulted_vs_healthy?.mean_difference, 4)} → ${num(x.effect_intervened_vs_healthy?.mean_difference, 4)}`),
          el("td", { class: "num", title: x.recovery_note || "" }, x.recovery_fraction === null || x.recovery_fraction === undefined ? (x.recovery_note || "–")
            : `${pct(x.recovery_fraction)}${x.recovery_ci95_bootstrap ? ` [${pct(x.recovery_ci95_bootstrap[0])}, ${pct(x.recovery_ci95_bootstrap[1])}]` : ""}`),
          el("td", { class: "small" }, `${x.counterfactual_exposure?.label || ""}: ${(x.counterfactual_exposure?.pairs_counterfactual_vs_realised || []).length} pairs`)); })),
        el("div", { class: "note" }, `Forecast: ${ivs[0][1].intervention.forecast?.status} (${ivs[0][1].intervention.forecast?.note}). Counterfactual exposure: ${ivs[0][1].intervention.counterfactual_exposure?.definition}.`)]
        : [el("div", { class: "empty" }, "This experiment declared no intervention.")]));
      // stream accounting
      acc.replaceChildren(el("div", { class: "scroll", style: "max-height:320px" }, el("table", { class: "tbl" }, el("tr", {}, ...["model", "arm", "rep", "role", "fault", "consumed bytes", "past onset", "SHA-256 consumed", "SHA-256 healthy base"].map((h) => el("th", {}, h))),
        ...d.units.flatMap((u) => (u.streams || []).map((s) => el("tr", {}, el("td", {}, u.model), el("td", {}, u.condition), el("td", { class: "num" }, u.replicate), el("td", {}, s.role), el("td", {}, s.fault || "–"),
          el("td", { class: "num" }, s.consumed_bytes.toLocaleString()), el("td", { class: "num" }, s.post_onset_bytes ?? "–"), el("td", { class: "mono small" }, short(s.consumed_sha256, 12)), el("td", { class: "mono small" }, short(s.base_sha256, 12))))))));
      // provenance
      const m = d.manifest || {};
      const replayOut = el("div");
      prov.replaceChildren(el("table", { class: "tbl" }, ...[["seal (record.sha256)", ev(d.seal.state, d.seal.state)], ["spec digest", short(m.spec_digest, 16)], ["result digest", short(m.result_digest, 16)],
        ["stream digest", short(m.stream_digest, 16)], ["code", `${short(m.code?.git_head, 10)} on ${m.code?.git_branch} · ${m.code?.dirty_paths?.length || 0} dirty paths`],
        ["module digest", short(m.code?.modules_digest, 16)], ["modules hashed", Object.keys(m.code?.module_sha256 || {}).length], ["Python / NumPy", `${m.environment?.python} / ${m.environment?.numpy}`]]
        .map(([k, v]) => el("tr", {}, el("th", {}, k), el("td", { class: "mono" }, v)))),
        el("div", { class: "btnrow" }, el("button", { class: "btn", onclick: async () => {
          replayOut.replaceChildren(el("span", { class: "muted" }, "replaying in a fresh worker…"));
          try { const r = await api.post(`/api/x/${id}/replay`, { source: App.sourceId });
            const poll = setInterval(async () => { const log = (await api.get(`/api/x/${r.job_id}/log`)).events; const rep = log.find((e) => e.t === "replay"); const err = log.find((e) => e.t === "error");
              if (rep || err) { clearInterval(poll); replayOut.replaceChildren(err ? el("div", { class: "banner bad" }, err.message) : el("div", {}, ev(rep.state, `replay ${rep.state}`),
                el("div", { class: "small" }, `result digest ${rep.result_digest_same ? "same" : "DIFFERENT"} · stream digest ${rep.stream_digest_same ? "same" : "DIFFERENT"} · code modules ${rep.code_modules_same ? "same" : "changed"}`),
                el("a", { href: `#/experiments/${rep.replay}` }, "open replay record"))); } }, 1000);
          } catch (err) { replayOut.replaceChildren(el("div", { class: "banner bad" }, err.message)); }
        } }, "replay & verify")), replayOut);
    }
    load().catch((err) => head.append(el("div", { class: "banner bad" }, err.message)));
    return { destroy() { es?.close(); } };
  }

  return { experiments: Experiments };
}
