"""Docs gate for resource-cost-control P7 AC6: O7 operator-checklist fence.

Mechanical doc fence ONLY — exactly the assertions the P7 plan specifies for
the O7 hygiene checklist, nothing more (no command-shape coverage; that
belongs to the owner suites). Future doc-drift on the load-bearing items
turns this suite red.

Cover points (load-bearing items the checklist MUST keep carrying, pinned
per item so wording drift on facts trips the test):

  (a) systemd memory bounds: ``MemoryHigh=7G`` + ``MemoryMax=8G`` drop-in
  (b) swap provisioning — USER DECISION (heading marker locks the scope)
  (c) test-DB cleanup — ``DRY-RUN FIRST`` + ``age_days > 7`` gate + all four
      in-scope prefixes (``ensemble_blob_prune_*`` · ``check_score_*``
      · ``debug_*`` · ``repro_*``)
  (d) orphan context7-mcp kill — ppid==1 / reparented-to-init identification
  (e) demo daemon keep/kill — USER DECISION (heading marker)
  (f) ``MALLOC_ARENA_MAX=2`` + adopt-unit deferred-routing wording (the live
      unit predates O1; reaches the live host at the next
      adoption/unit-regeneration pass)
  (g) O1 effect verification — ``T+24 h`` and ``T+72 h`` read-only protocol
      via ``/api/resources/history``

PATH FENCE — 2026-10-12 live-install-deletion incident class (pinned
critical-note convention; house style per tests/upgrade_test_lib.sh
FAKE_HOME/escape-guard and tests/test_phase8_docs_gate.py):

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

_CHECKLIST = _REPO_ROOT / "docs" / "runbooks" / "resource-hygiene-operator-checklist.md"


def _read(path: Path) -> str:
    """Read a repo-relative doc file (fenced: arg must be under _REPO_ROOT)."""
    resolved = path.resolve()
    if _REPO_ROOT not in resolved.parents and resolved != _REPO_ROOT:
        raise AssertionError(f"path fence violated: {path} escapes repo root")
    return resolved.read_text(encoding="utf-8")


# ── Existence + escape-guard ──────────────────────────────────────────────────


def test_checklist_resolves_under_repo_root() -> None:
    """Path fence: checklist must be inside the worktree, never $HOME-shaped."""
    resolved = _CHECKLIST.resolve()
    assert _REPO_ROOT in resolved.parents, (
        f"checklist path escapes repo root: {resolved}"
    )
    assert resolved.is_file(), f"checklist missing at {resolved}"


# ── (a) systemd memory bounds ─────────────────────────────────────────────────


def test_checklist_systemd_drop_in_has_both_bounds() -> None:
    """Item (a): ``MemoryHigh=7G`` soft throttle + ``MemoryMax=8G`` hard cap."""
    text = _read(_CHECKLIST)
    assert "MemoryHigh=7G" in text, (
        "checklist (a) missing the MemoryHigh=7G drop-in value"
    )
    assert "MemoryMax=8G" in text, (
        "checklist (a) missing the MemoryMax=8G drop-in value"
    )


# ── (b) Swap provisioning — USER DECISION ─────────────────────────────────────


def test_checklist_swap_block_marks_user_decision() -> None:
    """Item (b): swap is a USER DECISION — heading marker locks the scope.

    Pins the exact heading so a wording refactor that demotes swap below
    the USER-DECISION status (or relocates it) trips the test.
    """
    text = _read(_CHECKLIST)
    assert "(b) Swap provisioning — **USER DECISION**" in text, (
        "checklist (b) heading must lock '(b) Swap provisioning — "
        "**USER DECISION**'"
    )


# ── (c) Test-database cleanup (DRY-RUN FIRST, pattern + age gated) ────────────


def test_checklist_testdb_dryrun_pattern_and_age_markers() -> None:
    """Item (c): DRY-RUN FIRST + ``age_days > 7`` gate + all four prefixes.

    Pins both gates (DRY-RUN FIRST, age_days > 7) AND every in-scope
    pattern prefix so adding/removing a prefix trips the test.
    """
    text = _read(_CHECKLIST)
    assert "DRY-RUN FIRST" in text, "checklist (c) missing DRY-RUN FIRST"
    assert "age_days > 7" in text, "checklist (c) missing the age_days > 7 gate"
    for prefix in (
        "ensemble_blob_prune_*",
        "check_score_*",
        "debug_*",
        "repro_*",
    ):
        assert prefix in text, (
            f"checklist (c) missing in-scope pattern prefix: {prefix}"
        )


# ── (d) Orphan context7-mcp kill ──────────────────────────────────────────────


def test_checklist_orphan_kill_identifies_parent_dead() -> None:
    """Item (d): orphan identification — context7 target + ppid==1 reparent.

    Pins the target (context7-mcp) AND the parent-dead identification
    marker (``reparented to init`` on the ppid==1 line) so a refactor that
    loses the ppid-based identification trips the test.
    """
    text = _read(_CHECKLIST)
    assert "context7-mcp" in text, (
        "checklist (d) missing the context7-mcp orphan target"
    )
    assert "reparented to init" in text, (
        "checklist (d) missing the parent-dead identification marker "
        "(expected the literal 'reparented to init' on the ppid==1 line)"
    )


# ── (e) Demo daemon keep/kill — USER DECISION ─────────────────────────────────


def test_checklist_demo_daemon_user_decision() -> None:
    """Item (e): demo daemon keep/kill is a USER DECISION — heading marker.

    Same shape as item (b): pins the exact heading so a wording refactor
    that demotes this below USER-DECISION status trips the test.
    """
    text = _read(_CHECKLIST)
    assert "(e) Demo daemon keep/kill — **USER DECISION**" in text, (
        "checklist (e) heading must lock '(e) Demo daemon keep/kill — "
        "**USER DECISION**'"
    )


# ── (f) MALLOC_ARENA_MAX=2 + adopt-unit deferred-routing note ────────────────


def test_checklist_malloc_arena_note_for_adopt_unit() -> None:
    """Item (f): ``MALLOC_ARENA_MAX=2`` + adopt-unit deferred-routing wording.

    The template line landed in O1 (scripts/systemd/ensemble-daemon.service:106);
    the live unit predates that, so the value reaches the live host only at
    the next ``adoption/unit-regeneration`` pass. Both facts are pinned.
    """
    text = _read(_CHECKLIST)
    assert "MALLOC_ARENA_MAX=2" in text, (
        "checklist (f) missing the MALLOC_ARENA_MAX=2 environment note"
    )
    assert "adoption/unit-regeneration" in text, (
        "checklist (f) must carry the adopt-unit deferred-routing wording "
        "(the live unit predates O1; reaches the live host at the next "
        "adoption/unit-regeneration pass)"
    )


# ── (g) O1 effect verification protocol ──────────────────────────────────────


def test_checklist_o1_verification_protocol_T24_T72() -> None:
    """Item (g): read-only T+24h + T+72h protocol via /api/resources/history.

    Pins both verification points so collapsing or renaming one trips
    the test. The decision rule + O3 fallback knob (``LIMITS_INSTANCE_CACHE_TTL_HOURS``)
    round out the load-bearing wiring that decides whether O1 holds.
    """
    text = _read(_CHECKLIST)
    assert "T+24 h" in text, "checklist (g) missing T+24 h verification point"
    assert "T+72 h" in text, "checklist (g) missing T+72 h verification point"
    assert "LIMITS_INSTANCE_CACHE_TTL_HOURS" in text, (
        "checklist (g) missing the O3 fallback knob reference "
        "(LIMITS_INSTANCE_CACHE_TTL_HOURS)"
    )
