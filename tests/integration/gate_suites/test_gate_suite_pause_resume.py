"""Gate-suite enumeration gate — PR1 dry-run (plan line 96 + risk line 201).

Spec — ``.agents/shared/planning/langgraph-checkpoint-perf/phase1-plan.md``
line 96: "Enumerate-by-filename: assert the gate suites listed below all
pass on PR1's branch. ... PR1 contains a dry-run of this enumeration
gate; PR2 + PR3 + PR4 re-run it in CI."

DRY-RUN contract (this file, in PR1):
  * every path listed in ``GATE_SUITES.txt`` EXISTS on disk, and
  * every listed file is COLLECTABLE by pytest (a single BOUNDED
    ``pytest --collect-only`` subprocess over the whole list; exit 0),
  * every listed file contributes at least one collected test id.

The test deliberately does NOT run the suites themselves (the plan's
"must NOT spawn full recursive pytest runs of every suite on every test
invocation" constraint). Running the suites green is a separate,
dispatcher-owned verification step performed once per PR (see the PR1
report's gate-run table).

The single source of truth for the list is
``tests/integration/gate_suites/GATE_SUITES.txt`` (one path per line,
``#`` comments). PR1 creates it; subsequent PRs append.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
MANIFEST = Path(__file__).resolve().parent / "GATE_SUITES.txt"


def _parse_manifest() -> list[Path]:
    """Parse GATE_SUITES.txt → list of repo-relative Paths (comments stripped)."""
    lines = MANIFEST.read_text(encoding="utf-8").splitlines()
    entries = []
    for raw in lines:
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        entries.append(Path(stripped))
    return entries


def test_gate_suite_enumeration_passes():
    """Every manifest entry exists and is collectable by pytest (dry-run)."""
    entries = _parse_manifest()

    # (1) Manifest is non-trivial: covers all 6 gate concepts.
    assert len(entries) >= 10, (
        f"GATE_SUITES.txt lists only {len(entries)} entries — expected the "
        "full Phase 1 gate set (pause/resume, turn-reconciler, resume-from-"
        "checkpoint, 8-mirror-tables, aupdate_state, get_messages lifecycle)"
    )

    # (2) Every listed path exists.
    missing = [str(e) for e in entries if not (REPO_ROOT / e).exists()]
    assert not missing, f"Gate-suite files missing from the repo: {missing}"

    # (3) Single BOUNDED collect-only subprocess over the whole list.
    #     -o addopts="" strips the default '-m not integration' filter so
    #     integration-marked gate files are collected too (collection ≠ run).
    #     -p no:cacheprovider avoids .pytest_cache contention when the outer
    #     pytest run holds the cache dir.
    cmd = [
        sys.executable,
        "-m",
        "pytest",
        "--collect-only",
        "-q",
        "-o",
        "addopts=",
        "-p",
        "no:cacheprovider",
        "--no-header",
        *[str(e) for e in entries],
    ]
    proc = subprocess.run(
        cmd,
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=240,
    )
    assert proc.returncode == 0, (
        "Gate-suite collect-only failed:\n"
        f"stdout:\n{proc.stdout[-3000:]}\nstderr:\n{proc.stderr[-2000:]}"
    )

    # (4) Every listed file contributed at least one collected test id.
    #     In -q collect-only mode pytest prints one test id per line
    #     (path::test[::case]). Build the set of files that produced ids.
    contributed_files: set[str] = set()
    for line in proc.stdout.splitlines():
        line = line.strip()
        if "::" in line:
            file_part = line.split("::", 1)[0]
            contributed_files.add(file_part)
    not_contributing = [
        str(e)
        for e in entries
        if str(e) not in contributed_files
        and f"{e}" not in contributed_files
    ]
    assert not not_contributing, (
        "Gate-suite files that collected ZERO tests (missing tests or "
        f"collection-filtered): {not_contributing}"
    )


def test_gate_suite_manifest_concepts_covered():
    """The manifest maps every plan gate concept to ≥1 canonical file.

    Guard against accidental manifest truncation: each of the 6 concepts
    from the plan's gate list must have at least one file whose path or
    the manifest's comment block references it.
    """
    entries = _parse_manifest()
    joined = "\n".join(str(e) for e in entries)
    concepts = {
        "pause/resume": "pause" in joined.lower() and "resume" in joined.lower(),
        "turn-reconciler": "turn_reconciler" in joined,
        "resume-from-checkpoint (interrupt/human-approval/is_retry)": (
            "resume" in joined.lower() and "checkpoint" in joined.lower()
        ),
        "8-mirror-tables": "turn_reconciler" in joined
        or "mirror" in joined.lower(),
        "aupdate_state idempotent": "compaction" in joined.lower(),
        "get_messages lifecycle": "api_messages" in joined
        or "persistence" in joined.lower(),
    }
    uncovered = [name for name, ok in concepts.items() if not ok]
    assert not uncovered, f"Gate concepts with no canonical file: {uncovered}"
