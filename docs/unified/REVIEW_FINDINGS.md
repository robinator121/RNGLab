# RNGLab Unified: review findings and adjudication

Four independent reviews were made of the unified-suite work. Each worked read-only on a fixed
snapshot:

- a code and numerical review of the imported laboratory (snapshot `5de3f30`);
- a design critique of the interface;
- a scientific red-team review of the capability matrix and the interface (snapshot `e63e2cf`);
- a final review of the code diff `5de3f30..e63e2cf` and of the operator documents.

Every finding was checked against the code or the evidence before it was acted on. This document
records the decisions, including the points that were rejected. Frozen studies, protocols and result
files were not changed.

## 1. New finding about existing evidence (no change made)

**Phase II `analytic-dependence/SHA256SUMS` does not match one file.** The new manifest checker
reads that sub-manifest line by line. 18 entries match, 3 name off-repo trace archives (`*-CV-traces.npz`,
reported as unavailable), and 1 does not match:

| file | digest in `analytic-dependence/SHA256SUMS` | digest of the committed file |
|---|---|---|
| `analytic_dependence_report.md` | `15735a18…a3c193` | `dc37ddae…a27fd58` |

The file has a single commit (`8501f9a`, 2026-10-30), and the committed content has never matched
the sub-manifest. The study's top-level `research/phase2-20261030/SHA256SUMS` records `dc37ddae…`,
and all 160 of its entries verify. Reading: the report was edited after the sub-manifest was written
and before publication, and the top-level manifest was regenerated while the sub-manifest was not.
It is not a change after publication. The author decides whether to note this beside the study.
The study's files were left untouched.

## 2. Accepted and fixed

| # | Area | Finding | Fix |
|---|---|---|---|
| 1 | verification | an earlier attempt's `result_direct.json` could make a failed re-verification report `ok` | file deleted before each attempt; `ok` requires the attempt to complete; regression test |
| 2 | verification | concurrent requests could run two verifiers in one directory, or a finaliser could read the next attempt's result | a run is exclusively "verifying" until its record is written |
| 3 | verification | unknown Git identities compared equal (`None == None`); adapter edits were not part of "same code" | `ok` requires known, clean and unchanged identities of the science checkout and of the laboratory checkout; otherwise `warning` |
| 4 | capacity | the launch limit was checked without reserving slots; verifications bypassed it | atomic check and registration; verifications count |
| 5 | capacity | a worker that failed to start, or a failing status write, left a run counted as active forever | slot freed in both cases |
| 6 | input | negative `Content-Length` blocked a handler thread | refused with 400 |
| 7 | input | `h1_run` accepted a path as cell name | validated like `h1_runs`; test asserts the explicit error |
| 8 | storage | symlinked run directories were followed | refused |
| 9 | API | stored arrays with NaN or infinity broke the JSON encoder; `max=0` divided by zero | served as null; bounded |
| 10 | evidence | symlinked study folders and files, and manifest entries such as `../../.env`, were followed or hashed | links never followed; manifest names outside `research/` reported, not read; tests |
| 11 | evidence | a file was labelled "historical" by its location alone | historical only when tracked by Git and unchanged; otherwise "untracked" or "modified since commit" |
| 12 | interface | an alert claimed that runs record the diff digest of a dirty checkout | text corrected: only the dirty-file count is recorded |
| 13 | adapter (B2) | band check always `ok`; zero-variance mismatch shown as z = 0 | band check labelled descriptive with no gate; ±∞ for a zero-variance mismatch |
| 14 | adapter (Layer A) | lineage check covered the first 200,000 events but claimed all | all events checked |
| 15 | adapter (Ising) | Var(\|m\|) susceptibility shown next to the exact signed-m susceptibility | note added; summary key renamed `susceptibility_abs_m` |
| 16 | adapter (H1) | the gates file was described as frozen without its digest | SHA-256 of `results/gates.json` reported |
| 17 | matrix | batch-length diagnostic marked validated; its own report says it must not be used for the refreshed PIMC sampler | negative result, with the AR(1) support stated |
| 18 | matrix | v0.18 estimator study marked validated with `controls_valid` false and H-S not supported | negative result (mixed); see open decision D1 |
| 19 | matrix | standalone Phase II PIMC said "both healthy controls passed"; local Metropolis failed its gates on a healthy RNG | corrected; independently verified (exact finite-M reference) |
| 20 | matrix | Phase II channel faults: independent check not named | reference named (byte-identical pre-onset sweeps) |
| 21 | matrix | crypto BLUE sensitivity: missing "no security assurance" | added |
| 22 | interface | Atlas rows showed status and outcome without scope | scope and limits shown in every row |
| 23 | interface | status glyphs (✓✓, ✓, ⊘, !) implied an ordering | one neutral shape per class |
| 24 | interface | a frozen study without results (Stage D pilot) did not say "not run" | evidence class and "no results: not run" shown |
| 25 | manifests | `SHA256SUMS` files were not checked, only `*.sha256` | both checked (this found §1) |
| 26 | interface | every archived run failed to open: the player's change callback fired before the player was assigned | callback guarded (a defect in the imported code) |

## 3. Rejected or not acted on, with reasons

- **"H1 adapter labels unavailable uncertainty as wrong."** Rejected. That is the frozen protocol's
  definition (`research/v018-h1-detection/PROTOCOL.md` §2.4 and `AMENDMENT_01.md`: a non-`ESTIMATED`
  status counts as wrong). The adapter must not redefine it.
- **"Replay compares only a terminal digest; two different ensembles could coincide."** Rejected as
  stated. The digest is a SHA-256 over the full canonical result payload (arrays included), not over a
  scalar. Replay does not compare telemetry streams; that limitation is documented.
- **"6 of 13 studies fail their hashes."** Incorrect. Those 6 have no `protocol.sha256`; none fail.
  Phase II freezes through `SHA256SUMS`, which is now checked (160/160 ok, plus §1).
- **PIMC mean marker and the batch interval use different prefixes for non-divisible sample counts.**
  Not fixed in this branch. Listed as an open display issue.
- **Telemetry is not deterministic (wall-clock frame timing).** By design. Replay uses the official
  result digest.

## 4. Open decisions for the author

- **D1.** The taxonomy has no "mixed" label. Studies with some hypotheses supported and some gates
  failed (v0.18 estimators) are recorded as `negative-result` to avoid over-claiming. Should a
  `mixed` label be added?
- **D2.** Whether to add a note beside Phase II about the sub-manifest mismatch in §1.
- **D3.** Whether the stale top-level `README.md` (still "v0.18 in progress") should be updated. It
  was not changed here.

## 5. Second phase: shared scientific core (`rnglab_core`), red-team review of commit 54b2e56

An independent scientific review challenged whether the shared abstractions keep their statistical
and physical meaning. Fixes are in commit `897d275`, and the interface follows in `c04b646`. Each
fix is pinned by a test in `tests/unified/test_core_engine.py`, unless another test is named.

| # | Finding | Severity | Disposition | Pinned by |
|---|---|---|---|---|
| F1 | Units whose own uncertainty estimate broke (often the most harmed ones) dropped out of the paired effect and of Holm | high | fixed: paired tests use raw estimates; an interval that cannot be estimated counts as harm | `test_units_whose_uncertainty_broke_stay_in_the_paired_test` |
| F2 | Normal-approximation p-values on a handful of pairs (Holm p = 0.0) | high | fixed: exact sign-flip test up to 16 pairs, with the smallest attainable p stated; paired t test above that | `test_paired_test_is_exact_for_few_pairs` |
| F3 | Healthy PIMC interval miscalibrated at the default settings (9/40 failures) | high | mitigated: longer default runs; a warning when the healthy harm interval lies above α; harm reported as excess over the healthy arm. The estimator itself (canonical code) is unchanged. | `test_healthy_miscalibration_and_inactive_faults_are_reported` |
| F4 | "Same SHIFT" drives different decisions in each model | high | addressed: role semantics declared per model and shown in validation, record and interface; a note for the shared layout. Not a dose equivalence. | adapter declarations |
| F5 | Onset in bytes is not a comparable time; a fault past the end showed as a zero effect | medium | fixed: onset shown as a share of each run; models where the fault never acted are reported as not tested | as F3 |
| F6/F7 | Unknown, out-of-range or unaligned fault parameters accepted; onset accepted for faults without one | medium/low | fixed: strict validation | `test_fault_parameters_are_validated_strictly`, `test_byte_faults_refuse_an_onset_they_do_not_have` |
| F8 | The "forecast" used information not available at the alarm | medium | fixed: relabelled as a post hoc counterfactual exposure; forecast status "not implemented" | `test_intervention_needs_authorisation_and_switches_source` |
| F9 | Recovery fraction without uncertainty, sign hidden, pre-onset alarms included | medium | fixed: signed ratio, only for a significant effect, bootstrap interval, pre-onset alarms excluded, per-window false-alarm rate | as F8 |
| F10 | Detection-versus-harm table without a healthy baseline; harm rule differed silently | medium | fixed: table for both arms; harm rule recorded per model | `tests/unified/test_gui_schema.py` |
| F11 | Different experiments in one namespace reused identical streams | medium | fixed: a stream key from the scientific content of the spec enters every seed label | `test_different_specs_get_fresh_streams_and_replays_the_same` |
| F12 | Battery window not comparable across models | low | reported: the window's share of each run is recorded and shown | record field |
| F13 | Code identity depended on what the process had imported | low | fixed: fixed file list, including the frozen h1lib and the B2 protocol | `test_record_is_stamped_exploratory_and_code_identity_is_fixed` |
| F14 | Exploratory runs could be mistaken for evidence | high | fixed: every record and view carries the exploratory evidence status; implementation status is kept apart | as F13 |

**Open, from the design review of the integration plan:**
- Under Metropolis rejection, paired arms decorrelate once their accept decisions diverge. Common
  random numbers then give less variance reduction. The paired test remains valid under its
  symmetry assumption, but the gain is model-dependent. A variance-ratio study (paired vs.
  independent pairing) is listed as a next step.
- A byte onset is not physically comparable across models. A comparison normalised by each model's
  integrated autocorrelation time is not implemented.
- Statements of the kind "the application sees it before the battery" require both detectors to be
  calibrated to the same false-alarm rate on healthy streams. The current battery rule is not
  calibrated that way, and the interface says so.
