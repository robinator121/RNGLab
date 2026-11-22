"""Scientific Model layer: adapters over the canonical implementations, one shared interface.

Every adapter declares its random input (roles, read granularity, alignment), its progress clock,
its defaults, and turns one run into model-specific ``Endpoint`` objects with exact references.
The adapters call the canonical model code unchanged; they never reimplement a sampler. What is
shared is the contract, not the physics: each endpoint keeps its own observable and meaning.

Canonical implementations used:

- PIMC: ``rnglab14.models.pimc_harmonic.PathIntegralHarmonicOscillator.run_simulation``
  (local Metropolis), reference ``exact_finite_bead_energy``; uncertainty from the model's own
  Geyer summary. Interval status other than ESTIMATED counts as an interval failure (the same
  rule as the frozen Stage C protocol, section 2.4).
- Ising: ``rnglab14.models.ising_2d.Ising2D.run_simulation`` (Metropolis), exact reference
  ``rnglab16.core.enumeration`` (L <= 4).
- Nuclear B2: ``rnglab.nuclear.aggregate.simulate_aggregate``; Nuclear Layer A:
  ``rnglab.nuclear.decay.simulate_decays``. Schemes and cases are read from the frozen B2
  protocol file (read only; its namespace is never used). Exact law from ``rnglab.nuclear.bateman``.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from .contracts import Clock, RandomInput
from .measure import Endpoint

REPO_ROOT = Path(__file__).resolve().parents[2]
B2_PROTOCOL = REPO_ROOT / "research" / "v020-nuclear-b2-verify" / "protocol.json"
UNIFORM_RULE = "u = (w >> 11) / 2**53 with w a big-endian 64-bit word"


@dataclass
class UnitOutput:
    endpoints: list[Endpoint]
    observables: dict
    clocks: dict                      # model-specific progress, e.g. {"sweeps": 600}
    model_digest: str | None = None   # the model's own provenance/result hash, if it has one
    notes: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ModelAdapter:
    id: str
    title: str
    implementation: str
    clock: Clock
    defaults: dict
    inputs: dict[str, RandomInput]                 # channel layout -> declared random input
    split_words: bool                              # consumer reads words in bulk
    run: Callable[[dict, dict], UnitOutput]        # (role -> source, params) -> output
    status: str
    scope: str
    role_semantics: dict = field(default_factory=dict)   # role -> which decision its uniforms drive

    def describe(self) -> dict:
        return {"id": self.id, "title": self.title, "implementation": self.implementation, "clock": self.clock.value,
                "defaults": self.defaults, "inputs": {k: v.to_dict() for k, v in self.inputs.items()},
                "split_words": self.split_words, "status": self.status, "scope": self.scope,
                "role_semantics": self.role_semantics}


# ----------------------------------------------------------------------------------------- PIMC
def _pimc(sources: dict, p: dict) -> UnitOutput:
    from rnglab14.models.pimc_harmonic import PathIntegralHarmonicOscillator
    from rnglab14.sources.base import StochasticChannels
    model = PathIntegralHarmonicOscillator(beads=p["beads"], temperature=1.0 / p["beta"], displacement_delta=p["delta"])
    if "shared" in sources:
        arg = sources["shared"]
    else:
        arg = StochasticChannels(proposal=sources["proposal"], acceptance=sources["acceptance"],
                                 initialization=sources["initialization"])
    r = model.run_simulation(arg, sweeps=p["sweeps"], burn_in=p["burn_in"])
    g = (r.virial_uncertainty or {}).get("geyer") or {}
    estimated = g.get("status") == "ESTIMATED"
    interval = tuple(g["interval_95"]) if estimated and g.get("interval_95") else None
    energy = Endpoint(
        model="pimc-harmonic", observable="centroid-virial energy", unit="hbar*omega",
        meaning="mean centroid-virial energy of the retained sweeps; exact value is the finite-bead energy",
        estimate=r.virial_energy_mean, standard_error=g.get("mcse") if estimated else None,
        reference=r.exact_finite_bead_energy, reference_kind="exact",
        reference_source="rnglab14.models.pimc_harmonic.exact_finite_bead_energy",
        interval=interval, interval_method="Geyer initial-monotone MCSE, 95 % normal interval (model's own)",
        extra={"uncertainty_status": g.get("status"), "ess": g.get("ess"), "tau": g.get("tau"),
               "interval_failure": not (estimated and interval is not None
                                        and interval[0] <= r.exact_finite_bead_energy <= interval[1])})
    return UnitOutput([energy], {"acceptance_rate": r.acceptance_rate, "virial_energy_mean": r.virial_energy_mean,
                                 "thermodynamic_energy_mean": r.thermodynamic_energy_mean,
                                 "radius_of_gyration": r.radius_of_gyration},
                      {"sweeps": p["sweeps"]}, r.provenance_hash)


# ----------------------------------------------------------------------------------------- VMC
def _vmc(sources: dict, p: dict) -> UnitOutput:
    from rnglab14.models.vmc_harmonic import HarmonicOscillatorVMC
    from rnglab14.sources.base import StochasticChannels
    model = HarmonicOscillatorVMC(alpha=p["alpha"], delta=p["delta"])
    if "shared" in sources:
        arg = sources["shared"]
    else:
        arg = StochasticChannels(proposal=sources["proposal"], acceptance=sources["acceptance"],
                                 initialization=sources["initialization"])
    r = model.run_simulation(arg, steps=p["steps"], burn_in=p["burn_in"])
    g = (r.x2_uncertainty or {}).get("geyer") or {}
    estimated = g.get("status") == "ESTIMATED"
    interval = tuple(g["interval_95"]) if estimated and g.get("interval_95") else None
    x2 = Endpoint(
        model="vmc-harmonic", observable="<x^2>", unit="length^2",
        meaning="mean of x^2 under the sampled trial density; exact value 1/(4 alpha)",
        estimate=r.x2_mean, standard_error=g.get("mcse") if estimated else None, reference=r.exact_x2,
        reference_kind="exact", reference_source="HarmonicOscillatorVMC.exact_x2",
        interval=interval, interval_method="Geyer MCSE, 95 % normal interval (model's own)",
        extra={"uncertainty_status": g.get("status"),
               "interval_failure": not (estimated and interval is not None and interval[0] <= r.exact_x2 <= interval[1])})
    return UnitOutput([x2], {"acceptance_rate": r.acceptance_rate, "energy_mean": r.energy_mean,
                             "energy_std": r.energy_std, "x2_mean": r.x2_mean},
                      {"steps": p["steps"]}, None,
                      ["at alpha = 0.5 the local energy is constant (zero-variance property): the energy cannot "
                       "reveal a biased sampler, so <x^2> is the endpoint"])


# ----------------------------------------------------------------------------------------- Ising
def _ising(sources: dict, p: dict) -> UnitOutput:
    from rnglab14.models.ising_2d import Ising2D
    from rnglab14.sources.base import StochasticChannels
    from rnglab16.core import enumeration
    model = Ising2D(L=p["L"], temperature=p["temperature"])
    if "shared" in sources:
        arg = sources["shared"]
    else:
        arg = StochasticChannels(proposal=sources["proposal"], acceptance=sources["acceptance"],
                                 initialization=sources["initialization"])
    r = model.run_simulation(arg, sweeps=p["sweeps"], burn_in=p["burn_in"])
    n = p["L"] * p["L"]
    exact = enumeration(p["L"], p["temperature"]) if p["L"] <= 4 else None
    ess = r.effective_sample_size_energy
    se = math.sqrt(r.energy_variance * n) / n / math.sqrt(ess) if ess and ess > 0 else None
    energy = Endpoint(
        model="ising-2d", observable="energy per site", unit="J",
        meaning="mean energy per site of the retained sweeps; exact value by enumeration of all 2^(L*L) states",
        estimate=r.energy_mean, standard_error=se, reference=exact["energy"] if exact else None,
        reference_kind="exact" if exact else "none", reference_source="rnglab16.core.enumeration",
        interval=(r.energy_mean - 1.96 * se, r.energy_mean + 1.96 * se) if se else None,
        interval_method="normal interval with SE from the model's own energy ESS estimate",
        extra={"ess": ess})
    energy.extra["interval_failure"] = energy.covered is False or energy.interval is None
    absm = Endpoint(
        model="ising-2d", observable="|m| per site", unit="1",
        meaning="mean absolute magnetization per site; exact value by enumeration (no SE: the model reports none)",
        estimate=r.magnetization_mean, standard_error=None, reference=exact["abs_magnetization"] if exact else None,
        reference_kind="exact" if exact else "none", reference_source="rnglab16.core.enumeration")
    return UnitOutput([energy, absm], {"acceptance_rate": r.acceptance_rate, "energy_mean": r.energy_mean,
                                       "abs_magnetization_mean": r.magnetization_mean,
                                       "susceptibility_abs_m": r.susceptibility},
                      {"sweeps": p["sweeps"]}, None,
                      ["susceptibility_abs_m uses Var(|m|); it is not the signed-m exact susceptibility"])


# ----------------------------------------------------------------------------------------- nuclear
def _load_case(key: str):
    from rnglab.nuclear import DecayScheme
    protocol = json.loads(B2_PROTOCOL.read_text())
    return DecayScheme.from_dict(protocol["schemes"][key]), protocol["cases"][key]


def _scaled(initial: dict, population: int) -> dict:
    total = sum(initial.values())
    names = list(initial)
    out = {k: (initial[k] * population) // total for k in names}
    out[names[0]] += population - sum(out.values())
    return out


def _parent_endpoint(model_id: str, scheme, initial: dict, final: dict, horizon: float) -> Endpoint:
    """Surviving count of the single start nuclide vs its exact binomial law at the horizon."""
    import numpy as np
    from rnglab.nuclear.bateman import state_probabilities
    starts = [k for k, v in initial.items() if v > 0]
    if len(starts) != 1:
        raise ValueError("the parent endpoint needs exactly one populated start nuclide")
    parent = starts[0]
    names = [n.name for n in scheme.nuclides]
    prob = float(np.clip(state_probabilities(scheme, parent, np.array([0.0, horizon]))[-1, names.index(parent)], 0, 1))
    n0 = initial[parent]
    mean, sd = n0 * prob, math.sqrt(n0 * prob * (1 - prob))
    return Endpoint(model=model_id, observable=f"surviving {parent}", unit="nuclei",
                    meaning=f"number of {parent} nuclei left at t = {horizon:g}; exact law Binomial(N0, p) "
                            "with p from the Bateman solution",
                    estimate=float(final[parent]), standard_error=sd, reference=mean, reference_kind="exact-law",
                    reference_source="rnglab.nuclear.bateman.state_probabilities",
                    extra={"n0": n0, "p_exact": prob, "degenerate": not 1e-9 < prob < 1 - 1e-9,
                           "degenerate_note": "the exact law is (almost) deterministic at this horizon; the endpoint "
                                              "cannot show any fault effect. Choose a horizon near the half-life."})


def _nuclear_b2(sources: dict, p: dict) -> UnitOutput:
    import numpy as np
    from rnglab.nuclear.aggregate import simulate_aggregate
    from rnglab.nuclear.streams import WordStream
    scheme, case = _load_case(p["scheme"])
    initial = _scaled(case["initial"], p["population"])
    times = np.arange(p["intervals"] + 1, dtype=np.float64) * p["step"]
    run = simulate_aggregate(scheme, initial, times, WordStream(sources["decay"]), keep_outputs=False)
    names = [n.name for n in scheme.nuclides]
    final = {n: int(v) for n, v in zip(names, run.final_populations)}
    ep = _parent_endpoint("nuclear-b2", scheme, initial, final, float(times[-1]))
    return UnitOutput([ep], {"final_populations": final, "words_read": run.words_read},
                      {"intervals": p["intervals"], "simulated_time": float(times[-1]), "words": run.words_read},
                      run.digest)


def _nuclear_a(sources: dict, p: dict) -> UnitOutput:
    from rnglab.nuclear.decay import simulate_decays
    from rnglab.nuclear.streams import WordStream
    scheme, case = _load_case(p["scheme"])
    initial = _scaled(case["initial"], p["population"])
    run = simulate_decays(scheme, initial, p["horizon"], WordStream(sources["decay"]))
    names = [n.name for n in scheme.nuclides]
    pops = run.populations([p["horizon"]])
    final = {n: int(v) for n, v in zip(names, pops[-1])}
    ep = _parent_endpoint("nuclear-a", scheme, initial, final, p["horizon"])
    return UnitOutput([ep], {"final_populations": final, "events": int(run.n_events), "words_used": run.words_used},
                      {"simulated_time": p["horizon"], "decay_events": int(run.n_events), "words": run.words_used},
                      run.digest())


_WORD_ROLES = ("proposal", "acceptance", "initialization")
_MCMC_NOTE = "acceptance is read only for uphill moves, so its consumption depends on the state"

MODELS: dict[str, ModelAdapter] = {
    "pimc-harmonic": ModelAdapter(
        "pimc-harmonic", "Harmonic PIMC, local Metropolis", "rnglab14.models.pimc_harmonic", Clock.SWEEPS,
        {"beads": 8, "beta": 2.0, "delta": 0.5, "sweeps": 6000, "burn_in": 1000},
        {"separate": RandomInput(_WORD_ROLES, 8, True, UNIFORM_RULE, ("separate",), _MCMC_NOTE),
         "shared": RandomInput(("shared",), 8, True, UNIFORM_RULE, ("shared",), "one source for every role")},
        False, _pimc, "experimentally-validated",
        "one harmonic model; validated at one parameter point by the frozen v0.18 API validation",
        {"acceptance": "Metropolis accept/reject of uphill bead moves only (read only when dS > 0; count is state-dependent)",
         "proposal": "bead displacement, every move", "initialization": "initial path",
         "shared": "every decision: displacement, uphill acceptance and initial path (proposal becomes asymmetric under a uniform shift)"}),
    "vmc-harmonic": ModelAdapter(
        "vmc-harmonic", "Harmonic VMC, Metropolis", "rnglab14.models.vmc_harmonic", Clock.STEPS,
        {"alpha": 0.5, "delta": 1.0, "steps": 6000, "burn_in": 1000},
        {"separate": RandomInput(_WORD_ROLES, 8, True, UNIFORM_RULE, ("separate",),
                                 "one proposal and one acceptance word per step, always"),
         "shared": RandomInput(("shared",), 8, True, UNIFORM_RULE, ("shared",), "one source for every role")},
        False, _vmc, "unit-tested", "zero-variance energy at alpha = 0.5; <x^2> is the sensitive observable",
        {"acceptance": "Metropolis accept/reject of every step (one word per step, always)",
         "proposal": "walker displacement, every step", "initialization": "not read",
         "shared": "displacement and acceptance alternate on one stream"}),
    "ising-2d": ModelAdapter(
        "ising-2d", "2D Ising, single-spin Metropolis", "rnglab14.models.ising_2d", Clock.SWEEPS,
        {"L": 4, "temperature": 2.5, "sweeps": 4000, "burn_in": 500},
        {"separate": RandomInput(_WORD_ROLES, 8, False, UNIFORM_RULE, ("separate",),
                                 "proposal uses read_int (1 byte for L <= 16): only the acceptance and "
                                 "initialization roles are word-aligned; " + _MCMC_NOTE),
         "shared": RandomInput(("shared",), 1, False, UNIFORM_RULE, ("shared",),
                               "mixed 1-byte and 8-byte reads on one stream")},
        False, _ising, "unit-tested", "exact enumeration only for L <= 4",
        {"acceptance": "Metropolis accept/reject of energy-raising flips only (read only when dE > 0)",
         "proposal": "site choice via read_int (1 byte each for L <= 16); not word-aligned",
         "initialization": "initial spins", "shared": "mixed 1-byte site choices and 8-byte acceptances"}),
    "nuclear-b2": ModelAdapter(
        "nuclear-b2", "Nuclear decay, aggregated populations (B2)", "rnglab.nuclear.aggregate", Clock.INTERVALS,
        {"scheme": "S1", "population": 20000, "intervals": 40, "step": 0.025},   # horizon 1.0 = one half-life
        {"single": RandomInput(("decay",), 8, True, UNIFORM_RULE, ("single",),
                               "WordStream reads 8*n bytes per call; split into single words for word faults")},
        True, _nuclear_b2, "independently-verified", "synthetic decay schemes; non-operational",
        {"decay": "one binomial/multinomial quantile per sub-population and interval (a few uniforms move many nuclei)"}),
    "nuclear-a": ModelAdapter(
        "nuclear-a", "Nuclear decay, event level (Layer A)", "rnglab.nuclear.decay", Clock.EVENTS,
        {"scheme": "S1", "population": 4000, "horizon": 1.0},                   # one half-life of Sa-400
        {"single": RandomInput(("decay",), 8, True, UNIFORM_RULE, ("single",),
                               "WordStream reads 8*n bytes per call; split into single words for word faults")},
        True, _nuclear_a, "independently-verified", "synthetic decay schemes; non-operational",
        {"decay": "per nucleus: decay time, branching choice and emission draws (fixed words per nucleus)"}),
}


def per_role_aligned(model: ModelAdapter, layout: str, role: str) -> bool:
    """Ising's proposal role is not word-aligned even though its acceptance role is."""
    if model.id == "ising-2d" and layout == "separate":
        return role in ("acceptance", "initialization")
    return model.inputs[layout].aligned_words
