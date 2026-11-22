"""Telemetry emitter: one JSON object per line on the worker's stdout.

Message types (schema rnglab-gui-telemetry-v1):

``hello``     worker identity, Python and NumPy versions, sys.path used
``meta``      run-specific constants: references, totals, scheme graphs, layouts
``frame``     sim position, wall clock, and data: appended series points,
              replaced state snapshots, histograms, matrices, tables
``event``     discrete occurrences on the simulation axis (alarm, onset, warning)
``status``    controller state changes (running, paused, stepping)
``result``    the official result of the scientific call (JSON-safe; large arrays
              written to arrays.npz in the run directory)
``integrity`` checks with evidence states ok / warning / failed / incomplete / unavailable
``error``     exception text and traceback
``done``      final status: completed, stopped or failed

Wall-clock time (seconds since the worker started) and simulation position are
separate fields of every frame and are never mixed.
"""
from __future__ import annotations

import sys
import threading
import time
from typing import Any

from ..jsonsafe import dumps_line
from .. import TELEMETRY_SCHEMA


class Emitter:
    def __init__(self, stream=None, frame_interval_s: float = 0.1):
        self.stream = stream if stream is not None else sys.stdout.buffer
        self.lock = threading.Lock()
        self.t0 = time.monotonic()
        self.seq = 0
        self.frame_interval = max(0.0, frame_interval_s)
        self._last_frame = -1e9
        self.frames = 0

    def wall(self) -> float:
        return time.monotonic() - self.t0

    def send(self, message_type: str, /, **fields: Any) -> None:
        with self.lock:
            self.seq += 1
            message = {"t": message_type, "seq": self.seq, "wall": round(self.wall(), 6), **fields}
            if message_type == "hello":
                message["schema"] = TELEMETRY_SCHEMA
            line = dumps_line(message) + "\n"
            self.stream.write(line.encode())
            self.stream.flush()

    def due(self) -> bool:
        return time.monotonic() - self._last_frame >= self.frame_interval

    def frame(self, sim: dict, data: dict, progress: float | None = None) -> None:
        self._last_frame = time.monotonic()
        self.frames += 1
        self.send("frame", sim=sim, progress=progress, data=data)
