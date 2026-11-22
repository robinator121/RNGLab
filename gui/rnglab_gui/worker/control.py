"""Execution control at step boundaries.

The scientific computation runs on the worker's main thread. Observers call
``Controller.boundary(step)`` when a step (sweep, sample, interval, chunk,
replicate) has completed: from inside a random-number read of the next step,
or from an engine callback. Pausing blocks there; nothing in the computation
changes while it waits, and no random number is read. Stopping raises
``RunStopped`` at the next boundary, which abandons the run without a result.

A delay per step slows the run for watching; it is wall-clock only.
Commands arrive as JSON lines on stdin from the server.
"""
from __future__ import annotations

import json
import sys
import threading
import time
from typing import Callable


class RunStopped(Exception):
    """Raised at a step boundary after a stop command."""


class Controller:
    def __init__(self, on_status: Callable[[dict], None], start_paused: bool = False, delay_s: float = 0.0):
        self._cv = threading.Condition()
        self.on_status = on_status
        self.paused = bool(start_paused)
        self.stop_requested = False
        self.step_budget: int | None = None
        self.delay_s = max(0.0, float(delay_s))
        self._debt = 0.0
        self.paused_seconds = 0.0
        self.pauses = 0
        self.last_step: int | None = None

    # ---- commands (control thread) ---------------------------------------------------------
    def handle(self, message: dict) -> None:
        cmd = message.get("cmd")
        with self._cv:
            if cmd == "pause":
                self.paused = True
                self.step_budget = None
            elif cmd == "resume":
                self.paused = False
                self.step_budget = None
            elif cmd == "step":
                n = int(message.get("n", 1))
                self.step_budget = max(1, n)
                self.paused = False
            elif cmd == "speed":
                self.delay_s = max(0.0, float(message.get("delay_ms", 0.0))) / 1000.0
            elif cmd == "stop":
                self.stop_requested = True
            else:
                return
            self._cv.notify_all()
        self.on_status({"state": self.state(), "command": cmd, "delay_ms": self.delay_s * 1000.0})

    def state(self) -> str:
        if self.stop_requested:
            return "stopping"
        if self.paused:
            return "paused"
        if self.step_budget is not None:
            return "stepping"
        return "running"

    # ---- simulation thread -----------------------------------------------------------------
    def boundary(self, step: int, flush: Callable[[], None] | None = None) -> None:
        """Called after step ``step`` completed. May block (pause) or raise RunStopped."""
        self.last_step = step
        if self.stop_requested:
            raise RunStopped()
        if self.delay_s > 0.0:
            self._debt += self.delay_s
            if self._debt >= 0.004:
                end = time.monotonic() + self._debt
                self._debt = 0.0
                while True:
                    remaining = end - time.monotonic()
                    if remaining <= 0 or self.stop_requested or self.paused:
                        break
                    time.sleep(min(remaining, 0.05))
        with self._cv:
            if self.step_budget is not None:
                self.step_budget -= 1
                if self.step_budget <= 0:
                    self.step_budget = None
                    self.paused = True
            if self.paused and not self.stop_requested:
                self._cv.release()
                try:
                    if flush is not None:
                        flush()
                    self.on_status({"state": "paused", "at_step": step})
                finally:
                    self._cv.acquire()
                t0 = time.monotonic()
                self.pauses += 1
                while self.paused and not self.stop_requested:
                    self._cv.wait(0.5)
                self.paused_seconds += time.monotonic() - t0
                if not self.stop_requested:
                    state = "stepping" if self.step_budget is not None else "running"
                    self._cv.release()
                    try:
                        self.on_status({"state": state, "at_step": step})
                    finally:
                        self._cv.acquire()
            if self.stop_requested:
                raise RunStopped()


class NullController(Controller):
    """Used by direct (unobserved) runs: never blocks, never delays."""

    def __init__(self):
        super().__init__(lambda status: None)

    def boundary(self, step: int, flush=None) -> None:
        return None


def start_command_reader(controller: Controller, stream=None) -> threading.Thread:
    stream = stream if stream is not None else sys.stdin

    def loop():
        for line in stream:
            line = line.strip()
            if not line:
                continue
            try:
                message = json.loads(line)
            except ValueError:
                continue
            if isinstance(message, dict):
                controller.handle(message)
        # stdin closed: the server went away; stop at the next boundary
        controller.handle({"cmd": "stop"})

    thread = threading.Thread(target=loop, name="rnglab-gui-control", daemon=True)
    thread.start()
    return thread
