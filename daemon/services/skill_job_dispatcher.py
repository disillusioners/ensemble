"""Skill Job Dispatcher — enqueues skill-evolution jobs onto the job queue.

Phase 5 of the Skill Evolution System. This module is the single dispatch
front-door for skill-related JobItems (skill_analysis, skill_evolution,
skill_capture, skill_metric_scan). The skill-keeper agent picks these jobs
up and runs the Tier 2/3 evolution flows.

Why this module exists
----------------------

Job types are string literals on :class:`JobItem` — there is no central
registry. Without a dedicated dispatcher, callers would have to repeat
the queue-routing logic for every callsite (the trigger engine, the
metrics service capture check, the user-facing ``skill_fix`` tool,
etc.) and any mistake would silently re-route skill jobs onto
``system_fifo_queue`` (concurrency=1), serializing what should be
concurrent evolution work.

This module encodes the routing rule ONCE, mirrors the precedent set by
``daemon/services/instance_messaging.py:1332-1389`` for message jobs,
and exposes four ergonomic entry points (``enqueue_analysis``,
``enqueue_evolution``, ``enqueue_capture``, ``enqueue_metric_scan``).

CRITICAL routing invariant
--------------------------

All skill jobs MUST route to ``system_parallel_queue`` (concurrency=5),
NOT the default ``system_fifo_queue`` (concurrency=1).

``JobQueueService.enqueue()`` defaults ``queue_id=None`` → resolves to
``system_fifo_queue`` for the project. If we relied on that default,
every skill-evolution job would serialize behind concurrency=1 and
back-pressure the trigger engine. We therefore resolve the
``system_parallel_queue`` ID explicitly via
``queue_repo.get_by_name(project_id, "system_parallel_queue")`` and
pass it as ``queue_id=`` on every enqueue call.

The resolution uses the exact same pattern as
``daemon/services/instance_messaging.py`` (the message-job dispatch
path) so the two dispatchers stay structurally identical. See the
inline comment on :meth:`_resolve_parallel_queue_id` for the precedent.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from daemon.services.project_normalizer import normalize_project_id

logger = logging.getLogger(__name__)


# Job types created by this dispatcher. String literals because
# ``JobItem.job_type`` has no enum/registry (Phase 5 plan, "No central
# job_type registry" note). Keep in sync with the skill-keeper agent's
# ``soul.md`` workflow section — the agent reads ``job_type`` off the
# inbound JobItem to route to the right evolution tier.
JOB_TYPE_ANALYSIS = "skill_analysis"
JOB_TYPE_EVOLUTION = "skill_evolution"
JOB_TYPE_CAPTURE = "skill_capture"
JOB_TYPE_METRIC_SCAN = "skill_metric_scan"

# The privileged evolution agent that owns Tier 2/3 work. NOT a normal
# workflow participant — spawned on-demand via this dispatcher only.
SKILL_KEEPER_AGENT_ID = "skill-keeper"

# Source tag for skill-evolution jobs. ``enqueue()`` accepts any string
# here; the value flows through to ``JobItem.source`` and is visible in
# the Jobs UI. ``"skill_evolution"`` distinguishes these jobs from
# ``"api"``, ``"telegram"``, ``"scheduler"``, ``"webhook"`` — the four
# canonical sources defined in :class:`JobQueueService.enqueue`'s
# signature. Using a dedicated tag lets operators filter the Jobs page
# to "all skill evolution work" without parsing agent_id.
SOURCE_TAG = "skill_evolution"

# The system queue name skill jobs route to. Concurrency=5 (vs. FIFO's
# concurrency=1) lets multiple skill-evolution jobs run in parallel
# without serializing behind a single worker.
PARALLEL_QUEUE_NAME = "system_parallel_queue"


class SkillJobDispatcher:
    """Dispatches skill-related jobs via the job queue.

    The dispatcher is the single front-door for skill JobItems. Every
    call site that needs to enqueue a skill-evolution job MUST go
    through one of the public ``enqueue_*`` methods — never call
    ``job_service.enqueue()`` directly with a ``job_type`` starting
    with ``"skill_"`` because that would skip the parallel-queue
    routing invariant.

    Args:
        job_service: ``JobQueueService`` instance used to create
            :class:`JobItem` rows. Required.
        queue_repo: ``JobQueueRepository`` instance used to resolve the
            project's ``system_parallel_queue`` ID. Required.

    Example wiring::

        dispatcher = SkillJobDispatcher(
            job_service=manager._job_queue_service,
            queue_repo=manager._job_queue_service._queue_repo,
        )
        await dispatcher.enqueue_analysis(
            project_id="...",
            skill_id="skill_abc",
            reason="low completion rate",
            stats={"completion_rate": 0.42},
        )
    """

    def __init__(self, job_service: Any, queue_repo: Any) -> None:
        """Store the job service and queue repository.

        Args:
            job_service: :class:`JobQueueService` — provides ``enqueue()``.
            queue_repo: :class:`JobQueueRepository` — provides
                ``get_by_name(project_id, queue_name)``.
        """
        self._job_service = job_service
        self._queue_repo = queue_repo

    # ── Queue resolution ─────────────────────────────────────────────

    async def _resolve_parallel_queue_id(self, project_id: str | None) -> str | None:
        """Resolve ``system_parallel_queue`` ID for the project.

        Mirrors ``daemon/services/instance_messaging.py:1332-1389``
        exactly so the message-job and skill-job dispatch paths stay
        structurally identical. The lookup uses
        ``queue_repo.get_by_name(project_id, queue_name)`` — NOT
        ``queue_repo.get(queue_id)`` — because the queue ID is what
        we're trying to discover.

        Args:
            project_id: Project to scope the lookup to. ``None`` or
                empty values are normalized to the system default
                project via :func:`normalize_project_id` so the
                dispatcher behaves identically whether the caller
                passes an explicit project or relies on the default.

        Returns:
            The ``system_parallel_queue`` ID for the project, or
            ``None`` if the queue does not exist (which causes
            :meth:`_enqueue_skill_keeper_job` to fall back to
            ``enqueue()``'s default FIFO routing — degraded but
            non-fatal, so a missing parallel queue does not block
            evolution).
        """
        # Normalize first so ``None``/empty/blank values route through
        # the system default project the same way every other service
        # does. Without this, a None project_id would short-circuit the
        # queue lookup and force fallback to FIFO.
        normalized_project_id = normalize_project_id(project_id)

        try:
            queue = await asyncio.to_thread(
                self._queue_repo.get_by_name,
                normalized_project_id,
                PARALLEL_QUEUE_NAME,
            )
        except Exception as lookup_err:
            # Defensive: a misconfigured repository or a DB hiccup
            # must not block skill evolution. Log and return None so
            # ``_enqueue_skill_keeper_job`` falls back to FIFO rather
            # than crashing the trigger engine.
            logger.warning(
                "SkillJobDispatcher: failed to resolve %s for project %s: %s: %s",
                PARALLEL_QUEUE_NAME,
                normalized_project_id[:8] if normalized_project_id else "<none>",
                type(lookup_err).__name__,
                lookup_err,
            )
            return None

        if queue is None:
            logger.debug(
                "SkillJobDispatcher: %s not found for project %s — "
                "falling back to FIFO routing",
                PARALLEL_QUEUE_NAME,
                normalized_project_id[:8] if normalized_project_id else "<none>",
            )
            return None

        # ``queue_id`` is the primary key on the JobQueue model
        # (``daemon/repositories/job_queue/models.py:192``), NOT
        # ``.id``. The ``instance_messaging.py`` precedent reads the
        # same attribute (line 1384: ``queue_id_for_job = queue.queue_id``).
        return queue.queue_id

    # ── Core enqueue ────────────────────────────────────────────────

    async def _enqueue_skill_keeper_job(
        self,
        project_id: str | None,
        job_type: str,
        message: str,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        """Create a ``JobItem`` for the skill-keeper agent.

        This is the single chokepoint that enforces the
        ``system_parallel_queue`` routing rule. Every public
        ``enqueue_*`` method funnels through here, so the rule cannot
        be accidentally bypassed by adding a new public method without
        going through the queue resolution.

        Args:
            project_id: Project scope (None/empty normalized to system
                default). ``JobQueueService.enqueue`` rejects None
                after normalization, so this MUST be a valid project
                string at the point of the enqueue call.
            job_type: One of ``skill_analysis``, ``skill_evolution``,
                ``skill_capture``, ``skill_metric_scan``. Passed
                through to ``JobItem.job_type`` verbatim.
            message: Human-readable description of the work — becomes
                ``JobItem.message`` and surfaces in the Jobs UI.
            metadata: Optional dict stored on ``JobItem.job_metadata``.
                Must be JSON-serializable; the model uses ``JSONBType``
                so dicts, lists, strings, numbers, bools, and None
                values are all safe. Defaults to ``{}`` so the
                metadata column is never NULL (the skill-keeper's
                branching logic can rely on ``metadata.get(...)``
                without null-guarding every access).

        Returns:
            ``job.job_id`` of the newly-created :class:`JobItem`. The
            caller can use this ID to query status, register watchers,
            or cancel the job.

        Raises:
            ValueError: If the agent registry rejects ``skill-keeper``
                (the agent meta has not been loaded yet), if the
                project has no system queues at all (no FIFO AND no
                parallel), or if ``enqueue()``'s D13 ``job_type``
                guard rejects the literal (it doesn't for any of
                our four types, but the guard is there for safety).
        """
        # Resolve the parallel queue FIRST so a missing/broken
        # repository fails fast without burning a DB INSERT. The
        # resolver normalizes project_id internally.
        queue_id = await self._resolve_parallel_queue_id(project_id)

        # Normalize project_id for the enqueue call. ``enqueue()``
        # raises ValueError on None project_id, so we must always pass
        # a normalized value. We re-normalize here (rather than rely on
        # the resolver's internal normalization) so the caller can
        # pass ``None`` and still get the system default.
        normalized_project_id = normalize_project_id(project_id)

        job = await self._job_service.enqueue(
            agent_id=SKILL_KEEPER_AGENT_ID,
            message=message,
            source=SOURCE_TAG,
            project_id=normalized_project_id,
            queue_id=queue_id,
            job_type=job_type,
            metadata=metadata if metadata is not None else {},
        )
        return job.job_id

    # ── Public API: Tier 2 analysis ─────────────────────────────────

    async def enqueue_analysis(
        self,
        project_id: str | None,
        skill_id: str,
        reason: str = "",
        stats: dict[str, Any] | None = None,
    ) -> str:
        """Enqueue a ``skill_analysis`` job (Tier 2 — cheap LLM analysis).

        Called by the trigger engine when a skill crosses one of the
        analysis thresholds (e.g. low completion rate, consecutive
        failures, low feedback). The skill-keeper agent picks the job
        up, reads the skill content + recent usage records, and asks
        the analysis model whether evolution is warranted.

        Args:
            project_id: Project scope (None → system default).
            skill_id: The skill to analyze.
            reason: Short human-readable trigger reason (e.g.
                ``"low_completion_rate"``, ``"consecutive_failures"``).
                Stored in metadata so the agent can surface it in its
                logs without re-parsing the free-text message.
            stats: Trigger stats dict (e.g.
                ``{"completion_rate": 0.42, "fallback_rate": 0.15}``).
                Passed in metadata for the agent to read directly
                rather than re-querying the metrics service.

        Returns:
            ``job_id`` of the analysis JobItem.
        """
        stats_text = stats if stats is not None else {}
        message = f"Analyze skill {skill_id}. Reason: {reason}. Stats: {stats_text}"
        return await self._enqueue_skill_keeper_job(
            project_id,
            JOB_TYPE_ANALYSIS,
            message,
            metadata={
                "skill_id": skill_id,
                "reason": reason,
                "stats": stats_text,
            },
        )

    # ── Public API: Tier 3 evolution ────────────────────────────────

    async def enqueue_evolution(
        self,
        project_id: str | None,
        skill_id: str,
        evolution_type: str,
        direction: str,
    ) -> str:
        """Enqueue a ``skill_evolution`` job (Tier 3 — main LLM evolution).

        Called either directly by the trigger engine (action =
        ``evolve_fix`` skips Tier 2) or by the skill-keeper after
        ``enqueue_analysis`` returns ``should_evolve=True``. Performs
        FIX (in-place repair), DERIVED (specialized variant), or
        CAPTURED (new skill from observed task) — see Phase 5 plan.

        Args:
            project_id: Project scope (None → system default).
            skill_id: The skill to evolve.
            evolution_type: One of ``"FIX"``, ``"DERIVED"``,
                ``"CAPTURED"``. Stored verbatim in metadata; the
                agent's branching logic checks the value as-is.
            direction: Free-text description of the change
                direction (e.g. ``"Add error handling section"``).
                Stored in metadata for the evolution prompt.

        Returns:
            ``job_id`` of the evolution JobItem.
        """
        message = f"Evolve skill {skill_id}. Type: {evolution_type}. Direction: {direction}"
        return await self._enqueue_skill_keeper_job(
            project_id,
            JOB_TYPE_EVOLUTION,
            message,
            metadata={
                "skill_id": skill_id,
                "evolution_type": evolution_type,
                "direction": direction,
            },
        )

    # ── Public API: CAPTURED flow ───────────────────────────────────

    async def enqueue_capture(
        self,
        project_id: str | None,
        task_details: dict[str, Any],
    ) -> str:
        """Enqueue a ``skill_capture`` job (CAPTURED flow).

        Called by :meth:`SkillEvolutionService.check_and_capture` when
        a task completes successfully with high complexity but no
        skill was actually applied (checked via ``feedback_applied``
        records, NOT injection records — injection ≠ application).
        The skill-keeper extracts a reusable skill pattern from the
        task execution and creates a new skill with
        ``lineage_origin='captured'``.

        Args:
            project_id: Project scope (None → system default).
            task_details: Dict containing the captured-task context
                (``instance_id``, ``agent_id``, ``project_id``,
                ``task_message``, ``iterations``, ``duration_seconds``).
                Stored verbatim in metadata; the agent reads the same
                keys when prompting the LLM.

        Returns:
            ``job_id`` of the capture JobItem.
        """
        message = f"Capture skill from task. Details: {task_details}"
        return await self._enqueue_skill_keeper_job(
            project_id,
            JOB_TYPE_CAPTURE,
            message,
            metadata={"task_details": task_details},
        )

    # ── Public API: metric scan ─────────────────────────────────────

    async def enqueue_metric_scan(
        self,
        project_id: str | None = None,
    ) -> str:
        """Enqueue a ``skill_metric_scan`` job (periodic trigger scan).

        Called by the trigger engine's periodic scheduler to walk
        every skill in the project, evaluate the Tier 1 thresholds,
        and enqueue per-skill ``skill_analysis`` jobs for any that
        cross a threshold. Scoped to a single project; pass ``None``
        to scan the system default project.

        Args:
            project_id: Project to scan (None → system default).
                Defaults to ``None`` so a bare
                ``dispatcher.enqueue_metric_scan()`` call works
                without the caller thinking about project scope.

        Returns:
            ``job_id`` of the metric-scan JobItem.
        """
        target = project_id if project_id is not None else "all"
        message = f"Run skill metric scan for project {target}"
        return await self._enqueue_skill_keeper_job(
            project_id,
            JOB_TYPE_METRIC_SCAN,
            message,
            metadata={"scan_target": target},
        )

    # ── Public API: user-reported skill fix (skill_fix tool backend) ─

    async def dispatch_fix(
        self,
        project_id: str | None,
        skill_id: str,
        issue_description: str,
        suggested_fix: str = "",
        current_instance_id: str = "",
    ) -> str:
        """Dispatch a user-reported skill fix request.

        Maps to :meth:`enqueue_evolution` with ``evolution_type='FIX'``
        and a ``direction`` string composed from the issue description
        plus the optional suggested fix. The skill-keeper agent picks
        up the job and performs the actual repair.

        Args:
            project_id: Project scope (None → system default).
            skill_id: The skill to fix.
            issue_description: Plain-language description of the issue.
            suggested_fix: Optional proposed change. Appended to the
                direction string so the LLM has both signals.
            current_instance_id: Optional instance ID for audit /
                lineage purposes. Currently unused at the dispatcher
                layer (kept on signature for parity with the
                ``skill_fix`` tool's call site).

        Returns:
            ``job_id`` of the dispatched evolution JobItem.
        """
        del current_instance_id  # Reserved for Phase 5 audit hooks.
        direction = issue_description
        if suggested_fix:
            direction = f"{issue_description}\n\nSuggested fix: {suggested_fix}"
        return await self.enqueue_evolution(project_id, skill_id, "FIX", direction)