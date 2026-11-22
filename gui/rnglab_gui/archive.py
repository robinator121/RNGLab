"""Run archive: one directory per run, written by the server and the worker.

    <data>/runs/<run_id>/
        config.json       everything needed to run it again: model, validated parameters
                          (scientific and execution separated), code source path, branch,
                          commit and dirty state, interpreter, laboratory commit
        telemetry.jsonl   every message the worker emitted, in order, unchanged
        stderr.log        the worker's standard error
        status.json       state, timestamps, exit code, digest, summary, integrity states
        result.json       official result (JSON-safe) and its reproducibility digest
        integrity.json    integrity checks with evidence states
        arrays.npz        large arrays of the result (traces, event tables), written by the worker
        verification.json present after an unobserved re-run was compared

Files are never rewritten after a run has ended, except status.json and
verification.json (and both keep their earlier content in fields).
"""
from __future__ import annotations

import io
import itertools
import json
import os
import re
import threading
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

RUN_ID_RE = re.compile(r"^[0-9]{8}-[0-9]{6}-[a-z0-9-]{2,40}-[0-9]{3,6}$")
_counter = itertools.count(1)
_lock = threading.Lock()


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def write_json(path: Path, data: Any) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=1, sort_keys=False, allow_nan=False))
    os.replace(tmp, path)


def read_json(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return default


class Archive:
    def __init__(self, root: str | Path):
        self.root = Path(root).resolve()  # workers run with cwd = run dir, so paths must be absolute
        self.runs = self.root / "runs"
        self.runs.mkdir(parents=True, exist_ok=True)

    def new_run(self, model_id: str) -> tuple[str, Path]:
        with _lock:
            while True:
                stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
                run_id = f"{stamp}-{model_id}-{os.getpid() % 1000:03d}{next(_counter) % 1000:03d}"
                path = self.runs / run_id
                if not path.exists():
                    path.mkdir(parents=True)
                    return run_id, path

    def path(self, run_id: str) -> Path:
        if not RUN_ID_RE.match(run_id):
            raise KeyError("malformed run id")
        path = self.runs / run_id
        if path.is_symlink() or not path.is_dir() or path.resolve().parent != self.runs.resolve():
            raise KeyError(f"no run {run_id}")
        return path

    def summary(self, run_id: str) -> dict:
        path = self.path(run_id)
        config = read_json(path / "config.json", {})
        status = read_json(path / "status.json", {})
        verification = read_json(path / "verification.json")
        return {
            "run_id": run_id, "model": config.get("model"), "model_title": config.get("model_title"),
            "created": config.get("created"), "label": config.get("params", {}).get("label"),
            "namespace": config.get("params", {}).get("namespace"),
            "code_branch": config.get("code_source", {}).get("branch"),
            "code_head": config.get("code_source", {}).get("head"),
            "code_dirty": config.get("code_source", {}).get("dirty_files"),
            "state": status.get("state", "unknown"), "ended": status.get("ended"), "digest": status.get("digest"),
            "summary": status.get("summary"), "integrity": status.get("integrity_states"),
            "pair_id": config.get("pair_id"), "pair_role": config.get("pair_role"),
            "parent_run": config.get("parent_run"), "progress": status.get("progress"),
            "verified": (verification or {}).get("state"),
        }

    def list(self) -> list[dict]:
        out = []
        for child in sorted(self.runs.iterdir(), reverse=True):
            if child.is_dir() and RUN_ID_RE.match(child.name):
                try:
                    out.append(self.summary(child.name))
                except KeyError:
                    continue
        return out

    def detail(self, run_id: str) -> dict:
        path = self.path(run_id)
        return {
            "summary": self.summary(run_id),
            "config": read_json(path / "config.json"),
            "status": read_json(path / "status.json"),
            "result": read_json(path / "result.json"),
            "integrity": read_json(path / "integrity.json"),
            "verification": read_json(path / "verification.json"),
            "files": sorted(p.name for p in path.iterdir() if p.is_file()),
            "stderr_tail": _tail(path / "stderr.log", 4000),
        }

    def telemetry(self, run_id: str, after_seq: int = 0) -> list[dict]:
        out = []
        try:
            with open(self.path(run_id) / "telemetry.jsonl") as handle:
                for line in handle:
                    try:
                        message = json.loads(line)
                    except ValueError:
                        continue
                    if message.get("seq", 0) > after_seq:
                        out.append(message)
        except OSError:
            pass
        return out

    def array_names(self, run_id: str) -> list[str]:
        import numpy as np
        path = self.path(run_id) / "arrays.npz"
        if not path.exists():
            return []
        with np.load(path) as data:
            return [f"{name}:{data[name].dtype}:{list(data[name].shape)}" for name in data.files]

    def array(self, run_id: str, name: str, max_rows: int = 5000, row: int | None = None) -> dict:
        max_rows = max(1, min(int(max_rows), 200_000))
        """One stored array (recorded data), decimated by rows to at most ``max_rows`` for display."""
        import numpy as np
        path = self.path(run_id) / "arrays.npz"
        with np.load(path) as data:
            if name not in data.files:
                raise KeyError(f"no array {name}")
            array = data[name]
        rows = array.shape[0] if array.ndim else 1
        if row is not None:
            if array.ndim == 0 or not 0 <= row < rows:
                raise KeyError("row out of range")
            return {"name": name, "row": row, "rows": rows, "values": _finite(array[row].tolist()),
                    "dtype": str(array.dtype)}
        stride = max(1, -(-rows // max_rows)) if array.ndim else 1
        view = array[::stride] if array.ndim else array
        return {"name": name, "rows": rows, "stride": stride, "shape": list(array.shape), "dtype": str(array.dtype),
                "values": _finite(view.tolist())}

    def export_zip(self, run_id: str) -> bytes:
        path = self.path(run_id)
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for child in sorted(path.iterdir()):
                if child.is_file() and not child.is_symlink() and not child.name.endswith(".tmp"):
                    archive.write(child, f"{run_id}/{child.name}")
        return buffer.getvalue()


def _tail(path: Path, limit: int) -> str:
    try:
        data = path.read_bytes()
    except OSError:
        return ""
    return data[-limit:].decode(errors="replace")


def wait_for(predicate, timeout: float, interval: float = 0.05) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


def _finite(values):
    """Replace NaN and infinities by None so that the strict JSON encoder accepts stored arrays."""
    if isinstance(values, float):
        return values if values == values and values not in (float("inf"), float("-inf")) else None
    if isinstance(values, list):
        return [_finite(v) for v in values]
    return values
