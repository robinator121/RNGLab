"""Compatibility contracts between random sources, faults and scientific models.

A fault is defined once and is reusable on every model whose declared random-input contract it is
compatible with. Compatibility is decided from declared facts, never assumed:

- ``StreamLevel``: the unit a fault transforms (bits within bytes, bytes, aligned 64-bit words,
  uniforms derived from words, a pair of channels, or an application transition). A fault defined
  on 64-bit words is meaningful only for a consumer that reads aligned 8-byte words, because its
  severity and onset are expressed in words; on a byte-wise consumer the same bytes would mean
  something else.
- ``RandomInput``: what a model consumes from each role: the read granularity, whether reads are
  8-byte aligned, how uniforms are formed (e.g. top 53 bits of a big-endian word) and which roles
  exist (proposal, acceptance, initialization, decay, ...).
- ``FaultContract``: the levels a fault family acts on, the read alignment it needs, whether it
  needs more than one channel, and whether it needs model internals (then it is not a stream fault
  and cannot be shared: it stays model-specific).

``compatible`` returns ``(ok, reason)``. A reason is always given, so a refusal is explainable in
the interface and a permitted combination records why it was permitted.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum


class StreamLevel(str, Enum):
    BIT = "bit"                  # individual bit positions inside each byte
    BYTE = "byte"                # byte values of the stream
    WORD64 = "word64"            # aligned 8-byte words of the stream
    UNIFORM = "uniform"          # uniforms the consumer derives from words
    CHANNEL_PAIR = "channel-pair"  # relation between two roles' streams (coupling, reuse)
    TRANSITION = "transition"    # application-level transition (needs model internals)


class Clock(str, Enum):
    """Progress measures of different models. They are recorded side by side, never converted."""
    BYTES = "consumed-bytes"
    SWEEPS = "sweeps"
    STEPS = "steps"
    EVENTS = "decay-events"
    INTERVALS = "time-intervals"
    SIM_TIME = "simulated-time"
    REPLICATES = "replicates"


@dataclass(frozen=True)
class RandomInput:
    roles: tuple[str, ...]
    read_unit_bytes: int                 # bytes per read call in normal operation (8 for word readers)
    aligned_words: bool                  # every read starts on an 8-byte boundary of the role's stream
    uniform_rule: str | None             # e.g. "u = (w >> 11) / 2**53, big-endian w"
    channel_layouts: tuple[str, ...] = ("shared",)   # "shared": one source for all roles; "separate"
    notes: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class FaultContract:
    family: str
    levels: tuple[StreamLevel, ...]
    requires_aligned_words: bool = False
    requires_channels: int = 1
    requires_model_internals: bool = False
    onset_unit: str = "consumed-bytes"   # onset is always expressed in consumed bytes of the faulted role
    parameters: dict = field(default_factory=dict)   # name -> {"unit", "range", "meaning"}
    semantics: str = ""
    origin: str = ""                     # where the canonical implementation lives

    def to_dict(self) -> dict:
        out = asdict(self)
        out["levels"] = [lv.value for lv in self.levels]
        return out


def compatible(fault: FaultContract, model_input: RandomInput, *, faulted_roles: tuple[str, ...],
               channel_layout: str) -> tuple[bool, str]:
    if fault.requires_model_internals:
        return False, (f"{fault.family} acts on application transitions; it is model-specific and cannot be "
                       "applied through the shared stream layer")
    missing = [r for r in faulted_roles if r not in model_input.roles]
    if missing:
        return False, f"model has no random role(s) {missing}; roles are {list(model_input.roles)}"
    if channel_layout not in model_input.channel_layouts:
        return False, f"model does not support channel layout {channel_layout!r}"
    if fault.requires_channels > 1:
        if channel_layout != "separate":
            return False, f"{fault.family} relates {fault.requires_channels} channels; it needs the 'separate' layout"
        if len(model_input.roles) < fault.requires_channels:
            return False, f"{fault.family} relates {fault.requires_channels} roles; the model has {len(model_input.roles)}"
    if fault.requires_aligned_words and not model_input.aligned_words:
        return False, (f"{fault.family} is defined on aligned 64-bit words; this model's reads are not "
                       "word-aligned, so the fault's severity and onset would not mean the same thing")
    if StreamLevel.UNIFORM in fault.levels and model_input.uniform_rule is None:
        return False, f"{fault.family} is defined on uniforms, but the model declares no uniform rule"
    return True, (f"{fault.family} acts on {', '.join(lv.value for lv in fault.levels)} of role(s) "
                  f"{list(faulted_roles)}; model reads {model_input.read_unit_bytes}-byte units"
                  f"{' (aligned words)' if model_input.aligned_words else ''}")
