"""One run of the H1 (Stage C) design, with its monitors computed live.

Observed mode composes the run exactly as research/v018-h1-detection/h1lib.run_one
does (FaultyWords on the local PCG64 source, SweepClock on the centroid source,
exact Gaussian start, the package sampler with record_trace), with transparent
taps, and computes the record with h1lib's own functions. The monitor windows
are evaluated as soon as their sweeps exist, with the frozen calibration
thresholds of results/gates.json. Direct mode calls h1lib.run_one itself; the
verification therefore compares the observed composition with the study's own
function, digest for digest.
"""
from __future__ import annotations

import hashlib
import json
import math

import numpy as np

from ..runtime import check
from .pimc_harmonic import SweepObserver

GATES = "research/v018-h1-detection/results/gates.json"


def severity_of(p: dict):
    if p["family"] == "GFSR":
        return tuple(int(v) for v in p["gfsr_pair"].split(","))
    if p["family"] == "healthy":
        return 0.0
    return p["severity"]


def record_from(lib, ns, label, family, severity, replica, sweeps, onset_bytes, seeds, local, clock, result):
    """h1lib.run_one's post-processing, statement for statement (checked against run_one in the tests)."""
    from rnglab12.uncertainty import summarize_series
    from rnglab11.statistics import HealthTests
    M = lib.M
    trace = result.trace
    marks = np.asarray(clock.marks, dtype=np.int64)
    stream = bytes(local.emitted)
    onset_sweep = int(np.searchsorted(marks, onset_bytes, side="right")) if marks[-1] > onset_bytes else None
    record = {"namespace": ns, "condition": label, "family": family, "severity": severity, "replica": replica,
              "seeds": seeds, "sweeps": sweeps, "local_bytes": local.consumed_bytes,
              "local_sha256": hashlib.sha256(stream).hexdigest(),
              "prefix_sha256": hashlib.sha256(stream[:onset_bytes]).hexdigest(),
              "onset_sweep": onset_sweep, "acceptance_rate_4dp": result.acceptance_rate,
              "start_sha256": result.initialization_protocol["initial_path_sha256"]}
    if sweeps >= lib.HARM_END:
        bm = summarize_series(trace["CV"][lib.HARM_START:lib.HARM_END], reference=lib.exact_finite_bead_energy(M, lib.BETA),
                              batch_sizes=(lib.HARM_BATCH,))["batch_means"][str(lib.HARM_BATCH)]
        record["harm"] = {"mean": bm["prefix_mean"], "mcse": bm["mcse"], "interval": bm["interval_95"],
                          "covered": bm["covered"], "status": bm["status"],
                          "wrong": not (bm["status"] == "ESTIMATED" and bm["covered"])}
    conv = []
    for end in range(lib.CONV_WINDOW, sweeps + 1, lib.STRIDE):
        lo, hi = lib.window_bytes(marks, end - lib.CONV_WINDOW, end)
        conv.append({"end": end, **lib.conventional_window(stream[lo:hi])})
    d = trace["TD"] - trace["CV"]
    per_sweep_bytes = np.diff(np.concatenate([[0], marks]))
    uphill = (per_sweep_bytes - lib.PROPOSAL_BYTES_PER_SWEEP) / (8 * M)
    app = [{"end": end, "mean_D": float(d[end - lib.APP_WINDOW:end].mean()),
            "uphill_fraction": float(uphill[end - lib.APP_WINDOW:end].mean()),
            "mean_rg2": float(trace["rg2"][end - lib.APP_WINDOW:end].mean())}
           for end in range(lib.APP_WINDOW, sweeps + 1, lib.STRIDE)]
    tests = HealthTests(min_entropy=7, alpha=2**-20, alphabet_size=256)
    symbols = np.frombuffer(stream, dtype=np.uint8)
    rct, apt = lib.health_test_events(symbols, tests)
    record["health_tests"] = {"rct_cutoff": tests.rct_cutoff, "apt_cutoff": tests.apt_cutoff, "window": tests.window,
                              "rct_pre": int(np.sum(rct <= onset_bytes)), "rct_post": int(np.sum(rct > onset_bytes)),
                              "apt_pre": int(np.sum(apt <= onset_bytes)), "apt_post": int(np.sum(apt > onset_bytes))}
    record["conventional"] = conv
    record["application"] = app
    record["per_sweep_bytes_ok"] = bool(np.all(per_sweep_bytes >= lib.PROPOSAL_BYTES_PER_SWEEP)
                                        and np.all((per_sweep_bytes - lib.PROPOSAL_BYTES_PER_SWEEP) % 8 == 0)
                                        and len(marks) == sweeps and np.all(np.diff(marks) > 0))
    arrays = {"CV": trace["CV"], "TD": trace["TD"], "rg2": trace["rg2"], "marks": marks}
    return record, arrays, (rct, apt)


class Monitors:
    """Monitor windows evaluated as soon as their sweeps exist (same functions as the record)."""

    def __init__(self, ctx, lib, observer, local, clock, gates, onset_bytes, sweeps):
        self.ctx, self.lib, self.obs, self.local, self.clock = ctx, lib, observer, local, clock
        self.tau = gates["thresholds"]
        self.constants = gates["application_constants"]
        self.onset_bytes, self.sweeps = onset_bytes, sweeps
        self.next_conv = lib.CONV_WINDOW
        self.next_app = lib.APP_WINDOW
        self.conv, self.app = [], []
        self.onset_sweep = None
        self.alarmed = {"C": False, "A": False}
        self.statC = ([], [])
        self.statA = ([], [])
        self.uphill = ([], [])
        self.pcols, self.pvals = [], []
        self.uphill_sent = 0

    def step(self, sweep: int) -> None:
        lib = self.lib
        done = sweep + 1  # sweeps 0..sweep are complete
        marks = self.clock.marks
        if self.onset_sweep is None and marks and marks[-1] > self.onset_bytes:
            self.onset_sweep = int(np.searchsorted(np.asarray(marks), self.onset_bytes, side="right"))
            self.ctx.event("onset", {"unit": "sweep", "value": self.onset_sweep}, bytes=self.onset_bytes)
        while self.next_conv <= done:
            end = self.next_conv
            lo, hi = lib.window_bytes(np.asarray(marks, dtype=np.int64), end - lib.CONV_WINDOW, end)
            window = {"end": end, **lib.conventional_window(bytes(self.local.emitted[lo:hi]))}
            self.conv.append(window)
            stat = lib.conventional_statistic(window)
            self.statC[0].append(end)
            self.statC[1].append(stat)
            self.pcols.append(end)
            self.pvals.append([None if window[k] is None else -math.log10(max(window[k], 1e-300)) for k in lib.CONV_P_KEYS])
            self._alarm("C", end, stat)
            self.next_conv += lib.STRIDE
        while self.next_app <= done:
            end = self.next_app
            td = self.obs.probe.get("thermo_energies")
            cv = self.obs.probe.get("virial_energies")
            rg2 = np.asarray(self.obs.probe.get("trace_rg2")[:end], dtype=float)
            d = np.asarray(td[:end], dtype=float) - np.asarray(cv[:end], dtype=float)
            per = np.diff(np.concatenate([[0], np.asarray(marks[:end], dtype=np.int64)]))
            up = (per - lib.PROPOSAL_BYTES_PER_SWEEP) / (8 * lib.M)
            checkpoint = {"end": end, "mean_D": float(d[end - lib.APP_WINDOW:end].mean()),
                          "uphill_fraction": float(up[end - lib.APP_WINDOW:end].mean()),
                          "mean_rg2": float(rg2[end - lib.APP_WINDOW:end].mean())}
            self.app.append(checkpoint)
            stat = lib.application_statistic(checkpoint, self.constants)
            self.statA[0].append(end)
            self.statA[1].append(stat)
            self._alarm("A", end, stat)
            self.next_app += lib.STRIDE
        # uphill fraction per sweep, from the SweepClock marks
        n = len(marks)
        if n > self.uphill_sent:
            prev = marks[self.uphill_sent - 1] if self.uphill_sent else 0
            for i in range(self.uphill_sent, n):
                b = marks[i] - prev
                prev = marks[i]
                self.uphill[0].append(i)
                self.uphill[1].append((b - lib.PROPOSAL_BYTES_PER_SWEEP) / (8 * lib.M))
            self.uphill_sent = n

    def _alarm(self, arm: str, end: int, stat: float) -> None:
        tau = self.tau[arm]
        if stat > tau and self.onset_sweep is not None and end > self.onset_sweep and not self.alarmed[arm]:
            self.alarmed[arm] = True
            self.ctx.event("alarm", {"unit": "sweep", "value": end}, arm=arm, statistic=stat, tau=tau,
                           latency_sweeps=end - self.onset_sweep, first_post_onset=True)
        elif stat > tau and (self.onset_sweep is None or end <= self.onset_sweep):
            self.ctx.event("alarm", {"unit": "sweep", "value": end}, arm=arm, statistic=stat, tau=tau,
                           pre_onset=True)

    def frame_data(self, data: dict, final) -> None:
        series = data["series"]
        for key, buf in (("statC", self.statC), ("statA", self.statA), ("uphill", self.uphill)):
            if buf[0]:
                series[key] = {"x": buf[0], "y": buf[1]}
        self.statC, self.statA, self.uphill = ([], []), ([], []), ([], [])
        if self.pcols:
            data.setdefault("matrix", {})["conv_p"] = {"rows": list(self.lib.CONV_P_KEYS), "cols": list(self.all_cols()),
                                                       "values": self.all_values()}
        data["scalars"].update({"onset_sweep": self.onset_sweep, "alarm_C": self.alarmed["C"],
                                "alarm_A": self.alarmed["A"], "windows_C": len(self.conv), "windows_A": len(self.app)})

    def all_cols(self):
        return self.pcols

    def all_values(self):
        # rows = tests, columns = windows
        return [[row[k] if row[k] is not None else float("nan") for row in self.pvals] for k in range(len(self.lib.CONV_P_KEYS))]


def run(ctx) -> dict:
    lib = ctx.h1lib()
    from rnglab14.models.pimc_harmonic import PathIntegralHarmonicOscillator
    from rnglab14.sources import PCG64Source

    p = ctx.params
    ns, label, replica = p["namespace"], p["label"], p["replica"]
    family, severity, sweeps, onset = p["family"], severity_of(p), p["sweeps"], p["onset_bytes"]
    gates = json.loads((ctx.code_root / GATES).read_text())
    if not ctx.observed:  # the study's own function
        record, arrays = lib.run_one(ns, label, family, severity, replica, sweeps=sweeps, onset_bytes=onset)
        return {"payload": {"call": "h1lib.run_one", "record": record, "arrays": arrays},
                "summary": summary_of(record, gates)}
    seeds = lib.seeds_for(ns, label, replica)
    ctx.source_log.extend([{"role": role, "generator": "pcg64", "seed": seed, "seed_label": f"{ns}/{label}/{role}/{replica:05d}",
                            "seed_derivation": "h1lib.seeds_for(namespace, label, replica)", "deterministic": True}
                           for role, seed in seeds.items()])
    model = PathIntegralHarmonicOscillator(beads=lib.M, temperature=1 / lib.BETA, displacement_delta=lib.DELTA)
    reference = lib.exact_finite_bead_energy(lib.M, lib.BETA)
    observer = SweepObserver(ctx, model, PathIntegralHarmonicOscillator.run_simulation.__code__, sweeps, 0, 512,
                             reference, sweeps)
    local = lib.FaultyWords(ctx.tap(PCG64Source(seeds["local"]), "local/base"), family, severity, seeds["fault"],
                            seeds["gfsr"], onset)
    local_tap = ctx.tap(local, "local", observer.on_read)
    clock = lib.SweepClock(ctx.tap(PCG64Source(seeds["centroid"]), "centroid/base"), local)
    clock_tap = ctx.tap(clock, "centroid")
    start = lib.sample_exact_finite_bead_paths(ctx.tap(PCG64Source(seeds["start"]), "start"), lib.M, lib.BETA, 2)[0]
    monitors = Monitors(ctx, lib, observer, local, clock, gates, onset, sweeps)
    observer.on_step = monitors.step
    observer.extra_frame = monitors.frame_data
    ctx.meta(unit="sweep", total=sweeps, burn_in=0, beads=lib.M, beta=lib.BETA, delta=lib.DELTA, batch_size=512,
             references={"finite_bead_energy": reference}, thresholds={"tau_C": gates["thresholds"]["C"],
                                                                      "tau_A": gates["thresholds"]["A"]},
             application_constants=gates["application_constants"], onset_bytes=onset, family=family,
             severity=severity, roles=[t.role for t in ctx.taps], gates_file=GATES,
             call="h1lib.run_one composition with taps (direct mode: h1lib.run_one)")
    result = model.run_simulation(local_tap, sweeps=sweeps, burn_in=0, initial_path=start, centroid_source=clock_tap,
                                  initial_path_provenance={"namespace": ns, "condition": label, "role": "start",
                                                           "replica": replica, "seed": seeds["start"]},
                                  uncertainty_batch_sizes=(512,), record_trace=True)
    observer.probe.detach()
    record, arrays, (rct, apt) = record_from(lib, ns, label, family, severity, replica, sweeps, onset, seeds, local, clock,
                                             result)
    # final windows that complete with the last sweep
    observer.probe.locals = {"thermo_energies": list(result.trace["TD"]), "virial_energies": list(result.trace["CV"]),
                             "trace_rg2": result.trace["rg2"]}
    monitors.step(sweeps - 1)
    observer.probe.locals = None
    observer.emit(final={"trace": result.trace, "acceptance_rate": result.acceptance_rate, "official_interval": None})
    checks = []
    same_c = [w for w in monitors.conv] == record["conventional"]
    checks.append(check("live battery windows vs record", "ok" if same_c else "failed",
                        "every conventional window computed live equals the record's window"
                        if same_c else "live windows differ from the record", windows=len(monitors.conv)))
    same_a = monitors.app == record["application"]
    checks.append(check("live application windows vs record", "ok" if same_a else "failed",
                        "every application window (D, uphill fraction, Rg²) computed live equals the record's"
                        if same_a else "live application windows differ from the record", windows=len(monitors.app)))
    det = {arm: lib.detection(lib.arm_series(record, arm, gates["application_constants"]), gates["thresholds"][arm],
                              record["onset_sweep"], arrays["marks"]) for arm in ("C", "A")}
    gates_sha = hashlib.sha256((ctx.code_root / GATES).read_bytes()).hexdigest()
    checks.append(check("frozen calibration read", "ok", "thresholds and application constants read from the study's "
                        "results/gates.json (recorded, not recalibrated); its SHA-256 is given so it can be compared "
                        "with the study's evidence", tau_C=gates["thresholds"]["C"],
                        tau_A=gates["thresholds"]["A"], gates_sha256=gates_sha))
    ctx.event("health_tests", {"unit": "byte", "value": int(record["local_bytes"])}, **record["health_tests"])
    summary = summary_of(record, gates)
    summary["detection_C"] = det["C"]
    summary["detection_A"] = det["A"]
    return {"payload": {"call": "h1lib.run_one", "record": record, "arrays": arrays}, "checks": checks,
            "summary": summary}


def summary_of(record: dict, gates: dict) -> dict:
    harm = record.get("harm")
    return {"family": record["family"], "severity": record["severity"], "onset_sweep": record["onset_sweep"],
            "harm_wrong": harm["wrong"] if harm else None, "harm_interval": harm["interval"] if harm else None,
            "rct_post": record["health_tests"]["rct_post"], "apt_post": record["health_tests"]["apt_post"],
            "local_sha256": record["local_sha256"]}
