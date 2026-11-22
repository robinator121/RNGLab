"""Worker runtime: builds sources, observes, runs one adapter and reports.

Two modes share one composition of sources and one scientific call:

- ``observe``: taps on every source, telemetry frames, pause/step/speed/stop;
- ``direct``: no tap, no observer, no controller. It exists to check that
  observing a run does not change it: the official result digest of an
  observed run must equal the digest of the same configuration run directly.
"""
from __future__ import annotations

import hashlib
import importlib
import importlib.util
import platform
import sys
import time
import traceback
from pathlib import Path
from typing import Any, Callable

import numpy as np

from .. import guard
from ..jsonsafe import result_digest, to_jsonable
from .control import Controller, NullController, RunStopped, start_command_reader
from .tap import observed_class
from .telemetry import Emitter


class FrameProbe:
    """Read-only access to the local variables of a running scientific function.

    The probe finds the function's frame by its code object while the function
    is running (from inside a random-number read it made) and reads names from
    the frame's locals mapping. It never writes. Values that are mutable
    (lists, arrays) are copied before they leave the probe.
    """

    def __init__(self, code):
        self.code = code
        self.frame = None
        self.locals = None

    def attach(self, depth: int = 2) -> bool:
        if self.locals is not None:
            return True
        frame = sys._getframe(depth)
        while frame is not None:
            if frame.f_code is self.code:
                self.frame = frame
                self.locals = frame.f_locals
                return True
            frame = frame.f_back
        return False

    def get(self, name: str, default: Any = None) -> Any:
        if self.locals is None:
            return default
        try:
            return self.locals[name]
        except KeyError:
            return default

    def detach(self) -> None:
        self.frame = None
        self.locals = None


def check(name: str, state: str, detail: str, **data: Any) -> dict:
    return {"name": name, "state": state, "detail": detail, **({"data": data} if data else {})}


class Context:
    def __init__(self, cfg: dict, mode: str, emitter: Emitter, controller: Controller):
        self.cfg = cfg
        self.params: dict = cfg["params"]
        self.model_id: str = cfg["model"]
        self.mode = mode
        self.observed = mode == "observe"
        self.emitter = emitter
        self.controller = controller
        self.code_root = Path(cfg["code_source"]["path"])
        self.run_dir = Path(cfg["run_dir"]) if cfg.get("run_dir") else None
        self.taps: list = []
        self.retired: list[dict] = []   # transparency results of taps no longer observed
        self.source_log: list[dict] = []
        self.checks: list[dict] = []
        from rnglab14.sources.base import RandomSource
        self.RandomSource = RandomSource
        self.ObservedSource = observed_class(RandomSource)

    # ---------------------------------------------------------------- seeds and sources
    def seed_label(self, role: str) -> str:
        return "/".join([self.params["namespace"], self.params["label"], role])

    def seed(self, role: str) -> int:
        from rnglab.seeds import namespace_seed
        return namespace_seed(self.params["namespace"], self.params["label"], role)

    def generator(self, role: str, kind: str | None = None, seed: int | None = None):
        kind = kind or self.params["generator"]
        seed = self.seed(role) if seed is None else seed
        label = self.seed_label(role)
        if kind == "numpy-pcg64":
            from rnglab.nuclear.streams import NumpyPCG64Source
            source = NumpyPCG64Source(seed, label=label)
        elif kind == "pcg64":
            from rnglab14.sources.reference import PCG64Source
            source = PCG64Source(seed)
        elif kind == "xoshiro256pp":
            from rnglab14.sources.reference import Xoshiro256PlusPlusSource
            source = Xoshiro256PlusPlusSource(seed)
        elif kind == "mt19937":
            from rnglab14.sources.reference import MersenneTwisterSource
            source = MersenneTwisterSource(seed)
        elif kind == "chacha20":
            from rnglab14.sources.reference import ChaCha20Source
            source = ChaCha20Source(key=hashlib.sha256(label.encode()).digest())
        elif kind == "os-entropy":
            from rnglab14.sources.reference import OSEntropySource
            source = OSEntropySource()
        else:
            raise ValueError(f"unknown generator {kind}")
        self.source_log.append({"role": role, "generator": kind, "seed": None if kind == "os-entropy" else seed,
                                "seed_label": label, "source_id": source.manifest.source_id,
                                "deterministic": bool(source.manifest.is_deterministic),
                                "seed_derivation": ("ChaCha20 key = SHA-256(seed label)" if kind == "chacha20" else
                                                    "none (OS entropy)" if kind == "os-entropy" else
                                                    "rnglab.seeds.namespace_seed(namespace, label, role)")})
        return source

    def degraded(self, base):
        from rnglab.faults import DegradedFaultSource
        p = self.params
        stuck = None if p["stuck_bit"] == "none" else int(p["stuck_bit"])
        return DegradedFaultSource(base, bias_delta=p["bias_delta"], autocorrelation_rho=p["rho"],
                                   stuck_bit_index=stuck, stuck_bit_value=int(p["stuck_value"]),
                                   periodic_coupling_freq=p["periodic_freq"], periodic_coupling_amp=p["periodic_amp"])

    def h1lib(self):
        path = self.code_root / "research/v018-h1-detection/h1lib.py"
        if "rnglab_gui_h1lib" in sys.modules:
            return sys.modules["rnglab_gui_h1lib"]
        spec = importlib.util.spec_from_file_location("rnglab_gui_h1lib", path)
        module = importlib.util.module_from_spec(spec)
        sys.modules["rnglab_gui_h1lib"] = module
        spec.loader.exec_module(module)
        return module

    def h1_word_fault(self, base, role: str):
        p = self.params
        lib = self.h1lib()
        family = p["h1_family"]
        severity = tuple(int(v) for v in p["gfsr_pair"].split(",")) if family == "GFSR" else p["h1_severity"]
        fault_seed = self.seed(f"{role}/fault")
        gfsr_seed = self.seed(f"{role}/gfsr")
        self.source_log.append({"role": f"{role}/fault", "adapter": "h1lib.FaultyWords", "family": family,
                                "severity": severity, "onset_bytes": p["onset_bytes"], "fault_seed": fault_seed,
                                "gfsr_seed": gfsr_seed})
        return lib.FaultyWords(base, family, severity, fault_seed, gfsr_seed, onset_bytes=p["onset_bytes"])

    def tap(self, source, role: str, on_read: Callable | None = None):
        if not self.observed:
            return source
        tapped = self.ObservedSource(source, role, on_read)
        self.taps.append(tapped)
        return tapped

    def retire(self, tapped) -> None:
        """Stop reporting a tap's stream (many short-lived sources); keep its transparency result."""
        if tapped in self.taps:
            self.taps.remove(tapped)
            self.retired.append(tapped.transparency())

    def source_for(self, role: str, faulted: bool = False, on_read: Callable | None = None, kind: str | None = None,
                   fault: str | None = None, observe: bool = True):
        """generator -> [tap 'role/base' -> fault adapter] -> tap 'role'. Direct mode: same without taps.

        observe=False composes the same sources without any tap, for engines that record
        their source's class in the result (rnglab.nuclear.aggregate records a replay identity).
        """
        tap = self.tap if observe else (lambda source, role_, on_read_=None: source)
        base = self.generator(role, kind)
        fault = fault if fault is not None else self.params.get("fault", "none")
        if faulted and fault != "none":
            base = tap(base, f"{role}/base")
            if fault == "degraded":
                adapter = self.degraded(base)
                self.source_log.append({"role": f"{role}/fault", "adapter": "rnglab.faults.DegradedFaultSource",
                                        "manifest": adapter.manifest.to_dict()})
            elif fault == "h1-word":
                adapter = self.h1_word_fault(base, role)
            else:
                raise ValueError(f"unknown fault {fault}")
            return tap(adapter, role, on_read)
        return tap(base, role, on_read)

    # ---------------------------------------------------------------- telemetry
    def meta(self, **data: Any) -> None:
        if self.observed:
            self.emitter.send("meta", data=data)

    def frame(self, sim: dict, data: dict, progress: float | None = None) -> None:
        if self.observed:
            self.emitter.frame(sim, data, progress)

    def due(self) -> bool:
        return self.observed and self.emitter.due()

    def event(self, kind: str, sim: dict, **data: Any) -> None:
        if self.observed:
            self.emitter.send("event", kind=kind, sim=sim, data=data)

    def boundary(self, step: int, flush: Callable[[], None] | None = None) -> None:
        self.controller.boundary(step, flush)

    def stream_data(self) -> dict:
        """Summaries of every tapped stream, keyed by role."""
        return {tap.role: tap.stats.summary() for tap in self.taps}

    # ---------------------------------------------------------------- end of run
    def finish(self, outcome: dict) -> dict:
        payload = outcome["payload"]
        arrays: dict[str, np.ndarray] = {}
        jsonable = to_jsonable(payload, arrays=arrays, path="")
        digest_value, volatile = result_digest(jsonable)
        checks = list(outcome.get("checks", []))
        results = self.retired + [tap.transparency() for tap in self.taps]
        if len(results) > 8:
            bad = [t for t in results if t["state"] != "ok"]
            checks.append(check("tap transparency", "failed" if bad else "ok",
                                f"{len(results) - len(bad)} of {len(results)} taps forwarded exactly the source's bytes "
                                "(byte count and SHA-256)", failed_roles=[t["role"] for t in bad][:20],
                                bytes=sum(t["tap_bytes"] for t in results)))
        else:
            for t in results:
                checks.append(check(f"tap transparency: {t['role']}", t["state"], t["detail"], **{
                    k: t[k] for k in ("tap_bytes", "source_bytes_delta", "tap_sha256", "source_sha256")}))
        nondeterministic = [s for s in self.source_log if s.get("deterministic") is False]
        if nondeterministic:
            checks.append(check("replayable sources", "warning",
                                "a non-deterministic source was used; this run cannot be reproduced",
                                roles=[s["role"] for s in nondeterministic]))
        else:
            checks.append(check("replayable sources", "ok", "every source is a seeded deterministic generator"))
        code = self.cfg["code_source"]
        dirty = code.get("dirty_science", code.get("dirty_files"))
        if dirty:
            checks.append(check("code source state", "warning",
                                f"{dirty} uncommitted or untracked files under src/ or research/ of the code source; "
                                "the commit alone does not reproduce this run", head=code.get("head")))
        elif dirty == 0:
            other = code.get("dirty_files") or 0
            checks.append(check("code source state", "ok",
                                "src/ and research/ are clean at the recorded commit" +
                                (f" ({other} changed files elsewhere, e.g. the laboratory itself)" if other else ""),
                                head=code.get("head")))
        else:
            checks.append(check("code source state", "unavailable", "Git state could not be read"))
        if self.observed and self.controller.pauses:
            checks.append(check("wall-clock timing", "warning",
                                f"paused {self.controller.pauses} time(s), {self.controller.paused_seconds:.1f} s; "
                                "wall-clock durations include the pauses (simulation results do not depend on them)"))
        if self.run_dir is not None and arrays:
            np.savez_compressed(self.run_dir / ("arrays.npz" if self.observed else "arrays_direct.npz"), **arrays)
        out = {"payload": jsonable, "digest": digest_value, "volatile_excluded": volatile,
               "summary": outcome.get("summary", {}),
               "arrays": sorted(arrays), "sources": self.source_log}
        if self.observed:
            self.emitter.send("result", data=out)
            self.emitter.send("integrity", checks=checks)
        else:
            self.emitter.send("result", data={"digest": digest_value, "volatile_excluded": volatile,
                                              "summary": outcome.get("summary", {}),
                                              "arrays": sorted(arrays), "payload": jsonable})
        return out


def _adapter(model_id: str):
    module = model_id.replace("-", "_")
    return importlib.import_module(f"rnglab_gui.worker.adapters.{module}")


def run(cfg: dict, mode: str = "observe") -> int:
    params = cfg["params"]
    emitter = Emitter(frame_interval_s=params.get("frame_ms", 100) / 1000.0)
    if mode == "observe":
        controller = Controller(lambda status: emitter.send("status", **status),
                                start_paused=params.get("start_paused", False),
                                delay_s=params.get("delay_ms", 0.0) / 1000.0)
        start_command_reader(controller)
    else:
        controller = NullController()
    import numpy
    emitter.send("hello", mode=mode, model=cfg["model"], run_id=cfg.get("run_id"), python=sys.version,
                 implementation=platform.python_implementation(), numpy=numpy.__version__,
                 sys_path_head=sys.path[:2], executable=sys.executable)
    started = time.time()
    try:
        if "namespace" in params:
            guard.check(params["namespace"], params.get("label", ""), cfg["code_source"]["path"])
        adapter = _adapter(cfg["model"])
        ctx = Context(cfg, mode, emitter, controller)
        outcome = adapter.run(ctx)
        ctx.finish(outcome)
        emitter.send("done", status="completed", elapsed_wall=time.time() - started, frames=emitter.frames)
        return 0
    except RunStopped:
        emitter.send("done", status="stopped", elapsed_wall=time.time() - started, frames=emitter.frames,
                     at_step=controller.last_step)
        return 0
    except Exception as error:  # reported to the interface, then the run is marked failed
        emitter.send("error", message=f"{type(error).__name__}: {error}", traceback=traceback.format_exc())
        emitter.send("done", status="failed", elapsed_wall=time.time() - started, frames=emitter.frames)
        return 1
