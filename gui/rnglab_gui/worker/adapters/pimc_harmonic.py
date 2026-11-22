"""Harmonic PIMC through the package API, observed between random draws.

The official call is PathIntegralHarmonicOscillator.run_simulation with
record_trace=True (the option only stores copies; it changes neither the
transition nor the consumption). At each sweep boundary the observer reads,
without writing, the sampler's own state: the path array, the accepted and
proposed counters and the lists of retained energies. The live interval is
rnglab12.uncertainty.summarize_series applied to the retained prefix.
"""
from __future__ import annotations

import time

import numpy as np

from ..runtime import FrameProbe, check

STREAM_EVERY_S = 1.0
CI_EVERY_S = 0.25
MAX_LIVE_POINTS = 20000


class SweepObserver:
    """Detects sweep boundaries from the running sampler's own ``sweep`` variable."""

    def __init__(self, ctx, model, code, sweeps: int, burn_in: int, batch: int, reference: float, unit_total: int):
        self.ctx = ctx
        self.model = model
        self.probe = FrameProbe(code)
        self.sweeps = sweeps
        self.burn_in = burn_in
        self.batch = batch
        self.reference = reference
        self.total = unit_total
        self.current = None
        self.completed = -1
        self.sent = 0
        retained = sweeps - burn_in
        self.stride = max(1, retained // MAX_LIVE_POINTS)
        self.sent_cv: list[float] = []
        self.sent_index: list[int] = []
        self.last_ci = -1e9
        self.last_stream = -1e9
        self.ci_points: list[dict] = []
        self.acc_x: list[int] = []
        self.acc_y: list[float] = []
        self.extra_frame = None  # adapters may add data to every frame: f(data, final)
        self.on_step = None      # adapters may act at every sweep boundary: f(sweep)

    def on_read(self, tap, raw: bytes) -> None:
        if not self.probe.attach():
            return
        sweep = self.probe.get("sweep")
        if sweep is None or sweep == self.current:
            return
        previous, self.current = self.current, sweep
        if previous is not None:
            self.completed = previous
            self.step_done(previous)

    def step_done(self, sweep: int) -> None:
        accepted = self.probe.get("accepted")
        proposals = self.probe.get("total_proposals")
        if proposals:
            self.acc_x.append(sweep)
            self.acc_y.append(accepted / proposals)
        if self.on_step is not None:
            self.on_step(sweep)
        if self.ctx.due():
            self.emit()
        self.ctx.boundary(sweep, self.emit)

    def _ci(self, values: list[float], x: int, force: bool = False) -> None:
        from rnglab12.uncertainty import summarize_series
        now = time.monotonic()
        if len(values) < 32 or (not force and now - self.last_ci < CI_EVERY_S):
            return
        self.last_ci = now
        try:
            s = summarize_series(np.asarray(values, dtype=float), reference=self.reference,
                                 batch_sizes=(self.batch,))
        except ValueError:
            return
        bm = s["batch_means"][str(self.batch)]
        g = s["geyer"]
        self.ci_points.append({
            "x": x, "mean": s["mean"], "n": s["n"],
            "lo": bm["interval_95"][0] if bm["interval_95"] else None,
            "hi": bm["interval_95"][1] if bm["interval_95"] else None, "status": bm["status"],
            "glo": g["interval_95"][0] if g.get("interval_95") else None,
            "ghi": g["interval_95"][1] if g.get("interval_95") else None, "gstatus": g["status"],
        })

    def emit(self, final: dict | None = None) -> None:
        """One frame from the running sampler (or, with ``final``, from the official trace)."""
        data: dict = {"series": {}, "state": {}, "scalars": {}}
        if final is None:
            cv_all = self.probe.get("virial_energies") or []
            td_all = self.probe.get("thermo_energies") or []
            rg_all = self.probe.get("rg_vals") or []
            cent_all = self.probe.get("trace_centroid")
            n = len(cv_all)
            path = np.array(self.model.path, copy=True)
            sweep_now = self.completed
        else:
            trace = final["trace"]
            cv_all, td_all = trace["CV"], trace["TD"]
            rg_all = np.sqrt(np.maximum(trace["rg2"], 0.0))
            cent_all = trace["centroid"]
            n = len(cv_all)
            path = np.array(trace["paths"][-1], copy=True)
            sweep_now = self.sweeps - 1
        start = self.sent
        idx = list(range(start + ((-start) % self.stride), n, self.stride))
        if idx:
            cv_new = [float(cv_all[i]) for i in idx]
            td_new = [float(td_all[i]) for i in idx]
            x_new = [self.burn_in + i for i in idx]
            series = data["series"]
            series["cv"] = {"x": x_new, "y": cv_new}
            series["d"] = {"x": x_new, "y": [t - c for t, c in zip(td_new, cv_new)]}
            series["rg"] = {"x": x_new, "y": [float(rg_all[i]) for i in idx]}
            if cent_all is not None:
                series["centroid"] = {"x": x_new, "y": [float(cent_all[i]) for i in idx]}
            self.sent_cv.extend(cv_new)
            self.sent_index.extend(idx)
        self.sent = n
        if self.acc_x:
            data["series"]["acc"] = {"x": self.acc_x, "y": self.acc_y}
            self.acc_x, self.acc_y = [], []
        if n:
            values = list(cv_all[:n]) if final is None else cv_all
            self._ci(values, self.burn_in + n - 1, force=final is not None)
        if final is not None and final.get("official_interval"):
            oi = final["official_interval"]
            self.ci_points.append({"x": self.sweeps - 1, "mean": oi["mean"], "lo": oi["lo"], "hi": oi["hi"],
                                   "status": oi["status"], "official": True})
        if self.ci_points:
            data["series"]["ci"] = self.ci_points
            self.ci_points = []
        data["state"]["path"] = path.tolist()
        proposals = self.probe.get("total_proposals") if final is None else None
        accepted = self.probe.get("accepted") if final is None else None
        data["scalars"].update({"sweep": sweep_now, "retained": n,
                                "acceptance": (accepted / proposals) if proposals else
                                (final or {}).get("acceptance_rate"),
                                "cv_mean": float(np.mean(cv_all[:n])) if n else None})
        if self.extra_frame is not None:
            self.extra_frame(data, final)
        now = time.monotonic()
        if now - self.last_stream >= STREAM_EVERY_S or final is not None:
            data["streams"] = self.ctx.stream_data()
            self.last_stream = now
        done = sweep_now + 1
        self.ctx.frame({"unit": "sweep", "value": done, "total": self.total}, data,
                       progress=done / self.total if self.total else None)


def compose(ctx, observer_hook):
    """Sources exactly as configured; identical in observed and direct mode."""
    from rnglab14.sources.base import StochasticChannels
    p = ctx.params
    role = p["fault_role"]
    if p["channels"] == "shared":
        source = ctx.source_for("local", faulted=(role != "centroid"), on_read=observer_hook)
    else:
        source = StochasticChannels(
            proposal=ctx.source_for("proposal", faulted=(role == "proposal"), on_read=observer_hook),
            acceptance=ctx.source_for("acceptance", faulted=(role == "acceptance"), on_read=observer_hook),
            initialization=ctx.source_for("initialization", faulted=(role == "initialization"), on_read=observer_hook),
        )
    centroid = (ctx.source_for("centroid", faulted=(role == "centroid"), on_read=observer_hook)
                if p["centroid_refresh"] else None)
    return source, centroid


def run(ctx) -> dict:
    from rnglab14.models.pimc_harmonic import PathIntegralHarmonicOscillator, exact_finite_bead_energy

    p = ctx.params
    if p["fault"] != "none" and p["fault_role"] == "centroid" and not p["centroid_refresh"]:
        raise ValueError("a centroid fault needs the centroid heat bath")
    model = PathIntegralHarmonicOscillator(beads=p["beads"], temperature=1.0 / p["beta"],
                                           displacement_delta=p["delta"])
    reference = exact_finite_bead_energy(model.M, model.beta)
    observer = None
    if ctx.observed:
        observer = SweepObserver(ctx, model, PathIntegralHarmonicOscillator.run_simulation.__code__, p["sweeps"],
                                 p["burn_in"], p["batch_size"], reference, p["sweeps"])
    source, centroid = compose(ctx, observer.on_read if observer else None)
    ctx.meta(unit="sweep", total=p["sweeps"], burn_in=p["burn_in"], beads=model.M, beta=model.beta,
             temperature=model.T, delta=model.delta, batch_size=p["batch_size"],
             references={"finite_bead_energy": reference, "continuum_energy": model.exact_energy},
             live_stride=observer.stride if observer else 1, roles=[t.role for t in ctx.taps],
             call="PathIntegralHarmonicOscillator(beads, 1/beta, delta).run_simulation(..., record_trace=True)")
    result = model.run_simulation(source, sweeps=p["sweeps"], burn_in=p["burn_in"], centroid_source=centroid,
                                  initialization=p["initialization"], uncertainty_batch_sizes=(p["batch_size"],),
                                  record_trace=True)
    bm = result.virial_uncertainty["batch_means"][str(p["batch_size"])]
    checks = []
    if observer is not None:
        observer.probe.detach()
        official = None
        if bm.get("interval_95"):
            official = {"mean": bm["prefix_mean"], "lo": bm["interval_95"][0], "hi": bm["interval_95"][1],
                        "status": bm["status"]}
        observer.emit(final={"trace": result.trace, "official_interval": official,
                             "acceptance_rate": result.acceptance_rate})
        expected = result.trace["CV"][observer.sent_index] if observer.sent_index else np.zeros(0)
        same = np.array_equal(np.asarray(observer.sent_cv), expected)
        checks.append(check("live energies vs official trace", "ok" if same else "failed",
                            ("every streamed centroid-virial value equals the official trace value at its sweep"
                             if same else "streamed values differ from the official trace"),
                            compared=len(observer.sent_cv), stride=observer.stride))
    summary = {"cv_mean": result.virial_energy_mean, "finite_bead_energy": reference,
               "bm_interval": bm.get("interval_95"), "bm_status": bm.get("status"), "covered": bm.get("covered"),
               "acceptance_rate": result.acceptance_rate, "sampler": result.sampler_protocol}
    payload = {"call": "rnglab14.models.pimc_harmonic.PathIntegralHarmonicOscillator.run_simulation",
               "model": {"beads": model.M, "temperature": model.T, "beta": model.beta, "delta": model.delta},
               "result": result}
    return {"payload": payload, "checks": checks, "summary": summary}
