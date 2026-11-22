# RNGLab Unified: branch inventory and evidence audit

Status: read-only inventory, 2026-11-22. Reference commit: `e690488` (tip of `feature/criticality-rng-sensitivity`, the base of `feature/unified-suite`). Nothing was checked out, staged, committed, run or deleted to produce this document; branches were inspected with `git log`, `git show`, `git diff --stat`, `git merge-base` and `git rev-list`. Every outcome quoted below names the file it was read from. The machine-readable companion is `docs/unified/capability_matrix.json` (85 entries).

## 1. Branch table

"Unique" counts commits reachable from the branch but not from `e690488`. "Behind" counts commits of `e690488` not on the branch.

| Branch | Tip | Merge-base with `e690488` | Ancestor of `e690488` | Ancestor of `origin/main` | Unique | Behind | Unique content |
|---|---|---|---|---|---:|---:|---|
| `feature/criticality-rng-sensitivity` (= `origin/…`) | `e690488` | `e690488` | yes | no | 0 | 0 | reference commit (nuclear Layer A + B2) |
| `feature/unified-suite` | `5de3f30` at the time of this inventory (later commits add the unified suite) | `e690488` | no (child) | no | 1 | 0 | `gui/` (33 files) and `tests/gui/` (2 files), 5,710 lines: the Laboratory GUI imported unchanged |
| `feature/lab-gui` | `d91cb45` | `d91cb45` | yes | no | 0 | 3 | none committed; its untracked `gui/` is what `5de3f30` imported |
| `feature/control-center` = `feature/v020-stage-d-readiness` | `6b75205` | `6b75205` | yes | no | 0 | 23 | none committed; the worktree holds an **untracked** C++ prototype `ui/` (7 files, about 1,400 lines) |
| `feature/v019-audit` | `cdd9acd` | `cdd9acd` | **yes** | no | 0 | 41 | none: `research/v019-audit`, `src/rnglab/prognosis`, `research/v019-forecast-methods` are all in `e690488` |
| `feature/v019` | `33f701e` | `33f701e` | yes | no | 0 | 52 | none committed; the main checkout has untracked `research/v019-d-pilot/measure.py` and `tests/v019/test_d_pilot_measure.py` (see section 3) |
| `feature/v018` (= `origin/feature/v018`) | `3988c64` | `3988c64` | yes | no | 0 | 54 | none |
| `main` (local) | `697c943` | `697c943` | yes | yes | 0 | 86 | none; one commit behind `origin/main` |
| `origin/main` | `ded1e40` | `de2a5ee` | no | — | 1 | 57 | **none in content**: merge commit "fixed main" (parents `697c943`, `de2a5ee`) whose tree is byte-identical to `de2a5ee` (`git diff de2a5ee ded1e40` is empty). It brings v0.18 Phase 0 to Stage C onto `main` |
| `origin/feature/crypto-blue` | `a0a2c04` | `a0a2c04` | yes | yes | 0 | 87 | none (merged as PR #1 at `697c943`) |
| `origin/phase2-upstream-validation-v1` | `dd96adb` | `dd96adb` | yes | yes | 0 | 89 | none |
| `feature/native-simd-benchmark` | `a3f307b` | `f4ee240` | no | no | **1** | 32 | `research/native-optimization/` (15 files, 1,341 lines incl. C kernels), `tests/native_optimization/` (3 files, 279 lines), `docs/native-optimization/` (73 files, 27,986 lines, mostly JSON evidence) |
| `backup/feature-v018-before-trailer-rewrite-20261106` | `5a2a83b` | `697c943` | no | no | 18 | 86 | record only; tree identical to `33d63f7` (pre-rewrite copy, message differences only) |
| `backup/feature-v018-before-disclosure-rewrite-20261108` | `19f8b04` | `c15e398` | no | no | 22 | 80 | record only; tree identical to `bed4994` |
| `backup/feature-v019-before-clean-20261110` | `5877b95` | `3988c64` | no | no | 2 | 54 | record only; differs from `33f701e` in attribution wording of the frozen Stage D protocol (see section 6) and one log file |
| `backup/feature-v019-audit-before-path-redaction` | `c7e400d` | `3cacdfa` | no | no | 7 | 49 | record only; differs from `cdd9acd` in 4 files, 6 lines (path redaction) |

Worktrees (`git worktree list`): main checkout on `feature/v019`; four worktrees inside the repository (`criticality`, `lab-gui`, `unified`, `v019-audit`, the last one on `feature/control-center`); the native-simd worktree outside the repository under `~/Documents/<workspace>/…/native-simd-worktree`; a detached review snapshot of `5de3f30`; and two detached worktrees in a temporary directory marked **prunable** (`0aca45a`, `33f701e`). Backup branches are records and must never be used as a source.

## 2. DAG summary

History is linear from `1bca13b` to `e690488` (96 first-parent commits) with one merge, `697c943` (PR #1, crypto BLUE).

```text
1bca13b … 16bea1b (rnglab11-17) ─ dd96adb (phase2 upstream) ─ 8501f9a (Phase II evidence)
   └─ 697c943 main  [merge of a0a2c04 crypto-blue]
        └─ 96f773b v0.18 Phase 0 … bed4994 Stage C report ─ de2a5ee ──┬─ ded1e40 origin/main ("fixed main", empty tree diff)
                                                                      └─ c7231fa sensitivity 01 ─ 3988c64 feature/v018
             └─ b33846f Stage D protocol frozen ─ 33f701e feature/v019
                  └─ 4b55378 … cdd9acd feature/v019-audit (linear, not a side branch)
                       └─ 1410860 F-01 … f4ee240 Stage D implementation status ──── a3f307b feature/native-simd-benchmark
                            └─ … 6b75205 feature/control-center = feature/v020-stage-d-readiness
                                 └─ c616a7b Layer A frozen ─ 526e9f7 Layer A results
                                      └─ 50be70d … 25ff551 B2 frozen ─ e0d3b09 ─ 87780dc ─ d91cb45 feature/lab-gui
                                           └─ fffeecc ─ 4f79f6e ─ e690488 feature/criticality-rng-sensitivity
                                                └─ 5de3f30 feature/unified-suite
```

## 3. Unique work not reachable from `e690488`

| Item | Where | State | Recommended handling |
|---|---|---|---|
| Native C/AVX2 optimization laboratory | `feature/native-simd-benchmark` `a3f307b` | committed, not merged. Claims 1.433x (native/SIMD) and 1.474x (buffered) single-worker speed-up and 2.03x/2.15x at 6/8 workers, with 360 timed runs agreeing exactly (`docs/native-optimization/BENCHMARKS.md`, `EQUIVALENCE.md` on that branch) | code-source worker, read from the branch by commit; do not merge into the frozen pilot path. Its loader requires an externally supplied `measure.py` identified by SHA-256 |
| Laboratory GUI | `feature/unified-suite` `5de3f30` | committed on the unified branch only | the integration vehicle itself |
| Control-center C++ prototype (`ui/src/core/{Artifacts,Journal,Npz}.{h,cpp}`, `ui/tests/make_fixture.py`) | untracked in the `feature/control-center` worktree | never committed; at risk of loss | author decides whether to commit it on its own branch before it is used |
| Untracked `research/v019-d-pilot/measure.py` in the main checkout | main checkout (`feature/v019`) | byte-identical to the committed file in `e690488` (SHA-256 `f8d5ac52…1b9be`) | nothing to integrate |
| Untracked `tests/v019/test_d_pilot_measure.py` in the main checkout | main checkout | **differs** from the committed file in `e690488`; the committed one is the later version that moved its sampling to the audit-only namespace (B-12, `docs/v020/STAGE_D_IMPLEMENTATION_STATUS.md`) | treat the committed file as authoritative; the untracked copy predates B-12 |
| `origin/main` "fixed main" | `ded1e40` | merge with no content change | none; local `main` should be fast-forwarded by the author when convenient |

Not unique, contrary to earlier notes: `feature/v019-audit` (`research/v019-audit`, `src/rnglab/prognosis`, `research/v019-forecast-methods`) is an ancestor of `e690488`; `origin/feature/crypto-blue` and `origin/phase2-upstream-validation-v1` are ancestors of both `e690488` and `origin/main`.

## 4. Research lines: positive and negative results

Status words follow `capability_matrix.json`. Only results read in the cited files are quoted.

### 4.1 PIMC uncertainty and channel faults (Phase I, Phase II, v0.18 Phase 2)

- Positive. Phase II confirmation: local Metropolis 811/1,024, centroid refresh 964/1,024, IID control 969/1,024; both healthy controls passed the frozen gates (`research/phase2-20261030/README.md`, `experiment/confirmation-v1/summary.csv`). Through the package API: refresh 3846/4096, IID 3884/4096, local 3323/4096, H-API and H-REP supported, recount 21/21 (`research/v018-api-validation/README.md`).
- Positive. Fault study: healthy 487/512 versus 0/512 (acceptance shift) and 13/512 (coupling) (`research/phase2-20261030/README.md`).
- Negative or limiting. The PIMC interval undercoverage is a **healthy-sampler failure, not an RNG problem**: it persists from exact stationary starts, the slow centroid mode dominates the variance, and short batches miss it (`research/phase2-20261030/README.md`). Package default batches under-cover even with the refresh (0.8611 at b = 32, 0.9260 at b = 128); the batch-length diagnostic accepts every refreshed run but covers 0.8828; a small refresh bias (CV −0.000321) is unexplained (`research/v018-api-validation/README.md`). Monitors: the marginal source check missed coupling, the application-shape monitor missed acceptance shift; no general ranking (`research/phase2-20261030/README.md`). Phase I is historical and "was not evidence of RNG-induced failure" (`research/phase1-20261030/README.md`).

### 4.2 Ising and estimator repair (v0.17 probe, v0.18 Phase 1)

- Positive. Symmetry-aware susceptibility S calibrated in 9/9 cells; long-run estimators calibrated for τ ≤ 19 (H-L supported); batch-length diagnostic 21/21 gates (H-D supported); split-R̂ flags sign non-mixing 2,048/2,048; recount 28/28 (`research/v018-estimators/README.md`).
- Negative. Jackknife G fails 5/9 cells, so H-S is not supported; the plug-in fails (relative bias −0.917 at L4 T1.5); BM(32) covers 0.547 and 0.332 at long τ; the frozen control verdict was FALSE (1 of 20 intervals missed) (`research/v018-estimators/README.md`). The v0.17 healthy-chain susceptibility counterexample (0.745828 against exact 10.403227) is an estimator and mixing failure, not an RNG defect (`docs/calibrate/V017_NEGATIVE_FINDINGS.md`).

### 4.3 v0.16 TRIDENT (forecasting limits)

- Negative. Theory Brier 0.08869430 versus combined 0.08867247 (difference +0.00002183; required improvement 0.02 not reached), and theory was worse out of distribution; the amended endpoint "confounds normal thermal variability and reliability loss"; every conservative policy permitted 0 continuations; healthy M16 PIMC intervals covered 26/32 (`docs/trident/V016_NEGATIVE_FINDINGS.md`).
- Limited positive. 18/24 paired Ising energy contrasts pass Holm; strong acceptance bias makes the magnetised states absorbing (same file). Outputs are off-repo (section 5).

### 4.4 v0.17 CALIBRATE

- `docs/calibrate/HYPOTHESIS_STATUS.json`: H1 "PARTIAL / GLOBAL CRITERION FAILED: 10/12 cells pass"; H2 supported in seven selected cells only; H3 partial; H4 full hypothesis unsupported; H5 unsupported. Healthy PIMC still under-covers; intervention efficacy not established (`docs/calibrate/V017_NEGATIVE_FINDINGS.md`). Outputs off-repo.

### 4.5 Central hypothesis H1: Stage C detection at matched false alarms (v0.18)

- **Negative.** H1-C not supported in any of SHIFT, LAG1, DUP, GFSR; no H1 cell and no undetected-harm cell among 24; the battery alarmed in every run of all 18 synthetic cells; the application arm never alarmed alone and was later by a median of 512 to 5,376 sweeps (`research/v018-h1-detection/README.md`).
- Valid run. All gates pass, V1 3867/4096, recount 319/319 (same file). GFSR including R250 was harmless and quiet.
- Exploratory. Sensitivity 01 (post hoc): without the targeted word tests, LAG1 had up to 186/256 wrong runs with no alarm and LAG1-4 missed the H1 class only by its lower bound (0.496 vs 0.5) (`docs/v018/RESULTS_SUMMARY.md`, `research/v018-h1-detection/SENSITIVITY_01_RESULTS.md`).

### 4.6 Central hypothesis H2: Stage D pilot and forecasting methods (v0.19, v0.20 readiness)

- **Stage D pilot: frozen, not run.** Protocol frozen 2026-11-10, SHA-256 `5329464…5e97`; "No pilot, smoke or campaign run was generated"; assessment "CONDITIONAL GO, for the smoke first"; conditions C-3 to C-12 are open author or operator steps; a smoke pre-generation manifest is committed "for the author's review" (`docs/v020/STAGE_D_READINESS_REPORT.md`, `research/v019-d-pilot/smoke/pre_generation_manifest.json`).
- Defect found. F-01: the frozen noncentral-t oracle call is non-finite at 1,554 of 12,001 grid points (δ 8.53–43.61); documented resolution awaits a decision (`docs/v020/STAGE_D_READINESS_REPORT.md`, `research/v019-audit/results/f01_oracle.json`).
- Forecasting methods study (surrogate, frozen, run once): R1, R2, R3, R7 MET (`research/v019-forecast-methods/results/verdicts.json`); in-family skill 0.228 [0.189, 0.268], but outside the declared family skill −0.256 and −0.218, and 80% failure-time intervals cover 0.45 and 0.52 (`docs/v019/RESEARCH_REPORT.md`).
- Identifiability limit. The RUS counterexample shows identical histories with different futures; any history-based forecaster loses at least E[(p₁ − p₂)²]/4, 0.2488 in the Phase II construction (`docs/v019/RESEARCH_REPORT.md`, `research/phase2-20261030/rus/`).
- Readiness risks. Storage about 22.8 GiB and about 8.8 hours at six workers against earlier extrapolations of 5.49 GiB and 3.93 hours (`docs/v019/RESEARCH_REPORT.md`); the readiness report asks for about 31 GiB free.

### 4.7 Central hypothesis H3 (Stage E)

- Planned only. "Never measured" (`docs/v018/ROADMAP.md` §2); Stage E "stays conditional; build nothing yet" (`docs/v018/STAGE_DE_REVIEW.md`).

### 4.8 Crypto BLUE

- Positive demonstrations. Known 8-bit seeds through conditioner and expander pass three diagnostics in most streams yet are predicted exactly in 128/128; bounded-dictionary key recovery 128/128; ECDSA nonce reuse recovered the private scalar in 128/128 (`docs/crypto/RESULTS.md`).
- **Negative.** The frozen healthy source-monitor criterion was not met: "INSUFFICIENT EVIDENCE (CP95 upper false-alarm bound 0.1194; criterion <=0.10)" (`research/crypto-blue/confirmation-reviewed/RESULTS.md`); SHA256/ChaCha20 missed the preregistered 90% non-rejection criterion (adjusted p = 0.0509588) (`docs/crypto/RESULTS.md`). The campaign "does not establish general security-failure prediction" (same file). The sensitivity study met its four-cell criteria (63/64, 63/64, 64/64, 64/64) without changing the confirmation decision.

### 4.9 Nuclear module (v0.20)

- Layer A: "R1 PASS, R2 MET"; no Holm rejection in NC1 (47 tests) or NC2 (33 tests); 115 unit tests (`docs/v020/nuclear/NUCLEAR_PHYSICS_VALIDATION.md`).
- B2: "R1 PASS, R2 MET; R0: criterion C1 holds for both families"; null false-FAIL 0.0088 (NC1) and 0.0080 (NC2); 202 unit tests (`docs/v020/nuclear/NUCLEAR_B2_VALIDATION.md`).
- Open. BT-NEUTRON in NC1 at 2.52 times the union bound (1.47–4.03) in the calibration; the Xc-400m variance observation is unresolved; RNG-defect sensitivity (Phases E, F), Layers B and C are not started; benchmarks are single-workstation with `git_dirty: true` and the machine in ordinary use (`docs/v020/nuclear/NUCLEAR_B2_VALIDATION.md` §5, `NUCLEAR_B2_BENCHMARK.md`). Both reports state experimental validation is "no, and not applicable" (synthetic schemes).

### 4.10 Physical sources and detector data

- Backed. Detector export re-derived from the raw file: 117,962 records, 108,473 (91.96%) reach the 16-bit ceiling, timestamps are multiples of 80 (`docs/v018/AUDIT.md` R6).
- Limits. No particle identity or entropy per sample established (`docs/physical/MUON_DETECTOR_ASSESSMENT.md`); GM acquisition is a plan (`docs/physical/GM_NEXT_WEEK.md`); a physical SP 800-90B evaluation was never run (`docs/v018/AUDIT.md` R4).

### 4.11 Finance, legacy and unbacked material

- Finance: unit-tested models and the reused-seed fault; the √K deflation is analytic and unmeasured (`docs/v018/AUDIT.md` R8).
- Superseded or unbacked (`docs/v018/AUDIT.md` §2): `docs/validation/SOURCE_COMPARISON.md` (no script; 29–156x throughput overstatement; "all 15" SP 800-22 with a 3-test implementation); v0.14 "0.0% hazardous gap"; CPU/GPU benchmarks (no script or raw timing in Git); "formal proof" for the RUS result (a constructive counterexample); L = 8 exact validation (enumeration exists for L ≤ 4 only).

## 5. Off-repo evidence (author's machine; existence checked with `ls`/`du` only)

| Evidence | Location | Present | Size |
|---|---|---|---|
| v0.16 TRIDENT outputs | `~/<tool-output-dir>/…/rnglab-v016-trident-20261022/` (full path in `docs/v018/AUDIT.md`, "Off-repo evidence roots") | yes | 147 MB |
| v0.17 CALIBRATE outputs | `~/<tool-output-dir>/…/rnglab-v017-calibrate-20261022/` | yes | 933 MB |
| Phase II full outputs incl. 19 NPZ archives | `~/Documents/<workspace>/2026-10-30/…/outputs/phase2-20261030/` | yes | 1.9 GB |
| Raw detector export | `~/rnglab-data/` | yes | 614 MB directory |

`~/rnglab-data/` contains, besides the inventoried export `meas_data_2026-10-19 14.05.19.txt`, a **second** measurement file stamped 14.30.18 and two baseline CSV files. Only the first export is inventoried in the repository (`docs/v018/evidence/detector_inventory.json`); the others have no repository record. Contents were not read.

## 6. Gaps and risks

| # | Gap or risk | Evidence | Consequence | Mitigation |
|---|---|---|---|---|
| G1 | `README.md` at `e690488` still says "v0.18 in progress on branch `feature/v018`", expects 331 tests, and lists only three research packages | `README.md` | front page understates v0.19–v0.20 and the 736 test functions now defined | author-approved README update, separate from frozen material |
| G2 | v0.16, v0.17 and Phase II NPZ outputs exist only off-repo | section 5 | negative results there cannot be re-checked from Git | GUI browses the in-repo summaries only; mark off-repo numbers as such |
| G3 | Stage D pilot has never read a real run; F-01 oracle decision, storage and authorisation open | `docs/v020/STAGE_D_READINESS_REPORT.md` | H2-R has no data | no GUI action may sample the pilot or smoke namespaces |
| G4 | Native optimization not merged, depends on an externally supplied `measure.py` by hash | `feature/native-simd-benchmark` | speed-ups not available in the reference path | keep as a commit-pinned code source; reference stays authoritative |
| G5 | Untracked C++ `ui/` prototype | control-center worktree | loss on worktree cleanup | author decides whether to commit |
| G6 | Frozen Stage D protocol bytes were rewritten before generation (attribution wording); backup `5877b95` holds the earlier SHA-256 `6f805a69…0927` | `git diff 5877b95 33f701e` | two "frozen" hashes exist in history; the one cited everywhere is `5329464…5e97` | cite only `5329464…`; treat the backup as record |
| G7 | BT-NEUTRON calibration tail and Xc-400m variance open | `docs/v020/nuclear/NUCLEAR_B2_VALIDATION.md` §5 | B2 verdict stands but two questions remain | fresh-namespace probe before Phase E |
| G8 | Unbacked documents remain without banners (`SOURCE_COMPARISON.md`, `APPLICATION_RELIABILITY.md`, `docs/REPRODUCIBILITY.md`, benchmarks) | `docs/v018/AUDIT.md` | GUI could present them as results | label as unbacked/superseded in the catalog |
| G9 | CI has never executed (billing lock); Python 3.11/3.12 untested | `README.md`, `docs/v018/AUDIT.md` R21 | test counts are local only | record local evidence files |
| G10 | Phase II channel faults exist only in the standalone study, not in the package | `src/rnglab/faults.py` docstring | no package-level adapter for coupling | read-only browse, or a code-source worker pinned to the study |
| G11 | Second detector file and baseline CSVs uninventoried | section 5 | possible unused evidence | author decides whether to inventory |
| G12 | Two prunable temporary worktrees | `git worktree list` | clutter only | author may prune |

## 7. Recommended integration method per line

| Research line | Method | Reason |
|---|---|---|
| Core sources, faults, models, estimators (`src/rnglab`, `rnglab14`, `rnglab16`, `rnglab17`) | adapter | importable, unit-tested, re-exported by the v0.18 namespace |
| Phase I, Phase II, v0.16, v0.17, v0.18 studies, crypto BLUE studies, nuclear studies | read-only browse | frozen and complete; results must never be regenerated or edited |
| Stage C library, Stage D pilot library, independent audit toolkit | code-source worker pinned to a commit | research code with frozen hashes; Stage D namespaces must not be sampled |
| Stage D pilot and smoke | read-only browse of protocol and readiness documents only | no execution before the author's authorisation |
| Nuclear Layer A and B2 engines | adapter (GUI adapters exist) | importable and independently verified; use engineering namespaces only |
| Native optimization | code-source worker from `a3f307b` | unmerged, harness refuses other namespaces |
| Laboratory GUI | the integration vehicle | on `feature/unified-suite` |
| Control-center C++ prototype, GM acquisition, Layers B/C, Stage E | none yet | not committed or not built |
| Unbacked and superseded documents | read-only browse with a status label | traceability only |

## 8. The ten frozen studies with result files (historical browser)

| Study | Verdict | Result files |
|---|---|---|
| Phase II confirmation | gates passed; healthy-sampler undercoverage | `research/phase2-20261030/experiment/confirmation-v1/summary.csv`, `primary_decision.json` |
| Phase II fault study | 487/512 vs 0/512 and 13/512 | `research/phase2-20261030/experiment/fault-study-v1/summary.csv` |
| Phase II analytic dependence control | short-batch underestimation reproduced | `research/phase2-20261030/analytic-dependence/results.json`, `summary.csv` |
| v0.18 estimators | H-L, H-D supported; H-S not supported | `research/v018-estimators/results/gates.json`, `recount.json` |
| v0.18 API validation | H-API, H-REP supported | `research/v018-api-validation/results/gates.json`, `recount.json` |
| v0.18 Stage C (H1) | H1-C not supported | `research/v018-h1-detection/results/gates.json`, `report_tables.md`, `sensitivity01.json` |
| Crypto BLUE confirmation | demonstrations; healthy-alarm and one non-rejection criterion failed | `research/crypto-blue/confirmation-reviewed/results.json`, `RESULTS.md` |
| v0.19 forecasting methods | R1–R3, R7 MET; fails outside family | `research/v019-forecast-methods/results/verdicts.json`, `results.json` |
| v0.20 nuclear Layer A | R1 PASS, R2 MET | `research/v020-nuclear-a-verify/results/verification.json` |
| v0.20 nuclear B2 | R1 PASS, R2 MET, C1 holds | `research/v020-nuclear-b2-verify/results/verification.json`, `calibration.json` |

The Stage D pilot (`research/v019-d-pilot/protocol.json`) is frozen but has no result file; it belongs in the browser as "frozen, not run".
