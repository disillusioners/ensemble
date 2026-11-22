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
import json
import logging
from typing import TYPE_CHECKING, Any

from daemon.repositories.engine.models import EngineJob
from daemon.services.engine.spec import JobSpec, validate_spec
from daemon.services.timestamps import now_utc_iso

if TYPE_CHECKING:  # pragma: no cover — typing only
    from daemon.repositories.engine.repository import EngineRepository

__all__ = ["EngineRegistry"]

logger = logging.getLogger(__name__)

#: The registration-CONTENT columns of an ``engine_jobs`` row — the fields
#: that define WHAT is registered. The no-op echo compares exactly these
#: (DB row vs the candidate shape ``JobSpec.to_row_dict()`` would persist).
#: Deliberately EXCLUDED: ``version``, ``last_run_at``, ``last_outcome``,
#: ``last_skip_reason``, ``consecutive_skips``, ``dead`` (runtime/last-outcome
#: state) and ``registered_at`` / ``updated_at`` (timestamps). A change to
#: any of those must NOT be read as "the registration content changed".
_REGISTRATION_CONTENT_COLUMNS: tuple[str, ...] = (
    "service_tag",
    "service_name",
    "category",
    "module_info",
    "description",
    "job_metadata",  # JSON-backed (model attr for the ``metadata`` column)
    "trigger_kind",
    "interval_seconds",
    "jitter_seconds",
    "first_tick_kind",
    "first_tick_offset_seconds",
    "event_kind",
    "event_filter",  # JSON-backed
    "max_runtime_s",
    "timeout_policy",
    "retry_max",
    "retry_backoff_base_s",
    "retry_backoff_cap_s",
    "priority",
    "concurrency_class",
    "kill_switch_key",
    "enabled",
)

#: JSON-backed columns: compare parsed-equal, not string-equal, so a JSON
#: key-order / serialization difference cannot fake a content change.
_JSON_CONTENT_COLUMNS = frozenset({"job_metadata", "event_filter"})


def _json_normalized(value: Any) -> Any:
    """Parse a JSON-backed column value so serialization order cannot matter.

    Depending on the backend a JSONB column reads back as a dict or as JSON
    text. Parsing text first makes both shapes compare by VALUE. Floats and
    scalars are left untouched ("compare as stored").
    """
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (ValueError, TypeError):
            return value
    return value


def _registration_content_matches(
    existing: EngineJob, candidate: dict[str, Any]
) -> bool:
    """True when ``candidate`` would write no registration-content change.

    Grounds the no-op echo on the DB row (D1.5 "answerable from the DB
    alone"): a fresh registry with an empty RAM cache still echoes an
    identical spec instead of turning ``version`` into a boot counter (D-2).
    """
    for column in _REGISTRATION_CONTENT_COLUMNS:
        stored = getattr(existing, column)
        proposed = candidate.get(column)
        if column in _JSON_CONTENT_COLUMNS:
            stored = _json_normalized(stored)
            proposed = _json_normalized(proposed)
        if stored != proposed:
            return False
    return True


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
        # P3 T3.5: optional cancel hook invoked on a changed-content
        # re-registration of a live (in-flight) job. Set by EngineService.
        self._cancel_callback: Any = None

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

    # ── re-registration cancel hook (P3 T3.5) ──────────────────────────
    @property
    def cancel_callback(self) -> Any:
        return self._cancel_callback

    def set_cancel_callback(self, cb: Any) -> None:
        self._cancel_callback = cb

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

        # Ground the echo comparison on the DB row ALWAYS (P3 fold #4):
        # registrations are rare; correctness > cache. An out-of-band row
        # mutation followed by a same-spec register() in the SAME process
        # must BUMP (not echo) — the RAM fast path is removed so a stale
        # cached verdict can never mask a DB-side change.
        existing = self._repository.get_job(spec.id)
        row_kwargs = spec.to_row_dict()

        # DB-grounded no-op echo (D-2 / D1.5). After a restart the RAM cache
        # is empty; the same spec must still echo the stored version when
        # the persisted registration content matches, otherwise P4's
        # boot-time registration of N observer specs turns ``version`` into
        # a boot counter and moves ``updated_at`` on every boot. Compare
        # ONLY the registration-content columns — run history and
        # timestamps are not part of "same registration".
        if existing is not None and _registration_content_matches(
            existing, row_kwargs
        ):
            version = int(existing.version)
            # F4 echo-revival: re-registration of a dead job is revival intent.
            # Perform a targeted dead=0 update (version/updated_at UNTOUCHED
            # — still an echo) via the repository.  D3.4 body stands.
            if getattr(existing, "dead", 0):
                try:
                    self._repository.revive_job(spec.id)
                except Exception:  # noqa: BLE001
                    logger.exception("echo-revival failed for %s", spec.id)
            # Refresh the RAM cache for hot reads; NO DB write otherwise, so
            # version and updated_at are left exactly as stored.
            self._cache[spec.id] = {
                "version": version,
                "fingerprint": fingerprint,
                "spec": spec,
            }
            return version

        if existing is None:
            version = 1
            registered_at = now_utc_iso()
        else:
            version = int(existing.version) + 1
            registered_at = existing.registered_at or now_utc_iso()

        now = now_utc_iso()
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
                "dead": 0 if existing is None else 0,
            }
        )

        changed = existing is not None
        stored = self._repository.upsert_job(EngineJob(**row_kwargs))
        stored_version = int(stored.version)
        self._cache[spec.id] = {
            "version": stored_version,
            "fingerprint": fingerprint,
            "spec": spec,
        }
        # P3 T3.5: dynamic re-registration of a live (in-flight) job.
        if changed:
            # pending_event latch clear is routed through the ENGINE cache by
            # EngineService._cancel_inflight_tick (F3) — the registry never
            # owns the live latch.  Invoke the cancel hook to drive it.
            cb = self._cancel_callback
            if cb is not None:
                try:
                    cb(spec.id)
                except Exception:  # noqa: BLE001
                    logger.exception("re-registration cancel hook failed for %s", spec.id)
        logger.info(
            "engine job registered: id=%s service_tag=%s category=%s version=%d",
            spec.id,
            spec.meta.service_tag,
            spec.meta.category,
            stored_version,
        )
        return stored_version
