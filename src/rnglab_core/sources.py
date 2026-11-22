"""Random Source and Entropy layer: one registry over the existing source implementations.

All sources implement ``rnglab14.sources.base.RandomSource``: ``read_bytes(n)`` is the single
audited consumption boundary (byte count and SHA-256 of every emitted byte), and
``random_float`` reads one big-endian 8-byte word and keeps its top 53 bits. Nothing here
re-implements a generator; every entry points to the canonical class.

Each descriptor separates properties that are often conflated:

- ``deterministic`` / ``replayable``: the same seed reproduces the same bytes;
- ``statistical_quality``: what is documented about passing statistical tests (not entropy);
- ``entropy_claim``: always ``none`` for algorithmic sources; for physical and recorded sources it
  is ``unassessed`` unless a separate validated assessment exists (none does in this repository);
- ``unpredictability``: cryptographic design claim, if any (ChaCha20, OS entropy);
- ``independence``: distinct seeds give distinct streams; independence is *not* proven.

Two PCG64 implementations exist and give different streams for the same integer seed
(``rnglab14`` seeds the raw 128-bit state; ``numpy-pcg64`` seeds via ``SeedSequence``). They
are separate entries and are never interchanged.
"""
from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable

from rnglab14.sources.base import RandomSource, SourceManifest


@dataclass(frozen=True)
class SourceDescriptor:
    id: str
    title: str
    implementation: str
    kind: str                    # "algorithmic-prng", "csprng", "os-entropy", "recorded", "physical", "conditioner"
    deterministic: bool
    replayable: bool
    statistical_quality: str
    entropy_claim: str
    unpredictability: str
    independence: str
    status: str                  # taxonomy label of the implementation itself

    def to_dict(self) -> dict:
        return asdict(self)


def _pcg64(seed: int, label: str) -> RandomSource:
    from rnglab14.sources.reference import PCG64Source
    return PCG64Source(seed)


def _numpy_pcg64(seed: int, label: str) -> RandomSource:
    from rnglab.nuclear.streams import NumpyPCG64Source
    return NumpyPCG64Source(seed, label=label)


def _xoshiro(seed: int, label: str) -> RandomSource:
    from rnglab14.sources.reference import Xoshiro256PlusPlusSource
    return Xoshiro256PlusPlusSource(seed)


def _mt(seed: int, label: str) -> RandomSource:
    from rnglab14.sources.reference import MersenneTwisterSource
    return MersenneTwisterSource(seed)


def _chacha(seed: int, label: str) -> RandomSource:
    from rnglab14.sources.reference import ChaCha20Source
    # key derived from the seed label exactly as the laboratory worker does (gui/rnglab_gui/worker/runtime.py)
    return ChaCha20Source(key=hashlib.sha256(label.encode()).digest())


_NO_ENTROPY = "none: algorithmic output carries no entropy beyond its seed"
_NOT_PROVEN = "distinct namespace labels give distinct seeds; statistical independence is not proven"

SOURCES: dict[str, tuple[SourceDescriptor, Callable[[int, str], RandomSource]]] = {
    "pcg64": (SourceDescriptor(
        "pcg64", "PCG64 XSL-RR 128/64, pure Python", "rnglab14.sources.reference.PCG64Source", "algorithmic-prng",
        True, True, "widely used reference generator; RNGLab's own batteries treat it as healthy", _NO_ENTROPY,
        "none (not a CSPRNG)", _NOT_PROVEN, "unit-tested"), _pcg64),
    "numpy-pcg64": (SourceDescriptor(
        "numpy-pcg64", "NumPy PCG64 raw words, SeedSequence seeding", "rnglab.nuclear.streams.NumpyPCG64Source",
        "algorithmic-prng", True, True, "as pcg64; different stream for the same integer seed", _NO_ENTROPY,
        "none (not a CSPRNG)", _NOT_PROVEN, "unit-tested"), _numpy_pcg64),
    "xoshiro256pp": (SourceDescriptor(
        "xoshiro256pp", "xoshiro256++, pure Python", "rnglab14.sources.reference.Xoshiro256PlusPlusSource",
        "algorithmic-prng", True, True, "reference generator", _NO_ENTROPY, "none", _NOT_PROVEN, "unit-tested"),
        _xoshiro),
    "mt19937": (SourceDescriptor(
        "mt19937", "MT19937 through Python's random", "rnglab14.sources.reference.MersenneTwisterSource",
        "algorithmic-prng", True, True, "fails some linear-complexity tests by design", _NO_ENTROPY, "none",
        _NOT_PROVEN, "unit-tested"), _mt),
    "chacha20": (SourceDescriptor(
        "chacha20", "ChaCha20 keystream, pure Python", "rnglab14.sources.reference.ChaCha20Source", "csprng",
        True, True, "keystream of a cryptographic design", _NO_ENTROPY,
        "computational, from the cipher design, given a secret key (here the key is derived from a public label)",
        _NOT_PROVEN, "unit-tested"), _chacha),
}


def describe() -> list[dict]:
    out = [d.to_dict() for d, _ in SOURCES.values()]
    out.append(RecordedStreamSource.DESCRIPTOR.to_dict())
    return out


def make(source_id: str, seed: int, label: str) -> RandomSource:
    if source_id not in SOURCES:
        raise KeyError(f"unknown source {source_id!r}; known: {sorted(SOURCES)}")
    return SOURCES[source_id][1](seed, label)


class RecordedStreamSource(RandomSource):
    """Replays bytes from a recorded file, in order, once. No entropy claim is made.

    A recorded stream is replayable only as long as the file is unchanged; its SHA-256 is part of
    the manifest and is checked at open. Reading past the end raises ``EOFError``: a recorded stream
    is never wrapped around or extended, so a workload cannot silently reuse bytes.
    """

    DESCRIPTOR = SourceDescriptor(
        "recorded-file", "Recorded byte stream (file replay)", "rnglab_core.sources.RecordedStreamSource", "recorded",
        True, True, "unknown until assessed; statistical regularity is not entropy",
        "unassessed: no validated entropy assessment exists for any recorded stream in this repository",
        "unknown", "unknown: depends on acquisition; must be argued, not assumed", "unit-tested")

    def __init__(self, path: str | Path, *, expected_sha256: str | None = None, offset: int = 0,
                 limit: int | None = None):
        path = Path(path)
        data = path.read_bytes()
        actual = hashlib.sha256(data).hexdigest()
        if expected_sha256 is not None and actual != expected_sha256:
            raise ValueError("recorded stream changed: SHA-256 differs from the expected value")
        if offset < 0 or offset > len(data):
            raise ValueError("offset outside the recorded stream")
        super().__init__(SourceManifest(source_id=f"recorded:{actual[:16]}+{offset}", family="recorded",
                                        is_deterministic=True, is_physical=False,
                                        metadata={"file_sha256": actual, "file_bytes": len(data), "offset": offset,
                                                  "limit": limit, "entropy_claim": "unassessed"}))
        end = len(data) if limit is None else min(len(data), offset + limit)
        self._data = memoryview(data)[:end]
        self._pos = offset

    @property
    def remaining(self) -> int:
        return len(self._data) - self._pos

    def _read_bytes(self, n: int) -> bytes:
        if n > self.remaining:
            raise EOFError(f"recorded stream exhausted: {n} bytes requested, {self.remaining} left")
        out = bytes(self._data[self._pos:self._pos + n])
        self._pos += n
        return out
