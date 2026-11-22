"""Parameter schemas and their validation.

A parameter is either scientific (it changes what is computed or which random
numbers are consumed) or an execution setting (telemetry rate, playback delay,
chunk size where the engine guarantees identical results). The distinction is
shown in the interface and recorded in every run configuration. Validation
never fills in a value silently: a missing parameter takes its declared
default, and the configuration stored with the run lists every value used.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class Param:
    name: str
    kind: str  # int | float | enum | bool | str
    default: Any
    label: str = ""
    group: str = "Model"
    minimum: float | None = None
    maximum: float | None = None
    choices: tuple = ()
    help: str = ""
    scientific: bool = True
    # Shown only when another parameter has one of the listed values: {"fault": ["degraded"]}
    visible_if: dict = field(default_factory=dict)
    step: float | None = None
    unit: str = ""

    def to_dict(self) -> dict[str, Any]:
        out = {"name": self.name, "kind": self.kind, "default": self.default, "label": self.label or self.name,
               "group": self.group, "help": self.help, "scientific": self.scientific, "unit": self.unit}
        if self.minimum is not None:
            out["minimum"] = self.minimum
        if self.maximum is not None:
            out["maximum"] = self.maximum
        if self.choices:
            out["choices"] = [list(c) if isinstance(c, tuple) else c for c in self.choices]
        if self.visible_if:
            out["visible_if"] = self.visible_if
        if self.step is not None:
            out["step"] = self.step
        return out

    def choice_values(self) -> list:
        return [c[0] if isinstance(c, tuple) else c for c in self.choices]


class ParamError(ValueError):
    def __init__(self, errors: dict[str, str]):
        super().__init__("; ".join(f"{k}: {v}" for k, v in errors.items()))
        self.errors = errors


def _coerce(param: Param, value: Any) -> Any:
    kind = param.kind
    if kind == "bool":
        if isinstance(value, bool):
            return value
        if isinstance(value, str) and value.lower() in ("true", "false"):
            return value.lower() == "true"
        raise ValueError("must be true or false")
    if kind == "int":
        if isinstance(value, bool):
            raise ValueError("must be an integer")
        if isinstance(value, float):
            if not value.is_integer():
                raise ValueError("must be an integer")
            value = int(value)
        if isinstance(value, str):
            value = int(value.strip(), 0)
        if not isinstance(value, int):
            raise ValueError("must be an integer")
    elif kind == "float":
        if isinstance(value, bool):
            raise ValueError("must be a number")
        if isinstance(value, str):
            value = float(value.strip())
        if not isinstance(value, (int, float)):
            raise ValueError("must be a number")
        value = float(value)
        if not math.isfinite(value):
            raise ValueError("must be finite")
    elif kind == "enum":
        if value not in param.choice_values():
            raise ValueError(f"must be one of {param.choice_values()}")
        return value
    elif kind == "str":
        if not isinstance(value, str):
            raise ValueError("must be text")
        value = value.strip()
        if len(value) > 200:
            raise ValueError("at most 200 characters")
        return value
    else:  # pragma: no cover - schema error
        raise ValueError(f"unknown parameter kind {kind}")
    if param.minimum is not None and value < param.minimum:
        raise ValueError(f"must be >= {param.minimum}")
    if param.maximum is not None and value > param.maximum:
        raise ValueError(f"must be <= {param.maximum}")
    return value


def is_visible(param: Param, values: dict[str, Any]) -> bool:
    return all(values.get(key) in allowed for key, allowed in param.visible_if.items())


def validate(schema: list[Param], supplied: dict[str, Any] | None) -> dict[str, Any]:
    """Return the complete parameter set (defaults for absent names) or raise ParamError.

    Unknown names are an error, not ignored: a misspelt parameter would
    otherwise run with its default without anyone noticing.
    """
    supplied = dict(supplied or {})
    names = {p.name for p in schema}
    errors: dict[str, str] = {}
    for key in supplied:
        if key not in names:
            errors[key] = "unknown parameter"
    values: dict[str, Any] = {}
    for param in schema:
        raw = supplied.get(param.name, param.default)
        try:
            values[param.name] = _coerce(param, raw)
        except (ValueError, TypeError) as error:
            errors[param.name] = str(error)
    if errors:
        raise ParamError(errors)
    return values


def split(schema: list[Param], values: dict[str, Any]) -> tuple[dict, dict]:
    """(scientific, execution) parts of a validated configuration."""
    sci = {p.name: values[p.name] for p in schema if p.scientific}
    exe = {p.name: values[p.name] for p in schema if not p.scientific}
    return sci, exe
