"""EngineService (Engine Arc P2 — T2.5).

Construct / start / stop. start() idempotent; hydrates cache from
list_jobs() at start; task name 'engine-tick'; stop() cancels + awaits
within a bounded 5s budget (orphan_watcher precedent).
No lifespan wiring (P4), no api.py touch.
"""

from __future__ import annotations

import asyncio
import logging
import os
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Optional

from daemon.repositories.engine.models import EngineJob
from daemon.services.engine.locks import JobLockTable
from daemon.services.engine.metrics import EngineMetrics
from daemon.services.engine.state import now_iso, parse_iso
from daemon.services.engine.execution import EngineExecutionLoop

logger = logging.getLogger(__name__)


class EngineService:
    def __init__(
        self,
        repository: Any,
        registry: Any = None,
        config: Any = None,
        dedicated_executor: Optional[ThreadPoolExecutor] = None,
        shadow_mode: Optional[bool] = None,
    ) -> None:
        self.repository = repository
        self.registry = registry
        self.config = config
        self.shadow_mode = (
            shadow_mode if shadow_mode is not None
            else getattr(config, "shadow_mode", True)
        )
        tick_workers = getattr(config, "tick_workers", 2) if config is not None else 2
        self._dedicated_executor = dedicated_executor or ThreadPoolExecutor(
            max_workers=max(1, int(tick_workers)), thread_name_prefix="engine-dedicated"
        )
        self._locks = JobLockTable()
        self._cache: dict[str, dict] = {}
        self._executors: dict[str, Callable] = {}
        self._task: asyncio.Task | None = None
        self._stopping: bool = False
        self._boot_id: str = uuid.uuid4().hex
        self._execution_loop = EngineExecutionLoop(self)
        self.metrics = EngineMetrics(self)
        self._started: bool = False

    # ── introspection ────────────────────────────────────────────────
    @property
    def locks(self) -> JobLockTable:
        return self._locks

    @property
    def _cache_view(self):
        return self._cache

    # ── lifecycle ────────────────────────────────────────────────────
    async def start(self) -> None:
        if self._task is not None and not self._task.done():
            return  # idempotent
        self._stopping = False
        await self._hydrate()
        self._task = asyncio.create_task(
            self._execution_loop.run(), name="engine-tick"
        )
        self._started = True
        logger.info(
            "engine tick loop started: resolution=%s shadow_mode=%s",
            getattr(self.config, "tick_resolution_seconds", 1.0),
            self.shadow_mode,
        )

    async def stop(self, timeout: float = 5.0) -> None:
        self._stopping = True
        task = self._task
        if task is not None:
            task.cancel()
            try:
                await asyncio.wait_for(task, timeout=timeout)
            except (asyncio.CancelledError, asyncio.TimeoutError):
                pass
            self._task = None
        try:
            self._dedicated_executor.shutdown(wait=False)
        except Exception:  # noqa: BLE001
            pass

    # ── hydration (D2.12) ────────────────────────────────────────────
    async def _hydrate(self) -> None:
        jobs = await asyncio.to_thread(self.repository.list_jobs)
        for job in jobs:
            if job.id in self._cache:
                continue
            spec = self._resolve_spec(job)
            entry = self._make_cache_entry(job, spec)
            self._cache[job.id] = entry

    def _resolve_spec(self, job: EngineJob) -> Any:
        if self.registry is not None:
            try:
                spec = self.registry.cached_spec(job.id)
                if spec is not None:
                    return spec
            except Exception:  # noqa: BLE001
                pass
        return self._spec_from_row(job)

    def _spec_from_row(self, job: EngineJob) -> Any:
        # Best-effort reconstruction so hydration survives a restart where
        # the registry cache is empty. Uses the frozen spec module.
        try:
            from daemon.services.engine import spec as spec_mod
            from daemon.services.engine.spec import (
                IntervalTrigger,
                EventTrigger,
                JobSpec,
                ServiceMeta,
            )

            meta = ServiceMeta(
                service_tag=job.service_tag,
                category=job.category,
                service_name=job.service_name,
                module_info=job.module_info,
                description=job.description,
                metadata=dict(job.job_metadata or {}),
            )
            if job.trigger_kind == "event":
                trigger = EventTrigger(kind=job.event_kind or "", filter=job.event_filter)
            else:
                trigger = IntervalTrigger(
                    seconds=float(job.interval_seconds or 1.0),
                    jitter_seconds=job.jitter_seconds,
                    first_tick_kind=job.first_tick_kind,
                    first_tick_offset_seconds=job.first_tick_offset_seconds,
                )
            return JobSpec(
                id=job.id,
                meta=meta,
                trigger=trigger,
                max_runtime_s=float(job.max_runtime_s),
                timeout_policy=job.timeout_policy,
                concurrency_class=job.concurrency_class,
                priority=int(job.priority),
                retry_max=int(job.retry_max),
                retry_backoff_base_s=job.retry_backoff_base_s,
                retry_backoff_cap_s=job.retry_backoff_cap_s,
                kill_switch_key=job.kill_switch_key,
            )
        except Exception:  # noqa: BLE001
            return None

    def _make_cache_entry(self, job: EngineJob, spec: Any) -> dict:
        return {
            "version": getattr(job, "version", 1),
            "fingerprint": None,
            "spec": spec,
            "last_run_at": job.last_run_at,
            "consecutive_skips": int(job.consecutive_skips or 0),
            "last_outcome": job.last_outcome,
            "last_skip_reason": job.last_skip_reason,
            "last_duration_seconds": None,
            "running": False,
            "skips_by_reason": {},
            # CS-9: seed shadow evidence from the row hydration already has.
            # last_outcome/last_run_at describe the latest audit row, so when
            # that row is a SKIPPED_SHADOW its timestamp is the freshest proof.
            "shadow_evidence_ts": (
                [job.last_run_at]
                if job.last_outcome == "SKIPPED_SHADOW" and job.last_run_at
                else []
            ),
            "would_fire": False,
            "pending_event": False,
            "registered_at": job.registered_at,
            "first_tick_anchor": job.registered_at,
        }

    # ── seams ────────────────────────────────────────────────────────
    def ingest_event(self, kind: str, payload: Any = None) -> None:
        """Event-trigger seam (D2.11). P5 invokes this later."""
        self._execution_loop.ingest_event(kind, payload)

    def set_executor(self, job_id: str, fn: Callable) -> None:
        self._executors[job_id] = fn

    def add_job_spec(self, spec: Any, executor: Optional[Callable] = None) -> None:
        """In-memory spec registration (tests). Persistence is the caller's job."""
        entry = self._make_cache_entry_from_spec(spec)
        self._cache[spec.id] = entry
        if executor is not None:
            self._executors[spec.id] = executor

    def _make_cache_entry_from_spec(self, spec: Any) -> dict:
        return {
            "version": 1,
            "fingerprint": None,
            "spec": spec,
            "last_run_at": None,
            "consecutive_skips": 0,
            "last_outcome": None,
            "last_skip_reason": None,
            "last_duration_seconds": None,
            "running": False,
            "skips_by_reason": {},
            "shadow_evidence_ts": [],
            "would_fire": False,
            "pending_event": False,
            "registered_at": None,
            "first_tick_anchor": None,
        }

    def set_executor_and_seed(self, job_id: str, fn: Callable) -> None:
        self._executors[job_id] = fn


__all__ = ["EngineService"]
