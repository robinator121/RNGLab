"""Aggregated decay (milestone B2): rnglab.nuclear.aggregate.simulate_aggregate with its own sink.

The engine calls ``sink(interval, record)`` with detached copies after every
interval; the observer turns them into telemetry and pauses there between
intervals. The source is deliberately not tapped (the engine records its
source's class in the run's provenance). Schemes and cases are read from the
frozen B2 verification protocol in the code source; its seed namespace is not used.

Grid: the step is rounded to a multiple of 2**-20 so that every interval has
exactly the same float64 length (the plan then holds one interval law), and the
horizon becomes intervals * step. Both are recorded.
"""
from __future__ import annotations

import json
import math

import numpy as np

from ..runtime import check

PROTOCOL = "research/v020-nuclear-b2-verify/protocol.json"


def load_case(root, key: str):
    from rnglab.nuclear import DecayScheme
    path = root / PROTOCOL
    protocol = json.loads(path.read_text())
    scheme = DecayScheme.from_dict(protocol["schemes"][key])
    case = protocol["cases"][key]
    return scheme, case, protocol["schemes"][key], str(path.relative_to(root))


def scaled_initial(case_initial: dict, population: int) -> dict:
    if population <= 0:
        return dict(case_initial)
    total = sum(case_initial.values())
    names = list(case_initial)
    out = {n: (case_initial[n] * population) // total for n in names}
    out[names[0]] += population - sum(out.values())  # remainder to the first start nuclide
    return out


def grid(horizon: float, intervals: int) -> tuple[np.ndarray, float]:
    step = round(horizon / intervals * 2**20) / 2**20
    if step <= 0:
        step = 2.0**-20
    return np.arange(intervals + 1, dtype=np.float64) * step, step


def references(scheme, initial: dict, times: np.ndarray) -> dict:
    """Bateman expectations and, with one populated start nuclide, exact binomial 95% bands."""
    from rnglab.nuclear.bateman import expected_populations, state_probabilities
    names = [n.name for n in scheme.nuclides]
    expected = expected_populations(scheme, initial, times)
    out = {"expected": {n: expected[:, i].tolist() for i, n in enumerate(names)}, "band_kind": None}
    starts = [n for n, c in initial.items() if c > 0]
    if len(starts) == 1:
        from scipy.stats import binom
        n0 = initial[starts[0]]
        prob = np.clip(state_probabilities(scheme, starts[0], times), 0.0, 1.0)
        out["band_lo"] = {n: binom.ppf(0.025, n0, prob[:, i]).tolist() for i, n in enumerate(names)}
        out["band_hi"] = {n: binom.ppf(0.975, n0, prob[:, i]).tolist() for i, n in enumerate(names)}
        out["prob"] = {n: prob[:, i].tolist() for i, n in enumerate(names)}
        out["band_kind"] = f"exact binomial 2.5% and 97.5% quantiles, N = {n0} from {starts[0]}"
    return out


def run(ctx) -> dict:
    from rnglab.nuclear.aggregate import simulate_aggregate
    from rnglab.nuclear.streams import WordStream

    p = ctx.params
    scheme, case, scheme_dict, source_file = load_case(ctx.code_root, p["scheme"])
    initial = scaled_initial(case["initial"], p["population"])
    horizon = p["horizon"] if p["horizon"] > 0 else float(case.get("times", [0, 1])[-1])
    times, step = grid(horizon, p["intervals"])
    names = [n.name for n in scheme.nuclides]
    total = sum(initial.values())
    channel_names = [f"{n.name}/{c.mode.value if hasattr(c.mode, 'value') else c.mode}->{c.daughter}"
                     for n in scheme.nuclides for c in n.channels]
    ref = references(scheme, initial, times) if ctx.observed else None
    if ctx.observed:
        ctx.meta(unit="interval", total=int(times.size - 1), scheme=scheme_dict, scheme_sha256=scheme.sha256(),
                 scheme_file=source_file, initial=initial, horizon=float(times[-1]), step=step,
                 ref_times=times.tolist(), expected=ref["expected"], band_lo=ref.get("band_lo"),
                 band_hi=ref.get("band_hi"), band_kind=ref["band_kind"], channel_names=channel_names,
                 series_labels={**{f"z_{n}": n for n in names}},
                 call="simulate_aggregate(scheme, initial, times, WordStream(source), sink=...)")
    source = ctx.source_for("local", faulted=True, observe=False)  # no tap (see module docstring)
    stream = WordStream(source)
    pending = {"series": {}, "n": 0}
    conservation_bad = []
    received = []  # populations as delivered to the sink, compared with the run's kept outputs

    def add(key, x, y):
        s = pending["series"].setdefault(key, {"x": [], "y": []})
        s["x"].append(x)
        s["y"].append(y)

    def emit() -> None:
        k = pending["n"]
        ctx.frame({"unit": "interval", "value": k, "total": int(times.size - 1), "time": float(times[k])},
                  {"series": pending["series"], "scalars": pending.get("scalars", {})}, progress=k / (times.size - 1))
        pending["series"] = {}

    def sink(interval: int, record: dict) -> None:
        t = float(times[interval + 1])
        pops = record["populations"]
        received.append(np.array(pops, copy=True))
        decays = record["decays"]
        for i, n in enumerate(names):
            add(f"pop_{n}", t, int(pops[i]))
            pr = ref.get("prob", {}).get(n) if ref else None
            if pr is not None:
                q = pr[interval + 1]
                var = total * q * (1 - q)
                dev = int(pops[i]) - total * q
                # zero variance: the exact law is deterministic here; a mismatch is infinitely unlikely, not z = 0
                add(f"z_{n}", t, dev / math.sqrt(var) if var > 0 else (0.0 if dev == 0 else math.copysign(math.inf, dev)))
        flat = decays.reshape(-1).tolist()
        k = 0
        for i, nuc in enumerate(scheme.nuclides):
            for j, _ in enumerate(nuc.channels):
                add(f"dec_{k}", t, int(decays[i, j]))
                k += 1
        cons = int(pops.sum()) - total
        add("cons", t, cons)
        if cons != 0:
            conservation_bad.append(interval)
        pending["n"] = interval + 1
        pending["scalars"] = {"interval": interval + 1, "time": t,
                              "populations": {n: int(pops[i]) for i, n in enumerate(names)},
                              "photons": int(np.sum(record["photons"])), "neutrons": int(np.sum(record["neutrons"]))}
        if ctx.due():
            emit()
        ctx.boundary(interval + 1, emit)

    run_ = simulate_aggregate(scheme, initial, times, stream, sink=sink if ctx.observed else None, keep_outputs=True)
    checks = []
    if ctx.observed:
        emit()
        identity = run_.stream_provenance().get("replay_identity")
        checks.append(check("source untouched", "ok",
                            "the engine read its source directly (no tap); its recorded replay identity is "
                            + ("present" if identity else "absent because the source is a fault adapter or not replayable"),
                            replay_identity=identity))
        checks.append(check("conservation per interval", "ok" if not conservation_bad else "failed",
                            "Σ populations = N after every interval (also checked by the engine)"
                            if not conservation_bad else f"violated in intervals {conservation_bad[:10]}"))
        kept = np.asarray(run_.populations)[1:]
        same = len(received) == len(kept) and all(np.array_equal(a, b) for a, b in zip(received, kept))
        checks.append(check("sink records vs official outputs", "ok" if same else "failed",
                            "every interval's populations delivered to the sink equal the run's kept outputs"
                            if same else "sink records differ from the run's outputs", intervals=len(received)))
        if ref and ref.get("band_lo"):
            lo, hi = ref["band_lo"], ref["band_hi"]
            outside = sum(1 for i, n in enumerate(names) for k in range(1, times.size)
                          if not lo[n][k] <= run_.populations[k][i] <= hi[n][k])
            cells = len(names) * (times.size - 1)
            # Descriptive only: cells are correlated, so there is no pass/fail rule. The display flags a
            # count above twice the independent-cell expectation as a warning (a heuristic, not a test).
            checks.append(check("populations inside the exact 95% bands (descriptive, no gate)",
                                "warning" if outside > 0.10 * cells else "ok",
                                f"{outside} of {cells} (nuclide, time) cells outside their marginal 95% band "
                                f"(about {0.05 * cells:.0f} expected if every cell were independent; they are not). "
                                "Not a statistical test; the frozen B2 verification is the validated check.",
                                outside=outside, cells=cells, gate=None))
    summary = {"final_populations": {n: int(v) for n, v in zip(names, run_.final_populations)},
               "words_read": run_.words_read, "digest": run_.digest, "horizon": float(times[-1]), "step": step}
    payload = {"call": "rnglab.nuclear.aggregate.simulate_aggregate", "scheme": p["scheme"],
               "scheme_sha256": scheme.sha256(), "initial": initial, "times_step": step, "intervals": int(times.size - 1),
               "summary": run_.summary(), "digest": run_.digest, "populations": np.asarray(run_.populations),
               "decays": np.asarray(run_.decays), "photons": np.asarray(run_.photons),
               "neutrons": np.asarray(run_.neutrons), "provenance": run_.stream_provenance()}
    return {"payload": payload, "checks": checks, "summary": summary}
