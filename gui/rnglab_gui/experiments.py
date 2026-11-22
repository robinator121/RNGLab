"""Compound experiments in the laboratory: process management around ``worker/experiment.py``.

The server validates, launches, cancels and replays experiments by starting child processes that
run the shared ``rnglab_core`` engine from the chosen code source. It reads finished records from
disk (JSON and SHA-256 only) and checks their seal with ``rnglab_core.record.verify_seal``, whose
module imports nothing scientific. Progress events are kept beside the record in
``<data>/experiment-events/<id>.jsonl`` (outside the sealed record directory).
"""
from __future__ import annotations

import json
import queue
import re
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from .codesources import OWN_CHECKOUT

WORKER = Path(__file__).resolve().parent / "worker" / "experiment.py"
ID_RE = re.compile(r"^[0-9]{8}-[0-9]{6}-[0-9a-f]{6}(-replay-[0-9a-f]{4})?$")

_SRC = str(OWN_CHECKOUT / "src")
if _SRC not in sys.path:
    sys.path.append(_SRC)
from rnglab_core.record import verify_seal  # noqa: E402  (stdlib-only module)


class ExperimentError(ValueError):
    pass


def _read(path: Path, default=None):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return default


class Job:
    def __init__(self, exp_id: str, process: subprocess.Popen, events: Path):
        self.id, self.process, self.events_path = exp_id, process, events
        self.subscribers: list[queue.Queue] = []
        self.lock = threading.Lock()
        self.done = threading.Event()
        self.seq = 0
        self.last: dict = {}
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self) -> None:
        try:
            with open(self.events_path, "a") as sink:
                for raw in self.process.stdout:
                    line = raw.decode(errors="replace").strip()
                    if not line:
                        continue
                    try:
                        event = json.loads(line)
                    except ValueError:
                        continue
                    with self.lock:
                        self.seq += 1
                        event["seq"] = self.seq
                        self.last = event
                        subs = list(self.subscribers)
                    text = json.dumps(event)
                    sink.write(text + "\n")
                    sink.flush()
                    for q in subs:
                        q.put(text)
        finally:
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()
            self.done.set()
            for q in list(self.subscribers):
                q.put(None)

    def subscribe(self) -> queue.Queue:
        q: queue.Queue = queue.Queue()
        with self.lock:
            self.subscribers.append(q)
        return q

    def cancel(self) -> None:
        if self.process.poll() is None and self.process.stdin:
            try:
                self.process.stdin.write(b'{"cmd": "cancel"}\n')
                self.process.stdin.flush()
            except (BrokenPipeError, OSError):
                pass


class ExperimentManager:
    def __init__(self, data_dir: Path, sources, max_active: int = 2):
        self.root = Path(data_dir).resolve() / "experiments"
        self.events = Path(data_dir).resolve() / "experiment-events"
        self.root.mkdir(parents=True, exist_ok=True)
        self.events.mkdir(parents=True, exist_ok=True)
        self.sources = sources
        self.max_active = max_active
        self.jobs: dict[str, Job] = {}
        self.lock = threading.Lock()
        self._catalog: dict[str, tuple[float, dict]] = {}

    # ------------------------------------------------------------------ helpers
    def _one_shot(self, mode: str, payload: dict, source_id: str | None, timeout: float = 120) -> dict:
        source = self.sources.get(source_id)
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as handle:
            json.dump({"code_root": source.path, **payload}, handle)
            path = handle.name
        try:
            done = subprocess.run([source.interpreter, "-I", str(WORKER), mode, path], capture_output=True,
                                  timeout=timeout)
        finally:
            Path(path).unlink(missing_ok=True)
        lines = [ln for ln in done.stdout.decode(errors="replace").splitlines() if ln.strip()]
        if not lines:
            raise ExperimentError(done.stderr.decode(errors="replace")[-1500:] or "worker produced no output")
        return json.loads(lines[-1])

    def path(self, exp_id: str) -> Path:
        if not isinstance(exp_id, str) or not ID_RE.match(exp_id):
            raise KeyError("malformed experiment id")
        p = self.root / exp_id
        if p.is_symlink() or not p.is_dir():
            raise KeyError(f"no experiment {exp_id}")
        return p

    def active(self) -> list[Job]:
        with self.lock:
            return [j for j in self.jobs.values() if not j.done.is_set()]

    # ------------------------------------------------------------------ API
    def catalog(self, source_id: str | None) -> dict:
        source = self.sources.get(source_id)
        cached = self._catalog.get(source.id)
        if cached and time.monotonic() - cached[0] < 600:
            return cached[1]
        out = self._one_shot("catalog", {}, source_id)
        self._catalog[source.id] = (time.monotonic(), out)
        return out

    def validate(self, spec: dict, source_id: str | None) -> dict:
        return self._one_shot("validate", {"spec": spec}, source_id)

    def _start(self, mode: str, payload: dict, source_id: str | None, exp_id: str) -> Job:
        source = self.sources.get(source_id)
        with self.lock:
            if len([j for j in self.jobs.values() if not j.done.is_set()]) >= self.max_active:
                raise ExperimentError(f"at most {self.max_active} experiments at a time")
            cfg = self.events / f"{exp_id}.config.json"
            cfg.write_text(json.dumps({"code_root": source.path, "experiments_root": str(self.root),
                                       "experiment_id": exp_id, **payload}))
            try:
                process = subprocess.Popen([source.interpreter, "-I", str(WORKER), mode, str(cfg)],
                                           stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                           stderr=open(self.events / f"{exp_id}.stderr.log", "wb"))
            except OSError as error:
                raise ExperimentError(f"could not start the experiment worker: {error}") from None
            job = Job(exp_id, process, self.events / f"{exp_id}.jsonl")
            self.jobs[exp_id] = job
        return job

    def run(self, spec: dict, source_id: str | None) -> dict:
        report = self.validate(spec, source_id)
        if not report.get("ok"):
            raise ExperimentError("; ".join(report.get("errors", ["invalid experiment"])))
        exp_id = f"{datetime.now(timezone.utc):%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:6]}"
        self._start("run", {"spec": spec}, source_id, exp_id)
        return {"experiment_id": exp_id, "validation": report}

    def replay(self, exp_id: str, source_id: str | None) -> dict:
        self.path(exp_id)
        job_id = f"{exp_id}-replay-{uuid.uuid4().hex[:4]}" if "-replay-" not in exp_id else exp_id
        self._start("replay", {"original_id": exp_id}, source_id, job_id)
        return {"job_id": job_id}

    def cancel(self, exp_id: str) -> dict:
        with self.lock:
            job = self.jobs.get(exp_id)
        if job is None or job.done.is_set():
            raise ExperimentError("experiment is not running")
        job.cancel()
        return {"ok": True}

    def list(self) -> list[dict]:
        out = []
        for d in sorted((p for p in self.root.iterdir() if p.is_dir() and not p.is_symlink()), reverse=True):
            spec = _read(d / "spec.json", {}) or {}
            status = _read(d / "status.json", {}) or {}
            s = spec.get("spec", {})
            out.append({"id": d.name, "title": s.get("title"), "state": status.get("state"),
                        "models": [m["model"] for m in s.get("models", [])], "fault": s.get("fault"),
                        "source": s.get("source"), "replicates": s.get("replicates"),
                        "units_done": status.get("units_done"), "units_total": status.get("units_total"),
                        "result_digest": status.get("result_digest"), "replay_of": d.name.split("-replay-")[0]
                        if "-replay-" in d.name else None, "running": d.name in {j.id for j in self.active()}})
        return out

    def detail(self, exp_id: str) -> dict:
        try:
            d = self.path(exp_id)
        except KeyError:
            job = self.job(exp_id) if isinstance(exp_id, str) and ID_RE.match(exp_id) else None
            if job is None:
                raise
            # launched, record directory not created yet (the worker is starting)
            return {"id": exp_id, "spec": None, "status": {"state": "starting"}, "manifest": None, "analysis": None,
                    "units": [], "seal": {"state": "unavailable"}, "running": not job.done.is_set(),
                    "origin": "generated", "events": self.events_since(exp_id)[-3:]}
        units = []
        for p in sorted((d / "units").glob("*.json")):
            u = _read(p, {}) or {}
            units.append({k: u.get(k) for k in ("model", "condition", "replicate", "endpoints", "clocks", "streams",
                                                 "battery", "error", "observables", "notes", "model_digest")})
        return {"id": exp_id, "spec": _read(d / "spec.json"), "status": _read(d / "status.json"),
                "manifest": _read(d / "manifest.json"), "analysis": _read(d / "analysis.json"), "units": units,
                "seal": verify_seal(d) if (d / "record.sha256").exists() else {"state": "unavailable"},
                "running": exp_id in {j.id for j in self.active()}, "origin": "generated"}

    def events_since(self, exp_id: str, after: int = 0) -> list[dict]:
        path = self.events / f"{exp_id}.jsonl"
        if not path.exists():
            return []
        out = []
        for line in path.read_text().splitlines():
            try:
                e = json.loads(line)
            except ValueError:
                continue
            if e.get("seq", 0) > after:
                out.append(e)
        return out

    def job(self, exp_id: str) -> Job | None:
        with self.lock:
            return self.jobs.get(exp_id)
