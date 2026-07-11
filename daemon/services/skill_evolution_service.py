"""Skill evolution service — Tier 2/3 analysis, evolution, CAPTURED, A/B testing.

Phase 5 of the Skill Evolution System. Provides the evolution
pipeline's active layers: cheap-LLM Tier 2 analysis, main-LLM Tier 3
mutation, the CAPTURED flow for automatic skill creation from
successful task patterns, lineage tracking, and A/B test resolution.

Design highlights
-----------------

* **Sync repo calls behind ``asyncio.to_thread``.** All repositories
  are synchronous; every DB call hops to a worker thread so the
  async caller never blocks the event loop.

* **LLM calls via openai.OpenAI sync client behind asyncio.to_thread.**
  Same pattern as skill_embedding_service. ``analysis_model`` /
  ``evolution_model`` come from ``SkillEvolutionConfig`` with
  fallback to ``llm_config['model']``.

* **Defensive response parsing.** Tier 2 analysis prompt asks the
  LLM for structured JSON; the parser handles fenced JSON, bare
  JSON, and key:value prose forms.

* **Embedding updates are best-effort.** Evolution creates new skill
  rows whose embedding cache is refreshed, but failures here MUST NOT
  abort the evolution — skills remain BM25-searchable.

* **A/B test resolution is two-tier.** Resolve by
  ``completion_rate`` if the sample size is hit AND the
  ``ab_min_difference`` threshold is met; otherwise extend up to
  ``max_extensions`` times. After that, force-resolve by raw
  completion rate even if the difference is sub-threshold.

* **FIX guard.** ``_evolve_fix`` refuses to mutate a skill already
  in active A/B testing (``status='ab_testing'``) — creating nested
  A/B tests would corrupt the per-group resolution bookkeeping.
"""

from __future__ import annotations

import asyncio
import difflib
import json
import logging
import re
import uuid
from typing import Any, Optional

import openai

logger = logging.getLogger(__name__)


# ============================================================
# Module-level helpers & regexes
# ============================================================


# Strip ``<think>...</think>`` reasoning blocks. Chat-tuned models
# (DeepSeek, Qwen, GLM, …) emit chain-of-thought inside these tags
# even when told to return only JSON — the thinking is noise to us
# but parsing it as JSON would yield garbage.
_THINK_BLOCK_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)

# Match ```json ... ``` fenced JSON object.
_FENCED_OBJ_RE = re.compile(
    r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL | re.IGNORECASE
)

# Match ```json ... ``` fenced JSON array (kept for completeness).
_FENCED_JSON_RE = re.compile(
    r"```(?:json)?\s*(\[.*?\])\s*```", re.DOTALL | re.IGNORECASE
)

# Match a bare JSON object inside prose.
_JSON_OBJ_RE = re.compile(r"\{.*?\}", re.DOTALL)

# Valid evolution_type values returned by the Tier 2 analysis prompt.
_VALID_EVOLUTION_TYPES = {"FIX", "DERIVED", "CAPTURED", "NONE"}

# Per-key regexes for fallback prose parsing.
_RE_KV_SHOULD = re.compile(
    r'"?should_evolve"?\s*[:=]\s*"?([A-Za-z0-9_]+)"?', re.IGNORECASE
)
_RE_KV_TYPE = re.compile(
    r'"?evolution_type"?\s*[:=]\s*"?([A-Za-z0-9_]+)"?', re.IGNORECASE
)
_RE_KV_DIRECTION = re.compile(
    r'"?direction"?\s*[:=]\s*"?(.*?)"?\s*(?:[,\n}]|$)', re.IGNORECASE
)
_RE_KV_SUMMARY = re.compile(
    r'"?analysis_summary"?\s*[:=]\s*"?(.*?)"?\s*(?:[,\n}]|$)', re.IGNORECASE
)
_RE_KV_NAME = re.compile(
    r'"?name"?\s*[:=]\s*"?(.*?)"?\s*(?:[,\n}]|$)', re.IGNORECASE
)
_RE_KV_DESC = re.compile(
    r'"?description"?\s*[:=]\s*"?(.*?)"?\s*(?:[,\n}]|$)', re.IGNORECASE
)
_RE_KV_CONTENT = re.compile(
    r'"?content"?\s*[:=]\s*"?(.*?)"?\s*(?:[,\n}]|$)', re.IGNORECASE
)


# ============================================================
# Service
# ============================================================


class SkillEvolutionService:
    """Phase 5: drive Tier 2/3 analysis, evolution, CAPTURED, A/B tests.

    The service is intentionally thin: every persistent operation
    is delegated to a sync repository that we hop into via
    :func:`asyncio.to_thread`, every LLM call hops to a thread for
    the OpenAI sync client. The service owns no state beyond
    its collaborators.

    Args:
        skill_repo: :class:`SkillRepository` (sync).
        lineage_repo: :class:`SkillLineageRepository` (sync).
        usage_repo: :class:`SkillUsageRepository` (sync).
        embedding_service: :class:`SkillEmbeddingService` (async).
        metrics_service: :class:`SkillMetricsService` (async).
        ab_test_repo: :class:`SkillABTestRepository` (sync).
        config: :class:`SkillEvolutionConfig` — exposes
            ``analysis_model``, ``evolution_model``, ``ab_sample_size``,
            ``ab_min_difference``, ``max_extensions``,
            ``capture_min_iterations``, ``capture_min_duration_seconds``.
        llm_config: Dict with at least ``base_url``, ``api_key``,
            ``model`` (fallback for analysis/evolution model names).
    """

    def __init__(
        self,
        skill_repo: Any,           # SkillRepository
        lineage_repo: Any,         # SkillLineageRepository
        usage_repo: Any,           # SkillUsageRepository
        embedding_service: Any,    # SkillEmbeddingService
        metrics_service: Any,      # SkillMetricsService
        ab_test_repo: Any,         # SkillABTestRepository
        config: Any,               # SkillEvolutionConfig
        llm_config: dict[str, Any],
    ) -> None:
        self._skill_repo = skill_repo
        self._lineage_repo = lineage_repo
        self._usage_repo = usage_repo
        self._embedding_service = embedding_service
        self._metrics_service = metrics_service
        self._ab_test_repo = ab_test_repo
        self._config = config
        self._llm_config = dict(llm_config)  # defensive shallow copy

    # --------------------------------------------------------
    # Public API: analysis
    # --------------------------------------------------------

    async def analyze_skill(
        self,
        skill_id: str,
        reason: str = "",
        stats: dict | None = None,
    ) -> dict:
        """Tier 2 analysis — decide whether a skill should evolve.

        Loads usage stats (or accepts them pre-computed), pulls the
        recent usage records, and asks the cheap LLM to classify
        the skill as ``FIX`` / ``DERIVED`` / ``CAPTURED`` / ``NONE``
        with a one-line ``direction``.

        Args:
            skill_id: The skill to analyze.
            reason: Caller-provided reason (e.g. ``"consecutive
                failures >= 3"``). Embedded verbatim into the
                prompt so the LLM can prioritize.
            stats: Pre-computed stats dict — if ``None``, fetched
                via :meth:`SkillUsageRepository.get_stats`. The
                expected keys mirror that repo's return shape.

        Returns:
            Dict with keys ``should_evolve`` (bool),
            ``evolution_type`` (str), ``direction`` (str),
            ``analysis_summary`` (str). When the skill is missing
            the dict is a benign ``"don't evolve"`` verdict.
        """
        if stats is None:
            stats = await asyncio.to_thread(
                self._usage_repo.get_stats, skill_id
            )

        skill = await asyncio.to_thread(self._skill_repo.get, skill_id)
        if skill is None:
            return {
                "should_evolve": False,
                "evolution_type": "NONE",
                "direction": "",
                "analysis_summary": "skill not found",
            }

        usage_records, _ = await asyncio.to_thread(
            self._usage_repo.get_by_skill, skill_id, 20
        )

        prompt = self._build_analysis_prompt(
            skill, stats, usage_records, reason
        )
        raw = await self._call_llm(
            prompt, model=self._resolve_analysis_model()
        )
        return self._parse_analysis_response(raw)

    # --------------------------------------------------------
    # Public API: evolution
    # --------------------------------------------------------

    async def evolve_skill(
        self,
        skill_id: str,
        evolution_type: str,
        direction: str = "",
    ) -> dict:
        """Tier 3 evolution — mutate a skill per the analysis verdict.

        Dispatches on ``evolution_type``:

        * ``FIX`` — create a tweaked copy and start an A/B test
          vs the original (guarded against nested A/B tests).
        * ``DERIVED`` — create a specialized sibling (new name,
          generation 0, lineage to the original).

        The CAPTURED path is intentionally NOT routed through this
        method — it requires a richer ``task_details`` payload than
        ``(skill_id, direction)`` can carry. Call
        :meth:`capture_skill` (or
        :meth:`SkillMetricsService._check_capture_eligibility` →
        :meth:`SkillJobDispatcher.enqueue_capture`) instead.

        Args:
            skill_id: The skill to evolve.
            evolution_type: One of ``FIX`` / ``DERIVED``. Passing
                ``CAPTURED`` raises ``ValueError`` — use
                :meth:`capture_skill` for the capture flow.
            direction: Short instruction fed to the evolution LLM.

        Returns:
            Dict whose shape depends on the path:

            * ``FIX``: ``{"new_skill_id", "old_skill_id",
              "ab_test_group", "skipped"}``.
            * ``DERIVED``: ``{"new_skill_id", "parent_ids",
              "skipped"}``.

        Raises:
            ValueError: When ``skill_id`` is not found, when
                ``evolution_type`` is ``CAPTURED`` (rejected — use
                :meth:`capture_skill` instead), or when
                ``evolution_type`` is not one of the two known
                values.
        """
        skill = await asyncio.to_thread(self._skill_repo.get, skill_id)
        if skill is None:
            raise ValueError(f"skill not found: {skill_id}")

        if evolution_type == "CAPTURED":
            raise ValueError(
                "CAPTURED evolution cannot be initiated from "
                "evolve_skill(). Use capture_skill(instance_id, "
                "task_details) instead."
            )

        if evolution_type == "FIX":
            return await self._evolve_fix(skill, direction)
        if evolution_type == "DERIVED":
            return await self._evolve_derived(skill, direction)
        raise ValueError(
            f"unknown evolution_type: {evolution_type!r} "
            f"(expected FIX | DERIVED)"
        )

    # --------------------------------------------------------
    # Internal: evolution paths
    # --------------------------------------------------------

    async def _evolve_fix(self, skill: Any, direction: str) -> dict:
        """Tier 3 FIX — create a tweaked copy and start an A/B test.

        **Guard first:** if the source skill is already part of an
        active A/B test (``status == 'ab_testing'``), refuse the
        mutation. Spawning a nested A/B test against a skill that
        is itself a variant would corrupt the per-group
        resolution bookkeeping (the outer test would never see
        the inner test's comparisons aggregated under its own
        group).

        On success: the old skill and the new candidate both get
        ``status='ab_testing'`` (the repo default is 'active' —
        we override it) and share a fresh ``ab_test_group`` UUID
        so the resolution loop can pair them up.

        Args:
            skill: The source :class:`Skill` row.
            direction: Short instruction fed to the evolution LLM.

        Returns:
            Dict with ``new_skill_id``, ``old_skill_id``,
            ``ab_test_group``, ``skipped``. When the guard fires
            the dict carries ``skipped=True`` and a ``reason``.
        """
        # GUARD FIRST — never mutate a skill that's already in
        # an active A/B test (would create nested A/B tests).
        if getattr(skill, "status", None) == "ab_testing":
            logger.warning(
                f"[SkillEvolution] Refusing FIX on skill "
                f"id={getattr(skill, 'id', '?')}: already in "
                f"active A/B testing"
            )
            return {
                "skipped": True,
                "reason": "skill already in active A/B testing",
                "skill_id": getattr(skill, "id", None),
            }

        new_content = await self._generate_evolved_content(skill, direction)
        ab_group = str(uuid.uuid4())

        new_skill = await asyncio.to_thread(
            self._skill_repo.create,
            name=skill.name,
            description=skill.description,
            content=new_content,
            project_id=skill.project_id,
            category=skill.category,
            lineage_origin="evolved",
            generation=skill.generation + 1,
            ab_test_group=ab_group,
            status="ab_testing",
            is_active=True,
        )

        # Old skill also gets status='ab_testing' per spec —
        # the active variant during the test is whichever the
        # trigger resolver picks (both are ``is_active=True``).
        await asyncio.to_thread(
            self._skill_repo.update,
            skill.id,
            ab_test_group=ab_group,
            status="ab_testing",
        )

        await asyncio.to_thread(
            self._ab_test_repo.create_ab_test,
            ab_group,
            skill.id,
            new_skill.id,
        )

        await asyncio.to_thread(
            self._lineage_repo.create,
            new_skill.id,
            skill.id,
            change_summary=f"FIX: {direction}",
            content_diff=self._compute_diff(skill.content, new_content),
        )

        # Embedding refresh is best-effort — if the LLM/embedding
        # path is broken we MUST still keep the new skill
        # BM25-searchable.
        try:
            await self._embedding_service.update_skill_embeddings(new_skill)
        except Exception as e:
            logger.warning(
                f"[SkillEvolution] Embedding refresh failed for "
                f"new_skill_id={new_skill.id} after FIX: {e!s}. "
                f"Skill remains BM25-searchable."
            )

        return {
            "new_skill_id": new_skill.id,
            "old_skill_id": skill.id,
            "ab_test_group": ab_group,
            "skipped": False,
        }

    async def _evolve_derived(self, skill: Any, direction: str) -> dict:
        """Tier 3 DERIVED — create a specialized sibling.

        Derived skills are a *new* name (suffixed ``-specialized``),
        not a new generation of the original — so they start at
        ``generation=0`` and link back via :class:`SkillLineage`.

        No A/B test is started: derived skills are siblings of the
        original, not replacements for it. The trigger resolver
        sees both ``is_active=True`` rows; selection is driven by
        the search score, not a forced pairing.

        Args:
            skill: The source :class:`Skill` row.
            direction: Short instruction fed to the evolution LLM.

        Returns:
            Dict with ``new_skill_id``, ``parent_ids`` (always a
            one-element list), ``skipped``.
        """
        new_content = await self._generate_evolved_content(skill, direction)
        new_name = f"{skill.name}-specialized"

        new_skill = await asyncio.to_thread(
            self._skill_repo.create,
            name=new_name,
            description=skill.description,
            content=new_content,
            project_id=skill.project_id,
            category=skill.category,
            lineage_origin="evolved",
            generation=0,
        )

        await asyncio.to_thread(
            self._lineage_repo.create,
            new_skill.id,
            skill.id,
            change_summary=f"DERIVED: {direction}",
            content_diff=self._compute_diff(skill.content, new_content),
        )

        try:
            await self._embedding_service.update_skill_embeddings(new_skill)
        except Exception as e:
            logger.warning(
                f"[SkillEvolution] Embedding refresh failed for "
                f"new_skill_id={new_skill.id} after DERIVED: {e!s}. "
                f"Skill remains BM25-searchable."
            )

        return {
            "new_skill_id": new_skill.id,
            "parent_ids": [skill.id],
            "skipped": False,
        }

    async def _evolve_captured(self, task_details: dict) -> dict:
        """CAPTURED flow — extract a reusable skill from a successful task.

        Signature is deliberately ``(self, task_details: dict)`` —
        a *dict* of arbitrary shape so callers (the
        ``skill_metrics_service`` completion hook, the CAPTURED
        background job, future sources…) don't have to fit a
        fixed dataclass. Recognized keys:

        * ``task_message`` / ``message`` (str) — the user input.
        * ``iterations`` (int) — loop iterations the agent took.
        * ``duration_seconds`` (int) — wall-clock runtime.
        * ``agent_id`` (str) — the agent that executed.
        * ``project_id`` (str | None) — owning project.
        * ``skill`` (:class:`Skill`) — optional pre-existing
          skill to derive from; the prompt is augmented with
          its ``content`` if present.

        The LLM is asked to produce a JSON object with
        ``name`` / ``description`` / ``content``. The parser is
        defensive — on failure it falls back to using the raw
        response as the content body and derives ``name`` /
        ``description`` from its first 5 words / first sentence.

        Args:
            task_details: Dict of CAPTURED-flow inputs.

        Returns:
            Dict with ``new_skill_id`` and ``skipped``.

        Raises:
            ValueError: When ``task_details`` is empty / falsy.
        """
        if not task_details:
            raise ValueError(
                "_evolve_captured requires a non-empty task_details dict"
            )

        task_message = (
            task_details.get("task_message")
            or task_details.get("message")
            or ""
        )
        iterations = task_details.get("iterations", 0)
        duration_seconds = task_details.get("duration_seconds", 0)
        agent_id = task_details.get("agent_id", "")
        project_id = task_details.get("project_id")
        existing_skill = task_details.get("skill")

        prompt = self._build_capture_prompt(
            task_message=task_message,
            iterations=iterations,
            duration_seconds=duration_seconds,
            agent_id=agent_id,
            existing_skill=existing_skill,
        )

        raw = await self._call_llm(
            prompt, model=self._resolve_evolution_model()
        )
        name, description, content = self._parse_capture_response(raw)

        new_skill = await asyncio.to_thread(
            self._skill_repo.create,
            name=name,
            description=description,
            content=content,
            project_id=project_id,
            category="workflow",
            lineage_origin="captured",
            generation=0,
            status="active",
        )

        try:
            await self._embedding_service.update_skill_embeddings(new_skill)
        except Exception as e:
            logger.warning(
                f"[SkillEvolution] Embedding refresh failed for "
                f"new_skill_id={new_skill.id} after CAPTURED: {e!s}. "
                f"Skill remains BM25-searchable."
            )

        return {
            "new_skill_id": new_skill.id,
            "skipped": False,
        }

    # --------------------------------------------------------
    # Public API: capture wrapper
    # --------------------------------------------------------

    async def capture_skill(
        self,
        instance_id: str,
        task_details: dict,
    ) -> dict:
        """Public wrapper that delegates to :meth:`_evolve_captured`.

        The real validation (was the task successful, was it
        complex enough, was a skill already applied?) lives in
        :meth:`check_and_capture` — this is the post-validation
        entry point that actually creates the new skill.

        Args:
            instance_id: The instance the task ran on (kept for
                parity / future logging; not used today).
            task_details: Same dict shape as
                :meth:`_evolve_captured` expects.

        Returns:
            Dict with ``new_skill_id`` and ``skipped``.

        Raises:
            ValueError: When ``task_details`` is empty.
        """
        if not task_details:
            raise ValueError(
                "capture_skill requires a non-empty task_details dict"
            )
        return await self._evolve_captured(task_details)

    # --------------------------------------------------------
    # Public API: A/B test resolution
    # --------------------------------------------------------

    async def check_ab_test_resolution(
        self,
        ab_test_group: str,
        winner_id: Optional[str] = None,
    ) -> dict:
        """Decide whether an A/B test should resolve, extend, or wait.

        Decision tree:

        0. **Forced winner** (``winner_id`` argument is set) →
           validate the ID against the two persisted variants,
           then run the same side effects as Path 2 / Path 3
           (deactivate loser / resolve ab_test / promote winner).
           ``reason='forced_winner'``. Skips the
           sample-size / threshold / extension gates entirely —
           the caller has explicit authority to pick.
        1. **Not enough data** (``comparisons < ab_sample_size``) →
           keep collecting. ``reason='needs_more_data'``.
        2. **Threshold met** (``difference >= ab_min_difference``) →
           resolve by raw completion rate. ``reason='threshold_met'``.
        3. **Threshold missed + ``extension_count >= max_extensions``** →
           force-resolve by raw completion rate.
           ``reason='force_resolved_max_extensions'``.
        4. **Threshold missed + extensions remaining** → bump
           ``extension_count`` via the repo. ``reason='extended'``.

        The persisted ``SkillABTest`` row is the source of truth
        for ``comparisons`` / ``extension_count`` — the metrics
        service mirrors them into its returned dict but we trust
        the row to avoid drift under concurrent feedback
        ingestion.

        Args:
            ab_test_group: The shared UUID grouping old + new
                variants.
            winner_id: Optional forced-winner skill ID. When set,
                the test is force-resolved by selecting this
                variant as the winner (the other variant is
                deactivated). Must match one of the test's two
                variant IDs — anything else raises
                ``ValueError``. ``None`` (default) runs the
                normal completion-rate-driven decision tree.

        Returns:
            Dict with ``resolved`` (bool), ``winner_id`` / ``loser_id``
            (str | None), ``reason`` (str), ``extension_count`` (int).
            When ``winner_id`` is provided, ``reason`` is
            ``"forced_winner"``.

        Raises:
            ValueError: When ``winner_id`` is provided but does
                not match either variant of the test group.
        """
        sample_size = getattr(self._config, "ab_sample_size", 10) or 10
        min_diff = getattr(self._config, "ab_min_difference", 0.15) or 0.15
        max_ext = getattr(self._config, "max_extensions", 3) or 3

        # Fetch the test row + per-variant stats in parallel.
        ab_test, stats = await asyncio.gather(
            asyncio.to_thread(self._ab_test_repo.get_by_group, ab_test_group),
            self._metrics_service.get_ab_comparison_stats(ab_test_group),
        )

        if ab_test is None:
            return {
                "resolved": False,
                "winner_id": None,
                "loser_id": None,
                "reason": "ab_test_group not found",
                "extension_count": 0,
            }

        # Trust the persisted row over the stats dict for
        # counters — single source of truth.
        extension_count = int(getattr(ab_test, "extension_count", 0) or 0)
        comparisons = int(getattr(ab_test, "comparisons", 0) or 0)
        difference = float(stats.get("difference", 0.0) or 0.0)

        # Path 0: forced-winner override. Validates the supplied
        # ID against the persisted variant pair and short-circuits
        # the sample-size / threshold / extension gates. The
        # caller (e.g. an admin UI) has explicit authority to
        # pick a winner; we still run the same three concurrent
        # side effects as Paths 2 / 3 so the persisted state
        # matches the auto-resolution outcome.
        if winner_id is not None:
            skill_id_a = stats.get("skill_id_a")
            skill_id_b = stats.get("skill_id_b")
            if winner_id not in (skill_id_a, skill_id_b):
                raise ValueError(
                    f"winner_id={winner_id!r} is not a variant of "
                    f"ab_test_group={ab_test_group!r} "
                    f"(variants: {skill_id_a!r}, {skill_id_b!r})"
                )
            loser_id = (
                skill_id_b if winner_id == skill_id_a else skill_id_a
            )
            logger.info(
                f"[SkillEvolution] A/B test {ab_test_group}: forced "
                f"winner={winner_id} (loser={loser_id})"
            )
            await asyncio.gather(
                asyncio.to_thread(
                    self._skill_repo.deactivate, loser_id,
                ),
                asyncio.to_thread(
                    self._ab_test_repo.resolve,
                    ab_test_group, winner_id,
                ),
                asyncio.to_thread(
                    self._skill_repo.update,
                    winner_id,
                    ab_test_group=None,
                    status="active",
                ),
            )
            return {
                "resolved": True,
                "winner_id": winner_id,
                "loser_id": loser_id,
                "reason": "forced_winner",
                "extension_count": extension_count,
            }

        if comparisons < sample_size:
            return {
                "resolved": False,
                "winner_id": None,
                "loser_id": None,
                "reason": "needs_more_data",
                "extension_count": extension_count,
            }

        # Helper: pick the winner by raw completion_rate.
        def _pick_winner() -> tuple[Optional[str], Optional[str]]:
            rate_a = float(stats.get("completion_rate_a", 0.0) or 0.0)
            rate_b = float(stats.get("completion_rate_b", 0.0) or 0.0)
            if rate_a >= rate_b:
                return (
                    stats.get("skill_id_a"),
                    stats.get("skill_id_b"),
                )
            return (
                stats.get("skill_id_b"),
                stats.get("skill_id_a"),
            )

        # Path 2: threshold met.
        if difference >= min_diff:
            winner_id, loser_id = _pick_winner()
            await asyncio.gather(
                asyncio.to_thread(self._skill_repo.deactivate, loser_id),
                asyncio.to_thread(self._ab_test_repo.resolve, ab_test_group, winner_id),
                asyncio.to_thread(
                    self._skill_repo.update,
                    winner_id,
                    ab_test_group=None,
                    status="active",
                ),
            )
            return {
                "resolved": True,
                "winner_id": winner_id,
                "loser_id": loser_id,
                "reason": "threshold_met",
                "extension_count": extension_count,
            }

        # Path 3: out of extensions → force-resolve.
        if extension_count >= max_ext:
            winner_id, loser_id = _pick_winner()
            logger.info(
                f"[SkillEvolution] A/B test {ab_test_group}: max_extensions "
                f"({max_ext}) reached, force-resolving by raw completion_rate "
                f"(difference={difference:.3f} < threshold={min_diff})"
            )
            await asyncio.gather(
                asyncio.to_thread(self._skill_repo.deactivate, loser_id),
                asyncio.to_thread(self._ab_test_repo.resolve, ab_test_group, winner_id),
                asyncio.to_thread(
                    self._skill_repo.update,
                    winner_id,
                    ab_test_group=None,
                    status="active",
                ),
            )
            return {
                "resolved": True,
                "winner_id": winner_id,
                "loser_id": loser_id,
                "reason": "force_resolved_max_extensions",
                "extension_count": extension_count,
            }

        # Path 4: extend.
        await asyncio.to_thread(
            self._ab_test_repo.increment_extension, ab_test_group
        )
        new_ext_count = extension_count + 1
        logger.info(
            f"[SkillEvolution] A/B test {ab_test_group}: difference "
            f"{difference:.3f} < threshold {min_diff}, extending test "
            f"(extension {new_ext_count}/{max_ext})"
        )
        return {
            "resolved": False,
            "winner_id": None,
            "loser_id": None,
            "reason": "extended",
            "extension_count": new_ext_count,
        }

    # --------------------------------------------------------
    # Public API: read-only metrics for a skill
    # --------------------------------------------------------

    async def get_skill_metrics(self, skill_id: str) -> dict:
        """Bundle a skill row, its stats, and its A/B test status.

        Convenience accessor for the metrics / admin UI:
        one DB+service round trip per dependency, results merged
        into a single dict.

        Args:
            skill_id: The skill to summarize.

        Returns:
            Dict with ``skill_id``, ``found`` (bool), and — when
            found — ``skill`` (via ``Skill.to_dict()``), ``stats``
            (from :meth:`SkillMetricsService.get_skill_stats`),
            ``usage_recent_count`` (len of the 20 most-recent
            records), and ``ab_test`` (the persisted test row as
            a dict, or ``None`` when the skill isn't in a test).
        """
        skill = await asyncio.to_thread(self._skill_repo.get, skill_id)
        if skill is None:
            return {"skill_id": skill_id, "found": False}

        stats = await self._metrics_service.get_skill_stats(skill_id)
        usage_records, _ = await asyncio.to_thread(
            self._usage_repo.get_by_skill, skill_id, 20
        )

        ab_test_status: Optional[dict] = None
        ab_group = getattr(skill, "ab_test_group", None)
        if ab_group:
            ab_test = await asyncio.to_thread(
                self._ab_test_repo.get_by_group, ab_group
            )
            if ab_test is not None:
                ab_test_status = {
                    "ab_test_group": ab_test.ab_test_group,
                    "comparisons": ab_test.comparisons,
                    "extension_count": ab_test.extension_count,
                    "resolved_at": ab_test.resolved_at,
                    "winner_skill_id": ab_test.winner_skill_id,
                }

        return {
            "skill_id": skill_id,
            "found": True,
            "skill": skill.to_dict(),
            "stats": stats,
            "usage_recent_count": len(usage_records),
            "ab_test": ab_test_status,
        }

    # --------------------------------------------------------
    # Public API: capture gate
    # --------------------------------------------------------

    async def check_and_capture(
        self,
        instance_id: str,
        agent_id: str,
        project_id: Optional[str],
        task_message: str,
        task_succeeded: bool,
        iterations: int,
        duration_seconds: int,
    ) -> Optional[dict]:
        """Decide whether a successful task should spawn a CAPTURED skill.

        Validation rules (cheap — no LLM call yet):

        * ``task_succeeded`` must be true.
        * Either ``iterations > capture_min_iterations`` OR
          ``duration_seconds > capture_min_duration_seconds`` —
          we don't want to capture trivial / instant successes.
        * ``has_applied_for_instance(instance_id)`` must be false —
          if a skill was already applied to this instance the
          success is attributed to that skill, not a new pattern.

        The returned dict is the *input* to
        :meth:`capture_skill` — the caller (typically
        ``skill_metrics_service``) is responsible for enqueuing
        the actual CAPTURED job. We don't dispatch here because
        the job_queue plumbing lives in a separate layer and
        the spec asks us not to call into ``job_dispatcher``.

        Args:
            instance_id: The instance the task ran on.
            agent_id: The agent that executed.
            project_id: Owning project, or ``None`` for global.
            task_message: The user's input.
            task_succeeded: Whether the task completed.
            iterations: Loop iterations the agent took.
            duration_seconds: Wall-clock runtime.

        Returns:
            A ``task_details`` dict ready to feed into
            :meth:`capture_skill` if all gates pass, else ``None``.
        """
        if not task_succeeded:
            return None

        min_iter = (
            getattr(self._config, "capture_min_iterations", 5) or 5
        )
        min_dur = (
            getattr(self._config, "capture_min_duration_seconds", 60) or 60
        )

        # Trivial success — skip. We require *either* a non-trivial
        # iteration count OR a non-trivial duration.
        if iterations <= min_iter and duration_seconds <= min_dur:
            return None

        applied = await asyncio.to_thread(
            self._usage_repo.has_applied_for_instance, instance_id
        )
        if applied:
            # A skill was already applied — the success is
            # already attributed. Don't create a sibling.
            return None

        return {
            "instance_id": instance_id,
            "agent_id": agent_id,
            "project_id": project_id,
            "task_message": task_message,
            "iterations": iterations,
            "duration_seconds": duration_seconds,
            "task_succeeded": task_succeeded,
        }

    # --------------------------------------------------------
    # LLM: model resolution
    # --------------------------------------------------------

    def _resolve_analysis_model(self) -> str:
        """Resolve the Tier 2 analysis model.

        Order: ``config.analysis_model`` → ``llm_config['model']``
        → ``"gpt-4o-mini"`` (cheap default).
        """
        return (
            getattr(self._config, "analysis_model", None)
            or self._llm_config.get("model")
            or "gpt-4o-mini"
        )

    def _resolve_evolution_model(self) -> str:
        """Resolve the Tier 3 evolution model.

        Order: ``config.evolution_model`` → ``llm_config['model']``
        → ``"gpt-4o"`` (capable default).
        """
        return (
            getattr(self._config, "evolution_model", None)
            or self._llm_config.get("model")
            or "gpt-4o"
        )

    def _resolve_chat_base_url(self) -> Optional[str]:
        """Return the chat endpoint's ``base_url`` (no evolution override)."""
        return self._llm_config.get("base_url")

    def _resolve_chat_api_key(self) -> Optional[str]:
        """Return the chat endpoint's ``api_key`` (no evolution override)."""
        return self._llm_config.get("api_key")

    # --------------------------------------------------------
    # LLM: chat call
    # --------------------------------------------------------

    async def _call_llm(self, prompt: str, model: str | None = None) -> str:
        """Make a single chat-completion call behind ``asyncio.to_thread``.

        Same pattern as
        :meth:`SkillEmbeddingService.generate_trigger_queries`:
        the OpenAI Python SDK is sync-only, so we wrap the
        client construction + ``chat.completions.create`` in a
        thread. The response is then flattened through the
        defensive ``_extract_chat_content`` helper that handles
        both the standard ``message.content`` shape and the
        less common ``choices[0].text`` / list-of-content-blocks
        shapes (some proxy surfaces), plus strips
        ``<think>...</think>`` reasoning blocks from chat-tuned
        models.

        Args:
            prompt: The user-role prompt. Single user message —
                keeps Tier 2 / Tier 3 prompts self-contained.
            model: Override model name. ``None`` falls back to
                the analysis model (since this is called by both
                Tier 2 and Tier 3 paths; callers pass an explicit
                override when they want the evolution model).

        Returns:
            The extracted content string. Empty string on any
            error — callers are defensive parsers.
        """
        resolved_model = model or self._resolve_analysis_model()
        base_url = self._resolve_chat_base_url()
        api_key = self._resolve_chat_api_key()

        messages = [{"role": "user", "content": prompt}]

        def _call_chat() -> Any:
            client = openai.OpenAI(
                api_key=api_key or "",
                base_url=base_url or None,
            )
            # The OpenAI Python SDK accepts plain ``{"role": ..., "content": ...}``
            # dicts at runtime; the strict ``ChatCompletionMessageParam`` type
            # alias isn't expressible as a dict literal without ``cast``.
            return client.chat.completions.create(
                model=resolved_model,
                messages=messages,  # type: ignore[arg-type]
                temperature=0.7,
            )

        try:
            response = await asyncio.to_thread(_call_chat)
        except Exception as e:
            logger.warning(
                f"[SkillEvolution] LLM call failed: {e!s}"
            )
            return ""
        return self._extract_chat_content(response)

    @staticmethod
    def _extract_chat_content(response: Any) -> str:
        """Extract textual content from a chat-completion response.

        Handles the three shapes we see in practice:

        * ``response.choices[0].message.content`` — standard
          OpenAI shape.
        * ``response.choices[0].text`` — legacy / proxy shape.
        * ``content`` itself as a list of content blocks
          (``[{"type": "text", "text": "..."}]``).

        Plus strips ``<think>...</think>`` reasoning blocks so
        they don't pollute the JSON parser.

        Returns ``""`` on any unexpected error so the caller
        can fall through to its empty-result branch.
        """
        content = ""
        try:
            choices = getattr(response, "choices", None) or []
            if not choices:
                return ""
            first = choices[0]
            message = getattr(first, "message", None)
            if message is not None:
                content = getattr(message, "content", "") or ""
            else:
                content = getattr(first, "text", "") or ""
        except Exception:
            return ""

        if isinstance(content, list):
            parts: list[str] = []
            for block in content:
                if isinstance(block, dict):
                    text = block.get("text", "")
                    if text:
                        parts.append(str(text))
                else:
                    parts.append(str(block))
            content = " ".join(parts)

        return _THINK_BLOCK_RE.sub("", str(content or ""))

    # --------------------------------------------------------
    # LLM: prompt builders
    # --------------------------------------------------------

    @staticmethod
    def _build_analysis_prompt(
        skill: Any,
        stats: Optional[dict],
        usage_records: list,
        reason: str,
    ) -> str:
        """Build the Tier 2 analysis prompt.

        Asks the cheap LLM for a strict JSON object with keys
        ``should_evolve`` / ``evolution_type`` / ``direction`` /
        ``analysis_summary``. Content is truncated to 1500 chars
        to keep the prompt under the cheap-model context window.

        Args:
            skill: The skill being analyzed.
            stats: Output of
                :meth:`SkillUsageRepository.get_stats`. ``None``
                is tolerated (treated as an empty dict).
            usage_records: Recent usage rows (max 20; we keep
                the 10 most recent in the prompt).
            reason: Caller-provided reason string.

        Returns:
            The fully-formed prompt string.
        """
        stats = stats or {}
        content = (getattr(skill, "content", "") or "")[:1500]
        completion_rate = stats.get("completion_rate", 0.0)
        fallback_rate = stats.get("fallback_rate", 0.0)
        total = stats.get("total", 0)
        consecutive_failures = stats.get("consecutive_failures", 0)

        recent_lines: list[str] = []
        for rec in usage_records[:10]:
            ok = getattr(rec, "task_succeeded", None)
            note = getattr(rec, "feedback_note", "") or ""
            recent_lines.append(
                f"- succeeded={ok} feedback={note!r}"
            )
        recent_block = "\n".join(recent_lines) or "(no recent records)"

        return (
            "You are an expert at analyzing skill performance.\n\n"
            f"Skill name: {skill.name}\n"
            f"Skill description: {skill.description}\n"
            f"Skill content (first 1500 chars):\n{content}\n\n"
            f"Stats:\n"
            f"- total selections: {total}\n"
            f"- completion_rate: {completion_rate}\n"
            f"- fallback_rate: {fallback_rate}\n"
            f"- consecutive_failures: {consecutive_failures}\n\n"
            f"Reason for this analysis: {reason or '(none)'}\n\n"
            f"Recent usage (up to 10 records):\n{recent_block}\n\n"
            "Decide whether this skill should evolve. Reply with a "
            "single JSON object with exactly these keys:\n"
            '  "should_evolve": <true|false>,\n'
            '  "evolution_type": "FIX" | "DERIVED" | "CAPTURED" | "NONE",\n'
            '  "direction": "<short instruction for the evolution LLM>",\n'
            '  "analysis_summary": "<one paragraph rationale>"\n\n'
            "Definitions:\n"
            '- FIX: the skill is broken or underperforming; tweak '
            'it in place (A/B test against the current version).\n'
            '- DERIVED: the skill is fine but a specialized sibling '
            'would help on a sub-task.\n'
            '- CAPTURED: a NEW pattern should be extracted from '
            'observed usage (do not use here — use the capture flow).\n'
            '- NONE: the skill is healthy, no action needed.\n\n'
            "Return ONLY the JSON object. No markdown fences, no prose."
        )

    @staticmethod
    def _build_capture_prompt(
        task_message: str,
        iterations: int,
        duration_seconds: int,
        agent_id: str,
        existing_skill: Any,
    ) -> str:
        """Build the CAPTURED extraction prompt.

        Asks the LLM to distill a successful task into a reusable
        skill body. When an existing skill is supplied (rare —
        typically CAPTURED runs without one) its content is
        included as a starting point.

        Args:
            task_message: The user's input.
            iterations: Loop iterations the agent took.
            duration_seconds: Wall-clock runtime.
            agent_id: The agent that executed.
            existing_skill: Optional pre-existing skill to build
                from. ``None`` for the common fresh-capture case.

        Returns:
            The fully-formed prompt string.
        """
        header = (
            "You are an expert skill author. A task just succeeded "
            "in production; distill it into a reusable skill.\n\n"
            f"Agent: {agent_id}\n"
            f"Iterations: {iterations}\n"
            f"Duration (s): {duration_seconds}\n\n"
            f"Task message:\n{task_message}\n\n"
        )
        existing_block = ""
        if existing_skill is not None:
            existing_block = (
                "Existing skill to build on:\n"
                f"- name: {getattr(existing_skill, 'name', '')}\n"
                f"- description: {getattr(existing_skill, 'description', '')}\n"
                f"- content (excerpt):\n"
                f"{(getattr(existing_skill, 'content', '') or '')[:1500]}\n\n"
            )
        return (
            header
            + existing_block
            + "Return a JSON object with exactly these keys:\n"
            '  "name": "<short kebab-case skill name>",\n'
            '  "description": "<one-line summary>",\n'
            '  "content": "<markdown body of the skill — instructions an agent can follow>"\n\n'
            "Return ONLY the JSON object. No markdown fences, no prose."
        )

    # --------------------------------------------------------
    # LLM: response parsers
    # --------------------------------------------------------

    @staticmethod
    def _coerce_bool(value: Any) -> bool:
        """Coerce an arbitrary LLM-emitted ``should_evolve`` value to bool.

        Handles the obvious LLM failure modes:

        * ``"true"`` / ``"yes"`` / ``"1"`` → ``True``
        * ``"false"`` / ``"no"`` / ``"0"`` → ``False``
        * Booleans pass through.
        * Anything else falls back to Python truthiness — but
          never returns ``False`` for empty / missing values
          (defensive: ``None`` → ``False``).
        """
        if isinstance(value, bool):
            return value
        if value is None:
            return False
        text = str(value).strip().lower()
        if text in ("false", "no", "0", "off", ""):
            return False
        if text in ("true", "yes", "1", "on"):
            return True
        return bool(value)

    def _parse_analysis_response(self, raw: str) -> dict:
        """Parse the Tier 2 analysis JSON with four fallback layers.

        1. Strip ``<think>...</think>`` blocks.
        2. Try a fenced `````json ... ````` block.
        3. Try a bare JSON object (``{...}``) inside prose.
        4. Try per-key ``key: value`` regex over prose.

        On total failure: return ``{"should_evolve": False,
        "evolution_type": "NONE", "direction": "",
        "analysis_summary": <first 500 chars of raw>}`` so the
        caller can still surface what the LLM said.

        Args:
            raw: The raw LLM response.

        Returns:
            Dict with the four canonical keys; ``evolution_type``
            is coerced into the closed set ``FIX | DERIVED |
            CAPTURED | NONE``.
        """
        text = _THINK_BLOCK_RE.sub("", raw or "")

        parsed: Optional[dict] = None

        # Layer 2: fenced JSON.
        fenced = _FENCED_OBJ_RE.search(text)
        if fenced:
            try:
                candidate = json.loads(fenced.group(1))
                if isinstance(candidate, dict):
                    parsed = candidate
            except Exception:
                parsed = None

        # Layer 3: bare JSON object.
        if parsed is None:
            for match in _JSON_OBJ_RE.finditer(text):
                candidate_str = match.group(0)
                try:
                    candidate = json.loads(candidate_str)
                except Exception:
                    continue
                if isinstance(candidate, dict):
                    parsed = candidate
                    break

        if parsed is not None:
            should = self._coerce_bool(parsed.get("should_evolve", False))
            evo_type = str(parsed.get("evolution_type", "NONE") or "NONE")
            if evo_type not in _VALID_EVOLUTION_TYPES:
                evo_type = "NONE"
            direction = str(parsed.get("direction", "") or "")
            summary = str(
                parsed.get("analysis_summary", "") or ""
            )
            return {
                "should_evolve": should,
                "evolution_type": evo_type,
                "direction": direction,
                "analysis_summary": summary,
            }

        # Layer 4: key:value prose fallback.
        m_should = _RE_KV_SHOULD.search(text)
        m_type = _RE_KV_TYPE.search(text)
        m_dir = _RE_KV_DIRECTION.search(text)
        m_sum = _RE_KV_SUMMARY.search(text)
        if m_type:
            should = self._coerce_bool(m_should.group(1)) if m_should else False
            evo_type = str(m_type.group(1) or "NONE").upper()
            if evo_type not in _VALID_EVOLUTION_TYPES:
                evo_type = "NONE"
            direction = m_dir.group(1).strip() if m_dir else ""
            summary = m_sum.group(1).strip() if m_sum else ""
            return {
                "should_evolve": should,
                "evolution_type": evo_type,
                "direction": direction,
                "analysis_summary": summary,
            }

        # Layer 5: total failure.
        return {
            "should_evolve": False,
            "evolution_type": "NONE",
            "direction": "",
            "analysis_summary": (raw or "")[:500],
        }

    def _parse_capture_response(self, raw: str) -> tuple[str, str, str]:
        """Parse the CAPTURED JSON into ``(name, description, content)``.

        Same five-layer fallback strategy as
        :meth:`_parse_analysis_response` but with key names
        tailored for the CAPTURED schema. On any total failure
        the raw response becomes the content body and the
        name / description are derived from the first 5 words /
        first sentence of that body — enough for the skill row
        to be BM25-searchable and for a human reviewer to
        recognize it as an auto-capture.

        Args:
            raw: The raw LLM response.

        Returns:
            Tuple of ``(name, description, content)`` strings.
        """
        text = _THINK_BLOCK_RE.sub("", raw or "")

        parsed: Optional[dict] = None

        # Layer 2: fenced JSON.
        fenced = _FENCED_OBJ_RE.search(text)
        if fenced:
            try:
                candidate = json.loads(fenced.group(1))
                if isinstance(candidate, dict):
                    parsed = candidate
            except Exception:
                parsed = None

        # Layer 3: bare JSON object.
        if parsed is None:
            for match in _JSON_OBJ_RE.finditer(text):
                candidate_str = match.group(0)
                try:
                    candidate = json.loads(candidate_str)
                except Exception:
                    continue
                if isinstance(candidate, dict):
                    parsed = candidate
                    break

        if parsed is not None:
            name = str(parsed.get("name", "") or "").strip()
            description = str(parsed.get("description", "") or "").strip()
            content = str(parsed.get("content", "") or "").strip()
            if name and description and content:
                return name, description, content
            # Partial fill — fall through to the prose fallback
            # rather than committing a half-empty skill.

        # Layer 4: key:value prose fallback.
        m_name = _RE_KV_NAME.search(text)
        m_desc = _RE_KV_DESC.search(text)
        m_content = _RE_KV_CONTENT.search(text)
        if m_name and m_content:
            name = m_name.group(1).strip()
            description = (
                m_desc.group(1).strip() if m_desc else name
            )
            content = m_content.group(1).strip()
            return name, description, content

        # Layer 5: total failure — derive from raw text.
        body = (raw or "").strip()
        if not body:
            body = "(empty LLM response)"
        first_words = " ".join(body.split()[:5]) or "captured-skill"
        first_sentence = body.split(".")[0].strip() or body[:120]
        return first_words[:64], first_sentence[:256], body

    # --------------------------------------------------------
    # LLM: content generation for FIX / DERIVED
    # --------------------------------------------------------

    async def _generate_evolved_content(self, skill: Any, direction: str) -> str:
        """Ask the evolution model to produce a new skill body.

        Single user-role prompt with the source skill's content
        (truncated to 3000 chars to fit most context windows)
        and the analysis-supplied direction. The LLM is told
        to return only the new markdown body — no commentary.

        Args:
            skill: The source :class:`Skill` row.
            direction: Short instruction from the Tier 2 analysis.

        Returns:
            The raw response text. ``"(no content generated)"``
            if the LLM returned empty — the caller still creates
            a (broken) skill row so lineage stays consistent.
        """
        prompt = (
            "You are an expert skill author. The following skill "
            "needs evolution.\n"
            f"Reason / direction: {direction}\n\n"
            f"Current skill name: {skill.name}\n"
            f"Current description: {skill.description}\n"
            "Current content:\n"
            f"{(getattr(skill, 'content', '') or '')[:3000]}\n\n"
            "Produce an improved version of the skill content as "
            "markdown. Address the direction above. Return ONLY "
            "the new markdown content — no preamble, no code fences, "
            "no commentary."
        )
        raw = await self._call_llm(
            prompt, model=self._resolve_evolution_model()
        )
        return raw.strip() or "(no content generated)"

    # --------------------------------------------------------
    # Diff utility
    # --------------------------------------------------------

    @staticmethod
    def _compute_diff(old_content: str, new_content: str) -> str:
        """Return a unified diff between two skill content bodies.

        Uses :func:`difflib.unified_diff` so the diff is
        human-readable in logs and stored verbatim on the
        :class:`SkillLineage` row for later auditing.

        Args:
            old_content: The pre-evolution body.
            new_content: The post-evolution body.

        Returns:
            The unified diff as a single string (may be empty
            when the two bodies are identical).
        """
        diff = difflib.unified_diff(
            (old_content or "").splitlines(keepends=True),
            (new_content or "").splitlines(keepends=True),
            fromfile="old",
            tofile="new",
        )
        return "".join(diff)