"""Transparent observation at the audited random-number boundary.

``ObservedSource`` is a RandomSource that forwards every read to the source it
wraps and hands a copy of the bytes to the laboratory. It changes nothing a
model sees:

- it returns exactly the bytes the wrapped source returned, for every read size;
- it presents the wrapped source's manifest object, so provenance written by a
  model names the real source;
- it counts and hashes through RandomSource.read_bytes like any source, so its
  byte count and stream digest equal those of the wrapped source when both
  start fresh (checked at the end of every run, ``transparency``);
- it draws nothing itself and imposes no byte budget.

The draw methods (random_float, read_int, read_bits, read_uniform) are the base
class's, which go through read_bytes; no RNGLab source overrides them, so a
model reading the tap reads the same numbers it would read from the source.

``StreamStats`` summarises the consumed bytes for the RNG panels. It works on
copies, in bulk, at frame time.
"""
from __future__ import annotations

from typing import Any, Callable

import numpy as np

UNIFORM_BINS = 50
LAG_BINS = 32


def observed_class(random_source_base: type) -> type:
    """Build the tap class on the code source's RandomSource (imported in the worker only)."""

    class ObservedSource(random_source_base):
        def __init__(self, inner, role: str, on_read: Callable[[Any, bytes], None] | None = None):
            if not isinstance(inner, random_source_base):
                raise TypeError("ObservedSource wraps an rnglab RandomSource")
            random_source_base.__init__(self, inner.manifest, max_bytes=None)
            self.inner = inner
            self.role = role
            self.on_read = on_read
            self.inner_bytes_before = int(inner.consumed_bytes)
            self.inner_digest_before = inner.stream_digest
            self.reads = 0
            self.stats = StreamStats(role)

        def _read_bytes(self, n: int) -> bytes:
            raw = self.inner.read_bytes(n)
            self.reads += 1
            self.stats.add(raw)
            if self.on_read is not None:
                self.on_read(self, raw)
            return raw

        def transparency(self) -> dict:
            delta = int(self.inner.consumed_bytes) - self.inner_bytes_before
            same_count = delta == self.consumed_bytes
            fresh = self.inner_bytes_before == 0
            same_digest = (self.stream_digest == self.inner.stream_digest) if fresh else None
            ok = same_count and (same_digest is not False)
            return {"role": self.role, "source_id": self.manifest.source_id, "tap_bytes": int(self.consumed_bytes),
                    "source_bytes_delta": delta, "tap_sha256": self.stream_digest,
                    "source_sha256": self.inner.stream_digest, "digest_compared": fresh,
                    "state": "ok" if ok else "failed",
                    "detail": ("byte count and SHA-256 of the forwarded stream equal the source's"
                               if ok else "the tap and the source disagree")}

    return ObservedSource


class StreamStats:
    """Running summaries of one role's consumed stream (bytes, bits, 8-byte words as uniforms)."""

    def __init__(self, role: str):
        self.role = role
        self._bytes = bytearray()
        self._words = bytearray()
        self.total_bytes = 0
        self.total_reads = 0
        self.total_words = 0
        self.bit_ones = np.zeros(8, dtype=np.int64)
        self.byte_hist = np.zeros(256, dtype=np.int64)
        self.uniform_hist = np.zeros(UNIFORM_BINS, dtype=np.int64)
        self.lag_hist = np.zeros((LAG_BINS, LAG_BINS), dtype=np.int64)
        self.sum_u = 0.0
        self.sum_uu = 0.0
        self.sum_lag = 0.0
        self.lag_pairs = 0
        self._last_u: float | None = None
        self.recent_u = np.zeros(0)

    def add(self, raw: bytes) -> None:
        self.total_reads += 1
        self.total_bytes += len(raw)
        self._bytes += raw
        if len(raw) % 8 == 0:
            self._words += raw

    def flush(self) -> None:
        if self._bytes:
            data = np.frombuffer(bytes(self._bytes), dtype=np.uint8)
            self._bytes.clear()
            self.byte_hist += np.bincount(data, minlength=256)
            bits = np.unpackbits(data).reshape(-1, 8)       # column 0 = most significant bit
            self.bit_ones += bits.sum(axis=0, dtype=np.int64)[::-1]  # index 0 = least significant bit
        if self._words:
            words = np.frombuffer(bytes(self._words), dtype=">u8").astype(np.uint64)
            self._words.clear()
            u = (words >> np.uint64(11)).astype(np.float64) * 2.0 ** -53
            self.total_words += u.size
            self.uniform_hist += np.bincount(np.minimum((u * UNIFORM_BINS).astype(np.int64), UNIFORM_BINS - 1),
                                             minlength=UNIFORM_BINS)
            self.sum_u += float(u.sum())
            self.sum_uu += float((u * u).sum())
            seq = u if self._last_u is None else np.concatenate([[self._last_u], u])
            if seq.size >= 2:
                a, b = seq[:-1], seq[1:]
                ia = np.minimum((a * LAG_BINS).astype(np.int64), LAG_BINS - 1)
                ib = np.minimum((b * LAG_BINS).astype(np.int64), LAG_BINS - 1)
                np.add.at(self.lag_hist, (ib, ia), 1)
                self.sum_lag += float((a * b).sum())
                self.lag_pairs += int(a.size)
            self._last_u = float(u[-1])
            self.recent_u = np.concatenate([self.recent_u, u])[-512:]

    def summary(self) -> dict:
        self.flush()
        n = self.total_words
        mean = self.sum_u / n if n else None
        var = (self.sum_uu / n - mean * mean) if n else None
        lag_corr = None
        if self.lag_pairs > 2 and var and var > 0:
            lag_corr = (self.sum_lag / self.lag_pairs - mean * mean) / var
        expected = n / UNIFORM_BINS if n else 0
        chi2 = float(((self.uniform_hist - expected) ** 2 / expected).sum()) if expected > 0 else None
        chi2_p = None
        if chi2 is not None and expected >= 5:
            try:
                from scipy.stats import chi2 as chi2_law
                chi2_p = float(chi2_law.sf(chi2, UNIFORM_BINS - 1))
            except ImportError:  # pragma: no cover
                chi2_p = None
        total_bits = self.total_bytes
        return {
            "role": self.role, "bytes": self.total_bytes, "reads": self.total_reads, "words": n,
            "uniform_mean": mean, "uniform_var": var, "lag1_corr": lag_corr,
            "uniform_hist": self.uniform_hist.tolist(), "uniform_chi2": chi2, "uniform_chi2_p": chi2_p,
            "uniform_dof": UNIFORM_BINS - 1,
            "lag_hist": self.lag_hist.tolist() if n > 1 else None,
            "bit_fraction": (self.bit_ones / total_bits).tolist() if total_bits else None,
            "byte_hist_max": int(self.byte_hist.max()) if total_bits else 0,
        }
