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
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from daemon.tools import upgrade_journal as uj

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
    ) -> None:
        self._install_dir = install_dir
        self._interval_seconds = max(1, int(reconcile_interval_seconds))
        # Clamp floor 1 (not 60): the PRODUCTION 60s floor is enforced by
        # pydantic (``ServicesConfig.upgrade_journal_reaper_timeout_seconds
        # Field(ge=60)``) — the constructor kwarg is an internal test seam
        # (TmpImageCleanupService precedent), so tests may pass a tiny
        # timeout to exercise the benign-detach branch in seconds.
        self._reaper_timeout_s = max(1, int(reaper_timeout_seconds))
        self._reaper_queue: asyncio.Queue[ReaperJob] = asyncio.Queue()
        self._sweep_task: asyncio.Task[None] | None = None
        self._reaper_task: asyncio.Task[None] | None = None
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
                # CancelledError is the normal shutdown path (FM-11 — the
                # reaper re-raises it and lands HERE, not in a bare except);
                # Exception is defensive against an unexpected shutdown
                # error. Either way the service is stopped — no re-raise.
                pass
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

    # ── periodic sweep ───────────────────────────────────────────────────

    async def sweep_once(self) -> dict[str, int | str | None]:
        """One reconcile + GC tick. Returns a small visibility dict:
        ``{"reconcile": <note|None>, "gc_pruned": <int>, "skipped":
        <0|1>}``. Never raises — a failed tick logs and returns; the next
        tick retries by simply happening."""
        result: dict[str, int | str | None] = {
            "reconcile": None,
            "gc_pruned": 0,
            "skipped": 0,
        }
        if self._install_dir is None:
            return result
        try:
            op = uj.read_pending_op(self._install_dir)
            if op is not None and self._executor_alive_recent(op):
                # R-P1-3/R-P1-7: the op tracks a LIVE-recent executor — do
                # not clear while it runs (promote shells can legitimately
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
        reaps it; the journal entry is the operator's audit trail."""
        while True:
            job = await self._reaper_queue.get()
            try:
                # The blocking waitpid runs in a thread; the shield keeps a
                # stray outer cancellation from half-tearing the wait
                # (manager.py's bounded-wait pattern — FM-11 compliant).
                status = await asyncio.wait_for(
                    asyncio.shield(
                        asyncio.to_thread(self._waitpid_blocking, job.pid)
                    ),
                    timeout=self._reaper_timeout_s,
                )
                exit_code = os.waitstatus_to_exitcode(status)
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
                await self._journal_executor_exit(job, exit_code=-1)
                continue
            except Exception as exc:  # noqa: BLE001 — one bad job never
                # kills the worker; journal + move on.
                await self._journal_executor_error(job, exc)
                continue
            await self._journal_executor_exit(job, exit_code=exit_code)

    @staticmethod
    def _waitpid_blocking(pid: int) -> int:
        """Blocking ``os.waitpid`` — runs in a thread via ``to_thread``.
        Returns the raw wait status (decoded by the worker)."""
        _, status = os.waitpid(pid, 0)
        return status

    # ── journal writers (R-M5-1: OSError → WARNING, NO retry, continue) ──

    def _log_tail(self, install_dir: Path) -> str:
        """Last ≤4KB / ≤40 lines of ``data/upgrade.log``. seek(-4KB) +
        truncate-at-newline — never an O(n) whole-file read; the file is
        parent-created before Popen so absence just means an empty tail."""
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
        self, job: ReaperJob, *, exit_code: int
    ) -> None:
        tail = self._log_tail(job.install_dir)
        detail = (
            f"run_id={job.run_id} pid={job.pid} exit_code={exit_code} "
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
