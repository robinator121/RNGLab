"""RNGLab Unified: registry, evidence browser, index and provenance for the laboratory.

This package is imported by the laboratory *server* process. It must stay free of
scientific imports (no numpy, no ``rnglab*`` packages): it reads files, hashes
them and describes capabilities. Scientific code runs only in worker processes
(``gui/rnglab_gui/worker``). ``tests/unified/test_isolation.py`` enforces this.

Modules:

- ``taxonomy``: the scientific-claim status vocabulary and its rules.
- ``registry``: versioned capability descriptors, loaded from
  ``docs/unified/capability_matrix.json`` and the laboratory catalog.
- ``evidence``: read-only browser over frozen ``research/*`` studies with
  SHA-256 freeze checks; never writes inside a code source.
- ``index``: a local SQLite index of laboratory runs and studies for search.
- ``provenance``: environment and Git fingerprints recorded with runs.
"""
from __future__ import annotations

__version__ = "0.1.0"
REGISTRY_SCHEMA = "rnglab-unified-capability-matrix-v1"
