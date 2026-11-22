"""Monte Carlo decision support: replicated official calls under healthy and faulted sources.

Every (replicate, condition) is one call of MonteCarloDecisionSupport.evaluate_decisions,
unchanged. Replicate r of every condition reads the healthy generator seeded with
role 'replicate-r' (common random numbers), so differences between conditions
come from the fault adapter alone. One step is one call. Inside a call the observer
reads the function's own payoff lists to show the option means converging.
Aggregates (selection and rank frequencies, regret, Clopper-Pearson rates via
rnglab17.endpoints.rate) are derived from the official results.
"""
from __future__ import annotations

import time

import numpy as np

from ..runtime import FrameProbe, check


def conditions(p: dict) -> list[dict]:
    if p["design"] == "paired":
        return [{"name": "healthy", "fault": None}, {"name": f"faulted ({p['fault']})", "fault": "config"}]
    levels = np.linspace(0.0, p["sweep_max"], p["sweep_levels"])
    return [{"name": f"bias {lv:.4g}", "fault": None if lv == 0 else ("bias", float(lv)), "level": float(lv)}
            for lv in levels]


class LiveMeans:
    """Running option means of the call in progress, from the function's own payoff lists."""

    def __init__(self, ctx, code, names):
        self.ctx, self.code, self.names = ctx, code, names
        self.probe = None
        self.sums = None

    def start(self):
        self.probe = FrameProbe(self.code)
        self.sums = {n: [0.0, 0] for n in self.names}
        self.points = {n: ([], []) for n in self.names}

    def on_read(self, tap, raw):
        if self.ctx.due() and self.probe.attach():
            payoffs = self.probe.get("option_payoffs") or {}
            for name in self.names:
                values = payoffs.get(name, [])
                total, used = self.sums[name]
                if len(values) > used:
                    total += float(sum(values[used:]))
                    used = len(values)
                    self.sums[name] = [total, used]
                    self.points[name][0].append(used)
                    self.points[name][1].append(total / used)
            self.ctx.parent_emit()


def run(ctx) -> dict:
    from rnglab14.models.decision_support import DecisionOption, MonteCarloDecisionSupport
    from rnglab17.endpoints import rate

    p = ctx.params
    options = [DecisionOption("A", p["mean_a"], p["vol_a"]), DecisionOption("B", p["mean_b"], p["vol_b"]),
               DecisionOption("C", p["mean_c"], p["vol_c"])]
    dss = MonteCarloDecisionSupport(options)
    names = [o.name for o in options]
    conds = conditions(p)
    R = p["replicates"]
    total_calls = R * len(conds)
    sel = np.zeros((len(conds), len(names)), dtype=np.int64)
    ranks = np.zeros((len(conds), len(names), len(names)), dtype=np.int64)
    means = [[] for _ in conds]
    regrets = [[] for _ in conds]
    results = []
    live = LiveMeans(ctx, MonteCarloDecisionSupport.evaluate_decisions.__code__, names) if ctx.observed else None
    state = {"calls": 0, "last_live": {}}

    def aggregates() -> dict:
        n = np.array([len(m) for m in means])
        freq = (sel / np.maximum(n, 1)[:, None]).tolist()
        rank_rows, rank_vals, rank_counts, rank_n = [], [], [], []
        for c, cond in enumerate(conds):
            for o, name in enumerate(names):
                rank_rows.append(f"{cond['name']} · {name}")
                rank_vals.append((ranks[c, o] / max(1, n[c])).tolist())
                rank_counts.append(ranks[c, o].tolist())
                rank_n.append(int(n[c]))
        correct = [int(sum(1 for r in results if r["condition"] == c and r["ranking_correct"])) for c in range(len(conds))]
        rates = [rate(correct[c], int(n[c])) for c in range(len(conds))]
        est = {"options": names, "true_means": [o.true_mean_payoff for o in options],
               "conditions": [{"name": cond["name"], "n": int(n[c]),
                               "mean": (np.mean(means[c], axis=0).tolist() if means[c] else [None] * len(names)),
                               "sd": (np.std(means[c], axis=0, ddof=1).tolist() if len(means[c]) > 1 else [0.0] * len(names))}
                              for c, cond in enumerate(conds)]}
        xs = [cond.get("level", c) for c, cond in enumerate(conds)]
        rate_table = {"x": xs, "labels": [c["name"] for c in conds], "rate": [r["rate"] for r in rates],
                      "lo": [r["interval"][0] if r["interval"] else None for r in rates],
                      "hi": [r["interval"][1] if r["interval"] else None for r in rates], "correct": correct,
                      "n": n.tolist()}
        hist = {}
        all_regret = [v for rs in regrets for v in rs]
        top = max(all_regret + [1.0])
        edges = np.linspace(0.0, top * 1.0001, 21)
        for c, cond in enumerate(conds):
            if regrets[c]:
                hist[cond["name"]] = {"edges": edges.tolist(), "counts": np.histogram(regrets[c], bins=edges)[0].tolist()}
        return {"matrix": {"selection": {"rows": [c["name"] for c in conds], "cols": names, "values": freq,
                                         "counts": sel.tolist(), "n": n.tolist()},
                           "ranks": {"rows": rank_rows, "cols": [f"rank {k + 1}" for k in range(len(names))],
                                     "values": rank_vals, "counts": rank_counts, "n": rank_n}},
                "table": {"estimates": est, "rates": rate_table}, "hist": {"regret": hist}}

    def emit(final: bool = False) -> None:
        data = aggregates()
        if live is not None and live.points:
            data["series"] = {f"live_{n.lower()}": {"x": live.points[n][0], "y": live.points[n][1], "replace": True}
                              for n in names}
        data["scalars"] = {"calls": state["calls"], "replicate": state.get("r"), "condition": state.get("c_name")}
        ctx.frame({"unit": "call", "value": state["calls"], "total": total_calls}, data,
                  progress=state["calls"] / total_calls)

    ctx.parent_emit = lambda: emit()
    ctx.meta(unit="call", total=total_calls, options=names, conditions=[c["name"] for c in conds],
             references={f"true_mean_{o.name}": o.true_mean_payoff for o in options},
             series_labels={"live_a": "option A", "live_b": "option B", "live_c": "option C"},
             call="MonteCarloDecisionSupport(options).evaluate_decisions(source, scenarios)")
    for r in range(R):
        for c, cond in enumerate(conds):
            state.update(r=r, c_name=cond["name"])
            if live is not None:
                live.start()
            role = f"replicate-{r}"
            if cond["fault"] is None:
                source = ctx.source_for(role, faulted=False, on_read=live.on_read if live else None)
            elif cond["fault"] == "config":
                source = ctx.source_for(role, faulted=True, on_read=live.on_read if live else None)
            else:  # bias sweep level: DegradedFaultSource(bias_delta=level) only
                base = ctx.tap(ctx.generator(role), f"{role}/base")
                from rnglab.faults import DegradedFaultSource
                adapter = DegradedFaultSource(base, bias_delta=cond["fault"][1])
                ctx.source_log.append({"role": f"{role}/fault", "adapter": "rnglab.faults.DegradedFaultSource",
                                       "bias_delta": cond["fault"][1]})
                source = ctx.tap(adapter, role, live.on_read if live else None)
            out = dss.evaluate_decisions(source, scenarios=p["scenarios"])
            if ctx.observed:
                for t in list(ctx.taps):
                    ctx.retire(t)
            order = sorted(range(len(names)), key=lambda i: -out.estimated_means[names[i]])
            for rank, i in enumerate(order):
                ranks[c, i, rank] += 1
            sel[c, names.index(out.selected_option)] += 1
            means[c].append([out.estimated_means[n] for n in names])
            regrets[c].append(out.decision_regret)
            results.append({"replicate": r, "condition": c, "condition_name": cond["name"],
                            "selected": out.selected_option, "ranking_correct": out.ranking_correct,
                            "result": out})
            state["calls"] += 1
            if ctx.due():
                emit()
            ctx.boundary(state["calls"], emit)
    final = aggregates()
    checks = []
    if ctx.observed:
        emit(final=True)
        checks.append(check("common random numbers", "ok",
                            "replicate r of every condition read the healthy generator seeded with role "
                            "'replicate-r'; conditions differ only by the fault adapter"))
    summary = {c["name"]: {"p_correct": final["table"]["rates"]["rate"][i],
                           "interval": [final["table"]["rates"]["lo"][i], final["table"]["rates"]["hi"][i]]}
               for i, c in enumerate(conds)}
    payload = {"call": "rnglab14.models.decision_support.MonteCarloDecisionSupport.evaluate_decisions",
               "options": options, "design": p["design"], "conditions": conds, "replicates": R,
               "scenarios": p["scenarios"], "results": results}
    return {"payload": payload, "checks": checks, "summary": summary}
