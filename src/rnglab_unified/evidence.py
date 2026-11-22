"""Read-only browser over frozen studies in a code source.

Everything here opens files for reading only. Paths requested by a client are
resolved and must stay inside ``<root>/research`` (or ``<root>/docs``); symlinks
leaving that tree are refused. Digests are SHA-256 over the file bytes.

A study's freeze state:

- ``ok``: ``protocol.sha256`` names the SHA-256 of ``protocol.json`` and it matches;
- ``failed``: it names a different digest (the protocol changed after freezing);
- ``unavailable``: no ``protocol.sha256`` (not frozen, or frozen by another mechanism).

Other ``*.sha256`` files in a study (``<digest>  <relative path>`` lines, as written
by ``sha256sum``) are checked the same way and reported per line.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path

PREVIEW_LIMIT = 512 * 1024
TEXT_SUFFIXES = {".json", ".md", ".txt", ".csv", ".tsv", ".py", ".sha256", ".log", ".jsonl", ".toml", ".yaml", ".yml"}
BROWSABLE_TOPS = ("research", "docs")
_HEX64 = re.compile(r"^[0-9a-f]{64}$")


class EvidenceError(ValueError):
    pass


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _files(base: Path) -> list[Path]:
    """Regular files under base, never following or listing symbolic links (files or directories)."""
    out = []
    for dirpath, dirnames, filenames in os.walk(base, followlinks=False):
        here = Path(dirpath)
        dirnames[:] = sorted(d for d in dirnames if d != "__pycache__" and not (here / d).is_symlink())
        out.extend(here / f for f in sorted(filenames) if not (here / f).is_symlink() and (here / f).is_file())
    return out


def _safe(root: Path, rel: str) -> Path:
    """Resolve rel under root/{research,docs}; refuse anything else."""
    if not isinstance(rel, str) or not rel or "\x00" in rel or rel.startswith(("/", "\\")):
        raise EvidenceError("relative path expected")
    root = Path(root).resolve()
    target = (root / rel).resolve()
    allowed = [root / top for top in BROWSABLE_TOPS]
    if not any(target == a or a in target.parents for a in allowed):
        raise EvidenceError("path outside research/ and docs/ is not browsable")
    return target


def _read_digest_file(path: Path) -> list[tuple[str, str]]:
    """[(digest, name)] from a sha256sum-style file; a bare digest gives name ''."""
    out = []
    for line in path.read_text(errors="replace").splitlines():
        parts = line.strip().split(None, 1)
        if parts and _HEX64.match(parts[0].lower()):
            out.append((parts[0].lower(), parts[1].lstrip("*").strip() if len(parts) > 1 else ""))
    return out


def freeze_state(study_dir: Path) -> dict:
    protocol = study_dir / "protocol.json"
    record = study_dir / "protocol.sha256"
    if not protocol.is_file() or protocol.is_symlink() or record.is_symlink():
        return {"state": "unavailable", "reason": "no protocol.json (or a symbolic link, which is not followed)"}
    actual = sha256_file(protocol)
    if not record.is_file():
        return {"state": "unavailable", "reason": "no protocol.sha256", "actual": actual}
    entries = _read_digest_file(record)
    recorded = next((d for d, name in entries if name in ("", "protocol.json") or name.endswith("/protocol.json")), None)
    if recorded is None:
        return {"state": "failed", "reason": "protocol.sha256 names no digest for protocol.json", "actual": actual}
    return {"state": "ok" if recorded == actual else "failed", "recorded": recorded, "actual": actual}


def _protocol_meta(study_dir: Path) -> dict:
    meta: dict = {}
    for name in ("protocol.json", "design.json"):
        path = study_dir / name
        if not path.is_file() or path.is_symlink():
            continue
        try:
            data = json.loads(path.read_text())
        except (OSError, ValueError):
            meta["protocol_parse_error"] = name
            continue
        if isinstance(data, dict):
            for key in ("id", "title", "status", "namespace", "version", "frozen", "created"):
                if key in data and isinstance(data[key], (str, int, float, bool)):
                    meta.setdefault(key, data[key])
    return meta


def _headline(study_dir: Path) -> str | None:
    for name in ("README.md", "REPORT.md", "report.md"):
        path = study_dir / name
        if path.is_file() and not path.is_symlink():
            for line in path.read_text(errors="replace").splitlines():
                text = line.strip().lstrip("#").strip()
                if text:
                    return text[:200]
    return None


def list_studies(root: str | Path) -> list[dict]:
    """Every research/<study>/ with protocol identity, freeze state and file counts (no hashing of results)."""
    root = Path(root).resolve()
    research = root / "research"
    if not research.is_dir():
        return []
    out = []
    for study in sorted(p for p in research.iterdir()
                        if p.is_dir() and not p.is_symlink() and not p.name.startswith((".", "__"))):
        files = _files(study)
        results = [p for p in files if "results" in p.relative_to(study).parts[:1]]
        out.append({
            "id": study.name, "path": f"research/{study.name}", "title": _headline(study),
            "meta": _protocol_meta(study), "freeze": freeze_state(study),
            "n_files": len(files), "n_result_files": len(results),
            "bytes": sum(p.stat().st_size for p in files),
            "has_results": bool(results),
        })
    return out


def study_detail(root: str | Path, study_id: str) -> dict:
    """Files of one study with sizes and SHA-256, plus every *.sha256 manifest checked line by line."""
    if not re.match(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,120}$", study_id or ""):
        raise EvidenceError("bad study id")
    root = Path(root).resolve()
    study = _safe(root, f"research/{study_id}")
    if not study.is_dir() or (root / "research" / study_id).is_symlink():
        raise KeyError(study_id)
    files = []
    for path in _files(study):
        files.append({"path": str(path.relative_to(root)), "bytes": path.stat().st_size, "sha256": sha256_file(path)})
    by_path = {f["path"]: f["sha256"] for f in files}
    manifests = []
    research = root / "research"
    for manifest in [f for f in _files(study) if f.suffix == ".sha256" or f.name.upper().endswith("SHA256SUMS")]:
        checks = []
        for digest, name in _read_digest_file(manifest):
            if not name:
                continue
            # names are relative to the manifest's folder (sha256sum convention) or to the checkout root
            # Only files inside research/ are hashed; a name pointing elsewhere is reported, not read.
            rel, outside = None, False
            for base in (manifest.parent, root):
                raw = base / name
                candidate = raw.resolve()
                if candidate.is_file():
                    if research in candidate.parents and not raw.is_symlink():
                        rel = str(candidate.relative_to(root))
                    else:
                        outside = True
                    break
            actual = by_path.get(rel) if rel else None
            if rel and actual is None:  # another study's file: hash it read-only
                actual = sha256_file(root / rel)
            checks.append({"name": name, "recorded": digest, "actual": actual,
                           "state": "unavailable" if actual is None else ("ok" if actual == digest else "failed"),
                           **({"reason": "names a file outside research/; not read"} if outside else {})})
        manifests.append({"file": str(manifest.relative_to(root)), "checks": checks})
    return {"id": study_id, "path": f"research/{study_id}", "meta": _protocol_meta(study),
            "freeze": freeze_state(study), "files": files, "manifests": manifests}


def read_file(root: str | Path, rel: str) -> dict:
    """A bounded text preview of one file under research/ or docs/, with its SHA-256."""
    path = _safe(root, rel)
    if not path.is_file():
        raise KeyError(rel)
    size = path.stat().st_size
    out = {"path": rel, "bytes": size, "sha256": sha256_file(path), "truncated": size > PREVIEW_LIMIT}
    if path.suffix.lower() not in TEXT_SUFFIXES:
        out["kind"] = "binary"
        out["text"] = None
        return out
    with open(path, "rb") as handle:
        raw = handle.read(PREVIEW_LIMIT)
    out["kind"] = "json" if path.suffix.lower() == ".json" else "text"
    out["text"] = raw.decode("utf-8", errors="replace")
    return out


def tree_digest(root: str | Path, rel: str) -> str:
    """SHA-256 over (relative path, file digest) pairs of a directory, for whole-study identity."""
    base = _safe(root, rel)
    h = hashlib.sha256()
    for path in _files(base):
        h.update(str(path.relative_to(base)).encode() + b"\0" + sha256_file(path).encode() + b"\n")
    return h.hexdigest()


__all__ = ["EvidenceError", "sha256_file", "freeze_state", "list_studies", "study_detail", "read_file",
           "tree_digest", "PREVIEW_LIMIT"]
