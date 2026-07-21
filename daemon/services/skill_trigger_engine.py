"""Skill trigger engine — Tier 1 rule evaluator.

Phase 4 of the Skill Evolution System. Pure rule evaluation,
no LLM calls (Tier 1). Walks every enabled ``SkillTrigger``
row in the database, applies the type-specific condition
against each candidate skill's metrics, and returns the list
of flagged skills for downstream analysis (Tier 2 / Phase 5).

Condition types
---------------

The engine knows six built-in ``condition_type`` values,
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
* ``low_usefulness`` — avg ``feedback_usefulness`` (1-10
  agent-judged quality score) over the skill's non-null
  scored usage records is below ``threshold`` (default
  ``4.0``) AND at least ``min_samples`` (default ``5``)
  records carry a score. Unlike the other five, this
  condition reads ``skill_usage_records`` (the agent-judged
  score is not denormalized onto the ``Skill`` row), so it
  needs the ``SkillUsageRepository`` — see ``__init__``.

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
:class:`SkillRepository`, :class:`SkillUsageRepository`, and
the metrics service) and returns a result list. Job enqueue
is the caller's responsibility.
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

    The one exception is the ``low_usefulness`` condition —
    agent-judged quality scores live on ``skill_usage_records``
    (not denormalized onto the ``Skill`` row), so this engine
    holds a direct reference to the
    :class:`~daemon.repositories.skill.repository.SkillUsageRepository`.
    The other five conditions never touch ``usage_repo``.

    Attributes:
        trigger_repo: Sync :class:`SkillTriggerRepository` for
            the ``skill_triggers`` table.
        metrics_service: :class:`~daemon.services.skill_metrics_service.SkillMetricsService`
            used to compute per-skill stats for the
            condition check and the result payload.
        usage_repo: Sync :class:`SkillUsageRepository` used by
            the ``low_usefulness`` condition to aggregate
            ``feedback_usefulness`` scores. Falls back to
            ``metrics_service.usage_repo`` when not provided
            explicitly so older call sites keep working.
    """

    def __init__(
        self,
        trigger_repo: Any,
        metrics_service: Any,
        usage_repo: Any = None,
    ) -> None:
        """Store the trigger repo, metrics service, and usage repo.

        Args:
            trigger_repo: :class:`SkillTriggerRepository`.
            metrics_service: :class:`SkillMetricsService`.
            usage_repo: Optional
                :class:`~daemon.repositories.skill.repository.SkillUsageRepository`.
                When ``None`` (default), the engine falls back
                to ``metrics_service.usage_repo`` so existing
                call sites stay compatible. Pass explicitly when
                you want the trigger engine decoupled from the
                metrics service's wiring (e.g. tests with a
                stub metrics service).
        """
        self.trigger_repo = trigger_repo
        self.metrics_service = metrics_service
        # Fall back to the metrics service's own handle so older
        # call sites (and tests that only wire metrics_service)
        # keep working. The ``low_usefulness`` condition is the
        # only consumer — every other condition ignores this.
        self.usage_repo = (
            usage_repo
            if usage_repo is not None
            else getattr(metrics_service, "usage_repo", None)
        )

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
                    # _evaluate_condition is async because the
                    # ``low_usefulness`` branch reads the usage
                    # table via asyncio.to_thread — see its
                    # docstring.
                    if not await self._evaluate_condition(trigger, skill):
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
                        "reason": await self._build_reason(
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

    async def _evaluate_condition(
        self,
        trigger: Any,
        skill: Any,
    ) -> bool:
        """Evaluate one trigger against one skill.

        Async — even though five of the six built-in conditions
        are pure-sync (they read denormalized columns on the
        ``Skill`` row), the sixth — ``low_usefulness`` — needs
        a DB round-trip into ``skill_usage_records``, which is
        a synchronous repository. That call is hopped into a
        worker thread via :func:`asyncio.to_thread` so the
        event loop never blocks during a metric scan. Making
        this method ``async`` lets the dispatcher ``await`` the
        ``low_usefulness`` branch without changing the call
        shape for the other five.

        The skill row already carries the counters
        (``total_selections``, ``consecutive_failures``,
        ``last_used_at``) needed for the condition check.
        Rate-based checks (``low_completion_rate`` /
        ``high_fallback_rate``) additionally honor a
        ``min_selections`` floor so a single early data point
        doesn't flap.

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
        # honored. Cheap (one indexed point lookup). Run via
        # ``asyncio.to_thread`` so the sync repo call doesn't
        # block the event loop.
        skill_repo = self.metrics_service.skill_repo
        fresh_skill = await asyncio.to_thread(
            skill_repo.get, getattr(skill, "id", "")
        )
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
        elif ctype == "low_usefulness":
            # Async because it awaits an asyncio.to_thread-wrapped
            # DB call — see ``_eval_low_usefulness`` docstring.
            return await self._eval_low_usefulness(skill, condition)

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

    async def _eval_low_usefulness(
        self,
        skill: Any,
        condition: dict[str, Any],
    ) -> bool:
        """Fire when avg ``feedback_usefulness`` < threshold.

        Distinct from the other five built-in conditions:
        ``feedback_usefulness`` is the agent-judged 1-10 quality
        score on each ``skill_usage_records`` row (collected via
        the ``skill_feedback`` tool), NOT a denormalized
        counter on the ``Skill`` row. We therefore need a
        direct DB read of the usage table.

        Defined as an **instance method** (not
        ``@staticmethod`` like the other five evaluators)
        because it must read ``self.usage_repo``. The other
        evaluators only touch denormalized columns on the
        ``Skill`` row, which the engine re-fetches upstream;
        this one queries the usage table.

        This method is ``async`` because the usage repository
        is synchronous — the call is hopped into a worker
        thread via :func:`asyncio.to_thread` so the event
        loop never blocks during a metric scan (every other
        condition fires synchronously off the in-memory
        ``Skill`` snapshot; this one is the sole DB round-trip
        in the dispatch chain).

        Noise floor via ``min_samples``: requires at least this
        many usage records with a non-null
        ``feedback_usefulness`` score before considering the
        average. Without the floor a single low-rated record
        would flap the trigger.

        Args:
            skill: :class:`Skill` row.
            condition: Trigger ``condition_json`` dict; reads
                ``threshold`` (default ``4.0``) and
                ``min_samples`` (default ``5``).

        Returns:
            ``True`` iff the avg score over the qualified
            records is below the threshold AND the sample
            count meets the noise floor. ``False`` when
            ``usage_repo`` is unavailable, when no scored
            records exist, or when the threshold isn't crossed.
        """
        usage_repo = getattr(self, "usage_repo", None)
        if usage_repo is None:
            logger.warning(
                "SkillTriggerEngine._eval_low_usefulness: "
                "usage_repo unavailable — condition cannot "
                f"evaluate for skill {getattr(skill, 'id', '?')}"
            )
            return False

        threshold = float(
            condition.get("threshold", 4.0) or 4.0
        )
        min_samples = int(
            condition.get("min_samples", 5) or 5
        )

        try:
            # Sync repo → async boundary. The DB call is a single
            # aggregate query over ``skill_usage_records``; running
            # it on the event loop would block other coroutines
            # during the scan.
            result = await asyncio.to_thread(
                usage_repo.get_avg_usefulness,
                getattr(skill, "id", ""),
                min_samples=min_samples,
            )
        except Exception as exc:
            # Defensive: a misconfigured repo or transient DB
            # issue must not break the whole scan — the
            # dispatcher already wraps this call, but the
            # inner guard keeps the contract clean.
            logger.warning(
                "SkillTriggerEngine._eval_low_usefulness: "
                f"get_avg_usefulness failed for skill "
                f"{getattr(skill, 'id', '?')}: {exc}"
            )
            return False

        if result is None:
            # No scored records yet — no signal to act on.
            return False
        avg, count = result
        if count < min_samples:
            return False
        return avg < threshold

    # --------------------------------------------------------
    # Reason formatting
    # --------------------------------------------------------

    async def _build_reason(
        self,
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

        Defined as an instance method (not ``@staticmethod``)
        because the ``low_usefulness`` branch re-runs
        ``usage_repo.get_avg_usefulness`` to embed the actual
        average and sample count in the reason text. The
        other branches ignore ``self``.

        ``async`` because that DB call is synchronous — it's
        hopped into a worker thread via :func:`asyncio.to_thread`
        so the event loop never blocks during a metric scan.
        The other five branches are pure string formatting
        and don't actually need ``async``, but making the
        whole method ``async`` keeps the call site uniform
        with :meth:`_evaluate_condition`.

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
        if ctype == "low_usefulness":
            # Re-run the aggregate to embed the actual avg +
            # sample count in the reason string. Trigger fires
            # are rare (analysis queue) so the extra round-trip
            # is acceptable; keeps each method self-contained
            # without sharing mutable state across the
            # evaluate_condition / get_skill_stats / reason
            # pipeline. The DB call is hopped to a worker
            # thread via ``asyncio.to_thread`` so the event
            # loop never blocks here.
            #
            # ``N`` in the reason text is the count of scored
            # records aggregated by ``get_avg_usefulness`` — NOT
            # a "last N usages" window. The aggregate scans all
            # non-superseded scored records (no time filter), so
            # the wording reflects that accurately.
            usage_repo = getattr(self, "usage_repo", None)
            threshold = float(
                trigger.condition_json.get("threshold", 4.0) or 4.0
            )
            min_samples = int(
                trigger.condition_json.get("min_samples", 5) or 5
            )
            avg_str = "n/a"
            count_str = "0"
            if usage_repo is not None:
                try:
                    result = await asyncio.to_thread(
                        usage_repo.get_avg_usefulness,
                        getattr(skill, "id", ""),
                        min_samples=min_samples,
                    )
                    if result is not None:
                        avg_val, count_val = result
                        avg_str = f"{avg_val:.1f}"
                        count_str = str(count_val)
                except Exception as exc:
                    logger.warning(
                        "SkillTriggerEngine._build_reason: "
                        f"get_avg_usefulness failed for skill "
                        f"{getattr(skill, 'id', '?')}: {exc}"
                    )
            return (
                f"{ctype}: {name} — avg usefulness "
                f"{avg_str}/10 over {count_str} scored usages "
                f"below threshold {threshold}"
            )
        return f"{ctype}: {name} — unknown condition"