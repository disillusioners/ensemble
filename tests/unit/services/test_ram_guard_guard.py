"""Substrate tier — the guard: the two threads, the registry, the
pending set, the liveness sweep and the event HOOK
(test-plan §3.1 case S6, §3.7 N3, §3.8 O10/O11/O12/O14).

The tick and the lane bodies are driven DIRECTLY (``_tick_once`` /
``_lane_once``) so the legs are deterministic — the thread LIFECYCLE is
exercised separately.  The ladder's signals are fenced by the
``kill_census`` double (the same house idiom as the ladder pack): no
signal ever reaches a real process group.
"""

from __future__ import annotations

import json
import logging
import shutil

import pytest

from daemon.services.ram_guard import guard as guard_mod
from daemon.services.ram_guard import ladder as ladder_mod
from daemon.services.ram_guard.guard import (
    COUNTER_KEYS,
    HEARTBEAT_KEYS,
    RAM_GUARD_PENDING_MAX,
    RamGuard,
)
from daemon.services.ram_guard.verdict import (
    STATE_BREACHED,
    STATE_KILLED,
    STATE_OVERCOUNTED,
    STATE_REFUSED,
    STATE_WATCHING,
)
from tests.unit.services.ram_guard_fixtures import (
    FakeProc,
    kill_tree,
    stat_line,
    write_proc_tree,
)

MIB = 1024 * 1024
SYNTHETIC_PGID = 4242
LEADER_START = 4242
TRACKING_ID = "t-1"
CEILING_BYTES = 512 * 1024  # 512 KiB — below the fake tree's 1 MiB member


@pytest.fixture
def kill_census(monkeypatch):
    """Record ``(pgid, sig)`` without delivering — nothing is signalled."""
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


def build_tree(root, *, members=1, rss_kb=1024, pss_kb=512, environ=None, leader=True):
    """A fake tree whose members carry the ownership stamp by default."""
    if environ is None:
        environ = b"PATH=/bin\x00ENSEMBLE_BASH_TRACKING_ID=%s\x00" % TRACKING_ID.encode()
    procs = []
    if leader:
        procs.append(
            FakeProc(
                pid=SYNTHETIC_PGID,
                pgrp=SYNTHETIC_PGID,
                ppid=1,
                starttime=LEADER_START,
                rss_kb=rss_kb,
                pss_kb=pss_kb,
                environ=environ,
            )
        )
    for i in range(members):
        procs.append(
            FakeProc(
                pid=SYNTHETIC_PGID + 100 + i,
                pgrp=SYNTHETIC_PGID,
                ppid=SYNTHETIC_PGID if leader else 1,
                starttime=LEADER_START + 1,
                rss_kb=rss_kb,
                pss_kb=pss_kb,
                environ=environ,
            )
        )
    return write_proc_tree(root, procs)


def make_guard(tmp_path, root=None, **kwargs):
    """A guard over a fake procfs tree."""
    root = root if root is not None else build_tree(tmp_path / "proc")
    options = dict(
        proc_root=root,
        cgroup_root=tmp_path / "cgroup",
        resolved_ceiling=(3072 * MIB, "derived-meminfo"),
        poll_interval_ms=200,
        grace_ms=250,
        pss_confirm_deadline_ms=250,
    )
    options.update(kwargs)
    return RamGuard(**options), root


def register(guard, **overrides):
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


# ── The thread lifecycle (K6 / §4.2) ───────────────────────────────────────


def test_off_starts_neither_thread(tmp_path):
    """OFF ⇒ NO THREAD AT ALL (neither the tick nor the lane)."""
    guard, _root = make_guard(tmp_path, enabled=False)
    guard.start()
    health = guard.health()
    assert health["enabled"] is False
    assert health["thread_alive"] is False
    assert health["lane_alive"] is False
    assert guard._tick_thread is None
    assert guard._lane_thread is None


def test_on_starts_both_threads(tmp_path):
    """ON ⇒ BOTH threads exist (the tick and the execution lane)."""
    guard, _root = make_guard(tmp_path, poll_interval_ms=50)
    guard.start()
    try:
        for _ in range(200):
            if guard.health()["ticks"] >= 1:
                break
            import time

            time.sleep(0.01)
        health = guard.health()
        assert health["thread_alive"] is True
        assert health["lane_alive"] is True
    finally:
        guard.stop()
        guard.join(timeout=5)


# ── The counter + heartbeat surfaces (B5 / B23) ────────────────────────────


def test_counter_set_is_the_exact_ordered_set():
    """§15 B5 — the counter set, ordered, exact (and NO p8)."""
    assert COUNTER_KEYS == (
        "ram_kills",
        "ram_breaches_detected",
        "ram_kill_refusals_p0",
        "ram_kill_refusals_p1",
        "ram_kill_refusals_p2",
        "ram_kill_refusals_p3",
        "ram_kill_refusals_p4",
        "ram_kill_refusals_p5",
        "ram_kill_refusals_p7",
        "ram_overcounted",
        "ram_rearms",
        "ram_confirm_failures",
        "ram_events_emitted",
        "ram_events_dropped",
        "ownership_unverified",
        "tree_incomplete",
    )
    assert "ram_kill_refusals_p8" not in COUNTER_KEYS


def test_heartbeat_is_the_exact_eleven_field_set():
    """§15 B23 — the eleven-field heartbeat, ordered, exact."""
    assert HEARTBEAT_KEYS == (
        "enabled",
        "thread_alive",
        "last_tick_at",
        "missed_samples",
        "ticks",
        "registry_depth",
        "lane_alive",
        "lane_pending",
        "last_lane_progress_at",
        "resolved_ceiling_bytes",
        "ceiling_source",
    )
    assert len(HEARTBEAT_KEYS) == 11


def test_health_payload_carries_the_eleven_fields(tmp_path):
    """The read surface carries exactly the eleven fields."""
    guard, _root = make_guard(tmp_path)
    health = guard.health()
    assert tuple(health.keys()) == HEARTBEAT_KEYS
    assert health["resolved_ceiling_bytes"] == 3072 * MIB
    assert health["ceiling_source"] == "derived-meminfo"


# ── The tick path (S6 / N3 / S17) ──────────────────────────────────────────


def test_tick_never_reads_pss_and_never_signals(tmp_path, monkeypatch, kill_census):
    """The tick thread never reads PSS and never signals (B17)."""

    def _boom(*_args, **_kwargs):
        raise AssertionError("the tick path must never read PSS")

    monkeypatch.setattr(guard_mod, "read_tree_pss", _boom)
    guard, _root = make_guard(tmp_path)
    register(guard)
    guard._tick_once()
    assert guard.counters_snapshot()["ram_breaches_detected"] == 1
    assert kill_census == []


def test_enumeration_failure_drops_the_sample(tmp_path):
    """S6 — an enumeration failure drops the sample; no breach, no kill."""
    guard, root = make_guard(tmp_path)
    record = register(guard)
    record.record_sample(999 * MIB, now=1.0)
    peak = record.observed_peak_bytes
    # Break the enumeration: the procfs ROOT disappears ⇒ the listing
    # fails ⇒ scan_complete is False.
    shutil.rmtree(root)
    guard._tick_once()
    assert record.enumeration_failures == 1
    assert record.observed_peak_bytes == peak  # the peak keeps its value
    assert record.state == STATE_WATCHING  # no breach was evaluated
    assert guard.counters_snapshot()["ram_breaches_detected"] == 0


def test_breach_fires_on_the_current_sample_not_the_peak(tmp_path):
    """S10 — a spike released before the next sample is reported, NOT killed."""
    guard, _root = make_guard(tmp_path)
    record = register(guard, ceiling_bytes=4 * MIB)
    # A spike that is released before the next sample.
    record.record_sample(8 * MIB, now=1.0)
    guard._tick_once()
    # The CURRENT sample (2 MiB) is below the ceiling ⇒ no breach.
    assert guard.counters_snapshot()["ram_breaches_detected"] == 0
    assert record.state == STATE_WATCHING
    # The peak is still reported.
    assert record.observed_peak_bytes == 8 * MIB


def test_incomplete_but_above_ceiling_proves_a_breach(tmp_path):
    """S11 — an incomplete reading above the ceiling PROVES a breach."""
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
            ),
            FakeProc(
                pid=SYNTHETIC_PGID + 100,
                pgrp=SYNTHETIC_PGID,
                ppid=SYNTHETIC_PGID,
                starttime=LEADER_START + 1,
                rss_kb=1024,
                pss_kb=512,
                status_state="unreadable",
                environ=b"ENSEMBLE_BASH_TRACKING_ID=t-1\x00",
            ),
        ],
    )
    guard, _root = make_guard(tmp_path, root=root)
    record = register(guard)
    guard._tick_once()
    # The unreadable member contributes 0 — but the OTHER member's
    # bytes still cross the ceiling ⇒ the breach proves.
    assert guard.counters_snapshot()["ram_breaches_detected"] >= 1
    assert record.state == STATE_BREACHED
    assert record.tree_complete is False


def test_incomplete_below_ceiling_is_no_evidence(tmp_path):
    """S12 — an incomplete reading below the ceiling evaluates NO breach."""
    root = write_proc_tree(
        tmp_path / "proc",
        [
            FakeProc(
                pid=SYNTHETIC_PGID,
                pgrp=SYNTHETIC_PGID,
                starttime=LEADER_START,
                rss_kb=64,  # 64 KiB — below the 512 KiB ceiling
                pss_kb=32,
                status_state="unreadable",
                environ=b"ENSEMBLE_BASH_TRACKING_ID=t-1\x00",
            )
        ],
    )
    guard, _root = make_guard(tmp_path, root=root)
    record = register(guard)
    guard._tick_once()
    assert guard.counters_snapshot()["ram_breaches_detected"] == 0
    assert record.state == STATE_WATCHING
    assert record.tree_complete is False


# ── The pending set (the overflow re-offers) ──────────────────────────────


def test_pending_overflow_re_offers_and_never_skips_a_confirm(tmp_path, kill_census):
    """The newest breach is NOT claimed and is re-offered next tick."""
    guard, _root = make_guard(tmp_path)
    records = []
    for i in range(RAM_GUARD_PENDING_MAX + 1):
        records.append(
            register(
                guard,
                tracking_id=f"t-{i}",
                pid=SYNTHETIC_PGID + 100 + i,
            )
        )
    guard._tick_once()
    # Exactly RAM_GUARD_PENDING_MAX claims exist.
    assert len(guard._pending) == RAM_GUARD_PENDING_MAX
    assert guard.health()["lane_pending"] == RAM_GUARD_PENDING_MAX
    # The overflowed record stays WATCHING (it is re-offered next tick).
    overflowed = records[-1]
    assert overflowed.state == STATE_WATCHING
    assert overflowed.claim_token is None
    # …and the breach WAS counted.
    assert guard.counters_snapshot()["ram_breaches_detected"] == RAM_GUARD_PENDING_MAX + 1


def test_the_overflowed_breach_is_claimed_on_a_later_tick(tmp_path, kill_census):
    """The re-offered breach is claimed once the pending set drains."""
    guard, _root = make_guard(tmp_path)
    records = [
        register(guard, tracking_id=f"t-{i}", pid=SYNTHETIC_PGID + 100 + i)
        for i in range(RAM_GUARD_PENDING_MAX + 1)
    ]
    guard._tick_once()
    # Drain the pending set through the lane.
    for _ in range(RAM_GUARD_PENDING_MAX):
        guard._lane_once()
    guard._tick_once()
    assert records[-1].claim_token is not None
    assert records[-1].state == STATE_BREACHED


# ── The kill path + the emitter hook (T23 / O1 / N9) ──────────────────────


def test_the_lane_kills_and_emits_exactly_once(tmp_path, kill_census):
    """The lane is the ONLY executor; the emitter is called exactly once."""
    emitted: list[tuple[str, dict]] = []

    def _emitter(instance_id, payload):  # noqa: ANN001
        emitted.append((instance_id, dict(payload)))
        return True

    guard, _root = make_guard(tmp_path, emitter=_emitter)
    record = register(guard)
    guard._tick_once()
    assert record.state == STATE_BREACHED
    guard._lane_once()
    assert record.state == STATE_KILLED
    assert record.reason.startswith("killed:")
    assert len(emitted) == 1
    instance_id, payload = emitted[0]
    assert instance_id == "i-1"
    assert payload["state"] == "KILLED"
    assert payload["trigger"] == "rss_threshold"
    counters = guard.counters_snapshot()
    assert counters["ram_kills"] == 1
    assert counters["ram_events_emitted"] == 1
    assert counters["ram_events_dropped"] == 0


def test_a_raising_emitter_is_counted_as_dropped_and_never_raises(tmp_path, kill_census):
    """A raising emitter ⇒ ``ram_events_dropped++``; it never raises."""
    def _emitter(instance_id, payload):  # noqa: ANN001
        raise RuntimeError("boom")

    guard, _root = make_guard(tmp_path, emitter=_emitter)
    record = register(guard)
    guard._tick_once()
    guard._lane_once()  # must not raise
    counters = guard.counters_snapshot()
    assert counters["ram_events_dropped"] == 1
    assert counters["ram_events_emitted"] == 0
    assert record.state == STATE_KILLED


def test_an_emitter_that_drops_is_counted_as_dropped(tmp_path, kill_census):
    """``False`` ⇒ the event was DROPPED ⇒ ``ram_events_dropped++``."""
    guard, _root = make_guard(tmp_path, emitter=lambda i, p: False)
    register(guard)
    guard._tick_once()
    guard._lane_once()
    counters = guard.counters_snapshot()
    assert counters["ram_events_dropped"] == 1
    assert counters["ram_events_emitted"] == 0


def test_no_emitter_means_no_event_and_no_counter(tmp_path, kill_census):
    """No emitter wired ⇒ no event, counter only."""
    guard, _root = make_guard(tmp_path, emitter=None)
    record = register(guard)
    guard._tick_once()
    guard._lane_once()
    counters = guard.counters_snapshot()
    assert counters["ram_events_emitted"] == 0
    assert counters["ram_events_dropped"] == 0
    assert record.state == STATE_KILLED


def test_the_confirm_deciding_value_is_the_pss_not_the_peak(tmp_path, kill_census):
    """The deciding value is ``confirmed_pss_bytes`` — never the RSS peak."""
    guard, _root = make_guard(tmp_path)
    record = register(guard)
    guard._tick_once()
    guard._lane_once()
    assert record.confirmed_pss_bytes == 2 * 512 * 1024
    assert record.observed_peak_bytes == 2 * 1024 * 1024
    assert record.confirmed_pss_bytes != record.observed_peak_bytes


def test_a_failed_confirm_is_fail_open_and_kills(tmp_path, kill_census):
    """A confirm failure ⇒ FAIL-OPEN ⇒ kill."""
    root = write_proc_tree(
        tmp_path / "proc",
        [
            FakeProc(
                pid=SYNTHETIC_PGID,
                pgrp=SYNTHETIC_PGID,
                starttime=LEADER_START,
                rss_kb=1024,
                pss_state="unreadable",
                environ=b"ENSEMBLE_BASH_TRACKING_ID=t-1\x00",
            )
        ],
    )
    guard, _root = make_guard(tmp_path, root=root)
    record = register(guard)
    guard._tick_once()
    guard._lane_once()
    assert record.state == STATE_KILLED
    assert record.trigger == "pss_confirm_unavailable"
    assert record.confirmed_pss_bytes is None
    assert guard.counters_snapshot()["ram_confirm_failures"] == 1


def test_a_timed_out_confirm_is_fail_open_and_kills(tmp_path, kill_census):
    """A confirm that overruns the deadline ⇒ FAIL-OPEN ⇒ kill."""
    guard, _root = make_guard(tmp_path, pss_confirm_deadline_ms=0)
    record = register(guard)
    guard._tick_once()
    guard._lane_once()
    assert record.state == STATE_KILLED
    assert record.trigger == "pss_confirm_unavailable"
    assert guard.counters_snapshot()["ram_confirm_failures"] == 1


def test_the_lane_never_samples(tmp_path, kill_census, monkeypatch):
    """The lane never samples — the tick owns the measurement."""

    def _boom(*_args, **_kwargs):
        raise AssertionError("the lane must never sample RSS")

    guard, _root = make_guard(tmp_path)
    register(guard)
    guard._tick_once()
    monkeypatch.setattr(guard_mod, "read_tree_rss", _boom)
    guard._lane_once()  # must not raise


# ── The bounded re-arm, end to end (S13) ───────────────────────────────────


def test_the_overcounted_rearm_rearms_and_a_later_breach_kills(tmp_path, kill_census):
    """OVERCOUNTED ⇒ re-arm; a later breach at the re-armed threshold kills.

    The fork tree's summed RSS crosses the ceiling while its summed PSS
    does not ⇒ OVERCOUNTED, not killed.  The re-armed threshold is
    ratcheted and a later breach at it KILLS.
    """
    root = write_proc_tree(
        tmp_path / "proc",
        [
            FakeProc(
                pid=SYNTHETIC_PGID,
                pgrp=SYNTHETIC_PGID,
                starttime=LEADER_START,
                rss_kb=1024,
                pss_kb=64,  # the shared-page shape: RSS ≫ PSS
                environ=b"ENSEMBLE_BASH_TRACKING_ID=t-1\x00",
            )
        ],
    )
    guard, _root = make_guard(tmp_path, root=root)
    record = register(guard)
    guard._tick_once()
    guard._lane_once()
    # OVERCOUNTED — not killed.
    assert record.state == STATE_OVERCOUNTED
    assert guard.counters_snapshot()["ram_kills"] == 0
    assert guard.counters_snapshot()["ram_overcounted"] == 1
    assert guard.counters_snapshot()["ram_rearms"] == 1
    # ceiling + min(shared_extra = rss - pss, 32 MiB)
    assert record.rearm_threshold_bytes == CEILING_BYTES + (1024 * 1024 - 64 * 1024)
    # The episode's claim token was RELEASED.
    assert record.claim_token is None

    # Now the tree really does exceed the ceiling — and it has grown by
    # more than the 128 MiB growth governor, so the confirm is due.
    (root / str(SYNTHETIC_PGID) / "smaps_rollup").write_text("Pss:\t262144 kB\n")
    (root / str(SYNTHETIC_PGID) / "status").write_text("VmRSS:\t262144 kB\n")
    guard._tick_once()
    assert record.claim_token is not None  # the confirm was claimed
    guard._lane_once()
    assert record.state == STATE_KILLED


def test_the_confirm_is_governed_by_the_growth_after_a_rearm(tmp_path, kill_census):
    """The growth governor ⇒ a confirm does NOT run on every tick."""
    root = write_proc_tree(
        tmp_path / "proc",
        [
            FakeProc(
                pid=SYNTHETIC_PGID,
                pgrp=SYNTHETIC_PGID,
                starttime=LEADER_START,
                rss_kb=1024,
                pss_kb=64,
                environ=b"ENSEMBLE_BASH_TRACKING_ID=t-1\x00",
            )
        ],
    )
    guard, _root = make_guard(tmp_path, root=root)
    record = register(guard)
    guard._tick_once()
    guard._lane_once()
    assert record.state == STATE_OVERCOUNTED
    confirms = guard.counters_snapshot()["ram_overcounted"]
    # The next tick breaches the RE-ARMED threshold? Not yet — the
    # threshold is ceiling + 32 MiB and the sample is only 1 MiB.
    guard._tick_once()
    assert guard.counters_snapshot()["ram_overcounted"] == confirms
    assert record.confirm_count == 1


# ── The release clauses (B21) ──────────────────────────────────────────────


def test_a_claimed_records_release_is_deferred_to_the_lane(tmp_path, kill_census):
    """A release of a CLAIMED record is deferred to the lane."""
    guard, _root = make_guard(tmp_path)
    record = register(guard)
    guard._tick_once()
    assert record.claim_token is not None
    assert guard.release(TRACKING_ID) is False
    assert guard.read_verdict(TRACKING_ID) is not None
    guard._lane_once()
    # The lane published the verdict.  The fake tree is still alive, so
    # the post-kill probe could NOT confirm the group empty ⇒ the lane
    # does not release; the sweep owns it (B21 trigger (iii)).
    assert guard.read_verdict(TRACKING_ID) is not None
    assert guard.read_verdict(TRACKING_ID).state == STATE_KILLED


def test_a_live_watch_is_not_released_by_the_lane(tmp_path, kill_census):
    """A live watch is NEVER ended by a lane exit."""
    guard, _root = make_guard(tmp_path)
    record = register(guard)
    guard._tick_once()
    # The group is still alive ⇒ the release is refused.
    assert guard.release(TRACKING_ID) is False
    assert record.state == STATE_BREACHED


def test_the_sweep_drops_a_dead_entry_and_keeps_a_live_one(tmp_path, kill_census):
    """O10 — a dead tree's entry is dropped; a live-but-unreleased entry stays.

    The sweep is BOUNDED and NON-LETHAL: it never signals, it only
    drops an entry whose whole tree is gone (``group_member_count ==
    0``).  A live-but-unreleased entry is KEPT WATCHING — the safe
    direction: the runaway stays guarded.
    """
    stamp = b"ENSEMBLE_BASH_TRACKING_ID=t-1\x00"
    root = write_proc_tree(
        tmp_path / "proc",
        [
            FakeProc(pid=4242, pgrp=4242, starttime=LEADER_START, rss_kb=1024, pss_kb=512, environ=stamp),
            FakeProc(pid=4342, pgrp=4242, ppid=4242, starttime=LEADER_START + 1, rss_kb=1024, pss_kb=512, environ=stamp),
            FakeProc(pid=5252, pgrp=5252, starttime=LEADER_START, rss_kb=1024, pss_kb=512, environ=stamp),
            FakeProc(pid=5352, pgrp=5252, ppid=5252, starttime=LEADER_START + 1, rss_kb=1024, pss_kb=512, environ=stamp),
        ],
    )
    # A ceiling ABOVE the fake trees ⇒ no breach ⇒ nothing is claimed.
    guard, _root = make_guard(tmp_path, root=root)
    register(guard, tracking_id="t-live", pid=4242, pgid=4242, ceiling_bytes=64 * MIB)
    register(guard, tracking_id="t-dead", pid=5252, pgid=5252, ceiling_bytes=64 * MIB)
    guard._tick_once()
    assert guard.read_verdict("t-live") is not None
    assert guard.read_verdict("t-dead") is not None
    # The second tree dies entirely.
    kill_tree(root, [5252, 5352])
    guard._sweep_every_ticks = 1
    guard._tick_once()
    # The dead tree's entry is dropped (released).
    assert guard.read_verdict("t-dead").state == "RELEASED"
    # The live entry is still watched.
    assert guard.read_verdict("t-live") is not None
    assert guard.read_verdict("t-live").state == STATE_WATCHING


def test_a_refusal_emits_no_event(tmp_path, kill_census, caplog):
    """§17.2 — a genuine REFUSAL emits NO event (but it IS logged).

    The leg is the RECYCLED-PGID shape (P5a) — a real refusal with a
    real claim: the empty-group shape never breaches, so it would
    prove nothing.  The refusal is counted (``ram_kill_refusals_p5``)
    and carries a CONTENT-FREE transition log line (arch §5 / :779) —
    the reason code and the counters only.
    """
    emitted: list[tuple[str, dict]] = []
    guard, root = make_guard(tmp_path, emitter=lambda i, p: emitted.append((i, dict(p))) or True)
    record = register(guard)
    guard._tick_once()
    assert record.claim_token is not None  # the breach was claimed

    # The leader pid is recycled: a NEW process with a different starttime.
    recycled = FakeProc(
        pid=SYNTHETIC_PGID,
        pgrp=SYNTHETIC_PGID,
        ppid=1,
        starttime=LEADER_START + 999,
        rss_kb=1024,
        pss_kb=512,
        environ=b"ENSEMBLE_BASH_TRACKING_ID=t-1\x00",
    )
    (root / str(SYNTHETIC_PGID) / "stat").write_text(stat_line(recycled) + "\n")

    with caplog.at_level(logging.WARNING):
        guard._lane_once()

    # NO event for a refusal.
    assert emitted == []
    counters = guard.counters_snapshot()
    assert counters["ram_kill_refusals_p5"] == 1
    assert counters["ram_kills"] == 0
    assert record.reason == "refused:pgid-recycled"

    # The transition log line is present and CONTENT-FREE: the reason
    # code and the counters only — no argv, no env, no paths, no reprs.
    lines = [r for r in caplog.records if "ram-guard:" in r.getMessage()]
    assert lines, "a refusal must carry a transition log line"
    payload = json.loads(lines[-1].getMessage().split("ram-guard: ", 1)[1])
    assert payload["reason"] == "refused:pgid-recycled"
    assert set(payload) == {
        "reason",
        "state",
        "tracking_id",
        "ram_kill_refusals_p0",
        "ram_kill_refusals_p1",
        "ram_kill_refusals_p2",
        "ram_kill_refusals_p3",
        "ram_kill_refusals_p4",
        "ram_kill_refusals_p5",
        "ram_kill_refusals_p7",
        "ram_kills",
    }
    for key, value in payload.items():
        if key == "reason":
            continue
        assert value is None or isinstance(value, (int, float, bool, str))
    assert "/" not in payload["reason"]
    assert "Traceback" not in payload["reason"]


# ── The death modes (T14 / O11) ────────────────────────────────────────────


def test_a_raising_tick_restarts_and_counts_a_miss_without_logging(tmp_path, caplog):
    """O11 / S17 — the tick body raises ⇒ restart, ``missed_samples++``, NO log."""
    guard, _root = make_guard(tmp_path)
    register(guard)
    calls = {"n": 0}
    original = guard._tick_once

    def _boom():
        calls["n"] += 1
        guard._stop.set()  # stop the restart loop after the first failure
        raise RuntimeError("tick boom")

    guard._tick_once = _boom  # type: ignore[method-assign]
    with caplog.at_level(logging.DEBUG):
        guard._tick_loop()
    assert calls["n"] >= 1
    assert guard.health()["missed_samples"] >= 1
    # The tick path stays SILENT.
    assert [r for r in caplog.records if r.name.startswith("daemon.services.ram_guard")] == []
    guard._tick_once = original  # type: ignore[method-assign]


def test_a_raising_lane_restarts_and_keeps_the_pending_work(tmp_path, monkeypatch, kill_census):
    """§5 — the lane dies ⇒ its restart loop revives it; the pending resumes."""
    guard, _root = make_guard(tmp_path)
    register(guard)
    guard._tick_once()
    assert len(guard._pending) == 1
    calls = {"n": 0}
    original = guard._lane_once

    def _boom():
        calls["n"] += 1
        guard._stop.set()  # stop the restart loop after the first failure
        raise RuntimeError("lane boom")

    guard._lane_once = _boom  # type: ignore[method-assign]
    guard._lane_loop()
    assert calls["n"] >= 1
    # The pending breach is still there — the lane resumes on revival.
    assert len(guard._pending) == 1
    guard._lane_once = original  # type: ignore[method-assign]
    guard._lane_once()
    assert guard._pending == []


def test_the_lane_heartbeat_is_the_wedge_signal(tmp_path, kill_census):
    """The lane's progress heartbeat + the pending count are the signals."""
    guard, _root = make_guard(tmp_path)
    register(guard)
    guard._tick_once()
    before = guard.health()["last_lane_progress_at"]
    assert before is None
    guard._lane_once()
    after = guard.health()["last_lane_progress_at"]
    assert after is not None


# ── The carry-forward rule (§4.1) ──────────────────────────────────────────


def test_the_carry_forward_keeps_an_observed_member(tmp_path, kill_census):
    """A pid that was already observed stays a member (the carry-forward)."""
    root = build_tree(tmp_path / "proc", members=2)
    guard, _root = make_guard(tmp_path, root=root)
    record = register(guard)
    guard._tick_once()
    observed = record.members
    assert set(observed) == {SYNTHETIC_PGID, SYNTHETIC_PGID + 100, SYNTHETIC_PGID + 101}
    # The escapee leaves the group (setsid) but was already observed.
    (root / str(SYNTHETIC_PGID + 100) / "stat").write_text(
        (root / str(SYNTHETIC_PGID + 100) / "stat").read_text().replace(
            f"{SYNTHETIC_PGID} ", "9999 ", 1
        )
    )
    guard._tick_once()
    assert SYNTHETIC_PGID + 100 in record.members


def test_a_recycled_pgid_is_not_readopted(tmp_path, kill_census):
    """P5a — a recycled pgid's NEW members are NOT re-adopted.

    The already-observed members stay (the carry-forward's rule (a) is
    deliberately sticky), but a process that merely JOINS the recycled
    group is never adopted as ours.
    """
    root = build_tree(tmp_path / "proc", members=1)
    guard, _root = make_guard(tmp_path, root=root)
    record = register(guard)
    guard._tick_once()
    assert set(record.members) == {SYNTHETIC_PGID, SYNTHETIC_PGID + 100}
    # The leader pid is recycled: a NEW process with a different starttime.
    recycled = FakeProc(
        pid=SYNTHETIC_PGID,
        pgrp=SYNTHETIC_PGID,
        ppid=1,
        starttime=LEADER_START + 999,
        rss_kb=1024,
        pss_kb=512,
        environ=b"ENSEMBLE_BASH_TRACKING_ID=t-1\x00",
    )
    (root / str(SYNTHETIC_PGID) / "stat").write_text(stat_line(recycled) + "\n")
    # …and a NEW process joins the recycled group.
    write_proc_tree(
        root,
        [
            FakeProc(
                pid=SYNTHETIC_PGID + 200,
                pgrp=SYNTHETIC_PGID,
                ppid=SYNTHETIC_PGID,
                starttime=LEADER_START + 5,
                rss_kb=1024,
                pss_kb=512,
                environ=b"ENSEMBLE_BASH_TRACKING_ID=t-1\x00",
            )
        ],
    )
    guard._tick_once()
    # The carry-forward is deliberately STICKY (rule (a)/(b)): the
    # already-observed members stay.  What P5a protects is the KILL —
    # the recycled pgid can never be signalled.
    guard._lane_once()
    counters = guard.counters_snapshot()
    assert counters["ram_kills"] == 0
    assert counters["ram_kill_refusals_p5"] == 1
    assert record.reason == "refused:pgid-recycled"
    # Ruling A (§18.1): a permanent refusal terminates the KILL DECISION
    # only — the record stays REFUSED-but-watching.
    assert record.state == "REFUSED"


# ── The registration contract (§16.3) ──────────────────────────────────────


def test_an_instance_less_spawn_is_not_registered(tmp_path):
    """§16.3 — an instance-less spawn is NOT registered in v1."""
    guard, _root = make_guard(tmp_path)
    assert register(guard, instance_id=None) is None
    assert guard.health()["registry_depth"] == 0


def test_the_registry_reports_its_depth(tmp_path):
    """``registry_depth`` counts the live entries."""
    guard, _root = make_guard(tmp_path)
    register(guard)
    register(guard, tracking_id="t-2", pid=SYNTHETIC_PGID + 101)
    assert guard.health()["registry_depth"] == 2


# ── D-5 — a tracking-id collision must not be SILENT ───────────────────────


def test_duplicate_tracking_id_collision_warns_and_overwrites(tmp_path, caplog):
    """D-5 — ``register`` on a LIVE key warns (content-free) and keeps the
    prior semantics (the overwrite; no counter moves — B5 is frozen).

    The warning names the id and the prior state ONLY — never argv, never
    an env value, never a secret.
    """
    import logging

    guard, _root = make_guard(tmp_path)
    first = register(guard)
    assert first is not None
    with caplog.at_level(logging.WARNING, logger="daemon.services.ram_guard.guard"):
        second = register(guard, pid=SYNTHETIC_PGID + 7, pgid=SYNTHETIC_PGID + 7)
    assert second is not None
    # The overwrite semantics are unchanged: the key now maps to the
    # second record.
    assert guard.read_verdict(TRACKING_ID) is second
    # The collision produced exactly one content-free warning.
    warnings = [
        r for r in caplog.records
        if r.levelno == logging.WARNING and "already live" in r.getMessage()
    ]
    assert len(warnings) == 1, [r.getMessage() for r in warnings]
    msg = warnings[0].getMessage()
    assert TRACKING_ID in msg
    assert "prior_state=WATCHING" in msg
    # No counter moved (B5 frozen).
    assert all(v == 0 for v in guard.counters_snapshot().values())


def test_non_colliding_register_stays_silent(tmp_path, caplog):
    """D-5 — a fresh key logs nothing (semantics unchanged)."""
    import logging

    guard, _root = make_guard(tmp_path)
    with caplog.at_level(logging.WARNING, logger="daemon.services.ram_guard.guard"):
        assert register(guard) is not None
    assert [r for r in caplog.records if r.levelno == logging.WARNING] == []
