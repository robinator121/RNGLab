"""rnglab_unified: taxonomy rules, capability matrix, read-only evidence browser, index, isolation."""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from rnglab_unified import evidence, registry, taxonomy  # noqa: E402
from rnglab_unified.index import Index  # noqa: E402


# ------------------------------------------------------------------------- taxonomy
def test_statuses_are_exactly_the_nine_labels():
    assert list(taxonomy.STATUSES) == ["implemented", "unit-tested", "independently-verified", "experimentally-validated",
                                       "exploratory", "planned", "negative-result", "superseded", "unbacked"]


@pytest.mark.parametrize("entry, fragment", [
    ({"id": "x", "kind": "Study", "status": "experimentally-validated", "evidence": ["a"],
      "frozen_protocol": {"status": "none"}}, "frozen protocol"),
    ({"id": "x", "kind": "Study", "status": "negative-result", "evidence": []}, "evidence path"),
    ({"id": "x", "kind": "Monitor", "status": "unit-tested", "test_coverage": {"test_paths": []}}, "test paths"),
    ({"id": "x", "kind": "Monitor", "status": "planned", "integration_method": "adapter"}, "planned"),
    ({"id": "x", "kind": "Study", "status": "planned", "integration_method": "code-source-worker"}, "planned"),
    ({"id": "x", "kind": "Nope", "status": "implemented"}, "unknown kind"),
    ({"id": "x", "kind": "Study", "status": "validated"}, "unknown status"),
])
def test_check_entry_flags_inconsistent_claims(entry, fragment):
    assert any(fragment in p for p in taxonomy.check_entry(entry))


def test_check_entry_accepts_a_consistent_validated_study():
    entry = {"id": "x", "kind": "Study", "status": "experimentally-validated", "evidence": ["research/x/results/r.json"],
             "frozen_protocol": {"status": "frozen"}}
    assert taxonomy.check_entry(entry) == []


# ------------------------------------------------------------------------- capability matrix (repository content)
def test_capability_matrix_loads_and_uses_only_the_taxonomy():
    matrix = registry.load_matrix(ROOT)
    assert matrix["available"], matrix["problems"]
    assert len(matrix["entries"]) >= 40
    for entry in matrix["entries"]:
        assert entry["status"] in taxonomy.STATUSES
        assert entry["kind"] in taxonomy.KINDS


def test_capability_matrix_has_no_consistency_problems():
    assert registry.load_matrix(ROOT)["problems"] == []


def test_negative_results_are_present_in_the_matrix():
    statuses = [e["status"] for e in registry.load_matrix(ROOT)["entries"]]
    assert statuses.count("negative-result") >= 5


DEFERRED = json.loads((ROOT / "docs/unified/PUBLIC_DEFERRED.json").read_text())["deferred_study_folders"]


def _deferred_and_absent(path: str) -> bool:
    """True for files of a study folder deferred from the public release when that folder is absent."""
    return any(path.startswith(d + "/") for d in DEFERRED) and not (ROOT / path).exists()


def test_matrix_evidence_paths_exist_when_reachable():
    missing = []
    for entry in registry.load_matrix(ROOT)["entries"]:
        if not entry.get("reachable_from_unified"):
            continue
        for path in entry.get("evidence") or []:
            if not (ROOT / path).exists() and not _deferred_and_absent(path):
                missing.append(f"{entry['id']}: {path}")
    marker = ROOT / "docs/unified/PUBLIC_RELEASE.json"
    if marker.exists():
        # a public release withholds some evidence files; the private repository holds them all
        withheld = json.loads(marker.read_text())["withheld_file_count"]
        assert len(missing) <= withheld, missing
    else:
        assert missing == []


def test_nuclear_layers_b_and_c_are_not_claimed():
    for entry in registry.load_matrix(ROOT)["entries"]:
        text = (entry["id"] + " " + entry.get("title", "")).lower()
        if "nuclear" in text and ("layer b" in text or "layer c" in text or "kinetic" in text or "branching" in text):
            assert entry["status"] in ("planned", "exploratory", "unbacked"), entry["id"]


# ------------------------------------------------------------------------- evidence browser (fixture)
def _study(tmp_path: Path, protocol: dict, freeze: str | None) -> Path:
    s = tmp_path / "research" / "demo-study"
    (s / "results").mkdir(parents=True)
    (s / "protocol.json").write_text(json.dumps(protocol))
    (s / "results" / "r.json").write_text('{"value": 1}')
    if freeze is not None:
        (s / "protocol.sha256").write_text(f"{freeze}  protocol.json\n")
    return s


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_freeze_ok_failed_and_unavailable(tmp_path):
    s = _study(tmp_path, {"id": "p", "status": "FROZEN", "namespace": "rnglab-x"}, None)
    assert evidence.freeze_state(s)["state"] == "unavailable"
    (s / "protocol.sha256").write_text(f"{_sha(s / 'protocol.json')}  protocol.json\n")
    assert evidence.freeze_state(s)["state"] == "ok"
    (s / "protocol.json").write_text(json.dumps({"id": "p", "status": "EDITED"}))
    assert evidence.freeze_state(s)["state"] == "failed"


def test_manifest_lines_are_checked(tmp_path):
    s = _study(tmp_path, {"id": "p"}, None)
    good = _sha(s / "results" / "r.json")
    (s / "results" / "SHA256SUMS.sha256").write_text(f"{good}  r.json\n{'0' * 64}  missing.json\n")
    detail = evidence.study_detail(tmp_path, "demo-study")
    checks = {c["name"]: c["state"] for m in detail["manifests"] for c in m["checks"]}
    assert checks == {"r.json": "ok", "missing.json": "unavailable"}
    (s / "results" / "r.json").write_text('{"value": 2}')
    detail = evidence.study_detail(tmp_path, "demo-study")
    assert {c["name"]: c["state"] for m in detail["manifests"] for c in m["checks"]}["r.json"] == "failed"


@pytest.mark.parametrize("bad", ["../secret", "/etc/passwd", "src/x.py", "research/../../x", "", "research/\x00"])
def test_paths_outside_research_and_docs_are_refused(tmp_path, bad):
    _study(tmp_path, {"id": "p"}, None)
    with pytest.raises((evidence.EvidenceError, KeyError)):
        evidence.read_file(tmp_path, bad)


def test_symlink_leaving_the_tree_is_refused(tmp_path):
    s = _study(tmp_path, {"id": "p"}, None)
    outside = tmp_path / "private.txt"
    outside.write_text("secret")
    os.symlink(outside, s / "link.txt")
    with pytest.raises(evidence.EvidenceError):
        evidence.read_file(tmp_path, "research/demo-study/link.txt")


def test_bad_study_ids_are_refused(tmp_path):
    _study(tmp_path, {"id": "p"}, None)
    for bad in ("../x", "a/b", ".hidden", ""):
        with pytest.raises(evidence.EvidenceError):
            evidence.study_detail(tmp_path, bad)


def test_large_files_are_truncated_with_full_digest(tmp_path):
    s = _study(tmp_path, {"id": "p"}, None)
    big = s / "results" / "big.txt"
    big.write_bytes(b"x" * (evidence.PREVIEW_LIMIT + 10))
    out = evidence.read_file(tmp_path, "research/demo-study/results/big.txt")
    assert out["truncated"] and len(out["text"]) == evidence.PREVIEW_LIMIT and out["sha256"] == _sha(big)


def test_evidence_module_never_opens_for_writing():
    source = (ROOT / "src" / "rnglab_unified" / "evidence.py").read_text()
    for forbidden in ('"w"', "'w'", '"wb"', '"a"', "write_text", "write_bytes", "unlink", "rmtree", "rename"):
        assert forbidden not in source


# ------------------------------------------------------------------------- real frozen studies
def test_browsing_real_studies_changes_nothing():
    before = {p: (p.stat().st_mtime_ns, p.stat().st_size) for p in (ROOT / "research").rglob("*") if p.is_file()}
    studies = evidence.list_studies(ROOT)
    for s in studies:
        evidence.study_detail(ROOT, s["id"])
    after = {p: (p.stat().st_mtime_ns, p.stat().st_size) for p in (ROOT / "research").rglob("*") if p.is_file()}
    assert before == after


def test_every_recorded_protocol_freeze_still_holds():
    """A frozen protocol.json must still match its protocol.sha256 (no silent edits to frozen studies)."""
    frozen = [s for s in evidence.list_studies(ROOT) if (ROOT / s["path"] / "protocol.sha256").exists()]
    absent = sum(1 for d in DEFERRED if not (ROOT / d / "protocol.sha256").exists())
    assert len(frozen) >= 7 - absent
    assert {s["id"]: s["freeze"]["state"] for s in frozen} == {s["id"]: "ok" for s in frozen}


# ------------------------------------------------------------------------- index
def test_index_rebuild_and_search(tmp_path):
    runs = tmp_path / "runs"
    r = runs / "20260101-000000-pi-mc-000001"
    r.mkdir(parents=True)
    (r / "config.json").write_text(json.dumps({"model": "pi-mc", "params": {"label": "abc", "namespace": "rnglab-gui-test"},
                                               "created": "2026-01-01", "code_source": {"branch": "b", "head": "h"}}))
    (r / "status.json").write_text(json.dumps({"state": "completed", "digest": "d" * 64}))
    index = Index(tmp_path / "index.sqlite")
    assert index.rebuild_runs(runs) == 1
    assert index.rebuild_studies(ROOT) >= 10
    hit = index.search("abc")
    assert [x["run_id"] for x in hit["runs"]] == [r.name] and hit["runs"][0]["origin"] == "generated"
    assert all(s["origin"] == "historical" for s in index.search("v020")["studies"])


# ------------------------------------------------------------------------- isolation
def test_unified_package_imports_no_scientific_code():
    code = ("import sys; sys.path.insert(0, %r); import rnglab_unified.registry, rnglab_unified.evidence, "
            "rnglab_unified.index, rnglab_unified.provenance; "
            "bad = [m for m in sys.modules if m == 'numpy' or m.startswith('numpy.') or "
            "(m.startswith('rnglab') and not m.startswith('rnglab_unified'))]; print(bad)") % str(ROOT / "src")
    out = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True, text=True, check=True)
    assert out.stdout.strip() == "[]"


def test_every_runnable_model_links_to_existing_matrix_entries():
    ids = {e["id"] for e in registry.load_matrix(ROOT)["entries"]}
    for model, links in registry.MODEL_LINKS.items():
        assert links, model
        assert set(links) <= ids, (model, set(links) - ids)


def test_symlinked_study_directory_is_not_listed(tmp_path):
    _study(tmp_path, {"id": "p"}, None)
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    (outside / "README.md").write_text("# private heading")
    os.symlink(outside, tmp_path / "research" / "linked-study")
    assert [s["id"] for s in evidence.list_studies(tmp_path)] == ["demo-study"]
    with pytest.raises((KeyError, evidence.EvidenceError)):
        evidence.study_detail(tmp_path, "linked-study")


def test_symlinked_files_inside_a_study_are_not_hashed(tmp_path):
    s = _study(tmp_path, {"id": "p"}, None)
    secret = tmp_path / "secret.env"
    secret.write_text("TOKEN=1")
    os.symlink(secret, s / "results" / "linked.env")
    detail = evidence.study_detail(tmp_path, "demo-study")
    assert all(not f["path"].endswith("linked.env") for f in detail["files"])


def test_manifest_entries_outside_research_are_not_read(tmp_path):
    s = _study(tmp_path, {"id": "p"}, None)
    (tmp_path / ".env").write_text("TOKEN=1")
    (s / "results" / "m.sha256").write_text(f"{'0' * 64}  ../../../.env\n")
    check = evidence.study_detail(tmp_path, "demo-study")["manifests"][0]["checks"][0]
    assert check["actual"] is None and check["state"] == "unavailable" and "outside" in check["reason"]


def test_detector_audit_of_a_real_file_reports_saturation_and_lattice(tmp_path):
    """The consent-gated detector path, end to end on a small fixture written by the test."""
    sys.path.insert(0, str(ROOT / "gui"))
    from rnglab_gui.worker.studies import detector
    rows = []
    for k in range(50):
        samples = [1200, 65535 if k % 10 == 0 else 3000 + k, 2000, 1500]   # every tenth record clips
        rows.append(" ".join(str(v) for v in (k % 2, 16 * k * k + 16, *samples)))   # timestamps on a 16-tick lattice
    path = tmp_path / "export.txt"
    path.write_text("\n".join(rows) + "\n")
    out = detector(ROOT, path=str(path), samples_per_record=4, provenance="test fixture", preview=5)
    assert out["data_class"] == "recorded" and out["records"] == 50
    assert out["records_with_ceiling_fraction"] == 0.1
    assert out["timing"]["positive_gap_gcd_raw_units"] == 16
    assert out["validated_entropy_credit_bits"] == 0
    assert out["source_sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
    assert len(out["waveforms"]) == 5 and "path" not in out.get("metadata", {})
