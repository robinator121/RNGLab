"""Observing a run must not change it.

For every model, the official result digest of an observed run (taps, probes,
telemetry, controller) must equal the digest of the same configuration run
directly in a fresh process, and every integrity check must pass. For the H1
model the direct run is h1lib.run_one itself, so the equality also shows that
the laboratory's composition reproduces the study's own function.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _lab import by_type, run_worker  # noqa: E402

CASES = {
    "pi-healthy": ("pi-mc", {"samples": 4000}),
    "pi-degraded-rho": ("pi-mc", {"samples": 600, "fault": "degraded", "rho": 0.4, "bias_delta": 0.0}),
    "pi-h1-lag1": ("pi-mc", {"samples": 3000, "fault": "h1-word", "h1_family": "LAG1", "h1_severity": 0.5,
                             "onset_bytes": 16000}),
    "pimc-shared": ("pimc-harmonic", {"sweeps": 300, "generator": "numpy-pcg64"}),
    "pimc-separate-centroid-fault": ("pimc-harmonic", {"sweeps": 256, "channels": "separate", "centroid_refresh": True,
                                                       "fault": "degraded", "fault_role": "acceptance",
                                                       "bias_delta": 0.03, "initialization": "uniform"}),
    "pimc-h1-dup": ("pimc-harmonic", {"sweeps": 256, "fault": "h1-word", "h1_family": "DUP", "h1_severity": 0.2,
                                      "onset_bytes": 8000, "batch_size": 32}),
    "ising-separate-fault": ("ising-2d", {"L": 4, "sweeps": 60, "burn_in": 10, "channels": "separate",
                                          "fault": "degraded", "fault_role": "acceptance", "bias_delta": 0.05}),
    "ising-shared-L6": ("ising-2d", {"L": 6, "sweeps": 30, "burn_in": 5, "generator": "mt19937"}),
    "decision-paired": ("decision-support", {"replicates": 4, "scenarios": 200}),
    "decision-sweep": ("decision-support", {"design": "sweep", "replicates": 3, "scenarios": 120, "sweep_levels": 3}),
    "nuclear-aggregate-S2": ("nuclear-aggregate", {"scheme": "S2", "intervals": 30}),
    "nuclear-aggregate-S4-fault": ("nuclear-aggregate", {"scheme": "S4", "intervals": 12, "population": 5000,
                                                         "fault": "degraded", "bias_delta": 0.01}),
    "nuclear-events-S2": ("nuclear-events", {"scheme": "S2", "population": 2000, "chunk": 300}),
    "h1-shift": ("h1-detection", {"sweeps": 1280, "onset_bytes": 60000, "family": "SHIFT"}),
    "h1-gfsr": ("h1-detection", {"sweeps": 1024, "onset_bytes": 40000, "family": "GFSR", "gfsr_pair": "31,13"}),
}


@pytest.mark.parametrize("case", sorted(CASES))
def test_observed_run_equals_direct_run(case, tmp_path):
    model, overrides = CASES[case]
    gates = Path(__file__).resolve().parents[2] / "research/v018-h1-detection/results/gates.json"
    if model == "h1-detection" and not gates.exists():
        pytest.skip("the H1 model needs the Stage C study, deferred from this release (docs/unified/PUBLIC_DEFERRED.json)")
    observed = run_worker(model, overrides, tmp_path / "observed", "observe")
    direct = run_worker(model, overrides, tmp_path / "direct", "direct")
    errors = by_type(observed, "error") + by_type(direct, "error")
    assert not errors, errors[0]["traceback"]
    assert by_type(observed, "done")[-1]["status"] == "completed"
    assert by_type(direct, "done")[-1]["status"] == "completed"
    assert by_type(observed, "frame"), "an observed run emits telemetry frames"
    assert not by_type(direct, "frame"), "a direct run emits no frames"
    digest_observed = by_type(observed, "result")[0]["data"]["digest"]
    digest_direct = by_type(direct, "result")[0]["data"]["digest"]
    assert digest_observed == digest_direct
    checks = by_type(observed, "integrity")[0]["checks"]
    bad = [c for c in checks if c["state"] == "failed"]
    assert not bad, bad
    names = {c["name"] for c in checks}
    assert "replayable sources" in names and "code source state" in names


def test_nuclear_event_digest_does_not_depend_on_chunk(tmp_path):
    small = run_worker("nuclear-events", {"scheme": "S1", "population": 1500, "chunk": 97}, tmp_path / "a", "direct")
    large = run_worker("nuclear-events", {"scheme": "S1", "population": 1500, "chunk": 5000}, tmp_path / "b", "direct")
    assert by_type(small, "result")[0]["data"]["digest"] == by_type(large, "result")[0]["data"]["digest"]


def test_different_label_gives_different_result(tmp_path):
    a = run_worker("pi-mc", {"samples": 500, "label": "a"}, tmp_path / "a", "direct")
    b = run_worker("pi-mc", {"samples": 500, "label": "b"}, tmp_path / "b", "direct")
    assert by_type(a, "result")[0]["data"]["digest"] != by_type(b, "result")[0]["data"]["digest"]


def test_os_entropy_is_flagged_as_not_replayable(tmp_path):
    messages = run_worker("pi-mc", {"samples": 200, "generator": "os-entropy"}, tmp_path, "observe")
    checks = {c["name"]: c for c in by_type(messages, "integrity")[0]["checks"]}
    assert checks["replayable sources"]["state"] == "warning"


def test_volatile_timestamps_are_excluded_and_listed(tmp_path):
    messages = run_worker("pimc-harmonic", {"sweeps": 64, "centroid_refresh": True, "batch_size": 8}, tmp_path, "observe")
    result = by_type(messages, "result")[0]["data"]
    assert "/result/centroid_update/source_manifest/created_at" in result["volatile_excluded"]
