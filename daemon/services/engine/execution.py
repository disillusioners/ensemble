"""Tick loop (Engine Arc P2 — T2.4).

One asyncio task owns the loop: hydrate → compute due-set (priority sort) →
dispatch per job → sleep(resolution). A per-job exception never kills the
tick; the loop's OWN unexpected error logs at ERROR and re-raises to a
supervised restart boundary (F-7 — never a silent return).
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Optional

from daemon.services.engine.locks import JobLockTable
from daemon.services.engine.state import (
    effective_interval_seconds,
    now_iso,
    parse_iso,
    skip_outcome,
    stamp_terminal,
    TerminalStamp,
    IDLE_GATE_SKIP_REASONS,
    now_iso as _now_iso,
    REASON_TIMEOUT_PENDING_EXTEND,
    OUTCOME_TIMEOUT_PENDING_EXTEND,
)
from daemon.services.engine import dispatch as D
from daemon.services.engine.guardrails import (
    DEP_MISSING_DEAD_FACTOR,
    ExtendWedgeTracker,
    resolve_at_due,
)

logger = logging.getLogger(__name__)


class EngineExecutionLoop:
    """The tick loop. Owned by EngineService."""

    def __init__(self, engine: Any) -> None:
        self.engine = engine

    @property
    def cache(self) -> dict:
        return self.engine._cache

    @property
    def locks(self) -> JobLockTable:
        return self.engine._locks

    @property
    def repository(self) -> Any:
        return self.engine.repository

    @property
    def shadow_mode(self) -> bool:
        return bool(getattr(self.engine, "shadow_mode", True))

    @property
    def resolution(self) -> float:
        cfg = getattr(self.engine, "config", None)
        if cfg is not None and getattr(cfg, "tick_resolution_seconds", None) is not None:
            return float(cfg.tick_resolution_seconds)
        return 1.0

    @property
    def boot_id(self) -> str:
        return getattr(self.engine, "_boot_id", "")

    @property
    def host_pid(self) -> int:
        import os

        return os.getpid()

    async def run(self) -> None:
        """The supervised loop (D3.9). Unexpected errors are recorded, logged
        at ERROR with a restart counter, and the loop re-enters after a
        bounded backoff — never ``return`` (no Template-B permanent exit)."""
        try:
            while not getattr(self.engine, "_stopping", False):
                try:
                    await self._tick()
                except asyncio.CancelledError:
                    raise
                except Exception as exc:  # noqa: BLE001 — supervised restart
                    restarts = getattr(self.engine, "_supervisor_restarts", 0) + 1
                    self.engine._supervisor_restarts = restarts
                    logger.error(
                        "engine supervisor: unexpected error %s: %s — "
                        "restart #%d, re-entering after bounded backoff",
                        type(exc).__name__,
                        exc,
                        restarts,
                        exc_info=True,
                    )
                    backoff = min(self.resolution, 5.0)
                    await asyncio.sleep(backoff)
                    continue
                await asyncio.sleep(self.resolution)
        except asyncio.CancelledError:
            logger.debug("engine tick loop: cancelled (shutdown)")
            return

    async def _tick(self) -> None:
        # P4 — liveness atomics for the engine-independent watchdog. Stamped
        # at the top of EVERY tick so a stall inside a tick leaves this age
        # growing (the watchdog samples it from outside the loop).
        self.engine._last_tick_monotonic = time.monotonic()
        self.engine._tick_count = int(getattr(self.engine, "_tick_count", 0) or 0) + 1
        now = datetime.now(timezone.utc)
        cache = self.cache
        # compute due set
        due_ids: list[str] = []
        for job_id, c in list(cache.items()):
            if self._is_due(job_id, c, now):
                due_ids.append(job_id)
        # stable sort: priority asc (0 highest), ties by job_id
        due_ids.sort(key=lambda jid: (cache[jid]["spec"].priority, jid))

        # mark would_fire
        for job_id in cache:
            cache[job_id]["would_fire"] = job_id in due_ids

        # dispatch each due job
        for job_id in due_ids:
            try:
                await self._dispatch_one(job_id, now)
            except Exception as exc:  # noqa: BLE001 — per-job never kills tick
                logger.error(
                    "engine tick: job %s raised %s: %s — converting to FAILED_BACKOFF",
                    job_id,
                    type(exc).__name__,
                    exc,
                    exc_info=True,
                )
                c = cache.get(job_id)
                if c is not None:
                    c["running"] = False
                    c["last_outcome"] = "FAILED_BACKOFF"
                    c["consecutive_skips"] = 0
                    # W-7: best-effort guarded DB stamp so the audit closes.
                    try:
                        await asyncio.to_thread(
                            self.repository.stamp_job_outcome,
                            job_id,
                            last_outcome="FAILED_BACKOFF",
                            last_skip_reason=None,
                            consecutive_skips=0,
                        )
                    except Exception:  # noqa: BLE001
                        pass

        # P3 T3.6: extend-wedge escalation check
        try:
            tracker = getattr(self.engine, "_extend_wedge_tracker", None)
            esc_set = getattr(self.engine, "_extend_escalated", set())
            now_ts = datetime.now(timezone.utc).timestamp()
            for job_id, cache in list(self.cache.items()):
                if not self.locks.is_extended(job_id):
                    if tracker is not None:
                        tracker.clear_hold(job_id)
                    continue
                if tracker is None:
                    continue
                if tracker.check_escalate(job_id, now_ts):
                    esc_set.add(job_id)
                    self.engine._extend_escalated = esc_set
                    from daemon.services.engine.guardrails import escalate_dead_log_once
                    escalate_dead_log_once(
                        job_id, esc_set,
                        level="warning",
                        message=(
                            f"engine job {job_id} extend-wedge escalation: "
                            f"hold exceeded {2}x max_runtime_s"
                        ),
                    )
                    # persist dead=1
                    try:
                        await asyncio.to_thread(
                            self.repository.stamp_job_outcome, job_id, dead=1
                        )
                    except Exception:  # noqa: BLE001
                        pass
        except Exception:  # noqa: BLE001
            pass

        # ingest pending events latch clear happens inside _dispatch_one

    def _is_due(self, job_id: str, cache: dict, now: datetime) -> bool:
        spec = cache.get("spec")
        if spec is None:
            return False
        trigger = getattr(spec, "trigger", None)
        trigger_kind = getattr(trigger, "trigger_kind", None)
        # event trigger → pending-event latch
        if trigger_kind == "event":
            return bool(cache.get("pending_event"))
        # interval trigger
        last_run_at = cache.get("last_run_at")
        if last_run_at is None:
            # P4 — the boot-scoped first-tick offset (delayed offset + deterministic
            # jitter) is projected at registration; fall back to the legacy
            # delayed-only computation when a cache predates it.
            first_offset = cache.get("first_tick_due_offset_s")
            if first_offset is None:
                first_kind = getattr(trigger, "first_tick_kind", None)
                if first_kind == "delayed":
                    first_offset = getattr(
                        trigger, "first_tick_offset_seconds", None
                    ) or 0.0
                else:
                    first_offset = 0.0
            anchor_iso = cache.get("registered_at") or cache.get("first_tick_anchor")
            anchor = parse_iso(anchor_iso) if anchor_iso else now
            return now >= (anchor + timedelta(seconds=float(first_offset)))
        last_dt = parse_iso(last_run_at)
        if last_dt is None:
            return True
        interval = effective_interval_seconds(cache, spec)
        return (now - last_dt).total_seconds() >= interval

    async def _dispatch_one(self, job_id: str, now: datetime) -> None:
        cache = self.cache[job_id]
        spec = cache["spec"]
        started_iso = now_iso()
        started_dt = datetime.now(timezone.utc)

        # P3 T3.5: re-registration cancel — if a re-registration landed
        # while this job was in-flight, audit as SKIPPED_REREGISTERED.
        if cache.get("cancel_requested") or cache.get("reregistered"):
            cache["cancel_requested"] = False
            # fall through to stamp below
            prev_last_run_at = cache.get("last_run_at")
            await self._stamp(
                job_id, cache, spec, started_iso, "SKIPPED_REREGISTERED",
                "REREGISTERED", started_dt, None, 0, None,
            )
            cache["last_run_at"] = prev_last_run_at
            cache["skips_by_reason"]["REREGISTERED"] = (
                cache["skips_by_reason"].get("REREGISTERED", 0) + 1
            )
            cache["reregistered"] = False
            return

        # P3 T3.1: dependency resolution at due-time (getter, not ctor capture)
        deps = self.engine.get_dependencies(job_id) if hasattr(self.engine, "get_dependencies") else []
        if deps:
            from daemon.services.engine.guardrails import resolve_at_due
            result = resolve_at_due(deps)
            if result.missing:
                # counted every tick
                cache["skips_by_reason"]["DEP_MISSING"] = (
                    cache["skips_by_reason"].get("DEP_MISSING", 0) + 1
                )
                tracker = getattr(self.engine, "_dep_missing_tracker", None)
                now_ts = started_dt.timestamp()
                interval = effective_interval_seconds(cache, spec)
                escalated = False
                if tracker is not None:
                    tracker.record_missing(job_id, now_ts)
                    escalated = tracker.should_escalate(job_id, interval, now_ts)
                # stamp SKIPPED_DEP_MISSING
                prev_last_run_at = cache.get("last_run_at")
                dead_val = 1 if escalated else None
                if escalated:
                    # exactly ONE ERROR line
                    esc_set = getattr(self.engine, "_dep_missing_escalated", set())
                    from daemon.services.engine.guardrails import escalate_dead_log_once
                    escalate_dead_log_once(
                        job_id, esc_set,
                        level="error",
                        message=(
                            f"engine job {job_id} escalated to dead: "
                            f"dependency missing for {DEP_MISSING_DEAD_FACTOR}x interval "
                            f"({result.missing})"
                        ),
                    )
                    self.engine._dep_missing_escalated = esc_set
                await self._stamp(
                    job_id, cache, spec, started_iso, "SKIPPED_DEP_MISSING",
                    "DEP_MISSING", started_dt, None, 0, None,
                )
                if dead_val is not None:
                    # persist dead=1 via the one-write path already stamped;
                    # re-stamp dead column
                    try:
                        await asyncio.to_thread(
                            self.repository.stamp_job_outcome,
                            job_id, dead=dead_val,
                        )
                    except Exception:  # noqa: BLE001
                        pass
                return
            else:
                tracker = getattr(self.engine, "_dep_missing_tracker", None)
                if tracker is not None:
                    tracker.record_resolved(job_id)

        # single-flight
        acquired = await self.locks.try_acquire(job_id)
        if not acquired:
            # D2.7: while an extended dispatch still holds the lock the
            # per-tick overlap is the SPECIFIC TPE member, not generic OVERLAP.
            extended = self.locks.is_extended(job_id)
            reason = REASON_TIMEOUT_PENDING_EXTEND if extended else "OVERLAP"
            # An overlap is not a real run: preserve the in-flight job's due
            # clock so it stays reachable by each subsequent tick.
            prev_last_run_at = cache.get("last_run_at")
            await self._stamp(
                job_id, cache, spec, started_iso, skip_outcome(reason), reason,
                started_dt, None, 0, None,
            )
            cache["last_run_at"] = prev_last_run_at
            cache["skips_by_reason"][reason] = cache["skips_by_reason"].get(reason, 0) + 1
            return

        if cache.get("cancel_requested"):
            # Re-registration cancelled this dispatch before it started running.
            prev_last_run_at = cache.get("last_run_at")
            await self._stamp(
                job_id, cache, spec, started_iso, "SKIPPED_REREGISTERED",
                "REREGISTERED", started_dt, None, 0, None,
            )
            cache["last_run_at"] = prev_last_run_at
            cache["skips_by_reason"]["REREGISTERED"] = (
                cache["skips_by_reason"].get("REREGISTERED", 0) + 1
            )
            cache["cancel_requested"] = False
            cache["reregistered"] = False
            self.locks.release(job_id)
            return

        cache["running"] = True
        try:
            # shadow posture / withheld execute_fn
            executor = self.engine._executors.get(job_id)
            shadow = self.shadow_mode or executor is None
            if shadow:
                reason = "SHADOW"
                await self._stamp(
                    job_id, cache, spec, started_iso, skip_outcome(reason), reason,
                    started_dt, None, 0, None,
                )
                cache["skips_by_reason"][reason] = cache["skips_by_reason"].get(reason, 0) + 1
                # consecutive skips for idle-gate? SHADOW does not count toward backoff
                self._update_consecutive_skips(cache, reason, is_skip=True, is_run=False)
                return

            # dispatch by class
            conc = getattr(spec, "concurrency_class", "DB_OFFLOAD")
            max_runtime = float(getattr(spec, "max_runtime_s", 30.0))
            policy = getattr(spec, "timeout_policy", "cancel")
            retry_max = int(getattr(spec, "retry_max", 0) or 0)
            backoff_base = getattr(spec, "retry_backoff_base_s", None)
            backoff_cap = getattr(spec, "retry_backoff_cap_s", None)
            dedicated = getattr(self.engine, "_dedicated_executor", None)

            async def attempt(attempt_no):
                async def _dispatch():
                    if conc == "MAIN_LOOP":
                        return await D.run_main_loop(executor)
                    elif conc == "DB_OFFLOAD":
                        return await D.run_db_offload(executor)
                    else:
                        return await D.run_dedicated_executor(dedicated, executor)

                async def on_extend_fire():
                    # D2.7 fire-time event: one TPE run row + counter + job
                    # stamp, and mark the held dispatch as extended so later
                    # ticks stamp TPE rather than OVERLAP.
                    await self._stamp_extend_fire(job_id, cache, spec, started_iso)

                _extend_ceiling = None
                try:
                    _extend_ceiling = float(getattr(self.engine.config, "extend_ceiling_seconds", 30.0))
                except Exception:  # noqa: BLE001
                    _extend_ceiling = 30.0
                outcome, exc = await D.run_with_timeout(
                    lambda: _dispatch(),
                    max_runtime,
                    policy,
                    on_extend_tick=on_extend_fire,
                    extend_ceiling_s=_extend_ceiling,
                )
                if outcome == "TIMED_OUT":
                    raise TimeoutError("engine job timed out")
                # C-1 fix: an extend-policy late failure must propagate so the
                # retry/terminal machinery records FAILED_BACKOFF + error_class.
                if outcome == OUTCOME_TIMEOUT_PENDING_EXTEND and exc is not None:
                    raise exc
                if outcome not in ("ok", OUTCOME_TIMEOUT_PENDING_EXTEND):
                    raise RuntimeError(f"dispatch outcome {outcome}")
                return outcome

            # retry loop
            _max_delay = None
            try:
                _max_delay = float(getattr(self.engine.config, "retry_backoff_cap_seconds", 30.0))
            except Exception:  # noqa: BLE001
                _max_delay = 30.0
            retry_outcome, attempts, last_exc = await D.run_with_retry(
                attempt, retry_max, backoff_base, backoff_cap,
                max_delay_s=_max_delay,
            )

            finished_iso = now_iso()
            finished_dt = datetime.now(timezone.utc)
            duration = (finished_dt - started_dt).total_seconds()
            if retry_outcome == "SUCCEEDED":
                await self._stamp(
                    job_id, cache, spec, started_iso, "SUCCEEDED", None,
                    finished_dt, duration, attempts, None, 0,
                )
                cache["last_duration_seconds"] = duration
                cache["last_outcome"] = "SUCCEEDED"
                cache["last_skip_reason"] = None
                cache["last_run_at"] = finished_iso
                self._update_consecutive_skips(cache, None, is_skip=False, is_run=True)
            else:
                # FAILED_BACKOFF
                err_cls = type(last_exc).__name__ if last_exc is not None else None
                await self._stamp(
                    job_id, cache, spec, started_iso, "FAILED_BACKOFF", None,
                    finished_dt, duration, attempts, err_cls,
                )
                cache["last_duration_seconds"] = duration
                cache["last_outcome"] = "FAILED_BACKOFF"
                cache["last_skip_reason"] = None
                cache["last_run_at"] = finished_iso
                self._update_consecutive_skips(cache, None, is_skip=False, is_run=True)
        finally:
            cache["running"] = False
            cache["cancel_requested"] = False
            # extend policy keeps lock held until actual finish — already here
            self.locks.release(job_id)
            # clear pending event latch for event triggers
            cache["pending_event"] = False

    async def _stamp_extend_fire(self, job_id, cache, spec, started_iso) -> None:
        """D2.7 fire-time observable event — NOT the terminal outcome.

        Called once by ``run_with_timeout`` when the extend-policy wait_for
        fires. Writes one TPE row through the one-write path, bumps the TPE
        counter, sets the job's last_skip_reason to TPE, and marks the held
        lock as extended so subsequent ticks stamp TPE (not OVERLAP). The
        eventual completion still stamps the truthful terminal row.
        """
        fire_dt = datetime.now(timezone.utc)
        # The fire event is NOT a terminal run: preserve the due clock so the
        # still-running job stays due and later ticks reach the lock-held
        # branch (which stamps TPE while extended). The eventual terminal
        # stamp is the one that advances last_run_at / next-due.
        prev_last_run_at = cache.get("last_run_at")
        await self._stamp(
            job_id, cache, spec, started_iso, OUTCOME_TIMEOUT_PENDING_EXTEND,
            REASON_TIMEOUT_PENDING_EXTEND, fire_dt, None, 1, None,
        )
        cache["last_run_at"] = prev_last_run_at
        cache["skips_by_reason"][REASON_TIMEOUT_PENDING_EXTEND] = (
            cache["skips_by_reason"].get(REASON_TIMEOUT_PENDING_EXTEND, 0) + 1
        )
        self.locks.mark_extended(job_id, True)
        # P3 T3.6: track the extend hold for wedge escalation (2 x max_runtime_s)
        tracker = getattr(self.engine, "_extend_wedge_tracker", None)
        if tracker is not None:
            try:
                max_runtime = float(getattr(spec, "max_runtime_s", 30.0))
            except (TypeError, ValueError):
                max_runtime = 30.0
            tracker.record_hold_start(job_id, fire_dt.timestamp(), max_runtime)

    async def _stamp(
        self,
        job_id,
        cache,
        spec,
        started_iso,
        outcome,
        reason,
        finished_dt,
        duration,
        attempt,
        err_cls,
        dead=None,
    ):
        skip_reason = reason if (reason and outcome.startswith("SKIPPED_")) else None
        # reason may be the bare reason token; outcome is full SKIPPED_*
        if outcome.startswith("SKIPPED_") and skip_reason is None:
            skip_reason = outcome[len("SKIPPED_"):]
        if reason in ("IDLE_GATED", "KILL_SWITCHED", "DEP_MISSING", "SHADOW", "OVERLAP",
                      REASON_TIMEOUT_PENDING_EXTEND, "ERROR_BACKOFF", "NOT_DUE"):
            skip_reason = reason
        # consecutive_skips handling
        cs = int(cache.get("consecutive_skips", 0) or 0)
        if outcome.startswith("SKIPPED_") and reason in IDLE_GATE_SKIP_REASONS:
            cs = cs + 1
        elif not outcome.startswith("SKIPPED_"):
            cs = 0
        cache["consecutive_skips"] = cs
        cache["last_run_at"] = finished_dt.isoformat() if hasattr(finished_dt, "isoformat") else str(finished_dt)
        # use started_iso? we use finished time for last_run_at stamp (terminal)
        stamp_finished = finished_dt.isoformat() if hasattr(finished_dt, "isoformat") else str(finished_dt)
        started_s = started_iso
        stamp = TerminalStamp(
            job_id=job_id,
            service_tag=getattr(spec, "service_tag", None) or getattr(getattr(spec, "meta", None), "service_tag", None),
            started_at=started_s,
            finished_at=stamp_finished,
            outcome=outcome,
            skip_reason=skip_reason,
            duration_seconds=duration,
            attempt=attempt or 1,
            error_class=err_cls,
            boot_id=self.boot_id,
            host_pid=self.host_pid,
            consecutive_skips=cs,
            dead=dead,
        )
        # off-loop DB write
        await asyncio.to_thread(stamp_terminal, self.repository, stamp)
        # cache follows DB
        cache["last_outcome"] = outcome
        cache["last_skip_reason"] = skip_reason
        if dead is not None:
            cache["persisted_dead"] = bool(dead)
        # D2.15/CS-9: timestamped shadow evidence for the observer-dead window.
        # Kept in RAM like the rest of D2.11/D2.15 — no per-tick DB reads.
        if reason == "SHADOW" or outcome == "SKIPPED_SHADOW":
            cache.setdefault("shadow_evidence_ts", []).append(stamp_finished)

    def _update_consecutive_skips(self, cache, reason, is_skip, is_run):
        # already handled in _stamp; kept for compat
        pass

    def ingest_event(self, kind: str, payload: Any = None) -> None:
        """Event-trigger seam (D2.11). P5 invokes this; sets pending latch."""
        for job_id, cache in self.cache.items():
            spec = cache.get("spec")
            trigger = getattr(spec, "trigger", None) if spec is not None else None
            tkind = getattr(trigger, "trigger_kind", None)
            if tkind != "event":
                continue
            if getattr(trigger, "kind", None) != kind:
                continue
            # filter subset match (best effort)
            flt = getattr(trigger, "filter", None)
            if flt:
                ok = True
                for k, v in flt.items():
                    if payload is None or payload.get(k) != v:
                        ok = False
                        break
                if not ok:
                    continue
            cache["pending_event"] = True

    def set_executor(self, job_id: str, fn: Callable) -> None:
        self.engine._executors[job_id] = fn


__all__ = ["EngineExecutionLoop"]
