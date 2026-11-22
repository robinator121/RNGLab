# RNGLab Unified: architecture

## Decision: extend the existing laboratory instead of a rewrite

The repository already held an uncommitted, standard-library laboratory GUI (worktree `lab-gui`, based on
`d91cb45`). It runs each simulation in a child process that imports RNGLab from a chosen checkout and
observes it at the audited `RandomSource.read_bytes` boundary. It was imported unchanged (commit
`5de3f30`; the original files were left in place) and extended. Reasons:

- **No new runtime dependencies.** FastAPI, Pydantic, React and Vite would have to be installed into the
  locked environment that the frozen record runs use, or into a second environment. The standard-library
  server and the build-free JavaScript interface run offline on the existing `.venv`.
- **Process isolation already exists.** Scientific code never runs in the server process. Each run is
  one worker with its own interpreter, and several branches can be offered side by side as code sources
  without importing them into one process.
- **The guards already existed and were tested:** seed-namespace refusal, loopback binding, the
  DNS-rebinding host check and the custom-header CSRF guard.

## Layers

```text
browser (static/js: app.js, unified.js, panels.js, charts.js)
   │  REST + server-sent events, loopback only
server process (gui/rnglab_gui: server, runs, archive, codesources, guard, catalog, unified)
   │  imports rnglab_unified (stdlib only) — never numpy-based science, never rnglab*
   ├── src/rnglab_unified: taxonomy · registry · evidence (read-only) · index (SQLite) · provenance
   │
   └── worker processes (python -I bootstrap.py config.json observe|direct|study)
          imports rnglab* from the run's code source; adapters in worker/adapters/
```

## Contracts

- **Capability descriptor** (`docs/unified/capability_matrix.json`, schema
  `rnglab-unified-capability-matrix-v1`): `id, title, kind, research_line, source{branch,commit,paths},
  maturity, test_coverage, reference, frozen_protocol{status,protocol_path,sha256_file}, status,
  outcome_summary, evidence[], constraints, integration_method, reachable_from_unified`.
  `kind` is one of RandomSource, Fault, Simulation, DetectorImporter, Monitor, ReferenceLaw, Estimator,
  Forecaster, Study, Visualizer. `status` is exactly one of the nine taxonomy labels.
  `taxonomy.check_entry` enforces the consistency rules, and a test requires zero problems.
- **Runnable model** (`gui/rnglab_gui/catalog.py`): parameters with types, ranges and units, each split
  into scientific or execution; the required files in a code source; the panels. `registry.MODEL_LINKS`
  ties each model to its matrix entries.
- **Run record** (`<data>/runs/<id>/`): `config.json` (model, validated parameters, code source with
  branch, head and dirty state, interpreter, laboratory version), `telemetry.jsonl` (hello with
  environment, meta, frames, events, result, integrity, done), `result.json` (official result digest),
  `integrity.json`, `arrays.npz`, `status.json`, and after a replay `verification.json`.
- **Seeds:** `rnglab.seeds.namespace_seed(namespace, label, role)`. Interactive runs are restricted to
  `rnglab-gui-*`, refusing reserved and protocol-declared namespaces. Roles are kept apart
  (proposal, acceptance, initialization, and so on) when the channel layout is `separate`.
- **Byte accounting:** every read goes through the tap at `RandomSource.read_bytes`. The tap records
  the consumed bytes and a running SHA-256 per role, and integrity checks compare them with the
  engine's own counters.
- **Replay:** `POST /api/runs/{id}/verify` re-runs the configuration unobserved in a fresh process.
  The state is `ok` when the digests match and the code source is unchanged, `warning` when they match
  but the code changed, `failed` when they differ, and `incomplete` when the re-run did not complete.
  A result file from an earlier attempt is deleted before each attempt.
- **Historical evidence:** `/api/u/studies`, `/api/u/studies/{id}`, `/api/u/file?path=` read under
  `research/` and `docs/` only (resolved paths, symlink escapes refused, previews bounded to 512 KiB).
  Freeze state compares `protocol.json` with `protocol.sha256`, and every `*.sha256` manifest line is
  checked. Responses are marked `origin: historical`. Laboratory runs are `origin: generated`.
- **Index:** `<data>/index.sqlite` is a disposable cache of runs and studies for search. Files remain
  the authority.

## Gaps against the target architecture (honest list)

- No typed `ExperimentSpec` object separate from the catalog parameters. A study-level specification
  with a calibration/evaluation split and multiplicity correction is not implemented. The frozen
  studies keep their own protocols.
- No resource estimates (CPU, RAM, disk) before launch. There are limits on concurrent runs and
  parameter ranges only.
- No resume of an interrupted interactive run. A run is either replayed from scratch or not at all.
- Replay compares official result digests, not telemetry streams. Telemetry frame timing is wall-clock
  dependent by design.
- Adapters do not yet exist for VMC, crypto BLUE, forecasting (prognosis, TRIDENT, CALIBRATE), finance
  or the Stage D pilot. These are browsable as evidence only.
- No HPC adapter and no live serial-hardware mode.
