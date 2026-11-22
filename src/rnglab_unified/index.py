"""Local SQLite index of laboratory runs and frozen studies, for search.

The index is a cache: it is rebuilt from the run archive (``<data>/runs/*/``) and from the
evidence browser and may be deleted at any time. Stored runs and studies remain the
authority; the index never stores a scientific value that is not also in a run file.
"""
from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path

from . import evidence

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
  run_id TEXT PRIMARY KEY, model TEXT, label TEXT, namespace TEXT, state TEXT, created TEXT,
  code_branch TEXT, code_head TEXT, digest TEXT, verified TEXT, pair_id TEXT, pair_role TEXT,
  origin TEXT NOT NULL DEFAULT 'generated', text TEXT
);
CREATE TABLE IF NOT EXISTS studies (
  study_id TEXT PRIMARY KEY, title TEXT, status TEXT, namespace TEXT, freeze_state TEXT,
  n_result_files INTEGER, origin TEXT NOT NULL DEFAULT 'historical', text TEXT
);
"""
_lock = threading.Lock()


class Index:
    def __init__(self, path: str | Path):
        self.path = Path(path).resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as conn:
            conn.executescript(SCHEMA)

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        return conn

    def rebuild_runs(self, runs_dir: str | Path) -> int:
        rows = []
        for run in sorted(Path(runs_dir).glob("*/")):
            try:
                cfg = json.loads((run / "config.json").read_text())
            except (OSError, ValueError):
                continue
            try:
                status = json.loads((run / "status.json").read_text())
            except (OSError, ValueError):
                status = {}
            try:
                verification = json.loads((run / "verification.json").read_text())
            except (OSError, ValueError):
                verification = {}
            params = cfg.get("params") or {}
            src = cfg.get("code_source") or {}
            rows.append((run.name, cfg.get("model"), params.get("label"), params.get("namespace"), status.get("state"),
                         cfg.get("created"), src.get("branch"), src.get("head"), status.get("digest"),
                         verification.get("state"), cfg.get("pair_id"), cfg.get("pair_role"), "generated",
                         " ".join(str(v) for v in (run.name, cfg.get("model"), params.get("label"), json.dumps(params)))))
        with _lock, self._conn() as conn:
            conn.execute("DELETE FROM runs")
            conn.executemany("INSERT OR REPLACE INTO runs VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
        return len(rows)

    def rebuild_studies(self, code_root: str | Path) -> int:
        rows = []
        for s in evidence.list_studies(code_root):
            meta = s["meta"]
            rows.append((s["id"], s["title"], str(meta.get("status")) if meta.get("status") is not None else None,
                         meta.get("namespace"), s["freeze"]["state"], s["n_result_files"], "historical",
                         " ".join(str(v) for v in (s["id"], s["title"], meta.get("id"), meta.get("status")))))
        with _lock, self._conn() as conn:
            conn.execute("DELETE FROM studies")
            conn.executemany("INSERT OR REPLACE INTO studies VALUES (?,?,?,?,?,?,?,?)", rows)
        return len(rows)

    def search(self, text: str = "", limit: int = 100) -> dict:
        like = f"%{text}%"
        limit = max(1, min(int(limit), 1000))
        with self._conn() as conn:
            runs = [dict(r) for r in conn.execute(
                "SELECT run_id, model, label, state, created, digest, verified, pair_role, origin FROM runs "
                "WHERE text LIKE ? ORDER BY created DESC LIMIT ?", (like, limit))]
            studies = [dict(r) for r in conn.execute(
                "SELECT study_id, title, status, freeze_state, n_result_files, origin FROM studies "
                "WHERE text LIKE ? ORDER BY study_id LIMIT ?", (like, limit))]
        return {"query": text, "runs": runs, "studies": studies}
