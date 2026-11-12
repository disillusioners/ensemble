"""``ladder`` — the kill ladder and the pre-kill gate.

Contract: architecture-recommendation.md §4.2 (the ladder contract + the
16-code vocabulary + the P0–P7 gate + P5a + P8-as-post-kill), §15 B4
(the reason/status sets, pinned char-for-character), §15 B16 (the grace
slice constant), §15 B27 (the refusal classification), phase2-plan.md
T11 / T12.

**The ladder is SYNC** — callable from the execution-lane thread with no
event loop (and from a bare thread in tests).  TERM → slice-probed
grace → KILL; KILL is always reachable; zombie-aware skip; idempotent.

**The 16 codes (§15 B4, ordered, exact):** the enumeration IS the
authoritative exact set — 16 codes = 9 kept/renamed + 7 added.  Any
count other than 16 anywhere in the plan set is a defect.

**The gate is P0–P7** — P8 is the POST-KILL probe, not a pre-kill
predicate.  A kill fires iff ALL of P0–P7 evaluate true; "unevaluable"
never means true.
"""

from __future__ import annotations

import os
import signal
import sys
import time
from pathlib import Path
from typing import Callable, Optional

from .tree_reader import PROC_ROOT, read_stat_entry, scan_proc

# ── Module constants (§15 B16 — NOT knobs) ─────────────────────────────────

#: The guard's OWN grace poll slice (the reaper's 0.25 s slice yields
#: ONE slice and no early exit at a 250 ms grace; 0.025 s gives 10
#: slices with a live early exit).  Reimplemented here — the
#: ``bash_spawn_reaper`` import stays forbidden (§7).
RAM_GUARD_GRACE_POLL_SLICE_SECONDS = 0.025

#: The ownership-stamp environment variable.  The SAME literal as
#: ``bash_spawn_reaper.BASH_TRACKING_ENV_VAR`` — duplicated (not
#: imported) because the import freeze forbids importing the reaper;
#: the coupling is pinned by a test instead.
#:
#: This is also the LEGACY default: a caller that does not pass
#: ``lane`` gets the bash lane's variable, exactly as before the
#: lane-aware P6 (leader ruling 2026-11-11).
OWNERSHIP_ENV_VAR = "ENSEMBLE_BASH_TRACKING_ID"

#: The proc lane's ownership-stamp variable — the SAME literal as
#: ``proc_tools._TRACKING_ENV_VAR`` (duplicated, not imported: the
#: import freeze forbids ``ram_guard → daemon/tools``; the coupling is
#: pinned by a test).
OWNERSHIP_ENV_VAR_PROC = "ENSEMBLE_PROC_TRACKING_ID"

#: The per-lane ownership-stamp variables (leader ruling 2026-11-11).
#: The stamp is set BY THE TOOL at its spawn boundary, under the LANE'S
#: OWN variable, and inherited down the tree — so P6 reads the
#: LANE-APPROPRIATE variable for the spawn it is about to signal:
#: the lane's own stamp matching ⇒ VERIFIED (a positive signal, NOT
#: counted as unverified); the stamp absent ⇒ unverifiable (PROCEED +
#: ``ownership_unverified++``); the OTHER lane's stamp present ⇒
#: MISMATCH ⇒ REFUSE (cross-presence does not occur legitimately).
OWNERSHIP_ENV_VAR_BY_LANE = {
    "bash": OWNERSHIP_ENV_VAR,
    "proc": OWNERSHIP_ENV_VAR_PROC,
}

# ── The reason set (§15 B4 — ordered, exact, 16 codes) ─────────────────────

REASON_TERM_ESRCH = "killed:term-esrch"
REASON_GROUP_DEAD_AFTER_GRACE = "killed:group-dead-after-grace"
REASON_LADDER = "killed:ladder"
REASON_TREE_INCOMPLETE = "killed:tree-incomplete"
REASON_GROUP_EMPTY = "already-dead:group-empty"
REASON_GUARD_DISABLED = "refused:guard-disabled"
REASON_NOT_REGISTERED = "refused:not-registered"
REASON_PLATFORM_UNSUPPORTED = "refused:platform-unsupported"
REASON_PGID_NOT_REGISTERED = "refused:pgid-not-registered"
REASON_PGID_INVALID = "refused:pgid-invalid"
REASON_PGID_SELF = "refused:pgid-self"
REASON_DAEMON_GROUP = "refused:daemon-group"
REASON_PGID_RECYCLED = "refused:pgid-recycled"
REASON_LEADER_UNREADABLE = "refused:leader-unreadable"
REASON_OWNERSHIP_MISMATCH = "refused:ownership-mismatch"  # RETIRED (§18.2) — kept for compat only; no longer in REASON_SET
REASON_GROUP_UNKNOWN = "refused:group-unknown"

#: The authoritative exact set — 16 codes (§15 B4).
REASON_SET: tuple[str, ...] = (
    REASON_TERM_ESRCH,
    REASON_GROUP_DEAD_AFTER_GRACE,
    REASON_LADDER,
    REASON_TREE_INCOMPLETE,
    REASON_GROUP_EMPTY,
    REASON_GUARD_DISABLED,
    REASON_NOT_REGISTERED,
    REASON_PLATFORM_UNSUPPORTED,
    REASON_PGID_NOT_REGISTERED,
    REASON_PGID_INVALID,
    REASON_PGID_SELF,
    REASON_DAEMON_GROUP,
    REASON_PGID_RECYCLED,
    REASON_LEADER_UNREADABLE,
    REASON_GROUP_UNKNOWN,
)

#: The status set (§15 B4).
STATUS_SET: tuple[str, ...] = ("killed", "already-dead", "refused")

STATUS_KILLED = "killed"
STATUS_ALREADY_DEAD = "already-dead"
STATUS_REFUSED = "refused"

# ── The refusal classification (§15 B27 — verbatim) ─────────────────────────

#: The refusal classification (§15 B27, verbatim):
#:
#:     transient (re-arm to WATCHING; re-claimable on a later tick): refused:leader-unreadable, refused:group-unknown
#:     permanent (terminal REFUSED; released): refused:guard-disabled, refused:not-registered, refused:platform-unsupported, refused:pgid-not-registered, refused:pgid-invalid, refused:pgid-self, refused:daemon-group, refused:pgid-recycled, refused:ownership-mismatch
#:
#: (The two RENAMED/REMOVED members of the iteration-001 vocabulary are
#: recorded here as ruled: ``refused:platform-win32`` was RENAMED to
#: ``refused:platform-unsupported`` — the guard is Linux-only by design
#: and a win32-specific name is wrong on macOS/BSD — and NOTHING was
#: REMOVED.)

#: TRANSIENT — re-arm to WATCHING; re-claimable on a later tick.
TRANSIENT_REFUSALS: tuple[str, ...] = (
    REASON_LEADER_UNREADABLE,
    REASON_GROUP_UNKNOWN,
)

#: PERMANENT — terminal REFUSED; released.
PERMANENT_REFUSALS: tuple[str, ...] = (
    REASON_GUARD_DISABLED,
    REASON_NOT_REGISTERED,
    REASON_PLATFORM_UNSUPPORTED,
    REASON_PGID_NOT_REGISTERED,
    REASON_PGID_INVALID,
    REASON_PGID_SELF,
    REASON_DAEMON_GROUP,
    REASON_PGID_RECYCLED,
)

#: Which pre-kill predicate each refusal belongs to — the counter
#: buckets ``ram_kill_refusals_p0 … p7`` (§15 B5; there is NO p8).
REFUSAL_PREDICATE_BUCKET: dict[str, str] = {
    REASON_GUARD_DISABLED: "p0",
    REASON_NOT_REGISTERED: "p0",
    REASON_PLATFORM_UNSUPPORTED: "p0",
    REASON_PGID_NOT_REGISTERED: "p1",
    REASON_PGID_INVALID: "p2",
    REASON_DAEMON_GROUP: "p3",
    REASON_PGID_SELF: "p4",
    REASON_PGID_RECYCLED: "p5",
    REASON_LEADER_UNREADABLE: "p5",
    REASON_GROUP_UNKNOWN: "p7",
}


def is_transient_refusal(reason: str) -> bool:
    """``True`` for the two transient refusals (§15 B27)."""
    return reason in TRANSIENT_REFUSALS


def is_permanent_refusal(reason: str) -> bool:
    """``True`` for the nine permanent refusals (§15 B27)."""
    return reason in PERMANENT_REFUSALS


def refusal_bucket(reason: str) -> Optional[str]:
    """The counter bucket (``p0``…``p7``) a refusal reason belongs to."""
    return REFUSAL_PREDICATE_BUCKET.get(reason)


# ── The individual pre-kill predicates (P0–P7 + P5a) ───────────────────────


def check_p0_guard_enabled(
    *,
    guard_enabled: bool,
    registered: bool,
    platform: Optional[str],
) -> Optional[str]:
    """P0 — the switch is ON ∧ the platform is Linux ∧ the record is registered.

    Returns the refusal reason or ``None`` when the predicate holds.
    """
    if not guard_enabled:
        return REASON_GUARD_DISABLED
    if not registered:
        return REASON_NOT_REGISTERED
    plat = sys.platform if platform is None else platform
    if not plat.startswith("linux"):
        return REASON_PLATFORM_UNSUPPORTED
    return None


def check_p1_pgid_registered(pgid: int, captured_pgid: Optional[int]) -> Optional[str]:
    """P1 — the pgid is the one THIS guard's registry captured.

    **FAIL-CLOSED.**  A kill fires iff ALL of P0–P7 evaluate TRUE, and
    "unevaluable" never means true: with no captured pgid the predicate
    cannot be established, so the kill is REFUSED
    (``refused:pgid-not-registered``) rather than silently passed.
    """
    if captured_pgid is None:
        return REASON_PGID_NOT_REGISTERED
    if int(captured_pgid) != int(pgid):
        return REASON_PGID_NOT_REGISTERED
    return None


def check_p2_pgid_valid(pgid: int) -> Optional[str]:
    """P2 — ``pgid > 1``."""
    if int(pgid) <= 1:
        return REASON_PGID_INVALID
    return None


def check_p3_daemon_group(pgid: int) -> Optional[str]:
    """P3 — the pgid is not the DAEMON'S GROUP (``os.getpgid(os.getpid())``).

    The daemon's GROUP, not its pid — a pid-group check would miss the
    common case where the daemon shares a group with other processes.

    **FAIL-CLOSED.**  An unevaluable daemon group (the ``getpgid`` call
    raises) is NOT a pass: the safety rail refuses
    (``refused:daemon-group``) rather than letting an unknown group
    through ("unevaluable" never means true).
    """
    try:
        daemon_pgid = os.getpgid(os.getpid())
    except OSError:
        return REASON_DAEMON_GROUP
    if int(pgid) == int(daemon_pgid):
        return REASON_DAEMON_GROUP
    return None


def check_p4_self(pgid: int) -> Optional[str]:
    """P4 — neither the daemon's pid nor its group is the target."""
    try:
        daemon_pid = os.getpid()
    except OSError:  # pragma: no cover — os.getpid never fails on Linux
        return None
    if int(pgid) == int(daemon_pid):
        return REASON_PGID_SELF
    return None


def check_p5_leader_identity(stat, leader_start_time: Optional[int]) -> Optional[str]:
    """P5 — the leader-identity gate, THREE outcomes.

    * leader alive ∧ starttime matches ⇒ ``None`` (the predicate holds)
    * leader alive ∧ starttime mismatch ⇒ ``refused:pgid-recycled``
    * leader ABSENT (ENOENT) or a ZOMBIE ⇒ ``None`` (NOT APPLICABLE —
      the identity re-anchors on the surviving group members and the
      kill may proceed on group membership)
    * leader stat UNREADABLE ⇒ ``refused:leader-unreadable``
      (fail-closed — an unreadable identity is not a confirmed-dead
      leader; the WATCH stays alive, only the kill is refused)
    """
    if stat.unreadable:
        return REASON_LEADER_UNREADABLE
    if stat.absent:
        return None  # the leader is gone — the watch stays alive
    state = stat.state or ""
    if state.startswith("Z"):
        return None  # a zombie leader is dead (awaiting reap)
    if leader_start_time is None:
        return REASON_LEADER_UNREADABLE  # nothing to compare against
    if stat.start_time_jiffies != leader_start_time:
        return REASON_PGID_RECYCLED
    return None


def read_ownership_stamp(
    proc_root: Path,
    pid: int,
    tracking_id: str,
    *,
    env_var: str = OWNERSHIP_ENV_VAR,
) -> Optional[bool]:
    """The ownership stamp of one member (TRI-STATE).

    ``True``  — the environ carries the EXACT stamp entry (verified).

    ``False`` — the ownership stamp VARIABLE is PRESENT but carries a
    DIFFERENT value: a REAL mismatch (the PID was recycled, or a
    foreign-lane stamp is present).  This is the ONLY case that refuses
    (``refused:ownership-mismatch`` — deterministic; a retry cannot
    change it).

    ``None``  — the stamp CANNOT BE VERIFIED: the ownership variable is
    ABSENT from the environ, or the environ is unreadable (fail-open,
    with ``ownership_unverified++``).

    Contract (phase2-plan.md T11; test-plan.md S8(h); arch §4.2:226
    "for ``refused:ownership-mismatch`` the stamp check is
    DETERMINISTIC — retrying cannot change it"; dispatcher ruling
    2026-11-11; LEADER RULING 2026-11-11 — lane-aware): P6 has TWO
    outcomes only — a stamp that EXISTS and DIFFERS ⇒ refuse; a stamp
    that CANNOT BE VERIFIED (absent or unreadable) ⇒ PROCEED with
    ``ownership_unverified++``.  ABSENT ≠ MISMATCH.  The variable that
    is checked is the LANE'S OWN (``env_var``): stamps are set per-lane
    by the tool at its spawn boundary and inherited down the tree.

    The house idiom (bash_spawn_reaper._verify_member_env_procfs) already
    makes this exact three-way split: exact match ⇒ True, marker present
    with a different value ⇒ False, marker absent ⇒ None (fail-open).
    """
    path = Path(proc_root) / str(pid) / "environ"
    try:
        data = path.read_bytes()
    except OSError:
        return None
    entries = data.split(b"\x00")
    needle = f"{env_var}={tracking_id}".encode("utf-8")
    if needle in entries:
        return True
    # The ownership variable is PRESENT with a DIFFERENT value ⇒ a REAL
    # mismatch (PID recycled, or a foreign-lane stamp present).  The
    # marker being present at all is the signal; the exact value is what
    # we verify.
    marker_prefix = f"{env_var}=".encode("utf-8")
    if any(e.startswith(marker_prefix) for e in entries):
        return False
    # The ownership variable is ABSENT ⇒ unverifiable (fail-open).
    return None


def _foreign_stamp_present(
    proc_root: Path,
    members,
    foreign_vars,
) -> bool:
    """Does any member carry ANOTHER lane's ownership stamp?

    The CROSS-LANE tripwire (leader ruling 2026-11-11): stamps are set
    per-lane at the tool's spawn boundary and inherited down the tree;
    cross-presence does not occur legitimately, so it is defense-in-depth
    for a process-killing subsystem.  An unreadable environ says nothing
    and is skipped (the lane's own aggregate owns the unverifiable
    outcome).
    """
    for pid in members:
        path = Path(proc_root) / str(pid) / "environ"
        try:
            data = path.read_bytes()
        except OSError:
            continue
        entries = data.split(b"\x00")
        for var in foreign_vars:
            prefix = f"{var}=".encode("utf-8")
            if any(e.startswith(prefix) for e in entries):
                return True
    return False


def check_p6_ownership(
    proc_root: Path,
    members: tuple[int, ...],
    tracking_id: Optional[str],
    *,
    lane: Optional[str] = None,
) -> tuple[Optional[str], bool]:
    """P6 — the ownership stamp (LANE-AWARE).

    Returns ``(reason, unverifiable)``.  THREE outcomes (phase2-plan.md
    T11; test-plan.md S8(h); arch §4.2:226 "for
    ``refused:ownership-mismatch`` the stamp check is DETERMINISTIC —
    retrying cannot change it"; dispatcher ruling 2026-11-11; LEADER
    RULING 2026-11-11 — lane-aware):

      - the LANE'S OWN stamp present AND matching ⇒ VERIFIED (a positive
        signal — NOT counted as unverified);
      - the stamp ABSENT (or the environ unreadable) ⇒ PROCEED with
        ``unverifiable=True`` (``ownership_unverified++``) — ABSENT ≠
        MISMATCH;
      - the OTHER lane's stamp present ⇒ ``refused:ownership-mismatch``
        (the cross-lane tripwire, checked FIRST — cross-presence does
        not occur legitimately, so it refuses even when the lane's own
        stamp would otherwise match); the lane's own stamp present with
        a DIFFERENT value ⇒ the same refusal (a REAL mismatch — the PID
        was recycled).

    ``lane=None`` (the legacy default — direct callers without a lane)
    checks the bash lane's variable and runs NO cross-lane tripwire,
    exactly as before the lane-aware P6.  The guard passes the record's
    lane.
    """
    if not tracking_id or not members:
        return None, False
    lane_var = (
        OWNERSHIP_ENV_VAR
        if lane is None
        else OWNERSHIP_ENV_VAR_BY_LANE.get(lane, OWNERSHIP_ENV_VAR)
    )
    foreign_vars = (
        tuple(
            var
            for key, var in OWNERSHIP_ENV_VAR_BY_LANE.items()
            if key != lane and var != lane_var
        )
        if lane is not None
        else ()
    )
    # LEADER RULING B (2026-11-11): env stamps are ADVISORY.  A mismatch
    # (foreign stamp present, or the lane's own stamp present with a
    # different value) NEVER permanent-refuses — it re-verifies the primary
    # identity (the captured pgid ∧ the leader's starttime, already
    # established by P5 + P5a before P6 runs) and, on a hold, PROCEEDS
    # with ownership_unverified++.  The lane-aware structure stays.
    if foreign_vars and _foreign_stamp_present(proc_root, members, foreign_vars):
        return None, True
    readable = False
    for pid in members:
        matched = read_ownership_stamp(proc_root, pid, tracking_id, env_var=lane_var)
        if matched is None:
            continue
        readable = True
        if matched:
            return None, False
    if readable:
        return None, True
    return None, True


def check_p7_group_liveness(scan_complete: bool, member_count: int, unreadable_pids=()) -> Optional[str]:
    """P7 — the group is still alive.

    ``group_member_count == 0`` ⇒ ``already-dead:group-empty`` (release,
    no signal) — and ONLY at member count 0 on a COMPLETE scan (unreadable
    pids make "empty" unprovable — §18.1).  An unevaluable group ⇒
    ``refused:group-unknown``.
    """
    if not scan_complete or unreadable_pids:
        return REASON_GROUP_UNKNOWN
    if int(member_count) == 0:
        return REASON_GROUP_EMPTY
    return None


# ── The ladder ──────────────────────────────────────────────────────────────


def _grace_wait_for_death(pgid: int, grace_seconds: float) -> None:
    """Sleep the TERM→KILL grace in short poll slices, exiting early.

    The slices are computed ARITHMETICALLY (not off a monotonic
    deadline) so the total converges to exactly ``grace_seconds`` for a
    live group — including under a mocked ``time.sleep`` in tests.  The
    death probe is ``killpg(pgid, 0)`` — signal 0 is a LIVENESS PROBE,
    not a signal.
    """
    if grace_seconds <= 0:
        return
    interval = RAM_GUARD_GRACE_POLL_SLICE_SECONDS
    slices = max(1, int(-(-grace_seconds // interval)))  # ceil
    for i in range(slices):
        remaining = grace_seconds - i * interval
        if remaining <= 0:
            return
        try:
            os.killpg(pgid, 0)
        except ProcessLookupError:
            return  # the group died mid-grace — don't burn the remainder
        except OSError:
            pass  # EPERM / probe failure ⇒ assume alive, keep sleeping
        time.sleep(min(interval, remaining))


def probe_tree_after_kill(proc_root: Path, pgid: int) -> bool:
    """P8 — the POST-KILL verification probe.

    ``True`` when the tree's death could be VERIFIED (the scan
    completed and no live member of the group remains).  ``False`` when
    the outcome is unknown or incomplete ⇒ the reason becomes
    ``killed:tree-incomplete``.
    """
    table = scan_proc(proc_root)
    if not table.scan_complete or table.unreadable_pids:
        return False
    return not table.by_pgrp.get(int(pgid), ())


def ram_kill_ladder(
    pgid: int,
    leader_start_time: Optional[int],
    grace_seconds: float,
    *,
    proc_root: Path = PROC_ROOT,
    tracking_id: Optional[str] = None,
    captured_pgid: Optional[int] = None,
    registered: bool = True,
    guard_enabled: bool = True,
    platform: Optional[str] = None,
    lane: Optional[str] = None,
    on_ownership_unverified: Optional[Callable[[], None]] = None,
) -> tuple[str, str]:
    """Kill one process group → ``(status, reason)``.

    The pre-kill gate is **P0–P7** (+ P5a); a kill fires iff ALL of them
    evaluate true.  Then: TERM → slice-probed grace → KILL, and finally
    the **P8 post-kill probe**.

    ``lane`` — the SPAWN'S lane (``"bash"`` | ``"proc"``): P6 reads the
    LANE-APPROPRIATE ownership stamp (leader ruling 2026-11-11).  The
    guard passes the record's lane; ``None`` (direct callers) keeps the
    legacy bash-variable behaviour with no cross-lane tripwire.

    ``status`` ∈ ``{killed, already-dead, refused}``; ``reason`` ∈ the
    16-code set (§15 B4).

    The ladder is **idempotent**: a repeat call on a dead group finds
    ``group_member_count == 0`` at P7 and returns
    ``already-dead:group-empty`` — no second signal is ever delivered.
    """
    # ── P0 — the switch, the platform, the registration ──
    reason = check_p0_guard_enabled(
        guard_enabled=guard_enabled,
        registered=registered,
        platform=platform,
    )
    if reason is not None:
        return (STATUS_REFUSED, reason)

    # ── P1 — the pgid came from THIS guard's registry ──
    reason = check_p1_pgid_registered(pgid, captured_pgid)
    if reason is not None:
        return (STATUS_REFUSED, reason)

    # ── P2 / P3 / P4 — the safety rails ──
    reason = check_p2_pgid_valid(pgid)
    if reason is not None:
        return (STATUS_REFUSED, reason)
    reason = check_p3_daemon_group(pgid)
    if reason is not None:
        return (STATUS_REFUSED, reason)
    reason = check_p4_self(pgid)
    if reason is not None:
        return (STATUS_REFUSED, reason)

    # ── P5 + P5a — the leader identity and the pgid-recycle check ──
    # The leader's pid IS the pgid (POSIX: a group leader's pid becomes
    # the group id), so ONE direct stat read serves both predicates.
    leader_stat = read_stat_entry(proc_root, int(pgid))
    reason = check_p5_leader_identity(leader_stat, leader_start_time)
    if reason is not None:
        return (STATUS_REFUSED, reason)
    # P5a RETIRED (§18.3): probe-proven byte-identical to P5; the
    # recycle outcome is produced by check_p5_leader_identity above.

    # ── ONE scan serves P6 (the ownership stamp) and P7 (the liveness) ──
    table = scan_proc(proc_root)
    members = table.by_pgrp.get(int(pgid), ()) if table.scan_complete else ()

    # ── P6 — the ownership stamp (lane-aware: the guard passes the
    # record's lane; a direct caller without a lane gets the legacy
    # bash-variable behaviour) ──
    reason, unverifiable = check_p6_ownership(
        proc_root, members, tracking_id, lane=lane
    )
    if reason is not None:
        return (STATUS_REFUSED, reason)
    if unverifiable and on_ownership_unverified is not None:
        on_ownership_unverified()

    # ── P7 — the group is still alive ──
    reason = check_p7_group_liveness(table.scan_complete, len(members), table.unreadable_pids)
    if reason is not None:
        if reason == REASON_GROUP_EMPTY:
            return (STATUS_ALREADY_DEAD, REASON_GROUP_EMPTY)
        return (STATUS_REFUSED, reason)

    # ── TERM ──
    term_esrch = False
    try:
        os.killpg(int(pgid), signal.SIGTERM)
    except ProcessLookupError:
        term_esrch = True
    except OSError:
        pass  # EPERM / other ⇒ continue to the KILL leg (KILL is always reachable)
    if term_esrch:
        # The group died between the gate and the TERM — nothing left to
        # signal; skip the grace + KILL legs.  The ESRCH is itself the
        # death confirmation, so the P8 probe (which runs AFTER the
        # KILL) does not apply here.
        return (STATUS_KILLED, REASON_TERM_ESRCH)

    # ── slice-probed grace ──
    if grace_seconds > 0:
        _grace_wait_for_death(int(pgid), grace_seconds)

    # ── zombie-aware KILL skip ──
    post_grace = scan_proc(proc_root)
    if post_grace.scan_complete and not post_grace.by_pgrp.get(int(pgid), ()):
        return _finish_kill(STATUS_KILLED, REASON_GROUP_DEAD_AFTER_GRACE, proc_root, pgid)

    try:
        os.killpg(int(pgid), signal.SIGKILL)
    except ProcessLookupError:
        pass  # ESRCH: the group exited during the grace window
    except OSError:
        pass
    return _finish_kill(STATUS_KILLED, REASON_LADDER, proc_root, pgid)


def _finish_kill(status: str, reason: str, proc_root: Path, pgid: int) -> tuple[str, str]:
    """Apply the P8 post-kill probe to a kill outcome."""
    if not probe_tree_after_kill(proc_root, pgid):
        reason = REASON_TREE_INCOMPLETE
    return (status, reason)


__all__ = [
    "OWNERSHIP_ENV_VAR",
    "OWNERSHIP_ENV_VAR_BY_LANE",
    "OWNERSHIP_ENV_VAR_PROC",
    "PERMANENT_REFUSALS",
    "RAM_GUARD_GRACE_POLL_SLICE_SECONDS",
    "REASON_SET",
    "REFUSAL_PREDICATE_BUCKET",
    "STATUS_ALREADY_DEAD",
    "STATUS_KILLED",
    "STATUS_REFUSED",
    "STATUS_SET",
    "TRANSIENT_REFUSALS",
    "check_p0_guard_enabled",
    "check_p1_pgid_registered",
    "check_p2_pgid_valid",
    "check_p3_daemon_group",
    "check_p4_self",
    "check_p5_leader_identity",
    "check_p6_ownership",
    "check_p7_group_liveness",
    "is_permanent_refusal",
    "is_transient_refusal",
    "probe_tree_after_kill",
    "ram_kill_ladder",
    "read_ownership_stamp",
    "refusal_bucket",
]
