"""Tests for the P3-WP2 injection-time capability gate (proof-b fix 1/3).

Covers the ``SkillInjectionService._capability_preflight_block`` helper
plus the wiring in :meth:`SkillInjectionService.inject_skills` and
:meth:`SkillInjectionService.inject_explicit_skill`. The gate is the
load-bearing fix for the live proof-b run-2 failure mode where the
worker agent guessed at its tool inventory and invented a non-schema
escalation envelope kind (``capability_present`` — not in the §7.2
schema).

Coverage (per task brief — proof-b §5 chain fence):

* ``gate fires on missing`` — capability check returns ``missing`` for
  an MCP; the rendered block carries ``kind=capability_missing`` in the
  envelope, the skill body is still present after the block.
* ``gate fires on unconfigured`` — capability check returns
  ``unconfigured``; the block carries
  ``kind=installed_but_unconfigured``.
* ``no gate for skills without requires`` — byte-identical injection
  when the requirement lookup returns ``None`` (or an empty
  requirement).
* ``check-error → skill injects without block + warning logged`` —
  resolver error path: the skill still renders, a warning is logged.
* ``explicit lane (inject_explicit_skill)`` — same coverage as the
  bank-selected lane; the block preflight is wired into both paths.

All tests use in-memory mocks — no DB / network / no daemon boot.
"""
from __future__ import annotations

import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from daemon.services.capability_resolver import (
    CapabilityCheckResult,
    CapabilityRequirement,
    McpLookupResult,
)
from daemon.services.skill_injection_service import SkillInjectionService


# ═════════════════════════════════════════════════════════════════════
# Helpers
# ═════════════════════════════════════════════════════════════════════


def make_skill(
    *,
    skill_id: str = "skill-1",
    name: str = "opendesign-verify",
    description: str = "Verify opendesign design.",
    content: str = (
        "capability_check(\"opendesign\")\n\n"
        "Body: render the design via opendesign tooling."
    ),
    project_id: str | None = None,
    is_active: bool = True,
    status: str = "active",
    ab_test_group: str | None = None,
    source_skill_bank_id: str | None = None,
) -> SimpleNamespace:
    """Build a stand-in for a :class:`Skill` row.

    Mirrors the existing test fixture shape from
    ``tests/services/test_skill_injection_service.py`` plus a
    ``source_skill_bank_id`` field so the requirement_lookup callable
    has the FK it expects.
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
        source_skill_bank_id=source_skill_bank_id,
    )


def make_config(*, max_inject_skills: int = 2) -> MagicMock:
    cfg = MagicMock(spec=["max_inject_skills"])
    cfg.max_inject_skills = max_inject_skills
    return cfg


def make_search_service(*, search_return: dict | None = None) -> MagicMock:
    service = MagicMock()
    service.search = AsyncMock(
        return_value=search_return
        if search_return is not None
        else {"injected": [], "low_match": []}
    )
    return service


def make_ab_test_repo() -> MagicMock:
    repo = MagicMock()
    repo.increment_comparison = MagicMock(return_value=None)
    return repo


def make_skill_repo(*, get_by_name_return: object = None) -> MagicMock:
    repo = MagicMock()
    repo.get_ab_variants = MagicMock(return_value=[])
    # ``inject_explicit_skill`` falls back to a direct
    # ``get_by_name`` lookup when the clone service hasn't been
    # wired. Default to returning the most recent skill created in
    # the test (set by the caller via the test fixture); ``None``
    # means the lookup misses → no injection (the function returns
    # ``(None, [])`` before the gate runs).
    repo.get_by_name = MagicMock(return_value=get_by_name_return)
    return repo


def make_mcp_row(
    name: str = "opendesign",
    *,
    is_active: bool = True,
    config_env: dict[str, str] | None = None,
    bound_handle: str | None = "handle-123",
    requires_secret: bool = False,
) -> McpLookupResult:
    return McpLookupResult(
        name=name,
        is_active=is_active,
        config_env=config_env or {"OPENDESIGN_API_KEY": "__KMS_REF__opendesign__"},
        requires_secret=requires_secret,
        bound_handle=bound_handle,
    )


# Sentinel helper — distinct from None (real lookup result) and
# from a callable.
class _DefaultSentinel:
    pass


_SENTINEL = _DefaultSentinel()
_NONE_SENTINEL = _DefaultSentinel()  # distinct identity for "no req"


def make_capability_service(
    *,
    requirement: CapabilityRequirement | None = None,
    requirement_lookup_side_effect: Exception | None = None,
    mcp_lookup_return: McpLookupResult | None | object = _SENTINEL,
    mcp_lookup_side_effect: Exception | None = None,
    tools_allow: list[str] | None = None,
    env_lookup: dict[str, str] | None = None,
    capability_check_side_effect: Exception | None = None,
    explicit_skill: object = None,
) -> SkillInjectionService:
    """Construct an injection service with the capability gate wired.

    Defaults that match the live evidence scenario: the MCP row exists
    but ``is_active=False`` so the check returns ``unconfigured``.
    Override ``mcp_lookup_return`` to control state explicitly.

    Args:
        explicit_skill: Object returned by ``skill_repo.get_by_name``
            when :meth:`inject_explicit_skill` runs the fallback
            lookup. ``None`` means the lookup misses → the explicit
            lane returns ``(None, [])`` and the gate doesn't fire.
            Pass a real skill (e.g. from :func:`make_skill`) to
            exercise the gate path.
        capability_check_side_effect: Reserved for future tests
            that patch the resolver itself. Not currently wired
            because the production gate calls ``capability_check``
            which is not mockable from here without monkeypatching.
    """
    if requirement is None:
        # Default: requirement with one MCP capability. Tests can
        # override with None to assert the back-compat no-block path.
        requirement = CapabilityRequirement(mcp=["opendesign"])

    # Resolve the default sentinel for the MCP lookup.
    if mcp_lookup_return is _SENTINEL:
        # Default: MCP row present but is_active=False → unconfigured.
        mcp_lookup_return = make_mcp_row(is_active=False)

    # ``requirement`` controls what ``requirement_lookup`` RETURNS.
    # Three distinct cases:
    #   * ``requirement is _NONE_SENTINEL`` → lookup returns ``None``
    #     (skill has no ``requires:`` block).
    #   * ``requirement is None`` (default) → use the populated
    #     default ``CapabilityRequirement(mcp=["opendesign"])`` so
    #     the gate fires on the standard fixture (matches the live
    #     evidence scenario).
    #   * otherwise → use the passed requirement as-is (empty or
    #     populated).
    if requirement is _NONE_SENTINEL:
        requirement_value: object = None
    elif requirement is None:
        requirement_value = CapabilityRequirement(mcp=["opendesign"])
    else:
        requirement_value = requirement

    # requirement_lookup — closure; returns the requirement unless
    # told to raise.
    if requirement_lookup_side_effect is not None:
        def req_lookup(_skill):
            raise requirement_lookup_side_effect
    else:
        def req_lookup(_skill):
            return requirement

    def mcp_lookup(_capability_id: str):
        if mcp_lookup_side_effect is not None:
            raise mcp_lookup_side_effect
        return mcp_lookup_return

    def tools_allow_fn():
        return list(tools_allow) if tools_allow is not None else []

    def env_lookup_fn():
        return dict(env_lookup) if env_lookup is not None else {}

    # Inject the gate via set_capability_gate (the manager calls
    # this post-construction, mirroring the production wire path).
    # The search/ab_test/skill_repo deps are dummy mocks — these
    # tests exercise the gate pre-format path, which short-circuits
    # before A/B variant routing in some scenarios.
    service = SkillInjectionService(
        search_service=make_search_service(),
        config=make_config(),
        ab_test_repo=make_ab_test_repo(),
        skill_repo=make_skill_repo(get_by_name_return=explicit_skill),
    )
    if capability_check_side_effect is not None:
        # Patch the resolver itself so the gate's call raises.
        import daemon.services.capability_resolver as _cr
        orig_check = _cr.capability_check
        _cr.capability_check = lambda *args, **kwargs: (
            _ for _ in ()
        ).throw(capability_check_side_effect)
        # Restore after the test via monkeypatch in the fixture
        # below — see ``gate_test`` helper.

    service.set_capability_gate(
        requirement_lookup=req_lookup,
        mcp_lookup=mcp_lookup,
        tools_allow=tools_allow_fn,
        env_lookup=env_lookup_fn,
    )
    return service


# ═════════════════════════════════════════════════════════════════════
# Gate fires on missing
# ═════════════════════════════════════════════════════════════════════


class TestGateFiresOnMissing:
    """``state=missing`` → block present with kind=capability_missing."""

    @pytest.mark.asyncio
    async def test_block_present_and_envelope_kind_is_capability_missing(self):
        # MCP lookup returns None → check returns ``missing``.
        service = make_capability_service(
            mcp_lookup_return=None,  # no row
            explicit_skill=make_skill(
                name="opendesign-verify",
                content="Body: render the design via opendesign tooling.",
            ),
        )

        text, _ids = await service.inject_explicit_skill(
            skill_name="opendesign-verify",
            project_id="proj-1",
            instance_id="inst-1",
            message_id="msg-1",
            agent_id="tester",
        )

        # Block header is rendered.
        assert text is not None
        assert "[Capability Pre-flight: opendesign = missing]" in text
        # Envelope (Result: prefix + JSON) is in the block.
        assert "Result: " in text
        # The JSON carries kind=capability_missing.
        assert '"kind":"capability_missing"' in text
        # Skill body STILL renders after the block (gate is additive,
        # not a body replacement — agents reading the block must still
        # see the original instructions for context).
        assert "Body: render the design via opendesign tooling." in text


# ═════════════════════════════════════════════════════════════════════
# Gate fires on unconfigured
# ═════════════════════════════════════════════════════════════════════


class TestGateFiresOnUnconfigured:
    """``state=unconfigured`` → block present with kind=installed_but_unconfigured."""

    @pytest.mark.asyncio
    async def test_block_present_and_envelope_kind_is_installed_but_unconfigured(
        self,
    ):
        # MCP row present but is_active=False → ``unconfigured``.
        service = make_capability_service(
            mcp_lookup_return=make_mcp_row(is_active=False),
            explicit_skill=make_skill(
                name="opendesign-verify",
                content="Body: render the design via opendesign tooling.",
            ),
        )

        text, _ids = await service.inject_explicit_skill(
            skill_name="opendesign-verify",
            project_id="proj-1",
            instance_id="inst-1",
            message_id="msg-1",
            agent_id="tester",
        )

        assert text is not None
        assert "[Capability Pre-flight: opendesign = unconfigured]" in text
        # detection_evidence surfaces the underlying reason.
        assert "is_active=False" in text
        assert '"kind":"installed_but_unconfigured"' in text
        # Skill body still renders after the block.
        assert "Body: render the design via opendesign tooling." in text


# ═════════════════════════════════════════════════════════════════════
# No gate when capability is present
# ═════════════════════════════════════════════════════════════════════


class TestNoGateWhenPresent:
    """``state=present`` → no block; skill injects as today."""

    @pytest.mark.asyncio
    async def test_no_block_when_all_capabilities_present(self):
        # MCP row present + is_active=True + bound_handle → present.
        service = make_capability_service(
            mcp_lookup_return=make_mcp_row(is_active=True, bound_handle="handle-123"),
            explicit_skill=make_skill(
                name="opendesign-verify",
                content="Body: render the design via opendesign tooling.",
            ),
        )

        text, _ids = await service.inject_explicit_skill(
            skill_name="opendesign-verify",
            project_id="proj-1",
            instance_id="inst-1",
            message_id="msg-1",
            agent_id="tester",
        )

        assert text is not None
        # No pre-flight block — the gate confirms the capability.
        assert "[Capability Pre-flight:" not in text
        assert "Result: " not in text
        # Skill body renders normally.
        assert "Body: render the design via opendesign tooling." in text


# ═════════════════════════════════════════════════════════════════════
# No gate when requirement is None or empty (back-compat)
# ═════════════════════════════════════════════════════════════════════


class TestNoGateWhenNoRequirement:
    """Skills without a ``requires:`` block → zero behavior change."""

    @pytest.mark.asyncio
    async def test_no_block_when_requirement_lookup_returns_none(self):
        # Requirement lookup returns None → no requirement → no gate.
        service = make_capability_service(
            requirement=_NONE_SENTINEL,
            explicit_skill=make_skill(
                name="any-skill",
                content="Body: this is a normal skill.",
            ),
        )

        text, _ids = await service.inject_explicit_skill(
            skill_name="any-skill",
            project_id="proj-1",
            instance_id="inst-1",
            message_id="msg-1",
            agent_id="tester",
        )

        assert text is not None
        # No pre-flight block.
        assert "[Capability Pre-flight:" not in text
        # Skill body still renders.
        assert "Body:" in text

    @pytest.mark.asyncio
    async def test_no_block_when_requirement_is_empty(self):
        # Requirement exists but is empty (no mcp/tools/env) → no gate.
        service = make_capability_service(
            requirement=CapabilityRequirement(),
            explicit_skill=make_skill(
                name="any-skill",
                content="Body: this is a normal skill.",
            ),
        )

        text, _ids = await service.inject_explicit_skill(
            skill_name="any-skill",
            project_id="proj-1",
            instance_id="inst-1",
            message_id="msg-1",
            agent_id="tester",
        )

        assert text is not None
        assert "[Capability Pre-flight:" not in text

    @pytest.mark.asyncio
    async def test_unrelated_skill_byte_identical_with_and_without_gate(
        self, monkeypatch
    ):
        """Back-compat pinning test (per task brief).

        A skill whose requirement lookup returns ``None`` must
        produce a byte-identical injection whether the gate is wired
        or not. Two parallel ``SkillInjectionService`` instances,
        one with the gate wired, one without — same inputs, same
        outputs.
        """
        skill = make_skill(
            skill_id="s1",
            name="code-review",
            content="# Code Review\n\nLook for bugs.",
            source_skill_bank_id=None,
        )

        # Service WITHOUT the gate (no set_capability_gate call).
        no_gate_service = SkillInjectionService(
            search_service=make_search_service(),
            config=make_config(),
            ab_test_repo=make_ab_test_repo(),
            skill_repo=make_skill_repo(get_by_name_return=skill),
        )

        # Service WITH the gate, requirement_lookup returning None.
        with_gate_service = SkillInjectionService(
            search_service=make_search_service(),
            config=make_config(),
            ab_test_repo=make_ab_test_repo(),
            skill_repo=make_skill_repo(get_by_name_return=skill),
        )
        with_gate_service.set_capability_gate(
            requirement_lookup=lambda _s: None,
            mcp_lookup=lambda _c: None,
            tools_allow=lambda: [],
            env_lookup=lambda: {},
        )

        # Same input, both services.
        text_a, ids_a = await no_gate_service.inject_explicit_skill(
            skill_name="code-review",
            project_id="proj-1",
            instance_id="inst-1",
            message_id="msg-1",
            agent_id="tester",
        )
        text_b, ids_b = await with_gate_service.inject_explicit_skill(
            skill_name="code-review",
            project_id="proj-1",
            instance_id="inst-1",
            message_id="msg-1",
            agent_id="tester",
        )

        # Byte-identical text. ids are also identical (single skill).
        assert text_a is not None and text_b is not None
        assert text_a == text_b, (
            f"back-compat broken: gate changed output for a "
            f"requirement-less skill\n--- no_gate ---\n{text_a!r}\n"
            f"--- with_gate ---\n{text_b!r}"
        )
        assert ids_a == ids_b


# ═════════════════════════════════════════════════════════════════════
# Resolver error → skill injects without block + warning logged
# ═════════════════════════════════════════════════════════════════════


class TestResolverErrorFallthrough:
    """If the check raises, the skill renders WITHOUT the block."""

    @pytest.mark.asyncio
    async def test_requirement_lookup_raises_skill_injects_without_block(
        self, caplog
    ):
        service = make_capability_service(
            requirement_lookup_side_effect=RuntimeError("bank get failed"),
            explicit_skill=make_skill(
                name="opendesign-verify",
                content="Body: render the design via opendesign tooling.",
            ),
        )

        with caplog.at_level(
            logging.WARNING, logger="daemon.services.skill_injection_service"
        ):
            text, _ids = await service.inject_explicit_skill(
                skill_name="opendesign-verify",
                project_id="proj-1",
                instance_id="inst-1",
                message_id="msg-1",
                agent_id="tester",
            )

        assert text is not None
        # No block — the resolver error fell through.
        assert "[Capability Pre-flight:" not in text
        # Skill body still renders.
        assert "Body: render the design via opendesign tooling." in text
        # Warning logged.
        assert any(
            "requirement_lookup raised" in r.message
            for r in caplog.records
        )


# ═════════════════════════════════════════════════════════════════════
# Same coverage for the search-selected lane (inject_skills)
# ═════════════════════════════════════════════════════════════════════


class TestBankSelectedLaneGate:
    """``inject_skills`` (search-selected) wires the same gate."""

    @pytest.mark.asyncio
    async def test_search_lane_missing_block_present(self):
        skill = make_skill(
            skill_id="s1",
            name="opendesign-verify",
            content="Body: render the design via opendesign tooling.",
        )
        service = make_capability_service(
            mcp_lookup_return=None,  # missing
        )
        # Override the search service to return our skill.
        service._search_service = make_search_service(
            search_return={
                "injected": [{"skill": skill, "score": 0.9}],
                "low_match": [],
            }
        )

        text, _ids = await service.inject_skills(
            "verify the design",
            project_id="proj-1",
            instance_id="inst-1",
            message_id="msg-1",
        )

        assert text is not None
        assert "[Capability Pre-flight: opendesign = missing]" in text
        assert '"kind":"capability_missing"' in text
        assert "Body: render the design via opendesign tooling." in text

    @pytest.mark.asyncio
    async def test_search_lane_unconfigured_block_present(self):
        skill = make_skill(
            skill_id="s1",
            name="opendesign-verify",
            content="Body: render the design via opendesign tooling.",
        )
        service = make_capability_service(
            mcp_lookup_return=make_mcp_row(is_active=False),  # unconfigured
        )
        service._search_service = make_search_service(
            search_return={
                "injected": [{"skill": skill, "score": 0.9}],
                "low_match": [],
            }
        )

        text, _ids = await service.inject_skills(
            "verify the design",
            project_id="proj-1",
            instance_id="inst-1",
            message_id="msg-1",
        )

        assert text is not None
        assert "[Capability Pre-flight: opendesign = unconfigured]" in text
        assert '"kind":"installed_but_unconfigured"' in text

    @pytest.mark.asyncio
    async def test_search_lane_present_no_block(self):
        skill = make_skill(
            skill_id="s1",
            name="opendesign-verify",
            content="Body: render the design via opendesign tooling.",
        )
        service = make_capability_service(
            mcp_lookup_return=make_mcp_row(is_active=True, bound_handle="h-1"),
        )
        service._search_service = make_search_service(
            search_return={
                "injected": [{"skill": skill, "score": 0.9}],
                "low_match": [],
            }
        )

        text, _ids = await service.inject_skills(
            "verify the design",
            project_id="proj-1",
            instance_id="inst-1",
            message_id="msg-1",
        )

        assert text is not None
        assert "[Capability Pre-flight:" not in text
        assert "Body: render the design via opendesign tooling." in text


# ═════════════════════════════════════════════════════════════════════
# Multiple capabilities — first non-present wins (most informative)
# ═════════════════════════════════════════════════════════════════════


class TestMultiCapabilityOrdering:
    """Multiple capabilities: first non-present drives the envelope."""

    @pytest.mark.asyncio
    async def test_first_non_present_wins(self):
        # Two MCPs: opendesign present, jira missing → jira wins.
        service = SkillInjectionService(
            search_service=make_search_service(),
            config=make_config(),
            ab_test_repo=make_ab_test_repo(),
            skill_repo=make_skill_repo(
                get_by_name_return=make_skill(
                    name="composite-skill",
                    content="Body: render the design via opendesign tooling.",
                )
            ),
        )

        def mcp_lookup(cap_id: str):
            if cap_id == "opendesign":
                return make_mcp_row(name="opendesign", is_active=True)
            if cap_id == "jira":
                return None  # missing
            return None

        service.set_capability_gate(
            requirement_lookup=lambda _s: CapabilityRequirement(
                mcp=["opendesign", "jira"]
            ),
            mcp_lookup=mcp_lookup,
            tools_allow=lambda: [],
            env_lookup=lambda: {},
        )

        text, _ids = await service.inject_explicit_skill(
            skill_name="composite-skill",
            project_id="proj-1",
            instance_id="inst-1",
            message_id="msg-1",
            agent_id="tester",
        )

        assert text is not None
        # jira (missing) wins the envelope, not opendesign (present).
        assert "[Capability Pre-flight: jira = missing]" in text
        assert '"kind":"capability_missing"' in text
        # The block references jira's evidence, not opendesign's.
        assert "jira" in text
