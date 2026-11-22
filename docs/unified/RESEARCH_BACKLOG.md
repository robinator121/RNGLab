# Research backlog and HPC readiness

Order follows implementation speed, not publication value. Effort: S (days), M (one to two weeks),
L (weeks to months). "Gate" is what must hold before any result from the line is used as evidence.
Status words are the evidence classes of `src/rnglab_unified/taxonomy.py`; nothing below is a
finding.

| # | Direction | Branch / code | Status | Eng. / valid. effort | Exact or independent reference | Data | HPC need | Falsifiable question (example) | Acceptance gate |
|---|---|---|---|---|---|---|---|---|---|
| 1 | Finite-state Markov, Ising | `rnglab16` (exact enumeration, exact transition matrix), `rnglab_core` Ising adapter | unit-tested | S / S | enumeration L ≤ 4; exact transition matrix | none | low | Does a fault that leaves the stationary law unchanged alter spectral gap and detection latency? | exact kernel vs. simulated transition counts; healthy calibration |
| 2 | Nuclear Layer A / B2 | `rnglab.nuclear`, adapters | independently verified (frozen A and B2 verifications) | S / S | Bateman, binomial laws | none | medium | Is the aggregated engine's application error visible before the battery alarms? | frozen protocol at matched false alarm |
| 3 | Quantum jumps | `flagship/q-quantum`, `rnglab_quantum.two_level` | unit-tested | done / S | Lindblad exponential, Torrey, Bloch ODE (all agree) | none | low–medium | Do timing and channel faults bias photon statistics at rates conventional tests miss? | healthy calibration at scale; independent review |
| 4 | Langevin / Kramers escape | — | planned | M / M | Kramers rate (high barrier), Fokker–Planck numerics | none | medium | Escape-rate bias under source faults vs. step-size error | converged FP reference; step-size study |
| 5 | Neutrino oscillations (vacuum PMNS) | — | planned | S / S | closed-form vacuum probabilities | PDG parameters | low | Mostly deterministic; limited stochastic content | analytic agreement |
| 6 | Alpha decay (WKB, barrier) | — | planned | M / M | Gamow/WKB, numerical tunnelling | nuclear data tables | low | Sensitivity of half-life estimates to barrier model | benchmark nuclides |
| 7 | Radiative transport (1D) | — | planned | M / M | slab transmission analytic limits | none | medium–high | Transmission bias under faults vs. variance-reduction choices | analytic limits; convergence |
| 8 | Cosmology baseline (Flagship X) | `flagship/x-cosmology`, `rnglab_cosmo.background` | unit-tested (analytic cases, two integration routes) | done / M | EdS, de Sitter, Mattig; independent routes | official DESI BAO and SN products (not yet fetched, versions to pin) | medium–high | Can sampler or source faults create an apparent preference for evolving dark energy? | reproduce published baseline constraints first |
| 9 | Anharmonic / double-well PIMC (Flagship Q3) | — | planned | M / L | converged finite-bead reference (matrix numerics) | none | high | Observable-dependent undercoverage from slow sector exploration | independent converged reference |
| 10 | Detector dead time (Flagship N) | `flagship/n-nuclear`, `rnglab_detector.deadtime` | unit-tested (synthetic) | done / S | exact finite-time expectations (renewal sum, closed form) | none (real data unconfirmed) | low | Does uncorrected dead time bias rate and entropy estimates of physical streams? | real-data importer under authorisation |
| 11 | Neutron branching (counting) | — | planned | M / M | exact small-model moments (generating functions) | none | medium | Feynman-alpha / variance-to-mean bias from faults vs. detector effects | exact moments; conceptual label (not a reactor model) |
| 12 | Pure U(1) lattice gauge | — | planned | L / L | strong/weak coupling expansions, small-volume exact | none | high | Topological freezing vs. source faults | known plaquette values |
| 13 | Worldline Schwinger | — | planned | L / L | restricted analytic results | none | high | — | independent verification |
| 14 | Kerr photon transport | — | planned | L / L | geodesic invariants (E, L, Carter constant) | none | high | — | invariant conservation and convergence |

Work assignment per line is recorded in the private engineering notes; scientific claims and publication remain the owner's decision.

## HPC readiness (no submission without the owner's approval)

| Capability | Status |
|---|---|
| Versioned experiment specification, validation, sealed records, replay | implemented (`rnglab_core.spec`, `engine`, `record`) |
| Stream partitioning | `namespace_seed` (SHA-256 of labels) seeds one PCG64 stream per role and replicate. Distinct seeds do not prove independent streams. Before large campaigns, move to NumPy `SeedSequence.spawn` keys or counter-based Philox keys per replica. **Gap.** |
| Checkpoint / restart | only nuclear B2 (`simulate_aggregate(resume_from=…)`). Engine-level resume of interrupted campaigns: **gap.** |
| Parallel execution | one unit at a time per process. A worker pool over (model, condition, replicate) units: **gap.** |
| Chunked binary output | JSON units only. Chunked NPZ/Parquet per worker: **gap.** |
| Resource estimation | unit count only. Bytes, CPU time and memory per unit from a pilot: **gap.** |
| Scheduler adapter (Slurm/Komondor) | **gap.** The Komondor constraints (no Python 3.14 on the host, Lustre inode quotas, test partition limits) are recorded in the owner's notes. An Apptainer image would be needed. |
| Cross-hardware reproducibility | replay compares digests on the same machine. Cross-hardware tolerances per observable: **gap.** |

Campaigns Q-HPC, N-HPC and X-HPC each need the gaps above closed, a local pilot, a convergence
check, a frozen protocol and the owner's approval before any submission.
