"""MissionResolver — pure read-model projection (M1 of mission-class).

Mission-class Milestone M1 (per
``.agents/shared/planning/mission-class/architecture-recommendation.md``
§5 M1 row). Mission is a **first-class noun as a pure read-model
projection** — it derives from ``Instance.status`` (+ ``JobItem.terminal_reason``
for the W4 DEAD hazard) and NEVER writes. ``MissionResolver`` is a leaf
service: only READ repositories are wired in (``InstanceRepository`` and
``JobRepository``); no minting, no DML, no JobItem creation, no
admission-state writes — the census stays at 23 frozen admission-state
writers (see ``daemon/job_state/constitution.py``).

Identity & epochs (spec §3)
---------------------------

* ``mission_id == instance_id`` (one mission per instance — the leader's
  lean, adjudicated under pressure-test).
* ``parent_mission_id == instances.parent_id`` (permanent across
  terminate→revive; survives the JAFP/I4 boundary intact).
* an **epoch** = contiguous non-terminal interval. Opens on →RUNNING
  (spawn or revive); closes on terminal. ``completed`` (from
  ``InstanceStatus.COMPLETED``) is **revivable**; ``cancelled`` (from
  ``TERMINATED``) is **true-terminal**. Current epoch + current liveness
  are precise; historical epoch timestamps are best-effort (the DB has
  no terminal-transition timestamps — see :ref:`known-limitation`).

W4-hazard preservation
-----------------------

When a linked ``JobItem`` is ``DEAD`` (``admission_state='dead'``), the
mission's ``terminal_reason`` must surface the dead-letter truth
regardless of a since-revived instance (the docs/job-task-system.md §8
W4 hazard and agent-contract-draft.md §2 W4 rule). The resolver
implements this with a single ``admission_state='dead'`` lookup against
the JobItem table for the linked instance id.

Degradation contract (§8.2 — mission_liveness precedent)
--------------------------------------------------------

A transient DB error during the read MUST degrade every mission field
to ``None`` rather than blowing up the whole page (the
``message_metadata`` and ``mission_liveness=None`` lookups both follow
this shape — narrow ``SQLAlchemyError`` catch + ``logger.warning`` +
return None). The caller treats the absence as "split semantics
unavailable" and falls back to the JobItem-side view.

Kill-switch (M1 soak discipline, per spec §5 M1 / decision 4)
-------------------------------------------------------------

``ENSEMBLE_MISSION_PROJECTION_ENABLED`` (default OFF). OFF = responses
byte-identical to pre-M1 (no mission fields surface); ON = the three
additive fields (``mission_id`` / ``mission_epoch`` /
``mission_terminal_reason``) are populated on the four Fix-C read
surfaces (:class:`WorkRecord`, :class:`JobResponse`, :class:`_ResolvedWork`
SSE payload, and the ``_job_to_response`` delegation).

Why default OFF: this is the operator soak discipline (matching the
``ENSEMBLE_WC_WAKE_ENQUEUE`` precedent — ≤2-week soak on default OFF
then operator flip). M1 itself is read-model; the flip is the M2 tool
migration on-ramp. The flag is resolved once per process and cached
(``os.environ.get``; restart-required to pick up a flip), identical to
the ``_resolve_wc_wake_enqueue_enabled`` shape.

.. _known-limitation:

Known limitation (must ship in docs — spec §3 ⚠)
------------------------------------------------

The DB does not store terminal-transition timestamps — full-fidelity
historical epoch data (per-epoch ``started_at`` / ``ended_at``) is NOT
derivable at read time for pre-existing or multi-epoch instances.
**Current epoch is always ``1``** for every non-degraded projection
(non-terminal AND terminal) until M4(ii)'s append-only ``mission_events``
log preserves per-epoch timestamps — that is the honest answer today.
Epoch is ``None`` ONLY when the resolver degraded (no Instance row
available). The honest cure is the ``mission_events`` log; that is
M4(ii), gated on D's trigger or the N2 revive-boundary ticket — not in
M1's scope.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Iterable

from sqlalchemy.exc import SQLAlchemyError

from daemon.services.work_status import _STATUS_CANONICAL_MAP

if TYPE_CHECKING:
    from daemon.repositories.instance.repository import SQLModelInstanceRepository
    from daemon.repositories.job_queue.repository import JobRepository

logger = logging.getLogger(__name__)


# ── Kill-switch resolver (mirrors _resolve_wc_wake_enqueue_enabled) ────────
# M1 ships default OFF (soak discipline). Restart-required to pick up a
# flip — same as the WC-wake and governor-guard kill-switches. Valid
# truthy values: ("1", "true", "yes", "on"). Valid falsy: ("0",
# "false", "no", "off"). Blank / unset / unknown values all resolve OFF
# (the OFF default); blanking mid-incident is the instant-revert path.

_MISSION_PROJECTION_ENV = "ENSEMBLE_MISSION_PROJECTION_ENABLED"
_MISSION_PROJECTION_ENABLED: bool | None = None


def _resolve_mission_projection_enabled() -> bool:
    """Resolve and cache the M1 mission-projection kill-switch.

    Returns:
        ``True`` when mission projection is enabled (``ENSEMBLE_MISSION_PROJECTION_ENABLED=1``
        / ``true`` / ``yes`` / ``on``) — the additive fields surface on
        the four Fix-C read surfaces. ``False`` when disabled via the
        env's falsy values or unset (the OFF default) — responses stay
        byte-identical to the pre-M1 wire format.

    Cached for the daemon's lifetime: flipping the env mid-flight has
    no effect on the resolved state, only a restart picks up a new
    value. Mirrors the caching pattern in
    ``daemon/services/instance_messaging.py:_resolve_wc_wake_enqueue_enabled``.
    """
    global _MISSION_PROJECTION_ENABLED
    if _MISSION_PROJECTION_ENABLED is not None:
        return _MISSION_PROJECTION_ENABLED
    raw = os.environ.get(_MISSION_PROJECTION_ENV, "0").strip().lower()
    if raw in ("0", "false", "no", "off"):
        _MISSION_PROJECTION_ENABLED = False
    elif raw in ("1", "true", "yes", "on"):
        _MISSION_PROJECTION_ENABLED = True
    else:
        logger.warning(
            "%s=%r is not a recognized truthy/falsy value; falling back "
            "to OFF (default — no mission projection). Valid falsy: "
            "0/false/no/off. Valid truthy: 1/true/yes/on.",
            _MISSION_PROJECTION_ENV,
            raw,
        )
        _MISSION_PROJECTION_ENABLED = False
    return _MISSION_PROJECTION_ENABLED


def is_mission_projection_enabled() -> bool:
    """Public accessor — consult the cached kill-switch state."""
    return _resolve_mission_projection_enabled()


def _reset_mission_projection_for_tests() -> None:
    """Clear the cached kill-switch state so tests can re-resolve.

    Test-only — production code never invokes this. Mirrors
    ``_reset_wc_wake_enqueue_for_tests``.
    """
    global _MISSION_PROJECTION_ENABLED
    _MISSION_PROJECTION_ENABLED = None


# ── Liveness mapping ──────────────────────────────────────────────────────
# Instance.status canonicalizes onto the mission vocabulary via
# :data:`_STATUS_CANONICAL_MAP` (the same map Fix C's
# ``mission_liveness`` consult uses). The mapping is the single source
# of truth — no parallel vocabulary set is maintained here. Completes
# to ``"completed"`` (REVIVABLE); all other terminals are true-terminal.
#
# * COMPLETED  → "completed"   — TERMINAL, REVIVABLE
# * FAILED     → "failed"      — TERMINAL, true-terminal
# * ERROR      → "failed"      — TERMINAL, true-terminal
# * TERMINATED → "cancelled"   — TERMINAL, true-terminal
# * RUNNING    → "processing"
# * WAITING    → "processing"
# * WAITING_CHILDREN → "processing"
# * QUEUED     → "processing"
# * IDLE       → "processing"
# * PAUSED     → "paused"
#
# ``revivable`` is a derived flag specific to mission projection: only
# ``completed`` is revivable (per spec §3 — ``InstanceStatus.COMPLETED``
# → mission liveness ``completed`` which can be →RUNNING again on a
# ``send_message`` to a COMPLETED instance).


# ── MissionRecord ─────────────────────────────────────────────────────────
# The mission projection's view-model. Mirrors the §2 payload shape
# from agent-contract-draft.md (the M2 tool-surface spec) — the M1
# projection serves the same shape today so M2 can consume it
# directly. Three of the eight fields surface in M1's additive
# response fields (``mission_id`` / ``mission_epoch`` /
# ``mission_terminal_reason``); the remaining identity /
# liveness fields support M2 tool payload composition.

@dataclass
class MissionRecord:
    """Pure projection of an Instance onto the mission read-model.

    Fields mirror the §2 ``get_mission`` payload in
    ``agent-contract-draft.md`` (M2 tool surface). M1 only surfaces
    three of them on the additive response fields; the rest are
    consumed by M2's tools (out of M1's scope — no tools built here).

    Attributes:
        mission_id: Identity == ``instance_id`` (spec §3 adjudicated
            under pressure-test). Permanent across terminate→revive
            because ``instances.parent_id`` is permanent; the
            per-revive new-id alternative would churn identity and
            break the FE ``missions:N`` de-dup. ``None`` when the
            resolver degraded (no Instance lookup available).
        agent_id: The agent this mission is working on behalf of
            (``Instance.agent_id``).
        parent_mission_id: The parent instance's id (``Instance.parent_id``);
            ``None`` for root missions. Permanent record, same
            rationale as ``mission_id``.
        liveness: Canonical mission liveness (``pending`` /
            ``processing`` / ``paused`` / ``completed`` / ``failed`` /
            ``cancelled``). ``None`` when degraded.
        terminal_reason: A non-``None`` discriminator when the mission
            is terminal — one of ``completed`` / ``failed`` /
            ``cancelled`` / ``dead_letter`` (the W4-hazard path). For
            a living mission the value is ``None``. Populated from
            three sources in priority order: (1) linked JobItem
            ``admission_state='dead'`` (W4 hazard — surfaces
            ``dead_letter`` regardless of instance state),
            (2) mission liveness (when terminal), (3) ``None`` for
            non-terminal missions.
        epoch: Current epoch number (1 for a fresh mission). An
            ``epoch`` is a contiguous non-terminal interval — opens on
            →RUNNING, closes on terminal. ``None`` when degraded.
        linked_jobs: List of ``JobItem.job_id`` strings whose
            ``instance_id`` resolves to this mission. Best-effort:
            populated only when the JobItem lookup succeeds (graceful
            degrade to ``[]`` on transient DB error).
        started_at: ISO-8601 ``last_activity_at`` of the linked
            instance (closest analogue to "work began"). ``None``
            when the instance has no recorded activity yet or when
            degraded.
        last_activity_at: ISO-8601 pass-through of the instance's
            ``last_activity_at`` column. ``None`` when null on the
            instance, or when degraded.
    """

    mission_id: str | None
    agent_id: str | None
    parent_mission_id: str | None
    liveness: str | None
    terminal_reason: str | None
    epoch: int | None
    linked_jobs: list[str] = field(default_factory=list)
    started_at: str | None = None
    last_activity_at: str | None = None


# ── MissionResolver ───────────────────────────────────────────────────────
# Leaf service — reads from ``InstanceRepository`` and ``JobRepository``
# ONLY. No mutations, no JobItem creation, no admission-state writes.
# Census invariant: ``KNOWN_ADMISSION_STATE_WRITERS`` stays at 23; the
# resolver touches none of them.


class MissionResolver:
    """Pure read-model projection — no writes, no JobItem creation.

    Mirrors the leaf-service pattern in
    ``daemon/services/work_resolver.py:WorkResolverService``: the caller
    wires the repositories at construction time, the service holds no
    state, every public method hits the DB through the underlying
    repositories.

    Three concerns this service owns:

    * **Identity** — ``mission_id == instance_id``; ``parent_mission_id
      == instance.parent_id``. Permanent across revive (the
      ``instances.parent_id`` is permanent by design — see Case-2
      revive in spec §3).
    * **Liveness** — :class:`Instance.status` → canonical mission
      vocabulary via :data:`_STATUS_CANONICAL_MAP`. ``completed`` is
      revivable; ``cancelled``/``failed`` are true-terminal.
    * **W4 hazard** — a linked ``JobItem`` with
      ``admission_state='dead'`` flips ``terminal_reason`` to
      ``"dead_letter"`` regardless of a since-revived instance.

    Epochs (spec §3 known-limitation): the DB has no
    terminal-transition timestamps, so historical epoch
    ``started_at`` / ``ended_at`` are NOT derivable at read time.
    Current epoch is **always ``1``** for every non-degraded projection
    (terminal AND non-terminal) — the honest constant answer until
    M4(ii)'s append-only ``mission_events`` log preserves per-epoch
    truthmakers. Epoch is ``None`` only when the resolver degraded.
    Full per-epoch fidelity belongs to M4(ii) — out of M1's scope.
    """

    def __init__(
        self,
        instance_repo: "SQLModelInstanceRepository",
        job_repo: "JobRepository",
    ) -> None:
        """Initialize the resolver with the two READ repositories it needs.

        Args:
            instance_repo: ``SQLModelInstanceRepository`` for
                ``Instance`` lookups (identity, liveness source, parent
                edge). READ-ONLY access — no writes.
            job_repo: ``JobRepository`` for the ``linked_jobs`` list
                and the W4-hazard ``dead`` lookup. READ-ONLY access —
                no writes, no JobItem creation.
        """
        self._instance_repo = instance_repo
        self._job_repo = job_repo

    # ── Public API ────────────────────────────────────────────────────────

    def resolve(self, instance_id: str | None) -> MissionRecord | None:
        """Project a single ``instance_id`` onto the mission read-model.

        Lookup order: ``Instance`` row first (identity + liveness
        authority), then ``JobItem`` rows linked to this instance
        (W4-hazard + ``linked_jobs``).

        Graceful degradation (per §8.2 — mission_liveness precedent):

        * ``instance_id is None`` → ``MissionRecord(None, ...)`` with
          every field ``None`` (matches the "unknown" fallback shape).
        * Instance row missing → ``None`` (caller treats as unknown).
        * ``InstanceRepo.get`` raises ``SQLAlchemyError`` →
          ``MissionRecord(None, ..., liveness=None, ...)`` with the
          fields ``None`` and ``linked_jobs=[]``. Caller falls back to
          the JobItem-side view.
        * ``JobRepo.list_by_instance`` raises ``SQLAlchemyError`` →
          the instance-derived fields stay populated; only
          ``linked_jobs=[]`` and the W4-hazard sub-check is skipped.
          A warning is logged ONCE per call.

        Args:
            instance_id: The instance id to project. ``None`` yields
                the graceful "unknown" record shape.

        Returns:
            A populated :class:`MissionRecord`, or ``None`` when the
            instance lookup failed AND there is no Instance row to
            project (the only true miss shape; degradation-yields-None
            fields uses MissionRecord(None, ...) instead, NOT this
            function returning ``None``).
        """
        if instance_id is None:
            return _unknown_mission_record()

        # Instance lookup — graceful degrade on transient DB error.
        try:
            instance = self._instance_repo.get(instance_id)
        except SQLAlchemyError as exc:
            # §8.2 narrow catch — programmer mistakes (TypeError,
            # AttributeError) must propagate, not be masked as DB
            # outages. Same shape as Fix C's ``_lookup_instance``.
            logger.warning(
                "mission_resolver: instance lookup failed for %r: %s — "
                "returning degraded mission record",
                instance_id,
                exc,
            )
            return _unknown_mission_record()

        if instance is None:
            # No Instance row — caller treats as unknown. The
            # resolve_many batched path uses this as the "drop this id"
            # signal so the map only contains ids that resolved.
            return None

        # Single-row JobItem lookup reuses the batched helper with
        # N=1 — keeps the wire pattern uniform with resolve_many so a
        # future caller that switches between single-row and batched
        # doesn't pay a per-row penalty.
        jobitems_by_id = self._batch_jobitem_lookup([instance_id])
        dead_linked, linked_jobs = jobitems_by_id.get(
            instance_id, (False, [])
        )

        return self._project(instance, dead_linked, linked_jobs)

    def resolve_many(
        self, instance_ids: Iterable[str | None]
    ) -> dict[str, MissionRecord]:
        """Batched mission projection across many instance ids.

        Two round-trips per page: one batched ``Instance`` SELECT +
        one batched ``JobItem`` SELECT (fetching only the three
        consumed columns — ``job_id``, ``instance_id``,
        ``admission_state`` — via ``instance_id IN (...)``). The
        per-row ``JobItem`` N+1 that the pre-C9 implementation paid
        is gone. ``None`` and empty inputs short-circuit to an empty
        dict without hitting the DB.

        **Degradation contract (matches Fix C §8.2):** a transient
        ``SQLAlchemyError`` during either batched fetch degrades to a
        single warning + empty result (not per-row), so every
        requested instance maps to the unknown-shape record and the
        caller falls back to the JobItem-side view.

        Args:
            instance_ids: An iterable of instance ids to project.
                ``None`` entries are filtered out (the Instance PK
                is never null).

        Returns:
            A dict mapping ``instance_id`` → :class:`MissionRecord`.
            Missing ids are absent from the dict (caller treats them
            as unknown). On degradation, the dict is empty (every
            requested id effectively degrades).
        """
        valid_ids = [iid for iid in instance_ids if iid is not None]
        if not valid_ids:
            return {}

        try:
            instances_by_id = self._batch_instances(valid_ids)
        except SQLAlchemyError as exc:
            logger.warning(
                "mission_resolver: batched instance lookup failed for "
                "%d ids: %s — degrading page to receipt-only view",
                len(valid_ids),
                exc,
            )
            return {}

        try:
            jobitems_by_id = self._batch_jobitem_lookup(
                list(instances_by_id)
            )
        except SQLAlchemyError as exc:
            logger.warning(
                "mission_resolver: batched JobItem lookup failed for "
                "%d ids: %s — degrading linked_jobs/W4 to None/[]",
                len(instances_by_id),
                exc,
            )
            jobitems_by_id = {}

        out: dict[str, MissionRecord] = {}
        for instance_id, instance in instances_by_id.items():
            dead_linked, linked_jobs = jobitems_by_id.get(
                instance_id, (False, [])
            )
            record = self._project(instance, dead_linked, linked_jobs)
            if record is not None:
                out[instance_id] = record
        return out

    def project(self, instance: "Instance") -> MissionRecord:
        """Public passthrough for the per-instance projection.

        Wraps :meth:`_project` for callers (notably
        ``WorkResolverService._mission_fields_for_instance``) that
        already hold an :class:`Instance` row and want a
        :class:`MissionRecord` without re-fetching. Use this instead of
        reaching into ``resolver._project(instance)`` directly.

        Args:
            instance: A loaded :class:`Instance` row.

        Returns:
            A populated :class:`MissionRecord`. Same shape as
            ``resolve(iid)`` minus the lookup step.
        """
        return self._project(instance)

    # ── Internal helpers ──────────────────────────────────────────────────

    def _batch_instances(
        self, instance_ids: list[str]
    ) -> dict[str, "Instance"]:
        """Batched Instance SELECT for the resolver's batch path.

        Mirrors ``WorkResolverService._batch_instances``: one round-trip
        per batch. ``SQLAlchemyError`` is deliberately NOT caught here —
        a transient DB error on the Instance batch is a hard failure
        that must surface to the caller (the single-row fallback in
        :meth:`resolve` and the per-instance fallback in
        :meth:`resolve_many` cover the partial-failure case at the next
        layer up). Programmer mistakes (TypeError, etc.) must propagate
        from here as well — no broad ``except Exception`` swallowing.
        """
        from sqlmodel import Session as SQLModelSession

        from daemon.repositories.instance.models import Instance

        with SQLModelSession(self._instance_repo.engine) as session:
            stmt = select(Instance).where(Instance.instance_id.in_(instance_ids))
            return {row.instance_id: row for row in session.exec(stmt)}

    def _project(
        self,
        instance: "Instance",
        dead_linked: bool = False,
        linked_jobs: list[str] | None = None,
    ) -> MissionRecord:
        """Project one already-loaded ``Instance`` row onto the mission.

        Pure read-model: no writes, no JobItem creation, no
        admission-state mutation. The pre-fetched ``dead_linked`` /
        ``linked_jobs`` arguments come from
        :meth:`_batch_jobitem_lookup` (a single combined SELECT) so
        ``_project`` itself does not touch the DB.

        Args:
            instance: A loaded ``Instance`` row from the caller
                (single-instance or batched path).
            dead_linked: Whether a linked JobItem in DEAD admission
                state exists for this instance (the W4-hazard flag).
            linked_jobs: The list of ``JobItem.job_id`` values linked
                to this instance (non-deleted only). ``None`` is
                tolerated as ``[]`` for callers that have not (or
                could not) populate the field.

        Returns:
            A populated :class:`MissionRecord`. Never returns
            ``None`` — the caller guarantees ``instance`` is non-null.
        """
        liveness = _STATUS_CANONICAL_MAP.get(instance.status, instance.status)

        # W4 hazard: a linked JobItem in DEAD state must surface
        # ``dead_letter`` regardless of instance liveness (revived
        # instance + dead job = dead mission). The flag is pre-fetched
        # via the batched helper so this branch is a pure read-model
        # check.
        if dead_linked:
            terminal_reason: str | None = "dead_letter"
        elif liveness in {"completed", "failed", "cancelled"}:
            # For a terminal Instance the mission's terminal_reason
            # is the liveness itself. ``dead_letter`` is a
            # terminal_reason value only — it never appears on the
            # liveness side (Instance.status enum does not include
            # ``dead`` / ``dead_letter``).
            terminal_reason = liveness
        else:
            terminal_reason = None

        epoch = self._compute_epoch(instance, liveness)

        return MissionRecord(
            mission_id=instance.instance_id,
            agent_id=instance.agent_id,
            parent_mission_id=instance.parent_id,
            liveness=liveness,
            terminal_reason=terminal_reason,
            epoch=epoch,
            linked_jobs=list(linked_jobs) if linked_jobs is not None else [],
            started_at=(
                instance.last_activity_at.isoformat()
                if instance.last_activity_at is not None
                else instance.created_at
            ),
            last_activity_at=(
                instance.last_activity_at.isoformat()
                if instance.last_activity_at is not None
                else None
            ),
        )

    def _batch_jobitem_lookup(
        self, instance_ids: list[str]
    ) -> dict[str, tuple[bool, list[str]]]:
        """Single combined JobItem SELECT for the W4 + linked_jobs paths.

        C9 batching standard: one SELECT replaces the two per-row
        queries the pre-C9 ``_project`` path made (``select(JobItem)
        ...`` for the dead-check + ``select(JobItem.job_id) ...``
        for the linked_jobs list). The combined SELECT fetches only
        the three consumed columns — ``job_id``, ``instance_id``,
        ``admission_state`` — and groups in Python by ``instance_id``.

        **Performance bound (pinned by
        ``tests/unit/services/test_mission_resolver.py::
        TestBatchQueryCount``):** ``resolve_many`` must issue exactly
        ONE ``SELECT`` against ``job_queue_items`` per call,
        regardless of the page size. The bound holds for both the
        batched path (``resolve_many(N>1)``) and the single-row path
        (``resolve(1)``) — both go through this helper.

        Transient DB errors degrade to ``{}`` (the caller treats
        every requested instance as "no dead link, empty
        linked_jobs" — the liveness terminal-reason stands as the
        answer for the W4 path). The narrow ``SQLAlchemyError`` catch
        mirrors the single-row contract in :meth:`resolve`.

        Args:
            instance_ids: The ``Instance.instance_id`` values to
                batch-fetch JobItem rows for. The caller filters out
                ``None`` and missing-from-Instance entries.

        Returns:
            A mapping ``{instance_id: (has_dead_link, [job_ids])}``.
            Missing instance ids are absent from the mapping (the
            caller treats absence as "no jobs linked"). Soft-deleted
            JobItem rows (``deleted_at IS NOT NULL``) are excluded.
        """
        if not instance_ids:
            return {}
        try:
            with _job_session(self._job_repo) as session:
                stmt = (
                    select(
                        JobItem.job_id,
                        JobItem.instance_id,
                        JobItem.admission_state,
                    )
                    .where(JobItem.instance_id.in_(instance_ids))
                    .where(JobItem.deleted_at.is_(None))
                    .order_by(
                        JobItem.instance_id,
                        JobItem.created_at.desc(),
                        JobItem.job_id,
                    )
                )
                rows = list(session.exec(stmt))
        except SQLAlchemyError as exc:
            logger.warning(
                "mission_resolver: batched JobItem lookup failed for "
                "%d ids: %s — degrading W4 + linked_jobs to no-data",
                len(instance_ids),
                exc,
            )
            return {}

        out: dict[str, tuple[bool, list[str]]] = {}
        for job_id, instance_id, admission_state in rows:
            has_dead, jobs = out.get(instance_id, (False, []))
            jobs.append(job_id)
            if admission_state == AdmissionState.DEAD.value:
                has_dead = True
            out[instance_id] = (has_dead, jobs)
        return out

    @staticmethod
    def _compute_epoch(
        instance: "Instance", liveness: str
    ) -> int:
        """Compute the current epoch for ``instance``.

        Best-effort — see module docstring "Known limitation". Today
        we report ``epoch=1`` for every non-degraded instance because
        per-epoch timestamps are not stored. The M4(ii) event log
        will replace this with a precise ``epoch_count`` +
        ``last_epoch_at`` pair; for M1 the honest answer is "1" for
        everyone (current epoch semantics, not historical count).

        Args:
            instance: A loaded ``Instance`` row.
            liveness: The canonical mission liveness for ``instance``.

        Returns:
            The current epoch number; always ``1`` for non-degraded
            projections (revives are recorded per-instance but per-
            epoch timestamps are not stored yet).
        """
        # Non-terminal liveness → mission is currently in epoch 1.
        # Terminal liveness → mission is in (or just exited) epoch 1
        # (best-effort — single epoch per read-time projection today).
        # The mission_events log will eventually refine this.
        return 1


def _unknown_mission_record() -> MissionRecord:
    """Return the degraded-shape :class:`MissionRecord` (all-None)."""
    return MissionRecord(
        mission_id=None,
        agent_id=None,
        parent_mission_id=None,
        liveness=None,
        terminal_reason=None,
        epoch=None,
        linked_jobs=[],
        started_at=None,
        last_activity_at=None,
    )


# ── Local imports kept at module bottom ────────────────────────────────────
# Avoid the top-of-module heavyweight import for the two repositories
# the resolver uses — mirrors the leaf-service pattern in
# ``work_resolver.py`` (which also defers ``SELECT Instance`` imports
# inside the batched helper) and keeps the module importable from
# tests / docs without dragging the SQLAlchemy chain.

from sqlmodel import Session as _SQLModelSession, select  # noqa: E402

from daemon.repositories.job_queue.models import AdmissionState, JobItem  # noqa: E402


def _job_session(job_repo: "JobRepository"):
    """Open a SQLModel session on ``job_repo.engine`` (sync context).

    Small wrapper used by the W4-hazard and linked_jobs helpers so the
    two callsite ``with`` blocks stay readable. The engine is whatever
    the JobRepository was constructed with — sharing the work
    resolver's engine avoids SQLite WAL lock contention (the same
    rationale ``WorkResolverService.__init__`` documents).
    """
    return _SQLModelSession(job_repo.engine)


def mission_projection_to_dict(
    *,
    mission_id: str | None,
    mission_epoch: int | None,
    mission_terminal_reason: str | None,
) -> dict[str, Any]:
    """Return the additive ``mission_*`` projection payload, kill-switch gated.

    Used by every Fix-C read surface
    (:class:`WorkRecord.to_dict`,
    :meth:`daemon.routers.jobs_streaming._ResolvedWork.to_payload`,
    :meth:`daemon.routers.jobs_streaming._ResolvedWork.to_completed_payload`,
    and :meth:`daemon.routers.schemas.JobResponse._serialize`) so the
    additive-on-ON / absent-on-OFF contract stays in lock-step.

    Args:
        mission_id: The mission identity (== ``instance_id`` per spec §3
            adjudicated under pressure-test).
        mission_epoch: The current epoch number (always ``1`` for
            non-degraded projections per spec §3 known-limitation).
        mission_terminal_reason: The mission-side terminal discriminator
            (``completed`` / ``failed`` / ``cancelled`` / ``dead_letter``
            — ``dead_letter`` is W4-hazard only).

    Returns:
        A dict with the three keys when the kill-switch is ON
        (``ENSEMBLE_MISSION_PROJECTION_ENABLED`` truthy); an empty dict
        when OFF (so callers can splat ``**mission_projection_to_dict(...)``
        into their output payload without an explicit conditional).
    """
    if not is_mission_projection_enabled():
        return {}
    return {
        "mission_id": mission_id,
        "mission_epoch": mission_epoch,
        "mission_terminal_reason": mission_terminal_reason,
    }


__all__ = [
    "MissionResolver",
    "MissionRecord",
    "is_mission_projection_enabled",
    "mission_projection_to_dict",
    "_reset_mission_projection_for_tests",
    "_resolve_mission_projection_enabled",
]
