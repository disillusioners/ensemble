"""C7 — Unified Blueprint Trigger Coordinator.

A single chokepoint for all blueprint build enqueuing. Every trigger
surface (manual ``/rebuild``, ``/update``, ``/scan``, the daily
maintenance scan, the high-water threshold) MUST call
``coordinator.try_claim()`` before enqueuing a blueprinter job. The
coordinator guarantees:

* **Atomic project claim** — at most one build per project at a time.
* **Coalescing** — a second claim for the same mode returns the
  in-flight ``job_id`` instead of enqueuing a duplicate.
* **Cross-mode conflict** — a claim for a different mode (e.g.
  ``incremental`` while a ``rebuild`` is in flight) returns
  ``conflict_mode`` so the caller can decide (e.g. 409).
* **Terminal release** — the blueprinter calls ``release()`` with its
  ``run_token`` on success/failure/cancel.
* **Startup reconciliation** — on daemon start, leases whose backing
  job is gone (or already terminal) are released.

Lease lifecycle
---------------

* **Claim** — on ``try_claim()``, the coordinator atomically acquires
  a lease for the project.
* **Release** — on job completion (success/failure/cancel), the
  blueprinter calls ``release(run_token)``.
* **Reconcile** — on daemon startup, ``reconcile_on_startup()`` scans
  for leases whose backing job is gone or already terminal and
  releases them.

There is no TTL, no heartbeat, and no periodic sweep. A lease lives
until it is released by the job that claimed it, or until it is
reconciled on startup after a daemon crash.

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

        A lease is "active" if it exists and hasn't been released.
        No TTL, no heartbeat — leases live until released or
        reconciled on startup.

        Args:
            project_id: Project whose blueprint set is being built.
            mode: ``"rebuild"`` or ``"incremental"``.
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

        When ``mode`` is provided, only leases matching that mode count
        (an ``incremental`` lease does not satisfy a ``rebuild``
        query).
        """
        existing = self._read_lease(project_id)
        if existing is None:
            return False
        if mode is not None and existing.get("mode") != mode:
            return False
        return True

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
    "LEASE_META_KEY",
]
