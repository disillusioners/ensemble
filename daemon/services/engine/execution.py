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
)
from daemon.services.engine import dispatch as D

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
        """The supervised loop. Unexpected errors re-raise; cancel exits cleanly."""
        try:
            while not getattr(self.engine, "_stopping", False):
                try:
                    await self._tick()
                except asyncio.CancelledError:
                    raise
                except Exception as exc:  # noqa: BLE001 — F-7: log + re-raise
                    logger.error(
                        "engine tick loop: unexpected error %s: %s — re-raising",
                        type(exc).__name__,
                        exc,
                        exc_info=True,
                    )
                    raise
                await asyncio.sleep(self.resolution)
        except asyncio.CancelledError:
            logger.debug("engine tick loop: cancelled (shutdown)")
            return

    async def _tick(self) -> None:
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
            first_kind = getattr(trigger, "first_tick_kind", None)
            if first_kind == "delayed":
                offset = getattr(trigger, "first_tick_offset_seconds", None) or 0.0
                # anchor: registered_at from DB row or now
                anchor_iso = cache.get("registered_at") or cache.get("first_tick_anchor")
                anchor = parse_iso(anchor_iso) if anchor_iso else now
                return now >= (anchor + timedelta(seconds=offset))
            # immediate or None → due on first tick
            return True
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
        # single-flight
        acquired = await self.locks.try_acquire(job_id)
        if not acquired:
            # SKIPPED_OVERLAP
            await self._stamp(
                job_id, cache, spec, started_iso, "SKIPPED_OVERLAP", "OVERLAP",
                started_dt, None, 0, None,
            )
            cache["skips_by_reason"]["OVERLAP"] = cache["skips_by_reason"].get("OVERLAP", 0) + 1
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
                outcome, exc = await D.run_with_timeout(
                    lambda: _dispatch(),
                    max_runtime,
                    policy,
                )
                if outcome == "TIMED_OUT":
                    raise TimeoutError("engine job timed out")
                if outcome == "TIMEOUT_PENDING_EXTEND":
                    # record pending extend skip-reason but continue; final stamp below
                    cache["skips_by_reason"]["TIMEOUT_PENDING_EXTEND"] = (
                        cache["skips_by_reason"].get("TIMEOUT_PENDING_EXTEND", 0) + 1
                    )
                if outcome not in ("ok", "TIMEOUT_PENDING_EXTEND"):
                    raise RuntimeError(f"dispatch outcome {outcome}")
                return outcome

            # retry loop
            retry_outcome, attempts, last_exc = await D.run_with_retry(
                attempt, retry_max, backoff_base, backoff_cap
            )

            finished_iso = now_iso()
            finished_dt = datetime.now(timezone.utc)
            duration = (finished_dt - started_dt).total_seconds()
            if retry_outcome == "SUCCEEDED":
                await self._stamp(
                    job_id, cache, spec, started_iso, "SUCCEEDED", None,
                    finished_dt, duration, attempts, None,
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
            # extend policy keeps lock held until actual finish — already here
            self.locks.release(job_id)
            # clear pending event latch for event triggers
            cache["pending_event"] = False

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
    ):
        skip_reason = reason if (reason and outcome.startswith("SKIPPED_")) else None
        # reason may be the bare reason token; outcome is full SKIPPED_*
        if outcome.startswith("SKIPPED_") and skip_reason is None:
            skip_reason = outcome[len("SKIPPED_"):]
        if reason in ("IDLE_GATED", "KILL_SWITCHED", "DEP_MISSING", "SHADOW", "OVERLAP",
                      "TIMEOUT_PENDING_EXTEND", "ERROR_BACKOFF", "NOT_DUE"):
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
        )
        # off-loop DB write
        await asyncio.to_thread(stamp_terminal, self.repository, stamp)
        # cache follows DB
        cache["last_outcome"] = outcome
        cache["last_skip_reason"] = skip_reason

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

    def add_job_spec(self, spec: Any, executor: Optional[Callable] = None) -> None:
        """Register a spec into the cache (used by tests / hydration)."""
        # persisted? assume already persisted
        entry = self.engine._make_cache_entry(spec)
        self.cache[spec.id] = entry
        if executor is not None:
            self.engine._executors[spec.id] = executor


__all__ = ["EngineExecutionLoop"]
