"""RNGLab Laboratory: an observing instrument for RNGLab simulations.

The laboratory never computes a scientific result itself. Every simulation runs
in a separate worker process that imports RNGLab from one chosen checkout
(a "code source") and calls the scientific functions unchanged. The worker
observes a run at the audited random-number boundary (``RandomSource.read_bytes``)
and at callbacks the engines already provide, and reports structured telemetry.
The server stores that telemetry, the official result and its provenance, and
the browser draws what it receives.

Layers (see gui/ARCHITECTURE.md):

- ``catalog``, ``params``, ``guard``, ``codesources``: what can run, with which
  parameters, in which seed namespaces and from which checkout. None of these
  imports scientific code.
- ``worker``: the only place where scientific code is imported, in a child
  process per run.
- ``runs``, ``archive``, ``server``: process management, storage, HTTP and
  server-sent events.
- ``studies``: read-only views of frozen study outputs in a code source.
"""
from __future__ import annotations

__version__ = "0.1.0"

TELEMETRY_SCHEMA = "rnglab-gui-telemetry-v1"
RUN_SCHEMA = "rnglab-gui-run-v1"
GUI_NAMESPACE_PREFIX = "rnglab-gui-"
DEFAULT_NAMESPACE = "rnglab-gui-explore"

# Data classes shown on every panel. They are never merged.
DATA_CLASSES = {
    "simulated": "state or output of the scientific computation itself, read without changing it",
    "derived": "computed by the laboratory from simulated data with RNGLab's own functions",
    "reference": "analytic or exact value that draws no random numbers",
    "recorded": "read from a stored study or run file",
    "illustrative": "visual aid only; carries no measurement",
}

# Evidence states, after the five-state rule of the earlier Control Center design.
EVIDENCE_STATES = ("ok", "warning", "failed", "incomplete", "unavailable")
