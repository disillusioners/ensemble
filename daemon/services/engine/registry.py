"""Engine registration API — the REGISTRATION half only (Engine Arc P1, T1.8).

Scope (plan: ``.agents/shared/planning/engine-arc-phase1/phase1-plan.md``
T1.8, section 2 "In"): validate → upsert by id → version bump → RAM cache
insert → return the stored version. **The execution half of dynamic
re-registration (cancelling an in-flight tick) is P3's** — it needs the
execution core, which does not exist in Phase 1.

Contract (AC5):

* A FIRST registration stores ``version == 1``.
* Re-registering IDENTICAL content is a **no-op echo** — the stored
  version is returned unchanged and the DB row is not touched at all
  (no version bump, no ``updated_at`` churn).
* Re-registering CHANGED content yields ``version == 2`` (and onward) and
  moves ``updated_at``; the live last-outcome surface
  (``last_run_at`` / ``last_outcome`` / ``last_skip_reason`` /
  ``consecutive_skips`` / ``dead``) is PRESERVED across the re-registration
  — a re-registration never resets run history.
* A spec that violates any rule raises :class:`EngineSpecError` **before**
  any DB write — a rejected registration never leaves a partial row.

Loop discipline (D1.11 — the off-loop house rule v0.27.14):

* :meth:`EngineRegistry.aregister` is the NATIVE coroutine. It validates on
  the loop, then performs the DB write through ``asyncio.to_thread`` so a
  boot-time registration inside the lifespan never blocks the event loop.
  **This is the call site P2/P4 must use.**
* :meth:`EngineRegistry.register` is the THIN SYNC WRAPPER for pre-loop
  contexts (module-scope registration, unit tests). It runs the same
  implementation inline — no loop, no to_thread. It raises if called from a
  running loop, naming ``aregister`` as the correct entry point, because a
  blocking sync DB write on a live loop is exactly the discipline this
  wrapper exists to prevent.

The repository is the only writer of the two engine tables (see
``daemon/repositories/engine/repository.py``); this module never touches a
session itself.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

from daemon.repositories.engine.models import EngineJob
from daemon.services.engine.spec import JobSpec, validate_spec
from daemon.services.timestamps import now_utc_iso

if TYPE_CHECKING:  # pragma: no cover — typing only
    from daemon.repositories.engine.repository import EngineRepository

__all__ = ["EngineRegistry"]

logger = logging.getLogger(__name__)


class EngineRegistry:
    """The registration API over ``engine_jobs`` (registration half only).

    Args:
        repository: The engine repository (``EngineRepository``). The
            registry is the only caller that upserts registrations; the
            repository stays the only writer of the tables.
    """

    def __init__(self, repository: "EngineRepository") -> None:
        self._repository = repository
        # RAM cache — the hot-read half of the DB-source-of-truth contract.
        # job_id -> {"version": int, "fingerprint": tuple, "spec": JobSpec}
        self._cache: dict[str, dict[str, Any]] = {}

    # ── introspection ──────────────────────────────────────────────────

    @property
    def cached_ids(self) -> tuple[str, ...]:
        """The ids currently held in the RAM cache (sorted, stable)."""
        return tuple(sorted(self._cache))

    def cached_version(self, job_id: str) -> int | None:
        """The cached version for ``job_id``, or ``None`` when unregistered."""
        entry = self._cache.get(job_id)
        return None if entry is None else int(entry["version"])

    def cached_spec(self, job_id: str) -> JobSpec | None:
        """The cached spec for ``job_id``, or ``None``."""
        entry = self._cache.get(job_id)
        return None if entry is None else entry["spec"]

    # ── async registration (native — the call site P2/P4 uses) ─────────

    async def aregister(self, spec: JobSpec) -> int:
        """Register ``spec``, off-loading the DB write. Returns the version.

        Validation runs on the loop (cheap, pure); the DB upsert runs in a
        worker thread via ``asyncio.to_thread`` (the estate idiom, D1.11).

        Raises:
            EngineSpecError: The spec violated a substrate rule. No DB
                write happens in that case.
        """
        validate_spec(spec)
        return await asyncio.to_thread(self._register_sync, spec)

    # ── sync wrapper (pre-loop contexts only) ──────────────────────────

    def register(self, spec: JobSpec) -> int:
        """Register ``spec`` from a SYNC, loop-free caller. Returns the version.

        This is the thin wrapper for pre-loop registration contexts (module
        scope, unit tests). On a live event loop the correct entry point is
        ``await aregister(spec)`` — a blocking sync DB write on the loop is
        exactly what the off-loop discipline forbids, so this wrapper
        refuses rather than silently blocking.

        Raises:
            RuntimeError: Called while an event loop is running in this
                thread (use ``await aregister(spec)`` instead).
            EngineSpecError: The spec violated a substrate rule.
        """
        validate_spec(spec)
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return self._register_sync(spec)
        raise RuntimeError(
            "EngineRegistry.register() must not be called from a running "
            "event loop (it blocks on DB I/O). Use `await "
            f"aregister(spec)` instead (job_id={spec.id!r})."
        )

    # ── the shared implementation (validation already ran) ─────────────

    def _register_sync(self, spec: JobSpec) -> int:
        """Persist a validated spec: version arithmetic + cache insert."""
        fingerprint = spec.content_fingerprint()
        cached = self._cache.get(spec.id)

        # Identical re-registration — no-op echo (AC5). No DB write.
        if cached is not None and cached["fingerprint"] == fingerprint:
            return int(cached["version"])

        existing = self._repository.get_job(spec.id)
        if existing is None:
            version = 1
            registered_at = now_utc_iso()
        else:
            version = int(existing.version) + 1
            registered_at = existing.registered_at or now_utc_iso()

        now = now_utc_iso()
        row_kwargs = spec.to_row_dict()
        row_kwargs.update(
            {
                "version": version,
                "registered_at": registered_at,
                "updated_at": now,
                # Registration never resets run history: preserve the live
                # last-outcome surface when a row already exists.
                "last_run_at": None if existing is None else existing.last_run_at,
                "last_outcome": None if existing is None else existing.last_outcome,
                "last_skip_reason": (
                    None if existing is None else existing.last_skip_reason
                ),
                "consecutive_skips": (
                    0 if existing is None else existing.consecutive_skips
                ),
                "dead": 0 if existing is None else existing.dead,
            }
        )

        stored = self._repository.upsert_job(EngineJob(**row_kwargs))
        stored_version = int(stored.version)
        self._cache[spec.id] = {
            "version": stored_version,
            "fingerprint": fingerprint,
            "spec": spec,
        }
        logger.info(
            "engine job registered: id=%s service_tag=%s category=%s version=%d",
            spec.id,
            spec.meta.service_tag,
            spec.meta.category,
            stored_version,
        )
        return stored_version
