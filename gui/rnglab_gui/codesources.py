"""Code sources: the checkouts a run can import RNGLab from.

Several Git branches hold different experimental implementations. They are
never imported into one Python process. A code source is one checked-out
working tree (the laboratory's own checkout or another ``git worktree``); a run
names one, and its worker process puts only that tree's ``src`` on ``sys.path``.
The laboratory reads Git metadata only (``worktree list``, ``rev-parse``,
``status``); it never checks out, creates or modifies a worktree.

Capabilities are probed by file presence, without importing anything. The
worker checks the API it needs when it starts and reports a mismatch as an
error of that run.
"""
from __future__ import annotations

import hashlib
import os
import subprocess
import sys
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

GUI_ROOT = Path(__file__).resolve().parent.parent          # .../gui
# Paths a worker imports or reads scientific code and study files from. Uncommitted changes
# elsewhere (the laboratory itself, docs) do not change what a run computes.
SCIENCE_PATHS = ("src/", "research/")
OWN_CHECKOUT = GUI_ROOT.parent                              # checkout that holds this laboratory


@dataclass
class CodeSource:
    id: str
    path: str
    branch: str | None
    head: str | None
    head_subject: str | None
    dirty_files: int | None
    dirty_science: int | None
    own: bool
    interpreter: str
    available: dict = field(default_factory=dict)   # model id -> list of missing files (empty = available)

    def to_dict(self) -> dict:
        out = asdict(self)
        out["dirty"] = None if self.dirty_files is None else self.dirty_files > 0
        return out


def _git(args: list[str], cwd: str | Path, timeout: float = 15.0) -> str | None:
    try:
        done = subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True, timeout=timeout,
                              env={**os.environ, "GIT_OPTIONAL_LOCKS": "0"})
    except (OSError, subprocess.SubprocessError):
        return None
    return done.stdout if done.returncode == 0 else None


def source_id(path: str | Path) -> str:
    return "cs-" + hashlib.sha256(str(Path(path).resolve()).encode()).hexdigest()[:10]


def git_state(path: str | Path) -> dict:
    """HEAD, branch, subject and number of changed or untracked files (read-only Git calls)."""
    head = _git(["rev-parse", "HEAD"], path)
    branch = _git(["symbolic-ref", "--quiet", "--short", "HEAD"], path)
    subject = _git(["log", "-1", "--format=%s"], path)
    status = _git(["status", "--porcelain=v1", "--untracked-files=normal"], path)
    lines = None if status is None else [ln for ln in status.splitlines() if ln.strip()]
    science = None if lines is None else [ln for ln in lines if ln[3:].lstrip('"').startswith(SCIENCE_PATHS)]
    return {"head": head.strip() if head else None, "branch": branch.strip() if branch else None,
            "head_subject": subject.strip() if subject else None,
            "dirty_files": None if lines is None else len(lines),
            "dirty_science": None if science is None else len(science),
            "status_lines": None if lines is None else lines[:200]}


def main_checkout(path: str | Path = OWN_CHECKOUT) -> Path | None:
    common = _git(["rev-parse", "--path-format=absolute", "--git-common-dir"], path)
    if not common:
        return None
    return Path(common.strip()).parent


def interpreter_for(path: str | Path) -> str:
    """The checkout's .venv, else the main checkout's .venv, else this interpreter."""
    candidates = [Path(path) / ".venv/bin/python"]
    main = main_checkout(path)
    if main is not None:
        candidates.append(main / ".venv/bin/python")
    for candidate in candidates:
        if candidate.exists():
            return str(candidate)
    return sys.executable


def probe(path: str | Path, requirements: dict[str, list[str]]) -> dict[str, list[str]]:
    root = Path(path)
    return {model: [req for req in reqs if not (root / req).exists()] for model, reqs in requirements.items()}


class CodeSourceRegistry:
    """Discovers worktrees of the repository that holds this laboratory; caches for a few seconds."""

    def __init__(self, requirements: dict[str, list[str]], extra_paths: list[str] | None = None, ttl: float = 5.0):
        self.requirements = requirements
        self.extra_paths = [str(Path(p).resolve()) for p in (extra_paths or [])]
        self.ttl = ttl
        self._lock = threading.Lock()
        self._cache: tuple[float, list[CodeSource]] | None = None

    def _worktree_paths(self) -> list[str]:
        out = _git(["worktree", "list", "--porcelain"], OWN_CHECKOUT)
        paths: list[str] = []
        if out:
            for line in out.splitlines():
                if line.startswith("worktree "):
                    candidate = line[len("worktree "):].strip()
                    if Path(candidate, "src").is_dir():
                        paths.append(str(Path(candidate).resolve()))
        own = str(OWN_CHECKOUT.resolve())
        if own not in paths:
            paths.insert(0, own)
        for extra in self.extra_paths:
            if extra not in paths and Path(extra, "src").is_dir():
                paths.append(extra)
        return paths

    def list(self, refresh: bool = False) -> list[CodeSource]:
        with self._lock:
            if not refresh and self._cache and time.monotonic() - self._cache[0] < self.ttl:
                return self._cache[1]
            sources = []
            own = str(OWN_CHECKOUT.resolve())
            for path in self._worktree_paths():
                state = git_state(path)
                sources.append(CodeSource(id=source_id(path), path=path, branch=state["branch"], head=state["head"],
                                          head_subject=state["head_subject"], dirty_files=state["dirty_files"],
                                          dirty_science=state["dirty_science"], own=(path == own), interpreter=interpreter_for(path),
                                          available=probe(path, self.requirements)))
            sources.sort(key=lambda s: (not s.own, s.path))
            self._cache = (time.monotonic(), sources)
            return sources

    def get(self, source_id_: str | None) -> CodeSource:
        sources = self.list()
        if not source_id_:
            return sources[0]
        for source in sources:
            if source.id == source_id_:
                return source
        sources = self.list(refresh=True)
        for source in sources:
            if source.id == source_id_:
                return source
        raise KeyError(f"unknown code source {source_id_}")
