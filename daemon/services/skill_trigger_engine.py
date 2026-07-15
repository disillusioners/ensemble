"""Skill trigger engine — Tier 1 rule evaluator.

Phase 4 of the Skill Evolution System. Pure rule evaluation,
no LLM calls (Tier 1). Walks every enabled ``SkillTrigger``
row in the database, applies the type-specific condition
against each candidate skill's metrics, and returns the list
of flagged skills for downstream analysis (Tier 2 / Phase 5).

Condition types
---------------

The engine knows five built-in ``condition_type`` values,
matching the ``DEFAULT_TRIGGERS`` catalogue from
:mod:`daemon.services.skill_trigger_seed`:

* ``low_completion_rate`` — completion_rate < threshold
  (default ``0.3``) AND total_selections >= min_selections
  (default ``5``).
* ``high_fallback_rate`` — fallback_rate > threshold
  (default ``0.5``) AND total_selections >= min_selections
  (default ``5``).
* ``consecutive_failures`` — skill.consecutive_failures >=
  threshold (default ``3``).
* ``task_count_scan`` — skill.total_selections >=
  threshold (default ``20``).
* ``periodic_scan`` — last_used_at is older than
  ``interval_days`` (default ``7``); skipped when the skill
  has never been used (``last_used_at IS NULL``).

Unknown ``condition_type`` values are skipped with a
warning — the engine never raises on an unknown rule.

Resolution flow
---------------

Each flagged skill entry has a ``trigger_action`` that the
Phase 5 evolution pipeline consumes:

* ``"analyze"`` → enqueue a ``skill_analysis`` job (Tier 2).
* ``"evolve_fix"`` → enqueue a ``skill_evolution`` job
  (Tier 2, FIX-type evolution).
* any other value → forwarded verbatim; the engine treats
  unknown actions as opaque tokens.

The engine is intentionally side-effect free — it only reads
the DB (via :class:`SkillTriggerRepository`,
:class:`SkillRepository`, and the metrics service) and
returns a result list. Job enqueue is the caller's
responsibility.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Optional

logger = logging.getLogger(__name__)


# ============================================================
# SkillTriggerEngine
# ============================================================


class SkillTriggerEngine:
    """Tier 1 rule-based trigger evaluator.

    Iterates enabled :class:`SkillTrigger` rows and applies
    each rule to every candidate skill, returning the list of
    flagged skills. The metrics service provides the
    aggregate stats; the engine never queries usage records
    directly.

    Attributes:
        trigger_repo: Sync :class:`SkillTriggerRepository` for
            the ``skill_triggers`` table.
        metrics_service: :class:`~daemon.services.skill_metrics_service.SkillMetricsService`
            used to compute per-skill stats for the
            condition check and the result payload.
    """

    def __init__(
        self,
        trigger_repo: Any,
        metrics_service: Any,
    ) -> None:
        """Store the trigger repo and metrics service.

        Args:
            trigger_repo: :class:`SkillTriggerRepository`.
            metrics_service: :class:`SkillMetricsService`.
        """
        self.trigger_repo = trigger_repo
        self.metrics_service = metrics_service

    # --------------------------------------------------------
    # Public API
    # --------------------------------------------------------

    async def evaluate_all(
        self,
        project_id: Optional[str] = None,
    ) -> list[dict[str, Any]]:
        """Evaluate every enabled trigger for a project.

        Iterates enabled triggers (the repository's
        ``list(project_id=None)`` always returns the GLOBAL
        set; callers that want project-scoped filtering need
        to layer a custom check at the caller level). For
        each trigger, walks the candidate skills (currently
        every active skill in the system — see
        :meth:`_get_skills_for_trigger`) and applies the
        condition.

        Flags are returned in a stable order: by trigger
        name, then by skill name. The function does not
        dedupe across triggers — the same skill may appear
        twice if two triggers both fire (that's intentional;
        the downstream pipeline may want to react to both).

        Args:
            project_id: Project scope, or ``None`` for the
                global set. The repository implementation
                filters by ``project_id IS NULL`` when this
                is ``None``, so callers wanting a project +
                global union must do it themselves.

        Returns:
            List of flagged-skill dicts with keys:
            ``skill_id``, ``skill_name``, ``trigger_name``,
            ``trigger_action``, ``reason``, ``stats``.
        """
        triggers = await asyncio.to_thread(
            self.trigger_repo.list,
            project_id=project_id,
            enabled_only=True,
        )

        flagged: list[dict[str, Any]] = []
        for trigger in triggers:
            skills = await self._get_skills_for_trigger(
                trigger, project_id
            )
            for skill in skills:
                try:
                    if not self._evaluate_condition(trigger, skill):
                        continue
                except Exception as exc:
                    # Defensive: a misconfigured trigger should
                    # never break the whole scan.
                    logger.warning(
                        f"SkillTriggerEngine: condition eval "
                        f"failed for skill {getattr(skill, 'id', '?')}, "
                        f"trigger {getattr(trigger, 'name', '?')}: "
                        f"{exc}"
                    )
                    continue
                stats = await self.metrics_service.get_skill_stats(
                    skill.id
                )
                flagged.append(
                    {
                        "skill_id": skill.id,
                        "skill_name": getattr(skill, "name", ""),
                        "trigger_name": trigger.name,
                        "trigger_action": trigger.action,
                        "reason": self._build_reason(
                            trigger, skill, stats
                        ),
                        "stats": stats,
                    }
                )

        # Stable ordering — keep tests deterministic.
        flagged.sort(
            key=lambda item: (
                item["trigger_name"],
                item["skill_name"],
            )
        )
        return flagged

    # --------------------------------------------------------
    # Candidate skill resolution
    # --------------------------------------------------------

    async def _get_skills_for_trigger(
        self,
        trigger: Any,
        project_id: Optional[str],
    ) -> list[Any]:
        """Return the candidate skills for a trigger.

        Current implementation: every active skill in the
        system. The trigger engine is intentionally
        trigger-agnostic — the rule body itself decides
        whether a skill matches (via min_selections,
        consecutive_failures, etc.), so we don't pre-filter
        by ``project_id`` here.

        Routes through :meth:`SkillRepository.list_all_active`
        so the result spans project-specific skills AND
        globals (``project_id IS NULL``) — the trigger
        engine is system-wide, not project-scoped.

        If the system grows to thousands of skills and the
        one-shot list becomes a bottleneck, add paged
        iteration here.

        Args:
            trigger: :class:`SkillTrigger` (unused at this
                layer; kept on the signature for the future
                per-trigger scoping case).
            project_id: Project scope (unused at this
                layer; same rationale).

        Returns:
            List of :class:`Skill` instances (active only,
            across all projects).
        """
        del trigger, project_id  # Reserved for future scoping.
        from daemon.repositories.skill.repository import SkillRepository

        # The metrics service holds the skill repo reference;
        # we route through it so the engine stays decoupled
        # from the repository factory.
        skill_repo: SkillRepository = self.metrics_service.skill_repo

        def _list_skills() -> list[Any]:
            return skill_repo.list_all_active(limit=1000, offset=0)

        return await asyncio.to_thread(_list_skills)

    # --------------------------------------------------------
    # Condition evaluation
    # --------------------------------------------------------

    def _evaluate_condition(
        self,
        trigger: Any,
        skill: Any,
    ) -> bool:
        """Evaluate one trigger against one skill.

        Sync — no DB access at this layer. The skill row
        already carries the counters (``total_selections``,
        ``consecutive_failures``, ``last_used_at``) needed
        for the condition check. Rate-based checks
        (``low_completion_rate`` / ``high_fallback_rate``)
        additionally honor a ``min_selections`` floor so a
        single early data point doesn't flap.

        ``trigger_repo.list()`` always returns a fresh row,
        so ``trigger.condition_json`` is current. The
        ``skill`` argument, however, may be a stale
        snapshot (callers from upstream that mutated
        counters between fetch and condition check) — the
        engine re-fetches the fresh row from
        ``skill_repo.get(skill.id)`` before reading counter
        columns so concurrent bumps are honored.

        Args:
            trigger: :class:`SkillTrigger` row.
            skill: :class:`Skill` row. Only ``skill.id`` is
                used to re-fetch the current state.

        Returns:
            ``True`` if the trigger fires for this skill,
            ``False`` otherwise (including for unknown
            ``condition_type`` values).
        """
        # Re-fetch the skill from the DB so concurrent counter
        # bumps between list() and evaluate_condition() are
        # honored. Cheap (one indexed point lookup).
        skill_repo = self.metrics_service.skill_repo
        fresh_skill = skill_repo.get(getattr(skill, "id", ""))
        if fresh_skill is None:
            # Skill was deleted between list and eval — skip.
            return False
        skill = fresh_skill

        condition = (
            dict(trigger.condition_json)
            if getattr(trigger, "condition_json", None)
            else {}
        )
        ctype = getattr(trigger, "condition_type", "")

        if ctype == "low_completion_rate":
            return self._eval_low_completion_rate(skill, condition)
        elif ctype == "high_fallback_rate":
            return self._eval_high_fallback_rate(skill, condition)
        elif ctype == "consecutive_failures":
            return self._eval_consecutive_failures(
                skill, condition
            )
        elif ctype == "task_count_scan":
            return self._eval_task_count_scan(skill, condition)
        elif ctype == "periodic_scan":
            return self._eval_periodic_scan(skill, condition)

        logger.warning(
            f"SkillTriggerEngine: unknown condition_type "
            f"{ctype!r} on trigger {getattr(trigger, 'name', '?')}"
        )
        return False

    # --------------------------------------------------------
    # Built-in condition evaluators
    # --------------------------------------------------------

    @staticmethod
    def _min_selections_gate(
        skill: Any,
        condition: dict[str, Any],
    ) -> bool:
        """Return True iff ``skill`` has enough selections to evaluate.

        Used by rate-based triggers as a noise floor — without
        it, a skill with 1 selection and 1 failure has a 100%
        fallback rate but isn't actionable yet.

        Args:
            skill: :class:`Skill` row.
            condition: Trigger ``condition_json`` dict; reads
                ``min_selections`` (default ``5``).

        Returns:
            ``True`` iff the gate passes (selections meet the
            floor). When the gate fails the caller should
            return ``False`` immediately — the rate check is
            not meaningful.
        """
        floor = int(condition.get("min_selections", 5) or 5)
        selections = int(
            getattr(skill, "total_selections", 0) or 0
        )
        return selections >= floor

    @staticmethod
    def _eval_low_completion_rate(
        skill: Any,
        condition: dict[str, Any],
    ) -> bool:
        """Fire when ``completion_rate < threshold``.

        Uses the denormalized ``total_completions`` /
        ``total_selections`` columns — no join to usage
        records required. A skill with zero selections has
        ``completion_rate == 0`` by definition, so the
        ``min_selections`` gate is mandatory.

        Args:
            skill: :class:`Skill` row.
            condition: Trigger ``condition_json``.

        Returns:
            ``True`` iff the rate threshold is crossed AND
            the min-selections gate passes.
        """
        if not SkillTriggerEngine._min_selections_gate(
            skill, condition
        ):
            return False
        threshold = float(
            condition.get("threshold", 0.3) or 0.3
        )
        completions = int(
            getattr(skill, "total_completions", 0) or 0
        )
        selections = int(
            getattr(skill, "total_selections", 0) or 0
        )
        rate = completions / selections if selections else 0.0
        return rate < threshold

    @staticmethod
    def _eval_high_fallback_rate(
        skill: Any,
        condition: dict[str, Any],
    ) -> bool:
        """Fire when ``fallback_rate > threshold``.

        Same noise-floor as ``low_completion_rate``.

        Args:
            skill: :class:`Skill` row.
            condition: Trigger ``condition_json``.

        Returns:
            ``True`` iff the rate threshold is crossed AND
            the min-selections gate passes.
        """
        if not SkillTriggerEngine._min_selections_gate(
            skill, condition
        ):
            return False
        threshold = float(
            condition.get("threshold", 0.5) or 0.5
        )
        fallbacks = int(
            getattr(skill, "total_fallbacks", 0) or 0
        )
        selections = int(
            getattr(skill, "total_selections", 0) or 0
        )
        rate = fallbacks / selections if selections else 0.0
        return rate > threshold

    @staticmethod
    def _eval_consecutive_failures(
        skill: Any,
        condition: dict[str, Any],
    ) -> bool:
        """Fire when ``skill.consecutive_failures >= threshold``.

        No min-selections floor — a streak of failures is
        actionable from failure #1.

        Args:
            skill: :class:`Skill` row.
            condition: Trigger ``condition_json``.

        Returns:
            ``True`` iff the streak meets the threshold.
        """
        threshold = int(
            condition.get("threshold", 3) or 3
        )
        failures = int(
            getattr(skill, "consecutive_failures", 0) or 0
        )
        return failures >= threshold

    @staticmethod
    def _eval_task_count_scan(
        skill: Any,
        condition: dict[str, Any],
    ) -> bool:
        """Fire when ``skill.total_selections >= threshold``.

        Periodic health check at N selections — independent
        of outcome, catches "looks fine but never inspected"
        skills.

        Args:
            skill: :class:`Skill` row.
            condition: Trigger ``condition_json``.

        Returns:
            ``True`` iff the selection count meets the
            threshold.
        """
        threshold = int(
            condition.get("threshold", 20) or 20
        )
        selections = int(
            getattr(skill, "total_selections", 0) or 0
        )
        return selections >= threshold

    @staticmethod
    def _eval_periodic_scan(
        skill: Any,
        condition: dict[str, Any],
    ) -> bool:
        """Fire when the skill hasn't been used in N days.

        Uses ``last_used_at`` as a proxy for "last activity".
        A skill that has never been used
        (``last_used_at IS NULL``) is skipped — there's no
        signal to act on.

        Args:
            skill: :class:`Skill` row.
            condition: Trigger ``condition_json``.

        Returns:
            ``True`` iff ``last_used_at`` is older than
            ``interval_days`` (default ``7``). ``False``
            when ``last_used_at`` is ``None`` (never used)
            or recent.
        """
        interval_days = int(
            condition.get("interval_days", 7) or 7
        )
        last_used_raw = getattr(skill, "last_used_at", None)
        if not last_used_raw:
            return False
        try:
            last_used = datetime.fromisoformat(
                str(last_used_raw).replace("Z", "+00:00")
            )
        except (ValueError, TypeError):
            logger.warning(
                f"SkillTriggerEngine: could not parse "
                f"last_used_at {last_used_raw!r} on skill "
                f"{getattr(skill, 'id', '?')}"
            )
            return False
        now = datetime.now(timezone.utc)
        if last_used.tzinfo is None:
            last_used = last_used.replace(tzinfo=timezone.utc)
        age = now - last_used
        return age.days >= interval_days

    # --------------------------------------------------------
    # Reason formatting
    # --------------------------------------------------------

    @staticmethod
    def _build_reason(
        trigger: Any,
        skill: Any,
        stats: dict[str, Any],
    ) -> str:
        """Render a human-readable reason for the flag.

        One-line summary returned as the ``reason`` field on
        the flagged-skill dict. Format::

            <condition_type>: <skill_name> — <human summary>

        The summary text differs per ``condition_type`` so the
        downstream pipeline (Phase 5) can show a useful
        message in its logs without re-deriving the values.

        Args:
            trigger: :class:`SkillTrigger`.
            skill: :class:`Skill`.
            stats: Output of :meth:`SkillMetricsService.get_skill_stats`
                for the skill.

        Returns:
            One-line reason string.
        """
        name = getattr(skill, "name", "<unknown>")
        ctype = getattr(trigger, "condition_type", "unknown")
        if ctype == "low_completion_rate":
            return (
                f"{ctype}: {name} — completion_rate="
                f"{stats['completion_rate']:.2f} < "
                f"{trigger.condition_json.get('threshold', 0.3)}"
            )
        if ctype == "high_fallback_rate":
            return (
                f"{ctype}: {name} — fallback_rate="
                f"{stats['fallback_rate']:.2f} > "
                f"{trigger.condition_json.get('threshold', 0.5)}"
            )
        if ctype == "consecutive_failures":
            return (
                f"{ctype}: {name} — "
                f"consecutive_failures="
                f"{getattr(skill, 'consecutive_failures', 0)} >= "
                f"{trigger.condition_json.get('threshold', 3)}"
            )
        if ctype == "task_count_scan":
            return (
                f"{ctype}: {name} — total_selections="
                f"{getattr(skill, 'total_selections', 0)} >= "
                f"{trigger.condition_json.get('threshold', 20)}"
            )
        if ctype == "periodic_scan":
            return (
                f"{ctype}: {name} — last_used_at older than "
                f"{trigger.condition_json.get('interval_days', 7)} "
                f"days"
            )
        return f"{ctype}: {name} — unknown condition"