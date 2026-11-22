"""Shared helpers for the laboratory tests.

Workers run as real child processes (``python -I bootstrap.py``) against this
checkout, exactly as the server starts them. Every sampling test uses the
namespace ``rnglab-gui-test``; study and reserved namespaces are only ever read
from their protocol files, never written here.
"""
from __future__ import annotations

import json
import subprocess
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
GUI = ROOT / "gui"
if str(GUI) not in sys.path:
    sys.path.insert(0, str(GUI))

from rnglab_gui import catalog, params  # noqa: E402
from rnglab_gui.codesources import git_state  # noqa: E402

TEST_NAMESPACE = "rnglab-gui-test"
BOOTSTRAP = GUI / "rnglab_gui" / "worker" / "bootstrap.py"


def make_config(model: str, overrides: dict, run_dir: Path) -> dict:
    values = dict(overrides)
    names = {p.name for p in catalog.get(model)["params"]}
    if "namespace" in names:
        values.setdefault("namespace", TEST_NAMESPACE)
    values.setdefault("frame_ms", 50) if "frame_ms" in names else None
    validated = params.validate(catalog.get(model)["params"], values)
    state = git_state(ROOT)
    return {"model": model, "run_id": "test", "params": validated, "run_dir": str(run_dir),
            "code_source": {"path": str(ROOT), "head": state["head"], "dirty_files": state["dirty_files"],
                            "dirty_science": state["dirty_science"]}}


class WorkerRun:
    """A worker process with an open control channel; messages are collected as they arrive."""

    def __init__(self, model: str, overrides: dict, run_dir: Path, mode: str = "observe"):
        run_dir.mkdir(parents=True, exist_ok=True)
        self.config = make_config(model, overrides, run_dir)
        path = run_dir / "config.json"
        path.write_text(json.dumps(self.config))
        self.proc = subprocess.Popen([sys.executable, "-I", str(BOOTSTRAP), str(path), mode],
                                     stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.messages: list[dict] = []
        self.lock = threading.Lock()
        self.reader = threading.Thread(target=self._read, daemon=True)
        self.reader.start()

    def _read(self):
        for line in self.proc.stdout:
            try:
                message = json.loads(line)
            except ValueError:
                continue
            with self.lock:
                self.messages.append(message)

    def send(self, **command):
        self.proc.stdin.write((json.dumps(command) + "\n").encode())
        self.proc.stdin.flush()

    def wait_for(self, predicate, timeout: float = 60.0):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            with self.lock:
                found = [m for m in self.messages if predicate(m)]
            if found:
                return found[-1]
            if self.proc.poll() is not None and not self.reader.is_alive():
                break
            time.sleep(0.02)
        with self.lock:
            found = [m for m in self.messages if predicate(m)]
        return found[-1] if found else None

    def finish(self, timeout: float = 300.0) -> list[dict]:
        done = self.wait_for(lambda m: m["t"] == "done", timeout)
        try:
            self.proc.stdin.close()
        except OSError:
            pass
        self.proc.wait(timeout=30)
        self.reader.join(timeout=10)
        assert done is not None, self.proc.stderr.read().decode()[-3000:]
        return self.messages

    def first(self, kind: str):
        return next((m for m in self.messages if m["t"] == kind), None)


def run_worker(model: str, overrides: dict, run_dir: Path, mode: str = "observe") -> list[dict]:
    return WorkerRun(model, overrides, run_dir, mode).finish()


def by_type(messages: list[dict], kind: str) -> list[dict]:
    return [m for m in messages if m["t"] == kind]
