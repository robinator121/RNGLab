"""Experiment specification: typed, serialisable, validated before any random number is drawn.

One spec describes a compound experiment: one healthy source, at most one fault family, one or more
scientific models (each with its channel layout, parameters and faulted roles), matched healthy and
faulted conditions, a replicate count, a seed namespace and pairing design, and the shared
measurements. ``validate`` returns a report with a reason for every decision; nothing runs unless
it passes.

Namespaces: experiments sample only in ``rnglab-unified-*`` (or the laboratory's ``rnglab-gui-*``).
Reserved namespaces and every namespace declared by a ``research/*/protocol.json`` are refused, so
no frozen study's streams can be generated from here.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path

from . import faults as fault_registry
from . import models as model_registry
from . import sources as source_registry
from .contracts import compatible
from .record import digest

REPO_ROOT = Path(__file__).resolve().parents[2]
ALLOWED_PREFIXES = ("rnglab-unified-", "rnglab-gui-")
RESERVED = ("rnglab-v019-d-pilot", "rnglab-v019-audit-smoke", "rnglab-v020-nuclear-test")
NAMESPACE_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{2,79}$")
SPEC_SCHEMA = "rnglab-unified-experiment-spec-v1"


@dataclass
class ModelRun:
    model: str
    layout: str
    faulted_roles: list[str]
    params: dict = field(default_factory=dict)
    # Only the onset may differ between models (models consume bytes at very different rates);
    # the fault family and its severity parameters are the same for every model of an experiment.
    fault_overrides: dict = field(default_factory=dict)


@dataclass
class ExperimentSpec:
    title: str
    namespace: str
    source: str
    fault: str | None
    fault_params: dict
    models: list[ModelRun]
    replicates: int = 8
    pairing: str = "common"
    endpoint_alpha: float = 0.05
    battery_alpha: float = 0.01
    battery_window_bytes: int = 8192
    # Example: {"kind": "switch-to-independent-source", "authorized": True, "window_bytes": 4096, "alpha": 0.01}
    intervention: dict | None = None
    # For source = "recorded-file": {"path": ..., "sha256": ..., "bytes_per_stream": N}. Every
    # (model, role, replicate[, condition]) stream gets its own disjoint segment of the file.
    source_params: dict = field(default_factory=dict)
    notes: str = ""
    schema: str = SPEC_SCHEMA

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "ExperimentSpec":
        data = dict(data)
        data["models"] = [m if isinstance(m, ModelRun) else ModelRun(**m) for m in data.get("models", [])]
        if data.get("schema", SPEC_SCHEMA) != SPEC_SCHEMA:
            raise ValueError(f"unsupported spec schema {data.get('schema')!r}")
        return cls(**data)

    def resolved(self) -> dict:
        """The spec with model defaults filled in: what is executed and digested."""
        out = self.to_dict()
        for m in out["models"]:
            m["params"] = {**model_registry.MODELS[m["model"]].defaults, **m["params"]} \
                if m["model"] in model_registry.MODELS else m["params"]
        return out

    @property
    def spec_digest(self) -> str:
        return digest(self.resolved())

    @property
    def stream_key(self) -> str:
        """Part of every seed label: fresh streams for every scientifically different spec.

        It digests the resolved spec without the title, notes and replicate count, so a replay (same spec)
        regenerates the same streams, adding replicates extends them, and any other change (a fault
        parameter, a model setting, a different model set) gives fresh streams in the same namespace.
        """
        core = {k: v for k, v in self.resolved().items() if k not in ("title", "notes", "replicates")}
        return digest(core)[:16]


def protocol_namespaces(root: Path = REPO_ROOT) -> dict[str, str]:
    found = {}
    for path in sorted((root / "research").glob("*/*.json")):
        if path.name not in ("protocol.json", "design.json"):
            continue
        try:
            data = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        stack = [data]
        while stack:
            obj = stack.pop()
            if isinstance(obj, dict):
                for k, v in obj.items():
                    if isinstance(v, str) and "namespace" in k.lower():
                        found.setdefault(v, str(path.relative_to(root)))
                    else:
                        stack.append(v)
            elif isinstance(obj, list):
                stack.extend(obj)
    return found


def validate(spec: ExperimentSpec) -> dict:
    errors: list[str] = []
    decisions: list[dict] = []
    ns = spec.namespace
    if not isinstance(ns, str) or not NAMESPACE_RE.match(ns):
        errors.append("namespace must be 3-80 characters of a-z, 0-9, '.', '_' or '-'")
    elif not ns.startswith(ALLOWED_PREFIXES):
        errors.append(f"namespace must start with one of {ALLOWED_PREFIXES}")
    else:
        for r in RESERVED:
            if ns == r or ns.startswith(r + "-"):
                errors.append(f"'{ns}' is reserved")
        declared = protocol_namespaces()
        if ns in declared:
            errors.append(f"'{ns}' is declared by the frozen protocol {declared[ns]}")
    if spec.source == "recorded-file":
        sp = spec.source_params or {}
        path = Path(str(sp.get("path", "")))
        if not path.is_file():
            errors.append("recorded-file needs source_params.path naming an existing file")
        elif not isinstance(sp.get("sha256"), str) or len(sp["sha256"]) != 64:
            errors.append("recorded-file needs source_params.sha256 (the file's SHA-256, checked at open)")
        elif hashlib.sha256(path.read_bytes()).hexdigest() != sp["sha256"]:
            errors.append("recorded stream changed: its SHA-256 differs from source_params.sha256")
        elif not isinstance(sp.get("bytes_per_stream"), int) or sp["bytes_per_stream"] < 64:
            errors.append("recorded-file needs an integer source_params.bytes_per_stream >= 64")
        else:
            streams = sum(len(model_registry.MODELS[m.model].inputs.get(m.layout).roles)
                          for m in spec.models if m.model in model_registry.MODELS
                          and m.layout in model_registry.MODELS[m.model].inputs) * spec.replicates
            if spec.pairing == "independent":
                streams *= 3 if spec.intervention else 2
            need = streams * sp["bytes_per_stream"]
            size = path.stat().st_size
            if need > size:
                errors.append(f"recorded stream too short: {streams} disjoint segments of {sp['bytes_per_stream']} "
                              f"bytes need {need} bytes, the file has {size}")
            decisions.append({"model": "*", "fault": None, "compatible": need <= size,
                              "reason": f"recorded stream: {streams} disjoint segments, no byte reused; no entropy "
                                        "claim; a segment that runs out ends its unit with an error"})
    elif spec.source not in source_registry.SOURCES:
        errors.append(f"unknown source {spec.source!r}")
    if not isinstance(spec.replicates, int) or not 1 <= spec.replicates <= 512:
        errors.append("replicates must be an integer in 1..512 (larger campaigns need explicit approval)")
    if spec.pairing not in ("common", "independent"):
        errors.append("pairing must be 'common' or 'independent'")
    if not spec.models:
        errors.append("at least one model is required")
    iv = spec.intervention
    if iv is not None:
        if iv.get("kind") != "switch-to-independent-source":
            errors.append("the only implemented intervention is 'switch-to-independent-source'")
        if iv.get("authorized") is not True:
            errors.append("an intervention runs only when the spec marks it authorized: true")
        if not isinstance(iv.get("window_bytes"), int) or not 512 <= iv["window_bytes"] <= 1 << 20:
            errors.append("intervention window_bytes must be an integer in 512..1048576")
        if not isinstance(iv.get("alpha"), (int, float)) or not 0 < iv["alpha"] < 0.5:
            errors.append("intervention alpha must be in (0, 0.5)")
        if not spec.fault:
            errors.append("an intervention needs a fault to respond to")
    fault = fault_registry.FAULTS.get(spec.fault) if spec.fault else None
    if spec.fault and fault is None:
        errors.append(f"unknown fault family {spec.fault!r}")
    if fault is not None:
        allowed = set(fault.contract.parameters)
        extra = set(spec.fault_params) - allowed
        if extra:
            errors.append(f"{spec.fault} does not take parameter(s) {sorted(extra)}; it takes {sorted(allowed)}")
        for name, meta in fault.contract.parameters.items():
            if name not in spec.fault_params:
                errors.append(f"fault parameter {name!r} is required ({meta.get('unit')})")
                continue
            value, rng = spec.fault_params[name], meta.get("range")
            if rng and isinstance(value, (int, float)) and not isinstance(value, bool):
                lo, hi = rng
                if (lo is not None and value < lo) or (hi is not None and value > hi):
                    errors.append(f"fault parameter {name} = {value} outside {rng}")
        onsets = [spec.fault_params.get("onset_bytes")] + [m.fault_overrides.get("onset_bytes") for m in spec.models]
        for o in onsets:
            if o is None:
                continue
            if "onset_bytes" not in allowed:
                errors.append(f"{spec.fault} acts from the first byte and has no onset; remove onset_bytes")
                break
            if not isinstance(o, int) or o < 0 or o % 8:
                errors.append(f"onset_bytes must be a non-negative multiple of 8 (word boundary), got {o!r}")
    seen = set()
    for m in spec.models:
        adapter = model_registry.MODELS.get(m.model)
        if adapter is None:
            errors.append(f"unknown model {m.model!r}")
            continue
        if m.model in seen:
            errors.append(f"model {m.model!r} listed twice")
        seen.add(m.model)
        if m.layout not in adapter.inputs:
            errors.append(f"{m.model}: layout {m.layout!r} not offered; choose from {list(adapter.inputs)}")
            continue
        unknown = set(m.params) - set(adapter.defaults)
        if unknown:
            errors.append(f"{m.model}: unknown parameters {sorted(unknown)}")
        rin = adapter.inputs[m.layout]
        extra = set(m.fault_overrides) - {"onset_bytes"}
        if extra:
            errors.append(f"{m.model}: only onset_bytes may be overridden per model (got {sorted(extra)}); "
                          "severity must be identical across models")
        if fault is None:
            continue
        if not m.faulted_roles:
            errors.append(f"{m.model}: name the faulted role(s); roles are {list(rin.roles)}")
            continue
        # alignment is a property of each role's reads: judge it on the faulted roles only
        valid_roles = [r for r in m.faulted_roles if r in rin.roles]
        role_aligned = bool(valid_roles) and all(model_registry.per_role_aligned(adapter, m.layout, r) for r in valid_roles)
        ok, reason = compatible(fault.contract, replace(rin, aligned_words=role_aligned),
                                faulted_roles=tuple(m.faulted_roles), channel_layout=m.layout)
        if ok and spec.fault == "seed-reuse":
            partner = spec.fault_params.get("partner_role")
            if partner not in rin.roles or partner in m.faulted_roles:
                ok, reason = False, "seed-reuse needs a partner_role that exists and is not itself faulted"
        if ok and fault.build is None and spec.fault != "seed-reuse":
            ok, reason = False, f"{spec.fault} has no shared implementation"
        if ok and m.layout == "shared" and spec.fault:
            reason += ("; NOTE: with the shared layout the fault acts on every role of the model at once "
                       f"({adapter.role_semantics.get('shared', 'all decisions')})")
        if ok and spec.fault:
            reason += f"; faulted uniforms drive: " + "; ".join(
                f"{r}: {adapter.role_semantics.get(r, 'not declared')}" for r in m.faulted_roles)
        decisions.append({"model": m.model, "fault": spec.fault, "compatible": ok, "reason": reason})
        if not ok:
            errors.append(f"{m.model}: {reason}")
    return {"ok": not errors, "errors": errors, "decisions": decisions,
            "units": len(spec.models) * (3 if spec.intervention else 2) * spec.replicates if spec.models else 0}
