"""Manual Maintenance Console API service — Section 1.

AM-2 + AM-9 + AM-10 + AM-11 + AM-12 + AM-15. FastAPI-free so the
service unit-tests without HTTP (T5.1 — the error carrier is a
plain dataclass; the router maps ``MaintenanceError`` 1:1 to
``HTTPException(status, detail={"error", "message", **details})``
per the plane.py structured-dict pattern, A-8 RATIFIED).

Manual entry point composition (AM-2): ``run_checkpoint_prunes``
orders Op E → Op D inside the service. The auto cycle is UNCHANGED
(INV-1, INV-9) — it has its own inline A→E sequence with D→E
order.

Single-flight sequence (AM-4 + AM-5): ``acquire lock → conditional
INSERT → run → finalize-in-finally``. The lock is step 1; the
partial unique index on ``(section) WHERE status='running'`` is
the real gate. INSERT conflict → 409 ``run_in_flight`` with
``details.run_id`` + ``details.started_at`` of the IN-FLIGHT run
nested under ``details`` (C-2 v3 fix pass); NO row written for
the refused caller (AM-5 + AM-6).

The executing task lives on ``self._executing_tasks`` with a
done-callback discard (R-5 — GC-safe, memory O(1)). Manager
shutdown cancels + awaits the live task; a graceful shutdown marks
the row ``failed`` with ``error_json.code='run_interrupted_by_shutdown'``
per [AM-6/AM-7] state machine.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any, Optional

from daemon.checkpoint_adapter import (
    CheckpointerAdapter,
    PostgresCheckpointerAdapter,
)
from daemon.config import PersistenceConfig
from daemon.constants import MAINTENANCE_DRY_RUN_FRESH_SECONDS
from daemon.services.checkpoint_prune import (
    blob_prune_destructive_enabled,
    blob_prune_env_state,
)
from daemon.services.maintenance_run_identity import new_maintenance_run_id
from daemon.services.maintenance_run_lock import (
    MaintenanceRunContext,
    MaintenanceRunLock,
)
from daemon.services.timestamps import now_utc_iso, now_utc_naive
from daemon.repositories.maintenance_runs import (
    MaintenanceRun,
    MaintenanceRunsRepository,
)

if TYPE_CHECKING:
    # TYPE_CHECKING-only: the request schema import must NOT become a
    # runtime import (the service is deliberately FastAPI-free at
    # runtime — T5.1; duck-typed payloads keep working).
    from daemon.routers.schemas import CheckpointCleanupExecuteRequest
    from daemon.services.maintenance import CheckpointCleanupJob, MaintenanceService

logger = logging.getLogger(__name__)


# ── Requester forensics (T5.2) ──────────────────────────────────────────────────


@dataclass
class RequesterInfo:
    """Minimal per-request forensics stamped onto a manual run row.

    AM-15 — peer_ip / user_agent / origin. Never attribution (no
    session ids exist; nothing identifies an operator beyond the
    network edge). Origin is the same string the Origin guard saw;
    peer_ip is the request client (or ``None`` for localhost-style
    callers); user_agent is the raw ``User-Agent`` header (or
    ``None``).
    """

    peer_ip: Optional[str] = None
    user_agent: Optional[str] = None
    origin: Optional[str] = None


# ── Error carrier (T5.1) ────────────────────────────────────────────────────────


@dataclass
class MaintenanceError(Exception):
    """Service-level error carrier — maps 1:1 to HTTPException at the router.

    A-8 RATIFIED — structured dict shape (``{error, message, details}``)
    binding for all 5 endpoints. The router catches this exception
    type and emits ``HTTPException(e.http_status, detail={"error":
    e.code, "message": e.message, **e.details})``. Details default to
    ``{}`` so the wire shape stays consistent across every error.
    """

    code: str
    http_status: int
    message: str
    details: dict[str, Any] = field(default_factory=dict)


# ── Service ─────────────────────────────────────────────────────────────────────


class MaintenanceApiService:
    """The Section 1 manual orchestration surface (T5.2).

    Constructor parameters mirror the plan §6.2 — the SAME
    ``MaintenanceRunLock`` instance the auto cycle holds (so the
    manual + auto gates share one lock + one DB-claim row); the
    ``MaintenanceService`` reference is optional and used only for
    the idle-advisory probe (``maintenance_service=None`` returns
    ``True`` so unit tests don't need one).
    """

    def __init__(
        self,
        config: PersistenceConfig,
        checkpointer: CheckpointerAdapter,
        cleanup_job: "CheckpointCleanupJob",
        runs_repo: MaintenanceRunsRepository,
        run_lock: MaintenanceRunLock,
        maintenance_service: Optional["MaintenanceService"] = None,
        fresh_seconds: int = MAINTENANCE_DRY_RUN_FRESH_SECONDS,
    ) -> None:
        self._config = config
        self._checkpointer = checkpointer
        self._cleanup_job = cleanup_job
        self._runs_repo = runs_repo
        self._run_lock = run_lock
        self._maintenance_service = maintenance_service
        self._fresh_seconds = fresh_seconds
        # In-flight executing tasks (set + done-callback discard per R-5).
        self._executing_tasks: set[asyncio.Task] = set()

    # ── availability ─────────────────────────────────────────────────

    async def availability(self) -> dict[str, Any]:
        """FROZEN ``/availability`` shape — AM-13 state enum.

        States: ``ready`` / ``backend_unsupported`` /
        ``subsystem_disabled`` / ``kill_switched``. ``eligible`` is
        DERIVED (``state === 'ready'``); ``reason`` is a diagnostic
        string, NOT for FE branching (FE branches on ``state``).
        """
        # The kill-switch is read at the router (T6.3), not here —
        # ``/availability`` itself is NEVER 503'd by the kill-switch
        # (AM-13). The service surfaces ``ready`` whenever the
        # backend is PG and the checkpointer is wired.
        if not isinstance(self._checkpointer, PostgresCheckpointerAdapter):
            return {
                "eligible": False,
                "backend": "sqlite",
                "state": "backend_unsupported",
                "reason": "blob_prune_postgres_only",
            }
        return {
            "eligible": True,
            "backend": "postgres",
            "state": "ready",
            "reason": None,
        }

    # ── status ────────────────────────────────────────────────────────

    async def status(self) -> dict[str, Any]:
        """FROZEN ``/status`` shape — AM-9 + AM-12 + AM-13.

        ``config`` block is read from the LIVE effective env at
        request time (NOT boot-cached config — [R-6, leader ruling
        (a)]). ``last_run`` semantics: latest ``succeeded|failed``
        of ``kind ∈ {auto, manual_execute}``; ``manual_dry_run``
        NEVER surfaces. ``in_flight``: any ``running`` row (any
        kind).
        """
        # Config block — LIVE env read at request time.
        from daemon.constants import (
            CHECKPOINT_BLOB_PRUNE_DRY_RUN,
            CHECKPOINT_MAX_PER_THREAD_FLOOR,
        )

        config_block = {
            "checkpoint_max_per_thread": self._config.checkpoint_max_per_thread,
            "checkpoint_max_per_thread_floor": CHECKPOINT_MAX_PER_THREAD_FLOOR,
            "cleanup_interval_hours": self._config.checkpoint_cleanup_interval,
            "blob_prune_dry_run_env_default": (
                "1" if CHECKPOINT_BLOB_PRUNE_DRY_RUN else "0"
            ),
            "blob_prune_destructive_armed": blob_prune_destructive_enabled(),
        }

        # ``last_run`` — AM-9
        last = await asyncio.to_thread(
            self._runs_repo.latest_completed_for_section, "checkpoint-cleanup"
        )
        if last is None:
            last_run_payload: Optional[dict[str, Any]] = None
        else:
            last_run_payload = {
                "run_id": last.run_id,
                "kind": last.kind,
                "started_at": last.started_at,
                "completed_at": last.completed_at,
                "status": last.status,
                "summary": last.summary_json,
            }

        # ``in_flight`` — any running row.
        inflight = await asyncio.to_thread(
            self._runs_repo.get_running, "checkpoint-cleanup"
        )
        if inflight is None:
            in_flight_payload: Optional[dict[str, Any]] = None
        else:
            in_flight_payload = {
                "run_id": inflight.run_id,
                "kind": inflight.kind,
                "started_at": inflight.started_at,
                "triggered_by": inflight.triggered_by,
            }

        return {
            "config": config_block,
            "last_run": last_run_payload,
            "in_flight": in_flight_payload,
        }

    # ── dry-run ───────────────────────────────────────────────────────

    async def dry_run(self, requester: RequesterInfo) -> dict[str, Any]:
        """FROZEN ``/dry-run`` response — T5.4 ``dry_run``.

        Takes the SAME single-flight gate as execute (AM-4): the
        dry-run is a ``kind=manual_dry_run`` run row; its scan needs
        a stable blob set for ``expected_bytes`` to mean anything.
        """
        if not isinstance(self._checkpointer, PostgresCheckpointerAdapter):
            raise MaintenanceError(
                code="backend_unsupported",
                http_status=503,
                message="Checkpoint cleanup requires PostgreSQL",
            )

        ctx = MaintenanceRunContext(
            run_id=new_maintenance_run_id(),
            kind="manual_dry_run",
            started_at=now_utc_iso(),
            triggered_by="user",
        )
        # AM-4 fail-fast lock; AM-5 conditional INSERT.
        if not await self._run_lock.acquire(ctx):
            raise await self._conflict_error()

        try:
            # Conditional INSERT — conflict → 409 (defensive: lock is
            # held; belt-and-braces for the two-dev-daemons case).
            row = MaintenanceRun(
                run_id=ctx.run_id,
                section="checkpoint-cleanup",
                kind=ctx.kind,
                started_at=ctx.started_at,
                status="running",
                triggered_by=ctx.triggered_by,
                requester_json={
                    "peer_ip": requester.peer_ip,
                    "user_agent": requester.user_agent,
                    "origin": requester.origin,
                },
                # Raw dual-arm env state at dry-run time + the
                # override flag (False — the manual preview never arms
                # the DELETE arm regardless of env).
                env_flags_json={
                    **blob_prune_env_state(),
                    "destructive_override": False,
                },
            )
            inserted = await asyncio.to_thread(self._runs_repo.insert, row)
            if not inserted:
                # Concurrent inserter won the race; release the lock
                # and surface the conflict.
                self._run_lock.release()
                raise await self._conflict_error()

            # Run — manual entry point; ``destructive=False`` is
            # explicit (the manual preview never inherits an armed
            # env, even when the operator has the env dual-arm
            # armed — INV-2 + dry-run fidelity rule).
            try:
                result = await self._cleanup_job.run_checkpoint_prunes(
                    destructive=False
                )
            except Exception as exc:
                # Infra fault during dry-run — record as ``failed``,
                # do NOT swallow (caller logs + propagates).
                await asyncio.to_thread(
                    self._runs_repo.mark_terminal,
                    ctx.run_id,
                    "failed",
                    now_utc_iso(),
                    error_json={
                        "code": "execution_error",
                        "message": f"{type(exc).__name__}: {exc}",
                    },
                )
                raise

            # Build the wire response. ``fresh_until`` = now + fresh
            # seconds (TEXT ISO +00:00).
            fresh_until = (
                datetime.fromisoformat(ctx.started_at)
                + timedelta(seconds=self._fresh_seconds)
            ).isoformat()
            # v3.2 projection-class fields (R-1) — informational,
            # NEVER gate-bound; the echo gate below binds ONLY to
            # ``would_free_bytes`` (= ``bytes_reclaimable_now``).
            bytes_reclaimable_now = int(result.blobs.would_free_bytes)
            bytes_reclaimable_after_row_prune = int(
                result.rows.would_free_bytes_after_row_prune
            )
            bytes_reclaimable_total = (
                bytes_reclaimable_now + bytes_reclaimable_after_row_prune
            )
            wire = {
                "run_id": ctx.run_id,
                "would_delete": {
                    "checkpoint_rows": result.rows.would_delete_checkpoints,
                    "writes": result.rows.would_delete_writes,
                    "blobs": result.blobs.would_delete_count,
                    "bytes": result.blobs.would_free_bytes,
                },
                "would_delete_count": result.blobs.would_delete_count,
                "would_free_bytes": result.blobs.would_free_bytes,
                # v3.2 R-1 projection fields (additive; default 0
                # for legacy clients that ignore them). Schema-stability
                # alias: ``bytes_reclaimable_now`` is the SAME number
                # as ``would_free_bytes`` — explicit alias per the
                # amendment, not a recomputation. The echo gate binds
                # ONLY to ``would_free_bytes``.
                "bytes_reclaimable_now": bytes_reclaimable_now,
                "bytes_reclaimable_after_row_prune": (
                    bytes_reclaimable_after_row_prune
                ),
                "bytes_reclaimable_total": bytes_reclaimable_total,
                "scanned": {
                    "thread_ns_pairs": result.rows.scanned_pairs,
                },
                "skipped": [
                    {"thread_id": t, "checkpoint_ns": ns, "reason": r}
                    for (t, ns, r) in result.blobs.skipped[:1000]
                ],
                "skipped_truncated": len(result.blobs.skipped) > 1000
                or result.skipped_truncated,
                "duration_ms": result.duration_ms,
                "fresh_until": fresh_until,
            }
            # Mark the row succeeded; ``summary_json`` carries the
            # full dry-run payload (incl. skipped[] + projection
            # fields) — survives any future dry-run-row pruning and
            # is the snapshot the manual_execute row echoes from.
            await asyncio.to_thread(
                self._runs_repo.mark_terminal,
                ctx.run_id,
                "succeeded",
                now_utc_iso(),
                summary_json={
                    "dry_run": True,
                    "would_delete": wire["would_delete"],
                    "would_delete_count": wire["would_delete_count"],
                    "would_free_bytes": wire["would_free_bytes"],
                    "bytes_reclaimable_now": bytes_reclaimable_now,
                    "bytes_reclaimable_after_row_prune": (
                        bytes_reclaimable_after_row_prune
                    ),
                    "bytes_reclaimable_total": bytes_reclaimable_total,
                    "scanned": wire["scanned"],
                    "skipped": wire["skipped"],
                    "skipped_truncated": wire["skipped_truncated"],
                    "duration_ms": wire["duration_ms"],
                    "fresh_until": wire["fresh_until"],
                },
            )
            return wire
        finally:
            # Release the lock (success + failure + exception). Note:
            # the ``insert`` returned ``False`` branch above releases
            # explicitly and raises; this finally covers the rest.
            if self._run_lock.in_flight is ctx:
                self._run_lock.release()

    # ── execute ───────────────────────────────────────────────────────

    async def execute(
        self,
        payload: "CheckpointCleanupExecuteRequest",
        requester: RequesterInfo,
    ) -> dict[str, Any]:
        """FROZEN ``/execute`` 202 response — T5.4 ``execute``.

        Validation chain (in the Contract v3 order — first failure
        raises a ``MaintenanceError`` which the router maps 1:1 to
        HTTPException; the HTTP-level gate order [Origin → kill-switch
        → service] is pinned by case 28,
        ``TestRouterGates.test_gate_order_first_failure_wins``):

          1. backend_unsupported (PG-only)
          2. confirm_required (payload.confirm is not True)
          3. dry_run_required (payload.dry_run_run_id absent)
          4. not_found (dry_run_run_id not in maintenance_runs)
          5. dry_run_stale (age > fresh_seconds)
          6. byte_count_mismatch (echoed expected_bytes != stored)
          7. single-flight gate → 409 run_in_flight on conflict

        On success: spawn the executing task (background); respond
        202 with ``{run_id, status, started_at, advisory,
        expected_duration_ms_hint}``.
        """
        # 1. Backend gate (service-layer; the router's not_initialized
        # check comes BEFORE this when ``app.state`` is unwired).
        if not isinstance(self._checkpointer, PostgresCheckpointerAdapter):
            raise MaintenanceError(
                code="backend_unsupported",
                http_status=503,
                message="Checkpoint cleanup requires PostgreSQL",
            )
        # 2. Confirm.
        if not getattr(payload, "confirm", False):
            raise MaintenanceError(
                code="confirm_required",
                http_status=400,
                message="execute requires explicit confirm=true",
            )
        # 3. dry_run_run_id present.
        dry_run_run_id = getattr(payload, "dry_run_run_id", None)
        if not dry_run_run_id:
            raise MaintenanceError(
                code="dry_run_required",
                http_status=400,
                message="execute requires a dry_run_run_id echo",
            )
        # 4. dry-run row exists.
        dry_run_row = await asyncio.to_thread(
            self._runs_repo.get_dry_run, dry_run_run_id
        )
        if dry_run_row is None or dry_run_row.kind != "manual_dry_run":
            raise MaintenanceError(
                code="not_found",
                http_status=404,
                message=f"dry-run row {dry_run_run_id} not found",
                details={"run_id": dry_run_run_id},
            )
        # 5. freshness window. [tidier fix pass] a corrupt/unparseable
        # ``started_at`` on the stored row maps to 404 ``not_found``
        # (the row is unusable as a dry-run reference) instead of an
        # opaque 500 from ValueError.
        try:
            started = _parse_iso_naive(dry_run_row.started_at)
        except ValueError:
            raise MaintenanceError(
                code="not_found",
                http_status=404,
                message=(
                    f"dry-run row {dry_run_run_id} has an unreadable "
                    f"started_at"
                ),
                details={"run_id": dry_run_run_id},
            ) from None
        age_seconds = (now_utc_naive() - started).total_seconds()
        if age_seconds > self._fresh_seconds:
            raise MaintenanceError(
                code="dry_run_stale",
                http_status=400,
                message="dry-run row is older than the freshness window",
                details={
                    "age_seconds": int(age_seconds),
                    "max_age_seconds": self._fresh_seconds,
                },
            )
        # 6. byte-count match.
        expected_bytes = getattr(payload, "expected_bytes", None)
        stored_bytes = (
            (dry_run_row.summary_json or {}).get("would_delete", {}).get("bytes")
        )
        # [reviewer cheap fix] explicit ``stored_bytes is None`` arm:
        # a dry-run row whose summary lacks ``would_delete.bytes``
        # (legacy/corrupt row) is not echoable — refuse with the same
        # frozen 400 literal rather than a confusing ``None != N``.
        if (
            expected_bytes is None
            or stored_bytes is None
            or expected_bytes != stored_bytes
        ):
            raise MaintenanceError(
                code="byte_count_mismatch",
                http_status=400,
                message="echoed expected_bytes does not match stored dry-run",
                details={
                    "expected": expected_bytes,
                    "stored": stored_bytes,
                },
            )
        # 7. Single-flight gate [AM-4 + AM-5]: acquire → conditional
        # INSERT (manual_execute row, status='running',
        # triggered_by='user'). INSERT conflict → 409 run_in_flight
        # carrying details.run_id + details.started_at of the IN-FLIGHT
        # run (read via the lock holder or get_running() — NOT the
        # caller's; nested under details per [C-2, v3 fix pass]), NO
        # row written; a refused acquire logs ONE INFO line with
        # requester forensics [AM-6].
        ctx = MaintenanceRunContext(
            run_id=new_maintenance_run_id(),
            kind="manual_execute",
            started_at=now_utc_iso(),
            triggered_by="user",
        )
        if not await self._run_lock.acquire(ctx):
            logger.info(
                "maintenance execute refused (gate held): attempted_run_id=%s "
                "peer_ip=%s user_agent=%s origin=%s",
                ctx.run_id, requester.peer_ip, requester.user_agent,
                requester.origin,
            )
            raise await self._conflict_error()

        task_spawned = False
        try:
            # Idle advisory — computed BEFORE the claim insert so the
            # row carries it as a decision-input audit field at insert
            # time (AM-15); advisory, never a refusal (AM-12).
            idle = True
            if self._maintenance_service is not None:
                idle = await self._maintenance_service.is_idle()
            advisory = None if idle else "system_busy"

            row = MaintenanceRun(
                run_id=ctx.run_id,
                section="checkpoint-cleanup",
                kind=ctx.kind,
                started_at=ctx.started_at,
                status="running",
                triggered_by=ctx.triggered_by,
                requester_json={
                    "peer_ip": requester.peer_ip,
                    "user_agent": requester.user_agent,
                    "origin": requester.origin,
                },
                dry_run_run_id=dry_run_run_id,
                expected_bytes=expected_bytes,
                # Full dry-run snapshot survives any future row
                # pruning [AM-15].
                dry_run_summary_json=dry_run_row.summary_json,
                confirm=True,
                advisory=advisory,
                # INV-2 audit proof: the RAW dual-arm env state at
                # execute time + the override flag (the kwarg, not
                # env, armed the DELETE — blob_prune_env_state reads
                # the same env vars the gate function does).
                env_flags_json={
                    **blob_prune_env_state(),
                    "destructive_override": True,
                },
            )
            inserted = await asyncio.to_thread(self._runs_repo.insert, row)
            if not inserted:
                self._run_lock.release()
                logger.info(
                    "maintenance execute refused (DB claim held): "
                    "attempted_run_id=%s peer_ip=%s user_agent=%s origin=%s",
                    ctx.run_id, requester.peer_ip, requester.user_agent,
                    requester.origin,
                )
                raise await self._conflict_error()

            # Hint — the referenced dry-run's duration_ms [AM-12,
            # A-11 RATIFIED, R-5 v3 fix pass]. Unit: milliseconds
            # (the field name says ms and ms is what it carries).
            dry_run_duration_ms = (dry_run_row.summary_json or {}).get(
                "duration_ms", 0
            )
            hint_ms = int(dry_run_duration_ms) if isinstance(
                dry_run_duration_ms, (int, float)
            ) else 0

            # Spawn the executing task (background). From this point
            # the TASK owns the lock (its ``finally`` releases — see
            # ``_execute_run``); the sentinel flips so this method's
            # ``finally`` below does NOT release on the happy return.
            task = asyncio.create_task(
                self._execute_run(ctx.run_id), name=f"maintenance-execute-{ctx.run_id}"
            )
            self._executing_tasks.add(task)
            task.add_done_callback(self._executing_tasks.discard)
            task_spawned = True

            return {
                "run_id": ctx.run_id,
                "status": "running",
                "started_at": ctx.started_at,
                "advisory": advisory,
                "expected_duration_ms_hint": hint_ms,
            }
        finally:
            # [tidier fix pass] try/finally lock-release shape (was a
            # bare ``except BaseException: release; raise`` — the
            # catch-all was lint-stink and swallowed nothing, but the
            # sentinel-guarded ``finally`` states the ownership handoff
            # explicitly, mirroring the sibling ``dry_run`` pattern's
            # release-only-if-holder discipline): any path that did
            # NOT hand the lock to the background task releases it
            # here — pre-spawn infra faults (db/import errors), the
            # explicit INSERT-conflict branch above (already released;
            # the holder check makes the double release a no-op), and
            # cancellation alike. Once the task is spawned, its own
            # ``finally`` is the sole releaser (releasing here would
            # unlock the gate while the destructive run is live).
            # Identity re-check is load-bearing: a lost race means a newer claimant owns the lock — release must never revoke the wrong holder.
            if not task_spawned and self._run_lock.in_flight is ctx:
                self._run_lock.release()

    async def _execute_run(self, run_id: str) -> None:
        """Run the destructive cycle + terminal write in the background.

        On exception: row → ``failed`` with ``error_json.code =
        'execution_error'`` (infra faults only; pair failures live in
        ``summary.skipped``, never on this row). On success: row →
        ``succeeded`` with ``summary_json`` carrying the full
        ``CheckpointRunResult.to_summary_dict()`` payload. On
        CANCELLATION (manager shutdown): row → ``failed`` with
        ``error_json.code='run_interrupted_by_shutdown'`` per the
        AM-6/AM-7 state-machine ruling (a graceful shutdown is not a
        crash; the boot sweep's ``interrupted`` state is reserved for
        restart-orphaned rows) — then the CancelledError re-raises.

        The lock is released in ``finally`` — the router already
        returned 202 to the caller, so the caller never sees the
        lock state.
        """
        try:
            try:
                result = await self._cleanup_job.run_checkpoint_prunes(
                    destructive=True
                )
                # v3.2 (R-5): the manual_execute summary gains ONE
                # additive ``projection`` block sourced from the
                # snapshotted dry-run row (``dry_run_summary_json`` —
                # the field the INSERT above persisted when the
                # operator executed). Auto rows do NOT get this block
                # (auto cycle writes ``result.to_summary_dict()``
                # directly with the FROZEN shape — see
                # ``CheckpointCleanupJob.execute``). The block is
                # ECHOED, not recomputed — what the dry-run promised
                # is what the post-run banner can attest.
                execute_summary = result.to_summary_dict()
                dry_run_snapshot = await asyncio.to_thread(
                    self._runs_repo.get, run_id
                )
                if dry_run_snapshot is not None:
                    snap = dry_run_snapshot.dry_run_summary_json or {}
                    # v3.2 O2 + legacy-row hardening: BOTH-OR-NEITHER.
                    # The echo block is emitted only when the snapshotted
                    # dry-run summary carries BOTH projection values;
                    # pre-v3.2 legacy rows (summary predates the
                    # projection fields), one-sided rows, and non-dict /
                    # non-numeric shapes all fall through to NO block —
                    # never a partial echo, never a KeyError/TypeError
                    # from an unexpected row shape.
                    snap_now = (
                        snap.get("bytes_reclaimable_now")
                        if isinstance(snap, dict)
                        else None
                    )
                    snap_after = (
                        snap.get("bytes_reclaimable_after_row_prune")
                        if isinstance(snap, dict)
                        else None
                    )
                    if isinstance(snap_now, (int, float)) and isinstance(
                        snap_after, (int, float)
                    ):
                        execute_summary["projection"] = {
                            "bytes_reclaimable_now_at_dry_run": int(snap_now),
                            "bytes_reclaimable_after_row_prune_at_dry_run": int(
                                snap_after
                            ),
                        }
                await asyncio.to_thread(
                    self._runs_repo.mark_terminal,
                    run_id,
                    "succeeded",
                    now_utc_iso(),
                    summary_json=execute_summary,
                )
            except asyncio.CancelledError:
                # Shutdown hook (T5.5) — mark the row per the 4-state
                # enum, then let the cancellation propagate.
                try:
                    await asyncio.to_thread(
                        self._runs_repo.mark_terminal,
                        run_id,
                        "failed",
                        now_utc_iso(),
                        error_json={
                            "code": "run_interrupted_by_shutdown",
                            "message": "Run interrupted by daemon shutdown",
                        },
                    )
                except Exception as mark_exc:  # noqa: BLE001
                    logger.warning(
                        f"maintenance_runs shutdown-mark failed for "
                        f"{run_id}: {mark_exc}"
                    )
                raise
            except Exception as exc:
                logger.error(
                    f"maintenance execute_run failed (run_id={run_id}): "
                    f"{type(exc).__name__}: {exc}",
                    exc_info=True,
                )
                try:
                    await asyncio.to_thread(
                        self._runs_repo.mark_terminal,
                        run_id,
                        "failed",
                        now_utc_iso(),
                        error_json={
                            "code": "execution_error",
                            "message": f"{type(exc).__name__}: {exc}",
                        },
                    )
                except Exception as mark_exc:  # noqa: BLE001
                    logger.warning(
                        f"maintenance_runs failed-mark failed for "
                        f"{run_id}: {mark_exc}"
                    )
        finally:
            # Release the in-process lock (the 409-adoption contract
            # means a subsequent caller can pick up the audit row's
            # run_id and poll, but a new run acquires a NEW lock).
            self._run_lock.release()

    # ── get_run ───────────────────────────────────────────────────────

    async def get_run(self, run_id: str) -> dict[str, Any]:
        """FROZEN ``/runs/{id}`` response — T5.4 ``get_run``.

        ``running`` → ``{status, completed_at:null, summary:null,
        error:null}``; terminal → full body; unknown → 404
        ``not_found`` with ``details.run_id``.
        """
        row = await asyncio.to_thread(self._runs_repo.get, run_id)
        if row is None:
            raise MaintenanceError(
                code="not_found",
                http_status=404,
                message=f"run_id {run_id} not found",
                details={"run_id": run_id},
            )
        return {
            "run_id": row.run_id,
            "kind": row.kind,
            "status": row.status,
            "started_at": row.started_at,
            "completed_at": row.completed_at,
            "summary": row.summary_json,
            "error": row.error_json,
        }

    # ── shutdown ──────────────────────────────────────────────────────

    async def shutdown(self) -> None:
        """Cancel + await live executing tasks; mark each row
        ``failed`` with ``error_json.code='run_interrupted_by_shutdown'``.

        AM-6/AM-7 state-machine ruling: a graceful shutdown is not a
        crash; the row is marked ``failed`` (not ``interrupted`` —
        the boot sweep's ``interrupted`` state is reserved for
        restart-orphaned rows). Best-effort, never raises.
        """
        live = list(self._executing_tasks)
        for t in live:
            t.cancel()
        if live:
            # [tidier fix pass] no except needed: with
            # ``return_exceptions=True`` gather itself never raises
            # from task failures, and every failure path inside the
            # tasks is already logged by ``_execute_run``.
            await asyncio.gather(*live, return_exceptions=True)
        self._executing_tasks.clear()

    # ── helpers ───────────────────────────────────────────────────────

    async def _conflict_error(self) -> MaintenanceError:
        """Build the 409 ``run_in_flight`` body naming the IN-FLIGHT run.

        The in-flight run is resolved from BOTH sources, in order:
        the in-process lock holder (same-daemon contention — the
        common case) and the DB ``running`` row (cross-daemon
        contention on shared PG, where no local lock holder exists —
        AM-5). ``run_id``/``started_at`` nest under ``details``
        (C-2 v3 fix pass) — the payload the FE adopts to resume
        polling (409-adoption contract, AM-17).
        """
        holder = self._run_lock.in_flight
        details: dict[str, Any] = {}
        if holder is not None:
            details["run_id"] = holder.run_id
            details["started_at"] = holder.started_at
        else:
            try:
                running = await asyncio.to_thread(
                    self._runs_repo.get_running, "checkpoint-cleanup"
                )
            except Exception as exc:  # noqa: BLE001 — forensics only
                logger.debug(
                    "conflict forensics: get_running read failed: %s: %s",
                    type(exc).__name__, exc, exc_info=True,
                )
                running = None
            if running is not None:
                details["run_id"] = running.run_id
                details["started_at"] = running.started_at
                # [W1, v3 fix pass] This branch means NO in-process
                # holder — cross-daemon contention OR a stale
                # ``running`` row wedging the gate (the failed-boot-
                # sweep class). Additive details key (FE tolerates
                # extra keys, A-11); the frozen C-2 keys above are
                # untouched.
                details["heal_hint"] = (
                    "no in-process holder — if this run never "
                    "completes, see docs/runbooks/maintenance-console.md"
                )
        return MaintenanceError(
            code="run_in_flight",
            http_status=409,
            message="another maintenance run is in flight",
            details=details,
        )


# ── module helpers ──────────────────────────────────────────────────────────────


def _parse_iso_naive(s: str) -> datetime:
    """Parse an ISO-8601 ``+00:00`` aware string and return naive-UTC digits.

    R-5 — naive-vs-naive comparison: the dry-run's ``started_at``
    TEXT-ISO string is parsed and stripped of tzinfo so the diff
    against ``now_utc_naive()`` (NAIVE UTC digits) doesn't trip the
    mixed-frame trap.
    """
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        # Defensive — legacy or buggy row; assume UTC digits.
        return dt
    from datetime import timezone
    return dt.astimezone(timezone.utc).replace(tzinfo=None)


__all__ = [
    "MaintenanceApiService",
    "MaintenanceError",
    "RequesterInfo",
]
