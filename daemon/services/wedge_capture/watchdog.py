"""The wedge-capture WATCHDOG (D1 / D13 / D25b / D38 / A4 / B3 / B4 / T15).

A DEDICATED DAEMON THREAD — NEVER A COROUTINE
----------------------------------------------
A starved event loop cannot run a coroutine: that is the entire
premise of the instrument. The watchdog is a
``threading.Thread(daemon=True)`` that samples from OUTSIDE the loop.

THE LOOP BODY (T15 / B4(a))
---------------------------

    drain the manual slot → sample → evaluate triggers →
    (on fire) capture → cooldown bookkeeping

The manual slot is drained with ``get_nowait()`` — **NON-BLOCKING,
never ``get()``**. A manual request that arrives while a capture is in
progress is serviced on the tick AFTER that capture completes (the
watchdog is single-threaded, so there is exactly ONE capture executor
— no second concurrent-writer class can exist).

THE TICK PATH NEVER LOGS (A4 / RK-15)
--------------------------------------
Logging is permitted ONLY on state transitions (trigger fired /
capture started / capture failed / probe stopped / manual request
serviced) and inside the capture steps (which are OFF the tick path).
A tick's ``except`` branch RECORDS into ``missed_samples`` and
continues — it does **not** log. (A logging handler whose lock is held
by a loop thread blocked on a full stdout pipe would deadlock the
watchdog itself.)

THE LOCK IS NEVER HELD ACROSS A CAPTURE STEP (D37 / A-MR-a)
-------------------------------------------------------------
The lock guards only the small status-state mutation. Capture steps
run OUTSIDE the lock, so a reader (``status()``) NEVER waits behind a
20 s capture.

SAME-TICK PRECEDENCE (approver N8)
----------------------------------
A manual request wins the tick over an automatic trigger: the manual
request was already ACCEPTED with a 202 and a pre-allocated id, so
the operator is waiting on it. The automatic trigger for that tick is
re-evaluated on the NEXT tick (its cooldown is unaffected).

SHUTDOWN DISPOSITION OF A PENDING MANUAL REQUEST (approver N7)
--------------------------------------------------------------
A manual request still sitting in the slot when the watchdog stops is
**DROPPED** — it is never captured after the stop event. The operator
re-submits against a live daemon. The status surface reports the
dropped request so the loss is visible, not silent.
"""

from __future__ import annotations

import logging
import os
import queue
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from . import collector as _collector
from . import db_capture as _db_capture
from . import writer as _writer
from .boot_record import read_latest_boot_record
from .config import WedgeCaptureConfig
from .lag_probe import LoopLagProbe
from .sampler import CpuSampler
from .triggers import (
    REASON_MANUAL,
    REASON_SUSTAINED_CPU,
    REASON_SUSTAINED_LOOP_LAG,
    REASON_THREAD_CONCENTRATION,
    TriggerEngine,
    TriggerSample,
    samples_from,
)

logger = logging.getLogger("daemon.wedge_capture.watchdog")

#: The refusal tokens (the house family).
REFUSAL_WATCHDOG_DISABLED = "watchdog_disabled"
REFUSAL_MANUAL_RATE_LIMITED = "manual_rate_limited"
REFUSAL_CAPTURE_BUSY = "capture_busy"

#: The manual-capture rate cap (F-16) — max 2 per minute.
MANUAL_RATE_CAP_PER_MINUTE = 2
MANUAL_RATE_WINDOW_S = 60.0


@dataclass
class ManualCaptureRequest:
    """A queued manual-capture request (D38 / B3).

    Attributes:
        capture_id: The PRE-ALLOCATED capture id — the directory name
            the operator is told about in the 202.
        reason: The capture's reason label (``manual`` by default).
        requested_at: The monotonic timestamp of the request.
        requested_by: Who asked for it (the CLI's ``--by``, optional).
    """

    capture_id: str
    reason: str = REASON_MANUAL
    requested_at: float = 0.0
    requested_by: str = ""


class ManualCaptureSlot:
    """The depth-1 manual-capture slot (D38 / B3 / B4).

    The slot is the ONE contract between the Phase-2 enqueue side (the
    admin router) and the Phase-1 servicing side (the watchdog thread).

    * the enqueue is **NON-BLOCKING** (``put_nowait``) — a blocking
      ``put()`` in an async route would block the event loop for up to
      the 20 s capture deadline, which is exactly the RK-38 class;
    * a full slot ⇒ the typed refusal ``capture_busy`` — nothing is
      enqueued, no 202 is returned, and there is **NO COALESCING**: the
      refused operator retries;
    * the rate cap (2/min) is applied AT ENQUEUE — a third request
      inside the same minute is refused with ``manual_rate_limited``
      BEFORE anything is queued.
    """

    def __init__(self, *, max_per_minute: int = MANUAL_RATE_CAP_PER_MINUTE) -> None:
        self._queue: queue.Queue[ManualCaptureRequest] = queue.Queue(maxsize=1)
        self._lock = threading.Lock()
        self._max_per_minute = int(max_per_minute)
        self._timestamps: list[float] = []
        self._dropped_on_stop = 0

    @property
    def max_per_minute(self) -> int:
        """The rate cap (requests per minute)."""
        return self._max_per_minute

    def enqueue(self, request: ManualCaptureRequest) -> tuple[bool, str]:
        """Enqueue a manual request.

        Returns:
            ``(accepted, refusal_token)`` — ``refusal_token`` is
            ``""`` on acceptance, else ``manual_rate_limited`` or
            ``capture_busy``.
        """
        now = time.monotonic()
        with self._lock:
            self._timestamps = [
                stamp
                for stamp in self._timestamps
                if (now - stamp) < MANUAL_RATE_WINDOW_S
            ]
            if len(self._timestamps) >= self._max_per_minute:
                return False, REFUSAL_MANUAL_RATE_LIMITED
            try:
                self._queue.put_nowait(request)
            except queue.Full:
                return False, REFUSAL_CAPTURE_BUSY
            self._timestamps.append(now)
            return True, ""

    def get_nowait(self) -> ManualCaptureRequest | None:
        """Drain one request — NON-BLOCKING, never ``get()``."""
        try:
            return self._queue.get_nowait()
        except queue.Empty:
            return None

    def drop_pending(self) -> int:
        """Drop every pending request (the shutdown disposition, N7)."""
        dropped = 0
        while True:
            request = self.get_nowait()
            if request is None:
                break
            dropped += 1
        with self._lock:
            self._dropped_on_stop += dropped
        return dropped

    @property
    def dropped_on_stop(self) -> int:
        """How many requests were dropped at shutdown."""
        return self._dropped_on_stop

    @property
    def depth(self) -> int:
        """The slot's current depth (0 or 1)."""
        return self._queue.qsize()


class WedgeCaptureWatchdog:
    """The instrument's watchdog thread.

    Args:
        config: The effective configuration (read once at start).
        manager: The ``InstanceManager`` — used to resolve the saver
            pool (stats only, never a checkout) and the release
            version. Optional (the packs run without one).
        app_state: The Starlette ``app.state`` — the readiness
            composite's home.
        loop: The event loop to probe for lag.
        boot_epoch: The boot epoch (naive-UTC) — recorded in every
            capture.
        release_version: The daemon's release version.
    """

    def __init__(
        self,
        *,
        config: WedgeCaptureConfig,
        manager: Any = None,
        app_state: Any = None,
        loop: Any = None,
        boot_epoch: datetime | None = None,
        release_version: str = "",
        refresh_interval_s: float = _collector.DEFAULT_REFRESH_INTERVAL_S,
    ) -> None:
        self._config = config
        self._manager = manager
        self._app_state = app_state
        self._loop = loop
        self._boot_epoch = boot_epoch
        self._release_version = release_version
        self._refresh_interval_s = refresh_interval_s

        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._started = False
        self._lock = threading.Lock()

        # The status state — mutated under the lock, read under the lock.
        self._missed_samples = 0
        self._last_capture_time: str | None = None
        self._last_capture_reason: str | None = None
        self._last_capture_id: str | None = None
        self._capture_count = 0
        self._last_trigger_detail: str = ""
        self._started_at: float | None = None

        # The manual slot (the shared enqueue/servicing contract).
        self.manual_slot = ManualCaptureSlot()

        # The sampler + the lag probe.
        self._sampler = CpuSampler()
        self._lag_probe = LoopLagProbe(loop)
        self._engine = TriggerEngine(config)

        # The host name — resolved ONCE, at start (A5 / RK-27).
        self._hostname = _collector.resolve_hostname()

        # The saver pool — resolved ONCE, at start (D21 / A5).
        self._pool: Any = None
        self._pool_reason = "not_resolved"
        self._dsn: str | None = None
        self._repository: Any = None

        self._writer = _writer.CaptureWriter(
            config.capture_dir,
            deadline_s=config.deadline_s,
            object_census=config.object_census,
        )

    # ── lifecycle ──────────────────────────────────────────────────────
    def start(self) -> None:
        """Start the watchdog thread (idempotent).

        Resolves the pool + the DSN + the hostname ONCE — never inside
        the capture deadline (A5).
        """
        with self._lock:
            if self._started:
                return
            self._started = True
        self._resolve_dependencies()
        self._stop_event.clear()
        thread = threading.Thread(
            target=self._run_loop,
            name="WedgeCaptureWatchdog",
            daemon=True,
        )
        self._thread = thread
        thread.start()
        self._started_at = time.monotonic()
        logger.info(
            "Wedge-capture watchdog started: dir=%s interval=%ss "
            "cpu>=%.1f%%/%ss lag>=%.2fs/%ss concentration>=%.1f%%/%ss "
            "cooldown=%ss deadline=%ss",
            self._config.capture_dir,
            self._config.sample_interval_s,
            self._config.cpu_pct,
            self._config.cpu_sustain_s,
            self._config.lag_s,
            self._config.lag_sustain_s,
            self._config.thread_concentration_pct,
            self._config.cpu_sustain_s,
            self._config.cooldown_s,
            self._config.deadline_s,
        )

    def _resolve_dependencies(self) -> None:
        """Resolve the pool / the DSN / the repository — ONCE, at start."""
        # The saver pool — STATS ONLY, never a checkout (D21 / RK-1).
        try:
            checkpointer = getattr(self._manager, "checkpointer", None)
            raw_saver = getattr(checkpointer, "raw_saver", None)
            pool = getattr(raw_saver, "conn", None)
            if pool is not None and hasattr(pool, "get_stats"):
                self._pool = pool
                self._pool_reason = "resolved"
            else:
                self._pool = None
                self._pool_reason = "not_postgres_backend"
        except Exception as exc:  # noqa: BLE001
            self._pool = None
            self._pool_reason = f"unresolved:{type(exc).__name__}"

        try:
            engine = getattr(self._manager, "engine", None)
            self._dsn = _db_capture.resolve_dsn(engine)
        except Exception:  # noqa: BLE001
            self._dsn = None

        try:
            from daemon.repositories.wedge_captures.repository import (
                WedgeCaptureRepository,
            )

            engine = getattr(self._manager, "engine", None)
            if engine is not None:
                self._repository = WedgeCaptureRepository(engine)
        except Exception:  # noqa: BLE001
            self._repository = None

        # The stall-ledger cut (D15 / D18).
        try:
            from daemon.checkpoint_perf import set_slow_saver_cut_ms

            set_slow_saver_cut_ms(self._config.slow_saver_ms)
        except Exception:  # noqa: BLE001
            pass

        # The pending-sidecar reconciler (T12 / A3).
        self.reconcile_pending()

    def join(self, timeout: float | None = None) -> None:
        """Join the watchdog thread (the house detached-join target).

        The lifespan NEVER calls this inline — it runs on a detached
        thread so the event loop is not blocked on the join.
        """
        thread = self._thread
        if thread is not None:
            thread.join(timeout=timeout)

    def stop(self, *, timeout: float = 5.0) -> None:
        """Stop the watchdog. Idempotent, never raises.

        The pending manual request is **DROPPED** (N7) — a capture is
        never started after the stop event.
        """
        self._stop_event.set()
        dropped = self.manual_slot.drop_pending()
        if dropped:
            logger.info(
                "Wedge-capture watchdog stopping: dropped %d pending manual "
                "request(s) (the operator re-submits)",
                dropped,
            )
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=timeout)
        with self._lock:
            self._started = False
        self._thread = None

    # ── the loop ───────────────────────────────────────────────────────
    def _run_loop(self) -> None:
        """The watchdog's tick loop.

        The tick path NEVER logs (A4). A raising step increments
        ``missed_samples`` and the loop continues — the thread never
        dies on a collector error.
        """
        interval = max(0.05, self._config.sample_interval_s)
        while not self._stop_event.is_set():
            started = time.monotonic()
            try:
                self.run_once()
            except Exception:  # noqa: BLE001 — the tick NEVER logs
                with self._lock:
                    self._missed_samples += 1
            elapsed = time.monotonic() - started
            remaining = interval - elapsed
            if remaining > 0:
                self._stop_event.wait(remaining)

    def run_once(self) -> dict[str, Any]:
        """Advance the watchdog by exactly ONE tick.

        The hermetic single-tick entry point (the packs use it instead
        of a background thread). Returns a small summary of the tick.
        """
        summary: dict[str, Any] = {"captured": False}

        # ── 1. drain the manual slot (NON-BLOCKING, never get()) ──────
        request = self.manual_slot.get_nowait()
        if request is not None:
            logger.info(
                "Wedge-capture: servicing manual capture request %s",
                request.capture_id,
            )
            result = self.run_capture(
                reason=request.reason,
                trigger_source=REASON_MANUAL,
                capture_id=request.capture_id,
            )
            summary["captured"] = True
            summary["capture"] = result.as_dict()
            summary["reason"] = request.reason
            # N8: the manual request wins the tick; the automatic
            # trigger for this tick is re-evaluated on the NEXT tick.
            return summary

        # ── 2. sample ──────────────────────────────────────────────────
        cpu_sample = self._sampler.sample(
            loop_thread_native_id=self._lag_probe.loop_thread_native_id,
        )
        lag_s = self._lag_probe.tick()
        lag_state = self._lag_probe.probe_state
        sample = samples_from(
            cpu_sample,
            lag_s=lag_s,
            lag_state=lag_state,
        )

        # ── 3. evaluate the triggers ───────────────────────────────────
        decisions = self._engine.observe(sample)
        for decision in decisions:
            logger.info(
                "Wedge-capture: trigger fired reason=%s detail=%s",
                decision.reason, decision.detail,
            )
            result = self.run_capture(
                reason=decision.reason,
                trigger_source=decision.reason,
                summary=decision.detail,
                observed=decision.observed,
                since=decision.since,
            )
            summary["captured"] = True
            summary["capture"] = result.as_dict()
            summary["reason"] = decision.reason
            # One capture per tick — the cooldown is per reason, and a
            # second reason in the SAME tick is captured on the next
            # tick (the capture is single-threaded).
            break

        summary["sample"] = {
            "process_cpu_pct": round(cpu_sample.process_cpu_pct, 3),
            "lag_s": lag_s,
            "lag_state": lag_state,
        }
        return summary

    # ── the capture ────────────────────────────────────────────────────
    def run_capture(
        self,
        *,
        reason: str,
        trigger_source: str = "",
        capture_id: str | None = None,
        summary: str = "",
        observed: dict[str, Any] | None = None,
        since: float | None = None,
    ) -> _writer.CaptureResult:
        """Run ONE capture — the shared routine (D38's one code path).

        The manual trigger and the automatic triggers use THIS method:
        two capture paths would drift, and the drill path is the one
        most likely to rot.

        The lock is NEVER held across the capture steps.
        """
        started = time.monotonic()
        captured_at = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        if not capture_id:
            capture_id = _writer.make_capture_id(
                captured_at=captured_at,
                host=self._hostname,
                reason=reason,
            )
        executor_native_id = _current_native_id()

        # ── retention FIRST (prune-on-write, D23 / A3) ────────────────
        self._prune()

        # ── the artifacts ───────────────────────────────────────────────
        threads = [
            thread.as_dict() for thread in self._sampler_last_threads()
        ]
        lag_payload = self._lag_probe.snapshot_histogram()
        lag_payload["last_probe_age_s"] = self._lag_probe.last_probe_age_s
        lag_payload["never_fired"] = self._lag_probe.never_fired
        lag_payload["stale"] = self._lag_probe.never_fired or (
            self._lag_probe.last_probe_age_s is not None
            and self._lag_probe.last_probe_age_s > (2.0 * self._refresh_interval_s)
        )
        lag_payload["probe_state"] = self._lag_probe.probe_state

        metrics = _collector.collect_memory(object_census=self._config.object_census)
        metrics["pool"] = _collector.collect_pool(self._pool)
        metrics["process"] = _collector.collect_process_info()

        db_payload = _db_capture.capture_db_snapshot(
            self._dsn,
            boot_epoch=self._boot_epoch.isoformat() if self._boot_epoch else None,
            budget_s=self._phase_budget(started),
        )

        readyz_payload = _collector.collect_readiness(
            self._app_state,
            refresh_interval_s=self._refresh_interval_s,
        )

        tail = self._read_stall_ledger()

        boot_record = read_latest_boot_record(self._config.capture_dir)
        auto_continue_mode = "unknown"
        if isinstance(boot_record, dict):
            mode = boot_record.get("effective_mode")
            if isinstance(mode, str) and mode in ("on", "off", "unknown"):
                auto_continue_mode = mode

        manifest_extra: dict[str, Any] = {
            "observed": observed or {},
            "trigger_since": since,
            "pool_counters": metrics.get("pool"),
            "lag_probe": {
                "state": lag_payload.get("probe_state"),
                "last_probe_age_s": lag_payload.get("last_probe_age_s"),
                "never_fired": lag_payload.get("never_fired"),
                "stale": lag_payload.get("stale"),
            },
        }

        result = self._writer.write_capture(
            capture_id=capture_id,
            reason=reason,
            trigger_source=trigger_source or reason,
            executor_native_id=executor_native_id,
            boot_epoch=self._boot_epoch.isoformat() if self._boot_epoch else None,
            captured_at=captured_at,
            host=self._hostname,
            release_version=self._release_version,
            summary=summary,
            auto_continue_mode=auto_continue_mode,
            stacks=lambda path: _collector.collect_stacks(path),
            threads=threads,
            lag=lag_payload,
            metrics=metrics,
            db=db_payload,
            readyz=readyz_payload,
            checkpoint_perf_tail=tail,
            extra_manifest=manifest_extra,
            # W2: the writer spends the SAME budget the capture opened —
            # the deadline is measured from run_capture's ENTRY, so a
            # pre-writer phase that burned it forces the write phase's
            # data steps to skip + record (never a fresh 20 s clock).
            started_monotonic=started,
        )

        # ── the metadata row (file-first, D12) ─────────────────────────
        row = {
            "capture_id": capture_id,
            "capture_dir": str(result.capture_dir),
            "trigger_reason": reason,
            "trigger_source": trigger_source or reason,
            "captured_at": datetime.now(timezone.utc).isoformat(),
            "boot_epoch": self._boot_epoch.isoformat() if self._boot_epoch else None,
            "host": self._hostname,
            "release_version": self._release_version,
            "summary": summary,
            "auto_continue_mode": auto_continue_mode,
            "file_count": result.manifest.get("file_count"),
            "total_bytes": result.manifest.get("total_bytes"),
            "metrics_snapshot": {
                "process_cpu_pct": manifest_extra.get("observed", {}).get("process_cpu_pct"),
                "lag_s": manifest_extra.get("observed", {}).get("lag_s"),
                "share_pct": manifest_extra.get("observed", {}).get("share_pct"),
                "pool": metrics.get("pool"),
            },
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        write_result = _db_capture.write_capture_row(
            self._dsn,
            row,
            budget_s=self._phase_budget(started),
        )
        if write_result.get("status") == "ok":
            result.row_written = True
        else:
            logger.warning(
                "Wedge-capture: metadata row write failed for %s: %s",
                result.capture_dir, write_result.get("reason", "unknown"),
            )
            _writer.write_pending_row_sidecar(result.capture_dir, row)

        # ── the status state (under the lock) ──────────────────────────
        with self._lock:
            self._last_capture_time = datetime.now(timezone.utc).isoformat()
            self._last_capture_reason = reason
            self._last_capture_id = capture_id
            self._capture_count += 1
            self._last_trigger_detail = summary
        self._engine.note_capture_started(started)

        logger.info(
            "Wedge-capture: capture written dir=%s reason=%s artifacts=%s "
            "duration=%.2fs row_written=%s",
            result.capture_dir, reason, len(result.artifacts),
            time.monotonic() - started, result.row_written,
        )
        return result

    # ── helpers ────────────────────────────────────────────────────────
    def _phase_budget(self, started: float, share: float = 0.25) -> float:
        """A DB phase's budget — a quarter of what is left, at least
        1 s, never MORE than what is left (W2: the deadline is
        end-to-end, measured from the capture's entry ``started``).

        ``0.0`` (budget exhausted) makes the DB step SKIP + record
        ``skipped:budget`` instead of attempting a fresh connect.
        """
        left = max(0.0, self._config.deadline_s - (time.monotonic() - started))
        if left <= 0:
            return 0.0
        return min(left, max(1.0, left * share))

    def _sampler_last_threads(self) -> list[Any]:
        """The last sample's per-thread attribution."""
        return list(self._sampler.last_threads)

    def _read_stall_ledger(self) -> list[dict[str, Any]]:
        """The stall-ledger tail (D18)."""
        try:
            from daemon.checkpoint_perf import (
                get_slow_saver_cut_ms,
                read_slow_saver_ops,
            )

            ops = read_slow_saver_ops()
            for entry in ops:
                entry.setdefault("cut_ms", get_slow_saver_cut_ms())
            return ops
        except Exception:  # noqa: BLE001
            return []

    def _prune(self) -> None:
        """Prune the captures (directory-authoritative) + the rows."""
        try:
            removed = _writer.prune_captures(
                self._config.capture_dir,
                max_captures=self._config.max_captures,
                max_age_hours=self._config.max_age_hours,
                max_disk_mb=self._config.max_disk_mb,
                # Prune-on-write runs BEFORE the new capture exists, so
                # one slot is reserved for it: the cap is then exact at
                # every observation point (SC-7 / AC21).
                reserve=1,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Wedge-capture: prune failed: %s", exc)
            return
        if removed and self._repository is not None:
            try:
                self._repository.delete_by_dirs([str(path) for path in removed])
            except Exception:  # noqa: BLE001
                pass

    def reconcile_pending(self) -> int:
        """Re-insert the pending sidecars' rows (T12 / A3)."""
        if self._repository is None:
            return 0
        try:
            pending = _writer.read_pending_row_sidecars(self._config.capture_dir)
            if not pending:
                return 0
            count = self._repository.reconcile_pending(pending)
            if count:
                logger.info(
                    "Wedge-capture: reconciled %d pending metadata row(s)",
                    count,
                )
            return count
        except Exception as exc:  # noqa: BLE001
            logger.warning("Wedge-capture: reconcile failed: %s", exc)
            return 0

    # ── the manual-capture enqueue (the Phase-2 side's contract) ──────
    def enqueue_manual_capture(
        self,
        *,
        capture_id: str | None = None,
        reason: str = REASON_MANUAL,
        requested_by: str = "",
    ) -> tuple[bool, str, dict[str, Any]]:
        """Enqueue a manual capture request (D38 / B3 / A-MR-b).

        The kill-switch-off and the watchdog-stopped states BOTH refuse
        with ``watchdog_disabled`` — nothing is enqueued, no capture, no
        row.

        Returns:
            ``(accepted, refusal_token, detail)``.
        """
        if not self._config.enabled or not self._is_running():
            return False, REFUSAL_WATCHDOG_DISABLED, {
                "reason": REFUSAL_WATCHDOG_DISABLED,
                "enabled": self._config.enabled,
                "running": self._is_running(),
            }
        if capture_id is None:
            capture_id = _writer.make_capture_id(
                captured_at=datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"),
                host=self._hostname,
                reason=reason,
            )
        request = ManualCaptureRequest(
            capture_id=capture_id,
            reason=reason,
            requested_at=time.monotonic(),
            requested_by=requested_by,
        )
        accepted, refusal = self.manual_slot.enqueue(request)
        if not accepted:
            return False, refusal, {"reason": refusal}
        return True, "", {
            "capture_id": capture_id,
            "capture_dir": str(self._config.capture_dir / capture_id),
            "reason": reason,
            "enqueued": True,
            "note": (
                "enqueued — the watchdog services it on its next tick; "
                "read the outcome via the list / fetch surface"
            ),
        }

    def _is_running(self) -> bool:
        """Is the watchdog thread alive?"""
        thread = self._thread
        return bool(thread is not None and thread.is_alive())

    # ── the status surface (D37 / A-MR-a) ──────────────────────────────
    def status(self) -> dict[str, Any]:
        """The status snapshot (D37).

        EVERY field is read under the lock — a multi-field payload
        taken from two instants would be torn (RK-36). The lock is
        never held across a capture step, so this NEVER waits behind a
        capture in flight.
        """
        with self._lock:
            payload: dict[str, Any] = {
                "enabled": self._config.enabled,
                "running": self._is_running(),
                "started_at_monotonic": self._started_at,
                "missed_samples": self._missed_samples,
                "last_capture_time": self._last_capture_time,
                "last_capture_reason": self._last_capture_reason,
                "last_capture_id": self._last_capture_id,
                "last_trigger_detail": self._last_trigger_detail,
                "capture_count": self._capture_count,
                "manual_slot_depth": self.manual_slot.depth,
                "manual_requests_dropped_on_stop": self.manual_slot.dropped_on_stop,
            }
        payload.update(self._config.as_status())

        # The lag-probe markers (RK-23) — absent-when-N/A.
        payload["last_probe_age_s"] = self._lag_probe.last_probe_age_s
        payload["lag_probe_state"] = self._lag_probe.probe_state
        payload["lag_never_fired"] = self._lag_probe.never_fired
        payload["lag_stale"] = bool(
            self._lag_probe.never_fired
            or (
                self._lag_probe.last_probe_age_s is not None
                and self._lag_probe.last_probe_age_s
                > (2.0 * self._refresh_interval_s)
            )
        )
        payload["loop_thread_native_id"] = self._lag_probe.loop_thread_native_id

        # The pool block (A2's vocabulary — the SAME names as the capture).
        payload["pool"] = _collector.collect_pool(self._pool)

        # The effective auto-continue mode + its boot epoch (D37) — read
        # from the boot RECORD (what the pass read at its boot), never
        # re-derived from precedence (BC-1 / D16).
        record = read_latest_boot_record(self._config.capture_dir)
        if isinstance(record, dict):
            mode = record.get("effective_mode")
            payload["auto_continue_mode"] = (
                mode if mode in ("on", "off", "unknown") else "unknown"
            )
            payload["boot_epoch"] = record.get("boot_epoch")
        else:
            payload["auto_continue_mode"] = "unknown"

        return payload


def _current_native_id() -> int | None:
    """The calling thread's OS tid — the capture's executor."""
    try:
        return threading.get_native_id()
    except Exception:  # pragma: no cover — defensive
        return None


__all__ = [
    "MANUAL_RATE_CAP_PER_MINUTE",
    "REFUSAL_CAPTURE_BUSY",
    "REFUSAL_MANUAL_RATE_LIMITED",
    "REFUSAL_WATCHDOG_DISABLED",
    "ManualCaptureRequest",
    "ManualCaptureSlot",
    "WedgeCaptureWatchdog",
]
