"""Tests for ``SkillInjectionService`` (Phase 3 of Skill Evolution).

Tests the three-stage skill-injection pipeline:

* **Stage 1 — search delegation.** ``inject_skills`` forwards
  ``user_message``, ``project_id``, and ``max_results`` (from
  :attr:`SkillEvolutionConfig.max_inject_skills`) to
  :class:`SkillSearchService.search` and reads back the
  ``{"injected": [...], "low_match": [...]}`` shape.
* **Stage 2 — A/B variant routing.** ``_select_ab_variant``
  filters sibling variants to ``{"active", "ab_testing"}``
  statuses, picks one deterministically via
  ``md5(f"{instance_id}:{message_id}:{ab_group}")`` mod
  ``len(variants)``, and bumps the per-group comparison counter
  via ``increment_comparison``. Failures of either DB call fall
  back to the original skill.
* **Stage 3 — formatting.** ``_format_injection`` renders the
  results into a ``[System Inject]`` block with a unicode
  box-drawing separator (``─``, U+2500) and a closing hint.

Also covers:

* The in-memory ``track_injection`` /
  ``get_injected_skill_ids`` cache for Phase 4 metrics
  attribution.
* The ``_build_graph_input`` helper from
  :mod:`daemon.services.instance_messaging` that prepends the
  skill-injection ``HumanMessage`` to the graph-input message
  list.

All repository and search-service calls are mocked — no DB,
no network traffic.
"""

from __future__ import annotations

import hashlib
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from langchain_core.messages import HumanMessage

from daemon.services.instance_messaging import _build_graph_input
from daemon.services.skill_injection_service import SkillInjectionService


# ============================================================
# Fixtures / helpers
# ============================================================


def make_skill(
    *,
    skill_id: str = "skill-1",
    name: str = "code-review",
    description: str = "Review code for bugs and style.",
    content: str = "# Code Review\n\nLook for bugs.",
    project_id: str | None = None,
    is_active: bool = True,
    status: str = "active",
    ab_test_group: str | None = None,
) -> SimpleNamespace:
    """Build a minimal stand-in for a :class:`Skill` row.

    Uses :class:`SimpleNamespace` rather than ``MagicMock(spec=...)``
    so attribute access follows real-class semantics — this
    matters for ``getattr(skill, "name", "")`` fallback paths
    in the service.
    """
    return SimpleNamespace(
        id=skill_id,
        name=name,
        description=description,
        content=content,
        project_id=project_id,
        is_active=is_active,
        status=status,
        ab_test_group=ab_test_group,
    )


def make_config(*, max_inject_skills: int = 2) -> MagicMock:
    """Build a mock :class:`SkillEvolutionConfig`."""
    cfg = MagicMock(spec=["max_inject_skills"])
    cfg.max_inject_skills = max_inject_skills
    return cfg


def make_search_service(
    *,
    search_return: dict | None = None,
    search_side_effect: Exception | None = None,
) -> MagicMock:
    """Build a mock :class:`SkillSearchService`.

    ``search`` is ``AsyncMock`` — the production service exposes
    it as ``async``. ``search_return`` defaults to an empty dict
    (no injected, no low_match).
    """
    service = MagicMock()
    if search_side_effect is not None:
        service.search = AsyncMock(side_effect=search_side_effect)
    else:
        service.search = AsyncMock(
            return_value=search_return
            if search_return is not None
            else {"injected": [], "low_match": []}
        )
    return service


def make_ab_test_repo(
    *,
    increment_side_effect: Exception | None = None,
) -> MagicMock:
    """Build a mock :class:`SkillABTestRepository`.

    ``increment_comparison`` is a regular ``MagicMock`` (the
    service wraps it in ``asyncio.to_thread`` so a sync callable
    is sufficient).
    """
    repo = MagicMock()
    if increment_side_effect is not None:
        repo.increment_comparison = MagicMock(side_effect=increment_side_effect)
    else:
        repo.increment_comparison = MagicMock(return_value=None)
    return repo


def make_skill_repo(
    *,
    get_ab_variants_return: list | None = None,
    get_ab_variants_side_effect: Exception | None = None,
) -> MagicMock:
    """Build a mock :class:`SkillRepository`.

    ``get_ab_variants`` is a regular ``MagicMock`` (the service
    wraps it in ``asyncio.to_thread``).
    """
    repo = MagicMock()
    if get_ab_variants_side_effect is not None:
        repo.get_ab_variants = MagicMock(side_effect=get_ab_variants_side_effect)
    else:
        repo.get_ab_variants = MagicMock(
            return_value=get_ab_variants_return
            if get_ab_variants_return is not None
            else []
        )
    return repo


def make_service(
    *,
    search_service: MagicMock | None = None,
    config: MagicMock | None = None,
    ab_test_repo: MagicMock | None = None,
    skill_repo: MagicMock | None = None,
    max_inject_skills: int = 2,
) -> SkillInjectionService:
    """Construct a :class:`SkillInjectionService` with sensible defaults."""
    return SkillInjectionService(
        search_service=search_service
        if search_service is not None
        else make_search_service(),
        config=config if config is not None else make_config(max_inject_skills=max_inject_skills),
        ab_test_repo=ab_test_repo if ab_test_repo is not None else make_ab_test_repo(),
        skill_repo=skill_repo if skill_repo is not None else make_skill_repo(),
    )


# ============================================================
# TestInjectSkillsBasicFlow
# ============================================================


class TestInjectSkillsBasicFlow:
    """Happy-path ``inject_skills`` delegation to the search service."""

    @pytest.mark.asyncio
    async def test_inject_with_results_returns_formatted_text_and_ids(self):
        # Search returns one injected + one low_match. The service
        # must format both into the injection text and return the
        # injected skill's id in ``skill_ids``.
        skill = make_skill(skill_id="s1", name="review", content="review body")
        results = {
            "injected": [{"skill": skill, "score": 0.95}],
            "low_match": [
                {"name": "low-1", "score": 0.4, "description": "low desc"}
            ],
        }
        service = make_service(
            search_service=make_search_service(search_return=results)
        )

        text, ids = await service.inject_skills(
            "review my code",
            project_id="proj-1",
            instance_id="inst-1",
            message_id="msg-1",
        )

        # Both injected and low_match → non-None formatted text.
        assert text is not None
        # The injected section heading + skill name + score + body.
        assert "[System Inject]" in text
        assert "review" in text
        assert "0.95" in text
        assert "review body" in text
        # The low-match section is rendered.
        assert "Other available skills" in text
        assert "low-1" in text
        # Closing hint so the agent knows the search tool exists.
        assert "skill_search" in text
        # ``skill_ids`` reflects the injected skill id.
        assert ids == ["s1"]

    @pytest.mark.asyncio
    async def test_empty_results_returns_none_and_empty_list(self):
        # Empty search → caller can skip injection entirely.
        service = make_service(
            search_service=make_search_service(
                search_return={"injected": [], "low_match": []}
            )
        )

        text, ids = await service.inject_skills(
            "anything", project_id=None, instance_id="inst-1", message_id="msg-1"
        )

        assert text is None
        assert ids == []

    @pytest.mark.asyncio
    async def test_only_low_match_still_renders(self):
        # Only low_match candidates → the "other available skills"
        # section is independently useful; the service must
        # render it even though ``injected`` is empty.
        results = {
            "injected": [],
            "low_match": [
                {"name": "low-only", "score": 0.3, "description": "only low"},
            ],
        }
        service = make_service(
            search_service=make_search_service(search_return=results)
        )

        text, ids = await service.inject_skills(
            "query", project_id=None, instance_id="inst-1", message_id="msg-1"
        )

        assert text is not None
        assert "Other available skills" in text
        assert "low-only" in text
        # No injected → ids must be empty.
        assert ids == []

    @pytest.mark.asyncio
    async def test_max_results_from_config_passed_to_search(self):
        # ``config.max_inject_skills`` must reach the search
        # service as ``max_results``.
        search_service = make_search_service()
        service = make_service(
            search_service=search_service,
            config=make_config(max_inject_skills=5),
        )

        await service.inject_skills(
            "q", project_id=None, instance_id="i", message_id="m"
        )

        call_kwargs = search_service.search.call_args.kwargs
        assert call_kwargs.get("max_results") == 5

    @pytest.mark.asyncio
    async def test_project_id_forwarded_to_search(self):
        # ``project_id`` must be threaded through to
        # ``search_service.search`` for project-scoped queries.
        search_service = make_search_service()
        service = make_service(search_service=search_service)

        await service.inject_skills(
            "q", project_id="proj-42", instance_id="i", message_id="m"
        )

        call_kwargs = search_service.search.call_args.kwargs
        assert call_kwargs.get("project_id") == "proj-42"


# ============================================================
# TestABVariantSelection
# ============================================================


class TestABVariantSelection:
    """``_select_ab_variant`` gating and routing logic."""

    @pytest.mark.asyncio
    async def test_skill_without_ab_test_group_not_routed(self):
        # ``ab_test_group=None`` → fast path, no DB calls.
        skill = make_skill(ab_test_group=None, status="active")
        skill_repo = make_skill_repo()
        ab_test_repo = make_ab_test_repo()

        service = make_service(skill_repo=skill_repo, ab_test_repo=ab_test_repo)
        result = await service._select_ab_variant(skill, "i", "m")

        # Original skill returned unchanged.
        assert result is skill
        skill_repo.get_ab_variants.assert_not_called()
        ab_test_repo.increment_comparison.assert_not_called()

    @pytest.mark.asyncio
    async def test_skill_with_ab_test_group_but_wrong_status_not_routed(self):
        # ``status='active'`` is NOT in the routable set
        # (``ab_testing`` only) → no routing, no increment.
        skill = make_skill(ab_test_group="grp-1", status="active")
        skill_repo = make_skill_repo()
        ab_test_repo = make_ab_test_repo()

        service = make_service(skill_repo=skill_repo, ab_test_repo=ab_test_repo)
        result = await service._select_ab_variant(skill, "i", "m")

        assert result is skill
        skill_repo.get_ab_variants.assert_not_called()
        ab_test_repo.increment_comparison.assert_not_called()

    @pytest.mark.asyncio
    async def test_single_active_variant_no_increment(self):
        # Only one active variant → "effectively single-armed"
        # — the service returns the original and skips the
        # counter bump so stats don't get skewed by no-op picks.
        skill = make_skill(ab_test_group="grp-1", status="ab_testing")
        only_variant = make_skill(skill_id="only", status="ab_testing")
        skill_repo = make_skill_repo(get_ab_variants_return=[only_variant])
        ab_test_repo = make_ab_test_repo()

        service = make_service(skill_repo=skill_repo, ab_test_repo=ab_test_repo)
        result = await service._select_ab_variant(skill, "i", "m")

        # Original returned, no counter bump.
        assert result is skill
        ab_test_repo.increment_comparison.assert_not_called()

    @pytest.mark.asyncio
    async def test_multiple_active_variants_selects_and_increments(self):
        # Two active variants → router picks one deterministically
        # and bumps the counter.
        skill = make_skill(ab_test_group="grp-1", status="ab_testing")
        variant_a = make_skill(skill_id="var-a", status="ab_testing")
        variant_b = make_skill(skill_id="var-b", status="ab_testing")
        skill_repo = make_skill_repo(get_ab_variants_return=[variant_a, variant_b])
        ab_test_repo = make_ab_test_repo()

        service = make_service(skill_repo=skill_repo, ab_test_repo=ab_test_repo)
        result = await service._select_ab_variant(skill, "inst-x", "msg-y")

        # Selection must be one of the active variants.
        assert result in (variant_a, variant_b)
        # Counter bumped for the test group.
        ab_test_repo.increment_comparison.assert_called_once_with("grp-1")

    @pytest.mark.asyncio
    async def test_inactive_variants_filtered_out(self):
        # ``inactive`` variants must NOT participate in selection.
        # Provide an inactive + two active ones — only the active
        # pair drives the hash; the inactive one must not be
        # picked.
        skill = make_skill(ab_test_group="grp-1", status="ab_testing")
        inactive = make_skill(skill_id="inactive", status="inactive")
        active_a = make_skill(skill_id="active-a", status="active")
        active_b = make_skill(skill_id="active-b", status="ab_testing")
        # ``inactive`` deliberately listed FIRST to make sure
        # the filter actually runs.
        skill_repo = make_skill_repo(
            get_ab_variants_return=[inactive, active_a, active_b]
        )
        ab_test_repo = make_ab_test_repo()

        service = make_service(skill_repo=skill_repo, ab_test_repo=ab_test_repo)
        # Run a handful of different (instance, message) pairs
        # and verify we never pick the inactive one.
        for inst in ("a", "b", "c", "d", "e"):
            for msg in ("1", "2", "3"):
                result = await service._select_ab_variant(skill, inst, msg)
                assert result is not inactive, (
                    f"inactive variant picked for ({inst}, {msg})"
                )

    @pytest.mark.asyncio
    async def test_get_ab_variants_failure_falls_back_to_original(self, caplog):
        # DB failure on ``get_ab_variants`` is caught and the
        # original skill is used; the counter is NOT bumped.
        skill = make_skill(ab_test_group="grp-1", status="ab_testing")
        skill_repo = make_skill_repo(
            get_ab_variants_side_effect=RuntimeError("DB down")
        )
        ab_test_repo = make_ab_test_repo()

        service = make_service(skill_repo=skill_repo, ab_test_repo=ab_test_repo)

        with caplog.at_level("WARNING"):
            result = await service._select_ab_variant(skill, "i", "m")

        assert result is skill
        ab_test_repo.increment_comparison.assert_not_called()
        # Warning logged so an operator can diagnose.
        assert any("A/B variant fetch failed" in m for m in caplog.messages)


# ============================================================
# TestABDeterminism
# ============================================================


class TestABDeterminism:
    """Hash-based A/B variant selection — determinism contract."""

    @pytest.mark.asyncio
    async def test_same_input_selects_same_variant(self):
        # Same (instance, message, group) twice → same variant.
        # This is the core retry-stability promise.
        skill = make_skill(ab_test_group="grp-1", status="ab_testing")
        var_a = make_skill(skill_id="var-a", status="ab_testing")
        var_b = make_skill(skill_id="var-b", status="ab_testing")
        skill_repo = make_skill_repo(get_ab_variants_return=[var_a, var_b])
        ab_test_repo = make_ab_test_repo()

        service = make_service(skill_repo=skill_repo, ab_test_repo=ab_test_repo)

        r1 = await service._select_ab_variant(skill, "inst-1", "msg-1")
        r2 = await service._select_ab_variant(skill, "inst-1", "msg-1")
        assert r1 is r2

    @pytest.mark.asyncio
    async def test_different_instance_id_may_select_different(self):
        # With 2 variants, changing the instance_id participates
        # in the hash, so SOME pairs of instance_ids will land
        # on different variants. Verify the hash key genuinely
        # differs (not a smoke test, a math test).
        skill = make_skill(ab_test_group="grp-1", status="ab_testing")
        var_a = make_skill(skill_id="var-a", status="ab_testing")
        var_b = make_skill(skill_id="var-b", status="ab_testing")
        skill_repo = make_skill_repo(get_ab_variants_return=[var_a, var_b])
        ab_test_repo = make_ab_test_repo()

        service = make_service(skill_repo=skill_repo, ab_test_repo=ab_test_repo)

        seen: set[str] = set()
        for i in range(20):
            result = await service._select_ab_variant(skill, f"inst-{i}", "msg-1")
            seen.add(result.id)
        # With 2 variants and 20 hashes, we expect both to
        # appear (collision-free mod 2 over 20 samples has
        # probability ~1e-6).
        assert seen == {"var-a", "var-b"}, f"missing distribution: {seen}"

    @pytest.mark.asyncio
    async def test_different_message_id_may_select_different(self):
        # Same as above but vary message_id; same expectation.
        skill = make_skill(ab_test_group="grp-1", status="ab_testing")
        var_a = make_skill(skill_id="var-a", status="ab_testing")
        var_b = make_skill(skill_id="var-b", status="ab_testing")
        skill_repo = make_skill_repo(get_ab_variants_return=[var_a, var_b])
        ab_test_repo = make_ab_test_repo()

        service = make_service(skill_repo=skill_repo, ab_test_repo=ab_test_repo)

        seen: set[str] = set()
        for i in range(20):
            result = await service._select_ab_variant(skill, "inst-1", f"msg-{i}")
            seen.add(result.id)
        assert seen == {"var-a", "var-b"}, f"missing distribution: {seen}"

    @pytest.mark.asyncio
    async def test_hash_distribution_covers_all_variants(self):
        # Sanity check the modulo distributes picks evenly
        # across the variant list. Use 3 variants + 30 inputs.
        skill = make_skill(ab_test_group="grp-1", status="ab_testing")
        var_a = make_skill(skill_id="var-a", status="ab_testing")
        var_b = make_skill(skill_id="var-b", status="ab_testing")
        var_c = make_skill(skill_id="var-c", status="ab_testing")
        skill_repo = make_skill_repo(get_ab_variants_return=[var_a, var_b, var_c])
        ab_test_repo = make_ab_test_repo()

        service = make_service(skill_repo=skill_repo, ab_test_repo=ab_test_repo)

        seen: set[str] = set()
        for i in range(60):
            result = await service._select_ab_variant(skill, f"i-{i}", f"m-{i}")
            seen.add(result.id)
        # All three must show up over 60 samples.
        assert seen == {"var-a", "var-b", "var-c"}, f"missing distribution: {seen}"

    @pytest.mark.asyncio
    async def test_variants_sorted_by_id_before_selection(self):
        # Selection must be based on the SORTED-BY-ID order.
        # Provide variants in deliberately scrambled order and
        # verify the picked index matches a hand-computed hash
        # over the sorted-by-id list.
        skill = make_skill(ab_test_group="grp-1", status="ab_testing")
        # IDs sort lexicographically: ``c-99`` < ``c-z9`` < ``m-1``.
        var_a = make_skill(skill_id="m-1", status="ab_testing")
        var_b = make_skill(skill_id="c-99", status="ab_testing")
        var_c = make_skill(skill_id="c-z9", status="ab_testing")
        # Return in scrambled order (NOT sorted).
        skill_repo = make_skill_repo(get_ab_variants_return=[var_a, var_b, var_c])
        ab_test_repo = make_ab_test_repo()

        service = make_service(skill_repo=skill_repo, ab_test_repo=ab_test_repo)

        instance_id, message_id, ab_group = "inst-z", "msg-z", "grp-1"
        result = await service._select_ab_variant(
            skill, instance_id, message_id
        )

        # Recompute the expected index from the sorted-by-id list.
        sorted_variants = sorted(
            [var_a, var_b, var_c], key=lambda v: str(getattr(v, "id", ""))
        )
        hash_input = f"{instance_id}:{message_id}:{ab_group}".encode()
        hash_val = int(hashlib.md5(hash_input).hexdigest(), 16)
        expected = sorted_variants[hash_val % len(sorted_variants)]
        assert result is expected


# ============================================================
# TestFormatInjection
# ============================================================


class TestFormatInjection:
    """Markdown rendering of the injection block."""

    def test_empty_injected_and_empty_low_match(self):
        # Defensive path — the formatter always returns a string
        # (the ``inject_skills`` method gates the empty case
        # before calling it). Header + closing hint must still
        # be present.
        text = make_service()._format_injection(
            {"injected": [], "low_match": []}
        )
        assert isinstance(text, str)
        assert "[System Inject]" in text
        assert "skill_search" in text

    def test_one_injected_no_low_match(self):
        # Single injected skill → header + skill block + closing
        # hint, NO low-match section header.
        skill = make_skill(name="review", content="review body")
        text = make_service()._format_injection(
            {"injected": [{"skill": skill, "score": 0.9}], "low_match": []}
        )
        assert "[System Inject] Relevant skills loaded:" in text
        # Skill ID is inlined next to the name + score so the
        # consuming agent can call skill_feedback / skill_fix /
        # skill_view without an extra skill_search round-trip.
        # Default ``make_skill`` assigns ``id="skill-1"``.
        assert (
            "📋 **Skill: review** (id: skill-1, match score: 0.90)"
            in text
        )
        # Separator is emitted.
        assert "─" * 30 in text
        # Skill body.
        assert "review body" in text
        # Low-match section must be OMITTED (no candidates).
        assert "Other available skills" not in text
        # Closing hint.
        assert "Use `skill_search` tool to find more skills." in text

    def test_two_injected_two_low_match(self):
        # Both sections present with their respective formats.
        s1 = make_skill(skill_id="s1", name="alpha", content="alpha body")
        s2 = make_skill(skill_id="s2", name="beta", content="beta body")
        text = make_service()._format_injection(
            {
                "injected": [
                    {"skill": s1, "score": 0.95},
                    {"skill": s2, "score": 0.8},
                ],
                "low_match": [
                    {"name": "gamma", "id": "gamma-uuid",
                     "score": 0.4, "description": "gamma desc"},
                    {"name": "delta", "id": "delta-uuid",
                     "score": 0.3, "description": "delta desc"},
                ],
            }
        )
        # Injected headers + skill bodies + each skill's id is
        # inlined so the agent has the UUID ready for tool calls.
        assert "Skill: alpha" in text
        assert "(id: s1, match score: 0.95)" in text
        assert "Skill: beta" in text
        assert "(id: s2, match score: 0.80)" in text
        assert "alpha body" in text
        assert "beta body" in text
        # Low-match header + bullet format
        # ``• {name} ({id}, score: {score:.2f}) — {description}``.
        assert "Other available skills" in text
        assert "• gamma (gamma-uuid, score: 0.40) — gamma desc" in text
        assert "• delta (delta-uuid, score: 0.30) — delta desc" in text
        # Closing hint.
        assert "Use `skill_search` tool to find more skills." in text

    def test_separator_is_unicode_box_drawing(self):
        # The separator is exactly the unicode box-drawing char
        # ``─`` (U+2500) repeated 30 times. NOT an em-dash
        # (``—``, U+2014) or hyphen-minus (``-``, U+002D).
        skill = make_skill(content="body")
        text = make_service()._format_injection(
            {"injected": [{"skill": skill, "score": 0.5}], "low_match": []}
        )
        expected_sep = "─" * 30
        assert expected_sep in text
        # Explicit codepoint check: every separator char is
        # U+2500.
        for line in text.split("\n"):
            if line.startswith("─"):
                assert all(ch == "\u2500" for ch in line), (
                    f"non-box-drawing char in separator: {line!r}"
                )
                assert len(line) == 30

    def test_score_formatted_to_two_decimals(self):
        # Score must always render as 2-decimal float.
        skill = make_skill(content="x")
        text = make_service()._format_injection(
            {"injected": [{"skill": skill, "score": 0.95}], "low_match": []}
        )
        assert "0.95" in text
        # Also verify the rounding-up case (0.5 → 0.50).
        text2 = make_service()._format_injection(
            {"injected": [{"skill": skill, "score": 0.5}], "low_match": []}
        )
        assert "0.50" in text2

    def test_unnamed_skill_shows_placeholder(self):
        # Empty / None name → ``(unnamed)`` placeholder.
        for empty_name in ("", None):
            # Cast to the simple-str type the helper exposes
            # (None is the realistic edge case — a malformed
            # DB row with a NULL name). ``make_skill`` accepts
            # str; we coerce for the test fixture.
            name_for_helper: str = empty_name if empty_name is not None else ""
            skill = make_skill(name=name_for_helper, content="x")
            text = make_service()._format_injection(
                {"injected": [{"skill": skill, "score": 0.5}], "low_match": []}
            )
            assert "(unnamed)" in text

    def test_empty_content_renders_empty_line(self):
        # Empty content → the content line is present (and
        # empty between the separator and the trailing blank).
        skill = make_skill(content="", name="x")
        text = make_service()._format_injection(
            {"injected": [{"skill": skill, "score": 0.5}], "low_match": []}
        )
        # The separator must be followed by an empty content
        # line, then a blank.
        sep = "─" * 30
        idx = text.index(sep)
        after = text[idx + len(sep):]
        # First line after separator is the content (empty).
        first_line = after.split("\n", 1)[0]
        assert first_line == ""

    def test_closing_hint_always_present(self):
        # Regardless of input shape, the closing hint must be
        # the last line of the output.
        for results in (
            {"injected": [], "low_match": []},
            {"injected": [{"skill": make_skill(), "score": 0.5}], "low_match": []},
            {
                "injected": [],
                "low_match": [{"name": "x", "score": 0.3, "description": "d"}],
            },
        ):
            text = make_service()._format_injection(results)
            assert text.endswith(
                "Use `skill_search` tool to find more skills."
            ), f"closing hint not last line for {results}"


# ============================================================
# TestTrackingMethods
# ============================================================


class TestTrackingMethods:
    """In-memory ``track_injection`` / ``get_injected_skill_ids`` cache."""

    def test_track_and_retrieve(self):
        # Standard round-trip: track a list, retrieve it intact.
        service = make_service()
        service.track_injection("inst-1", "msg-1", ["skill-a", "skill-b"])
        assert service.get_injected_skill_ids("inst-1", "msg-1") == [
            "skill-a",
            "skill-b",
        ]

    def test_retrieve_unknown_returns_empty(self):
        # Unknown (instance, message) → empty list, no KeyError.
        service = make_service()
        assert service.get_injected_skill_ids("unknown", "unknown") == []

    def test_track_empty_list(self):
        # Tracking an empty list is a valid "nothing injected"
        # record and must be retrievable as ``[]``.
        service = make_service()
        service.track_injection("inst-1", "msg-1", [])
        assert service.get_injected_skill_ids("inst-1", "msg-1") == []

    def test_track_overwrites_previous(self):
        # Second ``track_injection`` for the same pair replaces
        # the first — no append, no list merge.
        service = make_service()
        service.track_injection("inst-1", "msg-1", ["a"])
        service.track_injection("inst-1", "msg-1", ["b"])
        assert service.get_injected_skill_ids("inst-1", "msg-1") == ["b"]

    def test_different_instances_isolated(self):
        # Different instance_ids maintain independent tracking.
        service = make_service()
        service.track_injection("inst-1", "msg-1", ["a"])
        service.track_injection("inst-2", "msg-1", ["b"])
        assert service.get_injected_skill_ids("inst-1", "msg-1") == ["a"]
        assert service.get_injected_skill_ids("inst-2", "msg-1") == ["b"]

    def test_get_returns_copy_not_reference(self):
        # Mutating the returned list must NOT affect the
        # internal storage — the Phase 4 service reads from
        # the same dict.
        service = make_service()
        service.track_injection("inst-1", "msg-1", ["a", "b"])
        result = service.get_injected_skill_ids("inst-1", "msg-1")
        result.append("evil-mutation")
        # Internal storage is unchanged.
        assert service.get_injected_skill_ids("inst-1", "msg-1") == ["a", "b"]


# ============================================================
# TestGracefulFailure
# ============================================================


class TestGracefulFailure:
    """Per-spec error handling: search propagates, A/B is caught."""

    @pytest.mark.asyncio
    async def test_search_failure_propagates(self):
        # Search failures are NOT swallowed — the caller
        # decides whether to fall back. The service must
        # re-raise.
        search_service = make_search_service(
            search_side_effect=RuntimeError("search broken")
        )
        service = make_service(search_service=search_service)

        with pytest.raises(RuntimeError, match="search broken"):
            await service.inject_skills(
                "q", project_id=None, instance_id="i", message_id="m"
            )

    @pytest.mark.asyncio
    async def test_increment_comparison_failure_does_not_crash(
        self, caplog
    ):
        # ``increment_comparison`` raising must NOT block the
        # injection — the variant was already picked, the
        # failure is logged, and the original skill is used
        # (which here equals the variant).
        skill = make_skill(ab_test_group="grp-1", status="ab_testing")
        var_a = make_skill(skill_id="var-a", status="ab_testing")
        var_b = make_skill(skill_id="var-b", status="ab_testing")
        skill_repo = make_skill_repo(get_ab_variants_return=[var_a, var_b])
        ab_test_repo = make_ab_test_repo(
            increment_side_effect=RuntimeError("increment failed")
        )
        results = {
            "injected": [{"skill": skill, "score": 0.9}],
            "low_match": [],
        }
        service = make_service(
            search_service=make_search_service(search_return=results),
            skill_repo=skill_repo,
            ab_test_repo=ab_test_repo,
        )

        with caplog.at_level("WARNING"):
            text, ids = await service.inject_skills(
                "q", project_id=None, instance_id="i", message_id="m"
            )

        # Injection completed normally — text rendered, ids
        # populated with the SELECTED variant's id (either
        # var-a or var-b; the increment failure happens AFTER
        # selection, so the chosen variant is what gets used).
        assert text is not None
        assert ids in (["var-a"], ["var-b"])
        # Warning logged so operators can see the increment
        # failure.
        assert any("increment" in m.lower() for m in caplog.messages)

    @pytest.mark.asyncio
    async def test_get_ab_variants_failure_does_not_crash(self, caplog):
        # ``get_ab_variants`` raising must NOT block injection.
        # The original skill is used as-is and the warning is
        # logged. ``increment_comparison`` must NOT be called
        # because we never got to the increment step.
        skill = make_skill(
            skill_id="orig",
            ab_test_group="grp-1",
            status="ab_testing",
            content="orig body",
        )
        skill_repo = make_skill_repo(
            get_ab_variants_side_effect=RuntimeError("DB down")
        )
        ab_test_repo = make_ab_test_repo()
        results = {
            "injected": [{"skill": skill, "score": 0.9}],
            "low_match": [],
        }
        service = make_service(
            search_service=make_search_service(search_return=results),
            skill_repo=skill_repo,
            ab_test_repo=ab_test_repo,
        )

        with caplog.at_level("WARNING"):
            text, ids = await service.inject_skills(
                "q", project_id=None, instance_id="i", message_id="m"
            )

        # Injection completed — the ORIGINAL skill is inlined.
        assert text is not None
        assert "orig body" in text
        assert ids == ["orig"]
        # Counter never bumped because variant lookup failed.
        ab_test_repo.increment_comparison.assert_not_called()
        # Warning logged.
        assert any("variant fetch failed" in m for m in caplog.messages)


# ============================================================
# TestInjectSkillsIntegration
# ============================================================


class TestInjectSkillsIntegration:
    """End-to-end ``inject_skills`` with A/B routing enabled."""

    @pytest.mark.asyncio
    async def test_full_flow_with_ab_routing(self):
        # Search returns an A/B-testing skill. ``get_ab_variants``
        # returns two active variants. Verify the SELECTED
        # variant (not the original) ends up in the injection
        # text and skill_ids.
        original = make_skill(
            skill_id="orig", name="original", ab_test_group="grp-1",
            status="ab_testing", content="original body",
        )
        var_a = make_skill(skill_id="var-a", name="variant-a",
                           status="ab_testing", content="variant a body")
        var_b = make_skill(skill_id="var-b", name="variant-b",
                           status="ab_testing", content="variant b body")
        skill_repo = make_skill_repo(get_ab_variants_return=[var_a, var_b])
        ab_test_repo = make_ab_test_repo()
        results = {
            "injected": [{"skill": original, "score": 0.95}],
            "low_match": [],
        }
        service = make_service(
            search_service=make_search_service(search_return=results),
            skill_repo=skill_repo,
            ab_test_repo=ab_test_repo,
        )

        text, ids = await service.inject_skills(
            "q", project_id=None, instance_id="inst-1", message_id="msg-1"
        )

        assert text is not None
        # Counter bumped for the test group.
        ab_test_repo.increment_comparison.assert_called_once_with("grp-1")
        # skill_ids must reflect the SELECTED variant, not the
        # original.
        assert ids in (["var-a"], ["var-b"])
        assert ids != ["orig"]
        # The injection text contains the selected variant's
        # body, not the original's.
        selected_body = "variant a body" if ids == ["var-a"] else "variant b body"
        assert selected_body in text
        assert "original body" not in text

    @pytest.mark.asyncio
    async def test_injected_skill_ids_match_routed_skills(self):
        # Two A/B-testing skills in the search result → both get
        # routed independently and the returned ``skill_ids`` are
        # the post-routing ids.
        original_1 = make_skill(
            skill_id="o-1", name="one",
            ab_test_group="grp-1", status="ab_testing",
        )
        original_2 = make_skill(
            skill_id="o-2", name="two",
            ab_test_group="grp-2", status="ab_testing",
        )
        var_1a = make_skill(skill_id="v1-a", status="ab_testing")
        var_1b = make_skill(skill_id="v1-b", status="ab_testing")
        var_2a = make_skill(skill_id="v2-a", status="ab_testing")
        var_2b = make_skill(skill_id="v2-b", status="ab_testing")

        # Per-group routing — use a side_effect to return
        # different variants based on the requested group.
        skill_repo = make_skill_repo()
        skill_repo.get_ab_variants = MagicMock(
            side_effect=lambda g: {
                "grp-1": [var_1a, var_1b],
                "grp-2": [var_2a, var_2b],
            }[g]
        )
        ab_test_repo = make_ab_test_repo()

        results = {
            "injected": [
                {"skill": original_1, "score": 0.9},
                {"skill": original_2, "score": 0.8},
            ],
            "low_match": [],
        }
        service = make_service(
            search_service=make_search_service(search_return=results),
            skill_repo=skill_repo,
            ab_test_repo=ab_test_repo,
        )

        _, ids = await service.inject_skills(
            "q", project_id=None, instance_id="i", message_id="m"
        )

        # Both ids must come from the variant sets, NOT the
        # original ids.
        assert "o-1" not in ids
        assert "o-2" not in ids
        assert set(ids).issubset({"v1-a", "v1-b", "v2-a", "v2-b"})
        assert len(ids) == 2

    @pytest.mark.asyncio
    async def test_results_dict_structure_handling(self):
        # Defensive: ``injected`` or ``low_match`` keys missing
        # from the search result must not crash — the service
        # uses ``.get(...) or []`` so missing/None is treated
        # as empty.
        skill = make_skill()
        # ``low_match`` key missing entirely.
        results = {"injected": [{"skill": skill, "score": 0.5}]}
        service = make_service(
            search_service=make_search_service(search_return=results)
        )
        text, ids = await service.inject_skills(
            "q", project_id=None, instance_id="i", message_id="m"
        )
        assert text is not None
        assert ids == ["skill-1"]
        # ``injected`` missing entirely (only low_match) →
        # still renders the low_match section.
        results2 = {"low_match": [{"name": "low", "score": 0.3,
                                   "description": "d"}]}
        service2 = make_service(
            search_service=make_search_service(search_return=results2)
        )
        text2, ids2 = await service2.inject_skills(
            "q", project_id=None, instance_id="i", message_id="m"
        )
        assert text2 is not None
        assert "low" in text2
        assert ids2 == []


# ============================================================
# TestBuildGraphInput
# ============================================================


class TestBuildGraphInput:
    """Integration tests for :func:`_build_graph_input`.

    The helper lives in
    :mod:`daemon.services.instance_messaging` but is the
    second half of the Phase 3 skill-injection pipeline: the
    injection service produces a ``HumanMessage``, and this
    helper prepends it to the graph-input message list.
    """

    def test_no_injection_msg_returns_single_message(self):
        # No injection → just the user message in the list.
        result = _build_graph_input("hello", "msg-1", None)

        assert "messages" in result
        assert len(result["messages"]) == 1
        user_msg = result["messages"][0]
        assert isinstance(user_msg, HumanMessage)
        # User message has the queue ``message_id`` so
        # ``add_messages`` can dedupe.
        assert user_msg.id == "msg-1"
        assert user_msg.content == "hello"

    def test_with_injection_msg_prepends(self):
        # When an injection message is provided, it goes FIRST
        # in the message list so the agent reads skill context
        # before user input.
        skill_msg = HumanMessage(content="skill text", id="skill-msg")
        result = _build_graph_input("hello", "msg-1", skill_msg)

        assert "messages" in result
        assert len(result["messages"]) == 2
        # Skill message first.
        assert result["messages"][0] is skill_msg
        assert result["messages"][0].content == "skill text"
        assert result["messages"][0].id == "skill-msg"
        # User message second.
        assert isinstance(result["messages"][1], HumanMessage)
        assert result["messages"][1].content == "hello"
        assert result["messages"][1].id == "msg-1"

    def test_user_message_has_correct_id(self):
        # The user message's ``id`` parameter must equal the
        # caller-provided ``message_id`` so LangGraph's
        # ``add_messages`` reducer can dedupe across retries.
        result = _build_graph_input("anything", "queue-msg-xyz", None)
        user_msg = result["messages"][0]
        assert user_msg.id == "queue-msg-xyz"

    def test_content_can_be_list(self):
        # Multimodal content (list of text + image blocks)
        # must be accepted. ``HumanMessage.content`` typing is
        # ``str | list[str | dict] | None`` — verify the list
        # form passes through without error.
        content = [
            {"type": "text", "text": "describe this image"},
            {
                "type": "image_url",
                "image_url": {"url": "data:image/png;base64,AAA"},
            },
        ]
        result = _build_graph_input(content, "msg-mm", None)

        assert len(result["messages"]) == 1
        # Content is preserved verbatim (not stringified).
        assert result["messages"][0].content == content