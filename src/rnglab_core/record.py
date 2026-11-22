"""Experiment records: one directory per experiment, content-digested, written atomically.

Layout::

    <root>/<experiment_id>/
        spec.json          the validated ExperimentSpec (canonical JSON) and its SHA-256
        manifest.json      code identity (git head, dirty paths, SHA-256 of every imported
                           RNGLab module file), environment, start/end times
        units/<unit>.json  one per (model, condition, replicate): observables, reference
                           comparison, consumed bytes and stream digests per role
        analysis.json      shared measurement layer output and cross-domain comparison
        record.sha256      sha256sum-style digests of every file above

``result_digest`` is computed over the scientific content only (spec, units, analysis without
wall-clock fields), so a replay can be compared bit for bit with the original.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import platform
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

VOLATILE_KEYS = {"wall_seconds", "started", "ended", "host_note"}


def _jsonable(obj: Any) -> Any:
    try:
        import numpy as np
    except ImportError:  # pragma: no cover
        np = None
    if isinstance(obj, dict):
        return {str(k): _jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_jsonable(v) for v in obj]
    if np is not None:
        if isinstance(obj, np.ndarray):
            return _jsonable(obj.tolist())
        if isinstance(obj, np.generic):
            return _jsonable(obj.item())
    if isinstance(obj, float):
        if math.isnan(obj):
            return "NaN"
        if math.isinf(obj):
            return "Infinity" if obj > 0 else "-Infinity"
        return obj
    if obj is None or isinstance(obj, (str, int, bool)):
        return obj
    return repr(obj)


def canonical(obj: Any) -> str:
    return json.dumps(_jsonable(obj), sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(obj: Any) -> str:
    return hashlib.sha256(canonical(obj).encode()).hexdigest()


def strip_volatile(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {k: strip_volatile(v) for k, v in obj.items() if k not in VOLATILE_KEYS}
    if isinstance(obj, list):
        return [strip_volatile(v) for v in obj]
    return obj


def write_json_atomic(path: Path, obj: Any) -> str:
    """Write canonical-but-indented JSON atomically; return the SHA-256 of the bytes written."""
    path.parent.mkdir(parents=True, exist_ok=True)
    data = (json.dumps(_jsonable(obj), sort_keys=True, indent=1, allow_nan=False) + "\n").encode()
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    return hashlib.sha256(data).hexdigest()


def _git(args: list[str], cwd: Path) -> str | None:
    try:
        done = subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True, timeout=20,
                              env={**os.environ, "GIT_OPTIONAL_LOCKS": "0"})
    except (OSError, subprocess.SubprocessError):
        return None
    return done.stdout.strip() if done.returncode == 0 else None


CODE_FILES = (
    "src/rnglab_core/*.py", "src/rnglab14/sources/*.py", "src/rnglab14/models/pimc_harmonic.py",
    "src/rnglab14/models/vmc_harmonic.py", "src/rnglab14/models/ising_2d.py", "src/rnglab14/statistics/multitest.py",
    "src/rnglab12/uncertainty.py", "src/rnglab16/core.py", "src/rnglab17/endpoints.py", "src/rnglab/seeds.py",
    "src/rnglab/nuclear/*.py", "research/v018-h1-detection/h1lib.py", "research/v020-nuclear-b2-verify/protocol.json",
)


def code_identity(repo_root: Path, extra_files: list[Path] | None = None) -> dict:
    """Git identity plus SHA-256 of a fixed list of files that define the computation.

    The list is fixed (``CODE_FILES``), not taken from ``sys.modules``, so a replay from another process
    (workbench or command line) hashes the same files. It includes the frozen Stage C library and the
    B2 protocol file whose schemes the nuclear adapters read.
    """
    repo_root = Path(repo_root).resolve()
    status = _git(["status", "--porcelain=v1", "--untracked-files=normal", "--", "src", "research", "gui"],
                  repo_root) or ""
    modules = {}
    for pattern in CODE_FILES:
        for p in sorted(repo_root.glob(pattern)):
            if p.is_file():
                modules[str(p.relative_to(repo_root))] = hashlib.sha256(p.read_bytes()).hexdigest()
    for p in extra_files or []:
        p = Path(p).resolve()
        try:
            modules[str(p.relative_to(repo_root))] = hashlib.sha256(p.read_bytes()).hexdigest()
        except (OSError, ValueError):
            continue
    return {"git_head": _git(["rev-parse", "HEAD"], repo_root),
            "git_branch": _git(["symbolic-ref", "--quiet", "--short", "HEAD"], repo_root),
            "dirty_paths": [line for line in status.splitlines() if line.strip()][:200],
            "module_sha256": modules,
            "modules_digest": digest(modules)}


def environment() -> dict:
    out = {"python": platform.python_version(), "implementation": platform.python_implementation(),
           "machine": platform.machine(), "system": platform.system(), "byteorder": sys.byteorder}
    try:
        import numpy
        out["numpy"] = numpy.__version__
    except ImportError:  # pragma: no cover
        out["numpy"] = None
    return out


def seal(directory: Path) -> str:
    """Write record.sha256 over every file in the record; return the digest of that manifest."""
    lines = []
    for path in sorted(p for p in Path(directory).rglob("*") if p.is_file() and p.name != "record.sha256"
                       and not p.name.endswith(".tmp")):
        lines.append(f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.relative_to(directory)}")
    text = "\n".join(lines) + "\n"
    (Path(directory) / "record.sha256").write_text(text)
    return hashlib.sha256(text.encode()).hexdigest()


def verify_seal(directory: Path) -> dict:
    directory = Path(directory)
    bad, missing = [], []
    for line in (directory / "record.sha256").read_text().splitlines():
        recorded, name = line.split("  ", 1)
        path = directory / name
        if not path.is_file():
            missing.append(name)
        elif hashlib.sha256(path.read_bytes()).hexdigest() != recorded:
            bad.append(name)
    return {"state": "ok" if not bad and not missing else "failed", "changed": bad, "missing": missing}
