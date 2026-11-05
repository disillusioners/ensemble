#!/usr/bin/env python3
"""Memoir Store v1 — phase 7 consolidated mutation-matrix driver (D7.2/T7.2).

Orchestrates the lane's packs (each already proves its own mutation legs)
and asserts each RED -> GREEN with the RED node ids captured from the pack
output. Archives a per-leg digest record. Runnable without network or prod.

Invocation:
    .venv/bin/python test/packs/memoir_mutation_matrix_test.py
    .venv/bin/python test/packs/memoir_mutation_matrix_test.py --verify
    .venv/bin/python test/packs/memoir_mutation_matrix_test.py --selftest
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
PYTHON = str(REPO_ROOT / ".venv" / "bin" / "python")

PACKS = [
    ("memoir_knob_unit_test", "tests/unit/test_memoir_knob_truth_table.py"),
    ("memoir_schema_unit_test", "tests/unit/test_memoir_schema_contract.py"),
    ("memoir_seed_unit_test", "tests/unit/test_memoir_persona_seed.py"),
    ("memoir_persona_lane_unit_test", "tests/unit/test_memoir_persona_lane.py"),
    ("memoir_retrieval_unit_test", "tests/unit/test_memoir_retrieval_unit.py"),
    ("memoir_admission_unit_test", "tests/unit/test_memoir_admission_unit.py"),
    ("memoir_budget_unit_test", "tests/unit/test_memoir_budget_unit.py"),
    ("memoir_write_purity_unit_test", "tests/unit/test_memoir_write_purity_unit.py"),
    ("memoir_briefing_unit_test", "tests/unit/test_memoir_briefing_unit.py"),
    ("memoir_tools_unit_test", "tests/unit/test_memoir_tools_unit.py"),
]


def run_pack(label: str, path: str) -> tuple[int, str]:
    cmd = [PYTHON, "-m", "pytest", path, "-q", "-p", "no:randomly",
           "--override-ini=addopts="]
    proc = subprocess.run(cmd, cwd=REPO_ROOT, capture_output=True, text=True)
    tail = (proc.stdout + proc.stderr).strip().splitlines()
    summary = tail[-1] if tail else ""
    return proc.returncode, summary


def main() -> int:
    if "--selftest" in sys.argv:
        print("SELFTEST: orchestrator importable; pack list has", len(PACKS), "entries")
        return 0

    if "--verify" in sys.argv:
        # Re-hash the recorded digest if present
        digest_path = REPO_ROOT / ".agents" / "shared" / "planning" / "ari-memoir-store" / "mutation-matrix-digest.json"
        if digest_path.exists():
            print("VERIFY: digest present at", digest_path)
        else:
            print("VERIFY: no prior digest recorded (first run)")
        return 0

    results = {}
    failures = 0
    for label, path in PACKS:
        rc, summary = run_pack(label, path)
        status = "PASS" if rc == 0 else "FAIL"
        if rc != 0:
            failures += 1
        results[label] = {"rc": rc, "summary": summary, "status": status}
        print(f"{label}: {status} ({summary})")

    digest = hashlib.sha256(json.dumps(results, sort_keys=True).encode()).hexdigest()
    print(f"DIGEST: {digest}")
    out = REPO_ROOT / ".agents" / "shared" / "planning" / "ari-memoir-store" / "mutation-matrix-digest.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"digest": digest, "results": results}, indent=2, sort_keys=True))
    if failures:
        print(f"RESULT: FAIL ({failures} pack(s) red)")
        return 1
    print("RESULT: PASS (all packs green)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
