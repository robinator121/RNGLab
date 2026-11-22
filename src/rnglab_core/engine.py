"""Experiment engine: one spec -> matched healthy/faulted units across models -> one record.

For every model, condition (healthy, faulted) and replicate the engine builds the role streams
(``streams.build_role_streams``), runs the model adapter, records per-role consumption (bytes and
SHA-256 of what the model read, and of the healthy base underneath), runs the conventional battery
on the bytes the faulted role(s) emitted, and stores the model-specific endpoints.

The analysis is computed per model from that model's own endpoints; the cross-domain table puts
the models side by side without converting their clocks or units. The questions it answers are the
same for every model: did the fault change the application's answer against its exact reference
(paired effect on the standardised deviation), did the application's own uncertainty interval fail
more often, and did a conventional battery on the consumed bytes alarm.

Replay: running the same resolved spec again must give the same ``result_digest`` (computed over
spec, units and analysis, without wall-clock fields). ``replay`` does this into a fresh directory
and compares digests and per-unit stream digests.
"""
from __future__ import annotations

import math
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from rnglab14.sources.base import RandomSource, SourceManifest

from . import faults as fault_registry
from . import models as model_registry
from .measure import StreamRecorder, conventional_battery, holm, paired_effect, paired_test, rate
from .record import code_identity, digest, environment, seal, strip_volatile, write_json_atomic
from .spec import ExperimentSpec, validate
from .streams import build_role_streams

CONDITIONS = ("healthy", "faulted")


class Cancelled(Exception):
    pass


class RecordingSource(RandomSource):
    """Pass-through that feeds every emitted byte to a ``StreamRecorder`` (identical bytes, same order)."""

    def __init__(self, inner: RandomSource, recorder: StreamRecorder):
        super().__init__(SourceManifest(source_id=inner.manifest.source_id, family=inner.manifest.family,
                                        is_deterministic=inner.manifest.is_deterministic,
                                        is_physical=inner.manifest.is_physical, metadata={"recorded": True}))
        self.inner, self.recorder = inner, recorder

    def _read_bytes(self, n: int) -> bytes:
        raw = self.inner.read_bytes(n)
        self.recorder.feed(raw)
        return raw


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _segment_base(spec: ExperimentSpec, model_run: dict, condition: str, replicate: int) -> int:
    """Index of the first recorded-file segment of this unit (disjoint across models, roles, replicates)."""
    offset = 0
    for m in spec.models:
        roles = len(model_registry.MODELS[m.model].inputs[m.layout].roles)
        if m.model == model_run["model"]:
            n_conditions = 1 if spec.pairing == "common" else (3 if spec.intervention else 2)
            cond_index = 0 if spec.pairing == "common" else ["healthy", "faulted", "intervened"].index(condition)
            return offset + (cond_index * spec.replicates + replicate) * roles
        n_conditions = 1 if spec.pairing == "common" else (3 if spec.intervention else 2)
        offset += n_conditions * spec.replicates * roles
    raise KeyError(model_run["model"])


def run_unit(spec: ExperimentSpec, model_run: dict, condition: str, replicate: int) -> dict:
    adapter = model_registry.MODELS[model_run["model"]]
    rin = adapter.inputs[model_run["layout"]]
    fault = spec.fault if condition in ("faulted", "intervened") else None
    fault_params = {**spec.fault_params, **model_run.get("fault_overrides", {})}
    streams = build_role_streams(source_id=spec.source, namespace=spec.namespace, model=adapter.id,
                                 roles=rin.roles, replicate=replicate, condition=condition, pairing=spec.pairing,
                                 fault=fault, fault_params=fault_params,
                                 faulted_roles=tuple(model_run["faulted_roles"]), split_words=adapter.split_words,
                                 intervention=spec.intervention, source_params=spec.source_params,
                                 segment_base=_segment_base(spec, model_run, condition, replicate),
                                 stream_key=spec.stream_key)
    onset = int(fault_params.get("onset_bytes", 0)) if spec.fault else 0
    recorders = {}
    sources = {}
    for role, st in streams.items():
        if role in model_run["faulted_roles"]:
            rec = StreamRecorder(start=onset, cap=spec.battery_window_bytes)
            recorders[role] = rec
            sources[role] = RecordingSource(st.source, rec)
        else:
            sources[role] = st.source
    t0 = time.perf_counter()
    error = None
    try:
        out = adapter.run(sources, model_run["params"])
    except Exception as exc:  # a failed unit is recorded, never dropped
        out, error = None, f"{type(exc).__name__}: {exc}"
    wall = time.perf_counter() - t0
    battery = {role: conventional_battery(bytes(rec.data), spec.battery_alpha) for role, rec in recorders.items()}
    accounting = [st.accounting() for st in streams.values()]
    for acc in accounting:
        if acc["role"] in model_run["faulted_roles"]:
            acc["onset_bytes"] = onset
            acc["post_onset_bytes"] = max(0, acc["consumed_bytes"] - onset)
            acc["fault_window_reached"] = acc["consumed_bytes"] > onset
    unit = {"model": adapter.id, "condition": condition, "replicate": replicate,
            "streams": accounting,
            "battery": battery, "error": error, "wall_seconds": wall}
    if out is not None:
        unit.update(endpoints=[e.to_dict() for e in out.endpoints], observables=out.observables,
                    clocks=out.clocks, model_digest=out.model_digest, notes=out.notes)
    return unit


EVIDENCE_STATUS = ("exploratory: interactive experiment, not pre-registered and not frozen; no result in this "
                   "record is evidence for or against a hypothesis until a frozen protocol reproduces it")


def _endpoint(unit: dict, observable: str) -> dict | None:
    return next((x for x in unit.get("endpoints", []) if x["observable"] == observable), None)


def _harm(e: dict | None, alpha: float) -> bool | None:
    """Per-model harm rule, recorded with the analysis.

    Models with their own uncertainty interval: harm = interval failure, where an interval that
    could not be estimated counts as a failure (frozen Stage C rule). Models without one (nuclear):
    harm = two-sided p < alpha against the exact law.
    """
    if e is None:
        return None
    extra = e.get("extra", {})
    if "interval_failure" in extra:
        return bool(extra["interval_failure"])
    p = e.get("p_two_sided")
    return None if p is None else p < alpha


def _two_by_two(units: list[dict], observable: str, roles: list[str], alpha: float) -> dict:
    out = {"alarm_and_harm": 0, "alarm_no_harm": 0, "harm_no_alarm": 0, "neither": 0, "undefined": 0}
    for u in units:
        harm = _harm(_endpoint(u, observable), alpha)
        al = [u.get("battery", {}).get(r, {}) for r in roles]
        alarm = None if any(not a.get("available") for a in al) else any(a.get("alarm") for a in al)
        if harm is None or alarm is None:
            out["undefined"] += 1
        else:
            out[{(True, True): "alarm_and_harm", (True, False): "alarm_no_harm",
                 (False, True): "harm_no_alarm", (False, False): "neither"}[(alarm, harm)]] += 1
    return out


def _median(xs: list[float]) -> float | None:
    xs = sorted(x for x in xs if x is not None)
    return xs[len(xs) // 2] if xs else None


def analyse(spec: ExperimentSpec, units: list[dict]) -> dict:
    per_model = {}
    family_p, family_keys = [], []
    alpha = spec.endpoint_alpha
    for m in spec.models:
        adapter = model_registry.MODELS[m.model]
        mu = [u for u in units if u["model"] == m.model]
        by = {(u["condition"], u["replicate"]): u for u in mu}
        observables = []
        for u in mu:
            for e in u.get("endpoints", []):
                if e["observable"] not in observables:
                    observables.append(e["observable"])
        reps = range(spec.replicates)
        # fault activity and where the onset falls in this model's run
        faulted_streams = [s for u in mu if u["condition"] == "faulted" for s in u["streams"] if s["role"] in m.faulted_roles]
        healthy_streams = [s for u in mu if u["condition"] == "healthy" for s in u["streams"] if s["role"] in m.faulted_roles]
        reached = [s.get("fault_window_reached") for s in faulted_streams]
        active = bool(reached) and any(reached)
        onset = int({**spec.fault_params, **m.fault_overrides}.get("onset_bytes", 0)) if spec.fault else None
        h_consumed = _median([s["consumed_bytes"] for s in healthy_streams])
        clock_totals = next((u.get("clocks") for u in mu if u.get("clocks")), {}) or {}
        onset_fraction = (onset / h_consumed) if (onset is not None and h_consumed) else None
        ends = {}
        for obs in observables:
            est = {c: [(_endpoint(by.get((c, r), {}), obs) or {}).get("estimate") for r in reps] for c in CONDITIONS}
            z = {c: [(_endpoint(by.get((c, r), {}), obs) or {}).get("z") for r in reps] for c in CONDITIONS}
            harm = {c: [_harm(_endpoint(by.get((c, r), {}), obs), alpha) for r in reps] for c in CONDITIONS}
            first = next((x for u in mu for x in u.get("endpoints", []) if x["observable"] == obs), {})
            test = paired_test(est["healthy"], est["faulted"]) if active else {
                "n": 0, "method": "not tested: the fault never acted in this model", "p_two_sided": None,
                "mean_difference": None}
            undefined_z = {c: sum(1 for v in z[c] if v is None) for c in CONDITIONS}
            hr = {c: rate(harm[c]) for c in CONDITIONS}
            entry = {"meaning": first.get("meaning"), "unit": first.get("unit"),
                     "reference_kind": first.get("reference_kind"), "reference_source": first.get("reference_source"),
                     "paired_test_on_estimate": test,
                     "descriptive_z_shift": paired_effect(z["healthy"], z["faulted"]) if active else None,
                     "units_with_undefined_se": undefined_z, "harm_rate": hr,
                     "excess_harm": (hr["faulted"]["rate"] - hr["healthy"]["rate"])
                     if hr["faulted"]["rate"] is not None and hr["healthy"]["rate"] is not None else None,
                     "z_values": z, "estimates": est}
            ends[obs] = entry
            if obs == observables[0]:
                family_p.append(test.get("p_two_sided"))
                family_keys.append(m.model)
        prim = observables[0] if observables else None
        warnings = []
        if reached and not all(reached):
            warnings.append(f"the faulted role(s) never consumed past the onset in {reached.count(False)} of "
                            f"{len(reached)} faulted stream(s); there the fault did not act")
        if not active and spec.fault:
            warnings.append("the fault never acted in this model: no effect can be measured (shown as not tested)")
        degenerate = sum(1 for u in mu for e in u.get("endpoints", []) if e.get("extra", {}).get("degenerate"))
        if degenerate:
            warnings.append(f"{degenerate} endpoint value(s) have a degenerate exact law; they cannot show an effect")
        if prim:
            hh = ends[prim]["harm_rate"]["healthy"]
            if hh["n"] and hh["interval"] and hh["interval"][0] > alpha:
                warnings.append(f"on healthy streams the primary endpoint already shows harm in {hh['events']}/{hh['n']} "
                                f"units (exact 95 % interval {hh['interval'][0]:.3f}-{hh['interval'][1]:.3f} > {alpha}): "
                                "the model's own uncertainty estimate is miscalibrated at these settings, so harm "
                                "rates must be read as excess over the healthy arm")
        alarms = {c: [u["battery"].get(r, {}).get("alarm") if u["battery"].get(r, {}).get("available") else None
                      for u in mu if u["condition"] == c for r in m.faulted_roles] for c in CONDITIONS}
        per_model[m.model] = {
            "clock": adapter.clock.value, "faulted_roles": m.faulted_roles, "layout": m.layout,
            "role_semantics": {r: adapter.role_semantics.get(r, "not declared") for r in m.faulted_roles},
            "endpoints": ends, "primary_endpoint": prim, "warnings": warnings,
            "fault_active_streams": {"reached": reached.count(True), "total": len(reached)},
            "onset": {"bytes": onset, "median_role_consumption_healthy": h_consumed, "fraction_of_run": onset_fraction,
                      "approx_in_model_clock": {k: round(onset_fraction * v, 1) for k, v in clock_totals.items()
                                                if isinstance(v, (int, float))} if onset_fraction is not None else None,
                      "note": "approximate: consumption per clock unit is state-dependent for MCMC acceptance roles"},
            "battery": {"alarm_rate": {c: rate(alarms[c]) for c in CONDITIONS},
                        "window_bytes": spec.battery_window_bytes,
                        "window_fraction_of_run": (spec.battery_window_bytes / h_consumed) if h_consumed else None},
            "harm_rule": ("interval failure (an interval that could not be estimated counts as failure)"
                          if prim and any("interval_failure" in (e.get("extra") or {}) for u in mu
                                          for e in u.get("endpoints", [])) else
                          f"two-sided p < {alpha} against the exact law (the model has no own interval)"),
            "detection_vs_harm": {c: _two_by_two([u for u in mu if u["condition"] == c], prim, m.faulted_roles, alpha)
                                  for c in CONDITIONS} if prim else None,
            "errors": [u["error"] for u in mu if u.get("error")],
        }
        if spec.intervention:
            per_model[m.model]["intervention"] = _intervention_analysis(spec, m, by, prim)
    adj = holm(family_p, alpha)
    for k, model in enumerate(family_keys):
        per_model[model]["primary_holm"] = {"adjusted_p": adj["adjusted"][k], "reject": adj["reject"][k],
                                            "family_size": adj["family_size"]}
    comparison = []
    for model, a in per_model.items():
        e = a["endpoints"].get(a["primary_endpoint"] or "", {})
        t = e.get("paired_test_on_estimate") or {}
        hr = e.get("harm_rate") or {}
        comparison.append({
            "model": model, "primary_endpoint": a["primary_endpoint"], "unit": e.get("unit"), "clock": a["clock"],
            "faulted_roles": a["faulted_roles"], "role_semantics": a["role_semantics"], "warnings": a["warnings"],
            "fault_active_streams": a["fault_active_streams"], "onset_fraction_of_run": a["onset"]["fraction_of_run"],
            "paired_mean_difference": t.get("mean_difference"), "paired_test": t.get("method"),
            "paired_p": t.get("p_two_sided"), "min_attainable_p": t.get("min_attainable_p"),
            "holm_adjusted_p": a.get("primary_holm", {}).get("adjusted_p"),
            "effect_detected_at_alpha": a.get("primary_holm", {}).get("reject"),
            "descriptive_z_shift": (e.get("descriptive_z_shift") or {}).get("mean_difference"),
            "units_with_undefined_se": e.get("units_with_undefined_se"),
            "harm_healthy": (hr.get("healthy") or {}).get("rate"), "harm_faulted": (hr.get("faulted") or {}).get("rate"),
            "excess_harm": e.get("excess_harm"), "harm_rule": a["harm_rule"],
            "battery_alarm_healthy": a["battery"]["alarm_rate"]["healthy"].get("rate"),
            "battery_alarm_faulted": a["battery"]["alarm_rate"]["faulted"].get("rate"),
            "battery_window_fraction_of_run": a["battery"]["window_fraction_of_run"],
            "detection_vs_harm": a["detection_vs_harm"],
        })
    return {"evidence_status": EVIDENCE_STATUS, "per_model": per_model, "cross_domain": comparison,
            "multiplicity": {"family": "primary endpoint, paired test on raw estimates, one test per model "
                                       "(models where the fault never acted are not tested)", **adj},
            "interpretation_limits": [
                EVIDENCE_STATUS,
                "The same fault family and severity drive a different decision in each model (see role semantics); "
                "the comparison is of how each application responds, not of equal doses.",
                "Each model is judged against its own exact reference in its own units and clock; the onset is a "
                "byte count whose position in each run differs (onset fraction).",
                "A battery alarm means the consumed bytes look non-uniform to ten tests (Holm at the stated alpha); "
                "it is not proof of a compromised source, and it is not the frozen study's calibrated threshold.",
                "Harm can come from the source, the sampler or the uncertainty estimator; read faulted harm rates "
                "as excess over the healthy arm.",
                "Paired tests use exact sign-flip enumeration for up to 16 pairs (smallest attainable p stated) and "
                "a paired t test above that."]}


def _monitor(unit: dict, roles: list[str]) -> dict | None:
    for s in unit.get("streams", []):
        if s["role"] in roles and s.get("monitor"):
            return {**s["monitor"], "onset_bytes": s.get("onset_bytes", 0), "consumed_bytes": s["consumed_bytes"]}
    return None


def _intervention_analysis(spec: ExperimentSpec, m, by: dict, prim: str | None) -> dict:
    """Detection, intervention and measured recovery (Example 3), with the counterfactual exposure.

    Not a forecast: the exposure without intervention is computed after the fact from the paired
    healthy unit's total consumption, which is not known at the time of the alarm. A forecaster that
    uses only information available at the alarm is not connected yet.
    """
    import numpy as np
    reps = range(spec.replicates)
    rows = []
    for r in reps:
        h, f, i = by.get(("healthy", r), {}), by.get(("faulted", r), {}), by.get(("intervened", r), {})
        mh, mf, mi = _monitor(h, m.faulted_roles), _monitor(f, m.faulted_roles), _monitor(i, m.faulted_roles)
        if not (mh and mf and mi):
            rows.append({"replicate": r, "available": False})
            continue
        onset = mf["onset_bytes"]
        alarm = mi["alarm_at_byte"]
        rows.append({"replicate": r, "available": True,
                     "healthy_windows": len(mh["windows"]), "healthy_alarmed_windows": sum(1 for w in mh["windows"] if w["alarm"]),
                     "alarm_byte": alarm, "pre_onset_alarm": alarm is not None and alarm < onset,
                     "detection_latency_bytes": None if alarm is None or alarm < onset else alarm - onset,
                     "switched_at_byte": mi["switched_at_byte"],
                     "counterfactual_exposed_bytes": None if alarm is None else max(0, mh["consumed_bytes"] - alarm),
                     "realised_exposed_bytes": None if mf["alarm_at_byte"] is None else max(0, mf["consumed_bytes"] - mf["alarm_at_byte"]),
                     "replacement_consumed_bytes": mi["replacement_consumed_bytes"]})
    avail = [x for x in rows if x["available"]]
    clean = {x["replicate"] for x in avail if x["alarm_byte"] is not None and not x["pre_onset_alarm"]}

    def est(cond, keep=None):
        return [None if keep is not None and r not in keep else
                (_endpoint(by.get((cond, r), {}), prim) or {}).get("estimate") for r in reps]

    eff_f = paired_test(est("healthy", clean), est("faulted", clean))
    eff_i = paired_test(est("healthy", clean), est("intervened", clean))
    recovery, ci, note = None, None, None
    significant = eff_f.get("p_two_sided") is not None and eff_f["p_two_sided"] < spec.endpoint_alpha
    if not significant:
        note = "not computed: without intervention the effect is not significant in the units with a post-onset alarm"
    elif eff_f["mean_difference"]:
        recovery = 1 - eff_i["mean_difference"] / eff_f["mean_difference"]
        h, f, i = (np.array([v for v in est(c, clean) if v is not None], dtype=float) for c in ("healthy", "faulted", "intervened"))
        if len(h) == len(f) == len(i) and len(h) >= 2:
            rng = np.random.default_rng(0)        # fixed: the interval is part of the reproducible record
            boots = []
            for _ in range(2000):
                k = rng.integers(0, len(h), len(h))
                df, di = (f[k] - h[k]).mean(), (i[k] - h[k]).mean()
                if df != 0:
                    boots.append(1 - di / df)
            if boots:
                ci = [float(np.percentile(boots, 2.5)), float(np.percentile(boots, 97.5))]
        note = ("1 - (mean shift with intervention / mean shift without), signed; percentile bootstrap over replicates "
                "(2000 resamples, fixed seed). Bytes consumed before the switch stay faulted.")
    hw = sum(x["healthy_windows"] for x in avail)
    ha = sum(x["healthy_alarmed_windows"] for x in avail)
    lat = [x["detection_latency_bytes"] for x in avail if x["detection_latency_bytes"] is not None]
    return {
        "kind": spec.intervention.get("kind"), "trigger": "first alarming monitor window (Holm over the Stage C battery)",
        "window_bytes": spec.intervention.get("window_bytes"), "alpha": spec.intervention.get("alpha"),
        "detected_post_onset": rate([x["alarm_byte"] is not None and not x["pre_onset_alarm"] for x in avail]),
        "pre_onset_alarms": sum(1 for x in avail if x["pre_onset_alarm"]),
        "healthy_arm": {"runs_with_any_alarm": rate([x["healthy_alarmed_windows"] > 0 for x in avail]),
                        "per_window_false_alarm_rate": rate([True] * ha + [False] * (hw - ha)) if hw else None},
        "detection_latency_bytes": {"values": lat, "median": _median(lat)},
        "units_in_recovery_analysis": sorted(clean),
        "effect_faulted_vs_healthy": eff_f, "effect_intervened_vs_healthy": eff_i,
        "recovery_fraction": recovery, "recovery_ci95_bootstrap": ci, "recovery_note": note,
        "counterfactual_exposure": {
            "label": "post hoc, not a forecast",
            "definition": "bytes the faulted role would still read after the alarm without intervention, assuming a "
                          "persistent fault and the paired healthy unit's total consumption",
            "pairs_counterfactual_vs_realised": [(x["counterfactual_exposed_bytes"], x["realised_exposed_bytes"])
                                                 for x in avail if x["counterfactual_exposed_bytes"] is not None]},
        "forecast": {"status": "not implemented",
                     "note": "a forecaster that uses only information available at the alarm (rnglab.prognosis) is "
                             "not connected yet"},
        "rows": rows}


def run(spec: ExperimentSpec, root: Path, *, progress: Callable[[dict], None] | None = None,
        cancel: threading.Event | None = None, experiment_id: str | None = None) -> Path:
    report = validate(spec)
    conditions = CONDITIONS + (("intervened",) if spec.intervention else ())
    if not report["ok"]:
        raise ValueError("invalid experiment: " + "; ".join(report["errors"]))
    resolved = spec.resolved()
    exp_id = experiment_id or f"{datetime.now(timezone.utc):%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:6]}"
    out = Path(root) / exp_id
    (out / "units").mkdir(parents=True, exist_ok=False)
    started = _now()
    write_json_atomic(out / "spec.json", {"spec": resolved, "spec_digest": spec.spec_digest, "validation": report})
    write_json_atomic(out / "status.json", {"state": "running", "started": started, "units_total": report["units"]})
    units, k = [], 0
    try:
        for m in resolved["models"]:
            for cond in conditions:
                for rep in range(spec.replicates):
                    if cancel is not None and cancel.is_set():
                        raise Cancelled()
                    unit = run_unit(spec, m, cond, rep)
                    name = f"{m['model']}__{cond}__{rep:04d}.json"
                    write_json_atomic(out / "units" / name, unit)
                    units.append(unit)
                    k += 1
                    if progress:
                        progress({"t": "unit", "done": k, "total": report["units"], "model": m["model"],
                                  "condition": cond, "replicate": rep, "error": unit.get("error"),
                                  "endpoints": unit.get("endpoints", []), "clocks": unit.get("clocks"),
                                  "streams": unit["streams"], "battery": unit["battery"]})
    except Cancelled:
        write_json_atomic(out / "status.json", {"state": "cancelled", "started": started, "ended": _now(),
                                                "units_done": k, "units_total": report["units"]})
        seal(out)
        return out
    spec_obj = ExperimentSpec.from_dict(resolved)
    analysis = analyse(spec_obj, units)
    write_json_atomic(out / "analysis.json", analysis)
    content = {"spec": resolved, "units": strip_volatile(units), "analysis": analysis}
    result_digest = digest(content)
    stream_digest = digest([[u["model"], u["condition"], u["replicate"],
                             [(s["role"], s["consumed_sha256"], s["consumed_bytes"]) for s in u["streams"]]] for u in units])
    write_json_atomic(out / "manifest.json", {
        "experiment_id": exp_id, "schema": "rnglab-unified-experiment-record-v1", "started": started, "ended": _now(),
        "spec_digest": spec.spec_digest, "result_digest": result_digest, "stream_digest": stream_digest,
        "code": code_identity(model_registry.REPO_ROOT), "environment": environment(),
        "stream_key": spec.stream_key, "origin": "generated", "evidence_status": EVIDENCE_STATUS})
    write_json_atomic(out / "status.json", {"state": "completed", "started": started, "ended": _now(),
                                            "units_done": k, "units_total": report["units"],
                                            "result_digest": result_digest})
    record_digest = seal(out)
    if progress:
        progress({"t": "done", "result_digest": result_digest, "record_sha256": record_digest})
    return out


def replay(record_dir: Path, root: Path, *, progress: Callable[[dict], None] | None = None,
           experiment_id: str | None = None) -> dict:
    """Re-run a stored experiment from its resolved spec and compare everything that must match."""
    import json
    record_dir = Path(record_dir)
    stored = json.loads((record_dir / "spec.json").read_text())
    manifest = json.loads((record_dir / "manifest.json").read_text())
    spec = ExperimentSpec.from_dict(stored["spec"])
    new = run(spec, root, progress=progress,
              experiment_id=experiment_id or f"{manifest['experiment_id']}-replay-{uuid.uuid4().hex[:4]}")
    new_manifest = json.loads((new / "manifest.json").read_text())
    code_same = manifest["code"]["modules_digest"] == new_manifest["code"]["modules_digest"]
    return {"original": manifest["experiment_id"], "replay": new.name,
            "spec_digest_same": manifest["spec_digest"] == new_manifest["spec_digest"],
            "stream_digest_same": manifest["stream_digest"] == new_manifest["stream_digest"],
            "result_digest_same": manifest["result_digest"] == new_manifest["result_digest"],
            "code_modules_same": code_same,
            "state": ("ok" if manifest["result_digest"] == new_manifest["result_digest"] and code_same else
                      "warning" if manifest["result_digest"] == new_manifest["result_digest"] else "failed")}
