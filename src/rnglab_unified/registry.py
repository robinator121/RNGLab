"""Capability registry: versioned descriptors of what RNGLab contains and how far each is backed.

Two sources are merged, never confused:

1. ``docs/unified/capability_matrix.json`` (the evidence inventory: every research line, its
   status under ``taxonomy.STATUSES``, evidence paths and source commit);
2. the laboratory catalog (``gui/rnglab_gui/catalog.py``): which simulations the interface can
   actually *run* through a worker adapter.

A runnable model links to its matrix entry by ``matrix_id``; running a model in the laboratory
does not change the scientific status of anything.
"""
from __future__ import annotations

import json
from pathlib import Path

from . import REGISTRY_SCHEMA, __version__
from .taxonomy import INTEGRATION, KINDS, ORDER, STATUSES, check_entry

MATRIX_REL = "docs/unified/capability_matrix.json"
ATLAS_REL = "docs/unified/research_atlas.json"

# Laboratory catalog model id -> capability-matrix id (filled where the matrix has the entry).
MODEL_LINKS = {
    "pimc-harmonic": ["pimc-harmonic-package", "exact-finite-bead-energy", "short-batch-means-intervals"],
    "h1-detection": ["study-v018-h1-detection", "stage-c-word-faults", "stage-c-conventional-battery",
                     "stage-c-application-monitors", "frozen-rank-calibration"],
    "pi-mc": ["pi-monte-carlo-toy"],
    "ising-2d": ["ising-2d-samplers", "ising-exact-enumeration"],
    "decision-support": ["decision-support-model"],
    "nuclear-aggregate": ["nuclear-b2-aggregate-engine", "nuclear-analytic-references", "numpy-pcg64-word-stream"],
    "nuclear-events": ["nuclear-layer-a-event-engine", "nuclear-analytic-references", "numpy-pcg64-word-stream"],
}


def _load_json(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


def load_matrix(root: str | Path) -> dict:
    """The capability matrix with per-entry consistency problems; a missing file gives an empty matrix."""
    path = Path(root) / MATRIX_REL
    data = _load_json(path)
    if not isinstance(data, dict):
        return {"schema": REGISTRY_SCHEMA, "entries": [], "problems": [f"{MATRIX_REL} missing or unreadable"],
                "available": False}
    entries = [e for e in data.get("entries", []) if isinstance(e, dict)]
    problems = []
    if data.get("schema") != REGISTRY_SCHEMA:
        problems.append(f"schema {data.get('schema')!r} is not {REGISTRY_SCHEMA!r}")
    seen = set()
    for entry in entries:
        problems.extend(check_entry(entry))
        if entry.get("id") in seen:
            problems.append(f"duplicate id {entry.get('id')!r}")
        seen.add(entry.get("id"))
    entries.sort(key=lambda e: (str(e.get("research_line")), ORDER.index(e["status"]) if e.get("status") in ORDER else 99,
                                str(e.get("id"))))
    return {"schema": data.get("schema"), "generated_from_commit": data.get("generated_from_commit"),
            "entries": entries, "problems": problems, "available": True}


def summary(matrix: dict) -> dict:
    by_status = {s: 0 for s in STATUSES}
    by_kind = {k: 0 for k in KINDS}
    by_line: dict[str, dict[str, int]] = {}
    for entry in matrix["entries"]:
        status = entry.get("status")
        if status in by_status:
            by_status[status] += 1
        if entry.get("kind") in by_kind:
            by_kind[entry["kind"]] += 1
        line = str(entry.get("research_line") or "unassigned")
        by_line.setdefault(line, {})
        by_line[line][status] = by_line[line].get(status, 0) + 1
    return {"by_status": by_status, "by_kind": by_kind, "by_line": by_line, "total": len(matrix["entries"])}


def registry(root: str | Path, catalog_models: list[dict] | None = None) -> dict:
    matrix = load_matrix(root)
    ids = {e.get("id") for e in matrix["entries"]}
    runnable = []
    for model in catalog_models or []:
        links = [i for i in MODEL_LINKS.get(model.get("id"), []) if i in ids]
        runnable.append({"model_id": model.get("id"), "title": model.get("title"), "matrix_ids": links,
                         "integration_method": "code-source-worker",
                         "note": "Running a model observes the unchanged scientific code; it does not change any "
                                 "evidence status."})
    return {"version": __version__, "statuses": STATUSES, "kinds": list(KINDS), "integration_methods": list(INTEGRATION),
            "matrix": matrix, "summary": summary(matrix), "runnable": runnable}


def atlas(root: str | Path) -> dict:
    """Research lines with their findings (positive and negative), from docs/unified/research_atlas.json."""
    data = _load_json(Path(root) / ATLAS_REL)
    if not isinstance(data, dict):
        return {"available": False, "lines": [], "problem": f"{ATLAS_REL} missing or unreadable"}
    data["available"] = True
    return data
