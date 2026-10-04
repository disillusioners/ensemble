"""Drill smoke — Post-Restart Arm-Notify (Phase 4 T8).

The bash drill (``test/drills/post_restart_arm_notify_drill.sh``) is the
operator's acceptance check (six sandbox scenarios D1–D6, structured log,
exit 0 = green — test-strategy.md §3.2). This smoke pins the drill's
reachability + exit-code contract inside pytest: a future PR that breaks
any scenario (or the driver, or the sandbox layout) fails LOUDLY here.

Plan note (phase4-plan T8 expected to "unskip" a Phase 3 placeholder, but
no placeholder was committed in Phase 3's implementation) — this file
AUTHORS the smoke directly at the planned contract: invoke the drill in a
sandbox run dir, assert the bash exit code is 0.

Isolation: the drill is fully sandboxed (fake homes under the run dir,
in-process driver, no DB / network / daemon boot / live contact — the D6
live-refusal scenario uses the FAKE-live marker, never the real install).
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
DRILL = REPO_ROOT / "test" / "drills" / "post_restart_arm_notify_drill.sh"


def test_drill_smoke(tmp_path: Path) -> None:
    """The arm-notify drill exits 0 on a sandbox run dir (AC1–AC5 + the
    live-outright-refusal inheritance, end-to-end)."""
    assert DRILL.is_file(), f"drill missing: {DRILL}"
    run_dir = tmp_path / "ens-wake-drill-smoke"
    proc = subprocess.run(
        ["bash", str(DRILL), str(run_dir)],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=300,  # the drill's own scenarios are in-process + fast
    )
    assert proc.returncode == 0, (
        "post_restart_arm_notify drill FAILED (exit "
        f"{proc.returncode})\n--- tail ---\n{proc.stdout[-3000:]}\n{proc.stderr[-1000:]}"
    )
    # Structured-log contract (test-strategy.md §3.2): per-scenario PASS
    # lines + the completion banner are grep-able in the transcript.
    for scenario in ("D1", "D2", "D3", "D4", "D5", "D6"):
        assert f"PASS: {scenario}: scenario green" in proc.stdout, (
            f"drill transcript lacks the {scenario} PASS line"
        )
    assert "DRILLS COMPLETE" in proc.stdout
    assert "0 failed" in proc.stdout
