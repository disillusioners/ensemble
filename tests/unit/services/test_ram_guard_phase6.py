# ENSEMBLE-32: platform-fixture rationale — see docs/runbooks/ram-guard-and-unit-bounds.md "Test-platform census"
"""Phase 6 — the guard-level additions (bounded, deterministic, NO subprocesses).

This pack owns the Phase-6-only assertions that were NOT part of the
Phase-2 substrate pack:

* **T11(b) — the FOUR death modes** (§5).  The raising variants live in
  ``test_ram_guard_guard.py``; this pack adds the WEDGED variants:
    - tick WEDGED (a never-returning scan) ⇒ ``last_tick_at`` frozen is
      the ONLY signal and ``missed_samples`` does NOT climb;
    - lane WEDGED (a never-returning service) ⇒ ``last_lane_progress_at``
      frozen + ``lane_pending`` climbing while ``lane_alive`` stays True
      (``Thread.is_alive()`` reports a wedged lane green).
  Plus the resume leg: a raising tick's outer restart loop RESUMES the
  body and the heartbeat ADVANCES.
* **T18 — the bounded, non-lethal liveness sweep** (O10).  The sweep
  examines only the registry (never the host pid space), drops a dead
  entry, keeps a live one, and NEVER signals.
* **T25 — the three RELEASE TRIGGERS** (§15 B21) exercised explicitly.
* **T22 — the canonical Q2 strings** pinned char-for-char and the
  leaderless case yielding NO reason code.
* **The DRIVEN lifespan test** (phase-5 review): OFF ⇒ the guard starts
  ZERO threads and owns neither thread object; the detached join does not
  block the caller.
* **D8 — the bash-lane ``ram_observed_peak_bytes`` surface name pinned**
  (both lanes' surfaces carry it; the proc side is pinned in
  ``tests/tools/test_proc_ram_guard.py``).

The ladder's signals are fenced by ``kill_census`` (no signal reaches a
real process group) — the same house double the substrate pack uses.
"""

from __future__ import annotations

import logging
import threading
import time

import pytest

from daemon.services.ram_guard import guard as guard_mod
from daemon.services.ram_guard.guard import (
    COUNTER_KEYS,
    HEARTBEAT_KEYS,
    RAM_GUARD_PENDING_MAX,
    RamGuard,
)
from daemon.services.ram_guard.ladder import REASON_GROUP_EMPTY
from daemon.services.ram_guard.verdict import (
    STATE_KILLED,
    STATE_RELEASED,
    STATE_WATCHING,
)
from tests.unit.services.ram_guard_fixtures import (
    FakeProc,
    kill_tree,
    write_proc_tree,
)

MIB = 1024 * 1024
SYNTHETIC_PGID = 4242
LEADER_START = 4242
TRACKING_ID = "t-1"
CEILING_BYTES = 512 * 1024


# ── The house census (no signal delivered) ─────────────────────────────────


@pytest.fixture
def kill_census(monkeypatch):
    from daemon.services.ram_guard import ladder as ladder_mod

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


def _build_tree(root, *, members=1, rss_kb=1024, pss_kb=512):
    environ = b"PATH=/bin\x00ENSEMBLE_BASH_TRACKING_ID=%s\x00" % TRACKING_ID.encode()
    procs = [
        FakeProc(
            pid=SYNTHETIC_PGID,
            pgrp=SYNTHETIC_PGID,
            ppid=1,
            starttime=LEADER_START,
            rss_kb=rss_kb,
            pss_kb=pss_kb,
            environ=environ,
        )
    ]
    for i in range(members):
        procs.append(
            FakeProc(
                pid=SYNTHETIC_PGID + 100 + i,
                pgrp=SYNTHETIC_PGID,
                ppid=SYNTHETIC_PGID,
                starttime=LEADER_START + 1,
                rss_kb=rss_kb,
                pss_kb=pss_kb,
                environ=environ,
            )
        )
    return write_proc_tree(root, procs)


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


# ── T11(b) — tick WEDGED ────────────────────────────────────────────────────


def test_t11b_a_wedged_tick_freezes_last_tick_at_and_does_not_count_a_miss(
    tmp_path, monkeypatch
):
    """A never-returning scan ⇒ ``last_tick_at`` frozen; ``missed_samples``
    does NOT climb (the tick never returns to count a miss).

    The tick thread is NOT recoverable from a wedge — no supervisor
    exists (§5).  The ONLY signal is the frozen heartbeat.
    """
    guard, _root = _make_guard(tmp_path)
    _register(guard)
    release = threading.Event()

    def _wedge(*_a, **_k):
        release.wait(5.0)  # never returns while wedged
        return {"x": 1}

    # C6: ``_last_tick_at`` is stamped only AFTER a completed scan, so
    # let one tick complete first to establish a frozen baseline.
    guard._tick_once()
    assert guard.health()["last_tick_at"] is not None
    frozen = guard.health()["last_tick_at"]

    # Then the scan wedges — the heartbeat freezes at the prior value.
    monkeypatch.setattr(guard_mod, "scan_proc", _wedge)
    thread = threading.Thread(target=guard._tick_loop, daemon=True)
    guard._tick_thread = thread  # the health surface reads this object
    thread.start()
    try:
        missed_before = guard.health()["missed_samples"]
        time.sleep(0.15)  # several poll intervals while wedged
        snap = guard.health()
        # The heartbeat is FROZEN — the only wedge signal.
        assert snap["last_tick_at"] == frozen
        # The miss counter does NOT climb — the tick never returned.
        assert snap["missed_samples"] == missed_before
        # ``thread_alive`` reports a WEDGED thread GREEN (the documented
        # limitation — that is why the heartbeat, not is_alive, is the
        # signal).
        assert snap["thread_alive"] is True
    finally:
        release.set()
        guard._stop.set()
        thread.join(timeout=2.0)


# ── T11(b) — lane WEDGED ────────────────────────────────────────────────────


def test_t11b_a_wedged_lane_freezes_the_progress_heartbeat_while_pending_climbs(
    tmp_path, kill_census
):
    """A never-returning service ⇒ ``last_lane_progress_at`` frozen +
    ``lane_pending`` climbing, while ``lane_alive`` stays True.

    The lane wedge signal is the PROGRESS heartbeat, NOT
    ``Thread.is_alive()`` (a wedged thread is alive).
    """
    root = write_proc_tree(
        tmp_path / "proc",
        [
            FakeProc(
                pid=SYNTHETIC_PGID,
                pgrp=SYNTHETIC_PGID,
                starttime=LEADER_START,
                rss_kb=1024,
                pss_kb=512,
                environ=b"ENSEMBLE_BASH_TRACKING_ID=t-1\x00",
            )
        ],
    )
    guard, _root = _make_guard(tmp_path, root=root)
    _register(guard)

    release = threading.Event()

    def _blocked_service(tracking_id, token):  # noqa: ANN001
        release.wait(5.0)

    guard._service_claim = _blocked_service  # type: ignore[method-assign]

    # Claim a breach so the lane actually enters the service.
    guard._tick_once()
    assert guard.health()["lane_pending"] >= 1

    before = guard.health()["last_lane_progress_at"]
    thread = threading.Thread(target=guard._lane_loop, daemon=True)
    guard._lane_thread = thread
    thread.start()
    try:
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            if guard.health()["lane_pending"] == 0:
                break
            time.sleep(0.01)
        # The lane popped the claim and is now wedged in the service.
        assert guard.health()["last_lane_progress_at"] == before  # frozen
        # The lane thread is alive (a wedged thread reports green).
        assert guard.health()["lane_alive"] is True
        # A second breach detected during the stall climbs lane_pending
        # while the progress heartbeat stays frozen.
        _register(guard, tracking_id="t-2", pid=SYNTHETIC_PGID + 100)
        guard._tick_once()
        time.sleep(0.05)
        snap = guard.health()
        assert snap["last_lane_progress_at"] == before
        assert snap["lane_pending"] >= 1
    finally:
        release.set()
        guard._stop.set()
        thread.join(timeout=2.0)


def test_t11a_a_raising_tick_resumes_and_the_heartbeat_advances(tmp_path):
    """A raising tick's outer restart loop RESUMES the body and the
    heartbeat ADVANCES on the next successful tick."""
    guard, _root = _make_guard(tmp_path)
    _register(guard)
    calls = {"n": 0}
    original = guard._tick_once

    def _boom_once():
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("tick boom once")
        # The SECOND call runs the real body ⇒ the heartbeat advances.
        original()

    guard._tick_once = _boom_once  # type: ignore[method-assign]

    deadline = time.monotonic() + 2.0
    thread = threading.Thread(target=guard._tick_loop, daemon=True)
    guard._tick_thread = thread
    thread.start()
    try:
        # Wait for the RESUMED body to complete a successful tick (the
        # heartbeat is set INSIDE the body; the miss reset happens just
        # AFTER the body returns — wait for BOTH, bounded).
        while time.monotonic() < deadline:
            snap = guard.health()
            if snap["last_tick_at"] is not None and snap["missed_samples"] == 0:
                break
            time.sleep(0.01)
        snap = guard.health()
        assert calls["n"] >= 2  # the body was RESUMED after the raise
        assert snap["last_tick_at"] is not None  # heartbeat advanced
        # The successful tick RESET the miss counter (0 after the resume).
        assert snap["missed_samples"] == 0
        assert snap["ticks"] >= 1
    finally:
        guard._stop.set()
        thread.join(timeout=2.0)


# ── Tick-path silence (S17 / O11 — the POSITIVE drive) ─────────────────────


def test_the_tick_path_emits_no_log_line_even_on_breach(tmp_path, kill_census, caplog):
    """S17/O11 — driving the REAL tick body (which breaches) emits NO
    ``daemon.services.ram_guard`` log line.  The tick's failures/logs are
    the ONLY channel the design forbids; the lane logs the transition."""
    guard, _root = _make_guard(tmp_path)
    _register(guard)
    with caplog.at_level(logging.DEBUG):
        guard._tick_once()
    tick_lines = [
        r for r in caplog.records if r.name.startswith("daemon.services.ram_guard")
    ]
    assert tick_lines == [], [r.getMessage() for r in tick_lines]


def test_a_raising_tick_climbs_missed_samples(tmp_path):
    """A raising tick INCREMENTS ``missed_samples`` (the counter, not a log)."""
    guard, _root = _make_guard(tmp_path)
    _register(guard)

    def _boom():
        guard._stop.set()  # stop the restart loop after the first failure
        raise RuntimeError("tick boom")

    guard._tick_once = _boom  # type: ignore[method-assign]
    guard._tick_loop()
    assert guard.health()["missed_samples"] >= 1


# ── T18 — the bounded, non-lethal liveness sweep (O10) ─────────────────────


def test_t18_the_sweep_is_bounded_to_the_registry_and_never_signals(
    tmp_path, monkeypatch
):
    """The sweep examines ONLY the registry (never the host pid space) and
    never signals — the bound is the registry depth.

    A dead entry is dropped; a live-but-unreleased entry is KEPT (the safe
    direction).
    """
    root = write_proc_tree(
        tmp_path / "proc",
        [
            FakeProc(pid=4242, pgrp=4242, starttime=LEADER_START, rss_kb=1024, pss_kb=512,
                     environ=b"ENSEMBLE_BASH_TRACKING_ID=t-1\x00"),
            FakeProc(pid=4342, pgrp=4242, ppid=4242, starttime=LEADER_START + 1, rss_kb=1024, pss_kb=512,
                     environ=b"ENSEMBLE_BASH_TRACKING_ID=t-1\x00"),
            FakeProc(pid=5252, pgrp=5252, starttime=LEADER_START, rss_kb=1024, pss_kb=512,
                     environ=b"ENSEMBLE_BASH_TRACKING_ID=t-1\x00"),
        ],
    )
    guard, _root = _make_guard(tmp_path, root=root)
    # Any kill would be a defect — the sweep is NON-LETHAL.  Signal 0 (a
    # liveness probe) is allowed; any real signal is a failure.
    def _no_signal(pid, sig):  # noqa: ANN001
        if sig != 0:
            raise AssertionError(f"the liveness sweep must never signal: ({pid}, {sig})")

    from daemon.services.ram_guard import ladder as ladder_mod

    monkeypatch.setattr(ladder_mod.os, "kill", _no_signal)
    monkeypatch.setattr(ladder_mod.os, "killpg", _no_signal)

    _register(guard, tracking_id="t-live", pid=4242, pgid=4242, ceiling_bytes=64 * MIB)
    _register(guard, tracking_id="t-dead", pid=5252, pgid=5252, ceiling_bytes=64 * MIB)
    guard._tick_once()
    # Kill the second tree entirely — its entry becomes droppable.
    kill_tree(root, [5252])
    guard._sweep_every_ticks = 1
    guard._tick_once()
    assert guard.read_verdict("t-dead").state == STATE_RELEASED
    assert guard.read_verdict("t-live").state == STATE_WATCHING
    # The sweep never touched a pid outside the registry (bounded): no
    # os.kill/ os.killpg call was ever made with a non-zero signal.
    # (The assertion above in _no_signal enforces this; reaching here
    # proves the bound.)


# ── T25 — the three RELEASE TRIGGERS (B21) ─────────────────────────────────


def test_t25_trigger_i_group_member_count_zero_via_the_tick_sweep(tmp_path, kill_census):
    """Trigger (i): ``group_member_count == 0`` — the tick-thread sweep."""
    root = write_proc_tree(
        tmp_path / "proc",
        [FakeProc(pid=5252, pgrp=5252, starttime=LEADER_START, rss_kb=1024, pss_kb=512,
                  environ=b"ENSEMBLE_BASH_TRACKING_ID=t-1\x00")],
    )
    guard, _root = _make_guard(tmp_path, root=root)
    record = _register(guard, tracking_id="t-i", pid=5252, pgid=5252, ceiling_bytes=64 * MIB)
    guard._tick_once()
    kill_tree(root, [5252])
    guard._sweep_every_ticks = 1
    guard._tick_once()
    assert record.state == STATE_RELEASED


def test_t25_trigger_ii_terminal_resolution_with_empty_group_via_the_lane(
    tmp_path, kill_census, monkeypatch
):
    """Trigger (ii): terminal resolution with an empty group — the lane.

    The record breaches (RSS over) and the confirm still proves the shared
    total, but by the time the LADDER scans the group is empty ⇒
    ``already-dead:group-empty`` ⇒ the lane releases (B21 trigger (ii)).
    """
    root = write_proc_tree(
        tmp_path / "proc",
        [FakeProc(pid=SYNTHETIC_PGID, pgrp=SYNTHETIC_PGID, starttime=LEADER_START,
                  rss_kb=1024, pss_kb=512, environ=b"ENSEMBLE_BASH_TRACKING_ID=t-1\x00")],
    )
    guard, _root = _make_guard(tmp_path, root=root)
    record = _register(guard)
    guard._tick_once()  # breach + claim
    # The confirm succeeds with the breach-time PSS reading (>= ceiling) —
    # the tree collapses only between the confirm and the ladder scan.
    from daemon.services.ram_guard.tree_reader import TreeReading

    def _confirm_reading(proc_root, members):  # noqa: ANN001
        return TreeReading(
            total_bytes=CEILING_BYTES,
            metric="summed_pss_bytes",
            member_count=len(tuple(members)),
            member_pids=tuple(members),
            complete=True,
            unreadable_pids=(),
            zero_pids=(),
            gone_pids=(),
            source="smaps_rollup:Pss",
        )

    monkeypatch.setattr(guard_mod, "read_tree_pss", _confirm_reading)
    kill_tree(root, [SYNTHETIC_PGID])
    guard._lane_once()
    assert record.reason == REASON_GROUP_EMPTY
    assert record.state == STATE_RELEASED


def test_t25_trigger_iii_post_kill_probe_empty_group_via_the_lane(tmp_path, kill_census):
    """Trigger (iii): the post-kill probe confirms an empty group on the
    RAM path — the lane releases after the kill."""
    root = write_proc_tree(
        tmp_path / "proc",
        [FakeProc(pid=SYNTHETIC_PGID, pgrp=SYNTHETIC_PGID, starttime=LEADER_START,
                  rss_kb=1024, pss_kb=512, environ=b"ENSEMBLE_BASH_TRACKING_ID=t-1\x00")],
    )
    guard, _root = _make_guard(tmp_path, root=root)
    record = _register(guard)
    guard._tick_once()
    # The census records the kill; simulate the tree dying at the KILL.
    from daemon.services.ram_guard import ladder as ladder_mod

    def _killpg_and_die(pgid, sig):  # noqa: ANN001
        if sig == 9:
            kill_tree(root, [SYNTHETIC_PGID])

    import unittest.mock as _mock

    with _mock.patch.object(ladder_mod.os, "killpg", _killpg_and_die):
        guard._lane_once()
    # The post-kill probe confirmed the empty group ⇒ ``killed:ladder``
    # (NOT ``killed:tree-incomplete``) and the lane released (trigger iii).
    from daemon.services.ram_guard.ladder import REASON_LADDER

    assert record.reason == REASON_LADDER
    assert record.state == STATE_RELEASED


# ── T22 — the canonical Q2 strings (char-for-char) ─────────────────────────


Q2_STRINGS = (
    "the watch stays alive until the group member count reaches 0",
    "identity = captured pgid ∧ surviving-member group membership",
    "starttime gate applies to the leader only while alive",
    "the watch is released only at group member count 0",
    "group_member_count == 0",
)


def test_t22_the_canonical_q2_strings_are_pinned_in_the_guard_module():
    """§15 B6/B12 — the five canonical Q2 strings, char-for-char.

    They live in the guard's module docstring (the single source); a
    drift is a defect (phase6 T22 / test-plan S8 are the SAME semantics).
    """
    doc = guard_mod.__doc__ or ""
    for string in Q2_STRINGS:
        assert string in doc, f"canonical Q2 string drifted: {string!r}"


def test_p5_leaderless_case_no_reason_code(tmp_path, kill_census):
    """The leaderless case re-anchors identity and yields NO reason code
    (a kill proceeds on group membership; there is no ``refused:``)."""
    from daemon.services.ram_guard.ladder import ram_kill_ladder

    root = write_proc_tree(
        tmp_path / "proc",
        [
            FakeProc(pid=SYNTHETIC_PGID + 100, pgrp=SYNTHETIC_PGID, ppid=1,
                     starttime=LEADER_START + 1, rss_kb=1024, pss_kb=512,
                     environ=b"ENSEMBLE_BASH_TRACKING_ID=t-1\x00"),
        ],
    )
    status, reason = ram_kill_ladder(
        SYNTHETIC_PGID, LEADER_START, 0.25, proc_root=root,
        captured_pgid=SYNTHETIC_PGID,
    )
    # A leaderless tree kills on group membership — NOT a refusal.
    assert status == "killed"
    assert not reason.startswith("refused:")


# ── T23 — the fixture split and the test-name shape (§15 B12) ──────────────


def test_t23_the_predicate_test_names_follow_the_b12_shape():
    """Every P0–P7 predicate leg in the ladder pack is named
    ``test_p<n>_<case>_<expected_status>`` (§15 B12, verbatim)."""
    import re
    from pathlib import Path

    text = (
        Path(__file__).resolve().parent / "test_ram_guard_ladder.py"
    ).read_text(encoding="utf-8")
    all_names = re.findall(r"^def (test_\w+)\(", text, flags=re.MULTILINE)
    assert all_names, "no tests found"
    predicate_names = [n for n in all_names if re.match(r"test_p\d_", n)]
    assert predicate_names, "no predicate tests found"
    for name in predicate_names:
        assert re.fullmatch(r"test_p\d+_[a-z0-9_]+", name), name


def test_t23_no_ladder_leg_runs_under_the_signal_fence():
    """The fixture-split rule, ENUMERATED over every leg in the packs.

    ``kill_fence`` hard-raises on ANY signal ⇒ a leg that DELIVERS a
    signal (``status == "killed"`` or a bare-thread ladder) must be
    ``kill_census``-backed, never ``kill_fence``.  A leg that can only
    REFUSE (P0–P7 refusal branches) is ``kill_fence``-backed.  Pure
    predicate legs (no ``ram_kill_ladder`` call) are exempt.  This mirrors
    the manual audit, for ALL legs — not a two-leg spot-check.
    """
    import ast
    from pathlib import Path

    text = (
        Path(__file__).resolve().parent / "test_ram_guard_ladder.py"
    ).read_text(encoding="utf-8")
    tree = ast.parse(text)
    killing_legs: list[str] = []
    refusal_legs: list[str] = []
    for node in tree.body:
        if not (isinstance(node, ast.FunctionDef) and node.name.startswith("test_")):
            continue
        params = {a.arg for a in node.args.args}
        uses_fence = "kill_fence" in params
        uses_census = "kill_census" in params
        seg = ast.get_source_segment(text, node) or ""
        calls_ladder = "ram_kill_ladder(" in seg
        if not calls_ladder:
            continue  # a pure predicate leg — no signal can fire
        asserts_killed = '"killed"' in seg  # the leg expects the ladder to SIGNAL
        if asserts_killed:
            killing_legs.append(node.name)
            assert uses_census, f"{node.name}: a KILL leg must be kill_census-backed"
            assert not uses_fence, f"{node.name}: a KILL leg must NOT be kill_fence-backed"
        else:
            # A ladder leg that asserts only refusal / already-dead outcomes.
            refusal_legs.append(node.name)
            assert uses_fence, f"{node.name}: a refusal leg must be kill_fence-backed"
            # A refusal leg may also census (tolerated) — no assertion.
    # The enumeration is non-vacuous: BOTH classes exist and were classified.
    assert len(killing_legs) >= 8, killing_legs
    assert len(refusal_legs) >= 8, refusal_legs


# ── The DRIVEN lifespan test (phase-5 review recommendation) ───────────────


def test_the_off_guard_starts_zero_threads_and_owns_neither():
    """OFF ⇒ the guard starts NO thread and owns neither thread object
    (the driven OFF path — ``app.state.ram_guard is None`` is the
    composition root's posture, asserted at source in the events pack)."""
    guard = RamGuard(enabled=False)
    before = {t.name for t in threading.enumerate() if t.name.startswith("ram-guard")}
    guard.start()
    after = {t.name for t in threading.enumerate() if t.name.startswith("ram-guard")}
    # Zero guard threads appeared.
    assert after == before
    # Neither thread object exists.
    assert guard._tick_thread is None
    assert guard._lane_thread is None
    assert guard.health()["thread_alive"] is False
    assert guard.health()["lane_alive"] is False


def test_t11c_a_live_then_dead_guard_flips_thread_alive_false(tmp_path):
    """T11(c) — a LIVE started guard, once its tick thread is DEAD, reports
    ``thread_alive`` False.

    The other OFF/lifecycle legs only assert ``thread_alive`` False for
    NEVER-STARTED / absent shapes.  A mutant that dropped the
    ``is_alive()`` check (keeping only the ``not-None`` test) would stay
    green against those.  This leg starts a LIVE guard, proves
    ``thread_alive`` True, then stops + joins the real tick thread so the
    object is DEAD but NON-None, and asserts ``thread_alive`` flips False
    — the death is visible on the read surface (§8 T11(c)).
    """
    guard, _root = _make_guard(tmp_path)
    guard.start()
    try:
        deadline = time.monotonic() + 2.0
        while not guard.health()["thread_alive"] and time.monotonic() < deadline:
            time.sleep(0.01)
        # A LIVE started guard ⇒ thread_alive True.
        assert guard.health()["thread_alive"] is True
        assert guard._tick_thread is not None  # the object EXISTS
        guard.stop()
        guard.join(timeout=3.0)
        # The thread is now DEAD but the object is still non-None ⇒ the
        # is_alive() check (not merely the not-None check) drives the
        # surface, and it flips False.
        assert guard._tick_thread is not None
        assert guard._tick_thread.is_alive() is False
        assert guard.health()["thread_alive"] is False
        assert guard.health()["lane_alive"] is False
    finally:
        guard.stop()
        guard.join(timeout=3.0)


def test_the_detached_join_does_not_block_the_caller(tmp_path):
    """The shutdown's detached join starts a thread that returns promptly;
    the caller (the event loop) is never blocked."""
    guard, _root = _make_guard(tmp_path)
    guard.start()
    guard.stop()
    started = time.monotonic()
    joiner = threading.Thread(target=guard.join, kwargs={"timeout": 2.0}, daemon=True)
    joiner.start()
    elapsed_start = time.monotonic() - started
    # Starting the detached join is non-blocking.
    assert elapsed_start < 1.0
    joiner.join(timeout=3.0)
    assert not joiner.is_alive()


# ── K5 — OFF ⇒ zero guard/repository touches on the bash OFF path ──────────


async def test_k5_off_touches_no_guard_registry_or_repository(monkeypatch):
    """K5 — the OFF path performs ZERO guard-registry / repository calls.

    With ``ENSEMBLE_RAM_GUARD=0`` the bash lane must not reach the guard
    facade's ``register`` at all (the switch is checked before the
    repository seam).  The counter is a recording spy: zero calls.
    """
    import importlib

    bash_module = importlib.import_module("daemon.tools.bash")
    monkeypatch.setenv("ENSEMBLE_RAM_GUARD", "0")
    calls: list[dict] = []

    def _spy_register(**kwargs):  # noqa: ANN001
        calls.append(kwargs)
        return None

    monkeypatch.setattr(bash_module, "_ram_register", _spy_register)
    # A real guard is installed so the ONLY reason no register fires is the
    # switch — this makes the absence attributable to OFF, not to a missing
    # guard.
    guard = RamGuard(enabled=True, resolved_ceiling=(3072 * MIB, "meminfo"))
    from daemon.services.ram_guard import set_ram_guard

    set_ram_guard(guard)
    try:
        out = await bash_module.bash.coroutine(command="echo hi", instance_id="k5-inst")
    finally:
        set_ram_guard(None)
    assert "EXIT CODE: 0" in out
    assert calls == [], f"the OFF path touched the guard registry: {calls}"


# ── D8 — the bash-lane ram_observed_peak_bytes surface name ────────────────


def test_d8_the_bash_lane_carries_the_ram_observed_peak_bytes_line_name():
    """D8 (leader-ordered) — the bash lane's additive peak line is named
    ``ram_observed_peak_bytes`` (and the proc lane pins the SAME name in
    ``tests/tools/test_proc_ram_guard.py``).

    The envelope/event key is ``observed_peak_bytes``; the ADDITIVE SURFACE
    line (the refusal / wait-expiry surface) carries the prefixed name so
    the two lanes speak identically.
    """
    import importlib

    bash_mod = importlib.import_module("daemon.tools.bash")
    from daemon.services.ram_guard import errors as errors_mod

    assert bash_mod.RAM_PEAK_LINE == "ram_observed_peak_bytes: {peak_bytes}"
    # Both lanes carry the SAME line name verbatim.
    assert errors_mod.PROC_PEAK_LINE == bash_mod.RAM_PEAK_LINE
    # The envelope/event key set carries the unprefixed key.
    assert "observed_peak_bytes" in errors_mod.ENVELOPE_KEYS
    assert "observed_peak_bytes" in errors_mod.EVENT_PAYLOAD_KEYS


# ── The counter/heartbeat surfaces do not drift under this pack ────────────


def test_the_counter_and_heartbeat_key_sets_are_stable():
    assert "ram_kill_refusals_p8" not in COUNTER_KEYS
    assert len(HEARTBEAT_KEYS) == 11


import pytest

@pytest.fixture(autouse=True)
def _ensemble32_linux_platform(monkeypatch):
    monkeypatch.setattr("sys.platform", "linux")
