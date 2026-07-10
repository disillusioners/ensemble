r"""Skill injection service — Phase 3 of the Skill Evolution System.

Given an incoming user message, this service decides which skills
should be prepended to the prompt as ``HumanMessage`` content so the
agent sees its relevant skills before its first response. The
service is the bridge between :class:`SkillSearchService` (which
finds which skills are relevant) and the message-processing
pipeline in :mod:`daemon.services.instance_messaging` (which
actually inserts them into the LangGraph state).

Pipeline
--------

1. **Relevance search.** Delegates to
   :meth:`SkillSearchService.search` with ``max_results`` from
   :attr:`SkillEvolutionConfig.max_inject_skills`. Search returns
   ``{"injected": [...], "low_match": [...]}``.
2. **A/B variant selection.** For each injected skill with
   ``ab_test_group`` set and ``status='ab_testing'``, fetch all
   variants via
   :meth:`SkillRepository.get_ab_variants`, filter to the active
   set (``status IN ('active', 'ab_testing')``), and pick one
   **deterministically** via::

       hash_val = int(md5(f"{instance_id}:{message_id}:{ab_test_group}").hexdigest(), 16)
       chosen = variants[hash_val % len(variants)]

   The hash-based allocation is intentional — the team found that
   ``random.choice()`` produced unstable results across retries
   (same instance + same message landed on different variants on
   resume), which made the A/B comparison statistics noisy.
   Hashing by ``(instance_id, message_id, ab_test_group)`` is
   stable across retries and across re-emissions of the same
   user message.

   After selecting a variant, the service calls
   :meth:`SkillABTestRepository.increment_comparison` so the A/B
   pipeline gets a per-feedback event counter. This MUST happen
   even when the same variant was chosen on the previous attempt —
   the comparison counter is total feedback events, not distinct
   variants. The Phase 4 metrics service reads this counter to
   decide when the test has enough data to resolve.

   If only one active variant exists (or none), no variant
   selection happens — the original skill is used as-is. This
   matches the A/B test resolve path which soft-deactivates the
   loser before the test is officially resolved.
3. **Formatting.** Renders the search results into a
   ``[System Inject]`` block with full markdown content for the
   injected skills and a one-liner list of low-match candidates.
4. **Tracking.** Stores ``{instance_id: {message_id: [skill_ids]}}``
   in memory so the Phase 4 metrics service can attribute a
   future feedback signal back to the skills that were offered
   for the task. The in-memory dict is intentionally lightweight
   — Phase 4 will refresh it from the ``skills.last_injected``
   metadata key when persisting.

Gating
------

The service itself does NOT gate on agent flags or message type
— the call site in
:meth:`daemon.services.instance_messaging.InstanceMessagingService._process_message_with_tracking`
checks ``agent_meta.skill_injection`` and the
``is_completion_report`` flag. This service is purely the
formatter + A/B router.

Error handling
--------------

* Search failures are propagated to the call site, which catches
  and logs. We do not swallow search errors here — the caller
  decides whether to fall back.
* A/B variant selection failures (DB error on
  ``get_ab_variants`` or ``increment_comparison``) are caught
  and logged so a transient DB issue doesn't blow up the user
  message. The original skill is used as-is in the failure case.

Design notes
------------

* **No I/O at construction.** The constructor takes duck-typed
  dependencies; nothing is loaded eagerly. The first
  :meth:`inject_skills` call is when the DB / search service
  get touched.
* **Async-only.** All public methods are ``async``; sync DB /
  repository calls go through ``asyncio.to_thread`` (the project
  pattern, established in Phase 2).
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from typing import Any

logger = logging.getLogger(__name__)


# Status values that mean "this variant can still be picked by A/B
# selection". ``inactive`` / ``archived`` variants are filtered out
# so the picker only sees live candidates.
_ACTIVE_AB_STATUSES: frozenset[str] = frozenset({"active", "ab_testing"})


# ============================================================
# SkillInjectionService
# ============================================================


class SkillInjectionService:
    r"""Render and A/B-route the skill-search results into an injectable text block.

    Phase 3 of the Skill Evolution System. Built on top of
    :class:`SkillSearchService` (Phase 2) and the A/B repos
    (Phase 1). The service is constructed once at the
    :class:`InstanceManager` level and held on the manager
    facade as ``self._skill_injection_service``.

    Attributes:
        _search_service: Duck-typed
            :class:`~daemon.services.skill_search_service.SkillSearchService`.
            Expected method: ``async search(user_message,
            project_id, max_results) -> {"injected": [...],
            "low_match": [...]}``.
        _config: Duck-typed
            :class:`~daemon.config.SkillEvolutionConfig`. Reads
            ``max_inject_skills`` to cap the search results.
        _ab_test_repo: Duck-typed
            :class:`~daemon.repositories.skill.repository.SkillABTestRepository`.
            Expected method:
            ``increment_comparison(ab_test_group) -> None``
            (synchronous; the service wraps it in
            ``asyncio.to_thread``).
        _skill_repo: Duck-typed
            :class:`~daemon.repositories.skill.repository.SkillRepository`.
            Expected method: ``get_ab_variants(ab_test_group)
            -> list[Skill]`` (synchronous; wrapped in
            ``asyncio.to_thread``).
        _injected_skills: In-memory
            ``{instance_id: {message_id: [skill_ids]}}`` cache
            for Phase 4 metrics attribution. Not persisted — a
            daemon restart drops the cache (Phase 4 reads from
            per-instance metadata instead, which is the
            long-term source of truth).
    """

    def __init__(
        self,
        search_service: Any,
        config: Any,  # SkillEvolutionConfig
        ab_test_repo: Any,
        skill_repo: Any,
    ) -> None:
        """Store the search service, config, and A/B repositories.

        Args:
            search_service: See :attr:`_search_service`.
            config: See :attr:`_config`.
            ab_test_repo: See :attr:`_ab_test_repo`.
            skill_repo: See :attr:`_skill_repo`.
        """
        self._search_service = search_service
        self._config = config
        self._ab_test_repo = ab_test_repo
        self._skill_repo = skill_repo
        # Per-instance, per-message — the Phase 4 metrics service
        # queries this to attribute a feedback signal back to
        # the skills that were offered for the task.
        self._injected_skills: dict[str, dict[str, list[str]]] = {}

    # --------------------------------------------------------
    # Public API
    # --------------------------------------------------------

    async def inject_skills(
        self,
        user_message: str,
        project_id: str | None,
        instance_id: str,
        message_id: str,
    ) -> tuple[str | None, list[str]]:
        """Resolve relevant skills and render an injection text block.

        Runs the three-stage search, applies A/B variant selection
        per skill, then formats the result as a ``[System Inject]``
        block. Returns ``(None, [])`` when the search yields no
        injected skills and no low-match candidates (the caller
        can skip injection entirely).

        Args:
            user_message: The raw user message text.
            project_id: The project scoping the search (``None``
                for a global-only search).
            instance_id: The receiving instance. Used as part of
                the deterministic A/B hash key — see class
                docstring.
            message_id: The queue message ID. Used alongside
                ``instance_id`` for A/B determinism.

        Returns:
            Tuple ``(injection_text, injected_skill_ids)``:

            * ``injection_text`` — formatted string, or ``None``
              when the search yielded nothing injectable.
            * ``injected_skill_ids`` — list of skill IDs that
              were selected (after A/B routing). Empty when no
              skills were injected.
        """
        # Stage 1 — run the three-stage search. Failures
        # propagate; the call site decides whether to swallow.
        max_results = getattr(self._config, "max_inject_skills", 2)
        results = await self._search_service.search(
            user_message,
            project_id=project_id,
            max_results=max_results,
        )

        injected = list(results.get("injected") or [])
        low_match = list(results.get("low_match") or [])

        # Empty result → nothing to inject. Caller treats this
        # as "skip the skill message entirely".
        if not injected and not low_match:
            return (None, [])

        # Stage 2 — A/B variant selection per injected skill.
        # Best-effort: any failure falls back to the original
        # skill so a transient DB issue doesn't blow up the user
        # message.
        routed_injected: list[dict[str, Any]] = []
        for item in injected:
            skill = item.get("skill") if isinstance(item, dict) else None
            score = item.get("score", 0.0) if isinstance(item, dict) else 0.0
            if skill is None:
                continue
            selected = await self._select_ab_variant(
                skill, instance_id, message_id
            )
            routed_injected.append({"skill": selected, "score": score})

        # Stage 3 — format. Empty routed_injected + non-empty
        # low_match still renders — the "other available skills"
        # section is independently useful even if the top-tier
        # list is empty (e.g. all variants were inactive).
        final_results: dict[str, list[dict[str, Any]]] = {
            "injected": routed_injected,
            "low_match": low_match,
        }
        injection_text = self._format_injection(final_results)

        # Collect injected skill IDs for Phase 4 tracking. Use
        # ``getattr(... , "id")`` so a mock skill without an id
        # attribute (test fixture) doesn't crash the injector.
        skill_ids: list[str] = [
            str(item["skill"].id)
            for item in routed_injected
            if item.get("skill") is not None
            and getattr(item["skill"], "id", None) is not None
        ]

        return (injection_text, skill_ids)

    def track_injection(
        self,
        instance_id: str,
        message_id: str,
        skill_ids: list[str],
    ) -> None:
        """Record which skills were offered for an (instance, message) pair.

        Populates the in-memory ``_injected_skills`` dict so the
        Phase 4 metrics service can attribute a future feedback
        signal back to the skills that were selected. Safe to
        call with ``skill_ids=[]`` — stores an empty list, which
        is a valid "nothing was injected" record.

        Args:
            instance_id: The receiving instance.
            message_id: The user message ID.
            skill_ids: Skill IDs selected by
                :meth:`inject_skills` (post-A/B routing).
        """
        if instance_id not in self._injected_skills:
            self._injected_skills[instance_id] = {}
        self._injected_skills[instance_id][message_id] = list(skill_ids)

    def get_injected_skill_ids(
        self,
        instance_id: str,
        message_id: str,
    ) -> list[str]:
        """Look up which skills were injected for a (instance, message).

        Args:
            instance_id: The receiving instance.
            message_id: The user message ID.

        Returns:
            List of skill IDs, or ``[]`` when none are recorded
            (no injection ran, the daemon restarted since the
            injection, or the message ID isn't yet known).
        """
        return list(
            self._injected_skills.get(instance_id, {}).get(message_id, [])
        )

    # --------------------------------------------------------
    # A/B variant selection
    # --------------------------------------------------------

    async def _select_ab_variant(
        self,
        skill: Any,
        instance_id: str,
        message_id: str,
    ) -> Any:
        """Pick the deterministic A/B variant for this skill.

        Looks up sibling variants by ``ab_test_group``, filters
        to the active set, and assigns one via a stable hash of
        ``(instance_id, message_id, ab_test_group)``.

        Critical contract:

        * After selecting a variant, MUST call
          :meth:`SkillABTestRepository.increment_comparison`. The
          approver explicitly flagged this as a blocking issue
          in the Phase 3 review — without the bump, the A/B
          comparison stats never accumulate and the test never
          resolves.
        * If ``skill`` has no ``ab_test_group`` or its status is
          not ``'ab_testing'``, return it unchanged.
        * If the variant lookup fails (DB error) or returns
          fewer than 2 active variants, return the original
          skill as-is. We do NOT fall back to first-encountered
          arbitrarily — the original skill is the safer choice.

        Args:
            skill: The skill returned by the search service.
            instance_id: Used as part of the hash key.
            message_id: Used as part of the hash key.

        Returns:
            The selected skill (possibly ``skill`` itself when
            A/B selection doesn't apply or fails).
        """
        ab_group = getattr(skill, "ab_test_group", None)
        status = getattr(skill, "status", "")

        # Fast path — not in an A/B test, no routing needed.
        if not ab_group or status != "ab_testing":
            return skill

        try:
            all_variants = await asyncio.to_thread(
                self._skill_repo.get_ab_variants, ab_group
            )
        except Exception as e:
            logger.warning(
                f"[SkillInjection] A/B variant fetch failed for "
                f"group={ab_group}: {e}. Using original skill."
            )
            return skill

        # Filter to active variants only. Sorting by ``id`` makes
        # the hash-based selection stable across retries — even
        # if the DB returns variants in a different order, the
        # same modulo picks the same skill.
        active_variants = sorted(
            [
                v
                for v in (all_variants or [])
                if getattr(v, "status", "") in _ACTIVE_AB_STATUSES
            ],
            key=lambda v: str(getattr(v, "id", "")),
        )

        # Fewer than 2 active variants → A/B is effectively
        # single-armed. Use the original skill and skip the
        # comparison bump so the stats don't get skewed by
        # no-op selections.
        if len(active_variants) < 2:
            return skill

        # Deterministic pick. md5 → int → modulo. The instance +
        # message pair is enough for uniqueness, but adding
        # ``ab_group`` future-proofs against per-group hashing
        # if we ever want to do per-group routing.
        hash_input = f"{instance_id}:{message_id}:{ab_group}".encode()
        hash_val = int(hashlib.md5(hash_input).hexdigest(), 16)
        chosen = active_variants[hash_val % len(active_variants)]

        # CRITICAL — bump the comparison counter. Must run AFTER
        # the variant is chosen so we only count the variant
        # that actually gets used (not the no-A/B fast path
        # above). Failure is logged but doesn't block injection.
        try:
            await asyncio.to_thread(
                self._ab_test_repo.increment_comparison, ab_group
            )
        except Exception as e:
            logger.warning(
                f"[SkillInjection] Failed to increment A/B "
                f"comparison for group={ab_group}: {e}"
            )

        return chosen

    # --------------------------------------------------------
    # Formatting
    # --------------------------------------------------------

    def _format_injection(
        self,
        results: dict[str, list[dict[str, Any]]],
    ) -> str:
        """Render the search results as a markdown injection block.

        Format::

            [System Inject] Relevant skills loaded:

            📋 **Skill: {name}** (match score: {score:.2f})
            ────────────────────────────
            {full_markdown_content}

            📋 **Other available skills** (low match):
            • {name2} ({score2:.2f}) — {description2}

            Use `skill_search` tool to find more skills.

        Notes:

        * When ``injected`` is empty (only ``low_match`` matches),
          the heading is still emitted so the reader knows the
          section is "available skills" not "nothing found".
          The block lists the low-match candidates directly.
        * When ``low_match`` is empty, the
          "Other available skills" section is omitted entirely
          — don't render an empty header.
        * The separator line is the unicode box-drawing char
          ``─`` (``U+2500``) repeated 30 times — matches the
          spec exactly. This is NOT an em-dash or hyphen-minus.
        * The closing "Use ``skill_search`` tool..." line is
          always emitted, even when both lists are empty (the
          service never reaches here for both-empty, but the
          formatter is defensive).

        Args:
            results: Dict with ``injected`` and ``low_match``
                lists. See :meth:`SkillSearchService.search` for
                per-item shape.

        Returns:
            The formatted injection text.
        """
        injected = results.get("injected") or []
        low_match = results.get("low_match") or []

        lines: list[str] = []
        lines.append("[System Inject] Relevant skills loaded:")
        lines.append("")

        # Top-tier skills — full markdown body for each.
        for item in injected:
            if not isinstance(item, dict):
                continue
            skill = item.get("skill")
            if skill is None:
                continue
            name = getattr(skill, "name", "") or "(unnamed)"
            score = item.get("score", 0.0)
            try:
                score_val = float(score)
            except (TypeError, ValueError):
                score_val = 0.0
            content = getattr(skill, "content", "") or ""
            lines.append(f"📋 **Skill: {name}** (match score: {score_val:.2f})")
            lines.append("─" * 30)
            lines.append(content)
            lines.append("")

        # Low-match section — only render when we actually have
        # candidates to show. The spec is explicit: "If no
        # low_match items, skip that section entirely".
        if low_match:
            lines.append("📋 **Other available skills** (low match):")
            for item in low_match:
                if not isinstance(item, dict):
                    continue
                name = item.get("name") or "(unnamed)"
                score = item.get("score", 0.0)
                try:
                    score_val = float(score)
                except (TypeError, ValueError):
                    score_val = 0.0
                description = item.get("description") or ""
                lines.append(
                    f"• {name} ({score_val:.2f}) — {description}"
                )
            lines.append("")

        # Closing hint — always present so the agent knows it
        # can expand the search with the dedicated tool.
        lines.append("Use `skill_search` tool to find more skills.")

        return "\n".join(lines)
