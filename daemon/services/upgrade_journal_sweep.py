"""Upgrade-journal periodic sweep + executor reaper (v0.15.3 P1 Item 4).

Three steady-state obligations in ONE service (the plan's FM-11 shape —
the reaper queue + worker are OWNED here so the spawn seam in
``daemon/manager.py`` only ever enqueues):

1. **Reconcile sweep** — periodic ``reconcile_pending_op`` ticks. Today the
   pending_op reconciles ONLY at tool entry (upgrade_tools :2027/:2293), so
   a stale armed op starves re-arm for ~20 min (the observed 2026-09-26
   defect). The tick reuses the UNCHANGED :func:`reconcile_pending_op`
   (single source; boot-time call + timer both go through it).

2. **Pending-actions GC** — periodic ``gc_pending_actions(install_dir,
   keep_run_id=None)`` ticks. The nonce registry previously only pruned
   opportunistically on pending_actions writes; a live daemon with no arm
   traffic never swept expired rows.

3. **Executor reaper** — the daemonized executor child is spawned WITHOUT
   any wait (deliberate: it must survive daemon death), so its exit was
   invisible to the daemon. The reaper queue + single worker observe every
   armed executor to its exit and journal ``executor_exit`` (exit code +
   bounded ``data/upgrade.log`` tail) or, on timeout, journal
   ``executor_still_running`` and BENIGN-DETACH (NO kill, NO raise — the
   child leads its own process group via ``start_new_session=True``; the OS
   reaps it eventually; the journal entry is the audit trail).

**Sweep-side liveness guard (R-P1-3/R-P1-7):** before the reconcile tick
may run, an armed op whose owner is an executor is checked with
``os.kill(pid, 0)`` AND a TIME-BOUND heartbeat predicate — pid-existence
alone is NOT load-bearing (PIDs recycle across long-lived daemons); the
evidence must be recent. While the executor is alive-recent the sweep
refrains from clearing (the op tracks a real run).

**ALWAYS ON** (no kill-switch — project owner's HARD POLICY, same as
``JobLockSweepService``); the only knobs are the interval + the reaper
timeout (``ServicesConfig``, pydantic fail-fast at boot). Journal writes
are observability-class: each is wrapped ``except OSError`` → one WARNING
carrying run_id + pid + the exception repr, NO retry, the loop continues
(R-M5-1 — a retry could storm a full disk and mask the I/O fault).

Lifecycle mirrors :class:`~daemon.services.job_lock_sweep.JobLockSweepService`:
``start()`` spawns the asyncio tasks, ``stop()`` cancels + awaits them
(CancelledError is the normal shutdown path — FM-11: never swallow it).
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import logging
import os
import signal
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from daemon.tools import upgrade_journal as uj

if TYPE_CHECKING:
    from daemon.manager import InstanceManager

logger = logging.getLogger(__name__)


# Default cadence — matches the other periodic sweeps (90s). The sweep is a
# janitor, not a hot path: reconcile at tool entry covers the interactive
# case, this closes the no-traffic starvation window.
DEFAULT_UPGRADE_JOURNAL_SWEEP_INTERVAL_SECONDS: int = 90

# Reaper timeout (C2): livez 60 + readyz 120 + soak 300 + overhead ≈ 490s
# observed minimum for a live promote → 660s headroom. Configurable via
# ``ServicesConfig.upgrade_journal_reaper_timeout_seconds`` (``ge=60`` —
# values < 1 minute are nonsensical and fail fast at boot).
DEFAULT_REAPER_TIMEOUT_SECONDS: int = 660

# Time bound for the sweep-side liveness predicate: heartbeat/armed evidence
# older than this no longer blocks the sweep even when the pid exists (PID
# recycling tolerance). Below the promote expiry + reconcile grace
# (600s + 600s), so a genuinely-live executor is protected for its whole
# normal lifetime and reconcile's own guards own the rest.
EXECUTOR_STALENESS_WINDOW_S: int = 900

# Log-tail bounds for the ``executor_exit`` payload.
_LOG_TAIL_MAX_BYTES = 4096
_LOG_TAIL_MAX_LINES = 40


@dataclass(frozen=True)
class ReaperJob:
    """One armed-executor observation (spawn-seam handoff, P1 Item 2)."""

    pid: int
    argv_summary: tuple[str, ...]  # bounded — truncated to 80 chars/item
    install_dir: Path
    run_id: str


class UpgradeJournalSweepService:
    """Periodic upgrade-journal reconcile + pending-actions GC + the
    executor reaper (queue + single worker).

    Args:
        install_dir: the daemon's OWN staged install dir (the self-env
            resolution the tool lane uses). ``None`` (dev repo checkout /
            unresolved env) is valid: the periodic tick becomes a no-op and
            only the reaper stays live.
        reconcile_interval_seconds: sweep cadence (default 90s; pydantic
            ``ge=1`` fail-fast upstream, ``max(1, ...)`` clamp here).
        reaper_timeout_seconds: how long the reaper waits for a child exit
            before benign-detaching (default 660s; ``ge=60`` upstream).
    """

    def __init__(
        self,
        install_dir: Path | None,
        *,
        reconcile_interval_seconds: int = (
            DEFAULT_UPGRADE_JOURNAL_SWEEP_INTERVAL_SECONDS
        ),
        reaper_timeout_seconds: int = DEFAULT_REAPER_TIMEOUT_SECONDS,
        manager: "InstanceManager | None" = None,
    ) -> None:
        """Initialize the sweep service.

        Args:
            install_dir: the daemon's OWN staged install dir (the self-env
                resolution the tool lane uses). ``None`` (dev repo checkout /
                unresolved env) is valid: the periodic tick becomes a no-op and
                only the reaper stays live.
            reconcile_interval_seconds: sweep cadence (default 90s; pydantic
                ``ge=1`` fail-fast upstream, ``max(1, ...)`` clamp here).
            reaper_timeout_seconds: how long the reaper waits for a child exit
                before benign-detaching (default 660s; ``ge=60`` upstream).
            manager: optional ``InstanceManager`` reference (Phase 2 T18, r4 fold
                C2 — the wake sweep needs ``manager.enqueue_message`` and
                ``manager.stamp_user_origin_window`` to deliver wakes). Default
                ``None`` preserves the pre-feature behavior where the wake
                branch is a no-op for dev/unwired installs. The manager is
                stored on ``self._manager``; the api.py:1489-1496 lifespan
                block wires the live ``manager`` singleton at construction
                (one-step, no setter API — invariant: no half-built state).
        """
        self._install_dir = install_dir
        self._interval_seconds = max(1, int(reconcile_interval_seconds))
        # Clamp floor 1 (not 60): the PRODUCTION 60s floor is enforced by
        # pydantic (``ServicesConfig.upgrade_journal_reaper_timeout_seconds
        # Field(ge=60)``) — the constructor kwarg is an internal test seam
        # (TmpImageCleanupService precedent), so tests may pass a tiny
        # timeout to exercise the benign-detach branch in seconds.
        self._reaper_timeout_s = max(1, int(reaper_timeout_seconds))
        # r4 fold C2: the manager seam — wake delivery reads
        # ``self._manager.enqueue_message`` and
        # ``self._manager.stamp_user_origin_window``. ``None`` is the
        # dev-mode no-op (the wake branch short-circuits; the existing
        # reconcile / GC / reaper sub-routines are unaffected).
        self._manager: "InstanceManager | None" = manager
        self._reaper_queue: asyncio.Queue[ReaperJob] = asyncio.Queue()
        self._sweep_task: asyncio.Task[None] | None = None
        self._reaper_task: asyncio.Task[None] | None = None
        # M-1: dedicated executor for the blocking waitpid. ``asyncio.
        # to_thread`` would use the SHARED default executor and strand one
        # thread per hung child daemon-wide; with the dedicated executor
        # the leak is bounded to ``max_workers`` (asyncio's default formula
        # is ``min(32, os.cpu_count()+4)`` — we keep the same shape but
        # cap at 16 so a long-promote storm cannot grow the leak surface
        # indefinitely). The cap is generous vs the plan's "arms are rare
        # and operator-gated" reality — concurrent reaping rarely exceeds
        # a handful — but allows the benign-detach-then-next-job pattern
        # (a single timed-out waitpid must not block the next queued
        # observation). Lazy: created on first ``start()`` so tests that
        # construct but do not start the service do not pay.
        self._waitpid_executor: concurrent.futures.ThreadPoolExecutor | None = None
        self._stopping: bool = False

    # ── introspection ────────────────────────────────────────────────────

    @property
    def interval_seconds(self) -> int:
        """Current sweep interval (seconds). Read-only."""
        return self._interval_seconds

    @property
    def reaper_timeout_seconds(self) -> int:
        """Reaper wait-before-benign-detach (seconds). Read-only."""
        return self._reaper_timeout_s

    # ── lifecycle (JobLockSweepService pattern) ──────────────────────────

    def start(self) -> None:
        """Spawn the periodic sweep + the reaper worker. Idempotent."""
        if (self._sweep_task is not None and not self._sweep_task.done()) or (
            self._reaper_task is not None and not self._reaper_task.done()
        ):
            logger.debug(
                "UpgradeJournalSweepService: start() called while already "
                "running — no-op"
            )
            return
        self._stopping = False
        # M-1: lazily create the dedicated waitpid executor on first start
        # (re-created on every start so a start-after-stop cycle is clean).
        # Match asyncio's default formula (``min(32, cpu+4)``) but cap at
        # 16 to bound the leak surface for hung-children storms.
        if self._waitpid_executor is None:
            self._waitpid_executor = concurrent.futures.ThreadPoolExecutor(
                max_workers=min(16, (os.cpu_count() or 1) + 4),
                thread_name_prefix="UpgradeJournalReaperWaitpid",
            )
        self._sweep_task = asyncio.create_task(
            self._run(), name="UpgradeJournalSweepService"
        )
        self._reaper_task = asyncio.create_task(
            self._reaper_worker(), name="UpgradeJournalSweepReaper"
        )
        logger.info(
            f"UpgradeJournalSweepService started: interval="
            f"{self._interval_seconds}s (default "
            f"{DEFAULT_UPGRADE_JOURNAL_SWEEP_INTERVAL_SECONDS}s), "
            f"reaper_timeout={self._reaper_timeout_s}s (default "
            f"{DEFAULT_REAPER_TIMEOUT_SECONDS}s), install_dir="
            f"{self._install_dir or '<none — dev/unresolved>'}"
        )
        # P1 §5(c) — daemon-boot supervision advisory (ownership-mode
        # commission): ONE journal history event carrying this daemon's
        # classification. Boot-time ONLY — deliberately NO periodic sweep.
        self._emit_supervision_boot_advisory()

    def _emit_supervision_boot_advisory(self) -> None:
        """One advisory ``supervision_boot`` journal event at start().

        Advisory only — never gates boot, never retries, never raises; a
        missing/torn journal (dev repo checkouts have none) is a debug
        line, not a warning. Uses the python twin's compute-once memo
        (``uj.supervision_detect``) — no disk writes in the detector
        itself; this hook is the journal side of the contract.
        """
        if self._install_dir is None:
            return
        try:
            det = uj.supervision_detect()
            # A1 (2026-09-29): NAME the declared×verified outcome — the
            # mapping twin supervision_outcome on the same module
            # (resolved-mode equivalence: det.mode is the declaration
            # POST-ladder, cells agree either way). Additive field in the
            # free-form advisory detail; the ENSEMBLE_SUPERVISION_RESULT
            # machine-line grammar stays frozen (shell side, P2 consumer).
            mapped = uj.supervision_outcome(det.mode, det.state, det.unit)
            detail = (
                f"state={det.state} mode={det.mode} "
                f"unit={det.unit or '<none>'} "
                f"outcome={mapped.outcome}"
            )
            if det.note:
                detail += f' note="{det.note}"'
            uj.journal_history_append(
                self._install_dir, "supervision_boot", detail
            )
            logger.info(
                "UpgradeJournalSweepService: boot supervision advisory — "
                "%s",
                detail,
            )
        except uj.JournalTorn:
            logger.debug(
                "UpgradeJournalSweepService: journal absent/torn — no "
                "supervision boot advisory (normal on dev checkouts)"
            )
        except Exception as exc:  # noqa: BLE001 — advisory, never gates boot
            logger.warning(
                "UpgradeJournalSweepService: supervision boot advisory "
                f"failed: {exc!r} — continuing (advisory only)"
            )

    async def stop(self) -> None:
        """Cancel both tasks and await their cancellation. Safe to call when
        never started (silent no-op)."""
        self._stopping = True
        for attr in ("_reaper_task", "_sweep_task"):
            task: asyncio.Task[None] | None = getattr(self, attr)
            if task is None:
                continue
            setattr(self, attr, None)
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                # Sweep-side cancel — not the worker-side contract. The
                # reaper task is SHUT-DOWN here; CancelledError is the
                # normal shutdown path (FM-11 — the reaper re-raises it
                # and lands HERE, not in a bare except). Exception is
                # defensive against an unexpected shutdown error. Worker
                # paths are a separate concern (see the explicit
                # _TERMINAL_EVENTS / never-swallow contract on the
                # reaper queue side). Either way the service is stopped
                # — no re-raise.
                pass
        # M-1: shut down the dedicated waitpid executor. ``wait=False`` so
        # ``stop()`` is bounded — any in-flight ``os.waitpid`` threads are
        # abandoned (Python cannot interrupt them from outside); the OS
        # reaps the child either way (C2: child leads its own process
        # group). ``cancel_futures=True`` drops any queued-but-not-started
        # work — bounded leak surface is ``max_workers`` per service
        # lifetime, the SHARED default executor stays untouched.
        if self._waitpid_executor is not None:
            self._waitpid_executor.shutdown(wait=False, cancel_futures=True)
            self._waitpid_executor = None
        logger.info("UpgradeJournalSweepService stopped")

    # ── reaper queue (spawn-seam entry point — P1 Item 2 calls this) ─────

    def enqueue_reaper(
        self, pid: int, argv_summary: list[str], install_dir: Path, run_id: str
    ) -> None:
        """Queue one armed executor for exit observation. Bounded
        argv_summary (≤ 80 chars/item); unbounded queue (one job per arm —
        arms are rare and operator-gated)."""
        self._reaper_queue.put_nowait(
            ReaperJob(
                pid=pid,
                argv_summary=tuple(s[:80] for s in argv_summary),
                install_dir=install_dir,
                run_id=run_id,
            )
        )

    # ── Post-Restart Arm-Notify sweep (Phase 2 T1–T9, T14, T15, T17–T19) ─
    #
    # The wake sub-routine is a NEW entry point of the existing sweep. It
    # runs on the SAME guaranteed pre-yield boot pass AND the SAME 90s
    # periodic tick as the existing reconcile. Wake delivery is
    # best-effort, never raises; the sweep is wrapped at TWO levels (per-
    # wake try/except + sweep-level try/except + api.py call-site
    # try/except) so a wake failure cannot abort boot (D-FA5.4).
    #
    # Wake primitive: ``self._manager.enqueue_message(...)`` (NOT a module-
    # level ``manager`` reference — r4 fold C2). The wired attribute is
    # set by the api.py lifespan block (T18); ``None`` preserves the
    # pre-feature behavior where the wake branch is a dev-mode no-op (T5.19).
    #
    # Lifecycle state machine (Phase 1 T1–T5, ADR-039):
    #     pending → delivering → delivered | abandoned
    # * CAS pending → delivering via ``mark_wake_delivering`` (journal lock).
    # * Unconditional delivering → delivered via ``mark_wake_delivered`` —
    #   STRUCTURAL REMOVAL from the dict (the idempotency key).
    # * Unconditional pending → abandoned via ``mark_wake_abandoned`` —
    #   structural removal + ``wake_abandoned`` history event.

    @dataclass(frozen=True)
    class WakeSweepResult:
        """Phase 2 T1 — the per-tick wake sweep outcome.

        Mirrors the small-struct shape of the existing sweep's reconcile
        result; default-constructible (zero counters) so the kill-switch /
        empty-case fast paths can return a clean struct without branches.

        ``delivered`` counts successfully enqueued wakes; ``abandoned``
        counts records transitioned to ``abandoned`` (kill-switch
        abandon-on-switch-off with ``reason=kill_switch_off`` + grace
        expiry with ``reason=grace_expired`` — see T14 / D-FA1.2 /
        ADR-042); ``errors`` increments on every catchable failure
        (the boot-never-wedge contract — never raises; never aborts
        boot). The two abandonment reasons are distinct and the
        grace pass runs BEFORE the kill-switch OFF pass so a
        past-grace record never gets a ``kill_switch_off`` label
        (the grace reason is more specific).
        """

        pending_at_start: int = 0
        pending_at_end: int = 0
        delivered: int = 0
        abandoned: int = 0
        errors: int = 0
        coalesce_overflows: int = 0

    def _is_enabled(self) -> bool:
        """Phase 2 T2 — kill-switch check (``ENSEMBLE_POST_RESTART_ARM_NOTIFY``).

        Default ON; ``=0`` disables the wake branch (no ``enqueue_message``
        calls). Read per tick (NOT cached) so an operator flip takes
        effect on the next tick without a restart. Mirrors the arm-side
        short-circuit in ``upgrade_journal.arm_pending_wake`` (Phase 1 T2).
        """
        return os.environ.get(uj.ARM_NOTIFY_KILL_SWITCH_ENV, "1") != "0"

    def _resolve_wake_targets(
        self,
        pending: list[uj.PendingWake],
        journal: dict[str, Any],
    ) -> list[tuple[uj.PendingWake, str]]:
        """Phase 2 T3 — gate wakes on a terminal-class journal event.

        For each pending wake, calls the lane-agnostic wake-owned
        reader ``uj.wake_terminal_event_after(journal, armed_at)`` (T13,
        ADR-042 addendum r4 fold C1/r5 fold N2 — NO ``run_id`` parameter
        reaches the reader; the reader is a pure mirror of
        ``_terminal_event_after``).

        RESTART-lane ``run_id`` detail-substring tie-break is applied
        HERE, caller-side (r5 fold N2) — this is the only site that
        holds the PendingWake record and therefore its ``kind``. When
        the event-class match yields multiple same-class candidates in
        the ``armed_at`` window AND ``wake.kind == "restart"``, prefer
        the candidate whose detail contains
        ``run_id={wake.run_id}``; promote-lane wakes use pure
        TS-scope (no tie-break). The tie-break NEVER blocks base
        event-class matching (r5 fold N3): a restart terminal whose
        detail prose mismatches (or omits) the ``run_id`` still fires
        the wake — the tie-break only disambiguates when MULTIPLE
        same-class candidates exist in scope.

        Output order matches input order (the Phase 3 coalesce is
        stable). Per-wake JournalTorn is caught here (the wake is held
        pending) so a torn journal doesn't poison the whole batch.
        """
        out: list[tuple[uj.PendingWake, str]] = []
        for wake in pending:
            try:
                outcome = uj.wake_terminal_event_after(journal, wake.armed_at)
            except uj.JournalTorn:
                logger.warning(
                    "UpgradeJournalSweepService: journal torn during "
                    "wake_terminal_event_after run_id=%s — wake held",
                    wake.run_id,
                )
                continue
            except Exception as exc:  # noqa: BLE001 — never wedge
                logger.warning(
                    "UpgradeJournalSweepService: wake_terminal_event_after "
                    "FAILED run_id=%s: %s — wake held",
                    wake.run_id, exc,
                )
                continue
            if outcome is None:
                continue  # no terminal event yet — hold pending
            # Optional RESTART-lane tie-break (caller-side; r5 fold N2):
            # if the wake is restart-kind AND multiple same-class
            # candidates exist in scope, prefer the one whose detail
            # prose contains run_id=<wake.run_id>. The pure-TS-scope
            # base match (above) ALREADY fired — the tie-break only
            # disambiguates WHEN multiple candidates exist (r5 fold N3).
            if (
                wake.kind == "restart"
                and outcome == "restart"
            ):
                outcome = self._rest_lane_tie_break(
                    journal, wake, outcome
                ) or outcome
            out.append((wake, outcome))
        return out

    @staticmethod
    def _rest_lane_tie_break(
        journal: dict[str, Any],
        wake: uj.PendingWake,
        current_outcome: str,
    ) -> str | None:
        """RESTART-lane ``run_id`` detail-substring tie-break (r5 fold N2/N3).

        If the wake is restart-kind AND the candidate ``restart`` event
        has detail prose that DOES NOT contain ``run_id=<wake.run_id>``,
        we STILL keep the match (the tie-break NEVER blocks base
        event-class matching — r5 fold N3). This helper is a no-op
        for the single-candidate case (the typical restart-lane shape
        is one terminal event per restart). It returns ``None`` on the
        rare case where the disambiguation is needed (multiple
        candidates, want to pick the matching ``run_id``); the caller
        keeps the original outcome on ``None``.
        """
        try:
            armed = uj.parse_iso_utc(wake.armed_at)
        except Exception:
            return None
        if armed is None:
            return None
        history = journal.get("history")
        if not isinstance(history, list):
            return None
        candidates: list[dict[str, Any]] = []
        for entry in history:
            if not isinstance(entry, dict):
                continue
            if str(entry.get("event", "")) != "restart":
                continue
            ts = uj.parse_iso_utc(entry.get("ts"))
            if ts is None or ts < armed:
                continue
            candidates.append(entry)
        # No disambiguation needed — single candidate (typical case).
        if len(candidates) <= 1:
            return None
        # Multiple candidates: prefer the one whose detail contains
        # run_id=<wake.run_id>. NEVER blocks base matching (the caller
        # already has a current_outcome; this only refines when
        # ambiguity exists).
        for entry in candidates:
            detail = str(entry.get("detail", ""))
            if f"run_id={wake.run_id}" in detail:
                return "restart"
        return None  # miss — keep the current outcome (don't block)

    def _coalesce_wakes(
        self,
        terminal_wakes: list[tuple[uj.PendingWake, str]],
    ) -> dict[str, tuple[list[uj.PendingWake], list[str]]]:
        """Phase 2 T4 — coalesce by ``arming_instance_id`` with cap.

        Groups by ``arming_instance_id``; per-group, sorts by ``armed_at``
        DESC (newest first); caps each group at
        ``PENDING_WAKE_COALESCE_MAX = 16``. Overflow: the dropped
        records' ``run_id``s accumulate into a separate ``overflow``
        list that the caller journals via ``wake_coalesce_overflow``
        history events.

        Returns a dict keyed by ``arming_instance_id`` — ``{(wakes,
        outcomes)}``. Preserves input order for stability.
        """
        groups: dict[str, list[tuple[uj.PendingWake, str]]] = {}
        for wake, outcome in terminal_wakes:
            groups.setdefault(wake.arming_instance_id, []).append((wake, outcome))
        coalesced: dict[str, tuple[list[uj.PendingWake], list[str]]] = {}
        for instance_id, items in groups.items():
            # Newest first by armed_at; secondary key: run_id (stable).
            sorted_items = sorted(
                items,
                key=lambda pair: (pair[0].armed_at, pair[0].run_id),
                reverse=True,
            )
            cap = uj.PENDING_WAKE_COALESCE_MAX
            kept = sorted_items[:cap]
            coalesced[instance_id] = (
                [w for w, _o in kept],
                [o for _w, o in kept],
            )
            # Overflow is the dropped tail — collected by the caller
            # via the per-group diff (Phase 2 T7 step (j)).
        return coalesced

    def _format_wake_body(
        self,
        wake: uj.PendingWake,
        terminal_outcome: str,
    ) -> str:
        """Phase 2 T5 — single-wake body (D-FA3.2 + D-FA5.2).

        Self-describing pointer: the wake says "call
        ``upgrade_status(run_id=...)`` and report back to the user". The
        LLM in the agent's first turn does the work; no LLM in the
        critical path (invariant 9).
        """
        return (
            f"Post-restart arm-notify. Your armed `{wake.kind}` "
            f"`{wake.run_id}` completed during the daemon downtime. "
            f"Outcome: `{terminal_outcome}`. "
            f"Target: `{wake.target_version or 'n/a'}`. "
            f"Armed at: `{wake.armed_at}`. "
            f"Wake at: `{uj.now_iso()}`. "
            "This is an auto-wake — the daemon restarted, the pipeline "
            "is terminal, and you (the arming instance) are being "
            "notified so you can call "
            f"`upgrade_status(run_id=\"{wake.run_id}\")` and report back "
            "to the user. The report will route to the same channel "
            "where the arm was confirmed."
        )

    def _format_coalesced_wake_body(
        self,
        wakes: list[uj.PendingWake],
        outcomes: list[str],
    ) -> str:
        """Phase 2 T5 — coalesced-wake body (D-FA5.2).

        Newest-first run list; one body for up to
        ``PENDING_WAKE_COALESCE_MAX`` entries (~80 chars per entry ≈
        1.3KB at the cap — well under the 64KB MessageQueue cap).
        """
        head = wakes[0]
        n = len(wakes)
        lines = [
            f"Post-restart arm-notify (coalesced — {n} arms pending for "
            f"this instance). The most recent armed `{head.kind}` "
            f"`{head.run_id}` completed during the daemon downtime.",
            "Outcomes (newest first):",
        ]
        for w, o in zip(wakes, outcomes):
            lines.append(f"  - `{w.run_id}`: `{o}` at `{w.armed_at}`")
        lines.append(
            "This is an auto-wake — call `upgrade_status(run_id=...)` on "
            "each, or `upgrade_status()` with no args to enumerate all."
        )
        return "\n".join(lines)

    def _resolve_fallback_target(
        self,
        arming_instance_id: str,
        project_id: str | None,
    ) -> str | None:
        """Phase 2 T19a — front-door ari fall-back target resolution.

        Private sweep-side helper (r5 R5 no-``manager.ari_lookup``
        method exists). Three properties (per the plan):

        * (i) front-door first — query the instance repository via the
          wired ``self._manager`` seam for an ACTIVE instance of the
          ``ari`` agent in the wake's project. The instance ROW is the
          delivery target (not a directory);
        * (ii) registry chain for agent-level metadata only — the
          ``sources/registry.py:883-902`` chain (metadata.agent_dir →
          agent name → agents.directory + "/ari") resolves agent
          DIRECTORIES, never instance ids — it may inform metadata but
          MUST NOT produce the delivery target;
        * (iii) FAILS LOUD — on absence (zero active ari instances)
          OR ambiguity (multiple active ari instances) the helper
          returns ``None`` AND logs a WARNING naming the project;
          the caller (T19 step (c)) then journals
          ``arm_notify_no_instance`` + marks the wake ``abandoned``
          with ``reason=instance_missing`` — the sweep NEVER silently
          improvises a delivery target.

        No new manager public method (invariant 2; the helper composes
        the existing manager surface reached via the T18 seam).

        Deterministic tie-breaker (ride-along #3): when multiple active
        ari instances are eligible, the one with the MOST RECENT
        ``last_activity_at`` wins (the active conversation is the most
        recent). When no ``last_activity_at`` is set on any candidate
        (rare — fresh ari just created), the lexicographically MAX
        ``instance_id`` wins (stable; documented). On ties (truly equal
        ``last_activity_at``), again ``max(instance_id)``. The choice
        is deterministic AND test-pinned (T5.18 covers it).

        Coverage of the lookup→delivery terminal race (ride-along #4):
        ``send_message`` to a terminal instance revives it (manager
        reuses existing checkpoint + auto-transitions to RUNNING). The
        ari instance the sweep picks at this tick may transition to
        terminal between tick + delivery; the enqueue path handles the
        revive (PAUSED-exempt + auto-RUNNING). If the target row is
        GONE (deleted between tick + delivery), the enqueue raises
        ``KeyError`` (per ``instance_lifecycle.get_instance``:4153);
        the sweep's per-wake ``try/except Exception`` catches it as a
        per-wake failure and the ari-fallback chain does NOT re-enter
        (a missing ari stays missing; the documented recovery is the
        ``arm_notify_no_instance`` history event for the operator).
        """
        if self._manager is None:
            return None
        try:
            repo = getattr(self._manager, "_instance_repository", None)
            if repo is None:
                return None
            get_by_agent_id = getattr(repo, "get_by_agent_id", None)
            if not callable(get_by_agent_id):
                return None
            instances = list(get_by_agent_id("ari") or [])
            if not instances:
                logger.warning(
                    "UpgradeJournalSweepService: ari fall-back: zero "
                    "active ari instances for project=%s — returning None",
                    project_id,
                )
                return None
            # Filter by project_id when the field is set (project-
            # scoping the ari look-up keeps the fall-back on-target).
            if project_id is not None:
                instances = [
                    i for i in instances
                    if getattr(i, "project_id", None) is None
                    or getattr(i, "project_id", None) == project_id
                ]
                if not instances:
                    logger.warning(
                        "UpgradeJournalSweepService: ari fall-back: zero "
                        "active ari instances for project=%s — returning "
                        "None",
                        project_id,
                    )
                    return None
            if len(instances) > 1:
                # Deterministic tie-breaker (ride-along #3): most
                # recently active wins; on ties, max instance_id.
                def _sort_key(inst: Any) -> tuple[str, str]:
                    ts = getattr(inst, "last_activity_at", None)
                    if ts is None:
                        ts_str = ""
                    else:
                        # Use ISO string sortability (the SQLModel writes
                        # ISO strings; we treat any value as str).
                        ts_str = str(ts)
                    iid = str(getattr(inst, "instance_id", "") or "")
                    return (ts_str, iid)

                instances.sort(key=_sort_key)
                # Pick the most-recently-active; break ties on max iid.
                chosen = max(
                    instances,
                    key=lambda inst: (
                        str(getattr(inst, "last_activity_at", "") or ""),
                        str(getattr(inst, "instance_id", "") or ""),
                    ),
                )
                return str(getattr(chosen, "instance_id", "") or "") or None
            only = instances[0]
            return str(getattr(only, "instance_id", "") or "") or None
        except Exception as exc:  # noqa: BLE001 — never wedge
            logger.warning(
                "UpgradeJournalSweepService: _resolve_fallback_target "
                "FAILED for arming_instance_id=%s: %s — returning None",
                arming_instance_id, exc,
            )
            return None

    async def _deliver_wake_ari_fallback(
        self,
        install_dir: Path,
        wake: uj.PendingWake,
        terminal_outcome: str,
        original_body: str,
    ) -> tuple[str | None, int]:
        """Phase 2 T19 — ari fall-back path (one try-block; per-wake).

        (a) ari lookup via ``_resolve_fallback_target`` (T19a) — returns
            the ari ``instance_id`` for the project, or ``None`` on
            absence/ambiguity (FAIL-LOUD, never silent improvise).
        (b) deliver to ari with an annotated body.
        (c) when no ari exists, journal ``arm_notify_no_instance`` +
            mark the wake abandoned with ``reason=instance_missing``.

        Returns ``(message_id, abandoned_count)`` so the caller can
        correctly increment ``result.abandoned``.
        """
        project_id: str | None = None
        if self._manager is not None:
            # Project id is reachable from the wake's arming_instance_id
            # via the instance repo (best-effort; the fall-back is the
            # primary contract, the project-id is for scoping).
            try:
                repo = getattr(self._manager, "_instance_repository", None)
                if repo is not None:
                    get = getattr(repo, "get", None)
                    if callable(get):
                        meta = get(wake.arming_instance_id)
                        if meta is not None:
                            project_id = getattr(meta, "project_id", None)
            except Exception:  # noqa: BLE001 — defensive
                project_id = None
        ari_instance_id = self._resolve_fallback_target(
            wake.arming_instance_id, project_id
        )
        if ari_instance_id is None:
            # T19 step (c): journal + abandon.
            try:
                uj.journal_history_append(
                    install_dir,
                    "arm_notify_no_instance",
                    f"run_id={wake.run_id} arming_instance_id="
                    f"{wake.arming_instance_id} project_id={project_id} "
                    "no ari available",
                )
            except Exception as hist_exc:  # noqa: BLE001 — never wedge
                logger.warning(
                    "UpgradeJournalSweepService: "
                    "arm_notify_no_instance history append FAILED for "
                    "run_id=%s: %s",
                    wake.run_id, hist_exc,
                )
            try:
                uj.mark_wake_abandoned(
                    install_dir, wake.run_id, "instance_missing"
                )
            except Exception as ab_exc:  # noqa: BLE001
                logger.warning(
                    "UpgradeJournalSweepService: mark_wake_abandoned "
                    "FAILED for run_id=%s: %s",
                    wake.run_id, ab_exc,
                )
            return None, 1
        # T19 step (b): deliver to ari with annotated body.
        if self._manager is None:
            return None, 0
        annotated_body = (
            f"(arm-notice: original arming instance not found; reporting "
            f"via ari fall-back) — `run_id={wake.run_id}` "
            "`upgrade_status` pointer — the arming instance row is gone, "
            "this is the project's front-door ari. **No user action "
            "required.**\n\n" + original_body
        )
        try:
            metadata = {
                "system_context": {
                    "kind": "post_restart_arm_notify_ari_fallback",
                    "run_id": wake.run_id,
                    "arm_kind": wake.kind,
                    "terminal_outcome": terminal_outcome,
                    "target_version": wake.target_version,
                    "armed_at": wake.armed_at,
                    "wake_at": uj.now_iso(),
                },
                "delivery": {
                    "channel": "post_restart_arm_notify",
                    "fallback": "ari",
                },
            }
            result = await self._manager.enqueue_message(
                instance_id=ari_instance_id,
                message=annotated_body,
                source=wake.source or "api",
                priority=2,
                metadata=metadata,
            )
            return getattr(result, "message_id", None) or None, 0
        except Exception as exc:  # noqa: BLE001 — never wedge
            logger.warning(
                "UpgradeJournalSweepService: ari fall-back enqueue "
                "FAILED for run_id=%s ari_instance_id=%s: %s — "
                "marking abandoned with reason=instance_missing",
                wake.run_id, ari_instance_id, exc,
            )
            try:
                uj.journal_history_append(
                    install_dir,
                    "arm_notify_no_instance",
                    f"run_id={wake.run_id} arming_instance_id="
                    f"{wake.arming_instance_id} ari_instance_id="
                    f"{ari_instance_id} ari_enqueue_failed={exc}",
                )
                uj.mark_wake_abandoned(
                    install_dir, wake.run_id, "instance_missing"
                )
            except Exception:  # noqa: BLE001
                pass
            return None, 1

    def _grace_pass(
        self,
        install_dir: Path,
        result: "UpgradeJournalSweepService.WakeSweepResult",
    ) -> "UpgradeJournalSweepService.WakeSweepResult":
        """Phase 2 T14 grace-abandonment (D-FA1.2, ADR-042 — review MUST-FIX).

        Iterates ``pending_wakes``; for any record whose ``abandon_after``
        is ``<= now``, calls ``uj.mark_wake_abandoned(install_dir,
        run_id, "grace_expired")`` (structural removal + ONE
        ``wake_abandoned`` history event for forensics).

        Runs BEFORE the kill-switch OFF branch so the OFF pass only
        sees not-yet-graced records: a past-grace record gets the
        more-specific ``reason="grace_expired"`` (the OFF pass would
        have wrongly labeled it ``kill_switch_off``). Records still
        within grace are HELD pending (this method does not touch
        them).

        Per-wake isolation: each ``mark_wake_abandoned`` is wrapped
        individually — a single failure logs WARNING and bumps
        ``errors += 1``, the loop continues (sweep-never-wedge
        discipline, consistent with the existing three-level error
        isolation: per-wake / per-group / sweep-level). The
        ``list_pending_wakes`` read is itself wrapped — a torn or
        unreadable journal surfaces as ``errors += 1`` and the
        grace pass yields no abandonments for that tick.

        ``mark_wake_abandoned`` is idempotent-on-missing (Phase 1 T5):
        if a record is concurrently removed (e.g. kill-switch OFF
        pass, or a delivery) the grace call is a structural no-op
        minus the ``wake_abandoned`` history event. Ordering note:
        a past-grace record can therefore appear in BOTH a grace
        abandon and a kill-switch-off abandon only if the OFF pass
        ran first and the grace pass finds the record already gone
        (no double-abandon is possible because the dict is the
        structural idempotency key).

        Returns the updated ``result`` with ``abandoned`` (and
        optionally ``errors``) incremented. Pure / sync (no
        asyncio — journal writes are in-process and bounded).
        """
        try:
            pending_for_grace = uj.list_pending_wakes(install_dir)
        except Exception as read_exc:  # noqa: BLE001 — never wedge
            logger.warning(
                "UpgradeJournalSweepService: grace pass "
                "list_pending_wakes FAILED: %s — continuing",
                read_exc,
            )
            return self.WakeSweepResult(
                pending_at_start=result.pending_at_start,
                pending_at_end=result.pending_at_end,
                delivered=result.delivered,
                abandoned=result.abandoned,
                errors=result.errors + 1,
                coalesce_overflows=result.coalesce_overflows,
            )
        now_dt = uj.parse_iso_utc(uj.now_iso())
        if now_dt is None:
            # now_iso() is always parseable; defensive only.
            return result
        abandoned_count = 0
        errors_count = 0
        for wake in pending_for_grace:
            abandon_dt = uj.parse_iso_utc(wake.abandon_after)
            if abandon_dt is None:
                # No ``abandon_after`` (defensive — from_json backfills
                # to ``expires_at + PENDING_WAKE_GRACE_S``; a record
                # that predates the field would land here if the
                # backfill also failed). Held pending — log + skip.
                logger.warning(
                    "UpgradeJournalSweepService: grace pass — "
                    "wake run_id=%s abandon_after unparseable; "
                    "held pending",
                    wake.run_id,
                )
                continue
            if now_dt < abandon_dt:
                continue  # within grace — held pending
            try:
                uj.mark_wake_abandoned(
                    install_dir, wake.run_id, "grace_expired"
                )
                abandoned_count += 1
            except Exception as ab_exc:  # noqa: BLE001 — per-wake
                logger.warning(
                    "UpgradeJournalSweepService: grace-abandon "
                    "FAILED run_id=%s: %s — continuing",
                    wake.run_id, ab_exc,
                )
                errors_count += 1
        if abandoned_count or errors_count:
            return self.WakeSweepResult(
                pending_at_start=result.pending_at_start + abandoned_count,
                pending_at_end=result.pending_at_end,
                delivered=result.delivered,
                abandoned=result.abandoned + abandoned_count,
                errors=result.errors + errors_count,
                coalesce_overflows=result.coalesce_overflows,
            )
        return result

    async def sweep_wake_records(self) -> "UpgradeJournalSweepService.WakeSweepResult":
        """Phase 2 T7 — the boot + periodic entry point.

        Order of operations:
          (g0) GRACE PASS — iterate ``pending_wakes`` and abandon any
              record whose ``abandon_after <= now`` with
              ``reason="grace_expired"`` (Phase 2 T14, D-FA1.2,
              ADR-042). Runs BEFORE (a) so the kill-switch OFF pass
              only sees not-yet-graced records. A past-grace record
              therefore gets the more-specific ``grace_expired`` reason
              (never ``kill_switch_off``). Per-wake try/except — a
              single failure bumps ``errors += 1`` and the loop
              continues (sweep-never-wedge).
          (a) _is_enabled → if OFF, run abandon-on-switch-off pass then
              return (Phase 2 T14 — the persisted-record × kill-switch
              semantics). Records present + kill-switch OFF → mark each
              ``abandoned`` with ``reason=kill_switch_off`` (one-time
              pass — already-abandoned/empty dict journals nothing new).
          (b) read install_dir (service field; ``None`` → return clean
              result; install_dir=None is the dev-mode no-op seam).
          (c) try ``journal_read(install_dir)``; on ``JournalTorn`` →
              log + return WakeSweepResult(errors=1).
          (d) ``pending = list_pending_wakes(install_dir)``.
          (e) record ``pending_at_start = len(pending)``.
          (f) ``terminal_wakes = self._resolve_wake_targets(pending,
              journal)``.
          (g) ``coalesced = self._coalesce_wakes(terminal_wakes)``.
          (h) for each (wakes, outcomes) in coalesced.items():
              try-block per group:
                - ``mark_wake_delivering(install_dir, wakes[0].run_id)``
                  (CAS, one per group; the others in the group are
                  marked delivered in the same call's structural sweep
                  after delivery).
                - re-stamp the user-origin window for any wake in the
                  group with a recorded ``source`` (defensive/redundant
                  per ADR-041 — the natural ``manager.py`` stamp is
                  binding for the wake turn that follows the CAS).
                - ``message_id = await
                  self._manager.enqueue_message(instance_id, body,
                  source, priority, metadata)`` (DIRECT call — NOT
                  through the deleted ``_deliver_wake`` helper; the
                  coalesced-body formatter and the group-level
                  structural removal are the parent helper's
                  responsibility).
                - on success, ``mark_wake_delivered(install_dir,
                  wakes[0].run_id, message_id)`` for ALL wakes in the
                  group (single dict-removal pass).
                - on failure, hold the wakes (do not mark abandoned yet
                  — wait for the next tick).
          (i) record ``pending_at_end = len(list_pending_wakes(install_dir))``.
          (j) handle overflow: journal ``wake_coalesce_overflow`` for
              the coalesce-cap drops.
          (k) sweep-level ``try/except Exception`` that logs WARNING and
              returns a result with ``errors += 1`` (D-FA5.4).
        """
        result = self.WakeSweepResult()
        try:
            # (g0) Grace pass — runs BEFORE the kill-switch OFF branch
            # so the OFF pass only sees not-yet-graced records. A
            # past-grace record therefore gets the more-specific
            # ``grace_expired`` reason (never ``kill_switch_off``).
            # Per-wake isolation; structural removal is the
            # idempotency key (Phase 1 T5 — ``mark_wake_abandoned`` is
            # idempotent-on-missing, so no double-abandon is
            # possible across concurrent ticks).
            if self._install_dir is not None:
                result = self._grace_pass(self._install_dir, result)
            # (a) kill-switch gate.
            if not self._is_enabled():
                # Phase 2 T14: abandon-on-switch-off one-time pass.
                try:
                    pending_off = uj.list_pending_wakes(
                        self._install_dir or Path("/__no_install__")
                    ) if self._install_dir is not None else []
                except Exception:  # noqa: BLE001
                    pending_off = []
                abandoned_off = 0
                for w in pending_off:
                    try:
                        uj.mark_wake_abandoned(
                            self._install_dir,  # type: ignore[arg-type]
                            w.run_id,
                            "kill_switch_off",
                        )
                        abandoned_off += 1
                    except Exception as ab_exc:  # noqa: BLE001
                        logger.warning(
                            "UpgradeJournalSweepService: kill-switch "
                            "abandon FAILED run_id=%s: %s",
                            w.run_id, ab_exc,
                        )
                # Preserve the (g0) grace-pass counters — past-grace
                # records were abandoned with ``reason="grace_expired"``
                # BEFORE the OFF pass and the OFF pass must ADD to that
                # total (not overwrite it with the post-grace count).
                result = self.WakeSweepResult(
                    pending_at_start=len(pending_off)
                    + result.abandoned,  # past-grace records already
                                         # counted in the (g0) pass
                    pending_at_end=0,
                    abandoned=result.abandoned + abandoned_off,
                    errors=result.errors,
                )
                return result
            # (b) install_dir seam.
            if self._install_dir is None:
                return result  # clean WakeSweepResult() — dev/unresolved
            install_dir = self._install_dir
            # (c) journal read (torn-safe).
            try:
                journal = uj.journal_read(install_dir)
            except uj.JournalTorn as torn_exc:
                logger.warning(
                    "UpgradeJournalSweepService: journal torn at boot "
                    "sweep: %s — errors=1, continuing (next tick retries)",
                    torn_exc,
                )
                return self.WakeSweepResult(errors=1)
            except OSError as os_exc:
                logger.warning(
                    "UpgradeJournalSweepService: journal read OSError at "
                    "boot sweep: %s — errors=1, continuing",
                    os_exc,
                )
                return self.WakeSweepResult(errors=1)
            # (d) read pending wakes.
            try:
                pending = uj.list_pending_wakes(install_dir)
            except Exception as pend_exc:  # noqa: BLE001
                logger.warning(
                    "UpgradeJournalSweepService: list_pending_wakes "
                    "FAILED: %s — errors=1, continuing",
                    pend_exc,
                )
                return self.WakeSweepResult(errors=1)
            # (e) record start. Preserve the grace-pass counters
            # (the (g0) step may have abandoned records with
            # ``reason="grace_expired"`` already; a fresh
            # ``WakeSweepResult`` here would lose that accounting).
            result = self.WakeSweepResult(
                pending_at_start=len(pending),
                abandoned=result.abandoned,
                errors=result.errors,
            )
            if not pending:
                return result
            # (f) terminal-state gating.
            terminal_wakes = self._resolve_wake_targets(pending, journal)
            if not terminal_wakes:
                # No terminal events yet — record end-state and return.
                # Preserve the (g0) grace-pass counters (a past-grace
                # record may already have been abandoned with
                # ``reason="grace_expired"``; a fresh
                # ``WakeSweepResult`` here would drop that accounting).
                try:
                    result = self.WakeSweepResult(
                        pending_at_start=result.pending_at_start,
                        pending_at_end=len(
                            uj.list_pending_wakes(install_dir)
                        ),
                        abandoned=result.abandoned,
                        errors=result.errors,
                    )
                except Exception:  # noqa: BLE001
                    pass
                return result
            # (g) coalesce by instance.
            coalesced = self._coalesce_wakes(terminal_wakes)
            # Overflow accounting (Phase 2 T4 — the drops past
            # ``PENDING_WAKE_COALESCE_MAX``).
            overflow_total = 0
            try:
                groups_input: dict[str, list[tuple[uj.PendingWake, str]]] = {}
                for w, o in terminal_wakes:
                    groups_input.setdefault(
                        w.arming_instance_id, []
                    ).append((w, o))
                for instance_id, items in groups_input.items():
                    kept = len(coalesced.get(instance_id, ([], []))[0])
                    overflow_total += max(0, len(items) - kept)
                if overflow_total > 0:
                    try:
                        uj.journal_history_append(
                            install_dir,
                            "wake_coalesce_overflow",
                            f"dropped {overflow_total} wake(s) past "
                            f"coalesce cap "
                            f"(cap={uj.PENDING_WAKE_COALESCE_MAX})",
                        )
                    except Exception as ov_exc:  # noqa: BLE001
                        logger.warning(
                            "UpgradeJournalSweepService: "
                            "wake_coalesce_overflow journal FAILED: %s",
                            ov_exc,
                        )
            except Exception as ov_calc_exc:  # noqa: BLE001
                logger.warning(
                    "UpgradeJournalSweepService: coalesce overflow "
                    "calculation FAILED: %s",
                    ov_calc_exc,
                )
            result = self.WakeSweepResult(
                pending_at_start=result.pending_at_start,
                pending_at_end=result.pending_at_start,  # updated below
                delivered=result.delivered,
                abandoned=result.abandoned,
                errors=result.errors,
                coalesce_overflows=overflow_total,
            )
            # (h) per-group delivery.
            delivered_count = 0
            for instance_id, (wakes, outcomes) in coalesced.items():
                # Per-group try-block.
                try:
                    if not wakes:
                        continue
                    head = wakes[0]
                    # CAS pending → delivering (lock-acquired).
                    cas = uj.mark_wake_delivering(install_dir, head.run_id)
                    if cas is None:
                        # CAS-loser / record gone — skip this group.
                        continue
                    # Deliver via the manager (the actual enqueue).
                    body = (
                        self._format_coalesced_wake_body(wakes, outcomes)
                        if len(wakes) > 1
                        else self._format_wake_body(head, outcomes[0])
                    )
                    # Coalesced delivery path: we directly call
                    # enqueue_message because the coalesced body
                    # formatter and the group-level structural removal
                    # are this helper's responsibility.
                    try:
                        if self._manager is None:
                            logger.warning(
                                "UpgradeJournalSweepService: coalesced "
                                "delivery with manager=None — skip group "
                                "instance_id=%s",
                                instance_id,
                            )
                            continue
                        # Re-stamp window if any wake in the group has a
                        # recorded source (defensive/redundant per ADR-041).
                        for w in wakes:
                            if w.source:
                                try:
                                    self._manager.stamp_user_origin_window(
                                        w.arming_instance_id,
                                        source=w.source,
                                        message_id=w.message_id,
                                    )
                                except Exception:  # noqa: BLE001
                                    pass
                                break
                        metadata = {
                            "system_context": {
                                "kind": "post_restart_arm_notify",
                                "run_id": head.run_id,
                                "arm_kind": head.kind,
                                "terminal_outcome": outcomes[0],
                                "target_version": head.target_version,
                                "armed_at": head.armed_at,
                                "wake_at": uj.now_iso(),
                                "coalesced_count": len(wakes),
                            },
                            "delivery": {"channel": "post_restart_arm_notify"},
                        }
                        result_msg = await self._manager.enqueue_message(
                            instance_id=head.arming_instance_id,
                            message=body,
                            source=head.source or "api",
                            priority=2,
                            metadata=metadata,
                        )
                        message_id = getattr(result_msg, "message_id", None) or None
                        # Structural removal: every wake in the group is
                        # marked delivered (single dict-removal pass).
                        if message_id is not None:
                            for w in wakes:
                                try:
                                    uj.mark_wake_delivered(
                                        install_dir, w.run_id, message_id
                                    )
                                except Exception as del_exc:  # noqa: BLE001
                                    logger.warning(
                                        "UpgradeJournalSweepService: "
                                        "mark_wake_delivered FAILED for "
                                        "run_id=%s: %s",
                                        w.run_id, del_exc,
                                    )
                            delivered_count += len(wakes)
                        else:
                            # No message_id — hold the wakes (do not mark
                            # abandoned yet; wait for the next tick).
                            logger.warning(
                                "UpgradeJournalSweepService: enqueue_message "
                                "returned no message_id for run_id=%s — "
                                "wake held pending next tick",
                                head.run_id,
                            )
                    except KeyError:
                        # Instance gone mid-tick — single-wake fall-back
                        # path: T19 ari fall-back for the head wake.
                        fb, abandoned_count = await self._deliver_wake_ari_fallback(
                            install_dir, head, outcomes[0], body
                        )
                        if fb is None:
                            # Fall-back failed — the head wake is already
                            # abandoned by _deliver_wake_ari_fallback.
                            # For the other wakes in the group, mark them
                            # abandoned (the structural sweep).
                            for w in wakes[1:]:
                                try:
                                    uj.mark_wake_abandoned(
                                        install_dir,
                                        w.run_id,
                                        "instance_missing",
                                    )
                                    abandoned_count += 1
                                except Exception:  # noqa: BLE001
                                    pass
                            result = self.WakeSweepResult(
                                pending_at_start=result.pending_at_start,
                                pending_at_end=result.pending_at_end,
                                delivered=result.delivered,
                                abandoned=result.abandoned + abandoned_count,
                                errors=result.errors,
                                coalesce_overflows=result.coalesce_overflows,
                            )
                        else:
                            for w in wakes:
                                try:
                                    uj.mark_wake_delivered(
                                        install_dir, w.run_id, fb
                                    )
                                except Exception:  # noqa: BLE001
                                    pass
                            delivered_count += len(wakes)
                    except Exception as enq_exc:  # noqa: BLE001
                        logger.warning(
                            "UpgradeJournalSweepService: enqueue_message "
                            "FAILED for run_id=%s: %s — per-wake "
                            "try/except continues; wakes held pending "
                            "next tick",
                            head.run_id, enq_exc,
                        )
                        result = self.WakeSweepResult(
                            pending_at_start=result.pending_at_start,
                            pending_at_end=result.pending_at_end,
                            delivered=result.delivered,
                            abandoned=result.abandoned,
                            errors=result.errors + 1,
                            coalesce_overflows=result.coalesce_overflows,
                        )
                except Exception as group_exc:  # noqa: BLE001
                    # Per-group exception — increment errors, move on.
                    logger.warning(
                        "UpgradeJournalSweepService: group delivery "
                        "FAILED instance_id=%s: %s — continuing",
                        instance_id, group_exc,
                    )
                    result = self.WakeSweepResult(
                        pending_at_start=result.pending_at_start,
                        pending_at_end=result.pending_at_end,
                        delivered=result.delivered,
                        abandoned=result.abandoned,
                        errors=result.errors + 1,
                        coalesce_overflows=result.coalesce_overflows,
                    )
            # (i) record end-state.
            try:
                end_count = len(uj.list_pending_wakes(install_dir))
            except Exception:  # noqa: BLE001
                end_count = result.pending_at_start - delivered_count
            result = self.WakeSweepResult(
                pending_at_start=result.pending_at_start,
                pending_at_end=max(0, end_count),
                delivered=delivered_count,
                abandoned=result.abandoned,
                errors=result.errors,
                coalesce_overflows=result.coalesce_overflows,
            )
            return result
        except Exception as sweep_exc:  # noqa: BLE001
            # (k) sweep-level catch — log WARNING, return clean result.
            logger.warning(
                "UpgradeJournalSweepService: sweep_wake_records FAILED: %s",
                sweep_exc,
            )
            return self.WakeSweepResult(
                pending_at_start=result.pending_at_start,
                pending_at_end=result.pending_at_end,
                delivered=result.delivered,
                abandoned=result.abandoned,
                errors=result.errors + 1,
                coalesce_overflows=result.coalesce_overflows,
            )

    # ── periodic sweep ───────────────────────────────────────────────────

    async def sweep_once(self) -> dict[str, int | str | None]:
        """One reconcile + GC + wake-sweep tick. Returns a small visibility
        dict: ``{"reconcile": <note|None>, "gc_pruned": <int>,
        "skipped": <0|1>, "wake": <WakeSweepResult>}``. Never raises — a
        failed tick logs and returns; the next tick retries by simply
        happening."""
        result: dict[str, int | str | None] = {
            "reconcile": None,
            "gc_pruned": 0,
            "skipped": 0,
            "wake": self.WakeSweepResult(),
        }
        if self._install_dir is None:
            return result
        try:
            op = uj.read_pending_op(self._install_dir)
            if op is not None and self._executor_alive_recent(op):
                # R-P1-3/R-P1-7: the op tracks a LIVE-recent executor — do
                # not clear while it runs (promote.shells can legitimately
                # run ~490s+; benign-detached reapers leave the pid alive).
                result["skipped"] = 1
                logger.debug(
                    "UpgradeJournalSweepService: skipping reconcile for "
                    "run_id=%s — executor pid=%s alive-recent",
                    op.run_id, op.owner_pid,
                )
                return result
        except Exception as guard_err:  # noqa: BLE001 — janitor, never gates
            logger.warning(
                "UpgradeJournalSweepService: liveness guard failed: "
                f"{type(guard_err).__name__}: {guard_err} — running "
                "reconcile anyway"
            )
        try:
            note = uj.reconcile_pending_op(self._install_dir)
            if note:
                logger.info(
                    "UpgradeJournalSweepService: reconcile — %s", note
                )
            result["reconcile"] = note
        except Exception as rec_err:  # noqa: BLE001
            logger.warning(
                "UpgradeJournalSweepService: reconcile tick failed: "
                f"{type(rec_err).__name__}: {rec_err} — next tick will retry"
            )
        try:
            pruned = uj.gc_pending_actions(self._install_dir, keep_run_id=None)
            if pruned:
                logger.info(
                    "UpgradeJournalSweepService: pending_actions GC pruned "
                    "%d expired/consumed entr(y|ies)",
                    pruned,
                )
            result["gc_pruned"] = pruned
        except Exception as gc_err:  # noqa: BLE001
            logger.warning(
                "UpgradeJournalSweepService: pending_actions GC tick failed: "
                f"{type(gc_err).__name__}: {gc_err} — next tick will retry"
            )
        # Phase 2 T8 — wake sweep on the same periodic tick.
        try:
            wake_result = await self.sweep_wake_records()
            result["wake"] = wake_result
        except Exception as wake_err:  # noqa: BLE001 — never wedge boot
            logger.warning(
                "UpgradeJournalSweepService: wake sweep tick failed: "
                f"{type(wake_err).__name__}: {wake_err} — next tick "
                "will retry"
            )
        return result

    def _executor_alive_recent(self, op: uj.PendingOp) -> bool:
        """TIME-BOUND liveness predicate (NOT bare pid-existence): the owner
        is an executor, its pid still exists, AND the heartbeat evidence is
        recent. Any failure → False (the sweep may proceed; reconcile's own
        guards still apply)."""
        if op.owner_kind != "executor" or op.owner_pid <= 0:
            return False
        try:
            os.kill(op.owner_pid, 0)
        except OSError:
            return False  # pid gone — the executor is dead; sweep may clear
        evidence = op.owner_heartbeat_at or op.armed_at
        stamp = uj.parse_iso_utc(evidence)
        if stamp is None:
            # Unparseable evidence + existing pid → treat as alive-recent
            # (fail-closed: never sweep a maybe-live executor).
            return True
        age_s = (datetime.now(tz=timezone.utc) - stamp).total_seconds()
        return age_s <= EXECUTOR_STALENESS_WINDOW_S

    async def _run(self) -> None:
        """Periodic tick loop — exits on ``stop()`` cancellation."""
        try:
            while not self._stopping:
                await self.sweep_once()
                await asyncio.sleep(self._interval_seconds)
        except asyncio.CancelledError:
            # Normal shutdown path via stop() — exit cleanly.
            return
        except Exception as loop_err:  # noqa: BLE001
            logger.error(
                "UpgradeJournalSweepService: unexpected loop error: "
                f"{type(loop_err).__name__}: {loop_err} — exiting",
                exc_info=True,
            )
            return

    # ── reaper worker ────────────────────────────────────────────────────

    async def _reaper_worker(self) -> None:
        """Consume the reaper queue forever: await the child exit (bounded),
        journal the outcome, continue. Benign-detach on timeout (C2): NO
        kill, NO raise — the child keeps its own process group and the OS
        reaps it; the journal entry is the operator's audit trail.

        comp5 (r-20260928-005506-f82e): the journal ``executor_exit``
        detail MUST surface the SIGNAL NAME when the executor was
        killed by a signal (r-f82e's exit code 143 = SIGTERM, but a
        bare ``143`` reads as "exited 143" — the operator has to know
        that 128+N = SIG(N); a signal attribution line removes that
        lookup). Implemented via ``os.WIFSIGNALED``/``os.WTERMSIG``
        inside ``_reaper_worker`` (the worker thread runs the raw
        waitpid; the result flows back into ``_journal_executor_exit``
        AFTER ``_waitpid_blocking`` returns the raw status).
        """
        while True:
            job = await self._reaper_queue.get()
            try:
                # The blocking waitpid runs in a thread; the shield keeps a
                # stray outer cancellation from half-tearing the wait
                # (manager.py's bounded-wait pattern — FM-11 compliant).
                #
                # M-1: run on the service-owned DEDICATED executor (not
                # ``asyncio.to_thread`` — that uses the SHARED default and
                # strands one thread per hung child daemon-wide). ``None``
                # for the default executor is precisely the bug being
                # replaced. ``start()`` guarantees the executor exists by
                # the time this coroutine runs.
                assert self._waitpid_executor is not None, (
                    "_reaper_worker started without a waitpid executor — "
                    "start() must precede worker scheduling"
                )
                status = await asyncio.wait_for(
                    asyncio.shield(
                        asyncio.get_running_loop().run_in_executor(
                            self._waitpid_executor,
                            self._waitpid_blocking,
                            job.pid,
                        )
                    ),
                    timeout=self._reaper_timeout_s,
                )
                # os.waitstatus_to_exitcode returns:
                #   - low 8 bits for normal exits (0..255)
                #   - negative signal number for signal kills (-15, etc.)
                # For signal kills we want the shell's convention
                # 128+signum so the journal detail reads ``exit_code=143``
                # for SIGTERM — matches the operator's mental model and
                # the shell's $? from a SIGTERM'd process.
                raw_exit_code = os.waitstatus_to_exitcode(status)
                if os.WIFSIGNALED(status):
                    exit_code = 128 + os.WTERMSIG(status)
                else:
                    exit_code = raw_exit_code
                # comp5: capture signal attribution when the child was
                # killed by a signal. ``os.WIFSIGNALED(status)`` returns
                # True iff the child died from an untrapped signal;
                # ``os.WTERMSIG(status)`` returns the signal number.
                # ``signal.Signals(...)`` maps to the canonical name
                # (e.g. SIGTERM, SIGKILL). On non-signal exits the
                # tuple is ``(None, None)`` and the journal surfaces
                # only the exit_code.
                if os.WIFSIGNALED(status):
                    sig_num = os.WTERMSIG(status)
                    try:
                        sig_name = signal.Signals(sig_num).name
                    except ValueError:
                        sig_name = f"SIG{sig_num}"
                    signal_info = (sig_name, sig_num)
                else:
                    signal_info = (None, None)
            except asyncio.CancelledError:
                # FM-11: never swallow — propagate to stop().
                raise
            except asyncio.TimeoutError:
                await self._journal_executor_still_running(
                    job, self._reaper_timeout_s
                )
                continue
            except ChildProcessError:
                # The child died (or was reaped) before we got to it — the
                # exit code is unobservable; journal the gap loudly.
                await self._journal_executor_exit(
                    job, exit_code=-1, signal_name=None, signal_num=None
                )
                continue
            except Exception as exc:  # noqa: BLE001 — one bad job never
                # kills the worker; journal + move on.
                await self._journal_executor_error(job, exc)
                continue
            await self._journal_executor_exit(
                job,
                exit_code=exit_code,
                signal_name=signal_info[0],
                signal_num=signal_info[1],
            )

    @staticmethod
    def _waitpid_blocking(pid: int) -> int:
        """Blocking ``os.waitpid`` — runs in a thread on the service-owned
        DEDICATED executor (M-1, not ``asyncio.to_thread``). Returns the
        raw wait status; the worker decodes signal kills via
        ``os.WIFSIGNALED``/``os.WTERMSIG`` and normal exits via
        ``os.waitstatus_to_exitcode``."""
        _, status = os.waitpid(pid, 0)
        return status

    # ── journal writers (R-M5-1: OSError → WARNING, NO retry, continue) ──

    def _log_tail(self, install_dir: Path) -> str:
        """Last ≤4KB byte-tail, then last ≤40 lines of decoded text of
        ``data/upgrade.log``. seek(-4KB) + truncate-at-newline — never
        an O(n) whole-file read; the file is parent-created before Popen
        so absence just means an empty tail."""
        try:
            log = uj.executor_log_path(install_dir)
            with log.open("rb") as fh:
                fh.seek(0, os.SEEK_END)
                size = fh.tell()
                fh.seek(max(0, size - _LOG_TAIL_MAX_BYTES))
                chunk = fh.read(_LOG_TAIL_MAX_BYTES)
            text = chunk.decode("utf-8", errors="replace")
            if size > _LOG_TAIL_MAX_BYTES:
                nl = text.find("\n")
                if nl != -1:
                    text = text[nl + 1:]  # drop the torn leading partial line
            return "\n".join(text.splitlines()[-_LOG_TAIL_MAX_LINES:])
        except OSError:
            return ""

    async def _journal_executor_exit(
        self,
        job: ReaperJob,
        *,
        exit_code: int,
        signal_name: str | None = None,
        signal_num: int | None = None,
    ) -> None:
        tail = self._log_tail(job.install_dir)
        # comp5 (r-f82e): when the executor was killed by a signal,
        # surface the signal name in the journal detail so the operator
        # reads "executor_exit run_id=… exit_code=143 terminated by
        # SIGTERM (15)" instead of having to recall that 128+N maps to
        # SIG(N). On non-signal exits (exit_code=0, 78, etc.) the
        # detail is the original bare exit_code line — preserves the
        # contract for the common (non-r-f82e) cases.
        detail = (
            f"run_id={job.run_id} pid={job.pid} exit_code={exit_code} "
            f"argv={list(job.argv_summary)} upgrade.log tail:\n{tail}"
        )
        if signal_name is not None and signal_num is not None:
            detail = (
                f"run_id={job.run_id} pid={job.pid} exit_code={exit_code} "
                f"terminated by {signal_name} ({signal_num}) "
                f"argv={list(job.argv_summary)} upgrade.log tail:\n{tail}"
            )
        try:
            uj.journal_history_append(job.install_dir, "executor_exit", detail)
        except OSError as exc:
            logger.warning(
                "UpgradeJournalSweepService: journal_write failed for "
                f"executor_exit (run_id={job.run_id} pid={job.pid} "
                f"exit_code={exit_code}): {exc!r} — no retry, continuing "
                "reaper loop"
            )

    async def _journal_executor_still_running(
        self, job: ReaperJob, timeout_s: int
    ) -> None:
        try:
            uj.journal_history_append(
                job.install_dir,
                "executor_still_running",
                f"run_id={job.run_id} pid={job.pid} still running after "
                f"{timeout_s}s — benign-detach (no kill; own process group; "
                "correlate with data/upgrade.log)",
            )
        except OSError as exc:
            logger.warning(
                "UpgradeJournalSweepService: journal_write failed for "
                f"executor_still_running (run_id={job.run_id} pid={job.pid} "
                f"timeout_s={timeout_s}): {exc!r} — no retry, continuing "
                "reaper loop"
            )

    async def _journal_executor_error(
        self, job: ReaperJob, exc: BaseException
    ) -> None:
        try:
            uj.journal_history_append(
                job.install_dir,
                "executor_error",
                f"run_id={job.run_id} pid={job.pid} reaper error "
                f"{type(exc).__name__}: {exc}",
            )
        except OSError as write_exc:
            logger.warning(
                "UpgradeJournalSweepService: journal_write failed for "
                f"executor_error (run_id={job.run_id} pid={job.pid} "
                f"original_exc={type(exc).__name__}): {write_exc!r} — no "
                "retry, continuing reaper loop"
            )
