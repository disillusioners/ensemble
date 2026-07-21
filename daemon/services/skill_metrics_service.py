"""Skill metrics service — Tier 0 passive recorder.

Phase 4 of the Skill Evolution System. Pure DB work, no LLM
calls (Tier 0 in the cost tier model). Called by the
job-queue completion hook after every task completion to
stamp usage records, bump denormalized counters on the
``skills`` row, and refresh the per-skill ``last_used_at``
timestamp.

Design highlights
-----------------

* **Sync repo calls behind ``asyncio.to_thread``.** All five
  repositories are synchronous (matching the Phase 1
  repository contract); every DB call hops to a worker
  thread so the async caller never blocks the event loop.

* **One usage record per injected skill.** For each skill
  listed in the instance's ``last_injected_skill_ids``
  metadata, the service writes a single
  :class:`SkillUsageRecord` capturing the four signal
  booleans (``selected``, ``applied``, ``task_succeeded``,
  ``fallback``) plus timing data. The record is created
  with ``selected=True``; the agent later overwrites
  ``applied`` via the ``skill_feedback`` tool.

* **Counter semantics.** The fallback heuristic is::

      fallback = (consecutive_failures > 0) and (not task_succeeded)

  — i.e. "the skill was applied to a failing task AND
  already had a streak of failures going in". The
  ``consecutive_failures`` counter is reset to ``0`` on
  every successful task (the agent proved the skill helped)
  and bumped by ``1`` on every failed task (the streak
  grows). After ``record_task_completion``, the
  ``last_used_at`` timestamp on the skill row is also
  stamped.

* **Capture vs application.** ``last_injected_skill_ids``
  reflects what was *injected* into the prompt. Whether the
  skill was actually *applied* (consumed by the agent) is
  recorded via the ``skill_feedback`` tool on the usage
  record's ``applied`` / ``feedback_applied`` column. W6
  capture eligibility is therefore evaluated against
  ``feedback_applied``, not ``last_injected_skill_ids``.

* **Instance metadata clear.** ``last_injected_skill_ids``
  is cleared on the instance row after recording so the
  next task starts with a fresh injection set. If
  ``last_injected_skill_ids`` is missing or empty, the
  service no-ops (no usage records, no counter bumps).

* **Graceful failures.** All sync DB calls are wrapped in
  ``asyncio.to_thread``; any exception is logged and
  swallowed so the job-queue completion hook (a
  separate session) never blocks on metrics failure.

A/B comparison
--------------

:meth:`get_ab_comparison_stats` reads persistent state from
``skill_ab_tests`` and computes per-variant stats from
``skill_usage_records`` filtered by ``ab_test_group``
(records are auto-tagged with the skill's
``ab_test_group`` at insertion time). Two scores feed the
resolution decision:

* **completion_rate_a/b** — raw ``completions / total``
  (kept for back-compat with downstream consumers).
* **composite_score_a/b** — weighted 5-metric blend
  (``ab_weight_completion`` / ``ab_weight_applied`` /
  ``ab_weight_efficiency`` / ``ab_weight_fallback`` /
  ``ab_weight_speed``) computed by
  :meth:`_composite_score` against the global baselines
  from :meth:`_get_global_baselines`.

``difference`` is ``abs(composite_score_a - composite_score_b)``.
A test is ``ready_to_resolve`` iff ``comparisons >=
ab_sample_size`` AND ``abs(diff) >= ab_min_difference``;
otherwise it's flagged ``needs_more_data`` (the engine bumps
``extension_count`` once ``max_extensions`` is exhausted —
handled by Phase 5).
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone, timedelta
from typing import Any, Optional

logger = logging.getLogger(__name__)


# Key on ``instance_metadata`` storing the list of skill IDs
# the Phase 3 injection service injected into the most recent
# task. Read by ``record_task_completion`` and cleared after
# recording so the next task starts clean.
INJECTED_SKILLS_METADATA_KEY: str = "last_injected_skill_ids"


def _now_iso() -> str:
    """Return current UTC time as ISO-8601 string.

    Mirrors the per-module helper in ``daemon/repositories/skill/repository.py``
    so the timestamp format matches the rest of the skill
    subsystem.
    """
    return datetime.now(timezone.utc).isoformat()


# ============================================================
# SkillMetricsService
# ============================================================


class SkillMetricsService:
    """Tier 0 passive metrics recorder for the skill system.

    Records every skill usage after task completion:
    ``selected`` (was the skill injected/searched?),
    ``applied`` (did the agent use it? — set later via the
    ``skill_feedback`` tool), ``task_succeeded`` (did the
    task complete?), ``iterations`` (LLM iterations), and
    ``duration_seconds``. The denormalized counters on the
    ``skills`` row are bumped atomically alongside each
    usage record.

    The constructor takes all five repositories the service
    touches plus the ``SkillEvolutionConfig`` (for A/B
    sample-size / min-difference thresholds). The
    ``instance_repo`` arg is optional — when ``None`` the
    service still functions for ``record_feedback``,
    ``get_skill_stats``, and ``get_ab_comparison_stats`` but
    ``record_task_completion`` becomes a no-op for the
    "read injected skills from metadata" step. Production
    wiring always passes the instance repo.

    Attributes:
        usage_repo: Sync :class:`SkillUsageRepository` for
            ``skill_usage_records``.
        skill_repo: Sync :class:`SkillRepository` for the
            ``skills`` table (counter bumps, fetches).
        trigger_repo: Sync :class:`SkillTriggerRepository`
            (reserved for future cross-trigger stats; not
            read by Phase 4 methods).
        ab_test_repo: Sync :class:`SkillABTestRepository`
            for ``skill_ab_tests`` (read by
            ``get_ab_comparison_stats``).
        config: :class:`~daemon.config.SkillEvolutionConfig`
            — provides ``ab_sample_size``, ``ab_min_difference``,
            ``max_extensions``.
        instance_repo: Optional sync instance repository used
            to read ``last_injected_skill_ids`` from instance
            metadata and clear it after recording.
        evolution_service: Optional :class:`SkillEvolutionService`
            (Phase 5). When set, ``record_task_completion``
            runs the CAPTURED-flow eligibility check after
            recording metrics — a successful task that didn't
            apply any skill (per ``feedback_applied``) and hit
            the complexity thresholds is enqueued for skill
            capture.
        agent_id_resolver: Optional callable
            ``(agent_id: str) -> AgentMetadata | None``. The
            CAPTURED check only fires when the resolved
            metadata has ``skill_injection=True``. Accepting a
            callable (instead of a registry reference) keeps
            the service decoupled from the registry module
            and makes it trivially mockable in tests.
    """

    def __init__(
        self,
        usage_repo: Any,
        skill_repo: Any,
        trigger_repo: Any,
        ab_test_repo: Any,
        config: Any,
        instance_repo: Any = None,
        evolution_service: Any = None,
        agent_id_resolver: Any = None,
    ) -> None:
        """Store repositories, config, and optional Phase 5 collaborators.

        Args:
            usage_repo: :class:`SkillUsageRepository`.
            skill_repo: :class:`SkillRepository`.
            trigger_repo: :class:`SkillTriggerRepository`.
            ab_test_repo: :class:`SkillABTestRepository`.
            config: :class:`~daemon.config.SkillEvolutionConfig`
                (provides ``ab_sample_size`` / ``ab_min_difference``
                / ``max_extensions`` /
                ``capture_min_iterations`` /
                ``capture_min_duration_seconds``).
            instance_repo: Optional instance repository. When
                ``None``, ``record_task_completion`` cannot read
                injected-skill metadata and silently no-ops for
                that step; all other methods work normally.
            evolution_service: Optional
                :class:`~daemon.services.skill_evolution_service.SkillEvolutionService`
                (Phase 5). When provided, the CAPTURED-flow
                eligibility check fires at the end of
                :meth:`record_task_completion`. ``None`` disables
                capture entirely — the rest of the metrics path
                is unaffected.
            agent_id_resolver: Optional callable ``(agent_id) ->
                AgentMetadata | None``. Used to gate the CAPTURED
                check on ``skill_injection``. When ``None``, the
                CAPTURED check is skipped (treated as
                ``skill_injection=False`` for every agent).
        """
        self.usage_repo = usage_repo
        self.skill_repo = skill_repo
        self.trigger_repo = trigger_repo
        self.ab_test_repo = ab_test_repo
        self.config = config
        self.instance_repo = instance_repo
        self.evolution_service = evolution_service
        self.agent_id_resolver = agent_id_resolver

    def set_evolution_service(self, evolution_service: Any) -> None:
        """Attach a Phase 5 :class:`SkillEvolutionService` after construction.

        Breaks the construction-time cycle between this service and
        the evolution service: the evolution service depends on the
        metrics service (for ``get_ab_comparison_stats``) and the
        metrics service depends on the evolution service (for
        ``check_and_capture``). The manager builds both in sequence
        and closes the loop with this setter.

        Re-assigning replaces the previous handle. Pass ``None`` to
        disable the CAPTURED check without re-instantiating the
        service.

        Args:
            evolution_service: The Phase 5 evolution service to wire
                in, or ``None`` to clear.
        """
        self.evolution_service = evolution_service

    def set_job_dispatcher(self, dispatcher: Any) -> None:
        """Attach the Phase 5 :class:`SkillJobDispatcher` after construction.

        The metrics service uses the dispatcher to actually enqueue
        CAPTURED jobs once :meth:`SkillEvolutionService.check_and_capture`
        decides a task is eligible for capture. Without the dispatcher
        wired in, the eligibility check still runs (so the
        evolution-service stats stay consistent) but the resulting
        ``task_details`` dict is silently dropped — there is nowhere
        to send the job.

        The dispatcher is constructed in
        :meth:`InstanceManager.set_job_queue_service`, which is called
        AFTER :meth:`InstanceManager.__init__` (the dispatcher needs
        ``JobQueueService._queue_repo`` which only exists post-init).
        This setter bridges that gap — the manager wires it
        immediately after building the dispatcher so subsequent
        completion-hook invocations can enqueue captures.

        Args:
            dispatcher: The Phase 5 :class:`SkillJobDispatcher` to
                wire in, or ``None`` to clear. When ``None``, the
                CAPTURED path remains eligibility-checked but no
                capture jobs are enqueued (soft-disabled).
        """
        self._skill_job_dispatcher = dispatcher

    # --------------------------------------------------------
    # Recording — task completion
    # --------------------------------------------------------

    async def record_task_completion(
        self,
        instance_id: str,
        agent_id: str,
        project_id: Optional[str],
        task_succeeded: bool,
        iterations: int,
        duration_seconds: int,
        task_message: str = "",
    ) -> int:
        """Record skill usage after a task completes.

        Steps:

        1. Read ``last_injected_skill_ids`` from the instance's
           metadata. If the metadata key is missing or empty,
           the method no-ops (returns ``0``).
        2. For each injected skill:

           a. Look up the skill row to read its current
              ``consecutive_failures`` (used by the fallback
              heuristic). A missing skill row is skipped with
              a warning — a deleted skill shouldn't break the
              completion hook.
           b. Compute ``fallback = (consecutive_failures > 0)
              and (not task_succeeded)``.
           c. Insert a :class:`SkillUsageRecord` with
              ``selected=True`` and ``applied`` defaulting to
              ``False``. ``applied`` is overwritten later by
              :meth:`record_feedback` when the agent calls
              ``skill_feedback``.
           d. Bump denormalized counters on the skill row:
              ``total_selections`` by ``+1``;
              ``total_completions`` by ``+1`` on success;
              ``total_fallbacks`` by ``+1`` if ``fallback``;
              ``consecutive_failures`` reset to ``0`` on
              success or incremented by ``+1`` on failure
              (uses :meth:`reset_counter` /
              :meth:`increment_counter`).
           e. Refresh ``last_used_at`` via
              :meth:`touch_last_used`.

        3. Clear the ``last_injected_skill_ids`` metadata key
           on the instance (so the next task starts clean).
        4. **CAPTURED-flow eligibility check** (only when an
           ``evolution_service`` was wired in). For successful
           tasks where the agent has ``skill_injection=True``
           and no skill was actually *applied* (per
           ``feedback_applied`` records — injection is NOT
           the same as application), delegate to
           :meth:`SkillEvolutionService.check_and_capture`
           which decides whether to enqueue a skill-capture
           job. Wrapped in soft-fail try/except — capture
           errors NEVER block metrics recording or job
           completion.

        All sync repo calls are wrapped in
        ``asyncio.to_thread``. Exceptions are logged and
        swallowed so the caller (the job-queue completion
        hook) never raises out of this method.

        Args:
            instance_id: The instance that just completed a
                task.
            agent_id: The agent that processed the task.
            project_id: Project scope (``None`` is tolerated;
                usage records require a non-null project, so
                ``None`` is coerced to ``""``).
            task_succeeded: True iff the task ended in
                ``completed`` status.
            iterations: Number of LLM iterations the task
                consumed.
            duration_seconds: Wall-clock seconds the task
                spent.
            task_message: The user input for the task. Optional
                — empty string when the caller doesn't have it.
                Forwarded to the CAPTURED eligibility check so
                the captured-skill prompt has full context.

        Returns:
            Number of usage records inserted. ``0`` when no
            skills were injected (or the instance repo is
            unavailable).
        """
        if self.instance_repo is None:
            logger.debug(
                "SkillMetricsService.record_task_completion: "
                "instance_repo is None — skipping metadata read"
            )
            return 0

        def _read_injected() -> list[str]:
            inst = self.instance_repo.get(instance_id)
            if inst is None:
                return []
            meta = getattr(inst, "instance_metadata", None) or {}
            raw = meta.get(INJECTED_SKILLS_METADATA_KEY)
            if not raw:
                return []
            # Defensive: tolerate any iterable, drop non-string
            # / empty entries. Stored value is expected to be a
            # list of skill IDs (strings).
            return [str(x) for x in raw if x]

        try:
            injected_ids = await asyncio.to_thread(_read_injected)
        except Exception as exc:
            logger.warning(
                f"SkillMetricsService: failed to read injected "
                f"skills for instance {instance_id}: {exc}"
            )
            return 0

        if not injected_ids:
            return 0

        # SkillUsageRecord.project_id is NOT NULL; tolerate
        # ``None`` from older callers by coercing to "".
        usage_project_id = project_id or ""

        inserted = 0
        for skill_id in injected_ids:
            try:
                inserted += await self._record_one(
                    skill_id=skill_id,
                    instance_id=instance_id,
                    agent_id=agent_id,
                    project_id=usage_project_id,
                    task_succeeded=task_succeeded,
                    iterations=iterations,
                    duration_seconds=duration_seconds,
                    task_message=task_message,
                )
            except Exception as exc:
                # Per-skill isolation — a failure on one skill
                # must not block the others.
                logger.warning(
                    f"SkillMetricsService: failed to record usage "
                    f"for skill {skill_id}, instance {instance_id}: "
                    f"{exc}"
                )

        # Clear injected-skill metadata so the next task starts
        # clean. Best-effort — a failure here is logged but
        # doesn't affect the just-written usage records.
        try:
            await asyncio.to_thread(
                self.instance_repo.delete_metadata,
                instance_id,
                INJECTED_SKILLS_METADATA_KEY,
            )
        except Exception as exc:
            logger.warning(
                f"SkillMetricsService: failed to clear injected "
                f"skills metadata for instance {instance_id}: "
                f"{exc}"
            )

        # Also clear ``explicitly_replaced_ids`` — set during the
        # finalize-on-replace flow to blocklist a skill ID from
        # the next auto_load injection. Without this clear, the
        # blocklist persists for the lifetime of the instance
        # (permanently disabling auto_load for the replaced
        # skill), which is wrong: the task has completed, so
        # the blocklist should reset too. Best-effort, same
        # shape as the ``last_injected_skill_ids`` clear above —
        # the key may not exist on instances that never went
        # through a finalize-on-replace, which is fine.
        try:
            await asyncio.to_thread(
                self.instance_repo.delete_metadata,
                instance_id,
                "explicitly_replaced_ids",
            )
        except Exception as exc:
            logger.warning(
                f"SkillMetricsService: failed to clear "
                f"explicitly_replaced_ids metadata for instance "
                f"{instance_id}: {exc}"
            )

        # CAPTURED-flow eligibility check. Runs AFTER metrics
        # recording so even if capture fails (or is skipped)
        # the denormalized counters are already up to date.
        # Soft-fail: any exception here is logged but never
        # propagates — the job-completion hook must always
        # return cleanly.
        try:
            await self._check_capture_eligibility(
                instance_id=instance_id,
                agent_id=agent_id,
                project_id=project_id,
                task_message=task_message,
                task_succeeded=task_succeeded,
                iterations=iterations,
                duration_seconds=duration_seconds,
            )
        except Exception as exc:
            logger.warning(
                f"SkillMetricsService: CAPTURED eligibility check "
                f"failed for instance {instance_id}: {exc}"
            )

        return inserted

    async def _check_capture_eligibility(
        self,
        instance_id: str,
        agent_id: str,
        project_id: Optional[str],
        task_message: str,
        task_succeeded: bool,
        iterations: int,
        duration_seconds: int,
    ) -> Optional[dict]:
        """Run the CAPTURED-flow eligibility gate after metrics recording.

        The CAPTURED flow auto-extracts a reusable skill from a
        successful task that did NOT use any existing skill.
        "Did not use" means no usage record for this instance
        has ``feedback_applied=True`` — checking injection
        records alone is not enough, because a skill may have
        been *injected* into the prompt but the agent decided
        not to apply it.

        The check is gated on:

        1. ``evolution_service`` is wired in — otherwise capture
           is a no-op for this service.
        2. ``task_succeeded`` — only successful tasks are
           eligible for capture.
        3. The agent has ``skill_injection=True`` (resolved via
           ``agent_id_resolver``) — capture is a feature of the
           injection subsystem; non-injection agents shouldn't
           spawn captures.
        4. No skill was *applied* on this instance —
           ``SkillUsageRepository.has_applied_for_instance``
           returns False. This is the expensive part; it's
           gated behind steps 1-3 so we don't hit the DB when
           the answer is already "no".
        5. Complexity threshold — at least one of
           ``iterations > capture_min_iterations`` or
           ``duration_seconds > capture_min_duration_seconds``
           must hold. Trivial / instant successes are ignored.

        The :meth:`SkillEvolutionService.check_and_capture`
        method does the final gatekeeping (re-checks
        ``has_applied_for_instance`` to close the race window
        between this read and the LLM call) and enqueues a
        ``skill_capture`` job via the dispatcher.

        Args:
            instance_id: The instance that just completed.
            agent_id: The agent that processed the task.
            project_id: Project scope (``None`` is tolerated).
            task_message: User input — passed through to the
                capture prompt.
            task_succeeded: Whether the task ended in success.
            iterations: LLM iterations the task took.
            duration_seconds: Wall-clock duration.

        Returns:
            The ``task_details`` dict returned by
            ``check_and_capture`` if it enqueued a capture,
            else ``None``. Returned for tests / observability;
            the metrics service itself does not act on the
            return value.
        """
        # Gate 1: evolution service must be wired. This is the
        # most common no-op path during Phase 4 (capture is
        # Phase 5+).
        if self.evolution_service is None:
            return None

        # Gate 2: capture only applies to successful tasks —
        # extracting a skill from a failure would propagate
        # the failure pattern.
        if not task_succeeded:
            return None

        # Gate 3: agent must have skill_injection enabled.
        # ``agent_id_resolver`` is a callable the manager wires
        # up against the registry. When ``None`` or it returns
        # ``None``, we treat the agent as non-injection (skip).
        skill_injection_enabled = False
        if self.agent_id_resolver is not None:
            try:
                agent_meta = self.agent_id_resolver(agent_id)
                skill_injection_enabled = bool(
                    getattr(agent_meta, "skill_injection", False)
                )
            except Exception as exc:
                logger.warning(
                    f"SkillMetricsService: agent_id_resolver "
                    f"failed for agent_id={agent_id}: {exc}"
                )
                skill_injection_enabled = False
        if not skill_injection_enabled:
            return None

        # Gate 4: no skill must have been applied to this
        # instance. Using ``feedback_applied`` (not injection
        # records) is the spec — a skill may have been offered
        # to the agent without being consumed.
        try:
            applied = await asyncio.to_thread(
                self.usage_repo.has_applied_for_instance,
                instance_id,
            )
        except Exception as exc:
            logger.warning(
                f"SkillMetricsService: has_applied_for_instance "
                f"failed for instance {instance_id}: {exc}"
            )
            # Fail closed: if we can't tell, don't capture.
            return None
        if applied:
            # A skill was already applied — the success is
            # already attributed to that skill. Don't spawn
            # a sibling capture.
            return None

        # Gate 5: complexity threshold. The evolution service
        # also enforces this; we duplicate the check here so
        # the capture-path log line makes the threshold
        # explicit AND we don't pay the cost of an LLM-bound
        # enqueue path for trivial successes.
        min_iter = (
            getattr(self.config, "capture_min_iterations", 5) or 5
        )
        min_dur = (
            getattr(self.config, "capture_min_duration_seconds", 60)
            or 60
        )
        if iterations <= min_iter and duration_seconds <= min_dur:
            return None

        # All gates passed — delegate to the evolution service.
        # ``check_and_capture`` re-checks ``has_applied_for_instance``
        # to close the TOCTOU window between our read and the
        # eventual LLM call.
        try:
            task_details = await self.evolution_service.check_and_capture(
                instance_id=instance_id,
                agent_id=agent_id,
                project_id=project_id,
                task_message=task_message,
                task_succeeded=task_succeeded,
                iterations=iterations,
                duration_seconds=duration_seconds,
            )
        except Exception as exc:
            logger.warning(
                f"SkillMetricsService: check_and_capture failed "
                f"for instance {instance_id}: {exc}"
            )
            return None

        # If the evolution service returned task_details (eligible
        # for capture), enqueue the actual CAPTURED job via the
        # dispatcher. The dispatcher may not be wired yet (early-boot
        # race) — soft-fail in that case: the metrics path must
        # never block on dispatch failures.
        if task_details is not None and self._skill_job_dispatcher is not None:
            try:
                await self._skill_job_dispatcher.enqueue_capture(
                    task_details.get("project_id"),
                    task_details,
                )
            except Exception as exc:
                logger.warning(
                    f"SkillMetricsService: enqueue_capture failed for "
                    f"instance {instance_id}: {exc}"
                )

        return task_details

    async def _record_one(
        self,
        skill_id: str,
        instance_id: str,
        agent_id: str,
        project_id: str,
        task_succeeded: bool,
        iterations: int,
        duration_seconds: int,
        task_message: str = "",
    ) -> int:
        """Record one (skill, instance) pair end-to-end (idempotent).

        Sync helper called from :meth:`record_task_completion`
        via ``asyncio.to_thread`` (one block per skill).

        **Idempotent insert-or-update contract (production fix):**
        the agent's ``skill_feedback`` tool may fire BEFORE this
        completion hook runs (it always runs from inside the
        agent turn, while the hook fires when the message task
        transitions to terminal — i.e. AFTER the turn). When
        ``skill_feedback`` lands first AND finds no usage record
        (e.g. on ``process_message`` task paths that don't go
        through the job-queue completion hook at all), it inserts
        a fresh record with feedback signals stamped. This method
        MUST NOT insert a duplicate row in that case — instead it
        calls :meth:`SkillUsageRepository.update_completion` so the
        task-outcome columns (``task_succeeded`` /
        ``iterations`` / ``duration_seconds``) get filled in on
        the existing row without losing the feedback signal or
        double-bumping ``total_selections``.

        Steps:

        1. Look up the existing latest :class:`SkillUsageRecord`
           for ``(skill_id, instance_id)``.
           - EXISTS → ``update_completion`` (skip counter bumps
             — the on-miss insertion already counted
             ``total_selections`` and possibly
             ``total_fallbacks``).
           - MISSING → standard INSERT path: read
             ``consecutive_failures``, write the row, bump
             denormalized counters (``total_selections`` and
             ``total_completions`` on success).

        Args:
            skill_id: Skill to record.
            instance_id: Owning instance.
            agent_id: Owning agent.
            project_id: Project scope (already coerced to
                non-empty string).
            task_succeeded: True iff the task ended in
                ``completed``.
            iterations: LLM iterations the task took.
            duration_seconds: Wall-clock duration.
            task_message: The user input for the task (snapshot
                of the triggering request, truncated to
                ``TASK_MESSAGE_MAX_LEN`` by the caller). Forwarded
                to both the INSERT and UPDATE paths so the
                ``skill_usage_records.task_message`` column carries
                the user's actual ask — required by the CAPTURED
                skill-evolution flow. Empty string when the caller
                doesn't have it; ``None``-ish values are accepted
                and forwarded verbatim (the repo coerces to
                ``NULL`` on the update branch when the value
                is ``None``).

        Returns:
            ``1`` if a usage record was inserted OR updated;
            ``0`` if the skill row was missing (no-op).
        """

        def _do_record() -> int:
            # Idempotency guard: if the feedback path already
            # inserted a row (because the agent called
            # ``skill_feedback`` first or this is a
            # ``process_message`` task that has no completion
            # hook), UPDATE the existing row's completion
            # columns instead of inserting a duplicate. The
            # on-miss insert already bumped ``total_selections``
            # (and ``total_fallbacks`` when ``applied is
            # False``), so this path must NOT bump them again.
            # ``task_message`` is forwarded here so the
            # CAPTURED flow gets the user's actual request even
            # on the feedback-first path (the on-miss insert
            # can't know what the user asked — only the
            # completion hook can).
            existing = self.usage_repo.get_latest_for_skill_instance(
                skill_id=skill_id, instance_id=instance_id
            )
            if existing is not None:
                updated = self.usage_repo.update_completion(
                    record_id=existing.id,
                    task_succeeded=task_succeeded,
                    iterations=iterations,
                    duration_seconds=duration_seconds,
                    task_message=task_message,
                )
                if updated is None:
                    # Race — record was deleted between our
                    # get_latest and update_completion. Fall
                    # through to the INSERT path so we still
                    # record something; this is the rarer case
                    # and the denormalized counters get bumped
                    # exactly once.
                    logger.debug(
                        f"SkillMetricsService: completion row raced "
                        f"away for skill={skill_id}, falling back to "
                        f"INSERT"
                    )
                else:
                    # Counter bump for completions OUTSIDE the
                    # on-miss insert path. ``total_completions``
                    # is the only counter that's safe to bump
                    # here — ``total_selections`` was already
                    # bumped by the on-miss insert, and the
                    # fallback/total_applied counters are owned
                    # by the feedback path.
                    if task_succeeded:
                        self.skill_repo.increment_counter(
                            skill_id, "total_completions", amount=1
                        )
                    return 1

            skill = self.skill_repo.get(skill_id)
            if skill is None:
                logger.warning(
                    f"SkillMetricsService: skill row not found "
                    f"for record_task_completion: id={skill_id}"
                )
                return 0

            # Snapshot the *current* consecutive_failures BEFORE
            # we mutate it — the fallback heuristic compares
            # against the pre-task value.
            current_failures = int(
                getattr(skill, "consecutive_failures", 0) or 0
            )
            # Option C (D20): Fallback is determined by worker's explicit
            # skill_feedback call (applied=False), NOT by task
            # success/failure. Neutral default here.
            fallback = False

            # Best-effort lookup of skill's ab_test_group for
            # test-period tagging. When the skill is part of an
            # active A/B test, the usage row inherits the group
            # UUID so the A/B stats query can isolate it (W6).
            # Soft-fail: a missing column on older schemas or a
            # transient DB error must not block the metrics path.
            _ab_group: Optional[str] = None
            _ab_group = getattr(skill, "ab_test_group", None)

            self.usage_repo.create(
                skill_id=skill_id,
                project_id=project_id,
                instance_id=instance_id,
                agent_id=agent_id,
                task_message=task_message if task_message else None,
                selected=True,
                applied=False,  # Set later by skill_feedback tool
                task_succeeded=task_succeeded,
                iterations=iterations,
                duration_seconds=duration_seconds,
                fallback=fallback,
                ab_test_group=_ab_group,
            )

            # Denormalized counters — atomic via raw SQL.
            self.skill_repo.increment_counter(
                skill_id, "total_selections", amount=1
            )
            if task_succeeded:
                self.skill_repo.increment_counter(
                    skill_id, "total_completions", amount=1
                )
            # Option C: total_fallbacks counter now managed by
            # record_feedback() based on worker's applied=False
            # judgment, not task completion.

            if task_succeeded:
                # Successful application resets the streak.
                # Negative-amount increment is the documented
                # way to clear it without a separate reset
                # call (cheaper when the value is 0 already).
                if current_failures > 0:
                    self.skill_repo.reset_counter(
                        skill_id, "consecutive_failures", value=0
                    )
            else:
                self.skill_repo.increment_counter(
                    skill_id, "consecutive_failures", amount=1
                )

            self.skill_repo.touch_last_used(skill_id)
            return 1

        return await asyncio.to_thread(_do_record)

    # --------------------------------------------------------
    # Recording — feedback (skill_feedback tool backend)
    # --------------------------------------------------------

    async def record_feedback(
        self,
        skill_id: str,
        instance_id: str,
        agent_id: str,
        project_id: Optional[str],
        applied: Optional[bool],
        note: str,
        usefulness: Optional[int] = None,
        improvement_note: str = "",
    ) -> bool:
        """Stamp feedback onto the most recent usage record (insert on miss).

        Backend for the ``skill_feedback`` tool (Phase 2 stub,
        implemented here). Locates the latest
        :class:`SkillUsageRecord` for ``(skill_id, instance_id)``
        and stamps ``feedback_applied`` + ``feedback_note``. When
        ``applied`` is explicitly ``True``, the skill row's
        ``total_applied`` counter is also bumped.

        Phase 5 (2026-07-21): additionally stamps the optional
        ``feedback_usefulness`` (1–10 quality score) and
        ``feedback_improvement`` (actionable skill-content
        suggestions) columns. Both are optional — callers who do
        not provide them get the existing behavior unchanged. The
        new columns feed the per-skill usefulness rollup and the
        skill-keeper evolution loop.

        **On-miss insert contract (production fix):** when no
        usage record exists yet, this method INSERTS one on
        demand with feedback signals stamped directly. This is
        necessary because the agents-facing ``skill_feedback``
        tool can be invoked BEFORE ``record_task_completion``
        ever fires — and on parent-dispatched child instances
        (``process_message`` task type, no job-queue completion
        hook) ``record_task_completion`` never fires at all. The
        injected record carries ``selected=True`` (the skill was
        in fact injected — the agent wouldn't be giving feedback
        otherwise), ``applied=applied``, ``feedback_applied=True``,
        ``fallback=(applied is False)``, and neutral
        ``task_succeeded=False`` / ``iterations=0`` /
        ``duration_seconds=0`` so the late-arriving
        ``record_task_completion`` can update those columns via
        :meth:`SkillUsageRepository.update_completion` without
        losing the feedback signal.

        Steps:

        1. Find the latest :class:`SkillUsageRecord` for the
           pair (most recent by ``created_at``).
        2. If the record exists →
           ``update_feedback(record_id, applied, note, ...)`` —
           ``applied=None`` is treated as
           "feedback recorded but outcome unknown" and skips
           the counter bump.
        3. If no record exists → INSERT a fresh usage record
           (see the on-miss contract above). Counter bumps apply
           to the inserted row exactly as if it had been created
           by ``record_task_completion`` first.
        4. If ``applied is True``: increment
           ``total_applied`` on the skill row.

        Args:
            skill_id: Skill being given feedback on.
            instance_id: The instance that produced the usage
                event.
            agent_id: The agent that produced the feedback
                (recorded on the on-miss inserted row, unused
                when updating an existing record — the original
                insert already captured it).
            project_id: Project scope (used to satisfy the
                ``SkillUsageRecord.project_id`` NOT NULL on the
                on-miss insert path; ``None`` is coerced to
                ``""``).
            applied: True if the skill was actually applied,
                False if recorded-but-not-applied, None when
                the agent is unsure.
            note: Free-form feedback note. Empty string when
                no note.
            usefulness: Optional agent-judged quality score
                1-10. ``None`` (default) = not recorded. The
                tool layer is responsible for validating range
                before calling; the service trusts the caller.
            improvement_note: Optional actionable suggestion
                text for improving the skill content itself.
                Empty string (default) = not recorded. Distinct
                from ``note`` which is the general context
                observation. Feeds the skill-keeper evolution
                loop directly.

        Returns:
            ``True`` if a usage record was updated OR inserted
            (and the skill row still exists); ``False`` if the
            skill row itself is missing (the skill was deleted
            between injection and feedback).
        """
        # SkillUsageRecord.project_id is NOT NULL; tolerate
        # ``None`` from older callers by coercing to "" so the
        # on-miss insert path doesn't blow up on the NOT NULL
        # constraint.
        usage_project_id = project_id or ""
        applied_bool: Optional[bool] = (
            bool(applied) if applied is not None else None
        )

        def _do_feedback() -> Optional[str]:
            record = self.usage_repo.get_latest_for_skill_instance(
                skill_id=skill_id, instance_id=instance_id
            )
            if record is None:
                # On-miss insert path. The agent would not be
                # giving feedback on a skill it never received,
                # so ``selected=True`` is the honest default for
                # the inserted row. The skill row's
                # ``ab_test_group`` is propagated so A/B isolation
                # still works even when feedback lands before the
                # completion hook. A missing skill row (deleted
                # between injection and feedback) means there's
                # nothing to attach the signal to — return None
                # and the tool surfaces the soft failure string.
                skill = self.skill_repo.get(skill_id)
                if skill is None:
                    logger.warning(
                        f"SkillMetricsService.record_feedback: "
                        f"skill row not found for on-miss insert: "
                        f"id={skill_id}"
                    )
                    return None
                _ab_group = getattr(skill, "ab_test_group", None)
                _fallback_miss = applied_bool is False
                inserted = self.usage_repo.create(
                    skill_id=skill_id,
                    project_id=usage_project_id,
                    instance_id=instance_id,
                    agent_id=agent_id or "",
                    selected=True,
                    applied=bool(applied_bool) if applied_bool is not None else False,
                    task_succeeded=False,  # Completion hook updates later if it fires.
                    iterations=0,
                    duration_seconds=0,
                    fallback=_fallback_miss,
                    ab_test_group=_ab_group,
                )
                # Stamp feedback_applied / feedback_note onto the
                # freshly-inserted row. ``usage_repo.create`` does
                # not expose these columns (its signature is the
                # narrow completion-hook contract), so we route
                # through ``update_feedback`` — the same code path
                # the existing-record branch uses. ``fallback`` is
                # already set on the inserted row above; we pass
                # ``fallback=None`` here so the update is a no-op
                # for fallback (avoids double-counting the
                # total_fallbacks bump we already did inline below).
                #
                # Phase 5 (2026-07-21): also forward the optional
                # ``usefulness`` / ``improvement_note`` so the
                # freshly-inserted row carries the full feedback
                # signal set. Empty string / None map to
                # "no change" in update_feedback, so callers that
                # don't provide them get the existing behavior.
                self.usage_repo.update_feedback(
                    record_id=inserted.id,
                    applied=bool(applied_bool) if applied_bool is not None else False,
                    note=note or "",
                    fallback=None,
                    usefulness=usefulness,
                    improvement_note=improvement_note or None,
                )
                logger.info(
                    f"SkillMetricsService.record_feedback: created "
                    f"usage record on-miss for skill={skill_id[:8]}..., "
                    f"instance={instance_id[:8]}..., applied={applied_bool}"
                )
                # Bump total_selections so the trigger engine's
                # denominator includes this feedback event even
                # if the completion hook never runs (which is
                # the prod bug for process_message child tasks).
                # Fractions of work: only +1 selection bump per
                # on-miss insert — the completion hook can later
                # run ``update_completion`` without double-counting
                # (it sees the row exists and skips its INSERT
                # path).
                self.skill_repo.increment_counter(
                    skill_id, "total_selections", amount=1
                )
                if _fallback_miss:
                    self.skill_repo.increment_counter(
                        skill_id, "total_fallbacks", amount=1
                    )
                self.skill_repo.touch_last_used(skill_id)
                return inserted.id

            # Found an existing record → standard update path.
            # Option C (D20): Fallback is driven by worker's applied judgment.
            # Capture previous fallback state to detect state transitions.
            _prev_fallback = bool(getattr(record, "fallback", False))

            if applied_bool is False:
                # Worker explicitly said skill was NOT applied/helpful -> real fallback signal
                self.usage_repo.update_feedback(
                    record_id=record.id,
                    applied=False,
                    note=note or "",
                    fallback=True,
                    usefulness=usefulness,
                    improvement_note=improvement_note or None,
                )
                # Issue 6: Increment total_fallbacks ONLY on state change (False->True)
                if not _prev_fallback:
                    self.skill_repo.increment_counter(skill_id, "total_fallbacks", 1)

            elif applied_bool is True:
                self.usage_repo.update_feedback(
                    record_id=record.id,
                    applied=True,
                    note=note or "",
                    fallback=False,
                    usefulness=usefulness,
                    improvement_note=improvement_note or None,
                )
                # Issue 6: Decrement total_fallbacks if this reverses a previous fallback
                if _prev_fallback:
                    self.skill_repo.increment_counter(skill_id, "total_fallbacks", -1)

            else:
                # applied is None — no worker judgment, leave fallback unchanged
                self.usage_repo.update_feedback(
                    record_id=record.id,
                    applied=False,  # stored as False when None
                    note=note or "",
                    usefulness=usefulness,
                    improvement_note=improvement_note or None,
                )

            return record.id

        try:
            record_id = await asyncio.to_thread(_do_feedback)
        except Exception as exc:
            logger.warning(
                f"SkillMetricsService.record_feedback: failed "
                f"for skill={skill_id}, instance={instance_id}: "
                f"{exc}"
            )
            return False

        if record_id is None:
            return False

        # Counter bump only on positive feedback. None and False
        # are both excluded — False is a negative signal the
        # trigger engine consumes separately, not a counter
        # bump.
        if applied_bool is True:
            try:
                await asyncio.to_thread(
                    self.skill_repo.increment_counter,
                    skill_id,
                    "total_applied",
                    1,
                )
            except Exception as exc:
                logger.warning(
                    f"SkillMetricsService: failed to bump "
                    f"total_applied for skill={skill_id}: {exc}"
                )
        return True

    # --------------------------------------------------------
    # Stats
    # --------------------------------------------------------

    async def get_skill_stats(self, skill_id: str) -> dict[str, Any]:
        """Get aggregated stats for a skill.

        Delegates to the usage repository's aggregation query
        (``get_stats_filtered``) which provides ``avg_iterations``,
        ``avg_duration``, and ``applied_rate``. Also augments with
        ``consecutive_failures`` from the skill row counter, which
        is not available from the usage-record aggregation shape.

        ``0``/``0.0`` values are returned when no records match
        so callers can detect "new skill" via ``total == 0``.

        Args:
            skill_id: The skill to compute stats for.

        Returns:
            Dict with keys: ``total``, ``selected``, ``applied``,
            ``completions``, ``fallbacks``, ``avg_iterations``,
            ``avg_duration``, ``completion_rate``, ``applied_rate``,
            ``fallback_rate``, ``consecutive_failures``. All counts
            and rates default to ``0``/``0.0`` when no rows match.
        """

        def _read() -> dict[str, Any]:
            stats = self.usage_repo.get_stats_filtered(
                skill_id, ab_test_group=None
            )
            # Augment with skill-row counter not available from
            # usage-record aggregation.
            skill = self.skill_repo.get(skill_id)
            if skill is not None:
                stats["consecutive_failures"] = int(
                    getattr(skill, "consecutive_failures", 0) or 0
                )
            else:
                stats["consecutive_failures"] = 0
            return stats

        return await asyncio.to_thread(_read)

    # --------------------------------------------------------
    # A/B comparison stats
    # --------------------------------------------------------

    async def get_ab_comparison_stats(
        self,
        ab_test_group: str,
    ) -> dict[str, Any]:
        """Compute A/B comparison stats for a test group.

        Reads persistent state from ``skill_ab_tests`` and the
        per-record aggregation from ``skill_usage_records`` to
        compute per-variant composite scores plus the
        resolution decision:

        * ``ready_to_resolve`` — comparisons have hit
          ``ab_sample_size`` AND the absolute **composite**
          difference has hit ``ab_min_difference``.
        * ``needs_more_data`` — comparisons have hit
          ``ab_sample_size`` but the difference is still
          below threshold; the engine will bump
          ``extension_count`` (and after ``max_extensions``,
          force-resolve by raw completion_rate — handled by
          Phase 5).

        Completion rates are computed from
        ``skill_usage_records`` (``task_succeeded = True``
        count / total records per variant) — they reflect
        the actual observed outcomes, independent of the
        denormalized counters (which the metrics service
        bumps asynchronously). The **composite score**
        extends this with applied-rate, efficiency, fallback
        rate and speed against a global baseline, so a
        variant can win on speed/efficiency even when raw
        completion rates tie.

        The A/B-scoped stats use
        :meth:`SkillUsageRepository.get_stats_filtered` with
        the ``ab_test_group`` filter so superseded rows
        (worker reuse) and pre/post-test rows are excluded
        from the comparison.

        Args:
            ab_test_group: The shared UUID grouping old + new
                variants.

        Returns:
            Dict with keys: ``skill_id_a`` (old), ``skill_id_b``
            (new), ``completion_rate_a``, ``completion_rate_b``,
            ``applied_rate_a``, ``applied_rate_b``,
            ``fallback_rate_a``, ``fallback_rate_b``,
            ``avg_iterations_a``, ``avg_iterations_b``,
            ``avg_duration_a``, ``avg_duration_b``,
            ``composite_score_a``, ``composite_score_b``,
            ``difference`` (now composite-based),
            ``comparisons``, ``extension_count``, ``sample_size``,
            ``ready_to_resolve``, ``needs_more_data``.

            Returns zeros + ``None`` for the skill IDs when no
            test row exists for the group, so callers can
            safely dispatch on the result without first
            checking existence.
        """
        sample_size = int(
            getattr(self.config, "ab_sample_size", 10)
        )
        min_diff = float(
            getattr(self.config, "ab_min_difference", 0.15)
        )

        def _compute() -> dict[str, Any]:
            test = self.ab_test_repo.get_by_group(ab_test_group)
            if test is None:
                return {
                    "skill_id_a": None,
                    "skill_id_b": None,
                    "completion_rate_a": 0.0,
                    "completion_rate_b": 0.0,
                    "applied_rate_a": 0.0,
                    "applied_rate_b": 0.0,
                    "fallback_rate_a": 0.0,
                    "fallback_rate_b": 0.0,
                    "avg_iterations_a": 0.0,
                    "avg_iterations_b": 0.0,
                    "avg_duration_a": 0.0,
                    "avg_duration_b": 0.0,
                    "composite_score_a": 0.0,
                    "composite_score_b": 0.0,
                    "difference": 0.0,
                    "comparisons": 0,
                    "extension_count": 0,
                    "sample_size": sample_size,
                    "ready_to_resolve": False,
                    "needs_more_data": False,
                }

            # A/B-scoped stats — only records tagged with this
            # group (and not superseded) participate. The repo
            # method already returns rates + averages so we
            # don't need a separate ``_completion_rate_for``
            # round-trip.
            stats_a = self.usage_repo.get_stats_filtered(
                test.skill_id_old, ab_test_group=ab_test_group
            )
            stats_b = self.usage_repo.get_stats_filtered(
                test.skill_id_new, ab_test_group=ab_test_group
            )

            # Global baselines normalize efficiency + speed
            # across all skills so the composite score is
            # comparable run-over-run (a "fast" variant on a
            # slow day is still relatively fast).
            baselines = self._get_global_baselines()
            score_a = self._composite_score(stats_a, baselines)
            score_b = self._composite_score(stats_b, baselines)
            difference = abs(score_a - score_b)

            comparisons = int(
                getattr(test, "comparisons", 0) or 0
            )
            extension_count = int(
                getattr(test, "extension_count", 0) or 0
            )

            ready = (
                comparisons >= sample_size
                and difference >= min_diff
            )
            needs_more = (
                comparisons >= sample_size and difference < min_diff
            )

            return {
                "skill_id_a": test.skill_id_old,
                "skill_id_b": test.skill_id_new,
                "completion_rate_a": float(
                    stats_a.get("completion_rate", 0.0)
                ),
                "completion_rate_b": float(
                    stats_b.get("completion_rate", 0.0)
                ),
                "applied_rate_a": float(
                    stats_a.get("applied_rate", 0.0)
                ),
                "applied_rate_b": float(
                    stats_b.get("applied_rate", 0.0)
                ),
                "fallback_rate_a": float(
                    stats_a.get("fallback_rate", 0.0)
                ),
                "fallback_rate_b": float(
                    stats_b.get("fallback_rate", 0.0)
                ),
                "avg_iterations_a": float(
                    stats_a.get("avg_iterations", 0.0)
                ),
                "avg_iterations_b": float(
                    stats_b.get("avg_iterations", 0.0)
                ),
                "avg_duration_a": float(
                    stats_a.get("avg_duration", 0.0)
                ),
                "avg_duration_b": float(
                    stats_b.get("avg_duration", 0.0)
                ),
                "composite_score_a": score_a,
                "composite_score_b": score_b,
                "difference": difference,
                "comparisons": comparisons,
                "extension_count": extension_count,
                "sample_size": sample_size,
                "ready_to_resolve": ready,
                "needs_more_data": needs_more,
            }

        return await asyncio.to_thread(_compute)

    def _get_global_baselines(self) -> dict[str, float]:
        """Compute global average iterations + duration across all skills.

        Used as the normalization baseline for the
        :meth:`_composite_score` efficiency + speed components.
        A variant whose ``avg_iterations`` is below the global
        average gets a positive efficiency contribution
        (``baseline / actual > 1.0`` capped at ``1.0``); a
        variant above average gets a smaller contribution.

        Soft-fail: any DB error is swallowed and the baselines
        fall back to ``0.0`` so the A/B path still computes a
        composite score (using the neutral ``0.5`` default for
        efficiency/speed in :meth:`_composite_score`).

        Returns:
            Dict with ``avg_iterations`` and ``avg_duration``
            floats. Both default to ``0.0`` when no
            non-superseded records exist (fresh database).
        """
        try:
            baselines = self.usage_repo.get_global_averages()
            # Defensive: clamp to non-negative in case a stale
            # schema yields negative averages (shouldn't happen,
            # but the composite math assumes >= 0).
            return {
                "avg_iterations": max(
                    0.0, float(baselines.get("avg_iterations", 0.0))
                ),
                "avg_duration": max(
                    0.0, float(baselines.get("avg_duration", 0.0))
                ),
            }
        except Exception as exc:
            logger.warning(
                f"SkillMetricsService: get_global_averages "
                f"failed: {exc}"
            )
            return {"avg_iterations": 0.0, "avg_duration": 0.0}

    def _composite_score(
        self,
        stats: dict[str, Any],
        global_baselines: dict[str, float],
    ) -> float:
        """Compute the weighted 5-metric composite score.

        The composite blends five signals into a single number
        in ``[0.0, 1.0]`` so the A/B winner picker can compare
        two variants on more than raw completion rate:

        1. ``completion_rate`` — fraction of records that
           succeeded.
        2. ``applied_rate`` — fraction of records the agent
           actually consumed (vs. just had injected).
        3. ``efficiency_score`` — ``baseline_avg_iterations /
           actual_avg_iterations`` capped to ``[0.0, 1.0]``;
           ``0.5`` (neutral) when baseline or actual is ``<= 0``.
        4. ``low_fallback_rate`` — ``1.0 - fallback_rate``
           (higher is better).
        5. ``speed_score`` — ``baseline_avg_duration /
           actual_avg_duration`` capped to ``[0.0, 1.0]``;
           ``0.5`` (neutral) when baseline or actual is ``<= 0``.

        Weights come from :class:`SkillEvolutionConfig`
        (``ab_weight_completion`` / ``ab_weight_applied`` /
        ``ab_weight_efficiency`` / ``ab_weight_fallback`` /
        ``ab_weight_speed``) and default to the values used
        during the milestone config: ``0.35 / 0.20 / 0.20 /
        0.15 / 0.10``. ``getattr`` with defaults keeps the
        helper decoupled from the typed config — tests can
        pass any object with the right attributes (or none).

        Args:
            stats: A single skill's stats dict as returned by
                :meth:`SkillUsageRepository.get_stats_filtered`.
                Must contain at least ``completion_rate``,
                ``applied_rate``, ``fallback_rate``,
                ``avg_iterations``, ``avg_duration``.
            global_baselines: ``{"avg_iterations": float,
                "avg_duration": float}`` as returned by
                :meth:`_get_global_baselines`.

        Returns:
            Composite score in ``[0.0, 1.0]`` (weights are
            non-negative and the components are clamped /
            neutral-defaulted, so the result is bounded).
            Returns ``0.0`` for an empty ``stats`` dict
            (``total == 0``) — there's nothing to score.
        """
        # No data → no score. Returning 0.0 (rather than
        # blindly summing neutral 0.5s) avoids rewarding
        # untested variants when one side has records and the
        # other doesn't.
        total = int(stats.get("total", 0) or 0)
        if total == 0:
            return 0.0

        # Weights — getattr with defaults so the helper
        # tolerates a config stub missing the weight fields.
        # No ``or default`` fallback: ``0.0`` is a valid operator
        # override to disable a metric, and ``or`` would silently
        # swap it back to the default. ``float()`` then handles
        # int-valued configs (e.g. ``0``) the same way.
        w_completion = float(
            getattr(self.config, "ab_weight_completion", 0.35)
        )
        w_applied = float(
            getattr(self.config, "ab_weight_applied", 0.20)
        )
        w_efficiency = float(
            getattr(self.config, "ab_weight_efficiency", 0.20)
        )
        w_fallback = float(
            getattr(self.config, "ab_weight_fallback", 0.15)
        )
        w_speed = float(
            getattr(self.config, "ab_weight_speed", 0.10)
        )

        # Component 1 — completion rate (already in [0, 1]).
        completion_rate = float(
            stats.get("completion_rate", 0.0) or 0.0
        )
        # Component 2 — applied rate (already in [0, 1]).
        applied_rate = float(stats.get("applied_rate", 0.0) or 0.0)
        # Component 4 — low fallback rate (1 - rate).
        fallback_rate = float(
            stats.get("fallback_rate", 0.0) or 0.0
        )
        low_fallback_rate = max(0.0, min(1.0, 1.0 - fallback_rate))

        # Component 3 — efficiency vs global baseline. A
        # variant using FEWER iterations than average scores
        # > 1.0 raw (capped at 1.0). When either side is
        # non-positive, we can't normalize → use 0.5 (neutral)
        # so the variant isn't unfairly penalized or rewarded.
        baseline_avg_it = float(
            global_baselines.get("avg_iterations", 0.0) or 0.0
        )
        actual_avg_it = float(
            stats.get("avg_iterations", 0.0) or 0.0
        )
        if baseline_avg_it <= 0.0 or actual_avg_it <= 0.0:
            efficiency_score = 0.5
        else:
            efficiency_score = max(
                0.0, min(1.0, baseline_avg_it / actual_avg_it)
            )

        # Component 5 — speed vs global baseline. Same shape
        # as efficiency but on duration.
        baseline_avg_dur = float(
            global_baselines.get("avg_duration", 0.0) or 0.0
        )
        actual_avg_dur = float(
            stats.get("avg_duration", 0.0) or 0.0
        )
        if baseline_avg_dur <= 0.0 or actual_avg_dur <= 0.0:
            speed_score = 0.5
        else:
            speed_score = max(
                0.0, min(1.0, baseline_avg_dur / actual_avg_dur)
            )

        score = (
            completion_rate * w_completion
            + applied_rate * w_applied
            + efficiency_score * w_efficiency
            + low_fallback_rate * w_fallback
            + speed_score * w_speed
        )
        # Final clamp — a misconfigured config (e.g. weights
        # summing to 1.1) could push the score above 1.0.
        return max(0.0, min(1.0, score))

    # --------------------------------------------------------
    # Superseded records (worker reuse / orphan sweep)
    # --------------------------------------------------------

    def finalize_superseded_skills(
        self,
        instance_id: str,
        agent_id: str,
        project_id: str,
        dropped_skill_ids: list[str],
    ) -> int:
        """Record ``SUPERSEDED`` usage rows for dropped skills.

        Called when a worker is reused with a different skill via
        a ``<meta>`` tag — the old skill's scope is REPLACED,
        not appended. Before the replacement takes effect, each
        dropped skill gets a usage record stamped with
        ``superseded=True`` so the standard completion-rate
        aggregation excludes them but the audit trail is
        preserved.

        For each dropped skill ID the method writes a
        :class:`SkillUsageRecord` with ``selected=True`` and all
        other signal flags zeroed (``applied=False``,
        ``task_succeeded=False``, ``iterations=0``,
        ``duration_seconds=0``, ``fallback=False``,
        ``superseded=True``) — the skill was selected (it was
        about to be dropped), but no outcome was ever
        observed.

        ``SUPERSEDED`` records are **neutral markers** and do NOT
        bump any denormalized counter on the skill row. The
        trigger engine reads ``total_selections`` without filtering
        on ``superseded``, so inflating it here would trigger
        spurious evolution evaluations. The usage record row
        itself (with ``superseded=True``) is sufficient for the
        audit trail; the completion-rate aggregation already
        excludes superseded rows.

        Soft-fail: any per-skill DB error is logged and
        swallowed so one bad skill doesn't block the others.
        The whole method is also wrapped in a try/except —
        metrics code must never raise out of the caller.

        Args:
            instance_id: The instance whose worker was rebound.
            agent_id: The agent that owned the worker.
            project_id: Project scope (``None`` is coerced to
                ``""`` to satisfy the NOT NULL constraint on
                ``SkillUsageRecord.project_id``).
            dropped_skill_ids: Skill IDs being replaced. Empty
                list short-circuits to ``0``.

        Returns:
            Number of ``SUPERSEDED`` usage records actually
            inserted. ``0`` when ``dropped_skill_ids`` is empty
            or every insert failed.
        """
        if not dropped_skill_ids:
            return 0

        # SkillUsageRecord.project_id is NOT NULL; coerce
        # ``None`` to ``""`` so the insert doesn't blow up the
        # metrics path on older callers.
        usage_project_id = project_id or ""

        try:
            inserted = 0
            for skill_id in dropped_skill_ids:
                try:
                    self.usage_repo.create(
                        skill_id=skill_id,
                        project_id=usage_project_id,
                        instance_id=instance_id,
                        agent_id=agent_id,
                        selected=True,
                        applied=False,
                        task_succeeded=False,
                        iterations=0,
                        duration_seconds=0,
                        fallback=False,
                        superseded=True,
                    )
                    # SUPERSEDED rows are neutral markers — no
                    # counter bump. Inflating ``total_selections``
                    # would trigger false-positive evolution
                    # evaluations (the trigger engine does not
                    # filter on ``superseded``).
                    inserted += 1
                except Exception as exc:
                    logger.warning(
                        f"[SkillMetrics] Failed to finalize "
                        f"SUPERSEDED record for skill "
                        f"{skill_id[:8]}...: {exc}"
                    )

            if inserted > 0:
                logger.info(
                    f"[SkillMetrics] Finalized {inserted} "
                    f"SUPERSEDED record(s) for instance "
                    f"{instance_id[:8]}..."
                )

            return inserted
        except Exception as exc:
            logger.warning(
                f"[SkillMetrics] finalize_superseded_skills "
                f"outer error: {exc}"
            )
            return 0

    async def sweep_orphaned_skill_records(
        self, max_age_hours: int = 24
    ) -> int:
        """Sweep stale pending usage records that escaped finalization.

        Periodic sweep run from the scheduler: find
        :class:`SkillUsageRecord` rows that still look "pending"
        (no feedback, no completion, no iterations, not already
        superseded) and are older than ``max_age_hours``, then
        flip them to ``superseded=True`` so they stop skewing
        the completion-rate aggregation.

        The threshold is a UTC ISO-8601 string. ``created_at``
        is stored as ISO-8601 text, so lexicographic comparison
        is correct as long as both sides use a UTC
        tz-aware ISO format (which ``datetime.now(timezone.utc)
        .isoformat()`` produces). Bound via SQLModel/SQLAlchemy
        parameters — never interpolated into the SQL string.

        Soft-fail: any DB error is logged and swallowed so a
        broken sweep never blocks the rest of the metrics
        pipeline.

        Args:
            max_age_hours: Age in hours beyond which a pending
                record is considered orphaned (default ``24``).

        Returns:
            Number of records flipped to ``superseded=True``.
            ``0`` when the sweep ran cleanly and found nothing
            to clean up, OR when the sweep itself errored.
        """
        try:
            threshold = datetime.now(timezone.utc) - timedelta(
                hours=max_age_hours
            )
            threshold_iso = threshold.isoformat()

            def _do_sweep() -> int:
                stale = self.usage_repo.find_stale_pending(
                    threshold_iso
                )
                swept = 0
                for record in stale:
                    updated = self.usage_repo.update_superseded(
                        record.id
                    )
                    if updated is not None:
                        swept += 1
                return swept

            swept = await asyncio.to_thread(_do_sweep)

            if swept > 0:
                logger.info(
                    f"[SkillMetrics] Orphan sweep: finalized "
                    f"{swept} stale record(s)"
                )

            return swept
        except Exception as exc:
            logger.warning(
                f"[SkillMetrics] Orphan sweep failed: {exc}"
            )
            return 0
