"""Shared measurement layer: standard result types and statistics used across models.

Every endpoint keeps its model-specific meaning (``observable``, ``meaning``, ``unit``). The shared
part is only the statistical machinery: deviation from an exact reference, interval coverage,
paired healthy-vs-faulted effects, multiplicity correction. A small p-value says the observed
endpoint is unlikely under the healthy model; it is not proof that the random source is
compromised, and it says nothing about which failure class (source, sampler, estimator) caused it.

Reused unchanged: Holm from ``rnglab14.statistics.multitest``, exact binomial rates from
``rnglab17.endpoints.rate``, and the Stage C conventional battery ``conventional_window`` from the
frozen ``research/v018-h1-detection/h1lib.py`` (loaded read-only).
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from typing import Sequence


@dataclass
class Endpoint:
    """One model-specific, reference-backed measurement of one experimental unit."""
    model: str
    observable: str
    meaning: str
    unit: str
    estimate: float
    standard_error: float | None
    reference: float | None
    reference_kind: str              # "exact", "exact-law", "none"
    reference_source: str = ""       # function that produced the reference
    interval: tuple[float, float] | None = None   # the model's own uncertainty interval (95 %)
    interval_method: str = ""
    extra: dict = field(default_factory=dict)

    @property
    def z(self) -> float | None:
        if self.reference is None or not self.standard_error or not math.isfinite(self.standard_error):
            return None
        return (self.estimate - self.reference) / self.standard_error

    @property
    def p_two_sided(self) -> float | None:
        z = self.z
        return None if z is None else math.erfc(abs(z) / math.sqrt(2.0))

    @property
    def covered(self) -> bool | None:
        if self.interval is None or self.reference is None:
            return None
        lo, hi = self.interval
        return bool(lo <= self.reference <= hi)

    def to_dict(self) -> dict:
        out = asdict(self)
        out.update(z=self.z, p_two_sided=self.p_two_sided, covered=self.covered)
        return out


def rate(flags: Sequence[bool | None]) -> dict:
    """Rate of True among defined flags with its exact 95 % interval (canonical ``rnglab17.endpoints.rate``)."""
    from rnglab17.endpoints import rate as exact_rate
    defined = [bool(f) for f in flags if f is not None]
    out = exact_rate(sum(defined), len(defined))
    out["undefined"] = len(flags) - len(defined)
    return out


def paired_effect(healthy: Sequence[float], faulted: Sequence[float]) -> dict:
    """Mean paired difference (faulted - healthy) over replicates sharing seeds.

    The normal-approximation p-value is labelled as such; with few replicates it is indicative only.
    """
    pairs = [(h, f) for h, f in zip(healthy, faulted) if h is not None and f is not None
             and math.isfinite(h) and math.isfinite(f)]
    n = len(pairs)
    if n == 0:
        return {"n": 0, "mean_difference": None, "se": None, "z": None, "p_two_sided_normal_approx": None}
    d = [f - h for h, f in pairs]
    mean = sum(d) / n
    var = sum((x - mean) ** 2 for x in d) / (n - 1) if n > 1 else float("nan")
    se = math.sqrt(var / n) if n > 1 else None
    if se:
        z = mean / se
        p = math.erfc(abs(z) / math.sqrt(2.0))
    else:
        z = p = None
    return {"n": n, "mean_difference": mean, "se": se, "z": z, "p_two_sided_normal_approx": p,
            "identical_pairs": sum(1 for x in d if x == 0)}


def holm(p_values: Sequence[float | None], alpha: float = 0.05) -> dict:
    """Holm step-down over the defined p-values (rnglab14 implementation); undefined ones are reported apart."""
    from rnglab14.statistics.multitest import holm_bonferroni_correction
    index = [i for i, p in enumerate(p_values) if p is not None]
    result = holm_bonferroni_correction([p_values[i] for i in index], alpha)
    adjusted: list[float | None] = [None] * len(p_values)
    reject: list[bool | None] = [None] * len(p_values)
    for k, i in enumerate(index):
        adjusted[i] = result.adjusted_p_values[k]
        reject[i] = result.reject_decisions[k]
    return {"method": "holm (rnglab14.statistics.multitest.holm_bonferroni_correction)", "alpha": alpha,
            "family_size": len(index), "adjusted": adjusted, "reject": reject}


class StreamRecorder:
    """Keeps the first ``cap`` bytes a role emitted from byte offset ``start`` on (for the battery)."""

    def __init__(self, start: int = 0, cap: int = 1 << 16):
        self.start, self.cap, self.data, self.seen = start, cap, bytearray(), 0

    def feed(self, raw: bytes) -> None:
        begin, end = self.seen, self.seen + len(raw)
        self.seen = end
        if end <= self.start or len(self.data) >= self.cap:
            return
        chunk = raw[max(0, self.start - begin):]
        self.data += chunk[: self.cap - len(self.data)]


def conventional_battery(data: bytes, alpha: float = 0.01) -> dict:
    """The ten Stage C battery p-values on one window, Holm-adjusted within the battery.

    The frozen study calibrated its battery statistic at matched false alarms for its own window
    and design; that calibration does not transfer. Here the decision rule is Holm at ``alpha``
    over the ten tests, and its empirical false-alarm rate is measured on the healthy arm of the
    same experiment. An alarm means the bytes look non-uniform to these tests, nothing more.
    """
    from .faults import h1lib
    lib = h1lib()
    if len(data) < 512:
        return {"available": False, "reason": f"window of {len(data)} bytes is shorter than 512"}
    pv = lib.conventional_window(bytes(data))
    keys = list(lib.CONV_P_KEYS)
    adj = holm([pv[k] for k in keys], alpha)
    return {"available": True, "window_bytes": len(data), "p_values": {k: pv[k] for k in keys},
            "holm": {"alpha": alpha, "adjusted": dict(zip(keys, adj["adjusted"])),
                     "reject": dict(zip(keys, adj["reject"]))},
            "alarm": any(r for r in adj["reject"] if r is not None),
            "rule": f"Holm over the 10 Stage C tests at alpha = {alpha} (not the frozen study's calibrated threshold)"}


SIGN_FLIP_MAX_N = 16


def paired_test(healthy: Sequence[float | None], faulted: Sequence[float | None]) -> dict:
    """Two-sided paired test of the faulted-minus-healthy differences of a raw estimate.

    It needs no per-unit standard error, so units whose own uncertainty estimate broke (often the
    most harmed ones) stay in. For n <= 16 pairs: exact sign-flip randomisation test over all 2^n
    sign patterns of the mean difference (smallest attainable p = 2 / 2^n, stated). For larger n:
    paired Student t. With n < 2 no test is made.
    """
    from itertools import product
    pairs = [(h, f) for h, f in zip(healthy, faulted) if h is not None and f is not None
             and math.isfinite(h) and math.isfinite(f)]
    d = [f - h for h, f in pairs]
    n = len(d)
    out = {"n": n, "mean_difference": (sum(d) / n) if n else None, "method": None, "p_two_sided": None,
           "min_attainable_p": None, "excluded_pairs": len(list(zip(healthy, faulted))) - n}
    if n < 2:
        out["method"] = "none (fewer than 2 complete pairs)"
        return out
    var = sum((x - out["mean_difference"]) ** 2 for x in d) / (n - 1)
    out["sd_difference"] = math.sqrt(var)
    if all(x == 0 for x in d):
        out.update(method="all differences zero", p_two_sided=1.0)
        return out
    if n <= SIGN_FLIP_MAX_N:
        observed = abs(sum(d))
        hits = sum(1 for signs in product((1, -1), repeat=n)
                   if abs(sum(s * x for s, x in zip(signs, d))) >= observed - 1e-12 * max(1.0, observed))
        out.update(method=f"exact sign-flip over 2^{n} patterns", p_two_sided=hits / 2 ** n,
                   min_attainable_p=2 / 2 ** n)
    else:
        from scipy.stats import t as student_t
        se = math.sqrt(var / n)
        tstat = out["mean_difference"] / se if se > 0 else math.inf
        out.update(method="paired Student t", p_two_sided=float(2 * student_t.sf(abs(tstat), n - 1)), t=tstat)
    return out
