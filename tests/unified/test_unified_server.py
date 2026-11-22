"""End-to-end through the real HTTP server: paired run, replay verification, cancellation, hostile input.

Workers are real child processes. Sampling uses only the test namespace ``rnglab-gui-test``.
"""
from __future__ import annotations

import http.client
import json
import sys
import threading
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
for p in (ROOT / "gui", ROOT / "src"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from rnglab_gui.server import serve  # noqa: E402

NS = "rnglab-gui-test"


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    srv = serve("127.0.0.1", 0, tmp_path_factory.mktemp("labdata"))
    thread = threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.1}, daemon=True)
    thread.start()
    yield srv
    for handle in srv.lab.runs.active():
        handle.control({"cmd": "stop"})
    srv.shutdown()
    srv.server_close()


def call(server, method, path, body=None, headers=None, raw=None):
    conn = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=60)
    h = {"Host": "127.0.0.1", **(headers or {})}
    if method == "POST":
        h.setdefault("X-RNGLab-Lab", "1")
    data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
    conn.request(method, path, body=data, headers=h)
    resp = conn.getresponse()
    payload = resp.read()
    conn.close()
    try:
        return resp.status, json.loads(payload or b"null")
    except ValueError:
        return resp.status, payload


def wait_state(server, run_id, states, timeout=120):
    end = time.time() + timeout
    while time.time() < end:
        _, d = call(server, "GET", f"/api/runs/{run_id}")
        if d["summary"]["state"] in states:
            return d
        time.sleep(0.2)
    raise AssertionError(f"{run_id} did not reach {states}")


def wait_verification(server, run_id, timeout=120):
    end = time.time() + timeout
    while time.time() < end:
        _, d = call(server, "GET", f"/api/runs/{run_id}")
        v = d.get("verification") or {}
        if v.get("state") not in (None, "running"):
            return v
        time.sleep(0.2)
    raise AssertionError("verification did not finish")


# ------------------------------------------------------------------------- unified endpoints
def test_unified_read_endpoints(server):
    for path in ("/api/u/registry", "/api/u/studies", "/api/u/env", "/api/u/branches", "/api/u/atlas",
                 "/api/u/studies/v020-nuclear-b2-verify", "/api/u/file?path=research/v020-nuclear-b2-verify/protocol.sha256"):
        status, _ = call(server, "GET", path)
        assert status == 200, path
    _, reg = call(server, "GET", "/api/u/registry")
    assert reg["matrix"]["problems"] == [] and reg["summary"]["total"] >= 40
    assert {r["model_id"] for r in reg["runnable"]} >= {"nuclear-events", "nuclear-aggregate", "pimc-harmonic"}
    _, detail = call(server, "GET", "/api/u/studies/v020-nuclear-b2-verify")
    assert detail["freeze"]["state"] == "ok" and detail["origin"] == "historical"


def test_detector_without_consent_reads_nothing_and_demo_is_labelled(server):
    _, none = call(server, "GET", "/api/u/detector")
    assert none["ok"] and none["data"]["data_class"] == "unavailable"
    _, demo = call(server, "GET", "/api/u/detector?demo=1")
    assert demo["data"]["label"] == "SYNTHETIC DEMO" and demo["data"]["data_class"] == "illustrative"
    assert demo["data"]["validated_entropy_credit_bits"] == 0


@pytest.mark.parametrize("path", ["/api/u/file?path=../../../etc/passwd", "/api/u/file?path=src/rnglab/__init__.py",
                                  "/api/u/studies/..%2F..%2Fetc"])
def test_hostile_paths_are_refused(server, path):
    status, _ = call(server, "GET", path)
    assert status in (400, 404)


def test_h1_run_refuses_a_path_as_cell_name_before_touching_files(server):
    _, out = call(server, "GET", "/api/studies/h1/run?cell=/etc/passwd&replica=0")
    assert out["ok"] is False and "bad cell name" in out["error"]


def test_foreign_host_and_missing_header_are_refused(server):
    assert call(server, "GET", "/api/u/registry", headers={"Host": "evil.example"})[0] == 403
    assert call(server, "POST", "/api/runs", {"model": "pi-mc"}, headers={"X-RNGLab-Lab": "0"})[0] == 403


def test_negative_content_length_is_refused(server):
    status, _ = call(server, "POST", "/api/runs", raw=b"", headers={"Content-Length": "-1"})
    assert status == 400


def _protocol_namespaces() -> list[str]:
    """Namespaces declared by frozen protocols, read at run time (no frozen namespace is written in a test)."""
    out = []
    for path in sorted((ROOT / "research").glob("*/protocol.json")):
        ns = json.loads(path.read_text()).get("namespace")
        if isinstance(ns, str):
            out.append(ns)
    return out


def test_reserved_and_study_namespaces_are_refused(server):
    declared = _protocol_namespaces()
    assert len(declared) >= 3
    for ns in (*declared, "rnglab-gui-explore/../x"):
        status, _ = call(server, "POST", "/api/runs", {"model": "pi-mc", "params": {"namespace": ns}})
        assert status == 400, ns


# ------------------------------------------------------------------------- paired experiment + replay
def test_paired_healthy_vs_faulted_run_replays_bit_for_bit(server):
    status, out = call(server, "POST", "/api/runs", {"model": "pi-mc", "pair": True, "params": {
        "namespace": NS, "label": "unified-pair", "samples": 20000, "fault": "degraded", "bias_delta": 0.05}})
    assert status == 201, out
    faulted, healthy = out["run_ids"]
    f = wait_state(server, faulted, {"completed", "failed"})
    h = wait_state(server, healthy, {"completed", "failed"})
    assert f["summary"]["state"] == h["summary"]["state"] == "completed"
    assert f["summary"]["pair_role"] == "faulted" and h["summary"]["pair_role"] == "healthy baseline"
    assert f["summary"]["digest"] != h["summary"]["digest"]          # the fault changed the computation
    # replay: same configuration, fresh unobserved process, same official digest
    assert call(server, "POST", f"/api/runs/{healthy}/verify")[0] == 202
    v = wait_verification(server, healthy)
    assert v["same"] is True and v["direct_digest"] == h["summary"]["digest"]
    # ok requires a known, clean, unchanged code identity (science and laboratory); otherwise warning
    clean = (v["code_head_at_run"] and h["summary"]["code_dirty"] == 0
             and server.lab.runs.gui_state.get("dirty_files") == 0)
    assert v["state"] == ("ok" if clean and v["code_match"] else "warning")
    if not clean:
        assert v["code_match"] is False
    # index sees both runs as generated, not historical
    call(server, "POST", "/api/u/index/rebuild", {})
    _, hits = call(server, "GET", "/api/u/search?q=unified-pair")
    assert {r["run_id"] for r in hits["runs"]} == {faulted, healthy}
    assert {r["origin"] for r in hits["runs"]} == {"generated"}


def test_a_failed_reverification_never_reports_ok(server):
    """Regression: an earlier attempt's result_direct.json must not be read as the new attempt's result."""
    _, out = call(server, "POST", "/api/runs", {"model": "pi-mc", "params": {"namespace": NS, "label": "stale", "samples": 5000}})
    run_id = out["run_ids"][0]
    wait_state(server, run_id, {"completed"})
    call(server, "POST", f"/api/runs/{run_id}/verify")
    assert wait_verification(server, run_id)["same"] is True
    run_dir = server.lab.archive.path(run_id)
    assert (run_dir / "result_direct.json").exists()
    config = json.loads((run_dir / "config.json").read_text())
    config["model"] = "no-such-model"                       # the next unobserved run must fail
    (run_dir / "config.json").write_text(json.dumps(config))
    call(server, "POST", f"/api/runs/{run_id}/verify")
    v = wait_verification(server, run_id)
    assert v["state"] == "incomplete" and v["same"] is False


def test_cancel_stops_a_running_worker(server):
    _, out = call(server, "POST", "/api/runs", {"model": "pi-mc", "params": {
        "namespace": NS, "label": "cancel", "samples": 20_000_000, "delay_ms": 50.0}})
    run_id = out["run_ids"][0]
    time.sleep(1.0)                                          # let the worker start sampling
    assert call(server, "POST", f"/api/runs/{run_id}/control", {"cmd": "stop"})[0] == 200
    d = wait_state(server, run_id, {"stopped", "failed", "completed"}, timeout=60)
    assert d["summary"]["state"] == "stopped"
    handle = server.lab.runs.get(run_id)
    end = time.time() + 10                                   # the reap can lag the status under load
    while handle.process.poll() is None and time.time() < end:
        time.sleep(0.05)
    assert handle.process.poll() is not None                 # the child was reaped


# ------------------------------------------------------------------------- compound experiments via the GUI server
XSPEC = {"title": "server test", "namespace": "rnglab-unified-test", "source": "pcg64", "fault": "SHIFT",
         "fault_params": {"severity": 0.05, "onset_bytes": 512}, "replicates": 2,
         "models": [{"model": "vmc-harmonic", "layout": "separate", "faulted_roles": ["acceptance"],
                     "params": {"steps": 800, "burn_in": 100}},
                    {"model": "nuclear-a", "layout": "single", "faulted_roles": ["decay"], "params": {"population": 400},
                     "fault_overrides": {"onset_bytes": 0}}]}


def wait_experiment(server, exp_id, timeout=120):
    end = time.time() + timeout
    while time.time() < end:
        status, d = call(server, "GET", f"/api/x/{exp_id}")
        if status == 200 and not d.get("running") and (d.get("status") or {}).get("state") in ("completed", "cancelled", "failed"):
            return d
        time.sleep(0.3)
    raise AssertionError("experiment did not finish")


def test_experiment_catalog_and_contract_refusal(server):
    status, cat = call(server, "GET", "/api/x/catalog")
    assert status == 200 and {m["id"] for m in cat["models"]} >= {"pimc-harmonic", "vmc-harmonic", "ising-2d", "nuclear-a", "nuclear-b2"}
    bad = {**XSPEC, "models": [{"model": "ising-2d", "layout": "separate", "faulted_roles": ["proposal"]}]}
    _, report = call(server, "POST", "/api/x/validate", {"spec": bad})
    assert report["ok"] is False and "aligned" in report["errors"][0]
    status, _ = call(server, "POST", "/api/x/run", {"spec": bad})
    assert status == 400


def test_experiment_runs_in_a_worker_and_replays_bit_for_bit(server):
    status, out = call(server, "POST", "/api/x/run", {"spec": XSPEC})
    assert status == 201, out
    d = wait_experiment(server, out["experiment_id"])
    assert d["status"]["state"] == "completed" and d["seal"]["state"] == "ok" and d["origin"] == "generated"
    assert {r["model"] for r in d["analysis"]["cross_domain"]} == {"vmc-harmonic", "nuclear-a"}
    assert len(d["units"]) == 8
    _, log = call(server, "GET", f"/api/x/{out['experiment_id']}/log")
    assert sum(1 for e in log["events"] if e["t"] == "unit") == 8
    status, rep = call(server, "POST", f"/api/x/{out['experiment_id']}/replay", {})
    assert status == 201
    end = time.time() + 120
    result = None
    while time.time() < end and result is None:
        _, log = call(server, "GET", f"/api/x/{rep['job_id']}/log")
        result = next((e for e in log["events"] if e["t"] in ("replay", "error")), None)
        time.sleep(0.3)
    assert result and result["t"] == "replay" and result["result_digest_same"] and result["stream_digest_same"]


def test_experiment_cancel_from_the_server(server):
    big = {**XSPEC, "replicates": 64}
    _, out = call(server, "POST", "/api/x/run", {"spec": big})
    time.sleep(1.5)
    status, _ = call(server, "POST", f"/api/x/{out['experiment_id']}/cancel", {})
    assert status == 200
    d = wait_experiment(server, out["experiment_id"])
    assert d["status"]["state"] == "cancelled" and d["seal"]["state"] == "ok" and d["analysis"] is None


def test_malformed_experiment_ids_are_refused(server):
    for bad in ("..%2F..%2Fetc", "20261122-000000-zzzzzz", "x"):
        assert call(server, "GET", f"/api/x/{bad}")[0] == 404


def test_server_process_never_imports_scientific_code_for_experiments():
    code = ("import sys; sys.path.insert(0, %r); sys.path.insert(0, %r); import rnglab_gui.server, rnglab_gui.experiments; "
            "bad = [m for m in sys.modules if m == 'numpy' or m.startswith(('rnglab_core.engine', 'rnglab_core.models', "
            "'rnglab14', 'rnglab.', 'h1lib'))]; print(bad)") % (str(ROOT / "gui"), str(ROOT / "src"))
    import subprocess
    out = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True, text=True, check=True)
    assert out.stdout.strip() == "[]"
