"""2D Ising (random-site Metropolis) through Ising2D.run_simulation, observed between random draws.

At each sweep boundary the observer copies the lattice (``self.spins``) and reads
the sampler's own running energy, magnetisation and acceptance counters. For
L <= 4 the exact enumeration reference (rnglab16.core.enumeration) is shown.
"""
from __future__ import annotations

import time

import numpy as np

from ..runtime import FrameProbe, check

STREAM_EVERY_S = 1.0


class IsingObserver:
    def __init__(self, ctx, model, code, sweeps: int):
        self.ctx, self.model, self.sweeps = ctx, model, sweeps
        self.probe = FrameProbe(code)
        self.current = None
        self.completed = -1
        self.buf = {"e": ([], []), "absm": ([], []), "acc": ([], [])}
        self.energy_trace: list = []      # sampler's own retained energies (list object), read after return
        self.boundary_e: dict[int, float] = {}
        self.last_stream = -1e9

    def on_read(self, tap, raw: bytes) -> None:
        if not self.probe.attach():
            return
        sweep = self.probe.get("sweep")
        if sweep is None or sweep == self.current:
            return
        previous, self.current = self.current, sweep
        if previous is not None:
            self.step_done(previous)

    def step_done(self, sweep: int) -> None:
        self.completed = sweep
        n = self.model.N
        e, m = self.probe.get("current_E"), self.probe.get("current_M")
        acc, tot = self.probe.get("accepted_flips"), self.probe.get("total_proposals")
        self.energy_trace = self.probe.get("energies")
        self.boundary_e[sweep] = e
        for key, value in (("e", e / n), ("absm", abs(m) / n), ("acc", acc / tot if tot else None)):
            self.buf[key][0].append(sweep)
            self.buf[key][1].append(value)
        if self.ctx.due():
            self.emit()
        self.ctx.boundary(sweep, self.emit)

    def emit(self, final: dict | None = None) -> None:
        data = {"series": {k: {"x": v[0], "y": v[1]} for k, v in self.buf.items() if v[0]},
                "state": {"spins": np.array(self.model.spins, copy=True).astype(int).tolist()},
                "scalars": {"sweep": self.completed if final is None else self.sweeps - 1}}
        self.buf = {k: ([], []) for k in self.buf}
        if final is not None:
            data["scalars"].update(final)
        now = time.monotonic()
        if now - self.last_stream >= STREAM_EVERY_S or final is not None:
            data["streams"] = self.ctx.stream_data()
            self.last_stream = now
        done = (self.completed + 1) if final is None else self.sweeps
        self.ctx.frame({"unit": "sweep", "value": done, "total": self.sweeps}, data, progress=done / self.sweeps)


def run(ctx) -> dict:
    from rnglab14.models.ising_2d import Ising2D
    from rnglab14.sources.base import StochasticChannels

    p = ctx.params
    model = Ising2D(L=p["L"], temperature=p["temperature"])
    observer = IsingObserver(ctx, model, Ising2D.run_simulation.__code__, p["sweeps"]) if ctx.observed else None
    hook = observer.on_read if observer else None
    role = p["fault_role"]
    if p["channels"] == "shared":
        source = ctx.source_for("local", faulted=True, on_read=hook)
    else:
        source = StochasticChannels(proposal=ctx.source_for("proposal", faulted=(role == "proposal"), on_read=hook),
                                    acceptance=ctx.source_for("acceptance", faulted=(role == "acceptance"), on_read=hook),
                                    initialization=ctx.source_for("initialization", faulted=(role == "initialization"),
                                                                  on_read=hook))
    references = {}
    if p["L"] <= 4:
        from rnglab16.core import enumeration
        exact = enumeration(p["L"], p["temperature"])
        references = {"exact_energy": exact["energy"], "exact_abs_m": exact["abs_magnetization"],
                      "exact_heat_capacity": exact["heat_capacity"], "exact_susceptibility_signed": exact["susceptibility"]}
    ctx.meta(unit="sweep", total=p["sweeps"], burn_in=p["burn_in"], L=p["L"], temperature=p["temperature"],
             references=references, roles=[t.role for t in ctx.taps],
             call="Ising2D(L, T).run_simulation(source_or_channels, sweeps, burn_in)")
    result = model.run_simulation(source, sweeps=p["sweeps"], burn_in=p["burn_in"])
    checks = []
    if observer is not None:
        observer.probe.detach()
        trace = list(observer.energy_trace or [])
        observer.emit(final={"energy_mean": result.energy_mean, "magnetization_mean": result.magnetization_mean,
                             "acceptance_rate": result.acceptance_rate})
        # boundary values of retained sweeps must equal the sampler's own retained energies
        retained = [observer.boundary_e[s] for s in sorted(observer.boundary_e) if s >= p["burn_in"]]
        same = retained == trace[:len(retained)]
        checks.append(check("live energies vs sampler's list", "ok" if same else "failed",
                            "the energy read at every sweep boundary equals the sampler's own retained value"
                            if same else "boundary readings differ from the sampler's list", compared=len(retained)))
        if references:
            checks.append(check("exact reference", "ok", f"exact enumeration at L = {p['L']} available for comparison. "
                                "Note: the sampler's susceptibility uses Var(|m|); the exact value is the signed-m "
                                "susceptibility. They are different observables and are not compared.",
                                **references))
        else:
            checks.append(check("exact reference", "unavailable", "exact enumeration exists only for L <= 4"))
    summary = {"energy_mean": result.energy_mean, "magnetization_mean": result.magnetization_mean,
               "acceptance_rate": result.acceptance_rate, "specific_heat": result.specific_heat,
               "susceptibility_abs_m": result.susceptibility, **references}  # Var(|m|)-based; not the signed-m reference
    payload = {"call": "rnglab14.models.ising_2d.Ising2D.run_simulation",
               "model": {"L": p["L"], "temperature": p["temperature"]}, "result": result,
               "final_spins": np.array(model.spins, copy=True)}
    return {"payload": payload, "checks": checks, "summary": summary}
