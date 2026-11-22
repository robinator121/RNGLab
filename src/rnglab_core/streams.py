"""Stream plans: per-role sources with namespace seeds, faults, splitting and accounting.

Seeds. Every role of every unit gets ``rnglab.seeds.namespace_seed(namespace, model, role, rep)``
(plus the condition when the design uses independent streams per condition). Fault parameters
that need their own randomness (DUP, GFSR) get ``namespace_seed(namespace, model, role, rep, "fault"|"gfsr")``.
Distinct labels give distinct seeds; independence of the resulting streams is not proven.

Pairing. ``common`` gives the healthy and faulted conditions of a replicate the same healthy
streams, so their difference isolates the fault (paired design). ``independent`` adds the condition
to every seed label. The design is recorded with the experiment.

``WordSplitter`` turns a bulk read of 8·n bytes into n reads of 8 bytes from the wrapped source.
For any byte-stream source the bytes are identical (tested); it lets word-defined faults, which
accept only single-word reads after onset, act on consumers that read words in bulk (nuclear
``WordStream``). It changes nothing else.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from rnglab14.sources.base import RandomSource, SourceManifest
from rnglab.seeds import namespace_seed

from . import faults as fault_registry
from . import sources as source_registry


class WordSplitter(RandomSource):
    def __init__(self, inner: RandomSource):
        super().__init__(SourceManifest(source_id=f"split8:{inner.manifest.source_id}", family=inner.manifest.family,
                                        is_deterministic=inner.manifest.is_deterministic,
                                        is_physical=inner.manifest.is_physical,
                                        metadata={"wraps": inner.manifest.to_dict(), "adapter": "WordSplitter"}))
        self.inner = inner

    def _read_bytes(self, n: int) -> bytes:
        if n % 8:
            return self.inner.read_bytes(n)
        return b"".join(self.inner.read_bytes(8) for _ in range(n // 8))


@dataclass
class RoleStream:
    role: str
    seed: int
    seed_label: str
    base: RandomSource
    source: RandomSource               # what the model reads (base, or fault wrapper over base)
    fault: str | None = None
    fault_params: dict = field(default_factory=dict)
    seed_from_role: str | None = None  # seed-reuse: the partner role whose seed was used
    monitor: object | None = None       # InterventionSource, when the experiment declares an intervention

    def accounting(self) -> dict:
        out = {"role": self.role, "seed": self.seed, "seed_label": self.seed_label,
               "base_source": self.base.manifest.source_id,
               "base_consumed_bytes": self.base.consumed_bytes, "base_sha256": self.base.stream_digest,
               "consumed_bytes": self.source.consumed_bytes, "consumed_sha256": self.source.stream_digest,
               "fault": self.fault, "fault_params": self.fault_params}
        if self.seed_from_role:
            out["seed_from_role"] = self.seed_from_role
        if self.monitor is not None:
            out["monitor"] = self.monitor.report()
        return out


def build_role_streams(*, source_id: str, namespace: str, model: str, roles: tuple[str, ...], replicate: int,
                       condition: str, pairing: str, fault: str | None, fault_params: dict,
                       faulted_roles: tuple[str, ...], split_words: bool,
                       intervention: dict | None = None, source_params: dict | None = None,
                       segment_base: int = 0, stream_key: str = "") -> dict[str, RoleStream]:
    """Healthy base per role; the fault (if any) wraps the faulted roles of the faulted condition."""
    if pairing not in ("common", "independent"):
        raise ValueError("pairing must be 'common' or 'independent'")
    unit = f"{replicate:05d}"

    def label(role: str, *extra: str) -> tuple[int, str]:
        parts = [model, role, unit, *extra]
        if pairing == "independent":
            parts.insert(0, condition)
        if stream_key:
            parts.insert(0, stream_key)
        return namespace_seed(namespace, *parts), "/".join([namespace, *parts])

    out: dict[str, RoleStream] = {}
    faulted = condition in ("faulted", "intervened") and fault is not None
    seed_map = {role: label(role) for role in roles}
    if faulted and fault == "seed-reuse":
        partner = fault_params["partner_role"]
        for role in faulted_roles:
            seed_map[role] = seed_map[partner]
    for k, role in enumerate(roles):
        seed, seed_label = seed_map[role]
        if source_id == "recorded-file":
            sp = source_params or {}
            segment = segment_base + k
            base = source_registry.RecordedStreamSource(sp["path"], expected_sha256=sp["sha256"],
                                                        offset=segment * int(sp["bytes_per_stream"]),
                                                        limit=int(sp["bytes_per_stream"]))
            seed_label = f"{seed_label} -> recorded segment {segment}"
        else:
            base = source_registry.make(source_id, seed, seed_label)
        stream = RoleStream(role, seed, seed_label, base, base)
        if faulted and role in faulted_roles:
            stream.fault, stream.fault_params = fault, dict(fault_params)
            if fault == "seed-reuse":
                stream.seed_from_role = fault_params["partner_role"]
            else:
                wrapped = fault_registry.wrap(fault, base, fault_params,
                                              lambda kind, r=role: label(r, kind)[0])
                stream.source = wrapped
        if intervention is not None and role in faulted_roles:
            seed_i, label_i = label(role, "intervention")
            monitor = InterventionSource(
                stream.source, lambda s=seed_i, l=label_i: source_registry.make(source_id, s, l),
                window_bytes=int(intervention["window_bytes"]), alpha=float(intervention["alpha"]),
                act=condition == "intervened")
            stream.source, stream.monitor = monitor, monitor
        if split_words:
            stream.source = WordSplitter(stream.source)
        out[role] = stream
    return out


class InterventionSource(RandomSource):
    """Online monitor plus an authorised intervention: switch the role to an independent source.

    The monitor evaluates the Stage C battery (Holm over ten tests) on consecutive, non-overlapping
    windows of ``window_bytes`` emitted bytes, counted from the first byte (it is not told the onset).
    After the first alarming window, if ``act`` is true, every later read is served by
    ``replacement`` (an independently seeded healthy source). With ``act`` false the monitor only
    observes, which measures its false-alarm behaviour on healthy streams.

    Switching repairs nothing that was already consumed: bytes read before the switch stay faulted,
    and the application state they produced is kept. Recovery is measured, not assumed.
    """

    def __init__(self, inner: RandomSource, replacement_factory, *, window_bytes: int, alpha: float, act: bool):
        super().__init__(SourceManifest(source_id=f"intervene:{inner.manifest.source_id}", family=inner.manifest.family,
                                        is_deterministic=True, is_physical=False,
                                        metadata={"adapter": "InterventionSource", "window_bytes": window_bytes,
                                                  "alpha": alpha, "act": act}))
        self.inner, self.factory, self.window, self.alpha, self.act = inner, replacement_factory, window_bytes, alpha, act
        self.replacement: RandomSource | None = None
        self.buffer = bytearray()
        self.emitted = 0
        self.windows: list[dict] = []
        self.alarm_at: int | None = None
        self.switched_at: int | None = None

    def _check_window(self) -> None:
        from .measure import conventional_battery
        while len(self.buffer) >= self.window:
            chunk, self.buffer = bytes(self.buffer[:self.window]), self.buffer[self.window:]
            end = self.emitted - len(self.buffer)
            result = conventional_battery(chunk, self.alpha)
            self.windows.append({"end_byte": end, "alarm": result.get("alarm"),
                                 "min_adjusted_p": min((v for v in result.get("holm", {}).get("adjusted", {}).values()
                                                        if v is not None), default=None)})
            if result.get("alarm") and self.alarm_at is None:
                self.alarm_at = end
                if self.act:
                    self.replacement = self.factory()
                    self.switched_at = end
                    self.buffer.clear()
                    return

    def _read_bytes(self, n: int) -> bytes:
        if self.replacement is not None:
            raw = self.replacement.read_bytes(n)
        else:
            raw = self.inner.read_bytes(n)
            self.buffer += raw
        self.emitted += n
        if self.replacement is None:
            self._check_window()
        return raw

    def report(self) -> dict:
        return {"windows_evaluated": len(self.windows), "alarm_at_byte": self.alarm_at,
                "switched_at_byte": self.switched_at, "acted": self.act,
                "replacement": self.replacement.manifest.source_id if self.replacement else None,
                "replacement_consumed_bytes": self.replacement.consumed_bytes if self.replacement else 0,
                "replacement_sha256": self.replacement.stream_digest if self.replacement else None,
                "emitted_bytes": self.emitted, "windows": self.windows[:200]}
