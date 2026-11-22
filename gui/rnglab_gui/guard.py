"""Seed-namespace guard.

The laboratory generates random numbers only in namespaces that start with
``rnglab-gui-``. That keeps every interactive run apart from the seed
namespaces of the frozen studies, the reserved Stage D pilot and smoke
namespaces and the namespaces that tests own. As a second line, every namespace
named in a code source's ``research/*/protocol.json`` (and ``design.json``) is
refused even if it were to use the laboratory's prefix.

Recorded study outputs are read, never regenerated, by the laboratory.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from . import GUI_NAMESPACE_PREFIX

# Namespaces that no interactive run may sample in, whatever the code source says.
RESERVED = (
    "rnglab-v019-d-pilot",          # Stage D pilot (also covers -smoke by prefix)
    "rnglab-v019-audit-smoke",      # audit-only namespace of the independent verification
    "rnglab-v020-nuclear-test",     # owned by the nuclear tests
)
LABEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
NAMESPACE_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{2,79}$")


def _strings_named_namespace(obj, out: set[str]) -> None:
    if isinstance(obj, dict):
        for key, value in obj.items():
            if isinstance(value, str) and "namespace" in str(key).lower():
                out.add(value)
            else:
                _strings_named_namespace(value, out)
    elif isinstance(obj, list):
        for value in obj:
            _strings_named_namespace(value, out)


def study_namespaces(code_root: str | Path) -> dict[str, str]:
    """{namespace: file that declares it} for every research/*/{protocol,design}.json in a checkout."""
    found: dict[str, str] = {}
    research = Path(code_root) / "research"
    if not research.is_dir():
        return found
    for path in sorted(research.glob("*/*.json")):
        if path.name not in ("protocol.json", "design.json"):
            continue
        try:
            data = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        names: set[str] = set()
        _strings_named_namespace(data, names)
        for name in names:
            if name.startswith("rnglab"):
                found.setdefault(name, str(path.relative_to(code_root)))
    return found


class NamespaceRefused(ValueError):
    pass


def check(namespace: str, label: str, code_root: str | Path | None = None) -> None:
    """Raise NamespaceRefused unless (namespace, label) is allowed for an interactive run."""
    if not isinstance(namespace, str) or not NAMESPACE_RE.match(namespace):
        raise NamespaceRefused("namespace must be 3-80 characters of a-z, 0-9, '.', '_' or '-'")
    if not namespace.startswith(GUI_NAMESPACE_PREFIX):
        raise NamespaceRefused(f"interactive runs sample only in namespaces starting with '{GUI_NAMESPACE_PREFIX}'")
    for reserved in RESERVED:
        if namespace == reserved or namespace.startswith(reserved + "-") or namespace.startswith(reserved + "/"):
            raise NamespaceRefused(f"'{namespace}' is a reserved namespace")
    if code_root is not None:
        studies = study_namespaces(code_root)
        if namespace in studies:
            raise NamespaceRefused(f"'{namespace}' is declared by {studies[namespace]}")
    if not isinstance(label, str) or not LABEL_RE.match(label):
        raise NamespaceRefused("run label must be 1-64 characters of letters, digits, '.', '_' or '-' "
                               "and start with a letter or digit")
