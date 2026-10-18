"""Docs gate for resource-cost-control Phase 8 (D5/T4).

Mechanical doc fence ONLY — exactly the assertions the phase8 plan
specifies, nothing more (no command-shape coverage; that belongs to the
owner suites). Future doc-drift turns this suite red:

  1. runbook exists + honesty columns + shape glossary + knob names +
     O7 checklist link + dry-run-first mention;
  2. hygiene checklist exists + fence markers (DRY-RUN FIRST /
     USER DECISION / never-execute banner);
  3. CHANGELOG carries an ``[Unreleased]`` section naming the commission;
  4. OOM-delta assertions: MemoryMax + MemoryHigh, swap USER-decision
     marker, tripwire evidence line (23:19:14Z / 8.4 GB / 97 %).

PATH FENCE — 2026-10-12 live-install-deletion incident class (pinned
critical-note convention; house style per tests/upgrade_test_lib.sh
FAKE_HOME/escape-guard and tests/test_resource_monitor_self_cost.py):

  * ZERO tolerance for live-shaped paths. This test is pure repo-relative
    READ-ONLY doc inspection — it needs no tmp dirs and no fixtures.
  * Every path is resolved from the repo root derived from
    ``Path(__file__).resolve().parent.parent`` ONLY.
  * ``os.environ["HOME"]`` (or any HOME-derived / install-dir path) is
    NEVER read, NEVER resolved, NEVER touched by this module.
"""

from __future__ import annotations

from pathlib import Path

# PATH FENCE: repo root from THIS FILE's location only — never $HOME,
# never any HOME-derived or live-install path (2026-10-12 incident class).
_REPO_ROOT = Path(__file__).resolve().parent.parent

_RUNBOOK = _REPO_ROOT / "docs" / "runbooks" / "resource-cost-control.md"
_CHECKLIST = _REPO_ROOT / "docs" / "runbooks" / "resource-hygiene-operator-checklist.md"
_CHANGELOG = _REPO_ROOT / "CHANGELOG.md"


def _read(path: Path) -> str:
    """Read a repo-relative doc file (fenced: arg must be under _REPO_ROOT)."""
    resolved = path.resolve()
    if _REPO_ROOT not in resolved.parents and resolved != _REPO_ROOT:
        raise AssertionError(f"path fence violated: {path} escapes repo root")
    return resolved.read_text(encoding="utf-8")


def _unreleased_section(changelog: str) -> str:
    """Slice the [Unreleased] section (heading → next '## [' heading)."""
    start = changelog.index("## [Unreleased]")
    next_heading = changelog.find("## [", start + len("## [Unreleased]"))
    return changelog[start: next_heading if next_heading != -1 else len(changelog)]


# ── 1. Runbook ───────────────────────────────────────────────────────────────


def test_runbook_exists_with_honesty_columns_section() -> None:
    text = _read(_RUNBOOK)
    for needle in ("unattributed_kb", "estimated", "coverage_pct"):
        assert needle in text, f"runbook missing honesty-column term: {needle}"


def test_runbook_has_shape_glossary_terms() -> None:
    text = _read(_RUNBOOK)
    for shape in ("spike", "ramp", "leak", "step"):
        assert shape in text, f"runbook missing anomaly shape: {shape}"


def test_runbook_names_pinned_knobs() -> None:
    text = _read(_RUNBOOK)
    for knob in ("RESOURCE_MONITOR_SAMPLE_SECONDS", "RESOURCE_MONITOR_SELF_RSS_BUDGET_MB"):
        assert knob in text, f"runbook missing knob name: {knob}"


def test_runbook_links_checklist_and_requires_dry_run_first() -> None:
    text = _read(_RUNBOOK)
    assert "docs/runbooks/resource-hygiene-operator-checklist.md" in text
    assert "DRY-RUN FIRST" in text, "runbook must carry a dry-run-first mention"


# ── 2. Hygiene checklist ─────────────────────────────────────────────────────


def test_checklist_exists_with_fence_markers() -> None:
    text = _read(_CHECKLIST)
    assert "DRY-RUN FIRST" in text, "checklist missing DRY-RUN FIRST marker"
    assert "USER DECISION" in text, "checklist missing USER DECISION marker"
    assert "never execute" in text, (
        "checklist missing the never-agent-executed banner (expected the "
        "literal 'never execute' in the banner sentence)"
    )


# ── 3. CHANGELOG ─────────────────────────────────────────────────────────────


def test_changelog_has_unreleased_section_naming_commission() -> None:
    section = _unreleased_section(_read(_CHANGELOG))
    assert "resource-cost-control" in section, (
        "CHANGELOG [Unreleased] section must name the resource-cost-control "
        "commission"
    )


# ── 4. OOM-delta assertions (2026-10-18) ─────────────────────────────────────


def test_runbook_carries_systemd_memory_bounds_strings() -> None:
    text = _read(_RUNBOOK)
    assert "MemoryMax" in text and "MemoryHigh" in text


def test_runbook_marks_swap_as_user_decision() -> None:
    text = _read(_RUNBOOK)
    assert "USER DECISION" in text, "runbook missing a USER-decision marker"
    assert "swap" in text.lower(), "runbook missing the swap USER-decision item"


def test_runbook_carries_tripwire_evidence_line() -> None:
    text = _read(_RUNBOOK)
    for evidence in ("23:19:14Z", "8.4", "97%"):
        assert evidence in text, f"runbook missing tripwire evidence: {evidence}"
