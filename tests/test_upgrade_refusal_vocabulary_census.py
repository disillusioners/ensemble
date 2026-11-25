"""AC-D5-13 — refusal/halt vocabulary census pin.

Provenance: every token in ``CENSUS`` is derived from the spec
``.agents/shared/planning/self-service-promote/architecture-recommendation.md``
and carries a ``spec_line`` field naming the line that quotes it. No token
was added because it happens to appear in the implementation; tokens that
cannot be traced to a spec line are NOT in this table.

A token that is absent from the implementation is a REAL FINDING and must
NOT be weakened out of the census — the census is spec-derived, and a missing
token means the implementation drifted from the spec, not that the census is
wrong.

The census pins two things (the two halves of AC-D5-13):

  1. Each expected refusal/halt vocabulary token is PRESENT (substring) in
     at least one of its designated production source files.
  2. The substitution-shape scanner ``tests/upgrade_refusal_scan.py`` reports
     0 findings over ``scripts/upgrade/*.sh``.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCANNER = ROOT / "tests" / "upgrade_refusal_scan.py"

# (token, spec_line, [designated production source files])
# spec_line = line number in architecture-recommendation.md that quotes token.
CENSUS = [
    ("watchdog_hold_unavailable", 371, ["scripts/upgrade/promote.sh", "scripts/upgrade/lib.sh", "daemon/tools/upgrade_tools.py"]),
    ("capture_invalid", 185, ["scripts/upgrade/promote.sh", "scripts/upgrade/lib.sh", "daemon/services/rollback_target_chain.py", "launcher.sh"]),
    ("rollback_target_ambiguous", 186, ["scripts/upgrade/promote.sh", "scripts/upgrade/lib.sh", "daemon/services/rollback_target_chain.py", "daemon/services/upgrade_journal_sweep.py", "launcher.sh"]),
    ("handback-budget-exhausted", 347, ["scripts/upgrade/lib.sh"]),
    ("executor_client_exit", 169, ["daemon/tools/upgrade_tools.py", "daemon/tools/upgrade_journal.py", "daemon/services/upgrade_journal_sweep.py"]),
    ("executor_liveness_unverifiable", 169, ["daemon/manager.py", "daemon/tools/upgrade_tools.py", "daemon/tools/upgrade_journal.py", "daemon/services/upgrade_journal_sweep.py"]),
    ("stale-scripts-dir", 413, ["daemon/tools/upgrade_tools.py"]),
    ("scripts-advance-unavailable", 220, ["daemon/tools/upgrade_tools.py"]),
    ("serving_green_commit", 20, ["scripts/upgrade/lib.sh", "daemon/tools/upgrade_tools.py", "daemon/tools/upgrade_journal.py", "daemon/services/upgrade_journal_sweep.py", "launcher.sh"]),
    ("rollback_requested", 186, ["scripts/upgrade/lib.sh", "daemon/tools/upgrade_journal.py", "daemon/services/rollback_target_chain.py", "daemon/services/upgrade_journal_sweep.py", "launcher.sh"]),
    ("wrongful_rollback_count", 206, ["launcher.sh"]),
    ("rollback_window_count", 404, ["scripts/upgrade/lib.sh", "scripts/upgrade/journal_splice.py", "daemon/tools/upgrade_tools.py", "daemon/tools/upgrade_journal.py", "launcher.sh"]),
    ("port-holder-outside-unit-lineage", 23, ["scripts/upgrade/lib.sh"]),
    ("NOT in the supervision-owned pid set", 59, ["scripts/upgrade/lib.sh", "daemon/services/bind_retry.py"]),
    ("already_closed", 202, ["scripts/upgrade/promote.sh", "daemon/services/upgrade_journal_sweep.py"]),
    ("previous_invalidated", 196, ["scripts/upgrade/promote.sh", "scripts/upgrade/lib.sh", "launcher.sh"]),
    ("watchdog_hold_deferred", 329, ["scripts/upgrade/lib.sh", "daemon/tools/upgrade_tools.py"]),
    ("owned-pids-outside-unit-cgroup", 129, ["scripts/upgrade/lib.sh"]),
    ("executor-inside-supervision-cgroup", 170, ["scripts/upgrade/lib.sh"]),
    ("watchdog_probe_unreadable", 150, ["scripts/upgrade/promote.sh", "scripts/upgrade/lib.sh"]),
    ("scripts-pin-drift", 220, ["daemon/tools/upgrade_tools.py"]),
    ("wrongful_rollback", 206, ["launcher.sh"]),
    ("stale-scripts", 421, ["scripts/upgrade/lib.sh", "daemon/tools/upgrade_tools.py"]),
]


def _read(path: str) -> str:
    p = ROOT / path
    if not p.is_file():
        return ""
    return p.read_text(encoding="utf-8", errors="replace")


def test_census_provenance_shape():
    assert CENSUS, "census must not be empty"
    for token, spec_line, sources in CENSUS:
        assert isinstance(token, str) and token, f"bad token: {token!r}"
        assert isinstance(spec_line, int) and spec_line > 0, f"bad spec_line for {token!r}: {spec_line!r}"
        assert isinstance(sources, list) and sources, f"no sources for {token!r}"
        # spec line must actually quote the token
        spec_text = (ROOT / ".agents/shared/planning/self-service-promote/architecture-recommendation.md").read_text(encoding="utf-8", errors="replace").splitlines()
        assert 1 <= spec_line <= len(spec_text), f"spec_line out of range for {token!r}"
        assert token in spec_text[spec_line - 1], (
            f"token {token!r} not present on spec line {spec_line}: {spec_text[spec_line-1]!r}"
        )


def test_refusal_vocabulary_present():
    missing = []
    for token, spec_line, sources in CENSUS:
        found = False
        for src in sources:
            if token in _read(src):
                found = True
                break
        if not found:
            missing.append((token, spec_line, sources))
    assert not missing, "refusal/halt vocabulary token absent from designated sources: " + repr(missing)


def test_shape_scanner_clean():
    assert SCANNER.is_file(), f"scanner missing: {SCANNER}"
    proc = subprocess.run(
        [sys.executable, str(SCANNER), str(ROOT)],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, (
        f"upgrade_refusal_scan.py exited {proc.returncode}\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}"
    )
