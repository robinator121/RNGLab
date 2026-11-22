"""Event-level decay (Layer A): rnglab.nuclear.decay.simulate_decays, observed per chunk.

The engine reads each chunk's words in one read (3 words per decay step and
nucleus), so a step here is a chunk of nuclei. The engine works nucleus by
nucleus, not in time order: after the run, the event table (the engine's own
DecayRun) is played back in simulated time. Populations come from
DecayRun.populations, references from the Bateman module, and the decay times
of the initially populated nuclide are compared with the exact expected counts
per time bin.
"""
from __future__ import annotations

import time

import numpy as np

from ..runtime import FrameProbe, check
from .nuclear_aggregate import load_case, scaled_initial

MAX_EMISSIONS = 40000
POP_POINTS = 241


class ChunkObserver:
    def __init__(self, ctx, code, total: int):
        self.ctx, self.total = ctx, total
        self.probe = FrameProbe(code)
        self.chunks = 0
        self.events_so_far = 0
        self.points = ([], [])
        self.last_stream = -1e9

    def on_read(self, tap, raw: bytes) -> None:
        if not self.probe.attach():
            return
        start = self.probe.get("start")
        if start is None:
            return
        if self.chunks:  # a new chunk starts: the previous one is complete
            self.step_done(start)
        self.chunks += 1

    def step_done(self, nuclei_done: int) -> None:
        columns = self.probe.get("columns") or {}
        events = int(sum(len(a) for a in columns.get("event_id", [])))
        self.points[0].append(nuclei_done)
        self.points[1].append(events - self.events_so_far)
        self.events_so_far = events
        self.nuclei_done = nuclei_done
        if self.ctx.due():
            self.emit()
        self.ctx.boundary(self.chunks, self.emit)

    def emit(self, final: dict | None = None) -> None:
        data = {"series": {"chunk_events": {"x": self.points[0], "y": self.points[1]}},
                "scalars": {"chunks": self.chunks, "nuclei_done": getattr(self, "nuclei_done", 0),
                            "events_so_far": self.events_so_far}}
        self.points = ([], [])
        if final:
            data.update(final)
        now = time.monotonic()
        if now - self.last_stream >= 1.0 or final is not None:
            data["streams"] = self.ctx.stream_data()
            self.last_stream = now
        done = getattr(self, "nuclei_done", 0) if final is None else self.total
        self.ctx.frame({"unit": "nucleus", "value": done, "total": self.total}, data, progress=done / self.total)


def run(ctx) -> dict:
    from rnglab.nuclear import simulate_decays
    from rnglab.nuclear.bateman import expected_populations
    from rnglab.nuclear.decay import PARTICLE_CODES
    from rnglab.nuclear.streams import WordStream

    p = ctx.params
    scheme, case, scheme_dict, source_file = load_case(ctx.code_root, p["scheme"])
    initial = scaled_initial(case["initial"], p["population"])
    horizon = p["horizon"] if p["horizon"] > 0 else float(case.get("times", [0, 1])[-1])
    names = [n.name for n in scheme.nuclides]
    total = sum(initial.values())
    observer = ChunkObserver(ctx, simulate_decays.__code__, total) if ctx.observed else None
    source = ctx.source_for("local", faulted=True, on_read=observer.on_read if observer else None)
    stream = WordStream(source)
    grid = np.linspace(0.0, horizon, POP_POINTS)
    if ctx.observed:
        expected = expected_populations(scheme, initial, grid)
        ctx.meta(unit="nucleus", total=total, scheme=scheme_dict, scheme_sha256=scheme.sha256(), scheme_file=source_file,
                 initial=initial, horizon=horizon, chunk=p["chunk"], words_per_nucleus=scheme.words_per_nucleus,
                 ref_times=grid.tolist(), expected={n: expected[:, i].tolist() for i, n in enumerate(names)},
                 call="simulate_decays(scheme, initial, horizon, WordStream(source), chunk=chunk)")
    run_ = simulate_decays(scheme, initial, horizon, stream, chunk=p["chunk"])
    checks = []
    if observer is not None:
        observer.probe.detach()
        pops = run_.populations(grid)
        final = {"series": {f"pop_{n}": {"x": grid.tolist(), "y": pops[:, i].tolist(), "replace": True}
                            for i, n in enumerate(names)}}
        em = run_.emissions()
        count = int(em["time"].size)
        stride = max(1, -(-count // MAX_EMISSIONS))
        code_names = {str(code): name for name, code in PARTICLE_CODES.items()}
        final["state"] = {"emissions": {"time": em["time"][::stride].tolist(), "particle": em["particle"][::stride].tolist(),
                                        "nucleus": em["nucleus"][::stride].tolist(), "names": code_names,
                                        "total": count, "stride": stride},
                          "final_counts": run_.summary()["final_counts"]}
        hist = {}
        bins = np.linspace(0.0, horizon, 41)
        for start_name, n0 in initial.items():
            if n0 <= 0:
                continue
            j = scheme.index[start_name]
            mask = (run_.events["parent"] == j) & (run_.events["step"] == 0)
            counts = np.histogram(run_.events["time"][mask], bins=bins)[0]
            lam = float(scheme.decay_constants[j])
            exp_counts = n0 * (np.exp(-lam * bins[:-1]) - np.exp(-lam * bins[1:]))
            hist[f"{start_name} first decays"] = {"edges": bins.tolist(), "counts": counts.tolist(),
                                                  "expected": exp_counts.tolist()}
        final["hist"] = {"waiting": hist}
        final["scalars"] = {"events": run_.n_events, "words_used": run_.words_used}
        observer.emit(final=final)
        cons = pops.sum(axis=1)
        ok = bool(np.all(cons == total))
        checks.append(check("conservation", "ok" if ok else "failed",
                            f"Σ populations = {total} at all {POP_POINTS} grid times (DecayRun.populations)"
                            if ok else "population sum changes over time"))
        lineage = run_.events["previous_event"]
        has_prev = lineage >= 0
        lineage_ok = bool(np.all(np.isin(lineage[has_prev], run_.events["event_id"])))
        checks.append(check("lineage", "ok" if lineage_ok else "failed",
                            "every event's predecessor exists in the event table" if lineage_ok
                            else "an event refers to a missing predecessor"))
        words_ok = stream.words_read == run_.words_used
        checks.append(check("word layout", "ok" if words_ok else "failed",
                            f"{stream.words_read:,} words read = nuclei × {scheme.words_per_nucleus} (fixed layout)"
                            if words_ok else "words read differ from the fixed layout"))
    summary = {**{k: v for k, v in run_.summary().items() if k in ("n_nuclei", "n_events", "words_used",
                                                                       "final_counts", "events_sha256")},
               "horizon": horizon}
    payload = {"call": "rnglab.nuclear.decay.simulate_decays", "scheme": p["scheme"],
               "scheme_sha256": scheme.sha256(), "initial": initial, "horizon": horizon,
               "summary": run_.summary(), "events": dict(run_.events),
               "final_state": np.asarray(run_.final_state)}
    return {"payload": payload, "checks": checks, "summary": summary}
