"""Fault Injection and Degradation layer: one registry, canonical implementations reused unchanged.

A fault family is implemented once, as a ``RandomSource`` wrapper, and applied to any model whose
``RandomInput`` contract it is compatible with (``contracts.compatible``). Families whose meaning
depends on model internals are registered as model-specific and are refused by the shared layer.

Canonical implementations:

- byte/bit faults: ``rnglab14.sources.faults.DegradedFaultSource`` (re-exported as
  ``rnglab.faults.DegradedFaultSource``). Active from the first byte (no onset parameter).
  Markov mode (``rho``) draws 8 base uniforms per emitted byte, so the faulted role consumes
  64 base bytes per emitted byte: the consumption ratio is recorded.
- word faults SHIFT, LAG1, DUP and the GFSR substitution: ``FaultyWords`` in the frozen study
  library ``research/v018-h1-detection/h1lib.py``, loaded read-only by path. Defined on aligned
  big-endian 64-bit words after an onset in consumed bytes; the implementation itself refuses
  non-word reads after onset.
- seed reuse between two roles (``seed-reuse``): implemented here, generically, as a source-
  management fault: the faulted role is given the *same seed* as a partner role. It needs the
  ``separate`` channel layout. It is the independence violation behind the finance reused-seed fault.
- Phase II acceptance-uniform shift and proposal/acceptance coupling: implemented only inside the
  frozen standalone study (``research/phase2-20261030/experiment/fault_study.py``) on the model's own
  arrays; registered as model-specific.
"""
from __future__ import annotations

import importlib.util
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from rnglab14.sources.base import RandomSource

from .contracts import FaultContract, StreamLevel

REPO_ROOT = Path(__file__).resolve().parents[2]
H1LIB = REPO_ROOT / "research" / "v018-h1-detection" / "h1lib.py"


def h1lib():
    """The frozen Stage C library, loaded read-only from its file (never copied or edited)."""
    name = "rnglab_core_h1lib"
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, H1LIB)
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return sys.modules[name]


@dataclass(frozen=True)
class FaultFamily:
    contract: FaultContract
    build: Callable[..., RandomSource] | None     # (base, params, seed_for) -> RandomSource; None = model-specific
    status: str


def _degraded(base: RandomSource, params: dict, seed_for: Callable[[str], int]) -> RandomSource:
    from rnglab14.sources.faults import DegradedFaultSource
    return DegradedFaultSource(base, bias_delta=float(params.get("bias_delta", 0.0)),
                               autocorrelation_rho=float(params.get("rho", 0.0)),
                               stuck_bit_index=params.get("stuck_bit"),
                               stuck_bit_value=int(params.get("stuck_value", 1)),
                               periodic_coupling_freq=float(params.get("periodic_freq", 0.0)),
                               periodic_coupling_amp=float(params.get("periodic_amp", 0.0)))


def _h1(family: str):
    def build(base: RandomSource, params: dict, seed_for: Callable[[str], int]) -> RandomSource:
        lib = h1lib()
        if family == "GFSR":
            severity = tuple(int(v) for v in params["gfsr_pair"])
        else:
            severity = float(params["severity"])
        return lib.FaultyWords(base, family, severity, seed_for("fault"), seed_for("gfsr"),
                               onset_bytes=int(params["onset_bytes"]))
    return build


_ONSET = {"onset_bytes": {"unit": "consumed bytes of the faulted role", "range": [0, None],
                          "meaning": "the fault acts on words emitted after this many bytes"}}

FAULTS: dict[str, FaultFamily] = {
    "byte-bias": FaultFamily(FaultContract(
        "byte-bias", (StreamLevel.BYTE,), parameters={"bias_delta": {"unit": "fraction of 255", "range": [-0.49, 0.49],
                                                                       "meaning": "every byte shifted by round(delta*255), clipped"}},
        semantics="constant additive byte shift from the first byte; every consumer sees biased bytes",
        origin="rnglab14.sources.faults.DegradedFaultSource"), _degraded, "unit-tested"),
    "stuck-bit": FaultFamily(FaultContract(
        "stuck-bit", (StreamLevel.BIT,), parameters={"stuck_bit": {"unit": "bit index 0-7", "range": [0, 7]},
                                                      "stuck_value": {"unit": "0 or 1", "range": [0, 1]}},
        semantics="one bit position of every byte forced to a value from the first byte",
        origin="rnglab14.sources.faults.DegradedFaultSource"), _degraded, "unit-tested"),
    "bit-markov": FaultFamily(FaultContract(
        "bit-markov", (StreamLevel.BIT,), parameters={"rho": {"unit": "lag-1 bit correlation", "range": [-0.99, 0.99]}},
        semantics="emitted bits form one Markov chain; consumes 8 base uniforms (64 base bytes) per emitted byte",
        origin="rnglab14.sources.faults.DegradedFaultSource"), _degraded, "unit-tested"),
    "SHIFT": FaultFamily(FaultContract(
        "SHIFT", (StreamLevel.WORD64, StreamLevel.UNIFORM), requires_aligned_words=True,
        parameters={"severity": {"unit": "additive shift of u", "range": [0, 1]}, **_ONSET},
        semantics="after onset, u = (w>>11)/2^53 of each word becomes min(u + severity, 1-2^-53); low 11 bits kept",
        origin="research/v018-h1-detection/h1lib.py:FaultyWords (frozen)"), _h1("SHIFT"), "independently-verified"),
    "LAG1": FaultFamily(FaultContract(
        "LAG1", (StreamLevel.WORD64, StreamLevel.UNIFORM), requires_aligned_words=True,
        parameters={"severity": {"unit": "AR(1) coefficient of the Gaussian copula", "range": [0, 1]}, **_ONSET},
        semantics="after onset, uniforms are replaced by Phi(z_t), z_t = rho z_{t-1} + sqrt(1-rho^2) Phi^-1(u_t)",
        origin="research/v018-h1-detection/h1lib.py:FaultyWords (frozen)"), _h1("LAG1"), "independently-verified"),
    "DUP": FaultFamily(FaultContract(
        "DUP", (StreamLevel.WORD64,), requires_aligned_words=True,
        parameters={"severity": {"unit": "probability", "range": [0, 1]}, **_ONSET},
        semantics="after onset, each word is replaced by the previous emitted word with probability severity",
        origin="research/v018-h1-detection/h1lib.py:FaultyWords (frozen)"), _h1("DUP"), "independently-verified"),
    "GFSR": FaultFamily(FaultContract(
        "GFSR", (StreamLevel.WORD64,), requires_aligned_words=True,
        parameters={"gfsr_pair": {"unit": "lag pair (p, q)", "range": None}, **_ONSET},
        semantics="after onset, words come from a lagged-Fibonacci GFSR generator (a real weak generator) "
                  "instead of the healthy source",
        origin="research/v018-h1-detection/h1lib.py:FaultyWords/GFSR (frozen)"), _h1("GFSR"), "independently-verified"),
    "seed-reuse": FaultFamily(FaultContract(
        "seed-reuse", (StreamLevel.CHANNEL_PAIR,), requires_channels=2,
        parameters={"partner_role": {"unit": "role name", "range": None,
                                     "meaning": "the faulted role receives this role's seed"}},
        semantics="two roles receive identical streams (independence violation); applied at seed assignment",
        origin="rnglab_core.faults (generic); finance reused-seed fault is the same violation"), None, "implemented"),
    "phase2-acceptance-shift": FaultFamily(FaultContract(
        "phase2-acceptance-shift", (StreamLevel.TRANSITION,), requires_model_internals=True,
        semantics="shift of the acceptance uniforms inside the standalone Phase II PIMC arrays",
        origin="research/phase2-20261030/experiment/fault_study.py (frozen, model-internal)"), None, "experimentally-validated"),
    "phase2-coupling": FaultFamily(FaultContract(
        "phase2-coupling", (StreamLevel.TRANSITION,), requires_model_internals=True,
        semantics="proposal/acceptance coupling inside the standalone Phase II PIMC arrays",
        origin="research/phase2-20261030/experiment/fault_study.py (frozen, model-internal)"), None, "experimentally-validated"),
    "stage-d-ramp": FaultFamily(FaultContract(
        "stage-d-ramp", (StreamLevel.WORD64, StreamLevel.UNIFORM, StreamLevel.TRANSITION), requires_aligned_words=True,
        requires_model_internals=True,
        semantics="progressive SHIFT/LAG1/DUP whose severity r(t - t0) ramps with the PIMC sweep clock, which "
                  "dlib.SweepClock reads from the 16-byte centroid draws; needs the PIMC sweep structure",
        origin="research/v019-d-pilot/dlib.py (frozen protocol; the pilot is not run)"), None, "independently-verified"),
    "crypto-operation-faults": FaultFamily(FaultContract(
        "crypto-operation-faults", (StreamLevel.TRANSITION,), requires_model_internals=True,
        semantics="reduced entropy, exposure, seed reuse, fixed nonce, rollback, starvation, ... indexed by "
                  "cryptographic operation over a ReplaySource (HMAC-DRBG) base; does not wrap arbitrary sources",
        origin="src/rnglab_crypto/faults/__init__.py:FaultAdapter"), None, "unit-tested"),
    "finance-innovation-faults": FaultFamily(FaultContract(
        "finance-innovation-faults", (StreamLevel.TRANSITION,), requires_model_internals=True,
        semantics="duplicated paths, biased or correlated innovations, gradual degradation applied to normals "
                  "inside FinancialRNGProvider subclasses",
        origin="src/rnglab_finance/faults/injection.py"), None, "unit-tested"),
}

# Fault code that exists on other source interfaces and is NOT reachable from the shared layer
# (different base class or byte order). Kept as historical implementations, listed for the atlas.
NOT_INTEGRATED = {
    "rnglab11-byte-faults": "SourceFaultSource / DegradationSource on rnglab11.ByteSource (read/provenance ABC)",
    "rnglab12-uniform-faults": "apply_uniform_fault on uniform arrays (rows x channels)",
    "rnglab16-array-faults": "bias, drift, abrupt, periodic, coupled, correlation on preallocated uniform arrays",
    "legacy-bit-faults": "rnglab.degradation.Fault on legacy bit providers (little-endian, 32-bit uniforms)",
}


def describe() -> list[dict]:
    return [{**f.contract.to_dict(), "status": f.status, "shared": f.build is not None or f.contract.family == "seed-reuse"}
            for f in FAULTS.values()]


def wrap(family: str, base: RandomSource, params: dict, seed_for: Callable[[str], int]) -> RandomSource:
    f = FAULTS[family]
    if f.build is None:
        raise ValueError(f"{family} is not a stream wrapper (it is applied at seed assignment or is model-specific)")
    return f.build(base, params, seed_for)
