"""The Experiments screen must read fields the engine actually writes (regression for a schema drift).

A small experiment is run through the engine; every field the screen depends on must exist in its
analysis, the screen's source must reference them, retired field names must be gone, and the
module must parse (when Node.js is installed).
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from rnglab_core import engine  # noqa: E402
from rnglab_core.spec import ExperimentSpec, ModelRun  # noqa: E402

JS = ROOT / "gui/rnglab_gui/static/js/experiments.js"
ROW_FIELDS = ("model", "primary_endpoint", "unit", "clock", "role_semantics", "onset_fraction_of_run",
              "paired_mean_difference", "paired_test", "paired_p", "min_attainable_p", "holm_adjusted_p",
              "effect_detected_at_alpha", "harm_healthy", "harm_faulted", "excess_harm", "harm_rule",
              "battery_alarm_healthy", "battery_alarm_faulted", "battery_window_fraction_of_run",
              "detection_vs_harm", "warnings")
INTERVENTION_FIELDS = ("detected_post_onset", "detection_latency_bytes", "pre_onset_alarms", "healthy_arm",
                       "effect_faulted_vs_healthy", "effect_intervened_vs_healthy", "recovery_fraction",
                       "recovery_ci95_bootstrap", "recovery_note", "counterfactual_exposure", "forecast")
RETIRED = ("paired_shift_in_z", "interval_failure_healthy", "interval_failure_faulted", "recovery_fraction_of_z_shift",
           "max_abs_error_bytes", "detection_vs_harm_faulted")


@pytest.fixture(scope="module")
def analysis(tmp_path_factory):
    spec = ExperimentSpec(title="schema", namespace="rnglab-unified-test", source="pcg64", fault="SHIFT",
                          fault_params={"severity": 0.2, "onset_bytes": 512}, replicates=2,
                          models=[ModelRun("vmc-harmonic", "separate", ["acceptance"], {"steps": 800, "burn_in": 100})],
                          intervention={"kind": "switch-to-independent-source", "authorized": True,
                                        "window_bytes": 2048, "alpha": 0.01})
    out = engine.run(spec, tmp_path_factory.mktemp("x"))
    return json.loads((out / "analysis.json").read_text())


def test_engine_writes_every_field_the_screen_reads(analysis):
    row = analysis["cross_domain"][0]
    assert set(ROW_FIELDS) <= set(row), set(ROW_FIELDS) - set(row)
    assert set(row["detection_vs_harm"]) == {"healthy", "faulted"}
    iv = analysis["per_model"]["vmc-harmonic"]["intervention"]
    assert set(INTERVENTION_FIELDS) <= set(iv), set(INTERVENTION_FIELDS) - set(iv)
    assert "evidence_status" in analysis


def test_screen_references_current_fields_and_no_retired_ones():
    source = JS.read_text()
    for name in ROW_FIELDS + INTERVENTION_FIELDS + ("evidence_status",):
        assert re.search(rf"\.{name}\b", source), name
    for name in RETIRED:
        assert name not in source, name


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js not installed")
def test_screen_module_parses(tmp_path):
    copy = tmp_path / "experiments.mjs"
    copy.write_text(JS.read_text())
    done = subprocess.run(["node", "--check", str(copy)], capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
