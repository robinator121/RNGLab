"""Run manager: starts workers, relays control, stores and fans out telemetry.

Each run is a child process (``worker/bootstrap.py`` under ``python -I``) with its
own interpreter state. The server never imports scientific code. A reader thread
per run copies every stdout line unchanged to telemetry.jsonl and hands it to the
subscribers of that run (server-sent-event connections). Commands go to the
worker's stdin as JSON lines.
"""
from __future__ import annotations

import json
import queue
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

from . import __version__, RUN_SCHEMA, catalog, guard, params as params_mod
from .archive import Archive, now_iso, read_json, write_json
from .codesources import GUI_ROOT, CodeSourceRegistry, git_state

BOOTSTRAP = GUI_ROOT / "rnglab_gui" / "worker" / "bootstrap.py"


class LaunchError(ValueError):
    def __init__(self, message: str, errors: dict | None = None):
        super().__init__(message)
        self.errors = errors or {}


def cross_check(model_id: str, values: dict) -> dict[str, str]:
    """Constraints between parameters that the API itself would reject later."""
    errors = {}
    if model_id == "pimc-harmonic":
        if values["sweeps"] - values["burn_in"] < 32:
            errors["burn_in"] = "at least 32 retained sweeps are required (sweeps - burn_in >= 32)"
        if values["fault"] != "none" and values["fault_role"] == "centroid" and not values["centroid_refresh"]:
            errors["fault_role"] = "a centroid fault needs the centroid heat bath"
    if model_id == "ising-2d" and values["burn_in"] >= values["sweeps"]:
        errors["burn_in"] = "burn-in must be shorter than the run"
    if model_id in ("pimc-harmonic", "pi-mc", "decision-support") and values.get("fault") == "h1-word":
        if values["h1_family"] in ("SHIFT", "DUP") and not 0 <= values["h1_severity"] < 1:
            errors["h1_severity"] = "must lie in [0, 1)"
    if values.get("fault") == "degraded":
        p_one = 0.5 + values["bias_delta"]
        rho = values["rho"]
        if rho and not (0 <= p_one + rho * (1 - p_one) <= 1 and 0 <= p_one * (1 - rho) <= 1):
            errors["rho"] = "rho and the bit probability 0.5 + bias are incompatible (DegradedFaultSource)"
    return errors


class RunHandle:
    def __init__(self, run_id: str, path: Path, config: dict, mode: str):
        self.run_id = run_id
        self.path = path
        self.config = config
        self.mode = mode
        self.process: subprocess.Popen | None = None
        self.state = "starting"
        self.progress: float | None = None
        self.sim: dict | None = None
        self.controller_state = "running"
        self.digest: str | None = None
        self.summary: dict | None = None
        self.integrity: list | None = None
        self.result: dict | None = None
        self.error: str | None = None
        self.last_seq = 0
        self.subscribers: list[queue.Queue] = []
        self.lock = threading.Lock()
        self.done = threading.Event()
        self.started = now_iso()
        self.ended: str | None = None

    # ---------------------------------------------------------------- process
    def start(self, interpreter: str) -> None:
        config_path = self.path / "config.json"
        stderr = open(self.path / ("stderr.log" if self.mode == "observe" else "stderr_direct.log"), "wb")
        try:
            self.process = subprocess.Popen(
                [interpreter, "-I", str(BOOTSTRAP), str(config_path), self.mode],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=stderr, cwd=str(self.path),
            )
        except OSError as error:  # e.g. the interpreter no longer exists: free the slot, report the run failed
            self.state, self.error, self.ended = "failed", f"could not start worker: {error}", now_iso()
            self.done.set()
            try:
                self._write_status()
            except OSError:
                pass
            raise LaunchError(f"could not start worker: {error}") from None
        finally:
            stderr.close()
        self.state = "running"
        self._write_status()
        threading.Thread(target=self._read, name=f"run-{self.run_id}", daemon=True).start()

    def _read(self) -> None:
        name = "telemetry.jsonl" if self.mode == "observe" else "telemetry_direct.jsonl"
        assert self.process is not None and self.process.stdout is not None
        try:
            with open(self.path / name, "ab") as sink:
                for raw in self.process.stdout:
                    sink.write(raw)
                    sink.flush()
                    try:
                        message = json.loads(raw)
                    except ValueError:
                        continue
                    self._absorb(message)
                    self._broadcast(raw.decode(errors="replace").rstrip("\n"))
        except Exception as error:  # a storage or message failure must not leave an unreaped worker
            with self.lock:
                self.error = f"laboratory reader failed: {error!r}"
                self.state = "failed"
            try:
                self.process.kill()
            except OSError:
                pass
        try:
            code = self.process.wait()
            with self.lock:
                if self.state in ("running", "starting"):
                    self.state = "failed" if code else "ended"
                self.ended = now_iso()
            self._write_status(exit_code=code)
            self._broadcast(json.dumps({"t": "closed", "state": self.state, "exit_code": code}))
        finally:
            self.done.set()  # a failing status write must never leave the run counted as active

    def _absorb(self, message: dict) -> None:
        kind = message.get("t")
        with self.lock:
            self.last_seq = max(self.last_seq, int(message.get("seq", 0)))
            if kind == "frame":
                self.progress = message.get("progress")
                self.sim = message.get("sim")
            elif kind == "status":
                self.controller_state = message.get("state", self.controller_state)
            elif kind == "result":
                data = message.get("data", {})
                self.digest = data.get("digest")
                self.summary = data.get("summary")
                self.result = data
                write_json(self.path / ("result.json" if self.mode == "observe" else "result_direct.json"), data)
            elif kind == "integrity":
                self.integrity = message.get("checks", [])
                write_json(self.path / "integrity.json", self.integrity)
            elif kind == "error":
                self.error = message.get("message")
            elif kind == "done":
                self.state = {"completed": "completed", "stopped": "stopped"}.get(message.get("status"), "failed")
        if kind in ("result", "integrity", "done", "status"):
            self._write_status()

    def _write_status(self, exit_code: int | None = None) -> None:
        if self.mode != "observe":
            return
        states: dict[str, int] = {}
        for item in self.integrity or []:
            states[item["state"]] = states.get(item["state"], 0) + 1
        previous = read_json(self.path / "status.json", {}) or {}
        write_json(self.path / "status.json", {
            "state": self.state, "controller": self.controller_state, "started": self.started, "ended": self.ended,
            "exit_code": exit_code if exit_code is not None else previous.get("exit_code"),
            "digest": self.digest, "summary": self.summary, "integrity_states": states or None,
            "progress": self.progress, "sim": self.sim, "error": self.error,
        })

    # ---------------------------------------------------------------- control and fan-out
    def control(self, message: dict) -> None:
        if self.process is None or self.process.poll() is not None or self.process.stdin is None:
            raise LaunchError("the run is not running")
        line = (json.dumps(message) + "\n").encode()
        try:
            self.process.stdin.write(line)
            self.process.stdin.flush()
        except (BrokenPipeError, OSError) as error:
            raise LaunchError(f"control channel closed: {error}")

    def subscribe(self) -> queue.Queue:
        q: queue.Queue = queue.Queue(maxsize=20000)
        with self.lock:
            self.subscribers.append(q)
        return q

    def unsubscribe(self, q: queue.Queue) -> None:
        with self.lock:
            if q in self.subscribers:
                self.subscribers.remove(q)

    def _broadcast(self, line: str) -> None:
        with self.lock:
            subscribers = list(self.subscribers)
        for q in subscribers:
            try:
                q.put_nowait(line)
            except queue.Full:  # a stalled client loses live frames; it can reload from the archive
                pass

    def live(self) -> dict:
        with self.lock:
            return {"run_id": self.run_id, "state": self.state, "controller": self.controller_state,
                    "progress": self.progress, "sim": self.sim, "digest": self.digest, "error": self.error,
                    "model": self.config["model"], "last_seq": self.last_seq}


class RunManager:
    def __init__(self, archive: Archive, sources: CodeSourceRegistry, max_active: int = 6):
        self.archive = archive
        self.sources = sources
        self.max_active = max_active
        self.handles: dict[str, RunHandle] = {}
        self.direct: dict[str, RunHandle] = {}
        self.lock = threading.Lock()
        self.launch_lock = threading.RLock()
        self.verifying: set[str] = set()
        self.gui_state = git_state(GUI_ROOT.parent)

    def active(self) -> list[RunHandle]:
        with self.lock:
            return [h for h in self.handles.values() if not h.done.is_set()]

    def get(self, run_id: str) -> RunHandle | None:
        with self.lock:
            return self.handles.get(run_id)

    def build_config(self, model_id: str, supplied: dict, source_id: str | None) -> tuple[dict, Any]:
        model = catalog.get(model_id)
        try:
            values = params_mod.validate(model["params"], supplied)
        except params_mod.ParamError as error:
            raise LaunchError("invalid parameters", error.errors)
        errors = cross_check(model_id, values)
        if errors:
            raise LaunchError("inconsistent parameters", errors)
        source = self.sources.get(source_id)
        missing = source.available.get(model_id, [])
        if missing:
            raise LaunchError(f"code source {source.path} lacks {', '.join(missing)}")
        gen = values.get("generator")
        if gen and source.available.get(f"generator:{gen}"):
            raise LaunchError(f"generator {gen} is not available in {source.path}")
        if "namespace" in values:
            try:
                guard.check(values["namespace"], values["label"], source.path)
            except guard.NamespaceRefused as error:
                raise LaunchError(str(error), {"namespace": str(error)})
        state = git_state(source.path)
        sci, exe = params_mod.split(model["params"], values)
        config = {
            "schema": RUN_SCHEMA, "model": model_id, "model_title": model["title"], "call": model["call"],
            "params": values, "scientific_params": sci, "execution_params": exe,
            "code_source": {"id": source.id, "path": source.path, "branch": state["branch"], "head": state["head"],
                            "head_subject": state["head_subject"], "dirty_files": state["dirty_files"],
                            "dirty_science": state["dirty_science"], "status_lines": state["status_lines"]},
            "interpreter": source.interpreter,
            "laboratory": {"version": __version__, "head": self.gui_state.get("head"),
                           "dirty_files": self.gui_state.get("dirty_files")},
            "created": now_iso(),
        }
        return config, source

    def launch(self, model_id: str, supplied: dict, source_id: str | None = None, *, pair: bool = False,
               parent_run: str | None = None) -> list[str]:
        with self.launch_lock:  # check capacity and register handles atomically (concurrent launches)
            return self._launch(model_id, supplied, source_id, pair=pair, parent_run=parent_run)

    def _launch(self, model_id: str, supplied: dict, source_id: str | None, *, pair: bool,
                parent_run: str | None) -> list[str]:
        config, source = self.build_config(model_id, supplied, source_id)
        needed = 2 if pair else 1
        busy = len(self.active()) + sum(1 for h in self.direct.values() if not h.done.is_set())
        if busy + needed > self.max_active:
            raise LaunchError(f"at most {self.max_active} runs at a time (including verifications); "
                              f"{busy} active, {needed} requested")
        configs = [config]
        if pair:
            baseline = baseline_params(model_id, config["params"])
            if baseline is None:
                raise LaunchError("this configuration has no healthy baseline to pair with")
            base_config, _ = self.build_config(model_id, baseline, source_id)
            configs = [config, base_config]
        ids = []
        pair_id = None
        for index, cfg in enumerate(configs):
            run_id, path = self.archive.new_run(model_id)
            if pair:
                pair_id = pair_id or run_id
                cfg["pair_id"] = pair_id
                cfg["pair_role"] = "faulted" if index == 0 else "healthy baseline"
            if parent_run:
                cfg["parent_run"] = parent_run
            cfg["run_id"] = run_id
            cfg["run_dir"] = str(path)
            write_json(path / "config.json", cfg)
            handle = RunHandle(run_id, path, cfg, "observe")
            with self.lock:
                self.handles[run_id] = handle
            handle.start(source.interpreter)
            ids.append(run_id)
        return ids

    def restart(self, run_id: str) -> list[str]:
        config = read_json(self.archive.path(run_id) / "config.json")
        if not config:
            raise LaunchError("run has no configuration")
        return self.launch(config["model"], config["params"], config["code_source"]["id"], parent_run=run_id)

    def verify(self, run_id: str) -> dict:
        """Run the same configuration unobserved and compare the official result digests."""
        path = self.archive.path(run_id)
        config = read_json(path / "config.json")
        status = read_json(path / "status.json", {})
        if not config or not status.get("digest"):
            raise LaunchError("only a completed run with a result can be verified")
        with self.launch_lock:  # check and register under one lock: no two verifiers share a directory
            if run_id in self.verifying:  # held until finish() has written verification.json
                raise LaunchError("a verification of this run is already running")
            busy = len(self.active()) + sum(1 for h in self.direct.values() if not h.done.is_set())
            if busy + 1 > self.max_active:
                raise LaunchError(f"at most {self.max_active} runs at a time (including verifications)")
            # A result left by an earlier verification must never be read as this attempt's result.
            (path / "result_direct.json").unlink(missing_ok=True)
            handle = RunHandle(run_id, path, config, "direct")
            with self.lock:
                self.direct[run_id] = handle
            handle.start(config.get("interpreter") or sys.executable)  # raises LaunchError on failure
            self.verifying.add(run_id)
        write_json(path / "verification.json", {"state": "running", "started": now_iso()})

        def finish():
            try:
                _finish()
            finally:
                with self.launch_lock:
                    self.verifying.discard(run_id)

        def _finish():
            handle.done.wait()
            completed = handle.state == "completed"
            direct = (read_json(path / "result_direct.json", {}) or {}) if completed else {}
            same = completed and direct.get("digest") == status["digest"]
            code_now = git_state(config["code_source"]["path"])
            lab_now = git_state(GUI_ROOT.parent)
            lab_then = config.get("laboratory") or {}
            then = config["code_source"]
            # same-code replay only when every identity is known: science checkout at the same head and clean
            # then and now, and the laboratory checkout (which holds the worker adapters) unchanged and clean.
            code_match = (bool(then.get("head")) and code_now.get("head") == then.get("head")
                          and then.get("dirty_science") == 0 and code_now.get("dirty_science") == 0
                          and bool(lab_then.get("head")) and lab_now.get("head") == lab_then.get("head")
                          and lab_then.get("dirty_files") == 0 and lab_now.get("dirty_files") == 0)
            if not completed or not direct.get("digest"):
                state = "incomplete"
            elif not same:
                state = "failed"
            else:
                state = "ok" if code_match else "warning"
            write_json(path / "verification.json", {
                "state": state, "started": handle.started, "ended": now_iso(), "observed_digest": status["digest"],
                "direct_digest": direct.get("digest"), "same": same, "verifier_state": handle.state,
                "code_head_at_run": config["code_source"].get("head"),
                "code_head_now": code_now.get("head"), "code_dirty_now": code_now.get("dirty_files"),
                "code_match": code_match,
                "detail": ("an unobserved run of the same configuration in a fresh process gave the same "
                           "official result digest" + ("" if code_match else
                           " (but the code source changed since the run: not a same-code replay)")
                           if same else
                           "the unobserved run did not complete" if not completed else
                           "the unobserved run gave a different digest or none"),
            })

        threading.Thread(target=finish, daemon=True).start()
        return {"state": "running"}


def baseline_params(model_id: str, values: dict) -> dict | None:
    """The same configuration without the fault (same seeds): the healthy arm of a paired run."""
    out = dict(values)
    if model_id == "h1-detection":
        if values["family"] == "healthy":
            return None
        out["family"] = "healthy"
    elif "fault" in values and model_id != "decision-support":
        if values["fault"] == "none":
            return None
        out["fault"] = "none"
    else:
        return None
    out["start_paused"] = values.get("start_paused", False)
    return out
