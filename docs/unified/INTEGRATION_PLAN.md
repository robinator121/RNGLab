# RNGLab Unified: integration plan and architecture reassessment

The first unified-suite pass built a workbench that launched separate programs. This document
records the second pass, which turns RNGLab into one research framework. The shared package
`src/rnglab_core` implements the integration. The workbench drives that package, and every
compound experiment produces one record. Frozen studies, their protocols, seeds and result bytes
are untouched. Historical implementations remain importable and serve as reproducibility
references.

## 1. Audit: what the code base already shares, duplicates and keeps apart

Two read-only audits were made of `src/`, `research/` and the laboratory adapters. The conclusions
that drive the design:

**Truly reusable, used as-is (canonical):**
- The audited consumption boundary `rnglab14.sources.base.RandomSource.read_bytes` (byte count
  and SHA-256 of every emitted byte) and `random_float`, which converts the top 53 bits of a
  big-endian 64-bit word to a uniform. `rnglab.sources` re-exports it.
- Reference generators in `rnglab14.sources.reference`, `rnglab.nuclear.streams.NumpyPCG64Source`,
  and the conditioners in `rnglab14.sources.conditioning`.
- Faults:
  - `rnglab14.sources.faults.DegradedFaultSource`: byte and bit faults.
  - The frozen `research/v018-h1-detection/h1lib.py:FaultyWords` (SHIFT, LAG1, DUP, GFSR). It is
    loaded read-only by path, never copied.
- Models:
  - `rnglab14` PIMC, VMC and Ising.
  - `rnglab.nuclear.decay.simulate_decays` (Layer A) and `rnglab.nuclear.aggregate.simulate_aggregate` (B2).
- Exact references:
  - `exact_finite_bead_energy`
  - VMC `exact_x2`
  - `rnglab16.core.enumeration`
  - `rnglab.nuclear.bateman`
- Statistics:
  - `rnglab14.statistics.multitest` (Holm)
  - `rnglab17.endpoints.rate` (exact binomial)
  - Stage C battery `h1lib.conventional_window`
  - `rnglab.seeds.namespace_seed`

**Duplicates (one canonical choice; the others stay as history):**
- Three source base classes (`rnglab11.ByteSource`, `rnglab13…RandomSource`,
  `rnglab14…RandomSource`). rnglab14 is canonical.
- Two PCG64 implementations that produce different streams for the same integer seed: the
  pure-Python rnglab14 class, and NumPy with SeedSequence. Both are registered as distinct
  sources and are never interchanged.
- Little-endian and 32-bit uniform conventions in `rnglab11` and legacy `providers`.
- Von Neumann and hash conditioners in four packages.
- Clopper–Pearson in eight places.
- `FaultyWords` in three places (h1lib, smoke timing, Stage D `dlib` ramp variant).
- Model copies: rnglab13, the v02–v04 matrix-driven models, and the vectorised rnglab16 models.

**Scientifically essential differences (kept, not abstracted away):**
- The Phase II standalone PIMC draws two uniforms per bead on one stream. The rnglab14 PIMC reads
  acceptance only for uphill moves. Their random sequences are not comparable, and the validated
  coverage evidence belongs to the former.
- The Ising proposal uses `read_int`: one byte for L ≤ 16, with rejection otherwise. Word-defined
  faults are therefore meaningless on that role.
- The nuclear engines read words in bulk. B2 reads a few words per interval for a whole population.
- `DegradedFaultSource` Markov mode reads 8 base uniforms per emitted byte. GFSR stops consuming
  the base after onset.
- Crypto faults are indexed by cryptographic operation on an HMAC-DRBG base. Finance faults act on
  normals inside provider subclasses. Stage D ramps follow the PIMC sweep clock.

**Composable:** any model that reads through the rnglab14 boundary can be combined with any stream
fault whose contract it satisfies. Today that means PIMC, VMC, Ising (per role), nuclear Layer A
and B2, all from one experiment specification.

**New contracts that were needed:** stream level and word alignment per role, channel count,
model-internal flag, a progress clock per model, model-specific endpoints with exact references,
and a typed experiment specification.

## 2. Layers and module boundaries

| Layer | Module | Owns | Reuses |
|---|---|---|---|
| A. Random source and entropy | `rnglab_core.sources` | registry with separate fields for determinism, statistical quality, entropy claim, unpredictability and independence; `RecordedStreamSource` (file replay, SHA-256 checked, refuses to wrap around) | rnglab14 sources, NumPy PCG64 source |
| | `rnglab_core.streams` | per-role seeds `namespace_seed(ns, model, role, rep)`; `common` or `independent` pairing; `WordSplitter` (bulk → single-word reads, byte-identical); `InterventionSource` (online monitor + authorised switch) | `rnglab.seeds` |
| B. Faults | `rnglab_core.faults`, `rnglab_core.contracts` | one registry. Shared stream faults: byte-bias, stuck-bit, bit-markov, SHIFT, LAG1, DUP, GFSR, seed-reuse. Registered as model-specific (refused by the shared layer, with the reason): Phase II channel faults, Stage D ramp, crypto operation faults, finance innovation faults. Listed as not integrated: rnglab11/12/16 and legacy fault code | `DegradedFaultSource`, frozen `FaultyWords` |
| C. Models | `rnglab_core.models` | `ModelAdapter`: declared random input per layout, clock, defaults, `run(sources, params) -> UnitOutput` with model-specific `Endpoint`s and exact references | canonical model code, called unchanged |
| D. Monitoring and statistics | `rnglab_core.measure` | `Endpoint` (z against the exact reference, two-sided p, coverage), paired effects, rates, Holm, the Stage C battery with a stated rule | rnglab14 multitest, rnglab17 rate, h1lib battery |
| E. Experiment and provenance | `rnglab_core.spec`, `rnglab_core.engine`, `rnglab_core.record` | typed spec and validation (contracts, namespaces, authorisation); engine (healthy, faulted, intervened arms); analysis and cross-domain table; atomic, sealed records; code identity (SHA-256 of every loaded RNGLab module, including the frozen h1lib); replay with digest comparison | — |
| F. Workbench | `gui/rnglab_gui/experiments.py`, `worker/experiment.py`, `static/js/experiments.js` | builder with live contract validation, worker jobs that execute the shared API, live progress, record view, replay | existing laboratory server, guards and charts |

**Dependency rules:**
- `rnglab_core` depends on the canonical packages and never the reverse, so historical code is
  unchanged.
- The laboratory server imports only the stdlib-only `rnglab_core.record`.
- Scientific code runs only in worker processes, from the selected code source. A test asserts
  that the server imports neither NumPy nor scientific modules.
- No new third-party dependency was added. The locked environment is unchanged.

## 3. Integrity rules and how they are enforced

| Rule | Enforcement |
|---|---|
| Frozen protocols and result bytes are never modified | Nothing under `research/` was written. A test requires every recorded protocol freeze to verify. h1lib is loaded from its committed bytes, and a test compares it with `git show HEAD:`. |
| No silent change of random-consumption sequences | Regression tests: the PIMC adapter reproduces three frozen golden cases (provenance hash, energies, per-role byte counts and digests). Registry faults emit the same bytes as direct construction. `WordSplitter` leaves the Layer A and B2 digests unchanged. Ising and VMC adapters equal direct calls. |
| Distinguish scientific replay from API compatibility | Replay compares spec, stream and result digests and the module digest. Same digests with changed code are reported as `warning`, not `ok`. |
| Integration-induced numerical changes recorded | None was introduced, because the adapters call the canonical code. The new composition layers (splitter, recording, intervention) are pass-through, and the tests prove it. |
| Frozen-study generation requires approval | Experiment namespaces must start with `rnglab-unified-` or `rnglab-gui-`. Reserved namespaces and every namespace declared in `research/*/protocol.json` are refused. There is a cap of 512 replicates. |
| Interventions need authorisation | The spec must say `authorized: true`. Otherwise validation fails. |
| Incompatible faults are not generalised | Contracts are checked per faulted role, with an explanation for every accept or refuse. Model-internal families are refused by the shared layer. |
| Alarms are not proof of compromise | Stated in the record (`interpretation_limits`), in the interface, and in each rule description. |
| Forecasts are conditional | The forecast states its declared model and is checked against the unintervened arm. |
| Failures are kept | Failed units are recorded with their error. Cancelled experiments are sealed as cancelled. Faults that never reach their onset, and degenerate exact laws, produce warnings. |

## 4. Cross-domain workflows: status

| Example | Status | Where |
|---|---|---|
| 1. Same degradation family against several models | **Implemented and exercised.** SHIFT on acceptance uniforms (PIMC, VMC, Ising) or decay uniforms (Layer A, B2), each judged against its own exact reference in its own clock | `engine.analyse`, cross-domain table |
| 2. Battery detection vs application correctness | **Implemented.** Per unit and model: battery alarm on the consumed bytes vs harm (interval failure or reference deviation), as a 2×2 table; healthy-arm alarm rate | `detection_vs_harm` |
| 3. Detection → forecast → intervention → recovery | **Implemented, minimal.** Online battery monitor; conditional exposure forecast under a declared persistent-step model, checked against the faulted arm; authorised switch to an independent source; measured recovery of the endpoint shift. The forecasters in `rnglab.prognosis` are not yet connected (they operate on surrogate traces). | `streams.InterventionSource`, `engine._intervention_analysis` |
| 4. Recorded or physical stream as an alternative source | **Implemented in the engine, not yet in the builder.** `source = "recorded-file"` with `source_params` (path, SHA-256 verified at validation and at open, bytes per stream). Every model, role and replicate stream gets a disjoint segment, so no byte is reused. A segment that runs out ends its unit with a recorded error. No entropy claim is made: the record states "unassessed". The validation requirement for physical data is the declared one: the file's digest, segment sizes and the healthy-arm battery result. The workbench does not offer arbitrary paths. A launch-time `--recorded-source` flag, like `--detector-file`, is the next step. | `spec.source_params`, `sources.RecordedStreamSource` |
| 5. Physical-event simulation vs detector-response models | **Not implemented.** No detector-response model exists in the repository. The nuclear engines produce event lists, and `rnglab15`/crypto audits read recorded waveforms. Connecting them needs a detector-response model with its own validation. Planned. | — |

## 5. Next steps (shortest path)

1. Offer recorded streams in the builder for files named at launch (consent), and add the
   detector-file conditioning path (`rnglab14.sources.conditioning`) as an explicit source stage.
2. Adapters for decision support, π, finance (after fixing `get_source_digest`) and crypto BLUE
   (a source-injection hook in its campaign).
3. Connect `rnglab.prognosis` forecasters to monitor traces under declared models (Example 3, full).
4. A study-level protocol mode for frozen campaigns: freeze the spec digest before generation, and
   require the author's approval to run.
5. A detector-response model (Example 5), validated on its own before any comparison.
