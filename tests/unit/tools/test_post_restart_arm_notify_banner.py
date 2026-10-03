"""Banner-text regression pin for the Post-Restart Arm-Notify feature
(Phase 4 — T4.7 / phase4-plan D5).

The D-FA1.2 supersession (pull-model → push-wake-on-boot) is closed
in-code by the arm-return banner swap in ``daemon/tools/upgrade_tools.py``:
the obsolete "post-restart: ask me to run ``upgrade_status``" instruction
is replaced by the auto-wake prose (the arm is recorded; the boot sweep
delivers the outcome report; no user action; kill-switch documented).

This file is the **release-blocker regression pin**: a future PR that
re-introduces the obsolete pull-model instruction (e.g. a revert of the
Phase 4 banner update) fails LOUDLY here with the file, the line number,
and the offending text in the assertion message.

Shape: static source-inspection (same convention as the ADR-034
splice-discipline grep in ``test_upgrade_journal.py`` and the Phase 3
``test_post_restart_arm_notify_no_parallel.py`` structural pins) — the
grep is on FILE CONTENT, not structure, so a refactor that moves the
arm-return branch into a helper is still covered as long as the string
literals survive anywhere in the module (phase4-plan R4.5).

All assertions are on ``daemon/tools/upgrade_tools.py`` only; no daemon
boot, no DB, no live contact.
"""
from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
UPGRADE_TOOLS = REPO_ROOT / "daemon" / "tools" / "upgrade_tools.py"

# The obsolete pull-model instruction (D-FA1.2, superseded by the
# push-wake-on-boot behavior — supersession-record.md §4). Captured as the
# single source of truth for the regression pin (phase4-plan T1).
OBSOLETE_PHRASE = "ask me to run"

# The replacement auto-wake prose (phase4-plan D1). Both arm-return
# branches (system_restart + system_upgrade) must carry it.
AUTO_WAKE_PROSE = "an auto-wake will be delivered to this instance"
NO_ACTION_HINT = "no user action required"
KILL_SWITCH_HINT = "ENSEMBLE_POST_RESTART_ARM_NOTIFY=0"
KILL_SWITCH_ENV_DOC = "<install_dir>/.env"


def _banner_lines() -> list[tuple[int, str]]:
    """The source lines carrying the auto-wake prose (the banner lines)."""
    source = UPGRADE_TOOLS.read_text(encoding="utf-8")
    return [
        (lineno, line)
        for lineno, line in enumerate(source.splitlines(), 1)
        if AUTO_WAKE_PROSE in line
    ]


def test_obsolete_pull_model_instruction_absent() -> None:
    """The obsolete "ask me to run `upgrade_status`" instruction is GONE
    from upgrade_tools.py. A revert of the Phase 4 banner swap fails here
    with the exact file + line + offending text (phase4-plan T7/D5)."""
    lines = UPGRADE_TOOLS.read_text(encoding="utf-8").splitlines()
    hits = [
        f"  line {lineno}: {line.strip()}"
        for lineno, line in enumerate(lines, 1)
        if OBSOLETE_PHRASE in line
    ]
    assert not hits, (
        "D-FA1.2 supersession REGRESSION: the obsolete pull-model "
        f"instruction {OBSOLETE_PHRASE!r} re-appeared in "
        f"{UPGRADE_TOOLS}:\n" + "\n".join(hits) + "\n"
        "The auto-wake (boot sweep + wake delivery) makes the pull-model "
        "instruction obsolete — see docs/runbooks/post-restart-arm-notify.md"
    )


def test_auto_wake_prose_present_in_both_arm_branches() -> None:
    """Both arm-return branches (system_restart AND system_upgrade) carry
    the auto-wake prose — the two lanes arm the same wake machinery
    (phase4-plan T2 + T3)."""
    banner = _banner_lines()
    assert len(banner) >= 2, (
        f"expected the auto-wake prose in BOTH arm-return banners "
        f"(system_restart + system_upgrade) of {UPGRADE_TOOLS}; found "
        f"{len(banner)} occurrence(s) at lines "
        f"{[lineno for lineno, _ in banner]}"
    )


def test_banner_carries_no_user_action_and_run_id() -> None:
    """Each banner line documents the zero-user-action contract and keeps
    the run_id binding (the arming agent reads the run_id straight off
    the banner — no second lookup)."""
    banner = _banner_lines()
    assert banner, "no auto-wake banner lines found (banner removed?)"
    for lineno, line in banner:
        assert NO_ACTION_HINT in line, (
            f"banner line {lineno} lost the {NO_ACTION_HINT!r} prose"
        )
        assert "run_id=" in line, (
            f"banner line {lineno} lost the run_id binding"
        )


def test_banner_documents_kill_switch() -> None:
    """Each banner line documents the operator kill-switch (ADR-044): the
    env var AND where to set it (the install dir's .env)."""
    banner = _banner_lines()
    assert banner, "no auto-wake banner lines found (banner removed?)"
    for lineno, line in banner:
        assert KILL_SWITCH_HINT in line, (
            f"banner line {lineno} lost the kill-switch env "
            f"{KILL_SWITCH_HINT!r} (ADR-044 documentation contract)"
        )
        assert KILL_SWITCH_ENV_DOC in line, (
            f"banner line {lineno} lost the kill-switch location "
            f"{KILL_SWITCH_ENV_DOC!r}"
        )


def test_banner_lines_are_single_list_elements() -> None:
    """Structural: each banner is ONE string element in the arm-return
    ``"\\n".join([...])`` list (the Phase 4 swap replaced one line per
    branch — a future edit splitting the prose across implicit-concat
    fragments is fine as long as the greps above hold; THIS test only
    pins that the banner did not grow past one line per branch, keeping
    the return block scannable)."""
    assert len(_banner_lines()) == 2, (
        "the auto-wake prose now appears in more than the two arm-return "
        f"banners: {[lineno for lineno, _ in _banner_lines()]}"
    )
