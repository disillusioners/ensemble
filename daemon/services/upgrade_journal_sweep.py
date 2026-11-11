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

# Size rationale (~3286 lines, refreshed 2026-11-02 — grew with the
# promote-handback-halt rows: the serving-green stale-txn closer): the
# sweep service
# owns SIX janitor lanes in one loop by design — periodic reconcile/GC
# tick, the two-signal (W4) liveness predicate, the wake-record sweep,
# the bounded reaper (waitpid + signal attribution), the
# agent-restart wake lane (kind-partitioned coalescing, reserved-source
# delivery, §5.3 body render, restart-audit settle, supervision_scope
# classifier), and the serving-green stale-txn closer (the P1-P9
# predicate, the journal lock + under-lock re-validation, and the
# commit-close / verdict-request dispositions). They share the install-dir
# lock + journal-read
# conventions; separating them would re-introduce the tick-ordering
# races the single loop exists to kill. Do NOT split.

from __future__ import annotations

import glob
import asyncio
import concurrent.futures
import logging
import json
import os
import signal
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, NamedTuple

from daemon.tools import upgrade_journal as uj
from daemon.tools.upgrade_journal import (
    RESTART_WAKE_SOURCE,
    SERVING_GREEN_COMMIT_EVENT,
    SWEEP_NO_USER_ORIGIN_WAKE_SOURCE,
)


def _outcome_labels() -> dict[str, str]:
    """Resolve ``upgrade_tools._OUTCOME_LABELS`` once per call, fail-open
    to an empty mapping (hygiene fold — replaces the two verbatim
    lazy-import blocks in the wake-body formatters; the sweep must not
    hard-depend on the tools module at import time)."""
    try:
        from daemon.tools.upgrade_tools import _OUTCOME_LABELS
    except Exception:  # noqa: BLE001 — fail-open render (raw keys)
        return {}
    return _OUTCOME_LABELS

if TYPE_CHECKING:
    from daemon.manager import InstanceManager
    from daemon.services.readiness import ReadinessComposite

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

# ── Phase 2 (promote-handback-halt) — the serving-green stale-txn closer ────
#
# The incident this lane exists for: incident run r-20261101-212542-8279
# (2026-11-01, target v0.28.5) — a promote HALTED at hand-back (Phase 1
# owns that verifier), leaving the txn OPEN with flipped=true. The target
# was SERVING ALL-GREEN. At the next launcher boot the launcher's sweep
# saw a stale flipped txn and AUTO-ROLLED BACK + QUARANTINED the healthy
# release (the OWNER RULING: "Don't roll back if that isn't needed — when
# the new version is working and living, there is no reason to roll
# back"). The launcher sweep is structurally BLIND to serving health (it
# runs PRE-daemon — "port down" is ambiguous: broken release, or THIS
# boot starting the daemon). THIS lane is the daemon-side half of the
# split closer: the daemon CAN observe its own serving health
# in-process, so it disposes of the stale txn on OBSERVED health.

SWEEP_STALE_S: int = 600
"""PROTOCOL MIRROR of the launcher's staleness gate (``launcher.sh:69``):
an ``in_flight`` txn older than this is STALE. The daemon had no
equivalent before this lane — ``EXECUTOR_STALENESS_WINDOW_S`` (above) is
the REAPER liveness predicate (executor liveness, a DIFFERENT question).
The PROTOCOL is the shared contract (the launcher implements it
self-contained: it never sources lib.sh, so the constant is mirrored,
not shared)."""

UPGRADE_CLOSER_BOOT_GRACE_S: int = 300
"""The closer's OWN boot-grace horizon (AM-P2-24 fold (b)).

NOT the borrowed ``READINESS_GRACE_HORIZON_S`` (=120.0,
``daemon/constants.py:165``) — that constant answers a DIFFERENT question
(the readiness service's POOL-BUSY load horizon). 120s cannot cover the
measured 210–360s slow-boot class (MCP warmup + auto-continue resume)
⇒ the warmup observation would land OUTSIDE the grace ⇒ the NOT-GREEN
verdict would fire ⇒ the WRITE-ONCE ``rollback_requested`` would stand
even after the daemon turned green ⇒ tonight's class, reproduced.

DERIVATION (one-way, normative): ``UPGRADE_CLOSER_BOOT_GRACE_S`` ≥
``HANDBACK_VERIFY_BUDGET_S`` (the shell knob, default 300, lib.sh) —
both windows must cover the SAME measured unit-active→serving boot. The
closer's horizon may be RAISED above the hand-back budget; it may NEVER
be lowered below it. The env override can therefore only RAISE it.

DECLARED IN EXACTLY ONE PLACE (AM-P2-24 note (c)): here, beside the other
sweep-local constants — NOT in ``daemon/constants.py`` (that module is
cited only as the site whose pool-busy horizon is NOT borrowed)."""

UPGRADE_CLOSER_BOOT_GRACE_ENV: str = "UPGRADE_CLOSER_BOOT_GRACE_S"
"""Env name of the daemon-side boot-grace horizon (the closer's OWN
window) — read by ``_closer_boot_grace_s``. RAISE-ONLY: the effective
grace may be raised above ``UPGRADE_CLOSER_BOOT_GRACE_S`` (and above the
hand-back budget), never lowered below it (the one-way derivation rule,
AM-P2-24)."""
HANDBACK_VERIFY_BUDGET_ENV: str = "HANDBACK_VERIFY_BUDGET_S"
"""Env name of the shell-side hand-back verify budget (F4) — read by
``_handback_verify_budget_s`` so the closer's grace floor is EFFECTIVE,
not just declared. It is the VERIFY-BUDGET CEILING (how long the
hand-back verifier waits for the unit to serve); the daemon's effective
boot grace is floored at
``max(UPGRADE_CLOSER_BOOT_GRACE_S, HANDBACK_VERIFY_BUDGET_S)``."""

SERVING_GREEN_CLOSER_ENV: str = "ENSEMBLE_UPGRADE_SERVING_GREEN_CLOSER"
"""Daemon-side kill-switch for the serving-green closer lane (REV-R-3).

Read PER TICK (never cached) so an operator can disable the lane without
a rebuild — the per-tick precedent is ``ENSEMBLE_POST_RESTART_ARM_NOTIFY``
(``_post_restart_arm_notify_enabled`` below). ``=0`` ⇒ the lane writes NO
``serving_green_commit``, NO ``serving_unhealthy`` and NO
``rollback_requested``; the stale txn is left untouched. Default ``1``.

THE TWO KILL-SWITCHES OF THIS LANE (REV-R-3 — one sentence, both sides):
the launcher-side ``SWEEP_SERVING_GREEN_ENABLED`` (=0 restores today's
unconditional auto-rollback disposition; =1, the default, installs the new
ladder) and this daemon-side ``ENSEMBLE_UPGRADE_SERVING_GREEN_CLOSER``
(=0 disables the closer lane entirely); BOTH default to 1 (enabled), both
are named in the operator runbook, and the launcher-side switch is the
second enumerated hard ceremony pre-step. ``=0`` is an escape hatch for
an unverified platform — NEVER a permanent state."""

ROLLBACK_REQUESTED_EVENT: str = "rollback_requested"
"""The daemon→launcher rollback-REQUEST event (T14 handoff)."""

SERVING_UNHEALTHY_EVENT: str = "serving_unhealthy"
"""The daemon closer's NOT-GREEN verdict event."""

QUARANTINE_CLEARED_SERVING_GREEN_EVENT: str = "quarantine_cleared_serving_green"
"""The explicit serving-green quarantine-clearance event (owner-ratified
verbatim — OQ-C-3; the amended shell gate's acceptance signal)."""

# Log-tail bounds for the ``executor_exit`` payload.
_LOG_TAIL_MAX_BYTES = 4096
_LOG_TAIL_MAX_LINES = 40


class PerRunObservation(NamedTuple):
    """M6 (tidier, 2026-10-30): the per-run observability record behind the
    ``executor_still_running`` row. ``None`` means ABSENT (rendered as
    ``unknown`` by the row builder); a real ``0`` stays distinguishable."""

    narrative: Path | None
    last_phase: str | None
    elapsed_s: int | None


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
        readiness_provider: 'Callable[[], "ReadinessComposite | None"] | None' = None,
        closer_boot_grace_seconds: int | None = None,
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
            readiness_provider: optional callable returning the daemon's
                CACHED readiness composite (the serving-green closer's
                health read — promote-handback-halt Phase 2 T6). It is a
                PROVIDER, not a captured value: the composite is refreshed
                periodically and a captured value would go stale. ``None``
                ⇒ the closer lane is a no-op (the same posture as
                ``manager=None`` — dev/unwired installs). Wired at the
                SAME lifespan seam as the manager (api.py).
            closer_boot_grace_seconds: INTERNAL TEST SEAM — overrides the
                closer's boot-grace horizon. The OPERATOR surface is the
                env knob ``UPGRADE_CLOSER_BOOT_GRACE_S``, which can only
                RAISE the horizon (the one-way derivation rule: never
                below the hand-back budget's 300s floor). A test that
                must exercise the post-grace verdict cannot wait 300s, so
                the seam exists — the same precedent as
                ``reaper_timeout_seconds`` (a constructor-only seam; the
                production value is unchanged).
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
        # promote-handback-halt Phase 2 T6 — the serving-green closer's
        # health seam. A PROVIDER (callable), never a captured composite:
        # the composite is refreshed on a cadence and a captured value
        # would go stale (the closer would judge a long-dead snapshot).
        # ``None`` ⇒ the closer lane no-ops (dev/unwired installs).
        self._readiness_provider: (
            'Callable[[], "ReadinessComposite | None"] | None'
        ) = readiness_provider
        # Phase 2 — the internal test seam for the boot-grace horizon (see
        # the constructor docstring). ``None`` ⇒ the env/default applies.
        self._closer_boot_grace_override: int | None = (
            int(closer_boot_grace_seconds)
            if closer_boot_grace_seconds is not None
            else None
        )
        # Phase 2 — the closer's OWN boot-grace state (observation-relative
        # arm, never extended, re-armed only for a NEW txn identity).
        self._closer_grace_key: str | None = None
        self._closer_grace_armed_at: datetime | None = None
        self._closer_grace_clamp_logged: bool = False
        # M5 (cycle-2 tidier, COUNCIL-CORRECTED shape) — STATE latch for
        # the closer's per-tick WARNINGs: maps condition-key → "currently
        # failing (already WARNED)". A TRANSITION into failure WARNs
        # exactly once; UNCHANGED repeats drop to DEBUG; a recovery
        # silently clears the key so the NEXT episode WARNs again — see
        # _closer_warn_transition.
        self._closer_warn_active: dict[str, bool] = {}
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
                # agent-restart N3: kind-aware reader — restart-kind wakes
                # match the full WAKE superset; promote-kind wakes match
                # _TERMINAL_EVENTS + ("restart",) ONLY (a stop_forced_kill
                # / restart_gate / restart_abandoned event must never
                # complete a promote wake — T0(b′)).
                outcome = uj.wake_terminal_event_after(
                    journal, wake.armed_at, kind=wake.kind
                )
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

        agent-restart F-1 (§2.5 row (j) — mixed-kind coalescing BLOCKED):
        the partition key is the COMPOSITE ``(arming_instance_id, kind)``.
        A restart-kind wake and a promote-kind wake armed by the SAME
        instance NEVER coalesce into one body/delivery (T0b mixed-group
        pin) — the restart wake's reserved-source routing and
        kind-scoped terminal matching must stay isolated from the
        promote lane's. Returns a dict keyed by
        ``(arming_instance_id, kind)`` — ``{(wakes, outcomes)}``.
        Preserves input order for stability.
        """
        groups: dict[tuple[str, str], list[tuple[uj.PendingWake, str]]] = {}
        for wake, outcome in terminal_wakes:
            groups.setdefault(
                (wake.arming_instance_id, wake.kind), []
            ).append((wake, outcome))
        coalesced: dict[
            tuple[str, str], tuple[list[uj.PendingWake], list[str]]
        ] = {}
        for group_key, items in groups.items():
            # Newest first by armed_at; secondary key: run_id (stable).
            # (F-1: ``group_key`` is the composite ``(instance_id, kind)``
            # tuple — renamed from ``instance_id`` which lied about the
            # shape after the kind partition.)
            sorted_items = sorted(
                items,
                key=lambda pair: (pair[0].armed_at, pair[0].run_id),
                reverse=True,
            )
            cap = uj.PENDING_WAKE_COALESCE_MAX
            kept = sorted_items[:cap]
            coalesced[group_key] = (
                [w for w, _o in kept],
                [o for _w, o in kept],
            )
            # Overflow is the dropped tail — collected by the caller
            # via the per-group diff (Phase 2 T7 step (j)).
        return coalesced

    # ── agent-restart §4(f): DERIVED-scope supervision classifier ────────────

    @staticmethod
    def supervision_scope(pid: int | None = None) -> dict[str, Any]:
        """Derive the daemon's supervision scope + unit for the escape-hatch
        render (agent-restart §4(f) rev-5).

        Discriminators (mirroring lib.sh's classifier INPUT SET exactly —
        the twins-pinned cgroup-leaf parse; drift is unit-tested against
        both slice fixtures):

        * cgroup of the daemon process (``/proc/<pid>/cgroup``, default
          our own pid): a leaf under ``/system.slice/`` ⇒ ``system``
          scope; under ``/user.slice/`` ⇒ ``user`` scope; a
          ``*.service`` leaf yields the unit name.
        * ``SUPERVISION_UNIT_DIR`` override — a directory scanned for
          unit FILES (lib.sh parity); an ``ensemble-*.service`` file
          found there corroborates the unit name.
        * ``SYSTEMCTL_BIN`` override — the binary named in the rendered
          command (default ``systemctl``).

        NEVER a hardcoded scope, NEVER a default ``--user``: an
        unresolvable shape returns ``scope="unknown"`` and the caller
        renders the generic guidance instead of a wrong command. Pure
        reads (no shell-out at render time); never raises.
        """
        out: dict[str, Any] = {
            "scope": "unknown",
            "unit": None,
            "systemctl_bin": os.environ.get("SYSTEMCTL_BIN", "systemctl"),
        }
        try:
            target_pid = pid if pid and pid > 0 else os.getpid()
            leaf: str | None = None
            slice_prefix: str | None = None
            try:
                with open(f"/proc/{target_pid}/cgroup", "r", encoding="utf-8") as fh:
                    for line in fh:
                        parts = line.rstrip("\n").split(":", 2)
                        path = parts[2] if len(parts) == 3 else ""
                        if not path:
                            continue
                        if "/system.slice/" in path + "/":
                            slice_prefix = "system"
                        elif "/user.slice/" in path + "/":
                            slice_prefix = "user"
                        base = path.rsplit("/", 1)[-1]
                        if base.endswith(".service"):
                            leaf = base
            except OSError:
                leaf = None
            unit = leaf
            unit_dir = os.environ.get(
                "SUPERVISION_UNIT_DIR", "/etc/systemd/system"
            )
            if unit is None and unit_dir:
                try:
                    
                    for cand in sorted(
                        glob.glob(os.path.join(unit_dir, "ensemble-*.service"))
                    ):
                        unit = os.path.basename(cand)
                        if slice_prefix is None:
                            slice_prefix = "system"  # unit-file dir = system units
                        break
                except Exception:  # noqa: BLE001 — pure-read, never raises
                    pass
            if slice_prefix is not None:
                out["scope"] = slice_prefix
            out["unit"] = unit
            return out
        except Exception:  # noqa: BLE001 — never raises
            return out

    @staticmethod
    def _escape_hatch_line(scope_info: dict[str, Any]) -> str:
        """§5.3/N-4: the DERIVED-scope escape-hatch line — rendered ONLY on
        failure outcomes, prefixed with the operator-action warning. The
        rev-4 launcher alternative is DROPPED (non-existent command); the
        script re-run is noted arm-intact-only."""
        unit = scope_info.get("unit")
        bin_ = scope_info.get("systemctl_bin", "systemctl")
        scope = scope_info.get("scope", "unknown")
        if scope == "system" and unit:
            cmd = f"sudo {bin_} restart {unit}"
        elif scope == "user" and unit:
            cmd = f"{bin_} --user restart {unit}"
        else:
            cmd = (
                f"<derive scope first: systemctl restart <unit> per this "
                "host's supervision shape — cgroup slice could not be "
                "classified from inside the daemon>"
            )
        return (
            "operator action — do NOT execute from inside the daemon "
            "(terminates the turn, bypasses anti-flap rails); relay to user: "
            f"recovery = `{cmd}` (derived scope: {scope}"
            + (f", unit {unit}" if unit else "")
            + "). The script re-run "
            "`ENSEMBLE_RESTART_LIVE=1 bash <abs-scripts>/upgrade/restart.sh "
            "live --run-id <r-…>` is ARM-INTACT-ONLY (refuses exit 78 once "
            "the op/lock are gone). No launcher alternative exists."
        )

    def _format_wake_body(
        self,
        wake: uj.PendingWake,
        terminal_outcome: str,
        journal: dict[str, Any] | None = None,
        install_dir: "Path | None" = None,
    ) -> str:
        """agent-restart §5.3 — single-wake body (contract contents).

        Outcome (label-rendered) + ``restart_gate`` per-gate detail from
        the journal detail channel · version now live (identity) ·
        applied-override list (V-8: ``None`` boot-resolution ≠ zero-dict)
        · reason echo · provenance class + root source + authorization
        age · in-flight-work disclosure (three-signal JobItem boundary) ·
        escape-hatch line (DERIVED scope ONLY — launcher alternative
        DROPPED; rendered ONLY on failure outcomes, N-4) · run_id. The
        re-run note renders an ABSOLUTE-path TEMPLATE (``<abs-scripts>``
        placeholder) — the concrete ``scripts_dir`` is carried by the
        ``restart_audits[run_id].scripts_dir`` field (N-3; NOT a
        PendingWake field — the wake record carries provenance fields
        only; audit-row consumers read it via ``_audit_row``).
        Fail-open: every sub-read is defensive.
        """
        label = _outcome_labels().get(terminal_outcome, terminal_outcome)
        audit = self._audit_row(journal, wake.run_id)
        gate_detail = self._restart_gate_detail(journal, wake.run_id)
        overrides_frag = self._render_applied_overrides(wake, audit)
        hatch = ""
        if terminal_outcome in ("halt", "restart_gate", "stop_forced_kill"):
            try:
                hatch = self._escape_hatch_line(self.supervision_scope())
            except Exception:  # noqa: BLE001
                hatch = ""
        version_frag = self._live_version_frag(journal, install_dir)
        prov_frag = self._provenance_frag(wake)
        reason = str(audit.get("reason") or "<none recorded>")
        return (
            f"Post-restart arm-notify. Your armed `{wake.kind}` "
            f"`{wake.run_id}` completed during the daemon downtime. "
            f"Outcome: `{label}` ({terminal_outcome}). "
            + (f"Gate detail: {gate_detail}. " if gate_detail else "")
            + f"Version now live: {version_frag} (identity — same binary, no version swap). "
            + f"{overrides_frag} "
            + f"Reason (echo): \"{reason}\". "
            + f"{prov_frag} "
            + "In-flight work at stop: turns resume from checkpoints; a "
            "bare send_message turn is NOT re-dispatched (three-signal "
            "JobItem boundary); mid-tool side effects / bash orphans / "
            "non-checkpointed state are NOT protected. "
            + (hatch + " " if hatch else "")
            + "This is an auto-wake — call "
            f"`upgrade_status(run_id=\"{wake.run_id}\", section='restarts')` "
            "for the audit row and report back to the user. The report "
            "routes to the same channel where the arm was confirmed."
        )

    def _format_coalesced_wake_body(
        self,
        wakes: list[uj.PendingWake],
        outcomes: list[str],
        journal: dict[str, Any] | None = None,
        install_dir: "Path | None" = None,
    ) -> str:
        """agent-restart §5.3 — coalesced-wake body (newest-first run
        list; per-run labels; the HEAD run carries the full §5.3
        detail block)."""
        head = wakes[0]
        n = len(wakes)
        lines = [
            f"Post-restart arm-notify (coalesced — {n} arms pending for "
            f"this instance). The most recent armed `{head.kind}` "
            f"`{head.run_id}` completed during the daemon downtime.",
            "Outcomes (newest first):",
        ]
        labels = _outcome_labels()
        for w, o in zip(wakes, outcomes):
            lines.append(
                f"  - `{w.run_id}`: `{labels.get(o, o)}` "
                f"(`{o}`) at `{w.armed_at}`"
            )
        lines.append(self._format_wake_body(head, outcomes[0], journal, install_dir))
        lines.append(
            "This is an auto-wake — call `upgrade_status(run_id=..., "
            "section='restarts')` on each, or `upgrade_status(section="
            "'restarts')` to enumerate all."
        )
        return "\n".join(lines)

    # ── §5.3 formatter sub-helpers (all defensive / fail-open) ────────────

    @staticmethod
    def _audit_row(journal: dict[str, Any] | None, run_id: str) -> dict[str, Any]:
        try:
            audits = (journal or {}).get("restart_audits")
            row = audits.get(run_id) if isinstance(audits, dict) else None
            return row if isinstance(row, dict) else {}
        except Exception:  # noqa: BLE001
            return {}

    @staticmethod
    def _restart_gate_detail(journal: dict[str, Any] | None, run_id: str) -> str:
        """Per-gate /livez+/readyz results from the journal detail channel
        (§5.3 — the ``restart_gate`` event written alongside the
        terminal carries the per-gate record)."""
        try:
            history = (journal or {}).get("history")
            if not isinstance(history, list):
                return ""
            for entry in reversed(history):
                if not isinstance(entry, dict):
                    continue
                if str(entry.get("event", "")) != "restart_gate":
                    continue
                detail = str(entry.get("detail", ""))
                if run_id in detail:
                    return detail
            return ""
        except Exception:  # noqa: BLE001
            return ""

    @staticmethod
    def _live_version_frag(
        journal: dict[str, Any] | None, install_dir: "Path | None"
    ) -> str:
        """Identity check render: the CURRENT release target from the
        journal (`current` key), degraded to the symlink, else '?'."""
        try:
            cur = (journal or {}).get("current")
            if isinstance(cur, str) and cur:
                return str(cur)
            if install_dir is not None:
                link = install_dir / "current"
                if link.is_symlink():
                    return os.readlink(link).rsplit("/", 1)[-1]
        except Exception:  # noqa: BLE001
            pass
        return "?"

    @staticmethod
    def _render_applied_overrides(
        wake: uj.PendingWake, audit: dict[str, Any]
    ) -> str:
        """§5.2 render — ``key: before→after (applied|not-applied…)``.
        V-8: a ``None`` boot-resolution record renders UNAVAILABLE
        (never a zero-dict claim); per-key status compares the arm-time
        pending set against ``get_last_applied_resolution()``."""
        pending = audit.get("overrides")
        if not isinstance(pending, list) or not pending:
            return "Applied overrides: none were pending at arm time."
        try:
            from daemon.services.settings_service import (
                get_last_applied_resolution,
            )

            resolution = get_last_applied_resolution()
        except Exception:  # noqa: BLE001
            resolution = None
        frags = []
        for o in pending:
            if not isinstance(o, dict):
                continue
            key = str(o.get("key", "?"))
            db_value = o.get("db_value")
            if resolution is None:
                status = "unavailable — boot resolution record absent"
            else:
                rec = resolution.get(key)
                if not isinstance(rec, dict):
                    status = "not-applied (no resolution record)"
                elif rec.get("source") != "db":
                    status = f"not-applied — resolved tier={rec.get('source')}"
                elif rec.get("value") == db_value:
                    status = "applied"
                else:
                    status = "late-write (db value changed after arm)"
            frags.append(
                f"{key}: {o.get('live_before')}→{db_value} ({status})"
            )
        return "Applied overrides: " + "; ".join(frags) + "."

    @staticmethod
    def _provenance_frag(wake: uj.PendingWake) -> str:
        """§5.3: provenance class + root source + authorization age."""
        pclass = getattr(wake, "provenance_class", "") or "none-recorded"
        root = getattr(wake, "provenance_root_source", None) or "?"
        stamped = getattr(wake, "provenance_stamped_at", None) or "?"
        depth = getattr(wake, "provenance_depth", 0) or 0
        return (
            f"Provenance: class={pclass} root={root} depth={depth} "
            f"authorization window stamped_at={stamped} (an accepted arm "
            "can rest on a window up to ~60 min old — the age is the "
            "operator's visibility, not a freshness guarantee)."
        )

    @staticmethod
    def _settle_restart_audit(
        install_dir: "Path",
        wake: uj.PendingWake,
        terminal_outcome: str,
        message_id: str | None,
    ) -> None:
        """agent-restart §6.2 boot-settle: patch the ``restart_audits``
        row in place at wake-delivery time — ``executed_at``/``outcome``
        (terminal mapping: restart⇒applied, halt⇒halted,
        stop_forced_kill⇒applied-forced, restart_abandoned⇒abandoned),
        ``overrides[].status`` (via ``get_last_applied_resolution`` —
        V-8 semantics), and ``notify`` {delivered, at, message_id}.
        Best-effort, restart-kind wakes only; never raises."""
        if wake.kind != "restart":
            return
        try:
            outcome_map = {
                "restart": "applied",
                "halt": "halted",
                "stop_forced_kill": "applied (forced-kill during stop)",
                "restart_abandoned": "abandoned",
            }
            settled = outcome_map.get(terminal_outcome, "unknown")
            fields: dict[str, Any] = {
                "executed_at": uj.now_iso(),
                "outcome": settled,
                "notify": {
                    "status": "delivered" if message_id else "pending",
                    "at": uj.now_iso(),
                    "message_id": message_id,
                },
            }
            # overrides[].status — same comparison as the render.
            try:
                data = uj.journal_read(install_dir)
                audits = data.get("restart_audits")
                row = (
                    audits.get(wake.run_id)
                    if isinstance(audits, dict)
                    else None
                )
                if isinstance(row, dict) and isinstance(
                    row.get("overrides"), list
                ):
                    from daemon.services.settings_service import (
                        get_last_applied_resolution,
                    )

                    resolution = get_last_applied_resolution()
                    for o in row["overrides"]:
                        if not isinstance(o, dict):
                            continue
                        key = str(o.get("key", "?"))
                        if resolution is None:
                            o["status"] = "not-applied (resolution record absent)"
                            continue
                        rec = resolution.get(key)
                        if not isinstance(rec, dict):
                            o["status"] = "not-applied (no resolution record)"
                        elif rec.get("source") != "db":
                            o["status"] = f"not-applied — resolved tier={rec.get('source')}"
                        elif rec.get("value") == o.get("db_value"):
                            o["status"] = "applied"
                        else:
                            o["status"] = "late-write"
                    fields["overrides"] = row["overrides"]
            except Exception as ov_exc:  # noqa: BLE001 — best-effort
                logger.debug(
                    "UpgradeJournalSweepService: restart-audit overrides "
                    "settle skipped for run_id=%s: %s (outcome/notify "
                    "fields still settle)",
                    wake.run_id, ov_exc,
                )
            uj.record_restart_audit(install_dir, wake.run_id, fields)
        except Exception as exc:  # noqa: BLE001 — never raises
            logger.warning(
                "UpgradeJournalSweepService: restart-audit settle FAILED "
                "for run_id=%s: %s",
                wake.run_id, exc,
            )

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
            # F5(ii) — non-terminal filter (review round). The repo's
            # ``get_by_agent_id`` returns ALL instances for the agent,
            # including terminal rows (COMPLETED / TERMINATED / ERROR
            # / FAILED). The fall-back is meant to deliver a wake to a
            # LIVE ari; a terminal row is the wrong target (the
            # enqueue path's auto-revive machinery is reserved for the
            # primary arming-instance path, not for the fall-back's
            # "front door" choice). Filter to non-terminal here so the
            # fall-back stays on a status that is actually ari-active.
            # PAUSED is intentionally NOT filtered out — the enqueue
            # path's PAUSED-exempt claim gate holds the wake Task
            # PENDING until resume (R-15 accepted behavior).
            _TERMINAL_STATUSES = frozenset(
                {"completed", "terminated", "error", "failed"}
            )
            instances = [
                i for i in instances
                if getattr(i, "status", None) not in _TERMINAL_STATUSES
            ]
            if not instances:
                logger.warning(
                    "UpgradeJournalSweepService: ari fall-back: zero "
                    "non-terminal ari instances for project=%s — returning None",
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
                # agent-restart C-2: same reserved-lane rule as the direct
                # deliver path — restart-kind wakes NEVER ride a user
                # source, even on the ari fall-back. DF-4 (P1): the
                # NON-restart fall-back arm rides the reserved lane too —
                # a wake armed with NO user origin must never mint a
                # DIRECT user-origin window (the retired bare-"api"
                # fallback forged one). A non-empty source still rides
                # VERBATIM — the reserved literal is the fallback arm ONLY.
                source=(
                    RESTART_WAKE_SOURCE
                    if wake.kind == "restart"
                    else (wake.source or SWEEP_NO_USER_ORIGIN_WAKE_SOURCE)
                ),
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
        """Phase 2 T14 grace-abandonment (D-FA1.2, ADR-042 — review MUST-FIX,
        F2 hardened).

        Iterates ``pending_wakes``; for any record whose ``abandon_after``
        is ``<= now`` AND for which ``wake_terminal_event_after(journal,
        armed_at)`` is ``None`` (no terminal event yet → NOT
        deliverable), calls ``uj.mark_wake_abandoned(install_dir,
        run_id, "grace_expired")`` (structural removal + ONE
        ``wake_abandoned`` history event for forensics).

        F2 hardening (review round): the pre-F2 grace pass abandoned
        every past-grace record unconditionally. This contradicted the
        feature's core promise — a wake whose terminal event fired
        during a long downtime (e.g. ``restart`` journaled at +30min
        with arm+40min = past grace) would be abandoned and the user
        would receive NO notification, exactly the scenario the
        feature exists for. The fix: the grace pass now reads the
        journal once at the top of the pass and uses
        ``wake_terminal_event_after(journal, wake.armed_at)`` to
        decide. When the predicate is non-None (deliverable NOW), the
        wake is HELD — the deliver pass on this same tick (or the
        next, if the deliver pass is skipped) will pick it up via
        the normal terminal-gating path. When the predicate is None
        (no terminal event, no chance of delivery), the wake is
        abandoned with ``reason=grace_expired`` exactly as before.
        The reading of the journal is itself wrapped (torn or
        OSError → ``errors += 1`` and the pass yields no
        abandonments); a single bad read does not poison the tick.

        Runs BEFORE the kill-switch OFF branch so the OFF pass only
        sees not-yet-graced records: a past-grace-and-not-deliverable
        record gets the more-specific ``reason="grace_expired"``
        (the OFF pass would have wrongly labeled it
        ``kill_switch_off``). Records still within grace are HELD
        pending (this method does not touch them).

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
        # F2: read the journal ONCE so we can run
        # ``wake_terminal_event_after`` per wake. A torn / OSError
        # read surfaces as ``errors += 1`` and the pass yields no
        # abandonments (a missing journal must not cause a
        # wholesale abandon — the next tick retries with a clean
        # read).
        try:
            grace_journal = uj.journal_read(install_dir)
        except uj.JournalTorn as torn_exc:
            logger.warning(
                "UpgradeJournalSweepService: grace pass "
                "journal_read FAILED (torn): %s — continuing (no "
                "abandons on this tick)",
                torn_exc,
            )
            return self.WakeSweepResult(
                pending_at_start=result.pending_at_start,
                pending_at_end=result.pending_at_end,
                delivered=result.delivered,
                abandoned=result.abandoned,
                errors=result.errors + 1,
                coalesce_overflows=result.coalesce_overflows,
            )
        except OSError as os_exc:
            logger.warning(
                "UpgradeJournalSweepService: grace pass "
                "journal_read FAILED (OSError): %s — continuing (no "
                "abandons on this tick)",
                os_exc,
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
            # F2 deliverability check: if a terminal event exists in
            # the journal AT/AFTER the wake's ``armed_at``, the wake
            # is deliverable now — the deliver pass on this tick (or
            # the next) will handle it. HELD here so the user
            # actually receives the notification.
            try:
                deliverable = (
                    uj.wake_terminal_event_after(
                        grace_journal, wake.armed_at, kind=wake.kind
                    )
                    is not None
                )
            except Exception as deliv_exc:  # noqa: BLE001
                # A torn / malformed history is already handled by
                # the read above; this catch is purely defensive
                # against an unexpected reader error. On reader
                # failure, fall back to the pre-F2 behavior (treat
                # as not deliverable → abandon) — losing the wake is
                # the worse outcome than a false-abandon, but this
                # is a last-resort branch and the next tick's read
                # will likely succeed.
                logger.warning(
                    "UpgradeJournalSweepService: grace pass — "
                    "wake_terminal_event_after FAILED for "
                    "run_id=%s: %s — falling back to pre-F2 "
                    "abandon (deliverability unknown)",
                    wake.run_id, deliv_exc,
                )
                deliverable = False
            if deliverable:
                # Past grace but DELIVERABLE — the deliver pass
                # handles it. Skip the abandon (the F2 fix).
                continue
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
                # agent-restart A-1: the overflow accounting mirrors the
                # F-1 composite partition key ``(arming_instance_id,
                # kind)`` EXACTLY — a restart/promote pair for one
                # instance forms TWO groups, never one (T0b: 17 restart +
                # 0 promote ⇒ 1 group; 17+17 ⇒ 2; mixed kinds ⇒ 0
                # overflow where same-kind would have dropped one).
                groups_input: dict[
                    tuple[str, str], list[tuple[uj.PendingWake, str]]
                ] = {}
                for w, o in terminal_wakes:
                    groups_input.setdefault(
                        (w.arming_instance_id, w.kind), []
                    ).append((w, o))
                for group_key, items in groups_input.items():
                    kept = len(coalesced.get(group_key, ([], []))[0])
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
            for (instance_id, _group_kind), (wakes, outcomes) in coalesced.items():
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
                        self._format_coalesced_wake_body(
                            wakes, outcomes, journal, install_dir
                        )
                        if len(wakes) > 1
                        else self._format_wake_body(
                            head, outcomes[0], journal, install_dir
                        )
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
                        # agent-restart A-2 (§2.5 row (i)): the re-stamp
                        # is KIND-GATED — restart-kind wakes NEVER re-stamp
                        # a user-origin window (the wake turn must carry
                        # NO direct provenance; the restart gate reads
                        # class=direct only from a REAL user turn). The
                        # gate is on ``wake.kind``, NEVER on a source
                        # sentinel. Promote-kind wakes keep the ADR-041
                        # re-stamp (defensive/redundant, unchanged).
                        # (Kind gate = ``head.kind`` — the group is
                        # kind-homogeneous BY CONSTRUCTION via the F-1
                        # composite partition key, so gating on the head
                        # gates the whole group.)
                        if head.kind != "restart":
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
                            # agent-restart C-2: restart-kind wakes deliver
                            # on the RESERVED internal lane (never a user
                            # source, never "api") — the wake turn can
                            # never re-mint direct provenance (§2.5 row
                            # (i)); promote-kind keeps today's routing.
                            # DF-4 (P1): the NON-restart fall-back arm now
                            # uses the reserved lane as well — a wake armed
                            # with NO user origin delivered on the retired
                            # bare-"api" fallback FORGED a DIRECT
                            # user-origin window (the delegated-forge root
                            # mint). A non-empty source still rides VERBATIM.
                            source=(
                                RESTART_WAKE_SOURCE
                                if head.kind == "restart"
                                else (head.source or SWEEP_NO_USER_ORIGIN_WAKE_SOURCE)
                            ),
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
                                # agent-restart §6.2: settle the audit row
                                # (outcome/overrides/notify) at delivery.
                                self._settle_restart_audit(
                                    install_dir, w, outcomes[0], message_id
                                )
                            delivered_count += len(wakes)
                        else:
                            # No message_id — F1 review round: the wake
                            # is currently ``delivering`` (the CAS at
                            # the top of this branch already flipped it)
                            # and the old "hold next tick" comment was
                            # a lie — the CAS gate on
                            # ``mark_wake_delivering`` requires
                            # ``status == "pending"``, so a
                            # ``delivering`` record was stranded
                            # forever. Roll the status back to
                            # ``pending`` so the next tick can re-claim
                            # and re-deliver. Safe per the atomicity
                            # verification in
                            # ``mark_wake_pending``'s docstring:
                            # ``enqueue_message`` either raised
                            # (no row written) or returned a
                            # ``message_id``; the no-message_id path
                            # here is the result-malformed defensive
                            # branch, never a partial write.
                            logger.warning(
                                "UpgradeJournalSweepService: enqueue_message "
                                "returned no message_id for run_id=%s — "
                                "rolling wake back to pending for next tick",
                                head.run_id,
                            )
                            for w in wakes:
                                try:
                                    uj.mark_wake_pending(
                                        install_dir, w.run_id
                                    )
                                except Exception as rb_exc:  # noqa: BLE001
                                    logger.warning(
                                        "UpgradeJournalSweepService: "
                                        "mark_wake_pending (F1 rollback) "
                                        "FAILED for run_id=%s: %s — "
                                        "record stays delivering (next "
                                        "tick will retry via re-claim)",
                                        w.run_id, rb_exc,
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
                        # F1 review round: a transient (non-KeyError)
                        # ``enqueue_message`` failure stranded the wake
                        # in ``delivering`` forever — the CAS gate on
                        # ``mark_wake_delivering`` requires
                        # ``status == "pending"``. Roll the status
                        # back so the next tick can re-claim. Safe per
                        # the atomicity verification in
                        # ``mark_wake_pending``'s docstring:
                        # ``enqueue_message`` either raised (no row
                        # written) or returned a ``message_id``; a
                        # raised exception = no row written = safe
                        # to retry.
                        logger.warning(
                            "UpgradeJournalSweepService: enqueue_message "
                            "FAILED for run_id=%s: %s — rolling wake "
                            "back to pending for next tick",
                            head.run_id, enq_exc,
                        )
                        for w in wakes:
                            try:
                                uj.mark_wake_pending(install_dir, w.run_id)
                            except Exception as rb_exc:  # noqa: BLE001
                                logger.warning(
                                    "UpgradeJournalSweepService: "
                                    "mark_wake_pending (F1 rollback) "
                                    "FAILED for run_id=%s: %s — "
                                    "record stays delivering (next "
                                    "tick will retry via re-claim)",
                                    w.run_id, rb_exc,
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

    # ── Phase 2 (promote-handback-halt): the serving-green closer ─────────

    def _closer_enabled(self) -> bool:
        """Per-tick kill-switch read (``ENSEMBLE_UPGRADE_SERVING_GREEN_CLOSER``).

        NEVER cached — the operator must be able to flip it without a
        rebuild (the per-tick precedent:
        ``_post_restart_arm_notify_enabled``). ``=0`` disables the whole
        lane: no ``serving_green_commit``, no ``serving_unhealthy``, no
        ``rollback_requested`` (the stale txn is left untouched)."""
        return os.environ.get(SERVING_GREEN_CLOSER_ENV, "1") != "0"

    def _closer_warn_transition(
        self, key: str, active: bool, msg: str, *args: Any
    ) -> None:
        """M5 (cycle-2 tidier, COUNCIL-CORRECTED shape) — STATE-latched
        WARNING for the closer's per-tick conditions.

        A persistent closer condition re-fires every ~90s tick. The
        TRANSITION into failure logs at WARNING exactly ONCE per episode
        (the operator backstop); UNCHANGED-state repeats drop to DEBUG
        (a WARNING per tick is the A5 noise class that buries the
        operator backstop under identical lines). A recovery
        (``active=False``) silently clears the latch so the NEXT episode
        — e.g. the journal healing and tearing again, or a verdict
        flipping unhealthy↔healthy — WARNs again. Keys carry the episode
        scope: txn-scoped conditions key by run_id (a NEW txn is a NEW
        episode); sweep-global conditions (journal unreadable, provider
        failed, tick failed) clear on their success paths. The journal
        is never mutated for observability."""
        if active:
            if self._closer_warn_active.get(key):
                logger.debug(
                    msg + " [unchanged — already WARNED for this episode]",
                    *args,
                )
                return
            self._closer_warn_active[key] = True
            logger.warning(msg, *args)
        else:
            self._closer_warn_active.pop(key, None)

    def _closer_boot_grace_s(self) -> int:
        """The closer's effective boot-grace horizon (seconds).

        The OPERATOR env override can only RAISE the horizon above the
        declared default — the one-way derivation rule (AM-P2-24 fold (b)):
        ``UPGRADE_CLOSER_BOOT_GRACE_S`` ≥ ``HANDBACK_VERIFY_BUDGET_S``. The
        constructor test seam bypasses that clamp (it is not an operator
        surface).

        F4 (code-council W2) — the floor is now EFFECTIVE, not just
        declared: the effective grace is
        ``max(UPGRADE_CLOSER_BOOT_GRACE_S, HANDBACK_VERIFY_BUDGET_S)``
        where the hand-back budget is read from the environment. The
        transport is the .env export seam (``launcher.sh`` exports every
        .env key and the daemon is the launcher's child), plus the
        ``export`` added beside the knob in ``scripts/upgrade/lib.sh`` so
        lib.sh-sourced contexts propagate it to their children. Both
        windows must cover the SAME measured unit-active→serving boot —
        pre-fix the docstring asserted the floor while the code clamped
        ONLY at the 300 constant, so an operator who raised the hand-back
        budget left the closer free to judge NOT-GREEN before the
        hand-back verifier had finished waiting.
        """
        if self._closer_boot_grace_override is not None:
            return max(1, int(self._closer_boot_grace_override))
        raw = os.environ.get(UPGRADE_CLOSER_BOOT_GRACE_ENV, "")
        try:
            value = int(float(raw))
        except (TypeError, ValueError, OverflowError):
            # M2 (cycle-2 tidier) — OverflowError joins the catch:
            # int(float("inf")) overflows (a poisoned env value must
            # never raise past this parse — pre-fix it killed the closer
            # EVERY tick, bricking the janitor lane); int(float("nan"))
            # is already a ValueError. Degradation: the default floor.
            value = UPGRADE_CLOSER_BOOT_GRACE_S
        if value < UPGRADE_CLOSER_BOOT_GRACE_S:
            value = UPGRADE_CLOSER_BOOT_GRACE_S
        handback = self._handback_verify_budget_s()
        if handback > value:
            value = handback
            # log the EFFECTIVE value on clamp — once per process (the
            # per-tick precedent is the read itself; a WARNING per tick
            # would be the A5 noise class).
            if not self._closer_grace_clamp_logged:
                self._closer_grace_clamp_logged = True
                logger.warning(
                    "UpgradeJournalSweepService: serving-green closer — "
                    "HANDBACK_VERIFY_BUDGET_S=%ss raises the effective "
                    "boot-grace floor to %ss (UPGRADE_CLOSER_BOOT_GRACE_S "
                    "default %ss; the one-way rule AM-P2-24: the closer's "
                    "horizon may never be lower than the hand-back budget)",
                    handback, value, UPGRADE_CLOSER_BOOT_GRACE_S,
                )
        return value

    @staticmethod
    def _handback_verify_budget_s() -> int:
        """The shell knob ``HANDBACK_VERIFY_BUDGET_S`` as seen by this
        process (0 = unset/unparseable ⇒ the declared default applies).

        Fail-safe on garbage: an unparseable value is IGNORED (the default
        floor stands) — the same ``_numeric_knob`` degradation lib.sh
        documents for the shell side.
        """
        raw = os.environ.get(HANDBACK_VERIFY_BUDGET_ENV, "")
        try:
            value = int(float(raw))
        except (TypeError, ValueError, OverflowError):
            # M2 — same bound/guard as the boot-grace parse:
            # int(float("inf")) overflows; the default applies (0).
            return 0
        return value if value > 0 else 0

    @staticmethod
    def _closer_authorized_under_lock(
        fresh: dict[str, Any], target: str
    ) -> bool:
        """F2 (code-council W3) — the P9 AUTHORIZATION conjunct, re-checked
        against the **already-loaded under-lock** history (NO extra read).

        The pre-lock P9 read happens BEFORE the journal lock is taken; the
        under-lock re-validation re-checked only ``in_flight`` / ``target``
        / ``flipped`` / ``displaced_version`` — **not** the authorization
        event. A journal mutated in that window (the evidence withdrawn,
        or a different txn's evidence substituted) therefore sailed through
        the commit-close ⇒ a direct AM-P2-3 non-conformance ("re-check
        ALL predicates P1-P9 under the lock").

        The matcher is the SAME per-entry, delimiter-anchored one the
        pre-lock read uses (``uj.history_entry_matches``) ⇒ the two reads
        share one contract (no substring conjunction, no cross-entry false
        positive).

        PER-TARGET semantics (A7, stated — the key is NOT per-txn): the
        promote lane emits ``intent_flip`` **BEFORE** ``journal_open_txn``
        (``promote.sh:515`` vs ``:522``) ⇒ the authorization row exists
        before the txn does, so it CANNOT be keyed per txn; the reader
        matches the event against the TARGET. The ORDERING INVARIANT this
        depends on: ``intent_flip`` is emitted first and the txn second —
        re-keying the reader per txn would break authorization entirely.
        """
        history = fresh.get("history")
        if not isinstance(history, list):
            return False
        return any(
            uj.history_entry_matches(entry, "intent_flip", target)
            for entry in history
        )

    @staticmethod
    def _closer_txn_identity(in_flight: dict[str, Any], target: str) -> str:
        """The txn identity used by the write-once key, the request detail
        and the launcher-side reader.

        The RESTART lane stamps ``run_id`` into ``in_flight``; the
        PROMOTE lane does NOT (``journal_open_txn`` in lib.sh writes
        {kind,target,started_at,flipped,owner_pid,last_heartbeat,
        displaced_version} — the promote run_id lives in the pipeline
        LOCK, which is gone by the time a stale txn is swept). ⇒ the
        identity falls back to ``<target>@<started_at>`` — stable across
        ticks AND across launcher boots (the same txn keeps the same
        started_at), and distinct per txn (a re-promote stamps a new
        started_at ⇒ a fresh write-once key, per AM-P2-13's rule).

        THE SHELL SIDE MUST DERIVE THE SAME STRING (launcher.sh's
        ``_js_serving_txn_identity``): the request reader matches on
        (target, run_id) inside ONE history entry."""
        run_id = str(in_flight.get("run_id") or "").strip()
        if run_id:
            return run_id
        started = str(in_flight.get("started_at") or "").strip()
        return f"{target}@{started}" if started else target

    @staticmethod
    def _closer_owner_dead(in_flight: dict[str, Any]) -> bool:
        """Owner liveness (P7) — the daemon's own os.kill-based shape.

        D1 (2026-11-11): the predicate now lives ONCE, in
        ``daemon.tools.upgrade_journal.txn_owner_dead`` (the mirror of
        lib.sh's ``_txn_owner_dead``) — this shim delegates so the closer
        and the reclaim can never drift apart.

        EPERM ⇒ ALIVE (the process exists under another user — the same
        contract as ``upgrade_journal._pid_alive`` and lib.sh's
        ``_pid_alive``): a foreign-owned owner is never presumed dead.
        """
        return uj.txn_owner_dead(in_flight)

    def _closer_symlink_flipped(self, data: dict[str, Any], target: str) -> bool:
        """P5's symlink-heal arm — the launcher twin (the kill-window
        symlink heal: ``launcher.sh``'s "the symlink is the authoritative
        flip evidence" block — the ``cur_link``/``jcur`` comparison that
        heals ``flipped=false`` when ``current`` already resolves to
        ``releases/<target>``).

        The journal's ``flipped`` marker can be false while the atomic
        flip ALREADY happened (promote died between the flip and the
        marker). The SYMLINK is the authoritative flip evidence: if
        ``current`` → ``releases/<target>`` while the journal's own
        ``current`` still names the PREVIOUS version, the flip happened."""
        try:
            link = os.readlink(self._install_dir / "current")
        except OSError:
            return False
        if link != f"releases/{target}":
            return False
        current = data.get("current")
        return not (isinstance(current, str) and current == target)

    def _closer_grace_elapsed(self, key: str) -> bool:
        """The closer's OWN boot-grace window (P8, first conjunct).

        OBSERVATION-RELATIVE: armed on the FIRST tick where the pre-grace
        predicates (P1-P9 — the call site sits AFTER the P9 authorization
        gate) hold; NEVER extended; re-armed only for a NEW txn identity
        (a full lapse does not re-arm the SAME txn — the verdict is
        write-once, so a re-arm would only defer ticks that have nothing
        left to decide)."""
        now = datetime.now(tz=timezone.utc)
        if self._closer_grace_key != key or self._closer_grace_armed_at is None:
            self._closer_grace_key = key
            self._closer_grace_armed_at = now
            return False
        elapsed = (now - self._closer_grace_armed_at).total_seconds()
        return elapsed >= float(self._closer_boot_grace_s())

    async def sweep_serving_green_closer(self) -> dict[str, Any]:
        """Phase 2 — the serving-green stale-txn closer (the daemon half of
        the SPLIT closer).

        A stale, already-FLIPPED promote txn is disposed of on OBSERVED
        serving health: serving-green ⇒ commit-close (stay on the release,
        undo any wrongful quarantine, NO rollback / quarantine / counter /
        cooldown); genuinely unhealthy ⇒ a journaled verdict + a rollback
        REQUEST for the launcher to execute at its next boot.

        THE PREDICATE (P1-P9 — ALL must hold; ANY miss ⇒ NO ACTION THIS
        TICK — never a commit, never a verdict, never a request):

        ======  ==================================================================
        P1      ``self._install_dir is not None``
        P2      the journal is readable and untorn
        P3      ``in_flight`` is a dict (an OPEN txn)
        P4      ``kind != "restart"`` (the daemon boot sweep owns restarts)
        P5      ``flipped == "true"`` OR the symlink-heal says so
        P6      ``age > SWEEP_STALE_S`` (600s — the protocol mirror)
        P7      the txn's ``owner_pid`` is dead
        P8      the boot-grace window elapsed AND ``grace_active == False``
        P9      the journal history carries an ``intent_flip`` event naming
                THIS txn's target — the AUTHORIZATION conjunct
        ======  ==================================================================

        P9 is the security hinge (OQ-C-11, RATIFIED): a positive health
        read proves the daemon is RUNNING the target — it does NOT prove
        the flip was operator-authorized. Absent P9 ⇒ the lane takes NO
        action at all (no commit, no verdict, no request).

        THE RESTORED INVARIANT (AM-P2-24, normative): *``rollback_requested``
        is writable ONLY when genuinely unhealthy OUTSIDE grace
        (``grace_active == False``); no path may write it during warmup.*
        ⇒ degraded-but-grace-held ⇒ DEFER.

        The daemon NEVER rolls back by itself: it repoints no symlink,
        stops nothing, counts nothing. The rollback EXECUTION is the
        launcher's existing chain (Option 2 of the plan's disposition
        table — a journaled request, executed at the next launcher boot).

        Never raises (janitor contract): a failed tick logs and the next
        tick retries."""
        result: dict[str, Any] = {
            "action": "none",
            "reason": None,
            "target": None,
            "run_id": None,
        }
        install_dir = self._install_dir
        if install_dir is None:                       # P1
            result["reason"] = "no_install_dir"
            return result
        if not self._closer_enabled():
            result["action"] = "disabled"
            result["reason"] = "kill_switch"
            return result
        try:
            data = uj.journal_read(install_dir)       # P2
            # M5 state-latch recovery: a readable journal re-arms the
            # unreadable WARNING for the NEXT episode.
            self._closer_warn_transition(
                "closer-journal-unreadable", False, ""
            )
        except Exception as read_err:  # noqa: BLE001 — janitor, never gates
            result["reason"] = "journal_unreadable"
            self._closer_warn_transition(
                "closer-journal-unreadable", True,
                "UpgradeJournalSweepService: serving-green closer journal "
                "read FAILED (%s: %s) — no action this tick",
                type(read_err).__name__, read_err,
            )
            return result
        in_flight = data.get("in_flight")
        if not isinstance(in_flight, dict):           # P3
            result["reason"] = "no_in_flight"
            return result
        if str(in_flight.get("kind", "")) == "restart":   # P4
            result["reason"] = "restart_kind"
            return result
        target = str(in_flight.get("target") or "").strip()
        if not target:
            result["reason"] = "no_target"
            return result
        result["target"] = target
        # P5 — flipped (marker OR symlink evidence)
        flipped = str(in_flight.get("flipped", "")).lower() == "true"
        if not flipped:
            flipped = self._closer_symlink_flipped(data, target)
        if not flipped:
            result["reason"] = "not_flipped"
            return result
        # P6 — age
        started = in_flight.get("started_at")
        started_dt = uj.parse_iso_utc(started)
        if started_dt is None:
            result["reason"] = "unparseable_started_at"
            return result
        age_s = int(
            (datetime.now(tz=timezone.utc) - started_dt).total_seconds()
        )
        if age_s <= SWEEP_STALE_S:
            result["reason"] = "fresh"
            return result
        # P7 — owner dead
        if not self._closer_owner_dead(in_flight):
            result["reason"] = "owner_alive"
            return result
        run_id = self._closer_txn_identity(in_flight, target)
        result["run_id"] = run_id
        # P9 — the authorization conjunct. ABSENT ⇒ NO ACTION THIS TICK
        # (never a commit, never a verdict, never a request).
        # PER-TARGET, not per-txn (A7, stated): promote emits intent_flip
        # BEFORE journal_open_txn (promote.sh:515 vs :522) ⇒ the evidence
        # predates the txn ⇒ per-txn keying would break authorization.
        # This read is PRE-LOCK; the under-lock re-check lives in
        # _closer_authorized_under_lock (F2) inside both arms.
        if not uj.journal_history_has_event(
            install_dir, "intent_flip", target
        ):
            result["reason"] = "no_intent_flip"
            logger.info(
                "UpgradeJournalSweepService: serving-green closer — stale "
                "flipped txn target=%s run_id=%s has NO intent_flip evidence "
                "— no action this tick (P9: never commit, never request)",
                target, run_id,
            )
            return result
        # The closer's OWN boot-grace window (P8, first conjunct) — armed
        # on the FIRST tick where the pre-grace predicates (P1-P9) hold
        # ("it is armed on the first tick where P1-P9 hold, and it does not
        # re-arm per tick"). It is evaluated HERE, before the health read,
        # so the window starts at the first OBSERVATION of the txn — not at
        # the first tick that happens to reach the verdict branch.
        closer_boot_grace_elapsed = self._closer_grace_elapsed(run_id)
        # Health read (in-process — no HTTP self-call, no DB).
        composite = None
        provider = self._readiness_provider
        if provider is not None:
            try:
                composite = provider()
            except Exception as provider_err:  # noqa: BLE001
                self._closer_warn_transition(
                    "closer-provider-failed", True,
                    "UpgradeJournalSweepService: serving-green closer "
                    "readiness_provider FAILED (%s: %s) — treating the "
                    "composite as missing (DEFER, never a verdict)",
                    type(provider_err).__name__, provider_err,
                )
                composite = None
        if composite is not None:
            # M5 state-latch recovery: a healthy provider read re-arms
            # the provider-failed WARNING for the NEXT episode.
            self._closer_warn_transition(
                "closer-provider-failed", False, ""
            )
        if composite is None:
            # composite-missing is a CANNOT-TELL, not an unhealthy verdict
            # (AM-P2-8) — the composite is None until the first readiness
            # tick; an in-process wiring defect must never become a
            # rollback request. ⇒ DEFER.
            result["action"] = "deferred"
            result["reason"] = "composite-missing"
            return result
        try:
            from daemon import __version__ as _daemon_version
            _version_import_failed = False
        except Exception:  # noqa: BLE001 — CANNOT-TELL, never a verdict
            # M3 (cycle-2 tidier) — an INTERNAL import failure is NOT a
            # release fault: pre-fix the empty _daemon_version made
            # version_match false ⇒ "version-mismatch" ⇒ a rollback
            # REQUEST — punishing the release for OUR import machinery,
            # exactly the punishment class this lane exists to end. The
            # composite-missing arm above is the mirror: DEFER (result
            # fields; no verdict, no request) — the existing defer
            # semantics carry it and the next tick retries.
            _daemon_version = ""
            _version_import_failed = True
        if _version_import_failed:
            result["action"] = "deferred"
            result["reason"] = "version-import-failed"
            return result
        v_target = target[1:] if target.startswith("v") else target
        version_match = bool(_daemon_version) and _daemon_version == v_target
        try:
            ready = bool(getattr(composite, "ready", False))
            grace_active = bool(getattr(composite, "grace_active", False))
        except Exception:  # noqa: BLE001 — an unreadable composite = not ready
            ready = False
            grace_active = True
        if version_match and ready:
            # GREEN ⇒ commit-close. The closer's OWN boot-grace gates the
            # NOT-GREEN VERDICT ONLY (AM-P2-24's normative sentence: "the
            # closer's NOT-GREEN verdict — and therefore every write of
            # serving_unhealthy and rollback_requested — additionally
            # requires grace_active == False"). A POSITIVE verdict needs no
            # grace: the grace exists so the daemon does not judge ITSELF
            # unhealthy while it is still booting — deferring a good
            # outcome would only keep the txn open (and grow the
            # launcher's defer counter) for no safety gain.
            action = await self._closer_commit_green(
                target, run_id, age_s, _daemon_version
            )
            result.update(action)
            return result
        # The NOT-GREEN path — the RESTORED INVARIANT applies HERE: a
        # grace-held observation NEVER produces a verdict or a request.
        if grace_active:
            result["action"] = "deferred"
            result["reason"] = "grace-held"
            return result
        if not closer_boot_grace_elapsed:
            result["action"] = "deferred"
            result["reason"] = "boot-grace"
            return result
        reason = "version-mismatch" if not version_match else "not-ready"
        action = await self._closer_verdict_unhealthy(
            target, run_id, reason, age_s
        )
        result.update(action)
        return result

    async def _closer_commit_green(
        self,
        target: str,
        run_id: str,
        age_s: int,
        version: str,
    ) -> dict[str, Any]:
        """The GREEN commit-close (AM-P2-1's history-before-close order).

        1. ``current`` → target
        2. ``previous`` → ``in_flight.displaced_version`` (absent/null ⇒
           HALT-FOR-HUMAN — the txn is old-format/ambiguous, so no silent
           commit; nothing is mutated)
        3. history append ``serving_green_commit`` — and FIRST, when the
           target is in ``quarantined[]``, append
           ``quarantine_cleared_serving_green`` (the EVENT is the amended
           shell gate's acceptance signal ⇒ event-before-close makes the
           clearance crash-proof)
        4. ``in_flight`` → null  ← the txn closes ONLY here
        5. ``quarantined[]`` array rewrite (hygiene; idempotent)

        NOT done on GREEN: quarantine marking, rollback counter increment,
        cooldown arming — the cap/cooldown are anti-flapping responses to
        BAD outcomes and a successful-and-declared flip is not one.

        Lock discipline (AM-P2-3): the journal lock is taken with
        ``wait_s=0`` and the txn is RE-READ + RE-CHECKED under it; a busy
        lock ⇒ no action this tick (never block the sweep)."""
        result: dict[str, Any] = {"action": "none", "reason": None}
        install_dir = self._install_dir
        assert install_dir is not None
        acquired, busy_run = uj.lock_acquire(
            install_dir, run_id, wait_s=0.0
        )
        if not acquired:
            result["action"] = "deferred"
            result["reason"] = "lock_busy"
            logger.info(
                "UpgradeJournalSweepService: serving-green closer — "
                "pipeline lock busy (run_id=%s) — no action this tick",
                busy_run or "?",
            )
            return result
        try:
            # RE-READ + RE-VALIDATE under the lock (AM-P2-3).
            try:
                fresh = uj.journal_read(install_dir)
            except Exception as read_err:  # noqa: BLE001
                result["reason"] = "journal_unreadable_under_lock"
                logger.warning(
                    "UpgradeJournalSweepService: serving-green closer "
                    "journal re-read FAILED under the lock (%s) — no action",
                    type(read_err).__name__,
                )
                return result
            fresh_in_flight = fresh.get("in_flight")
            if not isinstance(fresh_in_flight, dict):
                result["reason"] = "txn_closed_under_lock"
                return result
            if str(fresh_in_flight.get("target") or "") != target:
                result["reason"] = "txn_changed_under_lock"
                return result
            if str(fresh_in_flight.get("flipped", "")).lower() != "true" \
                    and not self._closer_symlink_flipped(fresh, target):
                result["reason"] = "not_flipped_under_lock"
                return result
            # F2 (code-council W3): re-check the AUTHORIZATION under the
            # lock, against the already-loaded history (no extra read).
            if not self._closer_authorized_under_lock(fresh, target):
                result["action"] = "deferred"
                result["reason"] = "no_intent_flip_under_lock"
                logger.warning(
                    "UpgradeJournalSweepService: serving-green closer — the "
                    "intent_flip authorization for target=%s is NOT present "
                    "in the journal re-read UNDER the lock — no commit "
                    "(P9 re-checked under the lock; the evidence was "
                    "withdrawn or mutated between the pre-lock read and "
                    "the lock)", target,
                )
                return result
            displaced = fresh_in_flight.get("displaced_version")
            if not isinstance(displaced, str) or not displaced \
                    or displaced == "null":
                # Old-format / ambiguous txn — HALT-FOR-HUMAN (the
                # launcher's twin: the "no in_flight.displaced_version"
                # halt-for-human arm of the launcher's serving-green
                # commit branch — the `sweep-halt-old-format` notify and
                # the "halt" history row): no silent
                # commit, nothing mutated, the txn stays in place.
                uj.journal_history_append(
                    install_dir,
                    "halt",
                    f"closer: stale flipped txn (target={target}, run_id={run_id}) "
                    f"has no in_flight.displaced_version (old-format txn) — "
                    f"halt-for-human, txn left in place",
                )
                result["action"] = "halted"
                result["reason"] = "no_displaced_version"
                self._closer_warn_transition(
                    f"closer-no-displaced-version:{run_id}", True,
                    "UpgradeJournalSweepService: serving-green closer — "
                    "stale flipped txn target=%s has no displaced_version — "
                    "halt-for-human, txn left open", target,
                )
                return result
            # A6 (code-council advisory 6) — pre-check the fields BEFORE the
            # first write: the commit mutates `current` then `previous`, and
            # `journal_update_field` raises KeyError on an absent field
            # (schema drift ⇒ ensure_extensions). Pre-fix the KeyError was
            # caught as journal_write_failed AFTER `current` had already
            # been mutated — a half-applied commit. (The `quarantined`
            # rewrite below is guarded by `journal_quarantined_contains`,
            # which reads False when the field is absent ⇒ no KeyError.)
            missing_fields = [
                f for f in ("current", "previous") if f not in fresh
            ]
            if missing_fields:
                result["action"] = "deferred"
                result["reason"] = "journal_field_missing"
                self._closer_warn_transition(
                    f"closer-field-missing:{run_id}", True,
                    "UpgradeJournalSweepService: serving-green closer — "
                    "journal is missing required field(s) %s — refusing to "
                    "commit (a half-applied commit is worse than a defer); "
                    "the txn is left untouched",
                    ", ".join(missing_fields),
                )
                return result
            try:
                uj.journal_update_field(install_dir, "current", target)
                uj.journal_update_field(install_dir, "previous", displaced)
                if uj.journal_quarantined_contains(fresh, target):
                    uj.journal_history_append(
                        install_dir,
                        QUARANTINE_CLEARED_SERVING_GREEN_EVENT,
                        f"target {target} run_id {run_id} "
                        f"serving_green_commit prev {displaced}",
                    )
                uj.journal_history_append(
                    install_dir,
                    SERVING_GREEN_COMMIT_EVENT,
                    f"target {target} run_id {run_id} prev {displaced} "
                    f"version {version} — serving green (in-process health "
                    f"read: version match + readiness composite ready); "
                    f"committed and continued — NOT rolled back, NOT "
                    f"quarantined, counter NOT incremented, no cooldown",
                )
                uj.journal_update_field(install_dir, "in_flight", None)
                quarantined = fresh.get("quarantined")
                if isinstance(quarantined, list) and target in quarantined:
                    uj.journal_update_field(
                        install_dir,
                        "quarantined",
                        [v for v in quarantined if v != target],
                    )
            except (uj.JournalTorn, OSError, KeyError) as write_err:
                result["action"] = "deferred"
                result["reason"] = "journal_write_failed"
                self._closer_warn_transition(
                    f"closer-commit-write-failed:{run_id}", True,
                    "UpgradeJournalSweepService: serving-green closer "
                    "commit-close write FAILED (%s: %s) — the txn is left "
                    "as-is; the next tick retries",
                    type(write_err).__name__, write_err,
                )
                return result
            # M5 state-latch recovery: a successful commit-close
            # re-arms the commit-arm WARNINGs for the NEXT txn episode.
            self._closer_warn_transition(
                f"closer-commit-write-failed:{run_id}", False, ""
            )
            self._closer_warn_transition(
                f"closer-field-missing:{run_id}", False, ""
            )
            result["action"] = "committed"
            result["reason"] = "serving_green"
            logger.info(
                "UpgradeJournalSweepService: serving-green closer — stale "
                "flipped txn target=%s run_id=%s (age %ss) is SERVING GREEN "
                "— committed and continued (no rollback, no quarantine, no "
                "counter, no cooldown)",
                target, run_id, age_s,
            )
            return result
        finally:
            uj.lock_release(install_dir)

    async def _closer_verdict_unhealthy(
        self,
        target: str,
        run_id: str,
        reason: str,
        age_s: int,
    ) -> dict[str, Any]:
        """The NOT-GREEN branch — a journaled VERDICT + a rollback REQUEST.

        The daemon does NOT roll back: it repoints no symlink, stops
        nothing, counts nothing. It journals (a) the ``serving_unhealthy``
        verdict and (b) the ``rollback_requested`` disposition that the
        LAUNCHER executes at its next boot (the plan's Option 2 — zero new
        execution surface; the launcher already owns the chain).

        WRITE-ONCE per ``(run_id, target)`` (AM-P2-13): the closer ticks
        every 90s and the journal must NOT grow one row per tick. The
        scan is over the PARSED history (matching on event + target +
        run_id inside ONE entry — never a substring conjunction over the
        raw document, the same cross-entry hazard the shell readers
        avoid)."""
        result: dict[str, Any] = {"action": "none", "reason": reason}
        install_dir = self._install_dir
        assert install_dir is not None
        acquired, busy_run = uj.lock_acquire(
            install_dir, run_id, wait_s=0.0
        )
        if not acquired:
            result["action"] = "deferred"
            result["reason"] = "lock_busy"
            logger.info(
                "UpgradeJournalSweepService: serving-green closer — "
                "pipeline lock busy (run_id=%s) — no action this tick",
                busy_run or "?",
            )
            return result
        try:
            try:
                fresh = uj.journal_read(install_dir)
            except Exception as read_err:  # noqa: BLE001
                result["reason"] = "journal_unreadable_under_lock"
                logger.warning(
                    "UpgradeJournalSweepService: serving-green closer "
                    "journal re-read FAILED under the lock (%s) — no action",
                    type(read_err).__name__,
                )
                return result
            fresh_in_flight = fresh.get("in_flight")
            if not isinstance(fresh_in_flight, dict):
                result["reason"] = "txn_closed_under_lock"
                return result
            if str(fresh_in_flight.get("target") or "") != target:
                result["reason"] = "txn_changed_under_lock"
                return result
            # F2 (code-council W3): re-check the AUTHORIZATION under the
            # lock, against the already-loaded history (no extra read) —
            # the unhealthy VERDICT and the REQUEST are writes too.
            if not self._closer_authorized_under_lock(fresh, target):
                result["action"] = "deferred"
                result["reason"] = "no_intent_flip_under_lock"
                logger.warning(
                    "UpgradeJournalSweepService: serving-green closer — the "
                    "intent_flip authorization for target=%s is NOT present "
                    "in the journal re-read UNDER the lock — no verdict, no "
                    "request (P9 re-checked under the lock)",
                    target,
                )
                return result
            try:
                verdict_already = uj.journal_history_has_event(
                    install_dir, SERVING_UNHEALTHY_EVENT, target, run_id
                )
                request_already = uj.journal_history_has_event(
                    install_dir, ROLLBACK_REQUESTED_EVENT, target, run_id
                )
                if not verdict_already:
                    uj.journal_history_append(
                        install_dir,
                        SERVING_UNHEALTHY_EVENT,
                        f"target {target} run_id {run_id} reason {reason} — "
                        f"serving unhealthy (age {age_s}s); NOT rolled back by "
                        f"the daemon — the launcher executes the disposition "
                        f"at its next boot",
                    )
                if not request_already:
                    uj.journal_history_append(
                        install_dir,
                        ROLLBACK_REQUESTED_EVENT,
                        f"target {target} run_id {run_id} reason {reason}",
                    )
            except (uj.JournalTorn, OSError, KeyError) as write_err:
                result["action"] = "deferred"
                result["reason"] = "journal_write_failed"
                self._closer_warn_transition(
                    f"closer-verdict-write-failed:{run_id}", True,
                    "UpgradeJournalSweepService: serving-green closer "
                    "unhealthy-verdict write FAILED (%s: %s) — next tick "
                    "retries",
                    type(write_err).__name__, write_err,
                )
                return result
            # M5 state-latch recovery: a successful verdict write
            # re-arms the write-failed WARNING for the NEXT episode.
            self._closer_warn_transition(
                f"closer-verdict-write-failed:{run_id}", False, ""
            )
            result["action"] = "unhealthy"
            # A5 (code-council advisory 5) — log the verdict ONCE: the
            # journal writes above are WRITE-ONCE per (run_id, target), so
            # the log must mirror them. A WARNING on every 90s tick is
            # noise that buries the operator backstop; the repeat ticks
            # downgrade to DEBUG.
            if verdict_already and request_already:
                logger.debug(
                    "UpgradeJournalSweepService: serving-green closer — "
                    "repeat tick for target=%s run_id=%s (reason=%s): the "
                    "verdict + rollback request were already journaled — "
                    "no new rows (the launcher executes them at its next "
                    "boot)",
                    target, run_id, reason,
                )
            else:
                logger.warning(
                    "UpgradeJournalSweepService: serving-green closer — stale "
                    "flipped txn target=%s run_id=%s is NOT SERVING GREEN "
                    "(reason=%s, age %ss) — verdict + rollback request journaled "
                    "(the launcher executes it at its next boot)",
                    target, run_id, reason, age_s,
                )
            return result
        finally:
            uj.lock_release(install_dir)

    # ── periodic sweep ───────────────────────────────────────────────────

    async def sweep_once(self) -> dict[str, int | str | None | dict[str, Any]]:
        """One reconcile + closer + GC + wake-sweep tick. Returns a small
        visibility dict: ``{"reconcile": <note|None>, "gc_pruned": <int>,
        "skipped": <0|1>, "wake": <WakeSweepResult>, "closer": <dict>}``.
        Never raises — a failed tick logs and returns; the next tick
        retries by simply happening."""
        result: dict[str, int | str | None | dict[str, Any]] = {
            "reconcile": None,
            "reclaim": None,
            "gc_pruned": 0,
            "skipped": 0,
            "wake": self.WakeSweepResult(),
            "closer": None,
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
            # D1 (2026-11-11): the ORPHANED-txn reclaim tick — the daemon
            # side of the ONE reclaim mechanism. Before this, the sweep
            # NEVER disposed a dead pre-flip txn (reconcile_pending_op
            # returns None whenever ``in_flight`` is a dict), so only the
            # shell preflight could clear it — the 2026-11-11 starvation.
            # The predicate + the claim-before-adopt disposal live in
            # ``daemon.tools.upgrade_journal`` (the shell lane's comp3 FAST
            # PATH rule, mirrored) and the TOOL busy-check arms call the very
            # same two helpers — no divergent copies. ORDER MATTERS: the
            # reclaim runs BEFORE the reconcile so the reconcile's
            # pending-op closure (expiry / terminal event) can fire on the
            # now-unblocked op.
            reclaim_note = uj.reclaim_stale_txn(
                self._install_dir, actor="daemon-sweep"
            )
            if reclaim_note:
                logger.info(
                    "UpgradeJournalSweepService: reclaim — %s", reclaim_note
                )
            result["reclaim"] = reclaim_note
        except Exception as reclaim_err:  # noqa: BLE001 — a janitor never gates
            logger.warning(
                "UpgradeJournalSweepService: reclaim tick failed: %s: %s — "
                "next tick will retry",
                type(reclaim_err).__name__,
                reclaim_err,
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
        # Phase 2 (promote-handback-halt) — the serving-green closer lane.
        # It runs AFTER the reconcile and BEFORE the GC. It is a SEPARATE
        # lane with its OWN narrow predicate (P1-P9): `reconcile_pending_op`
        # returns None whenever `in_flight` is a dict (upgrade_journal.py's
        # in_flight-dict guard) and the restart-arm lane's flipped=true
        # never-touch guard is CORRECT for THAT lane — carving an
        # exception into those proven fail-closed guards would widen
        # them, so the closer does not touch them at all.
        try:
            closer = await self.sweep_serving_green_closer()
            # M5 state-latch recovery: a clean tick re-arms the
            # tick-failed WARNING for the NEXT episode.
            self._closer_warn_transition("closer-tick-failed", False, "")
            result["closer"] = closer
            if closer.get("action") in ("committed", "unhealthy", "halted"):
                logger.info(
                    "UpgradeJournalSweepService: serving-green closer — %s",
                    closer,
                )
        except Exception as closer_err:  # noqa: BLE001 — never wedge boot
            self._closer_warn_transition(
                "closer-tick-failed", True,
                "UpgradeJournalSweepService: serving-green closer tick "
                "failed: %s: %s — next tick will retry",
                type(closer_err).__name__, closer_err,
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
        is an executor, its pid still exists, AND at least one of the three
        signals (heartbeat freshness / os.kill / cgroup) corroborates
        recent activity. P2 hardening (decisions (b) / C-7 / T2.5 / AC4).

        TWO signals, leave pending_op alone when EITHER holds (W4
        amendment, council 2026-10-12 — the plan said three; the third
        signal is NOT implemented, see the rationale below):
          (1) in_flight.last_heartbeat is FRESH (`OWNER_HEARTBEAT_FRESH_S`,
              default 60s — tightened from the EXECUTOR_STALENESS_WINDOW_S=900s
              backstop). The shell lane writes this via the splice path in
              lock_heartbeat; the accessor is `uj.journal_owner_heartbeat_fresh`.
              Consulted FIRST (C8): hb-fresh ⇒ alive regardless of the pid
              slot (the pre-spawn-stamp window has owner_pid == 0 while a
              live promote heartbeats).
          (2) `os.kill(owner_pid, 0) == 0` — the executor process is still
              in the process table (only reached when the hb signal is
              stale/unreadable).
          (3) systemd cgroup leaf — DEFERRED (not implemented). What it
              would add: a pid-recycled-under-a-foreign-unit discrimination
              for the stale-hb + alive-pid cell (a recycled pid would be
              wrongly trusted by signal 2 alone). Why deferred: that cell
              requires BOTH a stale hb AND a live pid, which the W1 ruling
              already treats as leave-alone (conservative); the leak is an
              unbounded conservatism (a swept-later op), not a lossy
              reclaim. Amending the docs + this comment is the W4 ruling;
              implementing the leaf match stays P3+ work.

        CLEAR-eligible (sweep may proceed) only when EVERY signal fails:
          pid ESRCH-dead (a heartbeat cannot resurrect a dead pid);
          hb stale beyond 900s OR no in_flight record;
          cgroup mismatch (recycled pid under a foreign unit) OR
          cgroup indeterminable AND hb stale.
        Note (per AC4 trust table): "fresh heartbeat alone" is enough to
        leave-alone even when pid and cgroup are missing — that's the
        wedge scenario where the bash scribe is alive but the probe ran
        before pid+cgroup resolved.
        """
        if op.owner_kind != "executor":
            return False
        # C8 (council 2026-10-12): heartbeat freshness is consulted
        # FIRST — before any pid gate. The prior shape short-circuited
        # `owner_pid <= 0` to False BEFORE the hb read, refuting the
        # AC4 trust-table cell "fresh heartbeat alone → leave alone
        # even when pid and cgroup are missing": a pending_op in the
        # PRE-SPAWN-STAMP window (record created before the executor
        # stamped its pid — owner_pid legitimately 0) had a live
        # promote heartbeating in_flight.last_heartbeat right now, and
        # the sweep cleared the op underneath it. hb-fresh ⇒ alive,
        # REGARDLESS of the pid slot; only a stale/unreadable hb lets
        # pid/cgroup decide.
        try:
            if self._install_dir is not None and uj.journal_owner_heartbeat_fresh(
                self._install_dir
            ):
                return True
        except Exception:
            pass  # hb signal unavailable — fall through to the pid gates
        if op.owner_pid <= 0:
            # no pid slot AND no fresh hb — nothing says alive
            return False
        # Signal (2) preparation — pid liveness (os.kill(pid, 0);
        # every OSError including ESRCH counts as not-alive here: the
        # hb-first gate above already returned True for the wedge case,
        # so this arm only runs on a STALE/unreadable hb where a dead
        # pid genuinely means the owner is gone).
        pid_alive = False
        try:
            os.kill(op.owner_pid, 0)
            pid_alive = True
        except OSError:
            pid_alive = False
        # Defense-in-depth reads (epoch from in_flight) — falls back to
        # the legacy `op.owner_heartbeat_at or op.armed_at` only when
        # the journal-side signal is unreadable.
        if pid_alive:
            # Signal (2) — pid alive. Combine with the (optional) cgroup
            # check below; the cgroup fail-closed default is "indeterminable
            # ⇒ accept pid as enough corroboration" (P2 minimum; P3 widens
            # the cgroup match to the expected-unit comparison per arch §2.2).
            return True
        # Signal (3) — cgroup match. Without a live pid, a cgroup match
        # alone would be a stale recycle defense — refuse as
        # "indeterminable ⇒ conservative sweep."
        return False

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

    @staticmethod
    def _per_run_narrative_path(install_dir: Path, run_id: str | None) -> Path | None:
        """D3-12/D3-14: resolve the run's NARRATIVE file by run_id.

        M7 (tidier, 2026-10-30): DELEGATES to the owning module's single
        resolver (``upgrade_journal.per_run_narrative_path``). This copy used
        to be a second implementation with NO run_id validation while the
        tools-side copy validated — the drift the review found. One naming
        rule, one home (the module that also owns the writer
        ``executor_run_log_paths`` and the env-scope table)."""
        return uj.per_run_narrative_path(install_dir, run_id)

    def _per_run_observation(
        self, install_dir: Path, run_id: str | None
    ) -> PerRunObservation:
        """Per-run observation for the executor_still_running row
        (D3-9/phase-4 T4.4 enrichment). Best-effort: any miss degrades to
        "unknown" — the row must never fail because observability is absent.

        M6 (tidier, 2026-10-30): a typed record instead of a bare 3-tuple of
        stringly sentinels (``"(no per-run narrative)"`` / ``"unknown"`` /
        ``"123s"``), so a caller cannot mistake ``"0s"`` for ``"unknown"``
        and a fourth field cannot silently reorder every call site. The
        "unknown" RENDERING lives at the row builder, not in the data.

        A8 (code council, 2026-10-30): the ``releases/state.json`` read is
        an unbounded whole-file ``json.load`` (455 KB+ today, and ``history``
        grows without limit) — a small instance of the event-loop-blocking
        class. Its async caller runs THIS function in a worker thread
        (``asyncio.to_thread``), so the loop never blocks on it."""
        narr = self._per_run_narrative_path(install_dir, run_id)
        last_phase: str | None = None
        if narr is not None:
            try:
                with narr.open("rb") as fh:
                    fh.seek(0, os.SEEK_END)
                    size = fh.tell()
                    fh.seek(max(0, size - _LOG_TAIL_MAX_BYTES))
                    text = fh.read().decode("utf-8", errors="replace")
                for line in reversed(text.splitlines()):
                    if "phase-marker:" in line:
                        last_phase = line.strip()
                        break
            except OSError:
                pass
        elapsed_s: int | None = None
        try:
            jp = uj.executor_log_path(install_dir)  # parent dir only
            state = jp.parent.parent / "releases" / "state.json"
            with state.open(encoding="utf-8") as fh:
                doc = json.load(fh)
            hist = doc.get("history") or []
            if hist:
                last_ts = hist[-1].get("ts") or ""
                if last_ts:
                    last_dt = datetime.fromisoformat(
                        last_ts.replace("Z", "+00:00")
                    )
                    elapsed_s = int(
                        (datetime.now(timezone.utc) - last_dt).total_seconds()
                    )
        except (OSError, ValueError, TypeError, KeyError):
            pass
        return PerRunObservation(
            narrative=narr, last_phase=last_phase, elapsed_s=elapsed_s
        )

    def _log_tail(self, install_dir: Path, run_id: str | None = None) -> str:
        """Last ≤4KB byte-tail, then last ≤40 lines of decoded text of the
        run's log. D3-14: when ``run_id`` resolves a per-run NARRATIVE file,
        read THAT (the aggregate is pointer-only after D3-12 — reading it
        here would lose the run's tail at exactly the wrong moment);
        otherwise fall back to the aggregate. seek(-4KB) + truncate-at-
        newline — never an O(n) whole-file read; the file is parent-created
        before Popen so absence just means an empty tail."""
        try:
            log = self._per_run_narrative_path(install_dir, run_id) \
                or uj.executor_log_path(install_dir)
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
        tail = self._log_tail(job.install_dir, run_id=job.run_id)
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
        # Incident 2026-10-31 (restart-arm livelock): an OBSERVED
        # executor exit on an unflipped restart txn is authoritative
        # death evidence — close the arm HERE (in_flight=None +
        # pending_op cleared + restart_abandoned event + run_id-matched
        # lock release) so the next sweep cycle delivers any pending
        # wake instead of livelocking until a human restarts the
        # daemon. BOTH reaper exit legs (normal waitpid and
        # ChildProcessError exit_code=-1) funnel into this method, so
        # both shapes close. NEVER touches a flipped=true txn (mutation
        # happened — halt-for-human semantics stay).
        try:
            close_note = uj.close_restart_arm_executor_exit(
                job.install_dir, job.run_id, exit_code, pid=job.pid,
            )
        except Exception as close_exc:  # noqa: BLE001 — the reaper loop
            # must never die on the closure path; a failed closure
            # leaves boot recovery (close_stale_restart_arm) as the
            # backstop.
            close_note = None
            logger.warning(
                "UpgradeJournalSweepService: executor-exit arm closure "
                "FAILED (run_id=%s exit_code=%s): %r — boot recovery "
                "remains the backstop",
                job.run_id, exit_code, close_exc,
            )
        if close_note:
            logger.info("UpgradeJournalSweepService: %s", close_note)

    async def _journal_executor_still_running(
        self, job: ReaperJob, timeout_s: int
    ) -> None:
        # T4.4/D3-9 (phase 3): the row carries the per-run narrative path,
        # the last phase marker and the elapsed-since-last-journal-activity
        # so the operator reads WHERE the run stalled instead of following
        # a hand-off into one shared aggregate file. The kill contract and
        # the 660 s threshold are UNCHANGED (pinned).
        # A8 (code council): the observation does a whole-file
        # ``json.load`` of releases/state.json — off the loop.
        obs = await asyncio.to_thread(
            self._per_run_observation, job.install_dir, job.run_id
        )
        narr_s = (
            str(obs.narrative) if obs.narrative is not None
            else "(no per-run narrative)"
        )
        last_phase = obs.last_phase if obs.last_phase is not None else "unknown"
        elapsed = (
            f"{obs.elapsed_s}s" if obs.elapsed_s is not None else "unknown"
        )
        try:
            uj.journal_history_append(
                job.install_dir,
                "executor_still_running",
                f"run_id={job.run_id} pid={job.pid} still running after "
                f"{timeout_s}s — benign-detach (no kill; own process group; "
                f"narrative={narr_s}; last={last_phase}; "
                f"last_journal_activity={elapsed} ago)",
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
