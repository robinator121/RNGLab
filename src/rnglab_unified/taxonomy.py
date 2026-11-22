"""Scientific-claim status taxonomy.

The labels are deliberately not interchangeable. A capability carries exactly one
status; the rules below say what evidence each one requires. ``check_entry``
reports violations (it never upgrades or downgrades a status by itself).
"""
from __future__ import annotations

STATUSES: dict[str, str] = {
    "implemented": "Code exists and runs; no dedicated tests and no reference comparison.",
    "unit-tested": "Code has automated tests in the repository; no independent or exact-law check.",
    "independently-verified": "Checked against an exact/analytic reference or an independent "
                              "re-implementation (recount, replay, second review).",
    "experimentally-validated": "A frozen, pre-registered protocol was executed once and its gates passed.",
    "exploratory": "Work in progress or interactive exploration; no claim is made.",
    "planned": "Described in a roadmap or design; no implementation.",
    "negative-result": "A frozen study or validation that did NOT support its hypothesis, or found a "
                       "limitation. First-class evidence, shown with equal prominence.",
    "superseded": "Replaced by a later version; kept for history and reproducibility.",
    "unbacked": "A claim found in documentation without code or evidence behind it.",
}
# Order used for sorting and for the strength bar in the interface (not a ranking of value:
# a negative result is as important as a validated one).
ORDER = list(STATUSES)

KINDS = ("RandomSource", "Fault", "Simulation", "DetectorImporter", "Monitor", "ReferenceLaw",
         "Estimator", "Forecaster", "Study", "Visualizer")

INTEGRATION = ("adapter", "read-only-browse", "code-source-worker", "none-yet")


def check_entry(entry: dict) -> list[str]:
    """Return human-readable problems with one capability entry (empty list = consistent)."""
    problems: list[str] = []
    ident = entry.get("id", "<no id>")
    status = entry.get("status")
    if status not in STATUSES:
        problems.append(f"{ident}: unknown status {status!r}")
        return problems
    if entry.get("kind") not in KINDS:
        problems.append(f"{ident}: unknown kind {entry.get('kind')!r}")
    evidence = entry.get("evidence") or []
    frozen = (entry.get("frozen_protocol") or {}).get("status")
    tests = (entry.get("test_coverage") or {}).get("test_paths") or []
    if status == "experimentally-validated" and frozen != "frozen":
        problems.append(f"{ident}: 'experimentally-validated' requires a frozen protocol")
    if status in ("experimentally-validated", "independently-verified", "negative-result") and not evidence:
        problems.append(f"{ident}: status {status!r} requires at least one evidence path")
    if status == "unit-tested" and not tests:
        problems.append(f"{ident}: 'unit-tested' requires test paths")
    if status == "planned" and entry.get("integration_method") in ("adapter", "code-source-worker"):
        # a planned study may have a frozen protocol to browse, but nothing of it can be run
        problems.append(f"{ident}: a 'planned' capability cannot have a runnable integration")
    return problems
