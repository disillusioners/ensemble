"""``guard`` — the RAM guard: the TICK thread + the EXECUTION-LANE thread.

Contract: architecture-recommendation.md §4.2 (the ladder contract),
§4.3.1 (the verdict-publication ordering + the conditional releases),
§4.4 (the verdict record), §4.5 (the durable event — the HOOK lives
here), §5 (the failure paths — four death modes), §7 (the module
decomposition + the thread lifecycle), §15 B5 (the counter set),
§15 B16 (the two-thread ruling + the pending max), §15 B17 (the
"blocked" invariant), §15 B21 (the release clauses), §15 B23 (the
heartbeat fields), phase2-plan.md T10 / T13 / T14 / T15 / T23.

**The two threads (arch §4.2 / §15 B16).**

    (b) TWO dedicated daemon threads — ONE TICK THREAD and ONE EXECUTION-LANE THREAD — started/stopped together on the FastAPI lifespan (app.state.ram_guard owns both; OFF ⇒ neither thread exists). The tick thread owns registration, the ONE shared /proc scan, the RSS trigger, the peak/counter updates and the breach handoff; it never reads PSS, never signals, never sleeps beyond the poll. The execution-lane thread is the only executor of the kill path: the one-shot PSS confirm (250 ms deadline, fail-open ⇒ kill), then ram_kill_ladder (TERM → slice-probed grace → KILL → the P8 probe), then the verdict publication and the emitter call. The lane never samples and the tick never kills, so a stalled confirm can never delay a tick.

**The blocked invariant (§15 B17, verbatim):**

    no PSS read and no signal on the tick path — both live on the execution-lane thread

**The phase2 risk-row property (§15 B17, verbatim):**

    The confirm and the kill ladder run on the execution-lane thread, never on the tick thread, so a stalled smaps_rollup read can delay a kill but can never delay a tick, a measurement or the liveness sweep.

**The release clauses (§15 B21, verbatim):**

    A release of a CLAIMED record is deferred to the lane (the lane releases after publishing).
    A live watch is never ended by a lane exit; the liveness sweep owns the release at group_member_count == 0.
    The claim token is EPISODE-scoped: taken at observation, released when the episode resolves (KILLED, OVERCOUNTED re-arm, or REFUSED); OVERCOUNTED publishes no verdict — the re-armed threshold IS the episode's recorded outcome.
    The release triggers: (i) group_member_count == 0 (the tick-thread sweep); (ii) terminal resolution with an empty group (the lane); (iii) the post-kill probe confirming the group empty on the RAM path (the lane).

**The canonical Q2 predicate strings (§15 B6, verbatim).**  They are
the rule this module implements:

    the watch stays alive until the group member count reaches 0
    identity = captured pgid ∧ surviving-member group membership
    starttime gate applies to the leader only while alive
    the watch is released only at group member count 0
    group_member_count == 0
"""

from __future__ import annotations

import json
import logging
import threading
import time
from collections import OrderedDict
from pathlib import Path
from typing import Any, Optional

from .ceiling import resolve_ceiling_bytes
from .errors import build_ram_event_payload
from .events import RamBreachEmitter
from .ladder import (
    REASON_TREE_INCOMPLETE,
    is_permanent_refusal,
    refusal_bucket,
    ram_kill_ladder,
)
from .tree_reader import (
    PROC_ROOT,
    RAM_GUARD_MAX_TREE_DEPTH,
    RAM_GUARD_MAX_TREE_NODES,
    read_start_time,
    read_tree_pss,
    read_tree_rss,
    resolve_members,
    scan_proc,
)
from .verdict import (
    STATE_OVERCOUNTED,
    STATE_WATCHING,
    TRIGGER_PSS_CONFIRM_UNAVAILABLE,
    TRIGGER_PSS_THRESHOLD,
    TRIGGER_RSS_THRESHOLD,
    SpawnRecord,
    breaches,
    confirm_due,
)

logger = logging.getLogger(__name__)

# ── Module constants (§15 B16 / B23 — NOT knobs) ───────────────────────────

#: The bounded pending set over the registry.  On overflow the NEWEST
#: breach is NOT claimed (its peak and ``ram_breaches_detected`` still
#: update; the state stays WATCHING) and it is re-offered next tick —
#: NO CONFIRM IS EVER SKIPPED, so ``pss_confirm_unavailable`` stays
#: reserved for a genuine confirm failure/timeout and lane congestion
#: can never fire the fairness gate.
RAM_GUARD_PENDING_MAX = 2

#: The liveness sweep's cadence (in ticks).  ~25 ticks at a 200 ms poll
#: is ~5 s.
RAM_GUARD_SWEEP_EVERY_TICKS = 25

#: How many released verdicts stay readable (``read_verdict`` after the
#: release).  Bounded — the registry itself must not retain them (the
#: guard's registry has no entry for a released pid).
RAM_GUARD_RELEASED_CACHE_MAX = 256

#: The counter set (§15 B5 — ordered, exact).
COUNTER_KEYS: tuple[str, ...] = (
    "ram_kills",
    "ram_breaches_detected",
    "ram_kill_refusals_p0",
    "ram_kill_refusals_p1",
    "ram_kill_refusals_p2",
    "ram_kill_refusals_p3",
    "ram_kill_refusals_p4",
    "ram_kill_refusals_p5",
    "ram_kill_refusals_p6",
    "ram_kill_refusals_p7",
    "ram_overcounted",
    "ram_rearms",
    "ram_confirm_failures",
    "ram_events_emitted",
    "ram_events_dropped",
    "ownership_unverified",
    "tree_incomplete",
)

#: The eleven-field heartbeat (§15 B23 — ordered, exact).
HEARTBEAT_KEYS: tuple[str, ...] = (
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


class GuardCounters:
    """The counters — the §15 B5 set, exactly."""

    def __init__(self) -> None:
        for key in COUNTER_KEYS:
            setattr(self, key, 0)

    def snapshot(self) -> dict[str, int]:
        """The ordered counter snapshot."""
        return {key: int(getattr(self, key)) for key in COUNTER_KEYS}

    def bump(self, key: str, delta: int = 1) -> None:
        """Increment one counter (the caller holds the guard lock)."""
        if key not in COUNTER_KEYS:
            raise KeyError(f"unknown ram-guard counter: {key}")
        setattr(self, key, int(getattr(self, key)) + delta)


class RamGuard:
    """The RAM guard — the registry, the two threads and the kill path.

    The guard is constructed ONCE per daemon lifetime; the ceiling
    resolution is a ONE BOOT-TIME SNAPSHOT (§4.7b): the resolved
    ceiling and its source are stable for the daemon's lifetime and a
    restart re-derives.
    """

    def __init__(
        self,
        *,
        enabled: bool = True,
        ceiling_mib: Optional[int] = None,
        resolved_ceiling: Optional[tuple[int, str]] = None,
        poll_interval_ms: int = 200,
        grace_ms: int = 250,
        pss_confirm_deadline_ms: int = 250,
        proc_root: Path = PROC_ROOT,
        cgroup_root: Path = Path("/sys/fs/cgroup"),
        emitter: Optional[RamBreachEmitter] = None,
        platform: Optional[str] = None,
        lane_poll_seconds: float = 0.01,
        sweep_every_ticks: int = RAM_GUARD_SWEEP_EVERY_TICKS,
    ) -> None:
        self._enabled = bool(enabled)
        self._poll_interval_ms = int(poll_interval_ms)
        self._poll_interval_seconds = max(self._poll_interval_ms, 1) / 1000.0
        self._grace_seconds = max(int(grace_ms), 0) / 1000.0
        self._pss_confirm_deadline_ms = int(pss_confirm_deadline_ms)
        self._proc_root = Path(proc_root)
        self._cgroup_root = Path(cgroup_root)
        self._emitter = emitter
        self._platform = platform
        self._lane_poll_seconds = float(lane_poll_seconds)
        self._sweep_every_ticks = int(sweep_every_ticks)

        if resolved_ceiling is not None:
            self._resolved_ceiling_bytes, self._ceiling_source = resolved_ceiling
        else:
            # The boot-time snapshot.  NEVER raises — a failed detector
            # yields the fixed fallback + one WARN (§4.7f).
            self._resolved_ceiling_bytes, self._ceiling_source = resolve_ceiling_bytes(
                self._proc_root,
                self._cgroup_root,
                ceiling_mib=ceiling_mib,
            )

        self._lock = threading.RLock()
        self._registry: dict[str, SpawnRecord] = {}
        self._released: "OrderedDict[str, SpawnRecord]" = OrderedDict()
        self._pending: list[tuple[str, int]] = []
        self._counters = GuardCounters()

        self._next_claim_token = 0
        self._ticks = 0
        self._missed_samples = 0
        self._lane_failures = 0
        self._last_tick_at: Optional[float] = None
        self._last_lane_progress_at: Optional[float] = None

        self._stop = threading.Event()
        self._tick_thread: Optional[threading.Thread] = None
        self._lane_thread: Optional[threading.Thread] = None

    # ── Read surfaces ──────────────────────────────────────────────────────

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def resolved_ceiling_bytes(self) -> int:
        return self._resolved_ceiling_bytes

    @property
    def ceiling_source(self) -> str:
        return self._ceiling_source

    @property
    def grace_seconds(self) -> float:
        """The configured kill grace, in seconds (the PUBLIC read surface).

        Additive and read-only: the guard's own service-worst-case
        inputs (the confirm deadline + the grace) are what a CONSUMER's
        bounded wait must be derived from (arch §15 B20), and they used
        to be reachable only as private attributes — a private read is a
        silent-revert seam (a rename would quietly re-derive the
        deadline).  The accessors are the seam; the consumer pins them.
        """
        return self._grace_seconds

    @property
    def pss_confirm_deadline_ms(self) -> int:
        """The configured PSS-confirm deadline, in ms (the PUBLIC read surface)."""
        return self._pss_confirm_deadline_ms

    @property
    def counters(self) -> GuardCounters:
        return self._counters

    def counters_snapshot(self) -> dict[str, int]:
        """The ordered counter snapshot (§15 B5)."""
        with self._lock:
            return self._counters.snapshot()

    def health(self) -> dict[str, Any]:
        """The eleven-field heartbeat (§15 B23, ordered, exact).

        ``thread_alive`` / ``lane_alive`` are the threads' ``is_alive()``
        — they report a WEDGED thread green.  The wedge signals are
        ``last_lane_progress_at`` (frozen) and ``lane_pending``
        (climbing) (§5).
        """
        with self._lock:
            payload: dict[str, Any] = {
                "enabled": self._enabled,
                "thread_alive": bool(self._tick_thread is not None and self._tick_thread.is_alive()),
                "last_tick_at": self._last_tick_at,
                "missed_samples": self._missed_samples,
                "ticks": self._ticks,
                "registry_depth": len(self._registry),
                "lane_alive": bool(self._lane_thread is not None and self._lane_thread.is_alive()),
                "lane_pending": len(self._pending),
                "last_lane_progress_at": self._last_lane_progress_at,
                "resolved_ceiling_bytes": self._resolved_ceiling_bytes,
                "ceiling_source": self._ceiling_source,
            }
        assert tuple(payload.keys()) == HEARTBEAT_KEYS, "heartbeat key set drifted"
        return payload

    # ── Registry API ───────────────────────────────────────────────────────

    def register(
        self,
        *,
        tracking_id: str,
        instance_id: Optional[str],
        lane: str,
        pid: int,
        pgid: int,
        start_time_jiffies: Optional[int],
        ceiling_bytes: Optional[int] = None,
        poll_interval_ms: Optional[int] = None,
        grace_ms: Optional[int] = None,
    ) -> Optional[SpawnRecord]:
        """Register a watched spawn → its :class:`SpawnRecord`.

        ``instance_id=None`` ⇒ **NOT registered** (§16.3 — the narrow
        claim: an instance-less spawn is invisible to the guard in v1
        and only the unit cap covers it).  ``ceiling_bytes`` is the
        runtime-injection parameter (arch §2 DQ7); the RESOLVED ceiling
        is the default.
        """
        if not self._enabled:
            return None
        if not instance_id:
            return None
        record = SpawnRecord(
            tracking_id=tracking_id,
            instance_id=instance_id,
            lane=lane,
            pid=int(pid),
            pgid=int(pgid),
            start_time_jiffies=start_time_jiffies,
            ceiling_bytes=int(ceiling_bytes if ceiling_bytes is not None else self._resolved_ceiling_bytes),
            poll_interval_ms=int(poll_interval_ms if poll_interval_ms is not None else self._poll_interval_ms),
            grace_ms=int(grace_ms if grace_ms is not None else int(self._grace_seconds * 1000)),
        )
        with self._lock:
            self._registry[tracking_id] = record
        return record

    def read_verdict(self, tracking_id: str) -> Optional[SpawnRecord]:
        """The verdict record for ``tracking_id`` (live or recently released)."""
        with self._lock:
            record = self._registry.get(tracking_id)
            if record is not None:
                return record
            return self._released.get(tracking_id)

    def release(self, tracking_id: str, *, force: bool = False) -> bool:
        """The guard-side release API.

        A release of a **CLAIMED** record is DEFERRED TO THE LANE (the
        lane releases after publishing), and a release of a record whose
        WATCH IS STILL LIVE is refused — the liveness sweep owns the
        release at ``group_member_count == 0``.  ``force=True`` is the
        LANE's own terminal path (and the sweep's).
        """
        with self._lock:
            record = self._registry.get(tracking_id)
            if record is None:
                return False
            if not force:
                if record.claim_token is not None:
                    return False
                if record.group_member_count > 0:
                    return False
            self._registry.pop(tracking_id, None)
            record.mark_released()
            self._remember_released(record)
            return True

    def _remember_released(self, record: SpawnRecord) -> None:
        self._released[record.tracking_id] = record
        self._released.move_to_end(record.tracking_id)
        while len(self._released) > RAM_GUARD_RELEASED_CACHE_MAX:
            self._released.popitem(last=False)

    # ── Lifecycle ──────────────────────────────────────────────────────────

    def start(self) -> None:
        """Start BOTH threads.  OFF ⇒ NEITHER thread exists."""
        if not self._enabled:
            return
        self._stop.clear()
        self._tick_thread = threading.Thread(
            target=self._tick_loop, name="ram-guard-tick", daemon=True
        )
        self._lane_thread = threading.Thread(
            target=self._lane_loop, name="ram-guard-lane", daemon=True
        )
        self._tick_thread.start()
        self._lane_thread.start()

    def stop(self) -> None:
        """Signal the stop flag on both threads (the join is the caller's)."""
        self._stop.set()

    def join(self, timeout: float = 5.0) -> None:
        """Join both threads with a bounded timeout (never on the loop)."""
        for thread in (self._tick_thread, self._lane_thread):
            if thread is not None and thread.is_alive():
                thread.join(timeout=timeout)

    # ── The tick thread ────────────────────────────────────────────────────

    def _tick_loop(self) -> None:
        """The tick thread's body — its own restart loop, its own try/except.

        The tick path NEVER logs (test-plan S17) — a failure increments
        ``missed_samples`` and the loop restarts the body.
        """
        while not self._stop.is_set():
            try:
                self._tick_once()
                with self._lock:
                    self._missed_samples = 0
            except Exception:
                with self._lock:
                    self._missed_samples += 1
                # A short backoff keeps a permanently-failing tick from
                # spinning hot; the restart is still prompt.
                self._stop.wait(0.05)
                continue
            self._stop.wait(self._poll_interval_seconds)

    def _tick_once(self) -> None:
        """One tick — the ONE shared scan serving ALL live spawns."""
        now = time.time()
        with self._lock:
            self._ticks += 1
            self._last_tick_at = now
            records = list(self._registry.values())
        # The scan is OUTSIDE the lock — it is the expensive part.
        table = scan_proc(self._proc_root)
        for record in records:
            self._sample_record(record, table, now)
        if self._sweep_every_ticks > 0 and self._ticks % self._sweep_every_ticks == 0:
            self._liveness_sweep()

    def _sample_record(self, record: SpawnRecord, table, now: float) -> None:
        """Sample one record — the RSS trigger, the peak, the counters."""
        if not table.scan_complete:
            # ENUMERATION FAILURE — the sample is DROPPED: the peak keeps
            # its value, no breach is evaluated, ``enumeration_failures``
            # climbs.  NEVER kill on no-evidence (test-plan S6 / N3).
            with self._lock:
                record.enumeration_failures += 1
            return

        members, group_count, killable = self._resolve_members(record, table)
        reading = read_tree_rss(self._proc_root, members)

        with self._lock:
            record.members = members
            record.group_member_count = group_count
            record.members_total = len(members)
            record.members_killable = killable
            record.members_unreachable = len(members) - killable
            record.unreadable_pids = reading.unreadable_pids
            record.gone_pids = reading.gone_pids
            record.tree_complete = bool(reading.complete)
            record.metric = reading.metric
            record.trigger_metric = reading.metric
            record.record_sample(reading.total_bytes, now)

            threshold = record.effective_threshold_bytes
            if not breaches(reading.total_bytes, threshold):
                return

            self._counters.bump("ram_breaches_detected")

            if record.state == STATE_OVERCOUNTED:
                # The growth governor gates the re-armed confirm (a
                # confirm does NOT run on every tick).
                if not confirm_due(
                    reading.total_bytes,
                    record.rearm_threshold_bytes,
                    record.last_confirm_rss_bytes,
                ):
                    return
            elif record.state != STATE_WATCHING:
                return

            if len(self._pending) >= RAM_GUARD_PENDING_MAX:
                # The newest breach is NOT claimed and is re-offered next
                # tick — no confirm is ever skipped.
                return

            record.mark_breached(now)
            self._next_claim_token += 1
            record.claim_token = self._next_claim_token
            self._pending.append((record.tracking_id, record.claim_token))

    def _resolve_members(
        self, record: SpawnRecord, table
    ) -> tuple[tuple[int, ...], int, int]:
        """Resolve the record's member set — with the CARRY-FORWARD rule.

        A pid joins the record's member set iff (a) it was already an
        observed member of this record, or (b) it is a bounded
        descendant of an already-observed member, or (c) it shares the
        captured pgid and is not a pgid-leader replacement (P5a).

        Without the carry-forward the re-anchor would re-adopt a
        recycled pgid's members.
        """
        resolved = resolve_members(
            table,
            pgid=record.pgid,
            leader_pid=record.pid,
            max_depth=RAM_GUARD_MAX_TREE_DEPTH,
            max_nodes=RAM_GUARD_MAX_TREE_NODES,
        )
        observed_alive = {pid for pid in record.members if pid in table.entries}
        carry = self._descendant_closure(table, observed_alive)

        leader_replaced = False
        if record.pgid in table.entries and record.start_time_jiffies is not None:
            current = read_start_time(self._proc_root, record.pgid)
            if current is not None and current != record.start_time_jiffies:
                leader_replaced = True

        allowed: set[int] = set(observed_alive)
        for pid in resolved.pids:
            entry = table.entries.get(pid)
            if entry is None:
                continue
            if pid in observed_alive or pid in carry:
                allowed.add(pid)
                continue
            if entry.pgrp == record.pgid and not leader_replaced:
                allowed.add(pid)

        members = tuple(sorted(allowed))
        group_count = sum(
            1 for pid in members if (table.entries.get(pid) is not None and table.entries[pid].pgrp == record.pgid)
        )
        killable = group_count
        return members, group_count, killable

    def _descendant_closure(self, table, seeds) -> set[int]:
        """The bounded descendant closure from the already-observed members."""
        closure: set[int] = set()
        frontier = list(seeds)
        depth = 0
        while frontier and depth < RAM_GUARD_MAX_TREE_DEPTH:
            next_frontier: list[int] = []
            for parent in frontier:
                for child in table.children.get(parent, ()):
                    if child in closure or child in seeds:
                        continue
                    if len(closure) >= RAM_GUARD_MAX_TREE_NODES:
                        return closure
                    closure.add(child)
                    next_frontier.append(child)
            frontier = next_frontier
            depth += 1
        return closure

    # ── The liveness sweep (T15) ───────────────────────────────────────────

    def _liveness_sweep(self) -> None:
        """The bounded, NON-LETHAL sweep — it never signals.

        A dead pid ⇒ the entry is dropped.  A live-but-unreleased entry
        is KEPT WATCHING — the safe direction: the runaway stays
        guarded.
        """
        with self._lock:
            for tracking_id, record in list(self._registry.items()):
                if record.claim_token is not None:
                    # A CLAIMED record's release is deferred to the lane.
                    continue
                if record.samples <= 0:
                    # Never sampled yet — the sweep does not judge it.
                    continue
                if record.group_member_count > 0:
                    continue
                self._registry.pop(tracking_id, None)
                record.mark_released()
                self._remember_released(record)

    # ── The execution-lane thread ──────────────────────────────────────────

    def _lane_loop(self) -> None:
        """The lane's body — its own restart loop, its own try/except.

        The lane is an EXECUTOR, not a supervisor: it never watches the
        tick and the tick never watches it.
        """
        while not self._stop.is_set():
            try:
                self._lane_once()
            except Exception:
                with self._lock:
                    self._lane_failures += 1
                self._stop.wait(0.05)
                continue
            self._stop.wait(self._lane_poll_seconds)

    def _lane_once(self) -> None:
        """Service ONE claimed breach (if any)."""
        with self._lock:
            if not self._pending:
                # The lane is idle — the progress heartbeat still
                # advances (the wedge signal is the heartbeat FROZEN
                # while a service is stuck, §5).
                self._last_lane_progress_at = time.time()
                return
            tracking_id, token = self._pending.pop(0)
        try:
            self._service_claim(tracking_id, token)
        finally:
            with self._lock:
                self._last_lane_progress_at = time.time()

    def _service_claim(self, tracking_id: str, token: int) -> None:
        """The kill path — confirm → ladder → publication → the emitter."""
        with self._lock:
            record = self._registry.get(tracking_id)
            if record is None or record.claim_token != token:
                # The record was released (or re-registered) while the
                # claim was in flight — nothing to service.
                return
            was_overcounted = record.state == STATE_OVERCOUNTED

        # ── 1. the one-shot PSS confirm (the DECIDING value) ──
        reading, confirm_ok, elapsed_ms = self._run_confirm(record)

        if not confirm_ok:
            # FAIL-OPEN ⇒ kill (a confirm failure/timeout never saves
            # the tree).  The deciding value is the RSS peak.
            with self._lock:
                self._counters.bump("ram_confirm_failures")
                record.trigger = TRIGGER_PSS_CONFIRM_UNAVAILABLE
                record.confirmed_pss_bytes = None
            self._run_ladder(record)
            return

        with self._lock:
            record.confirmed_pss_bytes = reading.total_bytes
        if reading.total_bytes < record.ceiling_bytes:
            # The summed RSS over-counted the shared pages — the
            # OVERCOUNTED re-arm.  No verdict, no event: the re-armed
            # threshold IS the episode's recorded outcome.
            shared_extra = max(0, int(record.last_total_bytes) - int(reading.total_bytes))
            with self._lock:
                record.mark_overcounted(
                    now=time.time(),
                    shared_extra_bytes=shared_extra,
                    summed_rss_bytes=int(record.last_total_bytes),
                )
                self._counters.bump("ram_overcounted")
                self._counters.bump("ram_rearms")
                record.claim_token = None
            return

        with self._lock:
            record.trigger = (
                TRIGGER_PSS_THRESHOLD if was_overcounted else TRIGGER_RSS_THRESHOLD
            )
        self._run_ladder(record)

    def _run_confirm(self, record: SpawnRecord):
        """The PSS confirm — the deciding value, under the deadline.

        Returns ``(reading, ok, elapsed_ms)``.  ``ok`` is False on a
        confirm failure, an incomplete reading or a deadline overrun —
        each of which is a FAIL-OPEN ⇒ kill.
        """
        members = record.members
        started = time.monotonic()
        reading = read_tree_pss(self._proc_root, members)
        elapsed_ms = (time.monotonic() - started) * 1000.0
        timed_out = elapsed_ms > self._pss_confirm_deadline_ms
        ok = (not timed_out) and bool(reading.complete)
        return reading, ok, elapsed_ms

    def _run_ladder(self, record: SpawnRecord) -> None:
        """The ladder, the publication and the emitter call."""
        with self._lock:
            tracking_id = record.tracking_id

        status, reason = ram_kill_ladder(
            record.pgid,
            record.start_time_jiffies,
            self._grace_seconds,
            proc_root=self._proc_root,
            tracking_id=record.tracking_id,
            captured_pgid=record.pgid,
            registered=True,
            guard_enabled=self._enabled,
            platform=self._platform,
            on_ownership_unverified=lambda: self._bump("ownership_unverified"),
        )

        now = time.time()
        publish = False
        refusal: Optional[str] = None
        with self._lock:
            if status == "killed":
                record.mark_killed(now, reason)
                self._counters.bump("ram_kills")
                if reason == REASON_TREE_INCOMPLETE:
                    self._counters.bump("tree_incomplete")
                publish = True
                group_empty = reason != REASON_TREE_INCOMPLETE
            elif status == "already-dead":
                # ``already-dead:group-empty`` — the group is empty, the
                # watch is over, nothing was signalled.
                record.reason = reason
                record.mark_released()
                group_empty = True
            else:
                bucket = refusal_bucket(reason)
                if bucket is not None:
                    self._counters.bump(f"ram_kill_refusals_{bucket}")
                permanent = is_permanent_refusal(reason)
                record.mark_refused(reason, permanent=permanent)
                group_empty = permanent
                refusal = reason
            record.claim_token = None
            if group_empty:
                # The terminal resolution with an empty group — the
                # lane releases (B21 trigger (ii)/(iii)).
                if tracking_id in self._registry:
                    self._registry.pop(tracking_id, None)
                    record.mark_released()
                    self._remember_released(record)

        if publish:
            self._emit(record)
        elif refusal is not None:
            # A REFUSAL emits NO event (arch §17.2 — operational, not
            # forensic) but it DOES carry a transition log line
            # (arch §5 / :779: "counters + the transition log line").
            # The line is CONTENT-FREE: the reason code and the
            # counters only — no argv, no env, no paths, no reprs.
            self._log_refusal(record, refusal)

    # ── The event HOOK (T23) ───────────────────────────────────────────────

    def _log_refusal(self, record: SpawnRecord, reason: str) -> None:
        """The REFUSAL transition log line — CONTENT-FREE (arch §5 / :779).

        A refusal emits NO event (§17.2) but it IS logged: the reason
        code (the 16-code enum) and the counters only — the same
        negative-space discipline the envelope and the event payload
        obey (no argv, no env, no stdout/stderr, no secret ids, no
        per-member lists, no host paths, no exception reprs).
        """
        payload = {
            "reason": reason,
            "state": record.state,
            "tracking_id": record.tracking_id,
            "ram_kill_refusals_p0": self._counters.ram_kill_refusals_p0,
            "ram_kill_refusals_p1": self._counters.ram_kill_refusals_p1,
            "ram_kill_refusals_p2": self._counters.ram_kill_refusals_p2,
            "ram_kill_refusals_p3": self._counters.ram_kill_refusals_p3,
            "ram_kill_refusals_p4": self._counters.ram_kill_refusals_p4,
            "ram_kill_refusals_p5": self._counters.ram_kill_refusals_p5,
            "ram_kill_refusals_p6": self._counters.ram_kill_refusals_p6,
            "ram_kill_refusals_p7": self._counters.ram_kill_refusals_p7,
            "ram_kills": self._counters.ram_kills,
        }
        logger.warning("ram-guard: %s", json.dumps(payload, default=str))

    def _emit(self, record: SpawnRecord) -> None:
        """The WATCHING→KILLED emitter call — EXACTLY ONCE, outside the lock.

        The call is wrapped so it can never raise into the lane; a
        raising emitter increments ``ram_events_dropped``.  Refusals
        emit NO event (§17.2) — this method is only reached on a kill.
        """
        if not record.instance_id:
            return  # defensive (arch §4.5)
        try:
            payload = build_ram_event_payload(record)
        except Exception:
            with self._lock:
                self._counters.bump("ram_events_dropped")
            return
        # The transition log line — the allow-listed key set, content-free.
        logger.info("ram-guard: %s", json.dumps(payload, default=str))
        emitter = self._emitter
        if emitter is None:
            return  # no emitter wired ⇒ no event, counter only
        try:
            delivered = bool(emitter(record.instance_id, payload))
        except Exception:
            with self._lock:
                self._counters.bump("ram_events_dropped")
            logger.warning(
                "ram-guard: event emitter raised; the event was DROPPED",
                exc_info=True,
            )
            return
        with self._lock:
            if delivered:
                self._counters.bump("ram_events_emitted")
            else:
                self._counters.bump("ram_events_dropped")

    # ── Counter helper ────────────────────────────────────────────────────

    def _bump(self, key: str) -> None:
        with self._lock:
            self._counters.bump(key)


__all__ = [
    "COUNTER_KEYS",
    "HEARTBEAT_KEYS",
    "RAM_GUARD_PENDING_MAX",
    "RAM_GUARD_SWEEP_EVERY_TICKS",
    "GuardCounters",
    "RamGuard",
]
