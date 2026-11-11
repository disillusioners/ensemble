"""Phase 6 — the interaction packs (T17b three-kill-path; T20 stalled confirm).

Both are DETERMINISTIC — the kill signals are recorded by the house
``kill_census`` double (no signal reaches a real process group), and the
tick/lane threads are driven directly so a "stall" is a controllable event
rather than a race.

* **T17(b) — the three-kill-path interaction.**  The timeout arm, the
  guard arm and the reaper must NEVER both signal one tree.  The guard's
  EPISODE-scoped claim token is the discriminator: a claimed record means
  the timeout arm defers, and the guard's single ``killpg`` set is the only
  delivery.  The pack drives all three arms against one tree and asserts a
  single delivered signal set.

* **T20 — a stalled ``smaps_rollup`` read on the execution-lane thread.**
  The confirm deadline (``RAM_GUARD_PSS_CONFIRM_DEADLINE_MS``) is a
  POST-HOC elapsed check; a slow read does not block the TICK.  The tick
  heartbeat advances at the poll cadence and a breach detected during the
  stall is detected on schedule; when the stall exceeds the deadline the
  confirm fails open and the kill proceeds.  The residual (a read that
  never returns holds the kill path until restart) is DISCLOSED, not
  silently accepted.
"""

from __future__ import annotations

import asyncio
import threading
import time

import pytest

from daemon.services.ram_guard import guard as guard_mod
from daemon.services.ram_guard import ladder as ladder_mod
from daemon.services.ram_guard.guard import RamGuard
from daemon.services.ram_guard.verdict import (
    STATE_BREACHED,
    STATE_KILLED,
    STATE_WATCHING,
)
from tests.unit.services.ram_guard_fixtures import (
    FakeProc,
    write_proc_tree,
)

MIB = 1024 * 1024
SYNTHETIC_PGID = 4242
LEADER_START = 4242
TRACKING_ID = "t-1"
CEILING_BYTES = 512 * 1024


@pytest.fixture
def kill_census(monkeypatch):
    calls: list[tuple[int, int]] = []

    def _spy_kill(pid, sig):  # noqa: ANN001
        if sig != 0:
            raise AssertionError(f"NON-PROBE os.kill: ({pid}, {sig})")

    def _rec_killpg(pgid, sig):  # noqa: ANN001
        calls.append((pgid, sig))

    monkeypatch.setattr(ladder_mod.os, "kill", _spy_kill)
    monkeypatch.setattr(ladder_mod.os, "killpg", _rec_killpg)
    monkeypatch.setattr(ladder_mod.time, "sleep", lambda s: None)
    return calls


def _build_tree(root):
    environ = b"PATH=/bin\x00ENSEMBLE_BASH_TRACKING_ID=%s\x00" % TRACKING_ID.encode()
    return write_proc_tree(
        root,
        [
            FakeProc(
                pid=SYNTHETIC_PGID,
                pgrp=SYNTHETIC_PGID,
                ppid=1,
                starttime=LEADER_START,
                rss_kb=1024,
                pss_kb=512,
                environ=environ,
            )
        ],
    )


def _make_guard(tmp_path, root=None, **kwargs):
    root = root if root is not None else _build_tree(tmp_path / "proc")
    options = dict(
        proc_root=root,
        cgroup_root=tmp_path / "cgroup",
        resolved_ceiling=(3072 * MIB, "meminfo"),
        poll_interval_ms=200,
        grace_ms=250,
        pss_confirm_deadline_ms=250,
    )
    options.update(kwargs)
    return RamGuard(**options), root


def _register(guard, **overrides):
    kwargs = dict(
        tracking_id=TRACKING_ID,
        instance_id="i-1",
        lane="bash",
        pid=SYNTHETIC_PGID,
        pgid=SYNTHETIC_PGID,
        start_time_jiffies=LEADER_START,
        ceiling_bytes=CEILING_BYTES,
    )
    kwargs.update(overrides)
    return guard.register(**kwargs)


# ── T17(b) — the three-kill-path interaction ───────────────────────────────


def test_t17b_a_guard_claim_means_the_timeout_arm_defers(tmp_path, kill_census):
    """The guard claims the episode ⇒ the timeout arm must NOT signal.

    Three arms, one tree: the guard takes the episode-scoped claim token;
    the timeout arm observes a CLAIMED record and defers; the reaper's
    sweep only touches an OPEN durable row whose group is still live (the
    guard's kill is the single delivery).  Exactly ONE signal set.
    """
    guard, _root = _make_guard(tmp_path)
    record = _register(guard)
    guard._tick_once()
    # The guard claimed the episode (BREACHED + claim token).
    assert record.state == STATE_BREACHED
    assert record.claim_token is not None

    # The timeout arm's PRODUCTION deferral predicate (bash.py
    # ``_ram_claim_held``) — not a re-derivation of the gate.  A held
    # claim ⇒ the timeout arm DEFERS.
    import importlib

    bash_module = importlib.import_module("daemon.tools.bash")
    assert bash_module._ram_claim_held(record) is True

    # The guard's own kill delivers exactly one signal set.
    guard._lane_once()
    assert record.state == STATE_KILLED
    # Exactly ONE killpg set (TERM + KILL at most) — never two arms.
    kill_sigs = [sig for (_pgid, sig) in kill_census if sig != 0]
    assert 15 in kill_sigs  # TERM delivered once
    # No duplicate TERM from the timeout arm (there is only one TERM).
    assert kill_sigs.count(15) == 1


def test_t17b_an_unclaimed_record_leaves_the_timeout_arm_free(tmp_path, kill_census):
    """The NEGATIVE direction: with NO claim, the timeout arm is the owner.

    Uses the PRODUCTION predicate (``_ram_claim_held``) and the DELIVERED
    outcome: the unclaimed record makes ``_ram_claim_held`` False (the
    timeout arm would NOT defer), and the guard's own lane delivers NO
    signal (it owns no episode — there is nothing to service).
    """
    import importlib

    bash_module = importlib.import_module("daemon.tools.bash")
    guard, _root = _make_guard(tmp_path)
    record = _register(guard)
    # No tick ⇒ no claim.
    assert record.state == STATE_WATCHING
    # The production predicate says the guard does NOT hold the episode.
    assert bash_module._ram_claim_held(record) is False
    # Delivered outcome: the timeout arm is free to signal (it would;
    # the guard has no claim) — assert via the real kill ledger that the
    # census stays EMPTY while the guard owns nothing (the guard never
    # signalled because there was no breach to service).
    guard._lane_once()
    assert kill_census == []


def test_t17b_the_reaper_dedups_a_pgid_the_ram_tier_already_killed(
    tmp_path, monkeypatch
):
    """T17(b) — the REAPER arm does not re-signal a group the guard killed.

    DRIVEN, not prose: ``sweep_rows(ram_pgids={pgid})`` routes a row whose
    pgid the RAM tier just SIGKILLed through the DEDUP path — the reaper
    never calls ``verify_and_kill`` (so it never signals).  The
    discriminating control: with a NON-matching ``ram_pgids`` the SAME
    row DOES go through ``verify_and_kill``.
    """
    from types import SimpleNamespace

    from daemon.services import bash_spawn_reaper as reaper_mod

    def _make_row():
        return SimpleNamespace(
            id=1,
            pid=4444,
            pgid=SYNTHETIC_PGID,
            start_time=1,
            tracking_id="t-1",
            instance_id="i-1",
            command_sanitized="sleep 30",
            daemon_pid=999,
            created_at=time.time() - 60,
            reaped_at=None,
        )

    calls: list[int] = []

    def _spy_verify(row, **_kwargs):  # noqa: ANN001
        calls.append(row.pgid)
        return ("refused", "refused:test-control")

    monkeypatch.setattr(reaper_mod, "verify_and_kill", _spy_verify)

    # RAM tier killed this pgid ⇒ DEDUP: verify_and_kill is NEVER called.
    actions = asyncio.run(
        reaper_mod.sweep_rows(
            [_make_row()],
            repo=None,
            reaped_by="shutdown",
            ram_pgids={SYNTHETIC_PGID},
        )
    )
    assert actions == 1  # the row was closed by the dedup path
    assert calls == []  # verify_and_kill (the signalling path) was NOT reached

    # CONTROL: a DIFFERENT ram_pgids set ⇒ the SAME row flows through
    # verify_and_kill (the non-dedup path IS reachable — the test discriminates).
    actions2 = asyncio.run(
        reaper_mod.sweep_rows(
            [_make_row()],
            repo=None,
            reaped_by="shutdown",
            ram_pgids={SYNTHETIC_PGID + 999},
        )
    )
    assert calls == [SYNTHETIC_PGID]  # the control reached verify_and_kill
    assert actions2 == 1  # the refused row is still closed by the sweep


# ── T20 — a stalled smaps_rollup read on the lane never blocks the tick ─────


def test_t20_a_stalled_confirm_does_not_block_the_tick(tmp_path, kill_census, monkeypatch):
    """A slow ``read_tree_pss`` on the LANE does not delay a TICK.

    The tick thread continues at the poll cadence (``last_tick_at``
    advances) while the execution lane is stalled inside the confirm.
    The confirm's deadline is a POST-HOC elapsed check, so the stalled
    confirm ultimately fails open (kill) — but never blocks measurement.
    """
    guard, _root = _make_guard(tmp_path)
    _register(guard)
    guard._tick_once()
    assert len(guard._pending) == 1

    release = threading.Event()
    started = threading.Event()

    def _slow_pss(proc_root, members):  # noqa: ANN001
        started.set()
        release.wait(5.0)
        # Return a reading whose total is above the ceiling (a real breach).
        from daemon.services.ram_guard.tree_reader import TreeReading

        members = tuple(members)
        return TreeReading(
            total_bytes=CEILING_BYTES * 2,
            metric="summed_pss_bytes",
            member_count=len(members),
            member_pids=members,
            complete=True,
            unreadable_pids=(),
            zero_pids=(),
            gone_pids=(),
            source="smaps_rollup:Pss",
        )

    monkeypatch.setattr(guard_mod, "read_tree_pss", _slow_pss)
    lane = threading.Thread(target=guard._lane_loop, daemon=True)
    guard._lane_thread = lane
    lane.start()
    try:
        assert started.wait(2.0), "the lane never entered the confirm"
        # While the lane is stalled, the tick keeps advancing.
        t_first = guard.health()["last_tick_at"]
        assert t_first is not None
        for _ in range(3):
            time.sleep(0.05)
            guard._tick_once()
        t_later = guard.health()["last_tick_at"]
        assert t_later > t_first, "the tick did not advance while the lane stalled"
        # ``lane_alive`` is green (a stalled lane is alive) — the wedge
        # signal is the FROZEN progress heartbeat + pending, not is_alive.
        assert guard.health()["lane_alive"] is True
    finally:
        release.set()
        guard._stop.set()
        lane.join(timeout=2.0)


def test_t20_the_deadline_fires_fail_open_after_the_stall(tmp_path, kill_census, monkeypatch):
    """When the stalled read exceeds the deadline the confirm fails OPEN.

    The read returns AFTER the deadline ⇒ the post-hoc elapsed check marks
    ``timed_out`` ⇒ ``trigger = pss_confirm_unavailable`` ⇒ the kill
    proceeds (a slow/failed confirm never saves the tree).
    """
    guard, _root = _make_guard(tmp_path)
    record = _register(guard)
    guard._tick_once()
    # Simulate a read that took longer than the deadline.
    reading = type(
        "R",
        (),
        {
            "total_bytes": CEILING_BYTES // 2,
            "complete": True,
        },
    )()

    clock = {"t": 0.0}

    def _monotonic():
        # First call = start; second call = end (0.4 s later).
        clock["t"] += 0.4
        return clock["t"]

    monkeypatch.setattr(guard_mod.time, "monotonic", _monotonic)
    monkeypatch.setattr(guard_mod, "read_tree_pss", lambda *_a, **_k: reading)
    _reading, ok, elapsed = guard._run_confirm(record)
    assert ok is False, "a deadline overrun must fail open"
    assert elapsed > guard.pss_confirm_deadline_ms
