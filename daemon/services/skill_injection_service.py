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
from typing import Any, Callable

logger = logging.getLogger(__name__)


# Status values that mean "this variant can still be picked by A/B
# selection". ``inactive`` / ``archived`` variants are filtered out
# so the picker only sees live candidates.
_ACTIVE_AB_STATUSES: frozenset[str] = frozenset({"active", "ab_testing"})


# Type alias for the injectable capability-resolution callables.
# The production caller (manager.py) wires these to thin closures
# over ``McpServerRepository``, the agent's effective ``tools.allow``
# set, and a ``dict(environ)`` snapshot — see the call site for
# details. Tests inject in-memory fakes so the surface stays
# hermetic.
RequirementLookupFn = Callable[[Any], "object | None"]
# ^ ``object`` is ``CapabilityRequirement | None`` from
# ``daemon.services.capability_resolver``. Typed loosely here to
# avoid an import cycle (capability_resolver does NOT depend on this
# module; this module depends on it lazily).
McpLookupFn = Callable[[str], "object | None"]
ToolsAllowFn = Callable[[], list[str]]
EnvLookupFn = Callable[[], dict[str, str]]


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
        _clone_service: Duck-typed
            :class:`~daemon.services.skill_clone_service.SkillCloneService`
            or ``None``. Wired in AFTER construction via
            :meth:`set_clone_service` to avoid the manager-init
            chicken-and-egg (clone service needs the embedding
            service, which itself depends on Phase 2 wiring that
            the injection service also needs). When ``None``,
            explicit-skill injection falls back to a direct
            repository lookup.
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
        requirement_lookup: RequirementLookupFn | None = None,
        mcp_lookup: McpLookupFn | None = None,
        tools_allow: ToolsAllowFn | None = None,
        env_lookup: EnvLookupFn | None = None,
    ) -> None:
        """Store the search service, config, and A/B repositories.

        Args:
            search_service: See :attr:`_search_service`.
            config: See :attr:`_config`.
            ab_test_repo: See :attr:`_ab_test_repo`.
            skill_repo: See :attr:`_skill_repo`.
            requirement_lookup: P3-WP2 wiring fix — callable that
                resolves a :class:`Skill` to its parsed
                :class:`CapabilityRequirement` (or ``None`` when the
                skill carries no ``requires:`` block). Production
                wires this to a closure over ``SkillBankRepository``
                that reads ``Skill.source_skill_bank_id`` and parses
                the bank row's ``requirement_json``. When ``None`` the
                gate is a no-op (zero behavior change — every existing
                skill has empty requirement; the resolve path is
                skipped entirely).
            mcp_lookup: Injectable MCP-server lookup forwarded to
                ``capability_check``. ``None`` falls through to
                ``missing`` (no MCP ever installed).
            tools_allow: Injectable accessor for the agent's effective
                ``tools.allow`` list.
            env_lookup: Injectable accessor for the merged environment
                mapping.
        """
        self._search_service = search_service
        self._config = config
        self._ab_test_repo = ab_test_repo
        self._skill_repo = skill_repo
        # Injected post-construction via ``set_clone_service`` —
        # the manager builds the clone service after this service
        # (it depends on the embedding service that the injection
        # service itself doesn't need at init time). Keeping this
        # ``None`` here means explicit-skill injection falls back
        # to a direct ``get_by_name`` lookup when the clone path
        # hasn't been wired yet (e.g. older test fixtures).
        self._clone_service: Any = None
        # Per-instance, per-message — the Phase 4 metrics service
        # queries this to attribute a feedback signal back to
        # the skills that were offered for the task.
        self._injected_skills: dict[str, dict[str, list[str]]] = {}
        # P3-WP2 wiring fix — capability gate (proof-b). All four
        # are optional; when ANY is missing the gate is a no-op
        # (the requirement lookup runs but produces no envelope
        # because we can't check the requirement). The full set is
        # wired by ``EnsembleManager`` so day-1 production runs the
        # gate; tests / fixtures can opt in selectively.
        self._requirement_lookup: RequirementLookupFn | None = (
            requirement_lookup
        )
        self._mcp_lookup: McpLookupFn | None = mcp_lookup
        self._tools_allow: ToolsAllowFn | None = tools_allow
        self._env_lookup: EnvLookupFn | None = env_lookup

    # --------------------------------------------------------
    # Construction-time setters (avoid init-order chicken-and-egg)
    # --------------------------------------------------------

    def set_clone_service(self, clone_service: Any) -> None:
        """Inject the ``SkillCloneService`` after construction (W1 fix).

        Called by :class:`EnsembleManager` once the clone service
        exists. Avoids the chicken-and-egg where the injection
        service would need the clone service at construction
        time but the clone service is built later in the manager
        init sequence (it depends on the embedding service which
        is itself constructed between the two).

        Args:
            clone_service: :class:`SkillCloneService` instance.
                Typically ``self._skill_clone_service`` on the
                manager.
        """
        self._clone_service = clone_service

    def set_capability_gate(
        self,
        *,
        requirement_lookup: RequirementLookupFn | None = None,
        mcp_lookup: McpLookupFn | None = None,
        tools_allow: ToolsAllowFn | None = None,
        env_lookup: EnvLookupFn | None = None,
    ) -> None:
        """Wire the P3-WP2 capability gate after construction.

        Mirrors the :meth:`set_clone_service` pattern — the lookup
        callables aren't available until the manager has fully built
        the surrounding services (MCP server repository, agent meta,
        env). Each argument is optional; passing ``None`` leaves the
        existing wiring untouched. Passing a callable REPLACES the
        existing wiring for that slot (idempotent re-wiring for
        test fixtures).

        Args:
            requirement_lookup: Callable that resolves a ``Skill`` →
                ``CapabilityRequirement | None``. Production wires a
                closure over ``SkillBankRepository`` that reads
                ``source_skill_bank_id`` and parses
                ``requirement_json``.
            mcp_lookup: Callable that resolves a capability_id → MCP
                row projection (``McpLookupResult | None``). Forwarded
                to ``capability_check``.
            tools_allow: Callable returning the agent's effective
                ``tools.allow`` list. Forwarded to ``capability_check``.
            env_lookup: Callable returning the merged env mapping.
                Forwarded to ``capability_check``.
        """
        if requirement_lookup is not None:
            self._requirement_lookup = requirement_lookup
        if mcp_lookup is not None:
            self._mcp_lookup = mcp_lookup
        if tools_allow is not None:
            self._tools_allow = tools_allow
        if env_lookup is not None:
            self._env_lookup = env_lookup

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
            # Stage 2.5 — capability pre-flight (P3-WP2 wiring fix,
            # proof-b). For each routed skill carrying a non-empty
            # requirement, run ``capability_check`` BEFORE composing
            # the injected content. Missing/unconfigured → prepend a
            # capability pre-flight block (per skill convention: the
            # first thing the agent sees is a clearly-marked
            # escalation envelope + the action required of it). The
            # skill body still renders after the block so a present
            # capability does not lose context. Failure of the check
            # itself (resolver error) → inject the skill WITHOUT the
            # block and log a warning; never block injection on a
            # resolver fault.
            preflight_block = await self._capability_preflight_block(
                selected
            )
            routed_injected.append({
                "skill": selected,
                "score": score,
                "preflight_block": preflight_block,
            })

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

    async def inject_explicit_skill(
        self,
        skill_name: str,
        project_id: str | None,
        instance_id: str,
        message_id: str,
        agent_id: str,
    ) -> tuple[str | None, list[str]]:
        """Bypass search; directly inject a named skill via clone-on-miss.

        Resolves the skill through :class:`SkillCloneService`
        (preferred — clone-on-miss from the bank) or a direct
        repository lookup as a fallback. Then runs the same A/B
        routing + formatting pipeline as :meth:`inject_skills`,
        but skips the BM25→embedding→LLM search and forces a
        score of ``1.0`` (the caller asked for this skill by
        name, so relevance is presumed).

        Used when an agent (or the manager) needs to surface a
        specific skill without paying the search cost — e.g.
        forcing ``dynamic-skill`` at the start of a session, or
        auto-loading a skill the prompt composition pipeline
        flagged as required for this agent+project pair.

        Args:
            skill_name: The skill name to inject (lookup key in
                both the ``skills`` and ``skill_bank`` tables).
            project_id: Project scope. ``None`` or empty
                short-circuits to ``(None, [])`` — explicit
                injection always needs a project scope.
            instance_id: The receiving instance. Used as part
                of the deterministic A/B hash.
            message_id: Queue message ID. Paired with
                ``instance_id`` for A/B determinism.
            agent_id: Owning agent — used by
                :meth:`SkillCloneService.clone_on_miss_sync` to
                disambiguate the bank-template lookup (multiple
                agents can own templates with the same name).

        Returns:
            Tuple ``(injection_text, injected_skill_ids)``.
            ``(None, [])`` when ``skill_name``/``project_id`` is
            empty or the skill cannot be resolved from the
            project scope or skill bank.
        """
        # Early-exit — explicit injection always needs a
        # project scope and a name to look up. Keeping the
        # rest of the method focused on the happy path.
        if not project_id or not skill_name:
            return (None, [])

        # Stage 1 — resolve the skill. Prefer the clone
        # service so a miss in the project scope gets
        # materialized from the bank automatically; fall back
        # to a direct lookup when the clone service hasn't
        # been wired (older manager init paths, test fixtures,
        # or pre-Phase 4 deployments).
        try:
            if self._clone_service is not None:
                skill = await asyncio.to_thread(
                    self._clone_service.clone_on_miss_sync,
                    skill_name, agent_id, project_id,
                )
            else:
                skill = await asyncio.to_thread(
                    self._skill_repo.get_by_name,
                    project_id, skill_name, 0,
                )
        except Exception as e:
            if self._clone_service is not None:
                logger.warning(
                    f"[SkillInjection] clone-on-miss failed for "
                    f"'{skill_name}' (agent={agent_id}): {e}"
                )
            else:
                logger.warning(
                    f"[SkillInjection] skill lookup failed for "
                    f"'{skill_name}': {e}"
                )
            return (None, [])

        if skill is None:
            logger.warning(
                f"[SkillInjection] Skill '{skill_name}' not "
                f"found in project {project_id[:8]}... or "
                f"skill bank"
            )
            return (None, [])

        # Stage 2 — A/B variant routing. Same pattern as
        # ``inject_skills``: deterministic per-(instance,
        # message) pick with best-effort fallback to the
        # original skill. A ``selected`` that resolves back to
        # ``skill`` (no A/B active, or DB error) is fine — the
        # formatter and ID collection both handle that.
        selected = await self._select_ab_variant(
            skill, instance_id, message_id
        )

        # Stage 2.5 — capability pre-flight (P3-WP2 wiring fix,
        # proof-b). Same semantics as ``inject_skills``: a skill
        # carrying a non-empty requirement runs ``capability_check``
        # BEFORE the formatter; missing/unconfigured → prepend the
        # pre-flight block; resolver failure → log + inject without
        # the block (never block injection on a resolver fault).
        preflight_block = await self._capability_preflight_block(selected)

        # Stage 3 — format. Explicit injection forces a 1.0
        # score since relevance is presumed (caller asked by
        # name); ``low_match`` is empty because we did not run
        # the search pipeline.
        injection_text = self._format_injection(
            {
                "injected": [
                    {
                        "skill": selected,
                        "score": 1.0,
                        "preflight_block": preflight_block,
                    }
                ],
                "low_match": [],
            }
        )

        # ``getattr`` on ``id`` so a mock skill without an id
        # attribute (test fixture) doesn't crash the injector —
        # matches the defensive pattern in ``inject_skills``.
        selected_id = getattr(selected, "id", None)
        skill_ids: list[str] = (
            [str(selected_id)] if selected_id is not None else []
        )

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

    # ──────────────────────────────────────────────────────────────
    # P3-WP2 wiring fix — capability pre-flight (proof-b)
    # ──────────────────────────────────────────────────────────────
    # Per skill convention: a skill body declaring ``requires:``
    # MUST begin with ``capability_check(...)``; the result of the
    # check is what tells the agent how to proceed (present → run
    # the body; missing/unconfigured → escalate to install).
    #
    # The injector runs the check BEFORE composing the injected
    # content and, when the result is not ``present``, prepends a
    # clearly-marked block carrying the escalation envelope. The
    # block is the load-bearing mechanism that lets a live agent
    # avoid the failure mode observed on proof (b) run 2 (worker
    # instance 498ca5b2 on :8081): the agent guessed at a missing
    # tool inventory, invented a non-schema envelope kind, and
    # skipped the install step entirely.
    #
    # Failure policy: resolver errors are caught, logged, and the
    # skill is injected WITHOUT the block (never block injection on
    # a resolver fault — the skill is still usable, just without the
    # gate).
    # ──────────────────────────────────────────────────────────────

    async def _capability_preflight_block(
        self,
        skill: Any,
    ) -> str:
        """Run ``capability_check`` for a skill's declared requirements.

        Returns a clearly-marked markdown block to prepend before
        the skill body when at least one capability is not
        ``present``. Returns ``""`` when the gate is a no-op (no
        requirement, capability is present, lookup not wired, or
        resolver error).

        The block is rendered from the existing
        :class:`EscalationEnvelope` + :func:`format_envelope_message`
        helpers — the wire format (``Result: <json>`` with the seven
        §7.2 fields) is the contract the consumer side already
        parses.

        Args:
            skill: The skill being injected (post A/B routing).

        Returns:
            Markdown text to prepend before ``skill.content`` in
            the injection block. Empty string when no block is
            needed (gate is a no-op OR the check returns
            ``present``).
        """
        # Fast path — no wiring → gate is a no-op. This is the
        # back-compat default for tests / fixtures / pre-Phase-3
        # deployments that don't supply the lookups. Zero behavior
        # change: the skill injects as today.
        if (
            self._requirement_lookup is None
            or self._mcp_lookup is None
            or self._tools_allow is None
            or self._env_lookup is None
        ):
            return ""

        # Resolve the requirement for this skill. The production
        # closure reads ``Skill.source_skill_bank_id`` and parses
        # the bank row's ``requirement_json``. None / empty →
        # back-compat (every existing repo skill lands here; the
        # gate is a no-op).
        try:
            requirement = self._requirement_lookup(skill)
        except Exception as e:
            logger.warning(
                f"[SkillInjection] requirement_lookup raised for skill "
                f"{getattr(skill, 'name', '?')!r}: {e}; injecting "
                f"without pre-flight block."
            )
            return ""

        if requirement is None:
            return ""
        # Defensive: the lookup may return anything callable-shaped
        # in tests; we only care about the recognized interface.
        is_empty = getattr(requirement, "is_empty", None)
        if callable(is_empty) and is_empty():
            return ""

        # Lazy import to keep the module surface lightweight and
        # to avoid a circular dep at module load time.
        from .capability_resolver import (
            CapabilityCheckResult,
            EscalationEnvelope,
            capability_check,
            format_envelope_message,
        )

        skill_name = getattr(skill, "name", "?") or "?"
        # Run capability_check across every declared capability on
        # every axis (mcp, tools, env). First non-present result
        # wins (most informative evidence) and becomes the
        # envelope. The check is a DB read — no LLM, no
        # subprocess, fast (p95 < 50ms per WP2 contract).
        checks: list[CapabilityCheckResult] = []
        mcp_ids = list(getattr(requirement, "mcp", []) or [])
        tool_ids = list(getattr(requirement, "tools", []) or [])
        env_ids = list(getattr(requirement, "env", []) or [])
        for mcp_id in mcp_ids:
            try:
                result = capability_check(
                    mcp_id,
                    mcp_lookup=self._mcp_lookup,
                    tools_allow=self._tools_allow,
                    env_lookup=self._env_lookup,
                    capability_kind="mcp",
                )
            except Exception as e:
                logger.warning(
                    f"[SkillInjection] capability_check raised for "
                    f"mcp={mcp_id!r} (skill={skill_name!r}): {e}; "
                    f"injecting without pre-flight block."
                )
                return ""
            checks.append(result)
        for tool_id in tool_ids:
            try:
                result = capability_check(
                    tool_id,
                    mcp_lookup=self._mcp_lookup,
                    tools_allow=self._tools_allow,
                    env_lookup=self._env_lookup,
                    capability_kind="tools",
                )
            except Exception as e:
                logger.warning(
                    f"[SkillInjection] capability_check raised for "
                    f"tools={tool_id!r} (skill={skill_name!r}): {e}; "
                    f"injecting without pre-flight block."
                )
                return ""
            checks.append(result)
        for env_id in env_ids:
            try:
                result = capability_check(
                    env_id,
                    mcp_lookup=self._mcp_lookup,
                    tools_allow=self._tools_allow,
                    env_lookup=self._env_lookup,
                    capability_kind="env",
                )
            except Exception as e:
                logger.warning(
                    f"[SkillInjection] capability_check raised for "
                    f"env={env_id!r} (skill={skill_name!r}): {e}; "
                    f"injecting without pre-flight block."
                )
                return ""
            checks.append(result)

        # First non-present check wins. ``present`` everywhere →
        # no block (the gate confirms the capability is live; the
        # skill body renders as today).
        non_present = next(
            (c for c in checks if c.state != "present"), None
        )
        if non_present is None:
            return ""

        # Build the envelope. ``installer_skill`` is advisory —
        # the resolver doesn't know the install path; we use the
        # capability_id itself as the default installer lookup
        # hint. The escalation protocol's consumer (designer
        # agent) owns the registry of installer_skill mappings.
        try:
            envelope = EscalationEnvelope.from_check(
                non_present,
                installer_skill=non_present.capability_id,
                blocker_scope="this_task",
                resume_hint="step_after_install",
            )
        except Exception as e:
            logger.warning(
                f"[SkillInjection] envelope construction failed for "
                f"{non_present.capability_id!r} (skill={skill_name!r}): "
                f"{e}; injecting without pre-flight block."
            )
            return ""
        envelope_text = format_envelope_message(envelope).rstrip("\n")
        state_label = non_present.state  # "missing" or "unconfigured"
        # Compose the clearly-marked block. The header is fixed so
        # downstream consumers (the agent, the report verifier)
        # can identify it without parsing the body. ``REQUIRED
        # ACTION`` is the single-line directive the agent must
        # honor (per the proof-b convention: emit exactly one
        # ``Result: <json>`` envelope then STOP — do not attempt
        # the skill body).
        missing_subkeys = list(
            getattr(non_present, "missing_subkeys", []) or []
        )
        subkey_line = (
            f"missing_subkeys: {missing_subkeys}\n"
            if missing_subkeys
            else ""
        )
        block = (
            f"[Capability Pre-flight: {non_present.capability_id} "
            f"= {state_label}]\n"
            f"detection_evidence: {non_present.detection_evidence}\n"
            f"{subkey_line}"
            f"REQUIRED ACTION (skill convention): your FIRST report "
            f"must be the escalation envelope — emit exactly "
            f"`Result: <single-line JSON>` with the "
            f"EscalationEnvelope schema (kind="
            f"{('capability_missing' if state_label == 'missing' else 'installed_but_unconfigured')};"
            f" capability={non_present.capability_id}; "
            f"installer_skill={envelope.installer_skill}; "
            f"detection_evidence={envelope.detection_evidence}; "
            f"blocker_scope=this_task; "
            f"resume_hint=step_after_install; "
            f"policy_denied_reason=null; ts={envelope.ts}) — then "
            f"STOP; do not attempt the skill body.\n"
            f"{envelope_text}\n"
        )
        return block

    def _format_injection(
        self,
        results: dict[str, list[dict[str, Any]]],
    ) -> str:
        """Render the search results as a markdown injection block.

        Format::

            [System Inject] Relevant skills loaded:

            📋 **Skill: {name}** (id: {skill.id}, match score: {score:.2f})
            ────────────────────────────
            {full_markdown_content}

            📋 **Other available skills** (low match):
            • {name2} ({id2}, score: {score2:.2f}) — {description2}

            Use `skill_search` tool to find more skills.

        Each skill's UUID4 ``id`` is rendered inline alongside the
        name and score so the consuming agent can pass it directly
        into ``skill_feedback(skill_id=...)`` /
        ``skill_fix(skill_id=...)`` /
        ``skill_view(skill_id=...)`` without paying for an extra
        ``skill_search`` round-trip just to resolve the name back to
        its UUID. Low-match candidates get the same ``id`` exposure
        so they remain one tool-call away if the agent decides to
        promote a low-match skill.

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
        * Skill IDs are read via ``getattr(skill, "id", None)``
          so missing-id fixtures (test mocks) don't crash the
          formatter — the ``(id: …)`` segment is omitted when
          the id is falsy.
        * When an injected item carries a non-empty
          ``preflight_block`` (P3-WP2 wiring fix), the block is
          rendered BEFORE the skill body so the agent sees the
          capability gate as the first thing in that skill's
          section. The block is clearly-marked (``[Capability
          Pre-flight: …]``) and ends with a ``REQUIRED ACTION``
          line so the agent can identify it without parsing.

        Args:
            results: Dict with ``injected`` and ``low_match``
                lists. See :meth:`SkillSearchService.search` for
                per-item shape. Injected items MAY carry a
                ``preflight_block`` key (string) added by
                :meth:`_capability_preflight_block` — when truthy,
                the formatter prepends it to the skill body.

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
            skill_id = getattr(skill, "id", None)
            preflight_block = item.get("preflight_block") or ""
            # Inline the skill ID next to the name + score so the
            # consuming agent has every signal it needs to call
            # ``skill_feedback`` / ``skill_fix`` / ``skill_view``
            # in ONE tool call — doesn't have to resolve the
            # name→UUID via ``skill_search``. When the id is
            # missing (test fixtures, legacy rows), the segment is
            # omitted entirely so the layout stays legible.
            if skill_id:
                lines.append(
                    f"📋 **Skill: {name}** "
                    f"(id: {skill_id}, match score: {score_val:.2f})"
                )
            else:
                lines.append(
                    f"📋 **Skill: {name}** (match score: {score_val:.2f})"
                )
            lines.append("─" * 30)
            # P3-WP2 wiring fix — capability pre-flight block. When
            # a skill's requirement returned ``missing`` /
            # ``unconfigured``, the block is prepended to the body
            # so the agent sees the escalation envelope BEFORE the
            # skill instructions (the skill's first-line directive
            # is the capability_check call, which would otherwise
            # be the first thing the agent reads; the block makes
            # the gate state explicit and unambiguous).
            if preflight_block:
                lines.append(preflight_block)
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
                # Surface the low-match skill's id too so a
                # promotion decision emits ``skill_view`` /
                # ``skill_feedback`` with the id in one call rather
                # than re-running ``skill_search`` just to look it
                # up. ``item.get("id")`` matches the dict shape
                # used by the search service for low-match rows;
                # missing or falsy → omit gracefully.
                item_id = item.get("id")
                if item_id:
                    lines.append(
                        f"• {name} ({item_id}, score: {score_val:.2f})"
                        f" — {description}"
                    )
                else:
                    lines.append(
                        f"• {name} ({score_val:.2f}) — {description}"
                    )
            lines.append("")

        # Closing hint — always present so the agent knows it
        # can expand the search with the dedicated tool.
        lines.append("Use `skill_search` tool to find more skills.")

        return "\n".join(lines)
