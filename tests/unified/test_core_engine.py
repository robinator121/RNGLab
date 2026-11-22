"""Experiment engine: validation, determinism and replay, accounting, intervention, refusal rules.

All runs sample in the test namespace ``rnglab-unified-test`` and are small.
"""
from __future__ import annotations

import json
import sys
import threading
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from rnglab_core import engine  # noqa: E402
from rnglab_core.record import verify_seal  # noqa: E402
from rnglab_core.spec import ExperimentSpec, ModelRun, protocol_namespaces, validate  # noqa: E402

NS = "rnglab-unified-test"
SMALL = {"pimc-harmonic": {"sweeps": 300, "burn_in": 50}, "vmc-harmonic": {"steps": 800, "burn_in": 100},
         "ising-2d": {"sweeps": 200, "burn_in": 20}, "nuclear-b2": {"population": 2000},
         "nuclear-a": {"population": 500}}


def spec(**kw):
    base = dict(title="t", namespace=NS, source="pcg64", fault="SHIFT",
                fault_params={"severity": 0.05, "onset_bytes": 512},
                models=[ModelRun("vmc-harmonic", "separate", ["acceptance"], SMALL["vmc-harmonic"]),
                        ModelRun("nuclear-a", "single", ["decay"], SMALL["nuclear-a"], {"onset_bytes": 0})],
                replicates=2)
    base.update(kw)
    return ExperimentSpec(**base)


def test_compound_experiment_record_is_complete_and_sealed(tmp_path):
    out = engine.run(spec(), tmp_path)
    manifest = json.loads((out / "manifest.json").read_text())
    analysis = json.loads((out / "analysis.json").read_text())
    assert {r["model"] for r in analysis["cross_domain"]} == {"vmc-harmonic", "nuclear-a"}
    assert len(list((out / "units").glob("*.json"))) == 2 * 2 * 2
    assert manifest["origin"] == "generated" and manifest["code"]["module_sha256"]
    assert any("h1lib.py" in k for k in manifest["code"]["module_sha256"])
    assert verify_seal(out)["state"] == "ok"
    unit = json.loads(next((out / "units").glob("vmc-harmonic__faulted__0000.json")).read_text())
    acc = next(s for s in unit["streams"] if s["role"] == "acceptance")
    assert acc["fault"] == "SHIFT" and acc["fault_window_reached"] and acc["consumed_sha256"] != acc["base_sha256"]
    healthy = json.loads(next((out / "units").glob("vmc-harmonic__healthy__0000.json")).read_text())
    h_acc = next(s for s in healthy["streams"] if s["role"] == "acceptance")
    assert h_acc["consumed_sha256"] == h_acc["base_sha256"] == acc["base_sha256"]   # common random numbers


def test_replay_reproduces_every_digest(tmp_path):
    out = engine.run(spec(), tmp_path)
    result = engine.replay(out, tmp_path)
    assert result["result_digest_same"] and result["stream_digest_same"] and result["spec_digest_same"]
    assert result["state"] == "ok"


def test_tampered_record_fails_its_seal(tmp_path):
    out = engine.run(spec(), tmp_path)
    unit = next((out / "units").glob("*.json"))
    unit.write_text(unit.read_text() + " ")   # one extra byte
    assert verify_seal(out)["state"] == "failed"


def test_independent_pairing_changes_streams_common_pairing_does_not(tmp_path):
    def base_digests(pairing):
        out = engine.run(spec(pairing=pairing, fault=None, fault_params={}), tmp_path)
        return {(u["condition"], s["role"]): s["base_sha256"] for u in
                (json.loads(p.read_text()) for p in (out / "units").glob("vmc-harmonic__*__0000.json"))
                for s in u["streams"]}
    common, independent = base_digests("common"), base_digests("independent")
    assert common[("healthy", "acceptance")] == common[("faulted", "acceptance")]
    assert independent[("healthy", "acceptance")] != independent[("faulted", "acceptance")]


def test_word_fault_on_a_non_aligned_role_is_refused():
    s = spec(models=[ModelRun("ising-2d", "separate", ["proposal"])])
    report = validate(s)
    assert not report["ok"] and "aligned" in report["errors"][0]
    ok = validate(spec(models=[ModelRun("ising-2d", "separate", ["acceptance"])]))
    assert ok["ok"], ok["errors"]


def test_model_internal_and_unknown_faults_are_refused():
    assert not validate(spec(fault="phase2-acceptance-shift", fault_params={}))["ok"]
    assert not validate(spec(fault="no-such-fault"))["ok"]


def test_only_onset_may_differ_between_models():
    s = spec(models=[ModelRun("vmc-harmonic", "separate", ["acceptance"], {}, {"severity": 0.5})])
    assert any("only onset_bytes" in e for e in validate(s)["errors"])


@pytest.mark.parametrize("ns", ["rnglab-v019-d-pilot", "rnglab-gui-explore/../x", "Rnglab-unified-x", "other-ns"])
def test_bad_or_reserved_namespaces_are_refused(ns):
    assert not validate(spec(namespace=ns))["ok"]


def test_every_frozen_protocol_namespace_is_refused():
    declared = protocol_namespaces()
    assert len(declared) >= 3
    for ns in declared:
        assert not validate(spec(namespace=ns))["ok"], ns


def test_fault_that_never_reaches_its_onset_is_reported(tmp_path):
    s = spec(models=[ModelRun("nuclear-b2", "single", ["decay"], SMALL["nuclear-b2"], {"onset_bytes": 1 << 30})])
    out = engine.run(s, tmp_path)
    row = json.loads((out / "analysis.json").read_text())["cross_domain"][0]
    assert row["fault_active_streams"]["reached"] == 0 and row["warnings"]


def test_seed_reuse_gives_two_roles_identical_streams(tmp_path):
    s = spec(fault="seed-reuse", fault_params={"partner_role": "proposal"},
             models=[ModelRun("vmc-harmonic", "separate", ["acceptance"], SMALL["vmc-harmonic"])])
    out = engine.run(s, tmp_path)
    unit = json.loads(next((out / "units").glob("vmc-harmonic__faulted__0000.json")).read_text())
    streams = {x["role"]: x for x in unit["streams"]}
    assert streams["acceptance"]["seed"] == streams["proposal"]["seed"]
    assert streams["acceptance"]["seed_from_role"] == "proposal"


def test_intervention_needs_authorisation_and_switches_source(tmp_path):
    iv = {"kind": "switch-to-independent-source", "authorized": False, "window_bytes": 2048, "alpha": 0.01}
    assert not validate(spec(intervention=iv))["ok"]
    iv["authorized"] = True
    s = spec(intervention=iv, fault_params={"severity": 0.2, "onset_bytes": 512},
             models=[ModelRun("vmc-harmonic", "separate", ["acceptance"], SMALL["vmc-harmonic"])])
    out = engine.run(s, tmp_path)
    analysis = json.loads((out / "analysis.json").read_text())["per_model"]["vmc-harmonic"]["intervention"]
    assert analysis["detected_post_onset"]["rate"] == 1.0
    assert analysis["counterfactual_exposure"]["label"] == "post hoc, not a forecast"
    assert analysis["forecast"]["status"] == "not implemented"
    unit = json.loads(next((out / "units").glob("vmc-harmonic__intervened__0000.json")).read_text())
    mon = next(x for x in unit["streams"] if x["role"] == "acceptance")["monitor"]
    assert mon["switched_at_byte"] is not None and mon["replacement_consumed_bytes"] > 0
    faulted = json.loads(next((out / "units").glob("vmc-harmonic__faulted__0000.json")).read_text())
    fmon = next(x for x in faulted["streams"] if x["role"] == "acceptance")["monitor"]
    assert fmon["switched_at_byte"] is None and fmon["alarm_at_byte"] == mon["alarm_at_byte"]


def test_cancel_leaves_a_sealed_partial_record(tmp_path):
    stop = threading.Event()
    seen = []

    def progress(event):
        seen.append(event)
        if event.get("t") == "unit" and event["done"] == 2:
            stop.set()
    out = engine.run(spec(), tmp_path, progress=progress, cancel=stop)
    status = json.loads((out / "status.json").read_text())
    assert status["state"] == "cancelled" and status["units_done"] == 2
    assert not (out / "analysis.json").exists() and verify_seal(out)["state"] == "ok"


def test_recorded_stream_as_source_uses_disjoint_segments_and_checks_its_digest(tmp_path):
    """Example 4 mechanics: a recorded byte file feeds the models; no segment is reused; no entropy claim."""
    import hashlib
    import numpy as np
    data = np.random.default_rng(1234).bytes(2 * 2 * 3 * 40000 + 2 * 2 * 1 * 40000)   # test fixture bytes
    path = tmp_path / "recorded.bin"
    path.write_bytes(data)
    sha = hashlib.sha256(data).hexdigest()
    s = spec(source="recorded-file", source_params={"path": str(path), "sha256": sha, "bytes_per_stream": 40000},
             fault=None, fault_params={},
             models=[ModelRun("vmc-harmonic", "separate", [], {"steps": 800, "burn_in": 100}),
                     ModelRun("nuclear-a", "single", [], {"population": 100})])
    report = validate(s)
    assert report["ok"], report["errors"]
    out = engine.run(s, tmp_path / "x")
    offsets = []
    for p in (out / "units").glob("*healthy*.json"):
        for st in json.loads(p.read_text())["streams"]:
            assert st["base_source"].startswith("recorded:")
            offsets.append(st["seed_label"].rsplit(" ", 1)[-1])
    assert len(offsets) == len(set(offsets))                     # every stream its own segment
    wrong = validate(spec(source="recorded-file", source_params={"path": str(path), "sha256": "0" * 64,
                                                                 "bytes_per_stream": 40000}, fault=None,
                          fault_params={}, models=[ModelRun("vmc-harmonic", "separate", [])]))
    assert any("SHA-256 differs" in e for e in wrong["errors"])
    tiny = spec(source="recorded-file", source_params={"path": str(path), "sha256": sha, "bytes_per_stream": 10 ** 7},
                fault=None, fault_params={}, models=[ModelRun("vmc-harmonic", "separate", [])])
    assert any("too short" in e for e in validate(tiny)["errors"])



# ------------------------------------------------------------------------- red-team regressions
def test_units_whose_uncertainty_broke_stay_in_the_paired_test(tmp_path):
    """F1: a strong fault can freeze Ising (zero variance, no SE); those units must count, not vanish."""
    s = spec(fault_params={"severity": 0.3, "onset_bytes": 0}, replicates=4,
             models=[ModelRun("ising-2d", "separate", ["acceptance"], {"sweeps": 400, "burn_in": 50})])
    out = engine.run(s, tmp_path)
    e = json.loads((out / "analysis.json").read_text())["per_model"]["ising-2d"]["endpoints"]["energy per site"]
    assert e["paired_test_on_estimate"]["n"] == 4
    assert e["harm_rate"]["faulted"]["events"] >= e["units_with_undefined_se"]["faulted"]


def test_paired_test_is_exact_for_few_pairs():
    """F2: no normal-approximation p-values on a handful of pairs."""
    from rnglab_core.measure import paired_test
    r = paired_test([0.0] * 3, [1.0, 1.1, 0.9])
    assert r["method"].startswith("exact sign-flip") and r["p_two_sided"] == 0.25 == r["min_attainable_p"]


@pytest.mark.parametrize("params, fragment", [
    ({"severity": 0.02, "onset_bytes": 4096, "rho": 0.5}, "does not take"),
    ({"severity": 5.0, "onset_bytes": 4096}, "outside"),
    ({"severity": 0.02, "onset_bytes": 4097}, "multiple of 8"),
])
def test_fault_parameters_are_validated_strictly(params, fragment):
    """F6/F7: unknown, out-of-range and unaligned parameters are refused."""
    assert any(fragment in e for e in validate(spec(fault_params=params))["errors"])


def test_byte_faults_refuse_an_onset_they_do_not_have():
    s = spec(fault="byte-bias", fault_params={"bias_delta": 0.02, "onset_bytes": 4096},
             models=[ModelRun("vmc-harmonic", "separate", ["acceptance"])])
    assert any("no onset" in e or "does not take" in e for e in validate(s)["errors"])


def test_different_specs_get_fresh_streams_and_replays_the_same(tmp_path):
    """F11: two experiments in one namespace never share streams unless the spec is the same."""
    a, b = spec(), spec(fault_params={"severity": 0.06, "onset_bytes": 512})
    assert a.stream_key != b.stream_key and a.stream_key == spec(title="other title").stream_key


def test_record_is_stamped_exploratory_and_code_identity_is_fixed(tmp_path):
    """F13/F14."""
    out = engine.run(spec(), tmp_path)
    manifest = json.loads((out / "manifest.json").read_text())
    assert manifest["evidence_status"].startswith("exploratory")
    assert json.loads((out / "analysis.json").read_text())["evidence_status"].startswith("exploratory")
    files = manifest["code"]["module_sha256"]
    assert "research/v020-nuclear-b2-verify/protocol.json" in files and "research/v018-h1-detection/h1lib.py" in files


def test_healthy_miscalibration_and_inactive_faults_are_reported(tmp_path):
    """F3/F5: a fault that never acts is shown as not tested, not as a zero effect."""
    s = spec(models=[ModelRun("nuclear-b2", "single", ["decay"], SMALL["nuclear-b2"], {"onset_bytes": 1 << 30})])
    row = json.loads((engine.run(s, tmp_path) / "analysis.json").read_text())["cross_domain"][0]
    assert row["paired_p"] is None and row["paired_mean_difference"] is None
    assert any("never acted" in w for w in row["warnings"])
