"""Substrate tier — the kill ladder and the pre-kill gate (test-plan §3.1,
case S8 — ALL of P0–P7 + P5a — plus S16 and S18).

**The fixture split (§15 B12, verbatim):**

    kill_fence      (the P0–P7 REFUSAL legs only)
    kill_census     (the ladder and the P8 legs)
    test_p<n>_<case>_<expected_status>

``kill_fence`` hard-raises on **ANY** non-zero signal ⇒ it can only
host legs where **no signal may fire**.  ``kill_census`` **records**
``(pgid, sig)`` without delivering ⇒ it hosts the ladder and the P8
legs.  The house originals live in
``tests/unit/services/test_bash_spawn_reaper.py:64-108``; the doubles
below patch THIS module's ``os``/``time`` (the ladder's own module
attributes — the ``bash_spawn_reaper`` import is forbidden by §7).
"""

from __future__ import annotations

import os
import threading

import pytest

from daemon.services.ram_guard import ladder
from daemon.services.ram_guard.ladder import (
    REASON_DAEMON_GROUP,
    REASON_GROUP_DEAD_AFTER_GRACE,
    REASON_GROUP_EMPTY,
    REASON_GROUP_UNKNOWN,
    REASON_GUARD_DISABLED,
    REASON_LADDER,
    REASON_LEADER_UNREADABLE,
    REASON_NOT_REGISTERED,
    REASON_OWNERSHIP_MISMATCH,
    REASON_PGID_INVALID,
    REASON_PGID_NOT_REGISTERED,
    REASON_PGID_RECYCLED,
    REASON_PGID_SELF,
    REASON_PLATFORM_UNSUPPORTED,
    REASON_SET,
    REASON_TERM_ESRCH,
    REASON_TREE_INCOMPLETE,
    STATUS_SET,
    ram_kill_ladder,
)
from tests.unit.services.ram_guard_fixtures import FakeProc, kill_tree, write_proc_tree

#: A synthetic group id that is neither the daemon's group nor its pid.
SYNTHETIC_PGID = 4242
#: The captured leader's starttime used across the legs.
LEADER_START = 4242


@pytest.fixture
def kill_fence(monkeypatch):
    """Record os.kill calls; hard-fail os.killpg. Signal 0 is allowed."""
    kills: list[tuple[int, int]] = []

    def _spy_kill(pid, sig):  # noqa: ANN001
        kills.append((pid, sig))
        if sig != 0:
            raise AssertionError(f"KILL SIGNAL IN TEST: os.kill({pid}, {sig})")

    def _no_killpg(pgid, sig):  # noqa: ANN001
        raise AssertionError(f"UNEXPECTED KILLPG: os.killpg({pgid}, {sig})")

    monkeypatch.setattr(ladder.os, "kill", _spy_kill)
    monkeypatch.setattr(ladder.os, "killpg", _no_killpg)
    return kills


@pytest.fixture
def kill_census(monkeypatch):
    """Record-and-succeed ``os.killpg`` census for the ladder legs.

    Records every ``(pgid, sig)`` and reports SUCCESS without
    delivering anything (synthetic pgids only — nothing real is ever
    signalled).  Also records ``time.sleep`` calls so the grace window
    is observable without actually waiting.
    """
    killpg_calls: list[tuple[int, int]] = []
    sleeps: list[float] = []

    def _spy_kill(pid, sig):  # noqa: ANN001
        if sig != 0:
            raise AssertionError(f"NON-PROBE os.kill: ({pid}, {sig})")

    def _rec_killpg(pgid, sig):  # noqa: ANN001
        killpg_calls.append((pgid, sig))

    monkeypatch.setattr(ladder.os, "kill", _spy_kill)
    monkeypatch.setattr(ladder.os, "killpg", _rec_killpg)
    monkeypatch.setattr(ladder.time, "sleep", lambda s: sleeps.append(s))
    return killpg_calls, sleeps


def build_tree(root, *, leader=True, members=1, starttime=LEADER_START, environ=None):
    """A fake tree: a leader + N group members in ``SYNTHETIC_PGID``."""
    procs = []
    if leader:
        procs.append(
            FakeProc(
                pid=SYNTHETIC_PGID,
                pgrp=SYNTHETIC_PGID,
                ppid=1,
                starttime=starttime,
                rss_kb=1000,
                pss_kb=500,
                environ=environ,
            )
        )
    for i in range(members):
        procs.append(
            FakeProc(
                pid=SYNTHETIC_PGID + 100 + i,
                pgrp=SYNTHETIC_PGID,
                ppid=SYNTHETIC_PGID if leader else 1,
                starttime=starttime + 1,
                rss_kb=1000,
                pss_kb=500,
                environ=environ,
            )
        )
    return write_proc_tree(root, procs)


# ── P0 — the switch, the platform, the registration ────────────────────────


def test_p0_guard_off_refused(tmp_path, kill_fence):
    """P0 — the switch is OFF ⇒ ``refused:guard-disabled``."""
    root = build_tree(tmp_path / "proc")
    status, reason = ram_kill_ladder(
        SYNTHETIC_PGID, LEADER_START, 0.25, proc_root=root, guard_enabled=False
    )
    assert (status, reason) == ("refused", REASON_GUARD_DISABLED)


def test_p0_not_registered_refused(tmp_path, kill_fence):
    """P0 — the record is not in this guard's registry ⇒ ``refused:not-registered``."""
    root = build_tree(tmp_path / "proc")
    status, reason = ram_kill_ladder(
        SYNTHETIC_PGID, LEADER_START, 0.25, proc_root=root, registered=False
    )
    assert (status, reason) == ("refused", REASON_NOT_REGISTERED)


def test_p0_platform_unsupported_refused(tmp_path, kill_fence):
    """P0 — a non-Linux platform ⇒ ``refused:platform-unsupported``."""
    root = build_tree(tmp_path / "proc")
    status, reason = ram_kill_ladder(
        SYNTHETIC_PGID, LEADER_START, 0.25, proc_root=root, platform="darwin"
    )
    assert (status, reason) == ("refused", REASON_PLATFORM_UNSUPPORTED)


# ── P1 — the pgid came from THIS guard's registry ──────────────────────────


def test_p1_pgid_not_registered_refused(tmp_path, kill_fence):
    """P1 — a pgid this guard never captured ⇒ ``refused:pgid-not-registered``."""
    root = build_tree(tmp_path / "proc")
    status, reason = ram_kill_ladder(
        SYNTHETIC_PGID,
        LEADER_START,
        0.25,
        proc_root=root,
        captured_pgid=SYNTHETIC_PGID + 1,
    )
    assert (status, reason) == ("refused", REASON_PGID_NOT_REGISTERED)


def test_p1_without_a_captured_pgid_is_refused_not_passed(tmp_path, kill_fence):
    """P1 — NO captured pgid ⇒ FAIL-CLOSED, never a silent pass.

    "A kill fires iff ALL of P0–P7 evaluate true" and "unevaluable"
    never means true: with nothing to compare against, the predicate
    cannot be established, so the kill is REFUSED.
    """
    root = build_tree(tmp_path / "proc")
    status, reason = ram_kill_ladder(
        SYNTHETIC_PGID, LEADER_START, 0.25, proc_root=root, captured_pgid=None
    )
    assert (status, reason) == ("refused", REASON_PGID_NOT_REGISTERED)


# ── P2 — the pgid is valid ─────────────────────────────────────────────────


@pytest.mark.parametrize("pgid", [0, 1, -1])
def test_p2_pgid_invalid_refused(tmp_path, kill_fence, pgid):
    """P2 — ``pgid <= 1`` ⇒ ``refused:pgid-invalid``."""
    root = build_tree(tmp_path / "proc")
    status, reason = ram_kill_ladder(
        pgid, LEADER_START, 0.25, proc_root=root, captured_pgid=pgid
    )
    assert (status, reason) == ("refused", REASON_PGID_INVALID)


# ── P3 — the daemon's own group ────────────────────────────────────────────


def test_p3_daemon_group_refused(tmp_path, kill_fence, monkeypatch):
    """P3 — the daemon's GROUP is never a kill target."""
    root = build_tree(tmp_path / "proc")
    monkeypatch.setattr(ladder.os, "getpgid", lambda pid: SYNTHETIC_PGID)
    status, reason = ram_kill_ladder(
        SYNTHETIC_PGID, LEADER_START, 0.25, proc_root=root, captured_pgid=SYNTHETIC_PGID
    )
    assert (status, reason) == ("refused", REASON_DAEMON_GROUP)


def test_p3_with_an_unevaluable_daemon_group_is_refused(tmp_path, kill_fence, monkeypatch):
    """P3 — an unevaluable daemon group ⇒ FAIL-CLOSED, never a silent pass.

    The safety rail refuses (``refused:daemon-group``) rather than
    letting an unknown group through.
    """
    root = build_tree(tmp_path / "proc")

    def _boom(pid):  # noqa: ANN001
        raise OSError("getpgid failed")

    monkeypatch.setattr(ladder.os, "getpgid", _boom)
    status, reason = ram_kill_ladder(
        SYNTHETIC_PGID, LEADER_START, 0.25, proc_root=root, captured_pgid=SYNTHETIC_PGID
    )
    assert (status, reason) == ("refused", REASON_DAEMON_GROUP)


# ── P4 — the daemon itself ─────────────────────────────────────────────────


def test_p4_pgid_self_refused(tmp_path, kill_fence, monkeypatch):
    """P4 — the daemon's own pid as a pgid ⇒ ``refused:pgid-self``."""
    root = build_tree(tmp_path / "proc")
    monkeypatch.setattr(ladder.os, "getpgid", lambda pid: SYNTHETIC_PGID + 7)
    status, reason = ram_kill_ladder(
        os.getpid(), LEADER_START, 0.25, proc_root=root, captured_pgid=os.getpid()
    )
    assert (status, reason) == ("refused", REASON_PGID_SELF)


# ── P5 — the leader-identity gate (three outcomes) ─────────────────────────


def test_p5_leader_alive_matching_starttime_kills(tmp_path, kill_census):
    """P5 — leader alive ∧ starttime matches ⇒ the kill proceeds."""
    root = build_tree(tmp_path / "proc")
    calls, _sleeps = kill_census
    status, _reason = ram_kill_ladder(
        SYNTHETIC_PGID, LEADER_START, 0.25, proc_root=root, captured_pgid=SYNTHETIC_PGID
    )
    assert status == "killed"
    assert calls, "the ladder must signal"


def test_p5_leader_alive_starttime_mismatch_refused(tmp_path, kill_fence):
    """P5 — leader alive ∧ starttime mismatch ⇒ ``refused:pgid-recycled``."""
    root = build_tree(tmp_path / "proc", starttime=LEADER_START + 99)
    status, reason = ram_kill_ladder(
        SYNTHETIC_PGID, LEADER_START, 0.25, proc_root=root, captured_pgid=SYNTHETIC_PGID
    )
    assert (status, reason) == ("refused", REASON_PGID_RECYCLED)


def test_p5_leader_absent_is_not_applicable(tmp_path, kill_census):
    """P5 — the leader is ABSENT ⇒ NOT APPLICABLE; the kill may proceed.

    This is the Q2 leaderless shape: the identity re-anchors on the
    surviving group members and the kill proceeds on group membership.
    """
    root = build_tree(tmp_path / "proc", leader=False, members=2)
    calls, _sleeps = kill_census
    status, reason = ram_kill_ladder(
        SYNTHETIC_PGID, LEADER_START, 0.25, proc_root=root, captured_pgid=SYNTHETIC_PGID
    )
    assert status == "killed"
    assert reason != REASON_LEADER_UNREADABLE
    assert calls, "the leaderless case must still signal the group"


def test_p5_leader_unreadable_refused(tmp_path, kill_fence):
    """P5 — an UNREADABLE leader stat ⇒ ``refused:leader-unreadable`` (fail-closed)."""
    root = build_tree(tmp_path / "proc")
    # A directory at the leader's stat path ⇒ reading it raises
    # IsADirectoryError (an OSError that is NOT FileNotFoundError).
    stat_path = root / str(SYNTHETIC_PGID) / "stat"
    stat_path.unlink()
    stat_path.mkdir()
    status, reason = ram_kill_ladder(
        SYNTHETIC_PGID, LEADER_START, 0.25, proc_root=root, captured_pgid=SYNTHETIC_PGID
    )
    assert (status, reason) == ("refused", REASON_LEADER_UNREADABLE)


# ── P5a — the pgid-recycle check ───────────────────────────────────────────


def test_p5a_pgid_recycled_refused(tmp_path, kill_fence):
    """P5a — a live process holding the captured pgid with a DIFFERENT
    starttime ⇒ ``refused:pgid-recycled`` (fail-closed)."""
    root = build_tree(tmp_path / "proc", starttime=LEADER_START + 99)
    status, reason = ram_kill_ladder(
        SYNTHETIC_PGID, LEADER_START, 0.25, proc_root=root, captured_pgid=SYNTHETIC_PGID
    )
    assert (status, reason) == ("refused", REASON_PGID_RECYCLED)


def test_p5a_absent_leader_is_not_recycled():
    """P5a — no live process holds the pid ⇒ NOT recycled."""
    stat = ladder.read_stat_entry.__wrapped__ if hasattr(ladder.read_stat_entry, "__wrapped__") else None
    from daemon.services.ram_guard.tree_reader import StatRead

    absent = StatRead(pid=1, status="absent")
    assert ladder.check_p5a_pgid_recycled(absent, LEADER_START) is None


# ── P6 — the ownership stamp ───────────────────────────────────────────────


def test_p6_ownership_mismatch_refused(tmp_path, kill_fence):
    """P6 — a readable stamp that does NOT match ⇒ ``refused:ownership-mismatch``."""
    root = build_tree(
        tmp_path / "proc", environ=b"OTHER=1\x00ENSEMBLE_BASH_TRACKING_ID=other-id\x00"
    )
    status, reason = ram_kill_ladder(
        SYNTHETIC_PGID,
        LEADER_START,
        0.25,
        proc_root=root,
        captured_pgid=SYNTHETIC_PGID,
        tracking_id="t-1",
    )
    assert (status, reason) == ("refused", REASON_OWNERSHIP_MISMATCH)


def test_p6_ownership_unreadable_proceeds_and_counts(tmp_path, kill_census):
    """P6 — an UNREADABLE stamp ⇒ PROCEED + ``ownership_unverified++``."""
    root = build_tree(tmp_path / "proc", environ=None)  # no environ file at all
    calls, _sleeps = kill_census
    unverified: list[int] = []
    status, _reason = ram_kill_ladder(
        SYNTHETIC_PGID,
        LEADER_START,
        0.25,
        proc_root=root,
        captured_pgid=SYNTHETIC_PGID,
        tracking_id="t-1",
        on_ownership_unverified=lambda: unverified.append(1),
    )
    assert status == "killed"
    assert unverified == [1]
    assert calls


def test_p6_ownership_absent_stamp_is_unverifiable(tmp_path, kill_census):
    """P6 — a readable environ that carries NO ownership stamp at all
    (the proc lane's case: it stamps ``ENSEMBLE_PROC_TRACKING_ID``, not
    the ladder's ``ENSEMBLE_BASH_TRACKING_ID``) ⇒ the stamp is
    UNVERIFIABLE ⇒ PROCEED + ``ownership_unverified++``.

    Contract (phase2-plan.md T11; test-plan.md S8(h); arch §4.2:226;
    dispatcher ruling 2026-11-11): P6 has TWO outcomes — a stamp that
    EXISTS and DIFFERS ⇒ refuse; a stamp that CANNOT BE VERIFIED (absent
    or unreadable) ⇒ PROCEED + ``ownership_unverified++``.  ABSENT ≠
    MISMATCH.  The proc lane has no stamp by design (DQ5), so its stamp
    is always unverifiable ⇒ P6 proceeds with ``ownership_unverified++``.
    """
    root = build_tree(tmp_path / "proc", environ=b"PATH=/bin\x00OTHER=1\x00")
    calls, _sleeps = kill_census
    unverified: list[int] = []
    status, reason = ram_kill_ladder(
        SYNTHETIC_PGID,
        LEADER_START,
        0.25,
        proc_root=root,
        captured_pgid=SYNTHETIC_PGID,
        tracking_id="t-1",
        on_ownership_unverified=lambda: unverified.append(1),
    )
    assert status == "killed", (status, reason)
    assert unverified == [1], "absent stamp must bump ownership_unverified"
    assert calls


def test_p6_ownership_match_proceeds(tmp_path, kill_census):
    """P6 — the exact stamp entry ⇒ the predicate holds."""
    root = build_tree(
        tmp_path / "proc",
        environ=b"PATH=/bin\x00ENSEMBLE_BASH_TRACKING_ID=t-1\x00",
    )
    calls, _sleeps = kill_census
    unverified: list[int] = []
    status, _reason = ram_kill_ladder(
        SYNTHETIC_PGID,
        LEADER_START,
        0.25,
        proc_root=root,
        captured_pgid=SYNTHETIC_PGID,
        tracking_id="t-1",
        on_ownership_unverified=lambda: unverified.append(1),
    )
    assert status == "killed"
    assert unverified == []
    assert calls


# ── P6 — the LANE-AWARE ownership stamp (leader ruling 2026-11-11) ─────────
#
# Three outcomes PER LANE, plus the cross-lane tripwire both ways:
#   * the lane's OWN stamp present AND matching ⇒ VERIFIED (a positive
#     signal — never counted as unverified);
#   * the stamp ABSENT (or unreadable) ⇒ PROCEED + ownership_unverified++;
#   * the OTHER lane's stamp present ⇒ refused:ownership-mismatch.
# ``lane=None`` (a direct caller without a lane) keeps the legacy
# bash-variable behaviour with no cross-lane tripwire — the pre-existing
# P6 tests above pin that.


def test_p6_lane_bash_own_stamp_matching_verifies(tmp_path, kill_census):
    root = build_tree(
        tmp_path / "proc",
        environ=b"OTHER=1\x00ENSEMBLE_BASH_TRACKING_ID=t-1\x00",
    )
    calls, _sleeps = kill_census
    unverified: list[int] = []
    status, reason = ram_kill_ladder(
        SYNTHETIC_PGID,
        LEADER_START,
        0.25,
        proc_root=root,
        captured_pgid=SYNTHETIC_PGID,
        tracking_id="t-1",
        lane="bash",
        on_ownership_unverified=lambda: unverified.append(1),
    )
    assert status == "killed", (status, reason)
    assert unverified == [], "a matching own stamp is VERIFIED — never counted"
    assert calls


def test_p6_lane_bash_stamp_absent_proceeds_and_counts(tmp_path, kill_census):
    root = build_tree(tmp_path / "proc", environ=b"PATH=/bin\x00OTHER=1\x00")
    calls, _sleeps = kill_census
    unverified: list[int] = []
    status, reason = ram_kill_ladder(
        SYNTHETIC_PGID,
        LEADER_START,
        0.25,
        proc_root=root,
        captured_pgid=SYNTHETIC_PGID,
        tracking_id="t-1",
        lane="bash",
        on_ownership_unverified=lambda: unverified.append(1),
    )
    assert status == "killed", (status, reason)
    assert unverified == [1], "an absent stamp must bump ownership_unverified"
    assert calls


def test_p6_lane_bash_foreign_proc_stamp_refuses(tmp_path, kill_fence):
    """The CROSS-LANE tripwire, bash side: a proc stamp on a bash spawn."""
    root = build_tree(
        tmp_path / "proc",
        environ=b"ENSEMBLE_PROC_TRACKING_ID=t-1\x00",
    )
    status, reason = ram_kill_ladder(
        SYNTHETIC_PGID,
        LEADER_START,
        0.25,
        proc_root=root,
        captured_pgid=SYNTHETIC_PGID,
        tracking_id="t-1",
        lane="bash",
    )
    assert (status, reason) == ("refused", REASON_OWNERSHIP_MISMATCH)


def test_p6_lane_proc_own_stamp_matching_verifies(tmp_path, kill_census):
    root = build_tree(
        tmp_path / "proc",
        environ=b"OTHER=1\x00ENSEMBLE_PROC_TRACKING_ID=t-1\x00",
    )
    calls, _sleeps = kill_census
    unverified: list[int] = []
    status, reason = ram_kill_ladder(
        SYNTHETIC_PGID,
        LEADER_START,
        0.25,
        proc_root=root,
        captured_pgid=SYNTHETIC_PGID,
        tracking_id="t-1",
        lane="proc",
        on_ownership_unverified=lambda: unverified.append(1),
    )
    assert status == "killed", (status, reason)
    assert unverified == [], "a matching own stamp is VERIFIED — never counted"
    assert calls


def test_p6_lane_proc_stamp_absent_proceeds_and_counts(tmp_path, kill_census):
    """The proc lane's own stamp ABSENT ⇒ unverifiable ⇒ PROCEED."""
    root = build_tree(tmp_path / "proc", environ=b"PATH=/bin\x00OTHER=1\x00")
    calls, _sleeps = kill_census
    unverified: list[int] = []
    status, reason = ram_kill_ladder(
        SYNTHETIC_PGID,
        LEADER_START,
        0.25,
        proc_root=root,
        captured_pgid=SYNTHETIC_PGID,
        tracking_id="t-1",
        lane="proc",
        on_ownership_unverified=lambda: unverified.append(1),
    )
    assert status == "killed", (status, reason)
    assert unverified == [1], "an absent stamp must bump ownership_unverified"
    assert calls


def test_p6_lane_proc_own_stamp_mismatch_refuses(tmp_path, kill_fence):
    """The proc lane's own stamp present with a DIFFERENT value ⇒ refuse."""
    root = build_tree(
        tmp_path / "proc",
        environ=b"ENSEMBLE_PROC_TRACKING_ID=other-id\x00",
    )
    status, reason = ram_kill_ladder(
        SYNTHETIC_PGID,
        LEADER_START,
        0.25,
        proc_root=root,
        captured_pgid=SYNTHETIC_PGID,
        tracking_id="t-1",
        lane="proc",
    )
    assert (status, reason) == ("refused", REASON_OWNERSHIP_MISMATCH)


def test_p6_lane_proc_foreign_bash_stamp_refuses(tmp_path, kill_fence):
    """The CROSS-LANE tripwire, proc side: a bash stamp on a proc spawn."""
    root = build_tree(
        tmp_path / "proc",
        environ=b"ENSEMBLE_BASH_TRACKING_ID=t-1\x00",
    )
    status, reason = ram_kill_ladder(
        SYNTHETIC_PGID,
        LEADER_START,
        0.25,
        proc_root=root,
        captured_pgid=SYNTHETIC_PGID,
        tracking_id="t-1",
        lane="proc",
    )
    assert (status, reason) == ("refused", REASON_OWNERSHIP_MISMATCH)


def test_p6_lane_foreign_stamp_outranks_own_match(tmp_path, kill_fence):
    """Precedence: the foreign stamp REFUSES even when the lane's own
    stamp is present and matching (cross-presence does not occur
    legitimately — defense-in-depth for a process-killing subsystem).
    """
    root = build_tree(
        tmp_path / "proc",
        environ=(
            b"ENSEMBLE_PROC_TRACKING_ID=t-1\x00"
            b"ENSEMBLE_BASH_TRACKING_ID=t-1\x00"
        ),
    )
    status, reason = ram_kill_ladder(
        SYNTHETIC_PGID,
        LEADER_START,
        0.25,
        proc_root=root,
        captured_pgid=SYNTHETIC_PGID,
        tracking_id="t-1",
        lane="proc",
    )
    assert (status, reason) == ("refused", REASON_OWNERSHIP_MISMATCH)


# ── P7 — the group is still alive ──────────────────────────────────────────


def test_p7_group_empty_already_dead(tmp_path, kill_fence):
    """P7 — ``group_member_count == 0`` ⇒ ``already-dead:group-empty``.

    Release, NO signal — and ONLY at member count 0 (it is NOT produced
    merely because the leader is gone).
    """
    root = write_proc_tree(tmp_path / "proc", [])
    status, reason = ram_kill_ladder(
        SYNTHETIC_PGID, LEADER_START, 0.25, proc_root=root, captured_pgid=SYNTHETIC_PGID
    )
    assert (status, reason) == ("already-dead", REASON_GROUP_EMPTY)


def test_p7_group_unknown_refused(tmp_path, kill_fence):
    """P7 — an unevaluable group ⇒ ``refused:group-unknown`` (fail-closed)."""
    root = tmp_path / "no-such-proc"  # the enumeration FAILS
    status, reason = ram_kill_ladder(
        SYNTHETIC_PGID, LEADER_START, 0.25, proc_root=root, captured_pgid=SYNTHETIC_PGID
    )
    assert (status, reason) == ("refused", REASON_GROUP_UNKNOWN)


# ── P8 — the post-kill probe ───────────────────────────────────────────────


def test_p8_complete_tree_keeps_the_killed_code(tmp_path, kill_census, monkeypatch):
    """P8 — ``tree_complete`` true ⇒ the existing ``killed:*`` code stands."""
    root = build_tree(tmp_path / "proc")
    calls, _sleeps = kill_census

    def _killpg(pgid, sig):  # noqa: ANN001
        calls.append((pgid, sig))
        if sig == 9:  # SIGKILL — the tree dies at the recorded KILL
            kill_tree(root, [SYNTHETIC_PGID, SYNTHETIC_PGID + 100])

    monkeypatch.setattr(ladder.os, "killpg", _killpg)
    status, reason = ram_kill_ladder(
        SYNTHETIC_PGID, LEADER_START, 0.25, proc_root=root, captured_pgid=SYNTHETIC_PGID
    )
    assert status == "killed"
    assert reason == REASON_LADDER


def test_p8_incomplete_tree_is_tree_incomplete(tmp_path, kill_census):
    """P8 — an incomplete/unknown post-kill state ⇒ ``killed:tree-incomplete``."""
    root = build_tree(tmp_path / "proc")
    kill_census  # the tree stays alive ⇒ the probe cannot verify the death
    status, reason = ram_kill_ladder(
        SYNTHETIC_PGID, LEADER_START, 0.25, proc_root=root, captured_pgid=SYNTHETIC_PGID
    )
    assert status == "killed"
    assert reason == REASON_TREE_INCOMPLETE


def test_p8_term_esrch_skips_the_grace_and_the_kill(tmp_path, kill_census, monkeypatch):
    """A TERM that lands ESRCH ⇒ ``killed:term-esrch``; no grace, no KILL."""
    root = build_tree(tmp_path / "proc")
    calls, sleeps = kill_census

    def _killpg(pgid, sig):  # noqa: ANN001
        calls.append((pgid, sig))
        if sig == 15:  # SIGTERM — the group is already gone
            raise ProcessLookupError(pgid)

    monkeypatch.setattr(ladder.os, "killpg", _killpg)
    status, reason = ram_kill_ladder(
        SYNTHETIC_PGID, LEADER_START, 0.25, proc_root=root, captured_pgid=SYNTHETIC_PGID
    )
    assert status == "killed"
    assert reason == REASON_TERM_ESRCH
    assert sleeps == []


def test_p8_group_dead_after_grace_skips_the_kill(tmp_path, kill_census, monkeypatch):
    """The group dies during the grace ⇒ ``killed:group-dead-after-grace``."""
    root = build_tree(tmp_path / "proc")
    calls, sleeps = kill_census

    def _killpg(pgid, sig):  # noqa: ANN001
        calls.append((pgid, sig))
        if sig == 15:  # SIGTERM — the group dies at the TERM
            kill_tree(root, [SYNTHETIC_PGID, SYNTHETIC_PGID + 100])

    monkeypatch.setattr(ladder.os, "killpg", _killpg)
    status, reason = ram_kill_ladder(
        SYNTHETIC_PGID, LEADER_START, 0.25, proc_root=root, captured_pgid=SYNTHETIC_PGID
    )
    assert status == "killed"
    assert reason == REASON_GROUP_DEAD_AFTER_GRACE
    # The grace was sliced at the guard's OWN constant.
    assert sleeps, "the grace must be sliced, not slept in one block"
    assert all(s <= ladder.RAM_GUARD_GRACE_POLL_SLICE_SECONDS for s in sleeps)


# ── S18 — the vocabulary + idempotency ─────────────────────────────────────


def test_s18_reason_set_is_the_exact_ordered_set():
    """§15 B4 — the reason set is EXACTLY these 16 codes, in this order."""
    assert REASON_SET == (
        "killed:term-esrch",
        "killed:group-dead-after-grace",
        "killed:ladder",
        "killed:tree-incomplete",
        "already-dead:group-empty",
        "refused:guard-disabled",
        "refused:not-registered",
        "refused:platform-unsupported",
        "refused:pgid-not-registered",
        "refused:pgid-invalid",
        "refused:pgid-self",
        "refused:daemon-group",
        "refused:pgid-recycled",
        "refused:leader-unreadable",
        "refused:ownership-mismatch",
        "refused:group-unknown",
    )
    assert len(REASON_SET) == 16
    assert len(set(REASON_SET)) == 16


def test_s18_status_set_is_the_exact_ordered_set():
    """§15 B4 — the status set."""
    assert STATUS_SET == ("killed", "already-dead", "refused")


def test_s18_ladder_is_idempotent(tmp_path, kill_census):
    """A repeat call on a dead group is a NO-OP (no second signal)."""
    root = build_tree(tmp_path / "proc")
    calls, _sleeps = kill_census
    first = ram_kill_ladder(
        SYNTHETIC_PGID, LEADER_START, 0.25, proc_root=root, captured_pgid=SYNTHETIC_PGID
    )
    assert first[0] == "killed"
    signals_after_first = len(calls)
    # The group is now empty ⇒ P7 ⇒ already-dead, no signal.
    kill_tree(root, [SYNTHETIC_PGID, SYNTHETIC_PGID + 100])
    second = ram_kill_ladder(
        SYNTHETIC_PGID, LEADER_START, 0.25, proc_root=root, captured_pgid=SYNTHETIC_PGID
    )
    assert second == ("already-dead", REASON_GROUP_EMPTY)
    assert len(calls) == signals_after_first


def test_s18_refusal_classification_is_the_pinned_split():
    """§15 B27 — the transient/permanent split."""
    assert ladder.TRANSIENT_REFUSALS == (
        "refused:leader-unreadable",
        "refused:group-unknown",
    )
    assert ladder.PERMANENT_REFUSALS == (
        "refused:guard-disabled",
        "refused:not-registered",
        "refused:platform-unsupported",
        "refused:pgid-not-registered",
        "refused:pgid-invalid",
        "refused:pgid-self",
        "refused:daemon-group",
        "refused:pgid-recycled",
        "refused:ownership-mismatch",
    )
    assert len(ladder.TRANSIENT_REFUSALS) + len(ladder.PERMANENT_REFUSALS) == 11
    # Every refusal has a counter bucket, and there is NO p8.
    for reason in ladder.TRANSIENT_REFUSALS + ladder.PERMANENT_REFUSALS:
        assert ladder.refusal_bucket(reason) in {"p0", "p1", "p2", "p3", "p4", "p5", "p6", "p7"}
    assert "p8" not in ladder.REFUSAL_PREDICATE_BUCKET.values()


# ── S16 — the thread-home viability ───────────────────────────────────────


def test_s16_ladder_runs_from_a_bare_thread(tmp_path, kill_census):
    """S16 — the kill path completes from a bare thread (no event loop)."""
    root = build_tree(tmp_path / "proc")
    result: list[tuple[str, str]] = []

    def _run():
        result.append(
            ram_kill_ladder(
                SYNTHETIC_PGID, LEADER_START, 0.25, proc_root=root, captured_pgid=SYNTHETIC_PGID
            )
        )

    thread = threading.Thread(target=_run)
    thread.start()
    thread.join(timeout=10)
    assert not thread.is_alive()
    assert result and result[0][0] == "killed"
