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
``skill_ab_tests`` and computes completion rates from
``skill_usage_records``. A test is ``ready_to_resolve`` iff
``comparisons >= ab_sample_size`` AND
``abs(diff) >= ab_min_difference``; otherwise it's flagged
``needs_more_data`` (the engine bumps ``extension_count``
once max_extensions is exhausted — handled by Phase 5).
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
    ) -> int:
        """Record one (skill, instance) pair end-to-end.

        Sync helper called from :meth:`record_task_completion`
        via ``asyncio.to_thread`` (one block per skill). Reads
        the current ``consecutive_failures`` from the skill
        row, writes a :class:`SkillUsageRecord`, then bumps
        the denormalized counters.

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

        Returns:
            ``1`` if the usage record was inserted, ``0`` if
            the skill row was missing (no-op).
        """

        def _do_record() -> int:
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
            fallback = (current_failures > 0) and (not task_succeeded)

            self.usage_repo.create(
                skill_id=skill_id,
                project_id=project_id,
                instance_id=instance_id,
                agent_id=agent_id,
                selected=True,
                applied=False,  # Set later by skill_feedback tool
                task_succeeded=task_succeeded,
                iterations=iterations,
                duration_seconds=duration_seconds,
                fallback=fallback,
            )

            # Denormalized counters — atomic via raw SQL.
            self.skill_repo.increment_counter(
                skill_id, "total_selections", amount=1
            )
            if task_succeeded:
                self.skill_repo.increment_counter(
                    skill_id, "total_completions", amount=1
                )
            if fallback:
                self.skill_repo.increment_counter(
                    skill_id, "total_fallbacks", amount=1
                )

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
    ) -> bool:
        """Stamp feedback onto the most recent usage record.

        Backend for the ``skill_feedback`` tool (Phase 2 stub,
        implemented here). Locates the latest
        :class:`SkillUsageRecord` for ``(skill_id, instance_id)``
        and stamps ``feedback_applied`` + ``feedback_note``.
        When ``applied`` is explicitly ``True``, the skill row's
        ``total_applied`` counter is also bumped.

        Steps:

        1. Find the latest :class:`SkillUsageRecord` for the
           pair (most recent by ``created_at``).
        2. ``update_feedback(record_id, applied, note)`` —
           ``applied=None`` is treated as
           "feedback recorded but outcome unknown" and skips
           the counter bump.
        3. If ``applied is True``: increment
           ``total_applied`` on the skill row.

        Args:
            skill_id: Skill being given feedback on.
            instance_id: The instance that produced the usage
                event.
            agent_id: The agent that produced the feedback
                (unused at the row layer but kept on the
                signature for Phase 5 audit hooks).
            project_id: Project scope (unused at the row
                layer; kept on signature for symmetry with
                ``record_task_completion``).
            applied: True if the skill was actually applied,
                False if recorded-but-not-applied, None when
                the agent is unsure.
            note: Free-form feedback note. Empty string when
                no note.

        Returns:
            ``True`` if a usage record was found and updated;
            ``False`` otherwise (no record to attach feedback
            to).
        """
        del agent_id, project_id  # Reserved for Phase 5 audit.
        applied_bool: Optional[bool] = (
            bool(applied) if applied is not None else None
        )

        def _do_feedback() -> Optional[str]:
            record = self.usage_repo.get_latest_for_skill_instance(
                skill_id=skill_id, instance_id=instance_id
            )
            if record is None:
                logger.warning(
                    f"SkillMetricsService.record_feedback: no "
                    f"usage record found for skill={skill_id}, "
                    f"instance={instance_id}"
                )
                return None
            self.usage_repo.update_feedback(
                record_id=record.id,
                applied=bool(applied_bool) if applied_bool is not None else False,
                note=note or "",
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
        """Compute aggregate stats for a skill.

        Reads the denormalized counter columns directly from
        the ``skills`` row (cheap — one indexed point lookup)
        and computes the derived rates. Used by the trigger
        engine to decide which skills to flag.

        Rates are computed as ``counter / total_selections``
        so they cover only the selected-skills universe (the
        denominator matches the Phase 1 ``SkillUsageRepository.get_stats``
        convention). ``0.0`` is returned when
        ``total_selections == 0`` to avoid division-by-zero.

        Args:
            skill_id: The skill to compute stats for.

        Returns:
            Dict with keys: ``total_selections``, ``total_applied``,
            ``total_completions``, ``total_fallbacks``,
            ``completion_rate``, ``fallback_rate``,
            ``applied_rate``, ``consecutive_failures``.
            ``total_selections == 0`` yields all-zero rates and
            counters; the caller can detect "new skill" via
            ``total_selections == 0``.
        """

        def _read() -> dict[str, Any]:
            skill = self.skill_repo.get(skill_id)
            if skill is None:
                # Return a zeroed dict — the trigger engine
                # can use ``total_selections == 0`` to skip a
                # missing skill without special-casing.
                return {
                    "total_selections": 0,
                    "total_applied": 0,
                    "total_completions": 0,
                    "total_fallbacks": 0,
                    "completion_rate": 0.0,
                    "fallback_rate": 0.0,
                    "applied_rate": 0.0,
                    "consecutive_failures": 0,
                }
            selections = int(
                getattr(skill, "total_selections", 0) or 0
            )
            completions = int(
                getattr(skill, "total_completions", 0) or 0
            )
            fallbacks = int(
                getattr(skill, "total_fallbacks", 0) or 0
            )
            applied = int(
                getattr(skill, "total_applied", 0) or 0
            )
            failures = int(
                getattr(skill, "consecutive_failures", 0) or 0
            )
            if selections == 0:
                completion_rate = 0.0
                fallback_rate = 0.0
                applied_rate = 0.0
            else:
                completion_rate = completions / selections
                fallback_rate = fallbacks / selections
                applied_rate = applied / selections
            return {
                "total_selections": selections,
                "total_applied": applied,
                "total_completions": completions,
                "total_fallbacks": fallbacks,
                "completion_rate": completion_rate,
                "fallback_rate": fallback_rate,
                "applied_rate": applied_rate,
                "consecutive_failures": failures,
            }

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
        completion-rate columns on ``skill_usage_records`` to
        compute per-variant rates plus the resolution
        decision:

        * ``ready_to_resolve`` — comparisons have hit
          ``ab_sample_size`` AND the absolute difference has
          hit ``ab_min_difference``.
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
        bumps asynchronously).

        Args:
            ab_test_group: The shared UUID grouping old + new
                variants.

        Returns:
            Dict with keys: ``skill_id_a`` (old), ``skill_id_b``
            (new), ``completion_rate_a``, ``completion_rate_b``,
            ``difference``, ``comparisons``, ``extension_count``,
            ``ready_to_resolve``, ``needs_more_data``.

            Returns zeros + ``None`` for the skill IDs when no
            test row exists for the group, so callers can
            safely dispatch on the result without first
            checking existence.
        """
        sample_size = int(
            getattr(self.config, "ab_sample_size", 10) or 10
        )
        min_diff = float(
            getattr(self.config, "ab_min_difference", 0.15) or 0.15
        )

        def _compute() -> dict[str, Any]:
            test = self.ab_test_repo.get_by_group(ab_test_group)
            if test is None:
                return {
                    "skill_id_a": None,
                    "skill_id_b": None,
                    "completion_rate_a": 0.0,
                    "completion_rate_b": 0.0,
                    "difference": 0.0,
                    "comparisons": 0,
                    "extension_count": 0,
                    "ready_to_resolve": False,
                    "needs_more_data": False,
                }

            rate_a = self._completion_rate_for(test.skill_id_old)
            rate_b = self._completion_rate_for(test.skill_id_new)
            difference = abs(rate_a - rate_b)

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
                "completion_rate_a": rate_a,
                "completion_rate_b": rate_b,
                "difference": difference,
                "comparisons": comparisons,
                "extension_count": extension_count,
                "ready_to_resolve": ready,
                "needs_more_data": needs_more,
            }

        return await asyncio.to_thread(_compute)

    def _completion_rate_for(self, skill_id: str) -> float:
        """Compute completion rate from ``skill_usage_records``.

        Sync helper — wraps the lightweight aggregation that
        :meth:`SkillUsageRepository.get_stats` already
        performs. Returns ``0.0`` when the skill has no
        records yet (consistent with the repo convention).

        Args:
            skill_id: The skill to compute the rate for.

        Returns:
            ``completions / total`` in ``[0.0, 1.0]``. ``0.0``
            when no records exist.
        """
        stats = self.usage_repo.get_stats(skill_id)
        total = int(stats.get("total", 0) or 0)
        if total == 0:
            return 0.0
        completions = int(stats.get("completions", 0) or 0)
        return completions / total

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
        observed. ``total_selections`` on the skill row is
        bumped alongside the insert.

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
                    self.skill_repo.increment_counter(
                        skill_id, "total_selections", amount=1
                    )
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