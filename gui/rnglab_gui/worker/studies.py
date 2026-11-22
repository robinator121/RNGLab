"""Read-only queries on frozen study outputs, executed in a worker process.

Statistics are recomputed from stored arrays with the study's own functions
(imported from the study's library in the chosen code source), never with
code written for the laboratory. Nothing here writes a file or draws a random
number; every response names the files it read and their SHA-256.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from pathlib import Path

import numpy as np

H1 = "research/v018-h1-detection"


def _sha(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _load_module(path: Path, name: str):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def inventory(root: Path) -> dict:
    """Every research/<study>/ with its protocol identity, freeze check and result files."""
    studies = []
    for study in sorted((root / "research").iterdir()):
        if not study.is_dir():
            continue
        entry = {"id": study.name, "path": f"research/{study.name}"}
        protocol = study / "protocol.json"
        if protocol.exists():
            try:
                data = json.loads(protocol.read_text())
            except ValueError:
                data = {}
            entry["protocol_id"] = data.get("id")
            entry["status"] = data.get("status")
            entry["namespace"] = data.get("namespace")
            actual = _sha(protocol)
            entry["protocol_sha256"] = actual
            recorded_file = study / "protocol.sha256"
            if recorded_file.exists():
                recorded = recorded_file.read_text().split()[0] if recorded_file.read_text().strip() else ""
                entry["freeze"] = {"state": "ok" if recorded == actual else "failed", "recorded": recorded,
                                   "detail": "protocol.json matches protocol.sha256" if recorded == actual
                                   else "protocol.json differs from its recorded SHA-256"}
            else:
                entry["freeze"] = {"state": "incomplete", "detail": "no protocol.sha256: not frozen"}
        else:
            entry["freeze"] = {"state": "unavailable", "detail": "no protocol.json"}
        readme = study / "README.md"
        if readme.exists():
            lines = [ln.strip() for ln in readme.read_text(errors="replace").splitlines() if ln.strip()]
            entry["title"] = lines[0].lstrip("# ") if lines else study.name
            entry["lead"] = next((ln for ln in lines[1:] if not ln.startswith("#")), "")[:400]
        results = study / "results"
        files = []
        if results.is_dir():
            for path in sorted(results.rglob("*")):
                if path.is_file():
                    files.append({"path": str(path.relative_to(root)), "bytes": path.stat().st_size})
        entry["result_files"] = len(files)
        entry["result_bytes"] = sum(f["bytes"] for f in files)
        entry["results_sample"] = files[:40]
        entry["viewer"] = "h1" if study.name == "v018-h1-detection" and (results / "gates.json").exists() else None
        studies.append(entry)
    return {"studies": studies}


def h1_cells(root: Path) -> dict:
    gates_path = root / H1 / "results/gates.json"
    gates = json.loads(gates_path.read_text())
    cells = []
    for key, cell in gates["cells"].items():
        cells.append({"cell": key, "family": cell["family"], "severity": cell["severity"], "runs": cell["runs"],
                      "wrong": cell["wrong"], "C_alarm": cell["C_alarm"], "A_alarm": cell["A_alarm"],
                      "class": cell.get("class"), "latency": cell.get("latency"),
                      "pass_but_broken": cell.get("pass_but_broken")})
    return {"thresholds": gates["thresholds"], "application_constants": gates["application_constants"],
            "order_statistic": gates.get("order_statistic"), "validity": gates.get("validity"),
            "cells": cells, "files": {"gates.json": _sha(gates_path)}}


def h1_runs(root: Path, cell: str) -> dict:
    if not cell.replace("-", "").isalnum():
        raise ValueError("bad cell name")
    path = root / H1 / "results/cells" / f"{cell}.jsonl"
    rows = []
    with open(path) as handle:
        for line in handle:
            r = json.loads(line)
            rows.append({"replica": r["replica"], "onset_sweep": r.get("onset_sweep"),
                         "wrong": (r.get("harm") or {}).get("wrong"), "harm": r.get("harm"),
                         "health_tests": r.get("health_tests"), "acceptance_rate_4dp": r.get("acceptance_rate_4dp"),
                         "local_bytes": r.get("local_bytes"), "local_sha256": r.get("local_sha256"),
                         "seeds": r.get("seeds")})
    return {"cell": cell, "runs": rows, "files": {path.name: _sha(path)}}


def h1_run(root: Path, cell: str, replica: int) -> dict:
    if not isinstance(cell, str) or not cell.replace("-", "").isalnum():
        raise ValueError("bad cell name")
    lib = _load_module(root / H1 / "h1lib.py", "rnglab_gui_h1lib")
    gates = json.loads((root / H1 / "results/gates.json").read_text())
    constants = gates["application_constants"]
    npz_path = root / H1 / "results/cells" / f"{cell}.npz"
    jsonl_path = root / H1 / "results/cells" / f"{cell}.jsonl"
    record = None
    with open(jsonl_path) as handle:
        for line in handle:
            r = json.loads(line)
            if r["replica"] == replica:
                record = r
                break
    with np.load(npz_path) as data:
        rows = np.flatnonzero(data["replica"] == replica)
        if not rows.size:
            raise KeyError(f"replica {replica} not in {cell}")
        i = int(rows[0])
        conv_p, conv_end, conv_bytes = data["conv_p"][i], data["conv_end"][i], data["conv_bytes"][i]
        app, app_end, app_bytes = data["app_stats"][i], data["app_end"][i], data["app_bytes"][i]
    checkpoints_c = [{k: (None if np.isnan(v) else float(v)) for k, v in zip(lib.CONV_P_KEYS, row)} for row in conv_p]
    stat_c = [lib.conventional_statistic(c) for c in checkpoints_c]
    checkpoints_a = [{"mean_D": float(r[0]), "uphill_fraction": float(r[1]), "mean_rg2": float(r[2])} for r in app]
    stat_a = [lib.application_statistic(c, constants) for c in checkpoints_a]
    onset = record.get("onset_sweep") if record else None
    tau = gates["thresholds"]
    det_c = lib.detection(list(zip(conv_end.tolist(), stat_c)), tau["C"], onset)
    det_a = lib.detection(list(zip(app_end.tolist(), stat_a)), tau["A"], onset)
    return {"cell": cell, "replica": replica, "record": record, "thresholds": tau,
            "conv": {"end": conv_end.tolist(), "bytes": conv_bytes.tolist(), "stat": stat_c,
                     "p": [[None if np.isnan(v) else float(v) for v in row] for row in conv_p],
                     "keys": list(lib.CONV_P_KEYS)},
            "app": {"end": app_end.tolist(), "bytes": app_bytes.tolist(), "stat": stat_a,
                    "mean_D": app[:, 0].tolist(), "uphill_fraction": app[:, 1].tolist(), "mean_rg2": app[:, 2].tolist()},
            "detection": {"C": det_c, "A": det_a},
            "computed_with": "h1lib.conventional_statistic, h1lib.application_statistic, h1lib.detection",
            "files": {npz_path.name: _sha(npz_path), jsonl_path.name: _sha(jsonl_path)}}


def detector(root: Path, path: str | None = None, samples_per_record: int | None = None,
             provenance: str | None = None, preview: int = 24, demo: bool = False) -> dict:
    """Audit of a recorded detector waveform export, or a clearly labelled synthetic demonstration.

    A real file is read only when the server was started with ``--detector-file`` (the operator's
    consent); the audit is RNGLab's own ``rnglab_crypto.sources.physical.audit_waveforms``. The
    synthetic demonstration draws from a fixed NumPy seed, is never stored as a run, and every
    value it returns is marked ``illustrative``.
    """
    preview = max(1, min(int(preview), 200))
    if demo:
        rng = np.random.default_rng(20261122)          # illustrative only; no RNGLab seed namespace
        n, m = 400, 64
        ticks = np.cumsum(rng.geometric(1 / 40, size=n)) * 8          # 8-tick lattice, on purpose
        base = rng.normal(1200, 25, size=(n, m))
        pulse = np.exp(-np.arange(m) / 9.0) * rng.uniform(5000, 90000, size=(n, 1))
        waves = np.clip(base + pulse, 0, 65535).astype(int)
        gaps = np.diff(ticks)
        return {"data_class": "illustrative", "label": "SYNTHETIC DEMO",
                "note": "Generated for display only (fixed seed); not a measurement and not a random source.",
                "records": n, "samples_per_record": m,
                "ceiling_fraction": float(np.mean(waves == 65535)), "floor_fraction": float(np.mean(waves == 0)),
                "records_with_ceiling_fraction": float(np.mean(np.any(waves == 65535, axis=1))),
                "timing": {"positive_gap_gcd_raw_units": int(np.gcd.reduce(gaps[gaps > 0])),
                           "zero_gaps": int(np.sum(gaps == 0)), "backward_gaps": int(np.sum(gaps < 0))},
                "gaps": gaps[:2000].tolist(), "waveforms": waves[:preview].tolist(),
                "timestamps": ticks[:preview].tolist(), "validated_entropy_credit_bits": 0}
    if not path:
        return {"data_class": "unavailable", "label": "NO DETECTOR FILE",
                "note": "No detector export was given. Start the laboratory with --detector-file PATH "
                        "--detector-samples N --detector-provenance TEXT to audit one (read-only)."}
    from rnglab15.ingest import waveforms
    from rnglab_crypto.sources.physical import audit_waveforms
    file = Path(path)
    if not file.is_file():
        return {"data_class": "unavailable", "label": "DETECTOR FILE MISSING",
                "note": "The configured detector file does not exist on this machine."}
    metadata = {"samples_per_record": int(samples_per_record or 0), "provenance": provenance or ""}
    report = audit_waveforms(file, metadata)
    waves, stamps, gaps, previous = [], [], [], None
    for k, record in enumerate(waveforms(file, samples=metadata["samples_per_record"])):
        if k < preview:
            waves.append(list(record.samples))
            stamps.append(record.timestamp)
        if previous is not None and len(gaps) < 20000:
            gaps.append(record.timestamp - previous)
        previous = record.timestamp
        if k >= preview and len(gaps) >= 20000:
            break
    report.update({"data_class": "recorded", "label": "RECORDED DETECTOR EXPORT", "waveforms": waves,
                   "timestamps": stamps, "gaps": gaps, "file_name": file.name,
                   "note": "Read-only audit. Statistical regularity is not entropy; no entropy credit is assigned."})
    report.get("metadata", {}).pop("path", None)
    return report


QUERIES = {"inventory": inventory, "h1_cells": h1_cells, "h1_runs": h1_runs, "h1_run": h1_run, "detector": detector}


def main(config: dict) -> int:
    root = Path(config["code_source"]["path"])
    query = config["query"]
    name = query.pop("name")
    try:
        out = {"ok": True, "data": QUERIES[name](root, **query)}
    except Exception as error:  # reported to the caller
        out = {"ok": False, "error": f"{type(error).__name__}: {error}"}
    from ..jsonsafe import dumps_line
    sys.stdout.write(dumps_line(out) + "\n")
    sys.stdout.flush()
    return 0 if out["ok"] else 1
