"""Model catalog: what the laboratory can run, as data.

Nothing here imports scientific code. Each entry names the RNGLab function a
worker calls, the files a code source must contain, the parameters (scientific
or execution), what one step means, and the panels of its dedicated view. The
worker adapter with the same id does the work (``worker/adapters``).
"""
from __future__ import annotations

from .params import Param
from . import DEFAULT_NAMESPACE

# --------------------------------------------------------------------------------------------
# Shared parameter blocks
# --------------------------------------------------------------------------------------------
GENERATORS = (
    ("numpy-pcg64", "NumPy PCG64 raw words (rnglab.nuclear.streams.NumpyPCG64Source)"),
    ("pcg64", "PCG64, pure Python (rnglab14.sources.PCG64Source)"),
    ("xoshiro256pp", "xoshiro256++, pure Python (rnglab14.sources.Xoshiro256PlusPlusSource)"),
    ("mt19937", "MT19937 through Python's random (rnglab14.sources.MersenneTwisterSource)"),
    ("chacha20", "ChaCha20, pure Python, slow (rnglab14.sources.ChaCha20Source)"),
    ("os-entropy", "OS entropy (rnglab14.sources.OSEntropySource): not replayable"),
)
GENERATOR_REQUIRES = {
    "numpy-pcg64": ["src/rnglab/nuclear/streams.py"],
    "pcg64": ["src/rnglab14/sources/reference.py"],
    "xoshiro256pp": ["src/rnglab14/sources/reference.py"],
    "mt19937": ["src/rnglab14/sources/reference.py"],
    "chacha20": ["src/rnglab14/sources/reference.py"],
    "os-entropy": ["src/rnglab14/sources/reference.py"],
}

H1_LIB = "research/v018-h1-detection/h1lib.py"
H1_GATES = "research/v018-h1-detection/results/gates.json"


def source_params(default_generator: str = "pcg64", layouts: tuple = ()) -> list[Param]:
    out = [
        Param("generator", "enum", default_generator, "Generator", "Random source", choices=GENERATORS,
              help="Healthy base generator. Every random number the model uses passes through its "
                   "RandomSource.read_bytes boundary."),
        Param("namespace", "str", DEFAULT_NAMESPACE, "Seed namespace", "Random source",
              help="Seeds are rnglab.seeds.namespace_seed(namespace, label, role). Interactive runs may only "
                   "use namespaces starting with 'rnglab-gui-'; study and reserved namespaces are refused."),
        Param("label", "str", "run-1", "Run label", "Random source",
              help="Second part of every seed label. The same namespace and label reproduce a run bit for bit."),
    ]
    if layouts:
        first = layouts[0][0] if isinstance(layouts[0], tuple) else layouts[0]
        out.append(Param("channels", "enum", first, "Channel layout", "Random source", choices=layouts,
                         help="shared: one source feeds every role (the API's single-source call). "
                              "separate: StochasticChannels with one seeded source per role."))
    return out


def degraded_params(group: str = "Fault injection") -> list[Param]:
    vis = {"fault": ["degraded"]}
    return [
        Param("bias_delta", "float", 0.02, "Bias delta", group, -0.49, 0.49, step=0.005, visible_if=vis,
              help="DegradedFaultSource.bias_delta. Without rho: every byte shifted by round(delta*255) and "
                   "clipped. With rho: P(bit = 1) = 0.5 + delta."),
        Param("rho", "float", 0.0, "Bit autocorrelation rho", group, -1.0, 1.0, step=0.05, visible_if=vis,
              help="Lag-1 Markov correlation of contiguous emitted bits (reads 8 base uniforms per byte)."),
        Param("stuck_bit", "enum", "none", "Stuck bit index", group, visible_if=vis,
              choices=("none", "0", "1", "2", "3", "4", "5", "6", "7"),
              help="Bit of every emitted byte forced to the stuck value (0 = least significant)."),
        Param("stuck_value", "enum", "1", "Stuck value", group, visible_if=vis, choices=("0", "1")),
        Param("periodic_freq", "float", 0.0, "Periodic coupling frequency", group, 0.0, 0.5, step=0.001,
              visible_if=vis, help="Cycles per emitted byte of the additive sinusoidal byte shift."),
        Param("periodic_amp", "float", 0.0, "Periodic coupling amplitude", group, 0.0, 0.5, step=0.005,
              visible_if=vis),
    ]


def h1_fault_params(group: str = "Fault injection") -> list[Param]:
    vis = {"fault": ["h1-word"]}
    return [
        Param("h1_family", "enum", "SHIFT", "Word fault family", group, visible_if=vis,
              choices=(("SHIFT", "SHIFT: u -> min(u + s, 1 - 2^-53)"),
                       ("LAG1", "LAG1: Gaussian-copula AR(1) with rho = s"),
                       ("DUP", "DUP: previous word repeated with probability s"),
                       ("GFSR", "GFSR: words from a lagged-Fibonacci XOR generator"))),
        Param("h1_severity", "float", 0.02, "Severity s", group, 0.0, 0.99, step=0.001, visible_if=vis,
              help="Shift for SHIFT, rho for LAG1, duplication probability for DUP. Ignored for GFSR."),
        Param("gfsr_pair", "enum", "250,103", "GFSR lags (p,q)", group, visible_if=vis,
              choices=("31,13", "89,38", "127,63", "250,103", "521,168", "1279,418")),
        Param("onset_bytes", "int", 0, "Onset (bytes)", group, 0, 2**40, visible_if=vis,
              help="The adapter passes words unchanged until this many bytes have been emitted."),
    ]


def execution_params(default_frame_ms: int = 100) -> list[Param]:
    g = "Execution"
    return [
        Param("frame_ms", "int", default_frame_ms, "Telemetry interval", g, 20, 5000, scientific=False, unit="ms",
              help="Wall-clock interval between telemetry frames. Does not change the computation."),
        Param("delay_ms", "float", 0.0, "Delay per step", g, 0.0, 5000.0, scientific=False, unit="ms",
              help="Wall-clock pause after each step (slows the run down to watch it). Simulation time is unaffected."),
        Param("start_paused", "bool", False, "Start paused", g, scientific=False,
              help="Pause at the first step boundary."),
    ]


def fault_selector(choices: tuple) -> Param:
    return Param("fault", "enum", "none", "Fault adapter", "Fault injection", choices=choices,
                 help="Fault adapters are RandomSources wrapping the healthy source. 'none' runs the healthy source.")


FAULT_NONE = ("none", "none (healthy source)")
FAULT_DEGRADED = ("degraded", "DegradedFaultSource (rnglab.faults): bias, bit rho, stuck bit, periodic")
FAULT_H1 = ("h1-word", "FaultyWords (research/v018-h1-detection/h1lib.py): SHIFT, LAG1, DUP, GFSR with onset")

STREAM_PANELS = [
    {"id": "rng_hist", "type": "hist", "title": "Consumed uniforms", "source": "hist:uniform",
     "cls": "derived", "note": "Uniforms (word >> 11)·2⁻⁵³ of every 8-byte read, per role"},
    {"id": "rng_lag", "type": "density", "title": "Lag-1 pairs (uᵢ, uᵢ₊₁)", "source": "density:lag1",
     "cls": "derived"},
    {"id": "rng_bits", "type": "bars", "title": "Bit frequency by position", "source": "bars:bits",
     "cls": "derived", "note": "Fraction of ones at each bit of the consumed bytes (0 = LSB); 0.5 expected"},
]

# --------------------------------------------------------------------------------------------
# Models
# --------------------------------------------------------------------------------------------
MODELS: list[dict] = [
    {
        "id": "pimc-harmonic",
        "title": "Harmonic path-integral Monte Carlo",
        "domain": "Monte Carlo · quantum statistical mechanics",
        "call": "rnglab.models.PathIntegralHarmonicOscillator.run_simulation (rnglab14.models.pimc_harmonic)",
        "summary": "Local Metropolis on a ring polymer of M beads, optional exact centroid heat bath. "
                   "The workload of the Phase II result and of the H1 campaign.",
        "requires": ["src/rnglab14/models/pimc_harmonic.py", "src/rnglab12/uncertainty.py"],
        "unit": "sweep",
        "controls": {"pause": True, "step": True, "speed": True, "stop": True},
        "observation": "RandomSource tap on every role; model state (path, counters, energy lists) read "
                       "from the running sampler between random draws; record_trace=True for replay.",
        "params": [
            Param("beads", "int", 16, "Beads M", "Model", 2, 256),
            Param("beta", "float", 2.0, "Inverse temperature β", "Model", 0.05, 64.0, step=0.1,
                  help="Passed as temperature = 1/β, as the H1 library does."),
            Param("delta", "float", 0.4, "Proposal half-width δ", "Model", 1e-3, 10.0, step=0.05),
            Param("sweeps", "int", 8192, "Sweeps", "Run length", 33, 2_000_000),
            Param("burn_in", "int", 0, "Burn-in sweeps", "Run length", 0, 1_000_000),
            Param("initialization", "enum", "exact_gaussian", "Initialization", "Model",
                  choices=(("exact_gaussian", "exact finite-M Gaussian path"), ("uniform", "uniform in [-0.5, 0.5)"))),
            Param("centroid_refresh", "bool", False, "Exact centroid heat bath", "Model",
                  help="harmonic_centroid_heatbath after every sweep, from its own 'centroid' source."),
            Param("batch_size", "int", 512, "Batch size (BM interval)", "Model", 2, 1_000_000,
                  help="uncertainty_batch_sizes=(b,) of summarize_series; also used for the live prefix interval."),
            *source_params("pcg64", layouts=(("shared", "shared: one source"), ("separate", "separate channels"))),
            fault_selector((FAULT_NONE, FAULT_DEGRADED, FAULT_H1)),
            Param("fault_role", "enum", "proposal", "Faulted role", "Fault injection",
                  choices=("proposal", "acceptance", "initialization", "centroid"),
                  visible_if={"fault": ["degraded", "h1-word"]},
                  help="With the shared layout the single local source is faulted, whatever is chosen here, "
                       "except 'centroid'."),
            *degraded_params(), *h1_fault_params(),
            *execution_params(),
        ],
        "references": ["exact finite-M energy (references.exact_finite_bead_energy)",
                       "continuum energy ½·coth(β/2)"],
        "panels": [
            {"id": "path", "type": "ring", "title": "Ring polymer (bead positions)", "source": "state:path",
             "cls": "simulated", "note": "x_j of the M beads in imaginary time; read from the sampler"},
            {"id": "cv", "type": "timeseries", "title": "Centroid-virial energy per retained sweep",
             "series": ["cv"], "refs": ["finite_bead_energy"], "cls": "simulated", "x": "sweep"},
            {"id": "ci", "type": "interval", "title": "Running mean and batch-means 95% interval",
             "source": "series:ci", "refs": ["finite_bead_energy", "continuum_energy"], "cls": "derived",
             "x": "sweep", "note": "summarize_series on the retained prefix; final interval is the official one"},
            {"id": "td", "type": "timeseries", "title": "TD − CV estimator difference", "series": ["d"],
             "cls": "derived", "x": "sweep", "note": "Exactly zero in expectation for the harmonic model"},
            {"id": "acc", "type": "timeseries", "title": "Acceptance rate (cumulative)", "series": ["acc"],
             "cls": "simulated", "x": "sweep", "ylim": [0, 1]},
            {"id": "centroid", "type": "timeseries", "title": "Centroid x̄ and radius of gyration",
             "series": ["centroid", "rg"], "cls": "simulated", "x": "sweep"},
            *STREAM_PANELS,
        ],
        "compare_series": ["ci_mean", "cv", "acc", "d"],
    },
    {
        "id": "h1-detection",
        "title": "H1 detection run (PIMC + word faults + monitors)",
        "domain": "RNG degradation · application-aware detection (H1)",
        "call": "research/v018-h1-detection/h1lib.py: FaultyWords, SweepClock, conventional_window, "
                "application statistics; PathIntegralHarmonicOscillator.run_simulation",
        "summary": "One run of the Stage C design: harmonic PIMC (M = 16, β = 2, δ = 0.4) from an exact start, "
                   "a word fault from an onset byte, the conventional battery on the consumed bytes and the two "
                   "application monitors, against the frozen calibration thresholds.",
        "requires": [H1_LIB, H1_GATES, "src/rnglab14/models/pimc_harmonic.py", "src/rnglab11/statistics.py"],
        "unit": "sweep",
        "controls": {"pause": True, "step": True, "speed": True, "stop": True},
        "observation": "The run is composed exactly as h1lib.run_one composes it, with transparent taps; the "
                       "monitor windows are computed with h1lib's own functions as soon as their sweeps exist, "
                       "and the final record is recomputed from the complete run and compared.",
        "params": [
            Param("family", "enum", "SHIFT", "Fault family", "Fault injection",
                  choices=("healthy", "SHIFT", "LAG1", "DUP", "GFSR")),
            Param("severity", "float", 0.019036539387158782, "Severity", "Fault injection", 0.0, 0.99, step=0.001,
                  help="Default: SHIFT level 3 of the frozen grid. Ignored for healthy and GFSR."),
            Param("gfsr_pair", "enum", "250,103", "GFSR lags (p,q)", "Fault injection",
                  choices=("31,13", "89,38", "127,63", "250,103", "521,168", "1279,418"),
                  visible_if={"family": ["GFSR"]}),
            Param("onset_bytes", "int", 524288, "Onset (bytes)", "Fault injection", 0, 2**40,
                  help="Study value 2^19."),
            Param("sweeps", "int", 10240, "Sweeps", "Run length", 1024, 200_000,
                  help="Study value 10240; harm is evaluated only when sweeps >= 10240."),
            Param("namespace", "str", DEFAULT_NAMESPACE, "Seed namespace", "Random source"),
            Param("label", "str", "h1-run", "Condition label", "Random source",
                  help="h1lib.seeds_for(namespace, label, replica) derives the five role seeds."),
            Param("replica", "int", 0, "Replica index", "Random source", 0, 99999),
            *execution_params(),
        ],
        "references": ["exact finite-M energy", "frozen thresholds τ_C, τ_A and application constants "
                       "(results/gates.json of the H1 study)"],
        "panels": [
            {"id": "monC", "type": "monitor", "title": "Conventional battery: −log₁₀ min p", "series": ["statC"],
             "threshold": "tau_C", "cls": "derived", "x": "sweep"},
            {"id": "monA", "type": "monitor", "title": "Application monitor: max |z|", "series": ["statA"],
             "threshold": "tau_A", "cls": "derived", "x": "sweep"},
            {"id": "pmap", "type": "pmatrix", "title": "Battery p-values per window", "source": "matrix:conv_p",
             "cls": "derived"},
            {"id": "path", "type": "ring", "title": "Ring polymer", "source": "state:path", "cls": "simulated"},
            {"id": "ci", "type": "interval", "title": "Running mean and BM512 interval", "source": "series:ci",
             "refs": ["finite_bead_energy"], "cls": "derived", "x": "sweep"},
            {"id": "uphill", "type": "timeseries", "title": "Uphill-proposal fraction per sweep",
             "series": ["uphill"], "cls": "derived", "x": "sweep",
             "note": "From the byte counter at each sweep boundary (SweepClock marks)"},
            *STREAM_PANELS,
        ],
        "compare_series": ["statC", "statA", "ci_mean", "uphill"],
    },
    {
        "id": "pi-mc",
        "title": "π by hit-or-miss Monte Carlo",
        "domain": "Monte Carlo · sampling",
        "call": "rnglab14.models.pi_mc.run_pi_monte_carlo",
        "summary": "Pairs of uniforms in the unit square; the fraction inside the quarter circle estimates π/4.",
        "requires": ["src/rnglab14/models/pi_mc.py"],
        "unit": "sample",
        "controls": {"pause": True, "step": True, "speed": True, "stop": True},
        "observation": "The sample points are the uniforms read through the tap (the same bytes and the same "
                       "conversion as RandomSource.random_float); the running count is the function's own "
                       "'inside' variable, checked against the reconstruction at every frame.",
        "params": [
            Param("samples", "int", 200_000, "Samples", "Run length", 100, 20_000_000),
            *source_params("numpy-pcg64"),
            fault_selector((FAULT_NONE, FAULT_DEGRADED, FAULT_H1)),
            *degraded_params(), *h1_fault_params(),
            *execution_params(),
            Param("points_kept", "int", 20000, "Points drawn", "Execution", 100, 200000, scientific=False,
                  help="How many recent sample points the scatter keeps (display only)."),
        ],
        "references": ["π"],
        "panels": [
            {"id": "scatter", "type": "scatter", "title": "Sample points in the unit square", "source": "state:points",
             "cls": "simulated", "note": "Most recent points; inside the quarter circle in colour"},
            {"id": "est", "type": "interval", "title": "Running estimate and 95% interval", "source": "series:ci",
             "refs": ["pi"], "cls": "derived", "x": "sample", "note": "4·p̂ ± 1.95996·4·√(p̂(1−p̂)/n), the "
             "function's own interval formula, at each prefix"},
            {"id": "err", "type": "timeseries", "title": "|estimate − π| and the 1/√n scale", "series": ["abs_err",
             "se"], "cls": "derived", "x": "sample", "logx": True, "logy": True},
            *STREAM_PANELS,
        ],
        "compare_series": ["ci_mean", "abs_err"],
    },
    {
        "id": "ising-2d",
        "title": "2D Ising model (Metropolis, source API)",
        "domain": "Monte Carlo · statistical physics",
        "call": "rnglab.models.Ising2D.run_simulation (rnglab14.models.ising_2d)",
        "summary": "Random-site Metropolis on an L×L periodic lattice; sites from read_int, acceptance from "
                   "random_float.",
        "requires": ["src/rnglab14/models/ising_2d.py", "src/rnglab16/core.py"],
        "unit": "sweep",
        "controls": {"pause": True, "step": True, "speed": True, "stop": True},
        "observation": "Lattice and the sampler's own running energy, magnetisation and acceptance counters, "
                       "read between random draws.",
        "params": [
            Param("L", "int", 16, "Lattice size L", "Model", 2, 64),
            Param("temperature", "float", 2.269, "Temperature T", "Model", 0.05, 20.0, step=0.01),
            Param("sweeps", "int", 600, "Sweeps", "Run length", 2, 1_000_000),
            Param("burn_in", "int", 100, "Burn-in sweeps", "Run length", 0, 1_000_000),
            *source_params("pcg64", layouts=(("shared", "shared: one source"), ("separate", "separate channels"))),
            fault_selector((FAULT_NONE, FAULT_DEGRADED)),
            Param("fault_role", "enum", "acceptance", "Faulted role", "Fault injection",
                  choices=("proposal", "acceptance", "initialization"), visible_if={"fault": ["degraded"]}),
            *degraded_params(),
            *execution_params(),
        ],
        "references": ["exact enumeration for L ≤ 4 (references.ising_exact_enumeration)"],
        "panels": [
            {"id": "lattice", "type": "lattice", "title": "Spin lattice", "source": "state:spins",
             "cls": "simulated"},
            {"id": "energy", "type": "timeseries", "title": "Energy per spin", "series": ["e"],
             "refs": ["exact_energy"], "cls": "simulated", "x": "sweep"},
            {"id": "mag", "type": "timeseries", "title": "|m| per sweep", "series": ["absm"],
             "refs": ["exact_abs_m"], "cls": "simulated", "x": "sweep", "ylim": [0, 1]},
            {"id": "acc", "type": "timeseries", "title": "Acceptance rate (cumulative)", "series": ["acc"],
             "cls": "simulated", "x": "sweep", "ylim": [0, 1]},
            *STREAM_PANELS,
        ],
        "compare_series": ["e", "absm", "acc"],
    },
    {
        "id": "decision-support",
        "title": "Monte Carlo decision support",
        "domain": "Decision support under uncertainty",
        "call": "rnglab.models.MonteCarloDecisionSupport.evaluate_decisions (rnglab14.models.decision_support)",
        "summary": "Three options with Gaussian payoffs; the highest estimated mean is chosen. Replicates with "
                   "common seeds compare a healthy and a faulted source, or sweep a fault's bias.",
        "requires": ["src/rnglab14/models/decision_support.py", "src/rnglab17/endpoints.py"],
        "unit": "replicate",
        "controls": {"pause": True, "step": True, "speed": True, "stop": True},
        "observation": "One official evaluate_decisions call per (condition, replicate); within a call the "
                       "payoff lists of the running function are read between draws.",
        "params": [
            Param("design", "enum", "paired", "Design", "Model",
                  choices=(("paired", "healthy vs faulted, common seeds"), ("sweep", "bias sweep, common seeds"))),
            Param("replicates", "int", 40, "Replicates per condition", "Run length", 2, 5000),
            Param("scenarios", "int", 2000, "Scenarios per replicate", "Run length", 10, 1_000_000),
            Param("mean_a", "float", 100.0, "Option A mean", "Model"),
            Param("vol_a", "float", 15.0, "Option A volatility", "Model", 0.0),
            Param("mean_b", "float", 120.0, "Option B mean", "Model"),
            Param("vol_b", "float", 40.0, "Option B volatility", "Model", 0.0),
            Param("mean_c", "float", 105.0, "Option C mean", "Model"),
            Param("vol_c", "float", 35.0, "Option C volatility", "Model", 0.0),
            Param("sweep_levels", "int", 6, "Sweep levels", "Model", 2, 41, visible_if={"design": ["sweep"]}),
            Param("sweep_max", "float", 0.1, "Largest bias delta", "Model", 0.0, 0.49,
                  visible_if={"design": ["sweep"]}),
            *source_params("numpy-pcg64"),
            Param("fault", "enum", "degraded", "Fault adapter (faulted condition)", "Fault injection",
                  choices=(FAULT_DEGRADED, FAULT_H1)),
            *degraded_params(), *h1_fault_params(),
            *execution_params(),
        ],
        "references": ["true means of the options (the decision rule's target)"],
        "panels": [
            {"id": "matrix", "type": "matrix", "title": "Selection frequency (condition × option)",
             "source": "matrix:selection", "cls": "derived"},
            {"id": "ranks", "type": "matrix", "title": "Rank frequency (condition/option × rank)",
             "source": "matrix:ranks", "cls": "derived"},
            {"id": "means", "type": "estimates", "title": "Estimated means per condition (mean ± 2 s.d. "
             "over replicates)", "source": "table:estimates", "cls": "derived"},
            {"id": "pcorrect", "type": "rate", "title": "P(correct decision) with Clopper-Pearson 95%",
             "source": "table:rates", "cls": "derived"},
            {"id": "live", "type": "timeseries", "title": "Running option means inside the current replicate",
             "series": ["live_a", "live_b", "live_c"], "cls": "simulated", "x": "scenario", "transient": True},
            {"id": "regret", "type": "hist", "title": "Decision regret per condition", "source": "hist:regret",
             "cls": "derived"},
        ],
        "compare_series": [],
    },
    {
        "id": "nuclear-aggregate",
        "title": "Radioactive decay, aggregated populations (B2)",
        "domain": "Nuclear decay · population level",
        "call": "rnglab.nuclear.aggregate.simulate_aggregate (sink callback)",
        "summary": "Exact multinomial interval laws over decay-path categories, interval by interval in "
                   "simulated time; Bateman references draw no random numbers.",
        "requires": ["src/rnglab/nuclear/aggregate.py", "src/rnglab/nuclear/bateman.py",
                     "research/v020-nuclear-b2-verify/protocol.json"],
        "unit": "interval",
        "controls": {"pause": True, "step": True, "speed": True, "stop": True},
        "observation": "The engine's own sink receives a detached copy of every interval's record; pausing "
                       "blocks inside the sink, between intervals. The source is not tapped: the engine records "
                       "its source's class (replay identity) in the run's provenance, so a tap would change it.",
        "params": [
            Param("scheme", "enum", "S2", "Decay scheme", "Model",
                  choices=("S1", "S2", "S3", "S4", "S5"),
                  help="Synthetic schemes of the frozen B2 verification protocol (read only)."),
            Param("population", "int", 0, "Initial population (0 = protocol case)", "Model", 0, 2**62,
                  help="Distributed over the case's start nuclides in the case's proportions."),
            Param("horizon", "float", 0.0, "Horizon (0 = case's last time)", "Model", 0.0, 1e7),
            Param("intervals", "int", 200, "Intervals", "Run length", 1, 100_000),
            *source_params("numpy-pcg64"),
            fault_selector((FAULT_NONE, FAULT_DEGRADED)),
            *degraded_params(),
            *execution_params(),
        ],
        "references": ["Bateman expected populations (rnglab.nuclear.bateman)",
                       "exact binomial 95% band when one nuclide is populated at t = 0"],
        "panels": [
            {"id": "chain", "type": "chain", "title": "Decay scheme", "source": "meta:scheme",
             "cls": "reference"},
            {"id": "pops", "type": "populations", "title": "Populations vs Bateman expectation",
             "source": "series:pops", "cls": "simulated", "x": "time"},
            {"id": "z", "type": "timeseries", "title": "Standardised deviation from the expectation",
             "series": ["z_*"], "cls": "derived", "x": "time", "note": "(n − Np)/√(Np(1−p)) per nuclide"},
            {"id": "decays", "type": "stack", "title": "Decays per interval by channel", "source": "series:decays",
             "cls": "simulated", "x": "time"},
            {"id": "cons", "type": "timeseries", "title": "Conservation: Σ populations − N", "series": ["cons"],
             "cls": "derived", "x": "time"},
        ],
        "compare_series": ["cons"],
    },
    {
        "id": "nuclear-events",
        "title": "Radioactive decay, event level (Layer A)",
        "domain": "Nuclear decay · individual events",
        "call": "rnglab.nuclear.decay.simulate_decays",
        "summary": "Every nucleus follows its decay path with a fixed word layout (3 words per step); events "
                   "carry time, parent, daughter, channel, mode, photon and neutron flags and lineage.",
        "requires": ["src/rnglab/nuclear/decay.py", "src/rnglab/nuclear/bateman.py",
                     "research/v020-nuclear-b2-verify/protocol.json"],
        "unit": "chunk",
        "controls": {"pause": True, "step": True, "speed": True, "stop": True},
        "observation": "The engine samples nucleus by nucleus in chunks, not in time order; progress is shown "
                       "per chunk, and the completed run is played back in simulated time from its own "
                       "event table.",
        "params": [
            Param("scheme", "enum", "S2", "Decay scheme", "Model", choices=("S1", "S2", "S3", "S4", "S5")),
            Param("population", "int", 20000, "Nuclei", "Model", 1, 2_000_000),
            Param("horizon", "float", 0.0, "Horizon (0 = case's last time)", "Model", 0.0, 1e7),
            Param("chunk", "int", 1000, "Chunk (nuclei)", "Execution", 1, 1_000_000, scientific=False,
                  help="simulate_decays(chunk=...). The fixed layout makes results identical for every chunk "
                       "size (checked by the module's tests and by this laboratory's tests)."),
            *source_params("numpy-pcg64"),
            fault_selector((FAULT_NONE, FAULT_DEGRADED)),
            *degraded_params(),
            *execution_params(),
        ],
        "references": ["Bateman expected populations", "exponential waiting-time law (truncated at 53 bits)"],
        "panels": [
            {"id": "chain", "type": "chain", "title": "Decay scheme", "source": "meta:scheme", "cls": "reference"},
            {"id": "progress", "type": "timeseries", "title": "Events per chunk", "series": ["chunk_events"],
             "cls": "simulated", "x": "nucleus"},
            {"id": "pops", "type": "populations", "title": "Populations vs Bateman expectation",
             "source": "series:pops", "cls": "simulated", "x": "time"},
            {"id": "raster", "type": "raster", "title": "Emissions in simulated time", "source": "events:emissions",
             "cls": "simulated", "note": "Playback of the completed run's emission table"},
            {"id": "wait", "type": "hist", "title": "Waiting times vs exponential law", "source": "hist:waiting",
             "cls": "simulated"},
        ],
        "compare_series": [],
    },
]

MODEL_INDEX = {m["id"]: m for m in MODELS}


def requirements() -> dict[str, list[str]]:
    reqs = {m["id"]: list(m["requires"]) for m in MODELS}
    for gen, files in GENERATOR_REQUIRES.items():
        reqs[f"generator:{gen}"] = files
    return reqs


def public(model: dict) -> dict:
    out = {k: v for k, v in model.items() if k != "params"}
    out["params"] = [p.to_dict() for p in model["params"]]
    return out


def get(model_id: str) -> dict:
    if model_id not in MODEL_INDEX:
        raise KeyError(f"unknown model {model_id}")
    return MODEL_INDEX[model_id]
