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
from datetime import datetime, timezone
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
    """

    def __init__(
        self,
        usage_repo: Any,
        skill_repo: Any,
        trigger_repo: Any,
        ab_test_repo: Any,
        config: Any,
        instance_repo: Any = None,
    ) -> None:
        """Store repositories and config.

        Args:
            usage_repo: :class:`SkillUsageRepository`.
            skill_repo: :class:`SkillRepository`.
            trigger_repo: :class:`SkillTriggerRepository`.
            ab_test_repo: :class:`SkillABTestRepository`.
            config: :class:`~daemon.config.SkillEvolutionConfig`
                (provides ``ab_sample_size`` / ``ab_min_difference``
                / ``max_extensions``).
            instance_repo: Optional instance repository. When
                ``None``, ``record_task_completion`` cannot read
                injected-skill metadata and silently no-ops for
                that step; all other methods work normally.
        """
        self.usage_repo = usage_repo
        self.skill_repo = skill_repo
        self.trigger_repo = trigger_repo
        self.ab_test_repo = ab_test_repo
        self.config = config
        self.instance_repo = instance_repo

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

        return inserted

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