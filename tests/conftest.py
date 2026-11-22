"""Suite-wide collection rules.

Some study folders are deferred from the first public release (docs/unified/PUBLIC_DEFERRED.json).
Test modules that exercise code which loads files from a deferred study are not collected when
that study is absent; they are named in the report header so nothing disappears silently. In the
complete private repository every module is collected.
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_DEFERRED = json.loads((ROOT / "docs/unified/PUBLIC_DEFERRED.json").read_text())
_NEEDS = {  # test module -> files from deferred studies it needs at run time
    "v018/test_api_validation_scripts.py": ["research/v018-api-validation/run_campaign.py"],
    "v018/test_h1_campaign.py": ["research/v018-h1-detection/run_campaign.py"],
    "v019/test_d_pilot_lib.py": ["research/v018-h1-detection/run_campaign.py"],
    "v019/test_d_pilot_measure.py": ["research/v018-h1-detection/analyze.py"],
    "v019/test_pilot_driver.py": ["research/v018-h1-detection/run_campaign.py"],
    "v019/test_pilot_pipeline.py": ["research/v018-h1-detection/run_campaign.py"],
    "v019/test_audit_d_pilot.py": ["research/v018-h1-detection/results/gates.json"],
}
collect_ignore = [m for m, needs in _NEEDS.items() if any(not (ROOT / n).exists() for n in needs)]


def pytest_report_header(config):
    if collect_ignore:
        return [f"deferred study folders absent: {len(collect_ignore)} test modules not collected "
                f"({', '.join(collect_ignore)}); see docs/unified/PUBLIC_DEFERRED.json"]
    return []
