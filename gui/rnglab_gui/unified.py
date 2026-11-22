"""HTTP glue between the laboratory server and ``rnglab_unified`` (registry, evidence, index, provenance).

The ``rnglab_unified`` package is loaded from the laboratory's own checkout (``<checkout>/src``).
It is stdlib-only, so loading it does not import scientific code into the server process. Study
files are read from the *selected* code source, read-only.
"""
from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

from .codesources import OWN_CHECKOUT

_SRC = str(OWN_CHECKOUT / "src")
if _SRC not in sys.path:
    sys.path.append(_SRC)  # appended, never ahead of the standard library

from rnglab_unified import evidence, provenance, registry  # noqa: E402
from rnglab_unified.index import Index  # noqa: E402


class Unified:
    def __init__(self, lab, detector: dict | None = None):
        self.lab = lab
        self.index = Index(Path(lab.archive.root) / "index.sqlite")
        self.detector = detector or {}
        self._lock = threading.Lock()
        self._branches: tuple[float, dict] | None = None

    # ------------------------------------------------------------------ helpers
    def _root(self, source_id: str | None) -> Path:
        return Path(self.lab.sources.get(source_id).path)

    def _source_info(self, source_id: str | None) -> dict:
        s = self.lab.sources.get(source_id)
        return {"id": s.id, "branch": s.branch, "head": s.head, "dirty_files": s.dirty_files,
                "dirty_science": s.dirty_science, "own": s.own}

    # ------------------------------------------------------------------ GET
    def get(self, parts: list[str], query: dict, catalog_models: list[dict]) -> dict:
        src = query.get("source")
        if parts == ["registry"]:
            return {**registry.registry(OWN_CHECKOUT, catalog_models), "source": self._source_info(None)}
        if parts == ["atlas"]:
            return registry.atlas(OWN_CHECKOUT)
        if parts == ["studies"]:
            return {"source": self._source_info(src), "origin": "historical",
                    "studies": evidence.list_studies(self._root(src))}
        if len(parts) == 2 and parts[0] == "studies":
            return {"source": self._source_info(src), "origin": "historical",
                    **evidence.study_detail(self._root(src), parts[1])}
        if parts == ["file"]:
            out = evidence.read_file(self._root(src), query.get("path", ""))
            return {"source": self._source_info(src), **out, **provenance.file_git_state(self._root(src), out["path"])}
        if parts == ["search"]:
            return self.index.search(query.get("q", ""), int(query.get("limit", 100)))
        if parts == ["env"]:
            return {"environment": provenance.environment(self.lab.sources.get(src).interpreter),
                    "laboratory_git": provenance.git_fingerprint(OWN_CHECKOUT)}
        if parts == ["branches"]:
            with self._lock:
                if self._branches and time.monotonic() - self._branches[0] < 60 and not query.get("refresh"):
                    return self._branches[1]
            out = provenance.branch_graph(OWN_CHECKOUT)
            with self._lock:
                self._branches = (time.monotonic(), out)
            return out
        if parts == ["detector"]:
            demo = query.get("demo") == "1"
            q = {"name": "detector", "demo": demo, "preview": int(query.get("preview", 24))}
            if not demo and self.detector.get("path"):
                q.update(path=self.detector["path"], samples_per_record=self.detector.get("samples"),
                         provenance=self.detector.get("provenance"))
            out = self.lab.study(src, q, ttl=3600.0)
            out.get("source", {}).pop("path", None)
            return out
        raise KeyError("unknown unified endpoint")

    # ------------------------------------------------------------------ POST
    def post(self, parts: list[str], body: dict) -> dict:
        if parts == ["index", "rebuild"]:
            runs = self.index.rebuild_runs(self.lab.archive.runs)
            studies = self.index.rebuild_studies(self._root(body.get("source")))
            return {"runs": runs, "studies": studies}
        raise KeyError("unknown unified endpoint")
