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
