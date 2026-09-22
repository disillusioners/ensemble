"""Structural sync between Ensemble projects and Plane projects.

The :class:`PlaneSyncService` mirrors an Ensemble ``Project`` to Plane as
a flat project (no issues, no cycles at v1). The mapping uses
``name`` ↔ ``Plane.name``, ``description`` ↔ ``Plane.description``, and
``status`` → ``Plane.state`` (via :data:`daemon.constants.PLANE_STATUS_MAP`).

Persistence
-----------
Sync state is stored on the project itself via project metadata records
(see :mod:`daemon.constants`):

- ``plane_project_id`` — Plane's internal UUID; the primary mapping handle.
- ``plane_sync_state`` — ``"linked"`` | ``"syncing"`` | ``"drift"`` | ``"error"``
  (``"synced"`` is accepted on read as a back-compat alias for ``"linked"``).
- ``plane_synced_at`` — ISO8601 timestamp of the most recent SUCCESSFUL sync.
- ``plane_last_attempt`` — ISO8601 timestamp of the most recent attempt
  (success OR failure); backs the watchdog's "next eligible retry" math.
- ``plane_attempt_count`` — int; consecutive-failure counter (reset on success).
- ``plane_last_error`` — short string; advisory, used for diagnostics.

Read/write goes through ``repo.list_metadata_records`` (single call, then
filter client-side) and ``repo.set_metadata_record`` — the lower-level
record-level methods, *not* the convenience ``set_metadata`` wrapper that
also rewrites ``Project.updated_at`` (CR-6).

State machine (Phase 3)
-----------------------
The sync engine runs on a 4-state machine::

    ┌────────┐  attempt_start   ┌─────────┐  success     ┌────────┐
    │ linked │ ────────────────▶│ syncing │─────────────▶│ linked │
    └────────┘                  └─────────┘              └────────┘
         ▲                             │
         │                             │  success + drift   ┌───────┐
         │                             ├───────────────────▶│ drift │
         │                             │                    └───────┘
         │                             │                         │
         │  success                    │  failure       ┌────────▼─────┐
         │  (corrective)               └────────────────│    error     │
         └──────────────────────────────               └──────────────┘

* ``syncing`` is written at the START of every attempt (the
  re-entrancy guard; HTTP endpoint returns 409 if a sync is already
  in flight). It is rolled back to ``error`` / ``linked`` / ``drift``
  in the same flow. The claim itself is a rowcount-guarded
  conditional upsert (Phase 4) so concurrent claimers cannot both
  win. A row stuck in ``syncing`` beyond
  ``N x watchdog-interval`` is crash wreckage — the watchdog boot
  sweep steals it back to ``error`` and re-drives (Phase 4).
* ``drift`` is written when an attempt SUCCEEDS at the API level but
  identity-field comparison flags divergence (e.g. Plane has been
  edited out-of-band). The watchdog re-drives drift because the
  corrective sync usually heals it.
* ``error`` is written on any failure (PlaneAPIError, PlaneAuthError,
  circuit-open, project-not-found, etc.). The watchdog re-drives with
  exponential backoff up to ``PLANE_SYNC_WATCHDOG_MAX_ATTEMPTS``.

Error contract
--------------
``sync_project`` **never raises**. On any Plane API error it records
``plane_sync_state="error"``, increments ``plane_attempt_count``, logs
a warning, and returns a structured result. The caller (HTTP router or
agent tool) decides whether to surface the error to the user; the
project itself is unaffected.

Kill-switch (Phase 4)
---------------------
``PLANE_SYNC_ENABLED`` (default ON) is an explicitly user-requested
integration kill-switch (sanctioned 2026-09-20) — the sanctioned
exception to the no-flags policy. OFF produces the same surfaces as
the no-key case across every sync-side consumer (endpoint 503,
watchdog boot-log + no-op, create-hook no-op, tool ``disabled``) with
NO error-state writes. The MCP plane server has an independent switch
(``PLANE_MCP_ENABLED``) at ``daemon/mcp/builtin_servers/plane.py``.

Re-drive idempotency (Phase 3 Step 4)
-------------------------------------
Before creating, the service lists Plane projects and looks for an
existing one matching ``plane_project_id`` OR matching the Ensemble
project's ``name`` (case-insensitive). If a match is found, it is
ADOPTED (UPDATE path) — never duplicate-created. The existing ``_create_or_adopt``
helper already implements name-based adoption; the new code path covers
the "metadata says error, but Plane has the row" recovery case.

v1 limitation
-------------
Status is **not** automatically mirrored to Plane (only computed for
logging). Sync happens on project creation (via auto-sync hooks in
``daemon.tools.project`` and ``daemon.routers.projects``) and on explicit
manual invocation via ``POST /api/plane/sync/{project_id}`` (Phase 3) or
the ``plane_sync_project`` agent tool. The
:class:`PlaneSyncWatchdogService` (Phase 3) drives the retry path for
projects stuck in ``error`` / ``drift``.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import logging
import os
from datetime import datetime, timezone
from typing import Any

from sqlmodel import Session

from daemon.clients.plane_http_client import (
    PlaneAPIError,
    PlaneAuthError,
    PlaneHttpClient,
    PlaneIdentifierCollisionError,
    PlaneNotFoundError,
    derive_plane_identifier,
    sanitize_plane_name,
)
from daemon.constants import (
    PLANE_ATTEMPT_COUNT_METADATA_KEY,
    PLANE_LAST_ATTEMPT_METADATA_KEY,
    PLANE_LAST_ERROR_METADATA_KEY,
    PLANE_PROJECT_ID_METADATA_KEY,
    PLANE_STATUS_MAP,
    PLANE_SYNC_STATE_DRIFT,
    PLANE_SYNC_STATE_ERROR,
    PLANE_SYNC_STATE_LINKED,
    PLANE_SYNC_STATE_METADATA_KEY,
    PLANE_SYNC_STATE_SYNCED_ALIAS,
    PLANE_SYNC_STATE_SYNCING,
    PLANE_SYNC_STATES,
    PLANE_SYNC_STATES_RETRYABLE,
    PLANE_SYNC_WATCHDOG_BACKOFF_BASE_SECONDS,
    PLANE_SYNC_WATCHDOG_BACKOFF_MAX_SECONDS,
    PLANE_SYNC_WATCHDOG_MAX_ATTEMPTS,
    PLANE_SYNCED_AT_METADATA_KEY,
)
from daemon.repositories.project.models import Project, ProjectMetadataRecord
from daemon.repositories.project.repository import SQLModelProjectRepository

logger = logging.getLogger(__name__)


def plane_sync_enabled() -> bool:
    """Return False when ``PLANE_SYNC_ENABLED`` opts the REST sync path out.

    ``PLANE_SYNC_ENABLED`` is an explicitly user-requested integration
    kill-switch (sanctioned 2026-09-20, see Phase-4 mission report) — a
    sanctioned exception to the project's no-flags policy (fix/flag
    policy, enforced 7d5285aa). Default (unset) is enabled. Values
    ``0`` / ``false`` / ``no`` / ``off`` (case-insensitive) disable;
    any other non-empty value keeps the integration on.

    This is the REST-sync side only (sync service + watchdog + POST
    endpoint + create-hooks + agent tool). The MCP plane server has its
    own independent switch (``PLANE_MCP_ENABLED``) at
    ``daemon/mcp/builtin_servers/plane.py``.
    """
    raw = os.environ.get("PLANE_SYNC_ENABLED", "").strip().lower()
    if not raw:
        return True
    return raw not in ("0", "false", "no", "off")


def _now_iso() -> str:
    """Return the current UTC time as ISO8601 string.

    TEXT-metadata timestamp producer for ``plane_last_attempt`` /
    ``plane_synced_at`` / ``plane_last_error``. For naive-UTC binds
    (``timestamp without time zone`` columns) use
    :func:`daemon.services.timestamps.now_utc_naive` instead — this
    helper is TEXT-only and must NEVER be bound to a naive column.
    """
    return datetime.now(timezone.utc).isoformat()


def _project_state_for_plane(status: str | None) -> str:
    """Map an Ensemble ``ProjectStatus`` value to Plane's state vocabulary."""
    if not status:
        return PLANE_STATUS_MAP.get("active", "active")
    return PLANE_STATUS_MAP.get(status, "active")


def _read_metadata_value(
    metadata_records: list[Any],
    key: str,
) -> Any:
    """Return ``meta_value`` from the first record matching ``key``.

    Operates on a pre-fetched list of ``ProjectMetadataRecord`` rows so
    callers can issue a single ``list_metadata_records`` call and filter
    client-side for the keys they care about (CR-6).
    """
    for record in metadata_records:
        if getattr(record, "meta_key", None) == key:
            return getattr(record, "meta_value", None)
    return None


def _coerce_int(value: Any, default: int = 0) -> int:
    """Coerce a metadata value to int (defensive — JSONB may carry strings)."""
    if value is None:
        return default
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, str):
        try:
            return int(value.strip())
        except (ValueError, AttributeError):
            return default
    return default


def normalize_state(raw: Any) -> str:
    """Normalize a stored ``plane_sync_state`` value to a canonical state.

    Phase 3 expansion:
    - ``"synced"`` → ``"linked"`` (back-compat alias from v1).
    - ``None`` → ``"error"`` (no prior sync recorded; treated as needing
      an initial sync; NOT auto-marked-error by the no-key code path).
    - anything outside the canonical vocabulary → ``"error"`` (treat the
      row as suspect; the next sync will resolve).

    Returns:
        A canonical state string, always one of ``PLANE_SYNC_STATES``.
    """
    if raw is None:
        # No prior sync recorded — for read paths we treat this as
        # "error" so the watchdog has a reason to retry, but the
        # SERVICE itself never marks a row "error" just because the
        # missing-key case happened (no-key behavior contract).
        return PLANE_SYNC_STATE_ERROR
    text = str(raw).strip()
    if text == PLANE_SYNC_STATE_SYNCED_ALIAS or text == PLANE_SYNC_STATE_LINKED:
        return PLANE_SYNC_STATE_LINKED
    if text in PLANE_SYNC_STATES:
        return text
    return PLANE_SYNC_STATE_ERROR


# Module-level reusable executor (W3).
#
# Avoids the two hazard patterns observed in the duplicated sync hooks:
#   (W1) ``with ThreadPoolExecutor() as executor: executor.submit(...)`` —
#        the ``with`` __exit__ calls ``shutdown(wait=True)``, which blocks
#        the caller until the background work finishes (defeats the
#        fire-and-forget intent).
#   (C2) bare ``asyncio.ensure_future`` + ``loop.run_until_complete`` —
#        raises ``RuntimeError: This event loop is already running`` and
#        leaks the orphan Task.
#
# A single module-level executor lets the fire-and-forget path submit and
# return immediately. ``max_workers=2`` caps concurrent syncs to two
# projects in flight at once (Plane API rate limits + DB pool safety).
_plane_sync_executor = concurrent.futures.ThreadPoolExecutor(max_workers=2)


def trigger_sync_fire_and_forget(
    project_id: str,
    project_repo: "SQLModelProjectRepository",
) -> None:
    """Fire-and-forget Plane sync — non-blocking, best-effort.

    Consolidates the async-driving pattern that was duplicated across
    ``daemon.tools.project``, ``daemon.routers.projects``, and
    ``daemon.tools.plane_sync`` (W3). Designed for the auto-sync hooks
    that run on project creation, where the caller must return quickly
    and must not be coupled to Plane's response.

    Handles three cases:

    1. No event loop bound → run directly via ``asyncio.run``.
    2. Event loop running → submit to the shared module-level
       ``_plane_sync_executor`` (a fresh thread with its own event loop).
    3. Any error during dispatch → log and swallow. The caller is never
       blocked and never sees an exception from Plane sync.

    This function is fire-and-forget: it returns as soon as the work is
    *submitted* (or run directly when no loop is bound). It does NOT
    call ``.result()`` on the executor's future — the agent tool
    ``plane_sync_project`` does that because the agent expects the
    result, but the auto-sync hooks do not.

    Args:
        project_id: The ensemble project UUID to sync.
        project_repo: Project repository for the sync service.
    """
    if not PlaneSyncService.is_available():
        return

    sync_service = PlaneSyncService(project_repo)

    async def _do_sync() -> None:
        try:
            result = await sync_service.sync_project(project_id)
            logger.info(
                "Plane sync completed for project %s: %s",
                project_id,
                result.get("status"),
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Plane sync failed for project %s: %s", project_id, exc
            )

    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            # Caller is inside a running loop — we cannot await from a
            # sync context. Submit to the shared executor (fresh thread,
            # fresh loop, runs asyncio.run internally).
            _plane_sync_executor.submit(asyncio.run, _do_sync())
        else:
            loop.run_until_complete(_do_sync())
    except RuntimeError:
        # No event loop bound at all — same fallback as the running-loop
        # path. Use the shared executor to keep pool usage consistent.
        _plane_sync_executor.submit(asyncio.run, _do_sync())
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "Plane sync dispatch error for project %s: %s", project_id, exc
        )


class PlaneSyncService:
    """Orchestrates project-level Plane sync.

    The service is intentionally cheap to construct — it holds no
    connection state and is safe to instantiate per-call (the agent
    tool does so via a factory pattern).
    """

    def __init__(
        self,
        project_repo: SQLModelProjectRepository,
        http_client: PlaneHttpClient | None = None,
    ) -> None:
        """Initialize the service.

        Args:
            project_repo: Project repository used to read/write Ensemble
                projects and their metadata.
            http_client: Optional :class:`PlaneHttpClient`. Defaults to
                :meth:`PlaneHttpClient.create` (returns ``None`` when the
                feature is disabled — handled gracefully in
                :meth:`sync_project`).
        """
        self._repo = project_repo
        # Lazy resolve — keeps imports tight and lets tests inject a
        # mock client without going through the env-var factory.
        self._http_client = http_client

    # ── Feature gating ──────────────────────────────────────────────────

    @classmethod
    def is_available(cls) -> bool:
        """Return True when the Plane integration is enabled AND configured.

        Two gates, both must pass:

        1. The ``PLANE_SYNC_ENABLED`` kill-switch (explicitly
           user-requested integration kill-switch, sanctioned
           2026-09-20 — see :func:`plane_sync_enabled`). Off → every
           sync-side consumer (REST endpoint 503, watchdog boot-log +
           no-op, create-hook no-op, agent tool disabled) no-ops with
           NO error-state writes, exactly matching the no-key
           discipline.
        2. The client env config (``PLANE_BASE_URL`` +
           ``PLANE_API_KEY``) via :meth:`PlaneHttpClient.is_available`.

        Use :meth:`unavailable_reason` for a human-readable reason when
        this returns False.
        """
        return cls.unavailable_reason() is None

    @classmethod
    def unavailable_reason(cls) -> str | None:
        """Return a human-readable unavailability reason, or None when up."""
        if not plane_sync_enabled():
            return "disabled by kill-switch (PLANE_SYNC_ENABLED=false)"
        if not PlaneHttpClient.is_available():
            return "not configured (PLANE_BASE_URL / PLANE_API_KEY not set)"
        return None

    def _get_client(self) -> PlaneHttpClient | None:
        """Resolve the HTTP client, defaulting to ``PlaneHttpClient.create``.

        Returns ``None`` when feature gating fails so :meth:`sync_project`
        can short-circuit without raising.
        """
        if self._http_client is not None:
            return self._http_client
        return PlaneHttpClient.create()

    # ── Internal helpers ────────────────────────────────────────────────

    def _set_metadata(self, project_id: str, key: str, value: Any) -> bool:
        """Persist a single metadata record (best-effort).

        Uses ``set_metadata_record`` directly — not ``set_metadata`` — so
        we don't churn ``Project.updated_at`` on every sync attempt
        (CR-6).

        Returns:
            ``True`` on successful write, ``False`` when the write failed
            and the exception was caught. The caller is responsible for
            deciding whether the failure is critical (e.g. the
            ``plane_project_id`` handle — losing this would cause the next
            sync to create a duplicate Plane project) or merely advisory
            (e.g. ``synced_at`` timestamp — losing this only delays
            observability).
        """
        try:
            with Session(self._repo.engine) as session:
                self._repo.set_metadata_record(session, project_id, key, value)
                session.commit()
            return True
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Plane sync: failed to persist metadata %s for project %s: %s",
                key,
                project_id,
                exc,
            )
            return False

    def _bump_attempt_count(self, project_id: str) -> int | None:
        """Increment ``plane_attempt_count`` (saturating).

        Reads the current value, increments, writes back. Saturates at
        ``PLANE_SYNC_WATCHDOG_MAX_ATTEMPTS`` so the JSONB column never
        overflows on a long outage (the watchdog's quarantine logic
        treats anything at-or-above as "maxed"; we keep the cap in the
        data so the next attempt is forced through the operator path).

        Returns:
            The post-increment value (clamped), or ``None`` when the
            READ failed. A failed read is ambiguous (the stored counter
            may hold any value) — returning ``None`` makes the caller
            SKIP the write-back entirely, PRESERVING the stored value
            instead of silently resetting it to 1. (Phase-4 advisory j:
            the old code returned 0 on read failure, so the write-back
            ``min(0+1, MAX)`` clobbered a stored counter of e.g. 4 back
            down to 1 — the quarantine ceiling became unreachable on
            any read hiccup.)

        Note: the returned value is advisory telemetry for the response
        body; the durable state is the metadata row itself.
        """
        try:
            with Session(self._repo.engine) as session:
                records = self._repo.list_metadata_records(session, project_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Plane sync: failed to read attempt count for %s — "
                "preserving stored value (no increment): %s",
                project_id,
                exc,
            )
            return None
        current = _coerce_int(
            _read_metadata_value(records, PLANE_ATTEMPT_COUNT_METADATA_KEY),
            default=0,
        )
        next_val = min(current + 1, PLANE_SYNC_WATCHDOG_MAX_ATTEMPTS)
        self._set_metadata(project_id, PLANE_ATTEMPT_COUNT_METADATA_KEY, next_val)
        return next_val

    def _reset_attempt_count(self, project_id: str) -> None:
        """Reset ``plane_attempt_count`` to 0 — called on every success."""
        self._set_metadata(project_id, PLANE_ATTEMPT_COUNT_METADATA_KEY, 0)

    def get_state_metadata(self, project_id: str) -> dict[str, Any]:
        """Read all Plane-related metadata for a project (best-effort).

        Returns a dict with these keys (any may be missing/None):
        ``plane_project_id``, ``plane_sync_state``, ``plane_synced_at``,
        ``plane_last_attempt``, ``plane_attempt_count``, ``plane_last_error``.

        Used by the watchdog, the HTTP endpoint, and the in-flight re-
        entrancy guard. Failures degrade to an empty dict (the watchdog
        treats that as "no prior state" → fresh sync).
        """
        try:
            with Session(self._repo.engine) as session:
                records = self._repo.list_metadata_records(session, project_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Plane sync: failed to read metadata for %s: %s",
                project_id,
                exc,
            )
            return {}
        keys = (
            PLANE_PROJECT_ID_METADATA_KEY,
            PLANE_SYNC_STATE_METADATA_KEY,
            PLANE_SYNCED_AT_METADATA_KEY,
            PLANE_LAST_ATTEMPT_METADATA_KEY,
            PLANE_ATTEMPT_COUNT_METADATA_KEY,
            PLANE_LAST_ERROR_METADATA_KEY,
        )
        return {
            key: _read_metadata_value(records, key) for key in keys
        }

    def claim_sync_slot(self, project_id: str) -> bool:
        """Atomically claim the sync slot (rowcount-guarded CAS).

        Phase-4 advisory a(1): the claim is a SINGLE conditional
        upsert — INSERT the ``plane_sync_state="syncing"`` record, or on
        conflict UPDATE it only ``WHERE meta_value IS DISTINCT FROM
        'syncing'`` — so a concurrent watchdog tick + manual POST can
        never both claim. The previous read-check-write sequence had a
        TOCTOU window: two claimers could both read a non-syncing state
        and both proceed. The winner is now decided by the DB: exactly
        one claimer's statement affects a row.

        Returns:
            True when the caller now OWNS the sync slot (row inserted
            or updated). False when the row is already in ``syncing``
            (another caller holds the slot — the HTTP caller surfaces
            409, the watchdog skips) or when the guard write itself
            failed (fail-closed: without a durable claim the sync must
            not run, or a crash mid-sync could strand the row wedged).

        The guard write stays inside a committed transaction; the
        follow-up ``plane_last_attempt`` stamp after a successful claim
        remains best-effort (crash-mid-sync then leaves an observable
        trail via the ``syncing`` state + boot-sweep recovery).
        """
        now = _now_iso()
        try:
            with Session(self._repo.engine) as session:
                insert_fn = self._repo._get_dialect_insert(session)
                stmt = insert_fn(ProjectMetadataRecord).values(
                    project_id=project_id,
                    meta_key=PLANE_SYNC_STATE_METADATA_KEY,
                    meta_value=PLANE_SYNC_STATE_SYNCING,
                    created_at=now,
                    updated_at=now,
                )
                stmt = stmt.on_conflict_do_update(
                    index_elements=["project_id", "meta_key"],
                    set_={
                        "meta_value": PLANE_SYNC_STATE_SYNCING,
                        "updated_at": now,
                    },
                    # Evaluated against the EXISTING row: only steal the
                    # slot when it is not already held ("syncing").
                    # IS DISTINCT FROM (not !=) so a NULL-valued row
                    # (should not happen for the state key, but be safe)
                    # is claimable rather than wedged forever.
                    where=(
                        ProjectMetadataRecord.meta_value.is_distinct_from(
                            PLANE_SYNC_STATE_SYNCING
                        )
                    ),
                )
                result = session.execute(stmt)
                session.commit()
                claimed = bool(result.rowcount)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Plane sync: claim_sync_slot guard write failed for %s — "
                "refusing to sync (fail-closed): %s",
                project_id,
                exc,
            )
            return False
        if not claimed:
            return False
        # Best-effort: stamp the attempt timestamp up front so even a
        # crash-mid-sync leaves an observable trail for the next boot.
        self._set_metadata(
            project_id,
            PLANE_LAST_ATTEMPT_METADATA_KEY,
            _now_iso(),
        )
        return True

    def release_sync_slot(
        self,
        project_id: str,
        new_state: str,
        *,
        last_error: str | None = None,
    ) -> None:
        """Roll the row back from ``syncing`` to ``new_state`` and stamp attempt.

        Always called from the tail of a sync flow (success OR failure)
        so the slot is freed for the next caller. The attempt timestamp
        is rewritten so it matches the actual attempt boundary (the
        initial claim stamped it optimistically — this is the canonical
        record).
        """
        self._set_metadata(
            project_id,
            PLANE_SYNC_STATE_METADATA_KEY,
            new_state,
        )
        self._set_metadata(
            project_id,
            PLANE_LAST_ATTEMPT_METADATA_KEY,
            _now_iso(),
        )
        if last_error is not None:
            self._set_metadata(
                project_id,
                PLANE_LAST_ERROR_METADATA_KEY,
                last_error[:500],
            )

    def is_stale_syncing(
        self,
        project_id: str,
        *,
        stale_after_seconds: int,
        now: datetime | None = None,
    ) -> bool:
        """Return True when the row looks wedged in ``syncing``.

        A row whose ``plane_last_attempt`` is older than
        ``stale_after_seconds`` was most likely claimed by a process
        that died mid-sync (crash / kill -9 between claim and release):
        the slot-claim contract guarantees a live sync releases its own
        slot in the same flow, so a syncing row with an ancient attempt
        timestamp is a crash-recovery wedge (the class the Phase-3
        incident history is full of).

        A missing / unparseable ``plane_last_attempt`` counts as stale —
        the claim stamps the attempt immediately after taking the slot,
        so absence implies the claimer died between the two writes.
        """
        meta = self.get_state_metadata(project_id)
        state = normalize_state(meta.get(PLANE_SYNC_STATE_METADATA_KEY))
        if state != PLANE_SYNC_STATE_SYNCING:
            return False
        last_attempt_raw = meta.get(PLANE_LAST_ATTEMPT_METADATA_KEY)
        if not last_attempt_raw:
            return True
        try:
            last_dt = datetime.fromisoformat(str(last_attempt_raw))
        except (TypeError, ValueError):
            return True
        now_dt = now if now is not None else datetime.now(timezone.utc)
        if last_dt.tzinfo is None:
            last_dt = last_dt.replace(tzinfo=timezone.utc)
        return (now_dt - last_dt).total_seconds() > stale_after_seconds

    def fail_stale_syncing(self, project_id: str) -> bool:
        """Steal a wedged ``syncing`` slot by marking it ``error`` (CAS).

        Phase-4 advisory a(2) half of the boot-sweep recovery: the
        caller (watchdog sweep) re-drives the row in the same tick — a
        row marked ``error`` here is immediately retryable, so the sweep
        feeds it straight back through :meth:`sync_project`.

        Rowcount-guarded like :meth:`claim_sync_slot`: the conditional
        UPDATE only fires ``WHERE meta_value == 'syncing'``, so a sync
        that came ALIVE between the staleness check and this steal (slow
        but healthy attempt) is not clobbered — its own release wins.

        Returns True when this caller marked the row ``error``.
        """
        now = _now_iso()
        try:
            with Session(self._repo.engine) as session:
                insert_fn = self._repo._get_dialect_insert(session)
                stmt = insert_fn(ProjectMetadataRecord).values(
                    project_id=project_id,
                    meta_key=PLANE_SYNC_STATE_METADATA_KEY,
                    meta_value=PLANE_SYNC_STATE_ERROR,
                    created_at=now,
                    updated_at=now,
                )
                stmt = stmt.on_conflict_do_update(
                    index_elements=["project_id", "meta_key"],
                    set_={
                        "meta_value": PLANE_SYNC_STATE_ERROR,
                        "updated_at": now,
                    },
                    where=(
                        ProjectMetadataRecord.meta_value
                        == PLANE_SYNC_STATE_SYNCING
                    ),
                )
                result = session.execute(stmt)
                session.commit()
                return bool(result.rowcount)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Plane sync: stale-syncing recovery write failed for %s: %s",
                project_id,
                exc,
            )
            return False

    def is_retry_eligible(self, project_id: str, *, now: datetime | None = None) -> bool:
        """Return True when the project's backoff window has elapsed.

        Used by the watchdog sweep to filter candidates before paying
        for a Plane HTTP call. Returns True when:
        - the row is in a retryable state (error / drift), AND
        - the row's ``plane_attempt_count`` is below the watchdog's
          ``PLANE_SYNC_WATCHDOG_MAX_ATTEMPTS`` ceiling, AND
        - enough time has passed since ``plane_last_attempt`` per the
          exponential-backoff formula
          ``min(MAX, BASE * 2 ** (count - 2))``.
        """
        meta = self.get_state_metadata(project_id)
        state = normalize_state(meta.get(PLANE_SYNC_STATE_METADATA_KEY))
        if state not in PLANE_SYNC_STATES_RETRYABLE:
            return False
        count = _coerce_int(meta.get(PLANE_ATTEMPT_COUNT_METADATA_KEY), default=0)
        if count >= PLANE_SYNC_WATCHDOG_MAX_ATTEMPTS:
            return False
        backoff = compute_backoff_seconds(
            count,
            base=PLANE_SYNC_WATCHDOG_BACKOFF_BASE_SECONDS,
            cap=PLANE_SYNC_WATCHDOG_BACKOFF_MAX_SECONDS,
        )
        last_attempt_raw = meta.get(PLANE_LAST_ATTEMPT_METADATA_KEY)
        if not last_attempt_raw:
            return True
        try:
            last_dt = datetime.fromisoformat(str(last_attempt_raw))
        except (TypeError, ValueError):
            return True
        now_dt = now if now is not None else datetime.now(timezone.utc)
        # Tolerate a stored naive value: assume UTC (matches
        # ``daemon.services.timestamps.coerce_to_aware_utc`` policy).
        if last_dt.tzinfo is None:
            last_dt = last_dt.replace(tzinfo=timezone.utc)
        return (now_dt - last_dt).total_seconds() >= backoff

    # ── Public API ──────────────────────────────────────────────────────

    async def sync_project(
        self,
        project_id: str,
        force: bool = False,  # noqa: ARG002 — accepted for forward-compat
        *,
        claim_slot: bool = True,
    ) -> dict[str, Any]:
        """Sync a single Ensemble project to Plane.

        The algorithm (Phase 3 expansion):

        1. **Feature gate** — short-circuit cleanly when the
            ``PLANE_SYNC_ENABLED`` kill-switch is off or env vars are
            missing (returns ``status="disabled"`` without touching
            project state).
        2. **Load project** — 404 / not-found returns ``status="error"``.
        3. **Re-entrancy claim** — atomically transition to ``syncing``;
           a row already in ``syncing`` returns ``status="syncing"``
           (the HTTP endpoint maps that to 409).
        4. **Read metadata** in one call (CR-6), filter for the Plane keys.
        5. **Drive CREATE / UPDATE path**:
           a. ``plane_project_id`` set → UPDATE path.
           b. Else → CREATE path. To avoid duplicates, list Plane
              projects and search by name (case-insensitive). If a match
              exists, ADOPT it (UPDATE path) — never duplicate-create.
        6. **Drift check** — on UPDATE success, compare identity fields
           between the Ensemble project and the freshly-updated Plane
           response. Divergence (other than the fields we just sent) is
           flagged ``drift``; agreement is ``linked``.
        7. **On success** — persist ``plane_project_id``,
           ``plane_sync_state="linked"`` (or ``"drift"`` if flagged),
           ``plane_synced_at``, ``plane_last_attempt``,
           ``plane_attempt_count=0``.
        8. **On failure** — persist ``plane_sync_state="error"``,
           ``plane_attempt_count+=1``, ``plane_last_error``, and the
           attempt timestamp.
        9. **Release the slot** in the same flow so the next caller can
           proceed.

        Args:
            project_id: Ensemble project UUID.
            force: Accepted for forward-compat with the cooldown layer;
                does not affect this service's behavior.
            claim_slot: When True (default), perform the re-entrancy CAS
                before doing the work. Set False ONLY when the caller
                has already claimed the slot (e.g. internal callers that
                hold a claim from a higher-level helper).

        Returns:
            Dict with ``status``
            (``"linked"`` | ``"drift"`` | ``"error"`` | ``"syncing"`` |
            ``"disabled"`` | ``"not_found"``), ``action``
            (``"created"`` | ``"updated"``), and
            ``plane_project_id`` when known.
        """
        # Feature gate — short-circuit cleanly when the PLANE_SYNC_ENABLED
        # kill-switch is off (Phase 4; explicitly user-requested,
        # sanctioned 2026-09-20) or env vars are missing. Per Phase-3
        # Step 3 no-key behavior: we MUST NOT mark projects
        # ``error`` merely because the integration is off. A disabled
        # return carries no state mutation. (Primary gates live at the
        # consumers via ``is_available()``; this is defense-in-depth for
        # direct callers.)
        client = self._get_client()
        if client is None or not plane_sync_enabled():
            if not claim_slot:
                # Wave-2 advisory: the caller already holds the slot
                # (``claim_slot=False`` path used by the HTTP router,
                # which claims BEFORE invoking sync_project). If the
                # caller released the slot on early-return, the row
                # would stay wedged in ``syncing`` for up to
                # ``STALE_SYNCING_INTERVAL_MULTIPLIER x interval``
                # (default 2 x 300s = 600s) waiting for the watchdog's
                # stale-steal pass to heal it. Release here to
                # ``error`` so the next call is immediately retryable.
                # Pre-claim state is unknown at this point (the
                # ``prior_state`` capture happens AFTER this branch);
                # ``error`` is the documented fallback (set
                # ``plane_last_error`` so the operator log explains
                # why the row is no longer ``syncing``).
                self.release_sync_slot(
                    project_id,
                    PLANE_SYNC_STATE_ERROR,
                    last_error=(
                        "early-return: sync disabled while caller "
                        "held the claim (claim_slot=False)"
                    ),
                )
            return {
                "status": "disabled",
                "message": f"Plane sync {self.unavailable_reason()}",
            }

        # 1. Load project.
        project = self._repo.get(project_id)
        if project is None:
            logger.warning(
                "Plane sync: project %s not found — skipping", project_id
            )
            if not claim_slot:
                # Wave-2 advisory (mirror of the disabled-path branch
                # above): release the slot the caller is holding so
                # the next caller is not blocked behind a stale
                # ``syncing`` row for the stale-steal window. Same
                # rationale: pre-claim state is unknown here;
                # ``error`` + ``plane_last_error`` is the documented
                # fallback.
                self.release_sync_slot(
                    project_id,
                    PLANE_SYNC_STATE_ERROR,
                    last_error=(
                        "early-return: project not found while "
                        "caller held the claim (claim_slot=False)"
                    ),
                )
            return {
                "status": "not_found",
                "action": None,
                "message": f"Project {project_id} not found",
            }

        # 3. Read metadata BEFORE claiming the slot — the prior_state
        # capture must observe the row as it was when we arrived, not
        # the transient "syncing" value we are about to write.
        try:
            with Session(self._repo.engine) as session:
                metadata_records = self._repo.list_metadata_records(
                    session, project_id
                )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Plane sync: failed to read metadata for %s: %s",
                project_id,
                exc,
            )
            metadata_records = []

        existing_plane_id = _read_metadata_value(
            metadata_records, PLANE_PROJECT_ID_METADATA_KEY
        )
        prior_state = normalize_state(
            _read_metadata_value(metadata_records, PLANE_SYNC_STATE_METADATA_KEY)
        )

        # 4. Re-entrancy claim. (Stays AFTER the metadata read so the
        # ``prior_state`` capture above sees the row as it was.)
        if claim_slot and not self.claim_sync_slot(project_id):
            return {
                "status": PLANE_SYNC_STATE_SYNCING,
                "action": None,
                "message": "A sync is already in flight for this project",
            }

        # 5. Drive the CREATE / UPDATE path.
        try:
            if existing_plane_id:
                plane_id, action, plane_response = await self._update_existing(
                    client, project, existing_plane_id
                )
            else:
                # Phase 3 Step 4 — re-drive idempotency: before CREATING,
                # list Plane projects and ADOPT any matching name.
                # Pre-existing rows may be in ``error`` because the
                # sync attempt was interrupted AFTER Plane had already
                # created the row but BEFORE the metadata write landed;
                # the listing path recovers that case.
                plane_id, action, plane_response = await self._create_or_adopt(
                    client, project
                )
        except PlaneAuthError as exc:
            logger.warning(
                "Plane sync: auth error syncing project %s: %s",
                project_id,
                exc,
            )
            attempt = self._bump_attempt_count(project_id)
            self.release_sync_slot(
                project_id,
                PLANE_SYNC_STATE_ERROR,
                last_error=f"auth: {exc}",
            )
            return {
                "status": "error",
                "action": None,
                "message": "Plane authentication failed — check PLANE_API_KEY",
                "attempt": attempt,
            }
        except PlaneNotFoundError as exc:
            # UPDATE path 404 means stored plane_project_id is stale —
            # recover by clearing it and retrying as CREATE on the NEXT
            # call. The current attempt fails (the slot is released to
            # ``error``) so the watchdog re-drives cleanly.
            logger.warning(
                "Plane sync: stored plane_project_id missing for %s: %s",
                project_id,
                exc,
            )
            attempt = self._bump_attempt_count(project_id)
            # Clear the stale handle so the next attempt falls through
            # to the CREATE/adopt path.
            self._set_metadata(project_id, PLANE_PROJECT_ID_METADATA_KEY, None)
            self.release_sync_slot(
                project_id,
                PLANE_SYNC_STATE_ERROR,
                last_error=f"stale plane_project_id: {exc}",
            )
            return {
                "status": "error",
                "action": None,
                "message": f"Stored Plane project missing — will recreate: {exc}",
                "attempt": attempt,
            }
        except PlaneAPIError as exc:
            logger.warning(
                "Plane sync: API error syncing project %s: %s",
                project_id,
                exc,
            )
            attempt = self._bump_attempt_count(project_id)
            self.release_sync_slot(
                project_id,
                PLANE_SYNC_STATE_ERROR,
                last_error=f"api: {exc}",
            )
            return {
                "status": "error",
                "action": None,
                "message": f"Plane API error: {exc}",
                "attempt": attempt,
            }
        except Exception as exc:  # noqa: BLE001 — last-resort guard
            logger.warning(
                "Plane sync: unexpected error syncing project %s: %s",
                project_id,
                exc,
            )
            attempt = self._bump_attempt_count(project_id)
            self.release_sync_slot(
                project_id,
                PLANE_SYNC_STATE_ERROR,
                last_error=f"unexpected: {exc}",
            )
            return {
                "status": "error",
                "action": None,
                "message": f"Unexpected error: {exc}",
                "attempt": attempt,
            }

        # 6. Persist success metadata. The plane_project_id handle is the
        # critical key — if its write fails, we must NOT report "linked",
        # because the next sync would treat this project as fresh and
        # create a duplicate on Plane. The other keys are advisory
        # (state for observability, timestamp for the UI) — we log and
        # continue if they fail.
        now = _now_iso()
        id_ok = self._set_metadata(
            project_id, PLANE_PROJECT_ID_METADATA_KEY, plane_id
        )
        if not id_ok:
            attempt = self._bump_attempt_count(project_id)
            self.release_sync_slot(
                project_id,
                PLANE_SYNC_STATE_ERROR,
                last_error="plane_project_id metadata write failed",
            )
            return {
                "status": "error",
                "action": action,
                "plane_project_id": plane_id,
                "message": (
                    "Plane project created but metadata write failed. "
                    "Manual reconciliation needed."
                ),
                "attempt": attempt,
            }
        # Reset attempt counter on any successful identity-confirmed
        # sync — drift is still a "synced" outcome at the API level, so
        # the counter resets and the row is flagged for the watchdog to
        # re-drive (the corrective sync is what heals drift).
        self._reset_attempt_count(project_id)

        # Phase 3 Step 2 — drift check on UPDATE paths (the UPDATE path
        # and the ADOPT path both land here with action="updated").
        # A fresh CREATE carries no prior Plane state to compare against
        # (``plane_response`` is ``{}``), so it is always "linked" (a
        # fresh project trivially agrees with itself).
        #
        # ``plane_response`` is the dict Plane returned from the UPDATE
        # — no extra HTTP call needed. The drift check is intentionally
        # lightweight: only name + description are compared (the
        # identity fields the v1 sync owns). A divergence here is
        # logged + flagged but does NOT fail the sync — the API call
        # succeeded, so the row is recoverable on the next corrective
        # attempt.
        new_state = PLANE_SYNC_STATE_LINKED
        if action == "updated" and _is_drift(project, plane_response):
            new_state = PLANE_SYNC_STATE_DRIFT

        if not self._set_metadata(
            project_id, PLANE_SYNC_STATE_METADATA_KEY, new_state
        ):
            logger.warning(
                "Plane sync: sync_state metadata write failed for %s",
                project_id,
            )
        if not self._set_metadata(
            project_id, PLANE_SYNCED_AT_METADATA_KEY, now
        ):
            logger.warning(
                "Plane sync: synced_at metadata write failed for %s",
                project_id,
            )
        # Note: release_sync_slot's stamp-on-attempt is the canonical
        # ``plane_last_attempt``; calling it AFTER the success writes
        # means a watcher reading the timestamp never sees the row in
        # the new state with a stale attempt timestamp.
        self.release_sync_slot(project_id, new_state)

        # If we transitioned out of ``drift`` via a corrective sync,
        # note it for the operator log. Prior state was captured before
        # the attempt started so we can tell "drift → linked" (healed)
        # apart from a steady-state "linked → linked" no-op.
        if prior_state == PLANE_SYNC_STATE_DRIFT and new_state == PLANE_SYNC_STATE_LINKED:
            logger.info(
                "Plane sync: drift resolved for project %s -> Plane %s",
                project_id,
                plane_id,
            )

        return {
            "status": new_state,
            "action": action,
            "plane_project_id": plane_id,
            "synced_at": now,
        }

    # ── Sync paths ──────────────────────────────────────────────────────

    async def _update_existing(
        self,
        client: PlaneHttpClient,
        project: Project,
        plane_id: str,
    ) -> tuple[str, str, dict[str, Any]]:
        """Update an already-known Plane project.

        Returns ``(plane_id, "updated", plane_response)``. ``plane_response``
        is the dict Plane returned from the PATCH call, used by the
        caller's drift check to avoid a second HTTP roundtrip via
        ``client.get_project``.

        Phase 3 note: ``PlaneNotFoundError`` is **propagated** to the
        caller — the stale-handle case (Plane row was deleted out-of-band)
        is handled by ``sync_project`` clearing the handle + marking
        ``error`` so the watchdog re-drives the row cleanly on the next
        sweep. The legacy fallback-to-CREATE behavior is preserved for
        ``plane_id=None`` callers (none currently exist) but no longer
        silent — it would have masked the recovery path the watchdog
        now owns.
        """
        # v1: state computed for observability only, not pushed to Plane API.
        # Plane has no stable project-level "state" field — the mapping is
        # purely informational and only surfaces in our log line below.
        # ``network`` and other Plane fields are deliberately omitted from
        # the v1 surface.
        plane_state = _project_state_for_plane(project.status)
        response = await client.update_project(
            plane_id,
            name=project.name,
            description=project.description,
        )
        logger.debug(
            "Plane sync: updated project %s -> Plane %s (state=%s)",
            project.project_id,
            plane_id,
            plane_state,
        )
        return plane_id, "updated", (response if isinstance(response, dict) else {})

    async def _create_or_adopt(
        self,
        client: PlaneHttpClient,
        project: Project,
    ) -> tuple[str, str, dict[str, Any]]:
        """CREATE path with duplicate-by-name avoidance.

        Before creating, lists all Plane projects in the workspace and
        searches for one with a matching ``name``. If found, adopts its
        ID (UPDATE path) — this prevents duplicates when the same
        Ensemble project is re-synced after the metadata record was lost.

        Returns:
            ``(plane_id, action, plane_response)``. ``action`` is
            ``"created"`` | ``"updated"``. ``plane_response`` is the
            dict Plane returned for the adopt-path UPDATE (drift-check
            input — Phase-4 advisory i: adoption previously discarded
            it, so a Plane-side divergence at adoption time was
            invisible and the row was mis-marked ``linked``); the
            fresh-CREATE path returns ``{}`` (no prior Plane state to
            compare against — the fresh row trivially agrees).
        """
        try:
            plane_projects = await client.list_projects()
        except PlaneAPIError:
            # Wave-2 advisory: a transient list failure MUST NOT fall
            # through to CREATE — a duplicate Plane row can be produced
            # when the project already exists in Plane but the listing
            # call hit a transient error (Plane-side outage, rate
            # limit, network blip). The list is the only idempotency
            # guard before CREATE; if we cannot enumerate we MUST NOT
            # create. Re-raise so the outer ``sync_project``'s
            # ``except PlaneAPIError`` handler stamps ``error`` on the
            # row (status='error', plane_last_error set,
            # plane_attempt_count bumped) — the watchdog re-drives on
            # the next sweep, retrying cleanly. The slot is released
            # to ``error`` in that handler, so a wedged ``syncing``
            # row is also healed here.
            raise

        existing_id = _find_plane_id_by_name(plane_projects, project.name)
        if existing_id:
            logger.info(
                "Plane sync: adopting existing Plane project %s for %s",
                existing_id,
                project.name,
            )
            response = await client.update_project(
                existing_id,
                name=project.name,
                description=project.description,
            )
            return (
                existing_id,
                "updated",
                response if isinstance(response, dict) else {},
            )

        # Fresh CREATE path. Plane ``identifier`` is REQUIRED
        # (verified live 2026-09-20 — POST without ``identifier``
        # returns 400 ``{"identifier":["This field is required."]}``).
        # We derive a deterministic identifier from the ensemble
        # project: prefer ``shortnames[0]`` (already short + human-set),
        # fall back to ``name``; sanitization rule lives in the client
        # (see ``derive_plane_identifier``).
        base_name = _project_identifier_base(project)
        new_id: str | None = None
        for attempt in range(1, _PLANE_IDENTIFIER_MAX_ATTEMPTS + 1):
            identifier = derive_plane_identifier(base_name, attempt=attempt)
            try:
                created = await client.create_project(
                    name=project.name,
                    description=project.description,
                    identifier=identifier,
                )
                break
            except PlaneIdentifierCollisionError:
                # 409 — Plane has another project with this identifier.
                # Deterministic suffix scheme: bump attempt; the suffix
                # never collides with attempt 1 because the base is
                # truncated to fit (see ``derive_plane_identifier``).
                logger.info(
                    "Plane sync: identifier %r taken for %s, retrying "
                    "with attempt=%d/%d",
                    identifier,
                    project.name,
                    attempt + 1,
                    _PLANE_IDENTIFIER_MAX_ATTEMPTS,
                )
                if attempt >= _PLANE_IDENTIFIER_MAX_ATTEMPTS:
                    # Exhausted the deterministic retry budget — let
                    # the outer error handler stamp ``error`` and the
                    # watchdog re-drive on the next sweep.
                    raise PlaneAPIError(
                        f"Plane identifier collision on all "
                        f"{_PLANE_IDENTIFIER_MAX_ATTEMPTS} deterministic "
                        f"attempts (last tried: {identifier!r})"
                    ) from None
                continue
        else:
            # Unreachable: the loop either breaks on success or raises
            # on the final attempt. Belt-and-braces explicit guard.
            raise PlaneAPIError(
                "Plane create_project exhausted retries without success"
            )

        new_id = created.get("id")
        if not new_id:
            raise PlaneAPIError(
                f"Plane create_project returned no id: {created!r}"
            )
        logger.debug(
            "Plane sync: created project %s -> Plane %s",
            project.project_id,
            new_id,
        )
        return str(new_id), "created", {}


def _find_plane_id_by_name(
    plane_projects: list[dict[str, Any]],
    name: str,
) -> str | None:
    """Return the first Plane project ID whose ``name`` matches, case-insensitive.

    BOTH sides are passed through :func:`sanitize_plane_name` before
    comparison: Plane stores the SANITIZED form of the Ensemble name
    (e.g. ``agents-ensemble`` → ``agents ensemble`` — hyphens are
    rejected with HTTP 400), so matching raw-vs-Plane-side names would
    never match for hyphenated projects → duplicate-create attempts on
    every sync. Sanitizing both sides makes the adoption lookup agree
    with what create/update actually store.

    Defensive against missing ``id``/``name`` keys — Plane's API is not
    strictly typed and we should not crash on a shape mismatch. Projects
    with empty/missing names are skipped (their sanitized form would be
    the non-empty fallback constant, which must never false-match).
    """
    if not name:
        return None
    target = sanitize_plane_name(name).strip().lower()
    for proj in plane_projects:
        raw_name = str(proj.get("name") or "")
        if not raw_name.strip():
            continue
        proj_name = sanitize_plane_name(raw_name).strip().lower()
        if proj_name == target:
            pid = proj.get("id")
            if pid is not None:
                return str(pid)
    return None


# How many deterministic attempts before giving up on CREATE and
# letting the watchdog re-drive. Bound guards against pathological
# identifier-poisoning by another team accidentally squatting all
# derived identifiers.
_PLANE_IDENTIFIER_MAX_ATTEMPTS: int = 5


def _project_identifier_base(project: Project) -> str:
    """Pick the human-readable seed for ``derive_plane_identifier``.

    Preference order (verified against the existing 3 workspace
    projects — ``NEA``/``Ensemble``/``LLM Proxy`` — and against
    Ensemble's own naming convention):

    1. ``shortnames[0]`` — already a short human-set slug; usually
       the closest match to the desired Plane ``identifier``.
    2. ``name`` — full project name (works for projects without
       shortnames); sanitization in the derivation helper handles
       spaces and special chars.

    The returned string is NOT sanitized — that's the derivation
    helper's job. This keeps the policy in ONE place.
    """
    shortnames = getattr(project, "shortnames", None) or []
    if shortnames:
        first = shortnames[0]
        if first and first.strip():
            return first
    return project.name or ""


def _is_drift(project: Project, plane_response: dict[str, Any]) -> bool:
    """Compare identity fields between an Ensemble project and a Plane response.

    Drift is a non-blocking state — the row stays "synced at the API
    level" but the watchdog is alerted to re-drive so the corrective
    sync can push the Ensemble values back. Identity fields we
    currently own: ``name``, ``description``.

    Semantics: drift is flagged ONLY when Plane RESPONDS with a value
    that disagrees with the Ensemble side. If Plane returns a dict
    that does NOT carry a given field (e.g. the test mocks return
    ``{"id": "..."}`` only, or Plane omits a field on a partial
    response), we treat it as "no info" rather than "drift". This
    matches the heuristic in the legacy service: a successful HTTP
    response with no contradiction is NOT drift, even if the response
    is sparse.

    A Plane response with both fields absent / whitespace-only is
    trivially "no info" → no drift (covers the
    ``update_project``-returns-id-only test mocks). The check is
    intentionally case-insensitive after stripping whitespace so
    cosmetic edits (capitalization, trailing whitespace) do not flag
    drift.

    Name comparison is done on SANITIZED forms (:func:`sanitize_plane_name`):
    Plane stores the sanitized name (hyphens etc. are rejected with
    HTTP 400 at create/update), so comparing the raw Ensemble name
    against the stored Plane name would flag perpetual false drift on
    every hyphenated project — each corrective sync would rewrite the
    same sanitized value and re-flag drift forever. Sanitizing both
    sides compares like-for-like.

    Returns True when the row has drifted; False when the fields
    agree OR when Plane's response carries no information to compare.
    """
    if not plane_response:
        return False
    # Name check — only fire if BOTH sides have a non-empty RAW value.
    # (Emptiness is judged on the raw values so the sanitizer's
    # non-empty fallback constant can't fabricate a comparison where
    # one side has no name at all.)
    plane_name_raw = plane_response.get("name")
    if plane_name_raw is not None:
        plane_name_str = str(plane_name_raw).strip()
        ens_name_str = (project.name or "").strip()
        if plane_name_str and ens_name_str:
            plane_name = sanitize_plane_name(plane_name_str).lower()
            ens_name = sanitize_plane_name(ens_name_str).lower()
            if plane_name != ens_name:
                return True
    # Description check — same rule. Absence on Plane side is "no info".
    plane_desc_raw = plane_response.get("description")
    if plane_desc_raw is not None:
        plane_desc = str(plane_desc_raw).strip()
        ens_desc = (project.description or "").strip()
        if plane_desc != ens_desc:
            # Both sides are already stripped here, so whitespace-only
            # differences were normalized away — reaching this branch
            # means the CONTENT genuinely differs → drift.
            return True
    return False


def compute_backoff_seconds(
    attempt_count: int,
    *,
    base: int,
    cap: int,
) -> float:
    """Return the per-project backoff window in seconds.

    Real behavior: ``attempt_count <= 1`` returns ``0.0`` IMMEDIATELY
    (early-out before the formula — first/second failure retry on the
    next tick, no backoff). For ``attempt_count >= 2`` the window is
    ``min(cap, base * 2 ** (attempt_count - 2))``.

    Per :data:`daemon.constants.PLANE_SYNC_WATCHDOG_BACKOFF_BASE_SECONDS`
    defaults this yields:
      - count=0/1 → 0.0 (immediate — early-out, formula not applied)
      - count=2 → base (60s)
      - count=3 → 2*base (120s)
      - count=4 → 4*base (240s)
      - count≥5 → 8*base=480s (capped — never exceeds ``cap``)

    The cap is the operator ceiling (default 1800s = 30min) so a long
    outage does not push retry delay into "hours" territory.
    """
    if attempt_count <= 1:
        return 0.0
    power = max(0, attempt_count - 2)
    raw = base * (2 ** power)
    return float(min(raw, cap))


def iter_projects_in_states(
    repo: SQLModelProjectRepository,
    states: frozenset[str],
    *,
    limit: int = 500,
) -> list[dict[str, Any]]:
    """Return ``{project_id, name, state}`` for projects in ``states``.

    Single pass over the repo. Used by the watchdog to discover
    retryable and stale-syncing projects without per-row metadata
    scans. The states filter is applied client-side because the repo
    does not expose an indexed metadata-key query.

    Each returned dict carries the row's NORMALIZED state
    (:func:`normalize_state` output) so a caller that sweeps multiple
    state classes in one walk can partition the results without a
    second metadata read.

    The ``limit`` bounds the sweep to a sane upper bound — at most a
    few thousand projects; rows beyond the limit are picked up on the
    next sweep tick.
    """
    projects = repo.list_projects(limit=limit)
    matching: list[dict[str, Any]] = []
    for project in projects:
        try:
            with Session(repo.engine) as session:
                records = repo.list_metadata_records(session, project.project_id)
        except Exception:
            continue
        raw_state = _read_metadata_value(records, PLANE_SYNC_STATE_METADATA_KEY)
        normalized = normalize_state(raw_state)
        if normalized in states:
            matching.append(
                {
                    "project_id": project.project_id,
                    "name": project.name,
                    "state": normalized,
                }
            )
    return matching