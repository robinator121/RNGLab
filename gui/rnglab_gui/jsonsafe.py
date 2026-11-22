"""JSON conversion and canonical digests shared by the server and the workers.

Floats are written with Python's ``repr`` (shortest round-trip form), so a value
read back from JSON is the same float64. Non-finite floats become the strings
"NaN", "Infinity" and "-Infinity" because browsers reject bare NaN in JSON.
NumPy arrays are either listed (small) or moved into an ``arrays`` mapping and
replaced by a descriptor that carries their dtype, shape and SHA-256.
"""
from __future__ import annotations

import dataclasses
import enum
import hashlib
import json
import math
from collections.abc import Mapping
from typing import Any

try:  # the server can run without NumPy; workers always have it
    import numpy as np
except ImportError:  # pragma: no cover - exercised only without numpy
    np = None

INLINE_ARRAY_LIMIT = 4096


def _float(value: float) -> Any:
    if math.isnan(value):
        return "NaN"
    if math.isinf(value):
        return "Infinity" if value > 0 else "-Infinity"
    return value


def array_sha256(array: Any) -> str:
    """SHA-256 of an array's little-endian C-order bytes (dtype and shape go in the descriptor)."""
    data = np.ascontiguousarray(array)
    if data.dtype.byteorder == ">" or (data.dtype.byteorder == "=" and not _little_endian()):
        data = data.astype(data.dtype.newbyteorder("<"))
    return hashlib.sha256(data.tobytes()).hexdigest()


def _little_endian() -> bool:
    import sys
    return sys.byteorder == "little"


def to_jsonable(obj: Any, arrays: dict | None = None, path: str = "", inline_limit: int = INLINE_ARRAY_LIMIT) -> Any:
    """Return a JSON-safe copy of ``obj``.

    With ``arrays`` given, every NumPy array larger than ``inline_limit`` elements
    is stored there under its path and replaced by a descriptor.
    """
    if obj is None or isinstance(obj, (bool, str)):
        return obj
    if isinstance(obj, int):
        return obj
    if isinstance(obj, float):
        return _float(obj)
    if np is not None:
        if isinstance(obj, np.bool_):
            return bool(obj)
        if isinstance(obj, np.integer):
            return int(obj)
        if isinstance(obj, np.floating):
            return _float(float(obj))
        if isinstance(obj, np.ndarray):
            if arrays is not None and obj.size > inline_limit:
                name = path.strip("/").replace("/", ".") or "array"
                arrays[name] = np.array(obj, copy=True)
                return {"__array__": name, "dtype": str(obj.dtype), "shape": list(obj.shape),
                        "sha256": array_sha256(obj)}
            return to_jsonable(obj.tolist(), arrays, path, inline_limit)
    if isinstance(obj, enum.Enum):
        return to_jsonable(obj.value, arrays, path, inline_limit)
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return {f.name: to_jsonable(getattr(obj, f.name), arrays, f"{path}/{f.name}", inline_limit)
                for f in dataclasses.fields(obj)}
    if isinstance(obj, Mapping):
        return {str(k): to_jsonable(v, arrays, f"{path}/{k}", inline_limit) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [to_jsonable(v, arrays, f"{path}/{i}", inline_limit) for i, v in enumerate(obj)]
    if isinstance(obj, (bytes, bytearray)):
        return {"__bytes__": len(obj), "sha256": hashlib.sha256(bytes(obj)).hexdigest()}
    if isinstance(obj, (set, frozenset)):
        return sorted(to_jsonable(v, arrays, path, inline_limit) for v in obj)
    return repr(obj)


def canonical(obj: Any) -> str:
    """Canonical JSON text: sorted keys, no whitespace, finite floats only."""
    return json.dumps(to_jsonable(obj), sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(obj: Any) -> str:
    return hashlib.sha256(canonical(obj).encode()).hexdigest()


def dumps_line(obj: Any) -> str:
    """One telemetry line (no sorting, compact)."""
    return json.dumps(to_jsonable(obj), separators=(",", ":"), allow_nan=False)


def sha256_file(path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


# Keys whose values are wall-clock facts of a run, not results. SourceManifest.created_at
# is a construction timestamp; RNGLab's PIMC provenance itself omits it for replay, but a
# few nested manifests (e.g. centroid_update.source_manifest) still carry it.
VOLATILE_KEYS = ("created_at",)


def strip_volatile(obj: Any, path: str = "", removed: list | None = None) -> Any:
    """Copy of a JSON-safe object without VOLATILE_KEYS; paths removed are appended to ``removed``."""
    if isinstance(obj, dict):
        out = {}
        for key, value in obj.items():
            if key in VOLATILE_KEYS:
                if removed is not None:
                    removed.append(f"{path}/{key}")
                continue
            out[key] = strip_volatile(value, f"{path}/{key}", removed)
        return out
    if isinstance(obj, list):
        return [strip_volatile(v, f"{path}[{i}]", removed) for i, v in enumerate(obj)]
    return obj


def result_digest(jsonable: Any) -> tuple[str, list[str]]:
    """Reproducibility digest of a JSON-safe result and the volatile paths it leaves out."""
    removed: list[str] = []
    stripped = strip_volatile(jsonable, "", removed)
    return digest(stripped), removed
