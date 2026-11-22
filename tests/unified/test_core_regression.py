"""Consolidation must not change historical computations: bit-identity regression tests.

Every model adapter, source factory and fault wrapper of ``rnglab_core`` is compared with a direct
call of the canonical implementation on the same seeds. Where a frozen golden exists (PIMC), the
adapter is held to it. Seeds here are test labels (no study or reserved namespace).
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from rnglab_core import faults, models, sources  # noqa: E402
from rnglab_core.streams import WordSplitter  # noqa: E402

GOLDEN = json.loads((ROOT / "tests/v018/data/pimc_default_golden.json").read_text())


def _golden_seed(case: str, role: str) -> int:   # identical to tests/v018/test_pimc_default_golden.py
    return int.from_bytes(hashlib.sha256(f"rnglab-v018-tests/pimc-default-golden/{case}/{role}".encode()).digest()[:16], "big")


@pytest.mark.parametrize("case, beads, temperature, delta, sweeps, burn_in, layout", [
    ("applications_M4", 4, 0.5, 0.5, 32, 0, "separate"),
    ("phase2_channels", 16, 0.5, 0.4, 256, 64, "separate"),
    ("single_source_M4", 4, 0.5, 0.5, 64, 16, "shared"),
])
def test_pimc_adapter_reproduces_the_frozen_golden(case, beads, temperature, delta, sweeps, burn_in, layout):
    if layout == "shared":
        src = {"shared": sources.make("pcg64", _golden_seed(case, "single"), "test")}
        roles = {"proposal": src["shared"], "acceptance": src["shared"], "initialization": src["shared"]}
    else:
        src = {r: sources.make("pcg64", _golden_seed(case, r), "test") for r in ("proposal", "acceptance", "initialization")}
        roles = src
    out = models.MODELS["pimc-harmonic"].run(src, {"beads": beads, "beta": 1.0 / temperature, "delta": delta,
                                                    "sweeps": sweeps, "burn_in": burn_in})
    golden = GOLDEN["cases"][case]
    assert out.model_digest == golden["result"]["provenance_hash"]
    assert repr(out.observables["virial_energy_mean"]) == repr(golden["result"]["virial_energy_mean"])
    for role in ("proposal", "acceptance", "initialization"):
        assert roles[role].consumed_bytes == golden["sources_after"][role]["consumed_bytes"]
        assert roles[role].stream_digest == golden["sources_after"][role]["stream_digest"]


def _direct_channels(seed0: int):
    from rnglab14.sources.base import StochasticChannels
    from rnglab14.sources.reference import PCG64Source
    return StochasticChannels(PCG64Source(seed0), PCG64Source(seed0 + 1), PCG64Source(seed0 + 2))


def test_ising_adapter_equals_direct_call():
    from rnglab14.models.ising_2d import Ising2D
    direct = Ising2D(L=4, temperature=2.5).run_simulation(_direct_channels(11), sweeps=300, burn_in=50)
    src = {r: sources.make("pcg64", 11 + k, "t") for k, r in enumerate(("proposal", "acceptance", "initialization"))}
    out = models.MODELS["ising-2d"].run(src, {"L": 4, "temperature": 2.5, "sweeps": 300, "burn_in": 50})
    assert repr(out.observables["energy_mean"]) == repr(direct.energy_mean)
    assert repr(out.observables["susceptibility_abs_m"]) == repr(direct.susceptibility)


def test_vmc_adapter_equals_direct_call():
    from rnglab14.models.vmc_harmonic import HarmonicOscillatorVMC
    direct = HarmonicOscillatorVMC(alpha=0.5, delta=1.0).run_simulation(_direct_channels(21), steps=2000, burn_in=200)
    src = {r: sources.make("pcg64", 21 + k, "t") for k, r in enumerate(("proposal", "acceptance", "initialization"))}
    out = models.MODELS["vmc-harmonic"].run(src, {"alpha": 0.5, "delta": 1.0, "steps": 2000, "burn_in": 200})
    assert repr(out.observables["x2_mean"]) == repr(direct.x2_mean)


@pytest.mark.parametrize("engine", ["nuclear-b2", "nuclear-a"])
def test_word_splitter_is_transparent_on_the_nuclear_engines(engine):
    """Bulk reads split into single words give the same bytes and the same engine digest."""
    from rnglab.nuclear.streams import NumpyPCG64Source, WordStream
    adapter = models.MODELS[engine]
    params = dict(adapter.defaults, population=3000)
    plain = adapter.run({"decay": NumpyPCG64Source(5)}, params)
    split_source = NumpyPCG64Source(5)
    split = adapter.run({"decay": WordSplitter(split_source)}, params)
    assert plain.model_digest is not None and split.model_digest == plain.model_digest
    assert plain.observables["final_populations"] == split.observables["final_populations"]


def test_word_splitter_bytes_equal_bulk_bytes():
    a, b = sources.make("numpy-pcg64", 9, "t"), sources.make("numpy-pcg64", 9, "t")
    bulk = a.read_bytes(8 * 1000)
    split = WordSplitter(b).read_bytes(8 * 1000)
    assert bulk == split and a.stream_digest == b.stream_digest


def test_byte_fault_from_registry_equals_direct_degraded_source():
    from rnglab14.sources.faults import DegradedFaultSource
    from rnglab14.sources.reference import PCG64Source
    direct = DegradedFaultSource(PCG64Source(3), bias_delta=0.02).read_bytes(4096)
    via = faults.wrap("byte-bias", sources.make("pcg64", 3, "t"), {"bias_delta": 0.02}, lambda k: 0).read_bytes(4096)
    assert via == direct


@pytest.mark.parametrize("family, params", [("SHIFT", {"severity": 0.02}), ("LAG1", {"severity": 0.3}),
                                            ("DUP", {"severity": 0.1}), ("GFSR", {"gfsr_pair": (250, 103)})])
def test_word_faults_from_registry_equal_the_frozen_h1lib(family, params):
    """The registry wraps the frozen FaultyWords itself; its words equal a direct construction."""
    from rnglab14.sources.reference import PCG64Source
    lib = faults.h1lib()
    severity = params.get("severity", params.get("gfsr_pair"))
    direct = lib.FaultyWords(PCG64Source(4), family, severity, 101, 202, onset_bytes=1024)
    via = faults.wrap(family, sources.make("pcg64", 4, "t"), {**params, "onset_bytes": 1024},
                      lambda kind: {"fault": 101, "gfsr": 202}[kind])
    assert b"".join(direct.read_bytes(8) for _ in range(800)) == b"".join(via.read_bytes(8) for _ in range(800))


def test_frozen_h1lib_is_loaded_from_its_frozen_file_unchanged():
    lib = faults.h1lib()
    assert Path(lib.__file__).resolve() == (ROOT / "research/v018-h1-detection/h1lib.py").resolve()
    manifest = ROOT / "research/v018-h1-detection/protocol.sha256"
    if not manifest.exists():
        pytest.skip("Stage C study folder deferred from this release (docs/unified/PUBLIC_DEFERRED.json)")
    recorded = {line.split()[1]: line.split()[0] for line in manifest.read_text().splitlines() if line.strip()}
    # the protocol file is frozen; h1lib is part of the study and must still be the committed bytes
    import subprocess
    committed = subprocess.run(["git", "show", "HEAD:research/v018-h1-detection/h1lib.py"], cwd=ROOT,
                               capture_output=True, check=True).stdout
    assert (ROOT / "research/v018-h1-detection/h1lib.py").read_bytes() == committed
    assert "protocol.json" in recorded


def test_two_pcg64_implementations_are_distinct_streams():
    """Documented incompatibility: same integer seed, different streams. They are never interchanged."""
    assert sources.make("pcg64", 7, "t").read_bytes(32) != sources.make("numpy-pcg64", 7, "t").read_bytes(32)
    from rnglab.nuclear.streams import NumpyPCG64Source
    assert NumpyPCG64Source(7).random_float() == np.random.default_rng(7).random()
