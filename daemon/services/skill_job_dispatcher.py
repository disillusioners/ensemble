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
import os
import time
from typing import Any

from daemon.services.project_normalizer import normalize_project_id

logger = logging.getLogger(__name__)


# Kill-switch env for the automatic CAPTURED flow (Phase 5 of Skill
# Evolution). Default OFF — automatic skill capture from successful
# tasks is opt-in. Per-call env resolution (no cached config field) to
# match the ``_resolve_repair_enabled`` exemplar at
# ``daemon/tools/ens_db_tools.py``. When OFF, every entry point that
# could spawn a ``skill_capture`` job must no-op:
# ``SkillJobDispatcher.enqueue_capture`` returns ``None``;
# ``SkillMetricsService._check_capture_eligibility`` returns ``None``
# without invoking the evolution service; the
# ``skill_execute_capture`` LangChain tool returns a ``{"skipped":
# true, "reason": ...}`` envelope without dispatching. Per-call
# resolution (vs a cached ``SkillEvolutionConfig`` knob) keeps the
# kill-switch live across hot-reload and operator env flips — see
# the no-new-ENSEMBLE_*-flags fix/flag policy (this flag is
# explicitly owner-authorized, not a free-form addition).
SKILL_CAPTURE_KILL_SWITCH_ENV = "ENSEMBLE_SKILL_CAPTURE_ENABLED"


# ── Shared switch token sets (2026-10-22 review minor (c)) ───────────────
# The CAPTURE switch and the EVOLUTION switch MUST accept exactly the
# same spellings — one parser, one token set, no drift. Do NOT invent a
# stricter or laxer set for either switch.
_SWITCH_ON_TOKENS = frozenset({"1", "true", "yes", "on"})
_SWITCH_OFF_TOKENS = frozenset({"0", "false", "no", "off"})


def _resolve_switch(env_name: str, configured_default: bool = False) -> bool:
    """Resolve a kill-switch env var against the SHARED token sets.

    Resolution order:

    1. ``env_name`` unset → ``configured_default``.
    2. ``env_name`` set but empty/whitespace → ``configured_default``
       (empty = "no opinion", defer to config).
    3. Recognized OFF token (``0``/``false``/``no``/``off``) → ``False``.
    4. Recognized ON token (``1``/``true``/``yes``/``on``) → ``True``.
    5. Anything else → ``ValueError`` (fail-closed on misconfiguration
       rather than silently enabling or disabling the lane).

    Args:
        env_name: The environment variable to read.
        configured_default: The configured default used when the env
            var is unset or empty.

    Returns:
        The resolved switch state.
    """
    raw = os.environ.get(env_name)
    if raw is None:
        return configured_default
    val = raw.strip().lower()
    if val == "":
        return configured_default
    if val in _SWITCH_OFF_TOKENS:
        return False
    if val in _SWITCH_ON_TOKENS:
        return True
    raise ValueError(
        f"Invalid value for {env_name}: {raw!r} "
        f"(expected 0/1/true/false/yes/no/on/off; unset/empty = default)"
    )


def _log_invalid_switch_env_at_load() -> None:
    """Load-time loud check for invalid kill-switch env values.

    The resolvers raise ``ValueError`` at CALL time; this makes a typo
    in the operator's env visible at process start as well (a switch
    that is never exercised would otherwise stay silent until the lane
    is first used). Purely observational — it never mutates state and
    never changes a resolution.
    """
    for env_name in (
        SKILL_CAPTURE_KILL_SWITCH_ENV,
        SKILL_EVOLUTION_KILL_SWITCH_ENV,
    ):
        raw = os.environ.get(env_name)
        if raw is None:
            continue
        val = raw.strip().lower()
        if val == "" or val in _SWITCH_ON_TOKENS or val in _SWITCH_OFF_TOKENS:
            continue
        logger.error(
            "Invalid %s=%r at load time — expected one of %s / %s "
            "(unset/empty = default). The switch raises ValueError on "
            "first use.",
            env_name,
            raw,
            sorted(_SWITCH_ON_TOKENS),
            sorted(_SWITCH_OFF_TOKENS),
        )


def _resolve_capture_enabled() -> bool:
    """Resolve the skill-capture kill-switch (default OFF; flag-ON enables).

    Resolution order:

    1. ``ENSEMBLE_SKILL_CAPTURE_ENABLED`` env (canonical).
    2. Unset / empty string → default OFF (capture disabled).

    Recognized ON values (case-insensitive): ``"1"``, ``"true"``,
    ``"yes"``, ``"on"``. Recognized OFF values: ``"0"``, ``"false"``,
    ``"no"``, ``"off"`` (and the default-OFF unset/empty case) — the
    SHARED token sets (see ``_SWITCH_ON_TOKENS`` / ``_SWITCH_OFF_TOKENS``),
    identical to the evolution switch's.

    Invalid env values raise ``ValueError`` — fail-closed on
    misconfiguration rather than silently enabling capture (parity
    with ``_resolve_repair_enabled`` at
    ``daemon/tools/ens_db_tools.py:131``).

    Returns:
        ``True`` if automatic capture should run; ``False`` if the
        kill-switch is OFF (every capture entry point must no-op).
    """
    return _resolve_switch(SKILL_CAPTURE_KILL_SWITCH_ENV, False)


# ── Evolution-lane kill-switch (2026-10-22 confinement fix) ─────────
#
# Incident 2026-10-21 (§5 L2): ``enqueue_evolution`` was the ONE
# skill-job admission lane with NO switch at all — the CAPTURED lane
# got a kill-switch (above, default OFF) while the Tier-3 mutation
# lane (FIX/DERIVED/CAPTURED admissions) admitted every ``skill_fix``
# call immediately, driving the 10-job burst (10 skill_fix calls → 10
# skill_evolution jobs, same skill re-FIXed 3× in 38 min).
#
# Default OFF. Resolution order (in :func:`_resolve_evolution_enabled`):
#
# 1. ``ENSEMBLE_SKILL_EVOLUTION_ENABLED`` env (canonical kill-switch,
#    wins over config — an operator emergency-off works without a
#    config change and is live per-call, no cache).
# 2. Unset / empty → the configured default
#    (``SkillEvolutionConfig.evolution_lane_enabled``, itself default
#    False — see ``daemon/config.py``).
#
# When OFF, EVERY producer of ``skill_evolution`` jobs no-ops at this
# chokepoint: the ``skill_fix`` tool (``dispatch_fix``), the admin
# HTTP endpoint ``POST /api/skills/{id}/fix``, the trigger-engine
# ``evolve_fix`` follow-through in
# ``InstanceManager._run_skill_metric_scan``, and any feedback-filed
# fix request (they all funnel through ``enqueue_evolution``).
# Tier-2 ``skill_analysis`` jobs, ``skill_metric_scan`` jobs, and the
# CAPTURED lane (its own switch above) are NOT gated here.
SKILL_EVOLUTION_KILL_SWITCH_ENV = "ENSEMBLE_SKILL_EVOLUTION_ENABLED"


def _resolve_evolution_enabled(configured_default: bool = False) -> bool:
    """Resolve the evolution-lane kill-switch (default OFF; opt-in enables).

    Resolution order:

    1. ``ENSEMBLE_SKILL_EVOLUTION_ENABLED`` env (canonical) — when set
       (non-empty), it WINS over the configured default so an operator
       can force the lane off (or on) without touching config.
    2. Unset / empty string → ``configured_default`` (the dispatcher's
       constructed value from ``SkillEvolutionConfig.evolution_lane_enabled``;
       that field itself defaults to False).

    Recognized ON values (case-insensitive): ``"1"``, ``"true"``,
    ``"yes"``, ``"on"``. Recognized OFF values: ``"0"``, ``"false"``,
    ``"no"``, ``"off"``.

    Invalid env values raise ``ValueError`` — fail-closed on
    misconfiguration rather than silently running (or silently
    disabling) the lane; parity with :func:`_resolve_capture_enabled`.

    Returns:
        ``True`` if Tier-3 evolution-job admission should proceed;
        ``False`` if the kill-switch is OFF (``enqueue_evolution``
        must no-op with ``None``).
    """
    return _resolve_switch(
        SKILL_EVOLUTION_KILL_SWITCH_ENV, configured_default
    )


def is_evolution_lane_enabled(configured_default: bool = False) -> bool:
    """PUBLIC accessor for the Tier-3 evolution-lane state.

    The single resolution point every caller outside this module uses
    (the manager's metric-scan fallback, the job processor's
    execution-time check, the HTTP layer). Import the PUBLIC name
    rather than the private ``_resolve_evolution_enabled`` — the
    private symbol is an implementation detail of this module.

    Args:
        configured_default: The configured default (from
            ``SkillEvolutionConfig.evolution_lane_enabled``), used
            when the env var is unset or empty.

    Returns:
        ``True`` when Tier-3 evolution-job admission is enabled.
    """
    return _resolve_evolution_enabled(configured_default)


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

# Evolution-type literals (``evolution_type`` on the evolution job and
# in its metadata). Named constants so the admission controls (the FIX
# cooldown / idempotency key) cannot drift from the producer side on a
# literal typo — 2026-10-22 review minor (b).
EVOLUTION_TYPE_FIX = "FIX"
EVOLUTION_TYPE_DERIVED = "DERIVED"
EVOLUTION_TYPE_CAPTURED = "CAPTURED"

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

    def __init__(
        self,
        job_service: Any,
        queue_repo: Any,
        evolution_lane_enabled: bool = False,
        fix_cooldown_seconds: int = 1800,
    ) -> None:
        """Store the job service and queue repository.

        Args:
            job_service: :class:`JobQueueService` — provides ``enqueue()``.
            queue_repo: :class:`JobQueueRepository` — provides
                ``get_by_name(project_id, queue_name)``.
            evolution_lane_enabled: Configured default for the
                Tier-3 evolution-lane kill-switch, wired from
                ``SkillEvolutionConfig.evolution_lane_enabled``
                (itself default False). The
                ``ENSEMBLE_SKILL_EVOLUTION_ENABLED`` env, when set,
                overrides this per-call — see
                :func:`_resolve_evolution_enabled`.
            fix_cooldown_seconds: Minimum seconds between ACCEPTED
                ``FIX`` admissions for the same
                ``(project_id, skill_id)``. Re-filing the same fix
                inside the window is refused (``None``) — this is
                the in-process half of the amplifier control (the
                DB half is the ``idempotency_key`` non-terminal
                dedup). Default 1800 (30 min; the 2026-10-21 burst
                re-FIXed the same skill 3× in 38 min).
        """
        self._job_service = job_service
        self._queue_repo = queue_repo
        self._evolution_lane_enabled = bool(evolution_lane_enabled)
        self._fix_cooldown_seconds = max(0, int(fix_cooldown_seconds))
        # (normalized_project_id, skill_id) -> monotonic timestamp of
        # the last ACCEPTED FIX admission. RAM-only by design: it is
        # a rate limiter, not durable state; a daemon restart resets
        # it, and the ``idempotency_key`` dedup still covers the
        # non-terminal window across restarts.
        self._fix_admission_cooldown: dict[tuple[str, str], float] = {}

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
        idempotency_key: str | None = None,
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
            idempotency_key: Optional dedup key forwarded to
                ``JobQueueService.enqueue`` — when a NON-TERMINAL
                job with the same key exists, the existing job is
                returned instead of creating a duplicate row. The
                FIX lane sets this (see :meth:`enqueue_evolution`);
                the other lanes leave it ``None`` (Tier-2 analysis
                re-runs are idempotent reads and a fresh scan job
                per cycle is the intended shape).

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
            # agent_tag intentionally omitted: SKILL_KEEPER is an internal system agent, not versioned
            agent_id=SKILL_KEEPER_AGENT_ID,
            message=message,
            source=SOURCE_TAG,
            project_id=normalized_project_id,
            queue_id=queue_id,
            job_type=job_type,
            metadata=metadata if metadata is not None else {},
            idempotency_key=idempotency_key,
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
    ) -> str | None:
        """Enqueue a ``skill_evolution`` job (Tier 3 — main LLM evolution).

        **GATED admission lane (2026-10-22 confinement fix).** This is
        the single chokepoint every producer of Tier-3 mutation jobs
        funnels through — the ``skill_fix`` tool, the admin
        ``POST /api/skills/{id}/fix`` endpoint, the trigger engine's
        ``evolve_fix`` follow-through in
        ``InstanceManager._run_skill_metric_scan``, and any
        feedback-filed fix request. Three controls apply, in order:

        1. **Kill-switch** — ``ENSEMBLE_SKILL_EVOLUTION_ENABLED``
           env wins; unset defers to the constructed
           ``evolution_lane_enabled`` (config default False). When
           OFF the method returns ``None`` WITHOUT touching the job
           service — the no-op contract, mirroring
           :meth:`enqueue_capture`.
        2. **FIX cooldown** — a second FIX admission for the same
           ``(project_id, skill_id)`` within
           ``fix_cooldown_seconds`` (default 1800) of the last
           ACCEPTED one is refused (``None``). Kills the
           self-propagating re-FIX sweep class (2026-10-21: same
           skill re-FIXed 3× in 38 min).
        3. **FIX idempotency** — the enqueue carries
           ``idempotency_key="skill_fix:<project>:<skill_id>"`` so a
           non-terminal duplicate collapses onto the live job at the
           DB layer.

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
            ``job_id`` of the evolution JobItem, or ``None`` when
            the kill-switch is OFF or the FIX cooldown refuses the
            admission. ``None`` is the no-op contract — callers
            must treat it as "not admitted", not as success.
        """
        # ── 1. Kill-switch gate ──────────────────────────────────
        # Belt-and-braces single chokepoint: env wins, config default
        # otherwise (default OFF). Covers EVERY producer — including
        # jobs already-queued and re-dispatched after a flag flip is
        # NOT covered here (those are already-admitted rows); what is
        # covered is every NEW admission attempt.
        if not _resolve_evolution_enabled(self._evolution_lane_enabled):
            # INFO, not WARNING: the lane is OFF BY DEFAULT — a
            # refused admission is the EXPECTED steady state, not an
            # anomaly (2026-10-22 review minor (d)). The cooldown
            # refusal below stays at WARNING (that one IS an anomaly:
            # a repeat admission attempt).
            logger.info(
                "SkillJobDispatcher.enqueue_evolution: evolution lane "
                "gate OFF (env %s unset-or-off, configured default=%s). "
                "No-op — no skill_evolution job admitted for skill %s.",
                SKILL_EVOLUTION_KILL_SWITCH_ENV,
                self._evolution_lane_enabled,
                skill_id,
            )
            return None

        normalized_project_id = normalize_project_id(project_id)
        is_fix = evolution_type == EVOLUTION_TYPE_FIX
        cooldown_key = (normalized_project_id, skill_id)

        if is_fix and self._fix_cooldown_seconds > 0:
            now = time.monotonic()
            last = self._fix_admission_cooldown.get(cooldown_key)
            if last is not None:
                elapsed = now - last
                if elapsed < self._fix_cooldown_seconds:
                    logger.warning(
                        "SkillJobDispatcher.enqueue_evolution: FIX "
                        "cooldown active for (project=%s, skill=%s) — "
                        "%.0fs since last admission, window=%ds. "
                        "Refusing duplicate FIX admission.",
                        normalized_project_id[:8] if normalized_project_id else "<none>",
                        skill_id[:8] if isinstance(skill_id, str) else skill_id,
                        elapsed,
                        self._fix_cooldown_seconds,
                    )
                    return None

        message = f"Evolve skill {skill_id}. Type: {evolution_type}. Direction: {direction}"
        job_id = await self._enqueue_skill_keeper_job(
            project_id,
            JOB_TYPE_EVOLUTION,
            message,
            metadata={
                "skill_id": skill_id,
                "evolution_type": evolution_type,
                "direction": direction,
            },
            idempotency_key=(
                f"skill_fix:{normalized_project_id}:{skill_id}" if is_fix else None
            ),
        )
        if is_fix and job_id:
            # Stamp the cooldown on BOTH outcomes (fresh row or
            # idempotent dedup-hit) — a dedup-hit means a live job
            # for this key already exists, which is exactly the
            # state the cooldown should keep protecting.
            self._fix_admission_cooldown[cooldown_key] = time.monotonic()
        return job_id

    # ── Public API: CAPTURED flow ───────────────────────────────────

    async def enqueue_capture(
        self,
        project_id: str | None,
        task_details: dict[str, Any],
    ) -> str | None:
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
            ``job_id`` of the capture JobItem, or ``None`` when the
            ``ENSEMBLE_SKILL_CAPTURE_ENABLED`` kill-switch is OFF
            (per-call resolved via :func:`_resolve_capture_enabled`).
            ``None`` is the no-op contract — ``record_task_completion``
            ignores the return value, so disabled callers proceed
            silently without dispatching.
        """
        # ── Kill-switch gate ─────────────────────────────────────
        # Belt-and-braces: even though the metrics-service gate at
        # ``SkillMetricsService._check_capture_eligibility`` is the
        # primary "skip the trigger entirely" check, this gate covers
        # every other producer of ``skill_capture`` jobs (incl. jobs
        # already-queued and re-dispatched after a flag flip).
        if not _resolve_capture_enabled():
            logger.debug(
                "SkillJobDispatcher.enqueue_capture: kill-switch OFF "
                "(env %s=disabled). No-op — no skill_capture job "
                "will be enqueued.",
                SKILL_CAPTURE_KILL_SWITCH_ENV,
            )
            return None

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
    ) -> str | None:
        """Dispatch a user-reported skill fix request (GATED).

        **This dispatches a Tier-3 mutation job IMMEDIATELY** (a
        ``skill_evolution`` job the skill-keeper picks up and executes
        — it does not merely "record a request"). The admission is
        gated by :meth:`enqueue_evolution`:

        * Kill-switch OFF (``ENSEMBLE_SKILL_EVOLUTION_ENABLED`` unset
          + ``SkillEvolutionConfig.evolution_lane_enabled=False``,
          the default) → returns ``None``, NO job is created.
        * FIX cooldown — a repeat fix for the same
          ``(project, skill)`` inside the cooldown window returns
          ``None``.

        Maps to :meth:`enqueue_evolution` with ``evolution_type='FIX'``
        and a ``direction`` string composed from the issue description
        plus the optional suggested fix. The direction text is stored
        verbatim and becomes part of the evolution prompt — treat it
        as untrusted filer input, not as a verified instruction.

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
            ``job_id`` of the dispatched evolution JobItem, or
            ``None`` when the evolution-lane gate is OFF or the FIX
            cooldown refuses the repeat admission.
        """
        del current_instance_id  # Reserved for Phase 5 audit hooks.
        direction = issue_description
        if suggested_fix:
            direction = f"{issue_description}\n\nSuggested fix: {suggested_fix}"
        return await self.enqueue_evolution(
            project_id, skill_id, EVOLUTION_TYPE_FIX, direction
        )

# Load-time loud check for invalid kill-switch env values (2026-10-22
# review minor (c)). Runs at module import — AFTER both switch
# constants are defined — so a typo in the operator's env surfaces at
# process start instead of only when the lane is first exercised.
_log_invalid_switch_env_at_load()
