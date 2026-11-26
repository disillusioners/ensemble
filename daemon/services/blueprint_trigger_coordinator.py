"""C7 — Unified Blueprint Trigger Coordinator.

A single chokepoint for all blueprint build enqueuing. Every trigger
surface (manual ``/rebuild``, ``/{blueprint_id}/rebuild``, ``/update``,
``/scan``, the daily maintenance scan, the high-water threshold) MUST
call ``coordinator.try_claim()`` before enqueuing a blueprinter job.
The coordinator guarantees:

* **Atomic project claim** — at most one build per project at a time.
* **Coalescing** — a second claim for the same mode returns the
  in-flight ``job_id`` instead of enqueuing a duplicate.
* **Cross-mode conflict** — a claim for a different mode (e.g.
  ``incremental`` while a ``rebuild`` is in flight, or ``single`` while
  any mode is in flight) returns ``conflict_mode`` so the caller can
  decide (e.g. 409).
* **Terminal release** — the blueprinter calls ``release()`` with its
  ``run_token`` on success/failure/cancel. The token reaches the agent
  in the ``[blueprint-trigger]`` block of the job message.
* **Daemon-side release** — the job-feedback observer releases the
  lease when the blueprinter job terminalizes, so an agent that never
  calls the tool cannot wedge the project.
* **Startup reconciliation** — on daemon start, leases whose backing
  job is gone (or already terminal) are released.
* **Absolute expiry** — a lease that outlives the TTL (default 180
  minutes, from ``claimed_at``) is released by the periodic sweep and
  is treated as absent by the next claim.

Supported modes
---------------

The coordinator is mode-string-generic — ``try_claim`` dispatches on
plain string equality (``existing.get("mode") == mode``). Three modes
are in active use:

* ``"rebuild"`` — full project-wide rebuild (``POST /rebuild``).
* ``"incremental"`` — process accumulated pending-experience records
  (``POST /update``).
* ``"single"`` — rebuild exactly ONE blueprint identified by
  ``metadata.blueprint_id`` (``POST /{blueprint_id}/rebuild``).
  Coalesces with itself (deduplicates rapid retries for the same
  project) and conflicts with ``"rebuild"`` / ``"incremental"``. Two
  ``"single"`` triggers for DIFFERENT blueprints coalesce onto the
  first — a known limitation; a per-``(project_id, blueprint_id)``
  lease is a follow-up.

Lease lifecycle
---------------

* **Claim** — on ``try_claim()``, the coordinator atomically acquires
  a lease for the project.
* **Release** — on job completion (success/failure/cancel), the
  blueprinter calls ``release(run_token)``. The token reaches the
  agent in the ``[blueprint-trigger]`` block of the job message.
* **Daemon-side release** — when the blueprinter job terminalizes,
  :class:`~daemon.services.job_feedback_observer.JobFeedbackObserver`
  releases the lease as a safety net (the agent may never call the
  tool).
* **Reconcile** — on daemon startup, ``reconcile_on_startup()`` scans
  for leases whose backing job is gone or already terminal and
  releases them.
* **Expiry (absolute TTL)** — a lease also expires on its own. The
  TTL is measured from the lease's ``claimed_at`` field (the lease
  dict shape is frozen — there is deliberately NO heartbeat field) and
  defaults to 180 minutes, tunable per run via the
  ``BLUEPRINT_LEASE_TTL_MINUTES`` env knob. An expired lease is
  treated as ABSENT by ``try_claim()`` — the next trigger reclaims
  the project without a daemon restart — and is released by the
  periodic ``sweep_expired_leases()`` pass (registered on the
  maintenance registry as ``blueprint_lease_sweep``).

There is no heartbeat and no LLM-dependent renewal: expiry is
absolute and derived from the claim timestamp alone, so a stuck
agent cannot keep a lease alive by accident.

Atomicity
---------

The claim is TOCTOU-safe enough for background-maintenance scale:
an ``asyncio.Lock`` per project serializes the read-check-write
critical section. Blueprint builds are infrequent (a few per day at
most), and the lock is per-project, so unrelated projects never
block each other. The DB row is the durable record; the lock
prevents in-process races between concurrent triggers (e.g. a
``/rebuild`` API call while the daily scan is firing).

.. note::

   Uses per-project ``asyncio.Lock`` for single-daemon coordination.
   DB-level ``ON CONFLICT DO NOTHING`` atomicity is a future enhancement
   for multi-daemon deployments. The current design is safe for the
   single-process deployment model.
"""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, TYPE_CHECKING
from uuid import uuid4

if TYPE_CHECKING:
    pass  # SQLModelProjectRepository is duck-typed; no import needed at runtime.

logger = logging.getLogger(__name__)


# ── Constants ────────────────────────────────────────────────────────────


#: Metadata key under which the lease dict is stored on the project row.
LEASE_META_KEY = "blueprint_build_lease"


#: Default ABSOLUTE lease TTL, in minutes (3 hours).
#:
#: The TTL is a LAST-RESORT backstop, not the primary unblock path: the
#: daemon-side release at job terminalization is immediate, and the
#: 30-minute sweep is near-term. 180 minutes matches the comparable
#: pending-queue lease reaper (``BLUEPRINT_LEASE_REAPER_*_LEASE_MINUTES``)
#: and clears the measured ceiling of a real blueprint rebuild with
#: worker fan-out, so a legitimately long build is never cut short.
DEFAULT_LEASE_TTL_MINUTES = 180


#: Env knob that overrides :data:`DEFAULT_LEASE_TTL_MINUTES`.
#:
#: Read PER RUN (never cached at import time) — the house convention for
#: operator knobs, so a config change takes effect on the next claim or
#: sweep without a daemon restart.
LEASE_TTL_ENV_VAR = "BLUEPRINT_LEASE_TTL_MINUTES"


def _lease_ttl_minutes() -> int:
    """Return the configured lease TTL in minutes.

    Read from the environment on EVERY call (per-run read, never cached).
    A malformed or non-positive value falls back to
    :data:`DEFAULT_LEASE_TTL_MINUTES` — an operator typo must not take
    the lease lane down, and a zero TTL would release every lease on
    the next claim.
    """
    raw = os.environ.get(LEASE_TTL_ENV_VAR)
    if raw is None or raw.strip() == "":
        return DEFAULT_LEASE_TTL_MINUTES
    try:
        value = int(raw.strip())
    except (TypeError, ValueError):
        logger.warning(
            "C7: %s=%r is not an integer — using the default %s minutes",
            LEASE_TTL_ENV_VAR, raw, DEFAULT_LEASE_TTL_MINUTES,
        )
        return DEFAULT_LEASE_TTL_MINUTES
    if value <= 0:
        logger.warning(
            "C7: %s=%r is not positive — using the default %s minutes",
            LEASE_TTL_ENV_VAR, raw, DEFAULT_LEASE_TTL_MINUTES,
        )
        return DEFAULT_LEASE_TTL_MINUTES
    return value


def _utcnow_iso() -> str:
    """Return current UTC time as an ISO-8601 string."""
    return datetime.now(timezone.utc).isoformat()


# ── Result dataclass ────────────────────────────────────────────────────


@dataclass
class ClaimResult:
    """Outcome of :meth:`BlueprintTriggerCoordinator.try_claim`.

    Attributes:
        claimed: True only when this caller acquired the lease.
        job_id: The ``job_id`` the caller should enqueue. Equals the
            caller's own ``job_id`` on a fresh claim; equals the
            in-flight job's id on a coalesce.
        coalesced: True when an active build of the same mode was
            found; caller should NOT enqueue.
        conflict_mode: Set when a build of a different mode is in
            flight; caller should reject with HTTP 409 or equivalent.
        run_token: The opaque lease token; the caller (the blueprinter
            job) must pass this back to ``release()``.
    """

    claimed: bool
    job_id: str | None = None
    coalesced: bool = False
    conflict_mode: str | None = None
    run_token: str | None = None


# ── Coordinator ─────────────────────────────────────────────────────────


class BlueprintTriggerCoordinator:
    """C7 unified trigger coordinator for ALL blueprint build enqueuing.

    Five trigger surfaces MUST go through ``try_claim()`` before
    enqueuing a blueprinter job:

    1. Manual ``/rebuild``
    2. Manual ``/update``
    3. ``/scan`` (manual smart scan)
    4. Daily maintenance (:class:`BlueprintScanService`)
    5. High-water threshold trigger

    Args:
        project_repository: The :class:`SQLModelProjectRepository`
            instance. Must expose ``get_metadata``, ``set_metadata``,
            ``delete_metadata``, and ``list_projects``.
        job_queue_service: Optional. Used only by
            :meth:`reconcile_on_startup` to check whether a leased
            ``job_id`` is still alive in the queue. May be ``None``
            for unit tests; reconciliation then treats every lease as
            orphaned.
    """

    def __init__(self, project_repository: Any, job_queue_service: Any = None) -> None:
        self._project_repository = project_repository
        self._job_queue_service = job_queue_service
        # Per-project asyncio.Lock. Lazily created on first claim so we
        # don't pay memory for projects we'll never see.
        self._locks: dict[str, asyncio.Lock] = {}
        self._locks_guard = asyncio.Lock()

    # ── Lock management ─────────────────────────────────────────────

    async def _get_lock(self, project_id: str) -> asyncio.Lock:
        """Return (or create) the per-project claim lock."""
        # Fast path: avoid the guard for already-known projects.
        lock = self._locks.get(project_id)
        if lock is not None:
            return lock
        async with self._locks_guard:
            lock = self._locks.get(project_id)
            if lock is None:
                lock = asyncio.Lock()
                self._locks[project_id] = lock
            return lock

    def set_job_queue_service(self, service: Any) -> None:
        """Late-bind the JobQueueService (manager wiring)."""
        self._job_queue_service = service

    # ── Lease helpers ────────────────────────────────────────────────

    def _read_lease(self, project_id: str) -> dict[str, Any] | None:
        """Read the lease dict from project metadata (sync)."""
        try:
            value = self._project_repository.get_metadata(
                project_id, LEASE_META_KEY,
            )
        except Exception as exc:
            # A malformed metadata value should not crash the claim.
            logger.warning(
                "C7: failed to read lease for project %s: %s",
                project_id, exc,
            )
            return None
        if not isinstance(value, dict):
            return None
        return value

    def _write_lease(self, project_id: str, lease: dict[str, Any]) -> None:
        """Persist the lease dict to project metadata (sync)."""
        self._project_repository.set_metadata(
            project_id, LEASE_META_KEY, lease,
        )

    def _delete_lease(self, project_id: str) -> None:
        """Remove the lease dict from project metadata (sync)."""
        try:
            self._project_repository.delete_metadata(
                project_id, LEASE_META_KEY,
            )
        except Exception as exc:
            logger.warning(
                "C7: failed to delete lease for project %s: %s",
                project_id, exc,
            )

    # ── try_claim ────────────────────────────────────────────────────

    async def try_claim(
        self,
        project_id: str,
        mode: str,
        job_id: str,
    ) -> ClaimResult:
        """Atomically acquire (or coalesce onto) the project's build lease.

        A lease is "active" if it exists, has not been released, and is
        not past the absolute TTL. An EXPIRED lease is treated as
        ABSENT: it is dropped and a fresh lease is claimed, so a leaked
        lease self-heals at the next trigger WITHOUT a daemon restart.

        There is no heartbeat — expiry is absolute and derived from the
        lease's ``claimed_at`` field.

        Args:
            project_id: Project whose blueprint set is being built.
            mode: ``"rebuild"``, ``"incremental"``, or ``"single"``.
                ``"single"`` is a third valid mode that targets one
                specific blueprint (via ``metadata.blueprint_id``) for
                a focused rebuild — it coalesces with itself and
                conflicts with ``"rebuild"`` / ``"incremental"``. The
                coordinator is mode-string-generic; the lease semantics
                are uniform across all three.
            job_id: The caller-chosen UUID for the would-be blueprinter
                job. Echoed back as ``ClaimResult.job_id`` on a fresh
                claim; preserved for caller-side correlation.

        Returns:
            :class:`ClaimResult` — see its docstring for the three
            outcomes (claimed, coalesced, conflict).
        """
        lock = await self._get_lock(project_id)
        async with lock:
            existing = self._read_lease(project_id)

            # Case 0: an EXPIRED lease is treated as absent — drop it
            # and fall through to a fresh claim. This is what makes a
            # leaked lease self-heal without a daemon restart.
            if existing is not None and self._lease_is_expired(existing):
                logger.info(
                    "C7: lease for project %s is past the %s-minute TTL "
                    "(claimed_at=%s) — reclaiming",
                    project_id, _lease_ttl_minutes(),
                    existing.get("claimed_at"),
                )
                self._delete_lease(project_id)
                existing = None

            # Case 1: no lease → fresh claim.
            if existing is None:
                now = _utcnow_iso()
                run_token = str(uuid4())
                lease = {
                    "run_token": run_token,
                    "job_id": job_id,
                    "mode": mode,
                    "claimed_at": now,
                }
                self._write_lease(project_id, lease)
                logger.info(
                    "C7: claimed %s lease for project %s (job=%s, token=%s)",
                    mode, project_id, job_id, run_token,
                )
                return ClaimResult(
                    claimed=True,
                    job_id=job_id,
                    run_token=run_token,
                )

            # Case 2: active lease, same mode → coalesce.
            if existing.get("mode") == mode:
                return ClaimResult(
                    claimed=False,
                    job_id=existing.get("job_id"),
                    coalesced=True,
                )

            # Case 3: active lease, different mode → conflict.
            return ClaimResult(
                claimed=False,
                conflict_mode=existing.get("mode"),
            )

    # ── release ──────────────────────────────────────────────────────

    async def release(self, project_id: str, run_token: str) -> bool:
        """Release the lease if the token matches.

        Returns False when the lease is absent or owned by a different
        token (the caller is not the current lease holder).
        """
        lock = await self._get_lock(project_id)
        async with lock:
            existing = self._read_lease(project_id)
            if existing is None:
                return False
            if existing.get("run_token") != run_token:
                return False
            self._delete_lease(project_id)
            logger.info(
                "C7: released lease for project %s (job=%s)",
                project_id, existing.get("job_id"),
            )
            return True

    # ── is_active ────────────────────────────────────────────────────

    async def is_active(self, project_id: str, mode: str | None = None) -> bool:
        """Return True if a lease exists for ``project_id``.

        An EXPIRED lease does not count as active — it is treated as
        absent, exactly as :meth:`try_claim` treats it (a lease that
        cannot block a claim must not report itself as blocking).

        When ``mode`` is provided, only leases matching that mode count
        (an ``incremental`` lease does not satisfy a ``rebuild``
        query).
        """
        existing = self._read_lease(project_id)
        if existing is None:
            return False
        if self._lease_is_expired(existing):
            return False
        if mode is not None and existing.get("mode") != mode:
            return False
        return True

    # ── expiry ───────────────────────────────────────────────────────

    def _lease_is_expired(self, lease: dict[str, Any]) -> bool:
        """Return True when the lease is older than the absolute TTL.

        The TTL is measured from the lease's ``claimed_at`` field — the
        lease dict shape is frozen (there is deliberately NO heartbeat
        field), so the claim timestamp is the only clock the lease
        carries. No LLM-dependent renewal is involved.

        A lease whose ``claimed_at`` is missing or unparsable is treated
        as NOT expired: expiry frees a build slot, so the unprovable
        case keeps the lease (the startup reconciliation and the
        job-aware sweep still free it).
        """
        claimed_at = lease.get("claimed_at")
        if not claimed_at:
            return False
        try:
            claimed = datetime.fromisoformat(str(claimed_at))
        except (TypeError, ValueError):
            logger.warning(
                "C7: lease claimed_at=%r is unparsable — treating the "
                "lease as live", claimed_at,
            )
            return False
        if claimed.tzinfo is None:
            claimed = claimed.replace(tzinfo=timezone.utc)
        ttl_minutes = _lease_ttl_minutes()
        age_seconds = (datetime.now(timezone.utc) - claimed).total_seconds()
        return age_seconds > ttl_minutes * 60

    async def _maybe_release_expired(
        self, project_id: str, lease: dict[str, Any],
    ) -> bool:
        """Release the lease on ``project_id`` if it is past the TTL.

        Returns True when a release happened.

        TOCTOU safety mirrors :meth:`_maybe_release_orphaned`: the
        lease snapshot observed by the outer scan is re-read under the
        per-project lock and the ``run_token`` is compared, so a
        concurrent ``try_claim`` that replaced the lease is never
        deleted.
        """
        lock = await self._get_lock(project_id)
        async with lock:
            existing = self._read_lease(project_id)
            if existing is None:
                return False  # Lease absent — nothing to release
            # The concurrent try_claim may have already replaced it.
            if existing.get("run_token") != lease.get("run_token"):
                return False  # Lease replaced concurrently — keep the new lease
            if not self._lease_is_expired(existing):
                return False  # Still inside the TTL — keep the lease
            self._delete_lease(project_id)
            logger.info(
                "C7: released expired lease for project %s (job=%s, "
                "claimed_at=%s)",
                project_id, existing.get("job_id"),
                existing.get("claimed_at"),
            )
            return True

    # ── periodic sweep ───────────────────────────────────────────────

    async def sweep_expired_leases(self) -> int:
        """Release leases that are past the TTL or whose job is gone.

        Two legs:

        * **TTL leg** — the lease is older than the absolute TTL. This
          is the backstop for a lease whose release never happened
          (the agent never called the tool AND the job never
          terminalized).
        * **Job-aware leg** — the backing job is gone or already
          terminal. This reuses the existing tri-state
          :meth:`_maybe_release_orphaned` machinery (which holds the
          per-project lock and the ``run_token`` re-check TOCTOU
          guard).

        Registered on the maintenance registry as
        ``blueprint_lease_sweep`` (every 30 minutes).

        Returns the count of leases released.
        """
        released = 0
        # The full-project DB scan is the expensive sync operation; keep
        # it off the event loop (same shape as reconcile_on_startup).
        entries = await asyncio.to_thread(self._iter_projects_with_lease)
        for project_id, lease in entries:
            try:
                if await self._maybe_release_expired(project_id, lease):
                    released += 1
                    continue
                # Not expired — the job-aware leg. With no queue to
                # consult the verdict is UNKNOWN, and the tri-state
                # contract keeps the lease (unlike the startup
                # reconciliation, which treats "no queue" as orphaned).
                if self._job_queue_service is None:
                    continue
                if await self._maybe_release_orphaned(project_id, lease):
                    released += 1
            except Exception as exc:
                # One bad project must not abort the whole pass.
                logger.warning(
                    "C7: lease sweep failed for project %s: %s",
                    project_id, exc,
                )
        if released:
            logger.info(
                "C7: lease sweep released %d lease(s)", released,
            )
        return released

    # ── reconciliation ──────────────────────────────────────────────

    async def reconcile_on_startup(self) -> int:
        """Release leases whose backing job is gone or already terminal.

        For each project with a lease:

        * If ``_job_queue_service`` is None, treat the lease as orphaned
          (release). This is the unit-test default — there is no queue
          to consult.
        * Otherwise ask the queue whether ``job_id`` is still active.
          If the job is unknown or in a terminal state, release.

        Returns the count of leases released.
        """
        released = 0
        # This full-project DB scan is the expensive sync operation; keep it
        # off the event loop. Single-row lease operations remain inline.
        entries = await asyncio.to_thread(self._iter_projects_with_lease)
        for project_id, lease in entries:
            try:
                if not await self._maybe_release_orphaned(project_id, lease):
                    continue
                released += 1
            except Exception as exc:
                # One bad project must not abort the whole pass.
                logger.warning(
                    "C7: reconcile failed for project %s: %s",
                    project_id, exc,
                )
        if released:
            logger.info(
                "C7: startup reconciliation released %d orphaned lease(s)",
                released,
            )
        return released

    # ── internal helpers ─────────────────────────────────────────────

    def _iter_projects_with_lease(self) -> list[tuple[str, dict[str, Any]]]:
        """Yield ``(project_id, lease_dict)`` for projects holding a lease.

        We can't filter by metadata in SQL without an extra join, and
        the project count is bounded enough that listing all projects
        and probing each one is acceptable for startup reconciliation.

        The ``lease_dict`` snapshot is the one reconciliation will
        compare against after re-reading inside the per-project
        lock. If a concurrent ``try_claim`` replaces the lease between
        this scan and the locked re-read, the ``run_token`` mismatch
        prevents us from deleting the new lease.
        """
        try:
            projects = self._project_repository.list_projects(limit=10_000)
        except Exception as exc:
            logger.warning("C7: list_projects failed: %s", exc)
            return []
        out: list[tuple[str, dict[str, Any]]] = []
        for p in projects:
            pid = getattr(p, "project_id", None)
            if not pid:
                continue
            try:
                lease = self._project_repository.get_metadata(pid, LEASE_META_KEY)
            except Exception:
                continue
            if isinstance(lease, dict) and lease:
                out.append((pid, lease))
        return out

    async def _maybe_release_orphaned(
        self, project_id: str, lease: dict[str, Any],
    ) -> bool:
        """Release the lease on ``project_id`` if its job is gone/terminal.

        Returns True when a release happened. Tri-state from
        :meth:`_job_is_terminal` (True = terminal, False = active,
        None = unknown) controls the outcome — on ``None`` we keep
        the lease (an operator can clear it manually, or the next
        daemon restart will retry reconciliation).

        TOCTOU safety: ``lease`` is the snapshot observed by the
        outer scan (before we acquired the per-project lock). After
        acquiring the lock we re-read the lease; if a concurrent
        ``try_claim`` has already replaced it (different
        ``run_token``) we leave the new lease alone. Without this
        guard, a reconcile racing with a fresh claim could delete the
        just-claimed lease.
        """
        lock = await self._get_lock(project_id)
        async with lock:
            existing = self._read_lease(project_id)
            if existing is None:
                return False  # Lease absent — nothing to release
            # The concurrent try_claim may have already replaced it.
            if existing.get("run_token") != lease.get("run_token"):
                return False  # Lease replaced concurrently — keep the new lease
            # No queue to consult → always treat as orphaned.
            if self._job_queue_service is None:
                self._delete_lease(project_id)
                return True  # Lease released
            job_id = existing.get("job_id")
            if not job_id:
                self._delete_lease(project_id)
                return True  # Lease released
            try:
                verdict = await self._job_is_terminal(job_id)
            except Exception as exc:
                logger.warning(
                    "C7: queue probe failed for job %s: %s", job_id, exc,
                )
                verdict = None  # Probe failed (indeterminate) — keep lease.
            if verdict is None:
                # Probe failed (indeterminate) — keep lease.
                return False  # Job still active or can't determine — keep lease
            if verdict is True:
                self._delete_lease(project_id)
                return True  # Lease released
            return False  # Job still active or can't determine — keep lease

    async def _job_is_terminal(self, job_id: str) -> bool | None:
        """Best-effort terminal-status probe via JobQueueService.

        Returns a tri-state:
            ``True``  — job is gone or in a known terminal state.
            ``False`` — job is still active.
            ``None``  — could not determine (queue raised / unknown
                        API). Callers MUST treat ``None`` as
                        "keep the lease" so a transient queue blip
                        does not free a live build.

        The probe tries a few attribute names so we don't couple
        tightly to the service's exact API.
        """
        svc = self._job_queue_service
        # Common API: ``get_job(job_id)`` returning a JobItem-like row.
        get_job = getattr(svc, "get_job", None)
        if callable(get_job):
            try:
                job = await get_job(job_id)
            except Exception:
                return None  # unknown — don't release on a queue blip
            if job is None:
                return True
            status = getattr(job, "status", None) or getattr(job, "state", None)
            if status is None:
                return True
            return self._is_terminal_status(status)
        # Fallback: a status-only accessor.
        get_status = getattr(svc, "get_job_status", None)
        if callable(get_status):
            try:
                status = await get_status(job_id)
            except Exception:
                return None
            if status is None:
                return True
            return self._is_terminal_status(status)
        # No usable API — assume terminal so the lease can be released.
        return True

    @staticmethod
    def _is_terminal_status(status: Any) -> bool:
        """Heuristic terminal-status check, matching the project's
        canonical terminal set (``TERMINAL_STATUSES`` in
        :mod:`daemon.services.job_queue_service`) plus common aliases.
        """
        if status is None:
            return True
        s = str(status).lower()
        # Conservative: only mark as terminal if it matches one of
        # the canonical terms. Unknown statuses are treated as
        # ACTIVE so we don't accidentally release a live lease.
        terminal = {
            "completed", "complete", "success", "succeeded",
            "failed", "failure", "error", "errored",
            "cancelled", "canceled", "abandoned",
            "dead_letter", "dead-letter",
        }
        return s in terminal


__all__ = [
    "BlueprintTriggerCoordinator",
    "ClaimResult",
    "DEFAULT_LEASE_TTL_MINUTES",
    "LEASE_META_KEY",
    "LEASE_TTL_ENV_VAR",
]
