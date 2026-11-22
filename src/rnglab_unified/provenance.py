"""Environment and Git fingerprints (read-only; no scientific imports).

Package versions are read with ``importlib.metadata`` so that numpy is never imported into
the server process. Paths under the user's home directory are shown as ``~/...``.
"""
from __future__ import annotations

import hashlib
import os
import platform
import subprocess
import sys
from importlib import metadata
from pathlib import Path

PACKAGES = ("numpy", "scipy", "pytest")


def redact(path: str | os.PathLike | None) -> str | None:
    if path is None:
        return None
    text = str(path)
    home = str(Path.home())
    return "~" + text[len(home):] if text == home or text.startswith(home + os.sep) else text


def environment(interpreter: str | None = None) -> dict:
    versions = {}
    for name in PACKAGES:
        try:
            versions[name] = metadata.version(name)
        except metadata.PackageNotFoundError:
            versions[name] = None
    return {"python": platform.python_version(), "implementation": platform.python_implementation(),
            "platform": platform.platform(terse=True), "machine": platform.machine(),
            "server_interpreter": redact(sys.executable), "worker_interpreter": redact(interpreter),
            "packages": versions, "byteorder": sys.byteorder}


def _git(args: list[str], cwd: str | Path) -> str | None:
    try:
        done = subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True, timeout=15,
                              env={**os.environ, "GIT_OPTIONAL_LOCKS": "0"})
    except (OSError, subprocess.SubprocessError):
        return None
    return done.stdout.strip() if done.returncode == 0 else None


def git_fingerprint(root: str | Path) -> dict:
    """HEAD, branch and a digest of the uncommitted diff (so 'dirty' runs can still be told apart)."""
    head = _git(["rev-parse", "HEAD"], root)
    status = _git(["status", "--porcelain=v1", "--untracked-files=normal"], root) or ""
    diff = _git(["diff", "HEAD", "--binary"], root) or ""
    return {"head": head, "branch": _git(["symbolic-ref", "--quiet", "--short", "HEAD"], root),
            "dirty": bool(status.strip()), "dirty_paths": len([l for l in status.splitlines() if l.strip()]),
            "diff_sha256": hashlib.sha256(diff.encode()).hexdigest() if status.strip() else None}


def branch_graph(root: str | Path, limit: int = 400) -> dict:
    """Local and remote branch heads with merge relations to the checkout's HEAD (read-only)."""
    refs = _git(["for-each-ref", "--format=%(refname:short)\t%(objectname)\t%(committerdate:iso-strict)\t%(subject)",
                 "refs/heads", "refs/remotes"], root) or ""
    head = _git(["rev-parse", "HEAD"], root)
    out = []
    for line in refs.splitlines()[:limit]:
        parts = line.split("\t", 3)
        if len(parts) < 4 or parts[0].endswith("/HEAD"):
            continue
        name, sha, date, subject = parts
        ancestor = subprocess.run(["git", "merge-base", "--is-ancestor", sha, head or "HEAD"], cwd=str(root),
                                  capture_output=True).returncode == 0 if head else None
        counts = _git(["rev-list", "--left-right", "--count", f"{head}...{sha}"], root) if head else None
        behind, ahead = (counts.split() if counts else (None, None))
        out.append({"ref": name, "commit": sha, "date": date, "subject": subject,
                    "is_backup": name.startswith("backup/"), "ancestor_of_head": ancestor,
                    "unique_commits": int(ahead) if ahead is not None else None,
                    "head_only_commits": int(behind) if behind is not None else None})
    return {"head": head, "refs": out}


def file_git_state(root: str | Path, rel: str) -> dict:
    """Whether a repository file is committed and unchanged: only then is it labelled a historical record."""
    tracked = _git(["ls-files", "--error-unmatch", "--", rel], root) is not None
    changed = bool((_git(["status", "--porcelain=v1", "--", rel], root) or "").strip())
    commit = _git(["log", "-1", "--format=%H", "--", rel], root) if tracked else None
    origin = "historical" if tracked and not changed else "uncommitted"
    return {"origin": origin, "git_tracked": tracked, "git_modified": changed, "last_commit": commit or None}
