"""Unit tests for :class:`SkillCloneService`.

Phase 4 (Clone-on-Miss) test pack. Eleven cases mirror the
spec in ``.agents/shared/planning/tester-skill-evolution/phase4-plan.md``
test strategy, plus two async wrappers and the C2
``auto_load`` propagation contract.

Tests use a real in-memory SQLite engine (``StaticPool`` per
project convention — see ``tests/message_queue_redesign/conftest.py``)
backed by real :class:`SkillRepository` and
:class:`SkillBankRepository` instances. No mocks — the clone
service's behaviour depends on real SQLModel row hydration,
constraint enforcement, and the ``(project_id, name,
generation)`` UNIQUE index.

Async cases use ``@pytest.mark.asyncio`` against the project's
``asyncio_mode=auto`` config (set in ``pyproject.toml``).
"""

from __future__ import annotations

from typing import Any, Iterator

import pytest
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel

from daemon.repositories.skill.repository import SkillRepository
from daemon.repositories.skill.skill_bank_repository import (
    SkillBankRepository,
)
from daemon.services.skill_clone_service import SkillCloneService


# ============================================================================
# Fixtures
# ============================================================================


@pytest.fixture
def engine() -> Iterator[Engine]:
    """In-memory SQLite engine with all skill tables created.

    ``StaticPool`` + ``check_same_thread=False`` so the
    in-memory database is visible from every thread — required
    for the ``asyncio.to_thread`` async wrapper tests that
    hand off to the worker pool.
    """
    eng = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(eng)
    try:
        yield eng
    finally:
        eng.dispose()


@pytest.fixture
def skill_repo(engine: Engine) -> SkillRepository:
    """``SkillRepository`` wired to the in-memory engine."""
    return SkillRepository(engine)


@pytest.fixture
def skill_bank_repo(engine: Engine) -> SkillBankRepository:
    """``SkillBankRepository`` wired to the in-memory engine."""
    return SkillBankRepository(engine)


@pytest.fixture
def clone_service(
    skill_repo: SkillRepository,
    skill_bank_repo: SkillBankRepository,
) -> SkillCloneService:
    """``SkillCloneService`` with ``embedding_service=None`` (N1 fix).

    Per the N1 embedding fix documented in the Phase 4 task
    spec, the sync clone path does NOT use the embedding
    service. The constructor parameter is retained for forward
    compatibility but receives ``None`` here.
    """
    return SkillCloneService(
        skill_repo=skill_repo,
        skill_bank_repo=skill_bank_repo,
        embedding_service=None,
    )


def _seed_template(
    bank: SkillBankRepository,
    *,
    name: str,
    agent_id: str = "tester",
    auto_load: bool = False,
    description: str = "",
    content: str = "# Body\nDo the thing.",
    category: str = "workflow",
) -> Any:
    """Helper: insert one ``SkillBankItem`` and return the row.

    Defaults are tuned so a freshly-seeded template is
    immediately clone-eligible. ``description`` defaults to
    empty so the clone-test only asserts what it cares about;
    override per-test to check field propagation.
    """
    return bank.create(
        name=name,
        content=content,
        description=description,
        category=category,
        agent_id=agent_id,
        auto_load=auto_load,
    )


# ============================================================================
# Case 1 — clone_new_skill
# ============================================================================


class TestCloneNewSkill:
    """First-time clone from a bank template into project scope."""

    def test_clone_new_skill(
        self,
        clone_service: SkillCloneService,
        skill_repo: SkillRepository,
        skill_bank_repo: SkillBankRepository,
    ) -> None:
        """Template exists, no project skill → clone succeeds.

        Verifies every cloned field lands correctly: identity
        fields, lineage marker, generation, status, the soft
        FK back to the template, and the C2 ``auto_load``
        propagation.
        """
        template = _seed_template(
            skill_bank_repo,
            name="alpha-skill",
            description="Alpha planner",
            content="# Alpha\nplan it.",
            category="planning",
            auto_load=True,
        )

        cloned = clone_service.clone_on_miss_sync(
            name="alpha-skill",
            agent_id="tester",
            project_id="proj-1",
        )

        assert cloned is not None
        assert cloned.id != template.id  # distinct row, not the bank item
        assert cloned.name == "alpha-skill"
        assert cloned.description == "Alpha planner"
        assert cloned.content == "# Alpha\nplan it."
        assert cloned.project_id == "proj-1"
        assert cloned.category == "planning"
        assert cloned.lineage_origin == "bank_clone"
        assert cloned.generation == 0
        assert cloned.status == "active"
        assert cloned.is_active is True
        assert cloned.source_skill_bank_id == template.id
        assert cloned.auto_load is True

        # And it's reachable via the project-scoped lookup.
        refetched = skill_repo.get_by_name(
            project_id="proj-1", name="alpha-skill", generation=0
        )
        assert refetched is not None
        assert refetched.id == cloned.id


# ============================================================================
# Case 2 — clone_idempotency
# ============================================================================


class TestCloneIdempotency:

    def test_clone_idempotency(
        self,
        clone_service: SkillCloneService,
        skill_bank_repo: SkillBankRepository,
    ) -> None:
        """Second clone with the same ``(project_id, name,
        generation=0)`` MUST return the existing row, not
        produce a duplicate.
        """
        _seed_template(skill_bank_repo, name="dup-skill")

        first = clone_service.clone_on_miss_sync(
            name="dup-skill",
            agent_id="tester",
            project_id="proj-x",
        )
        second = clone_service.clone_on_miss_sync(
            name="dup-skill",
            agent_id="tester",
            project_id="proj-x",
        )

        assert first is not None
        assert second is not None
        assert first.id == second.id, (
            "Idempotency broken: second clone produced a new row"
        )


# ============================================================================
# Case 3 / 4 — auto_load propagation (C2)
# ============================================================================


class TestAutoLoadPropagation:

    def test_auto_load_propagation_true(
        self,
        clone_service: SkillCloneService,
        skill_bank_repo: SkillBankRepository,
    ) -> None:
        """``template.auto_load=True`` → ``cloned.auto_load=True`` (C2)."""
        _seed_template(skill_bank_repo, name="auto-on", auto_load=True)

        cloned = clone_service.clone_on_miss_sync(
            name="auto-on",
            agent_id="tester",
            project_id="proj-1",
        )

        assert cloned is not None
        assert cloned.auto_load is True

    def test_auto_load_propagation_false(
        self,
        clone_service: SkillCloneService,
        skill_bank_repo: SkillBankRepository,
    ) -> None:
        """``template.auto_load=False`` → ``cloned.auto_load=False`` (C2).

        Critical anti-regression for the C2 fix: prior phases
        hardcoded ``auto_load=False`` on clones, which silently
        suppressed the auto-load propagation. This case
        verifies the fix is wired correctly.
        """
        _seed_template(skill_bank_repo, name="auto-off", auto_load=False)

        cloned = clone_service.clone_on_miss_sync(
            name="auto-off",
            agent_id="tester",
            project_id="proj-1",
        )

        assert cloned is not None
        assert cloned.auto_load is False


# ============================================================================
# Case 5 — missing_template_returns_none
# ============================================================================


class TestMissingTemplate:

    def test_missing_template_returns_none(
        self, clone_service: SkillCloneService
    ) -> None:
        """No template in bank → ``clone_on_miss_sync`` returns ``None``."""
        result = clone_service.clone_on_miss_sync(
            name="nonexistent-skill",
            agent_id="tester",
            project_id="proj-1",
        )
        assert result is None


# ============================================================================
# Case 6 — source_skill_bank_id back-link
# ============================================================================


class TestSourceSkillBankId:

    def test_source_skill_bank_id_set_correctly(
        self,
        clone_service: SkillCloneService,
        skill_bank_repo: SkillBankRepository,
    ) -> None:
        """FK link from cloned skill back to its template ID."""
        template = _seed_template(skill_bank_repo, name="linked-skill")

        cloned = clone_service.clone_on_miss_sync(
            name="linked-skill",
            agent_id="tester",
            project_id="proj-1",
        )

        assert cloned is not None
        assert cloned.source_skill_bank_id == template.id, (
            f"Expected FK back to {template.id}, got "
            f"{cloned.source_skill_bank_id!r}"
        )


# ============================================================================
# Case 7 — ensure_auto_load only clones auto_load templates
# ============================================================================


class TestEnsureAutoLoadClonesOnlyAutoLoad:

    def test_ensure_auto_load_clones_only_auto_load(
        self,
        clone_service: SkillCloneService,
        skill_bank_repo: SkillBankRepository,
    ) -> None:
        """Mix of ``auto_load=True`` and ``False`` templates →
        ``ensure_auto_load_skills_sync`` clones only the
        ``auto_load=True`` ones.
        """
        _seed_template(
            skill_bank_repo,
            name="auto-on",
            auto_load=True,
        )
        _seed_template(
            skill_bank_repo,
            name="auto-off",
            auto_load=False,
        )

        results = clone_service.ensure_auto_load_skills_sync(
            agent_id="tester",
            project_id="proj-1",
        )

        names = sorted(s.name for s in results)
        # Only the auto_load=True template made it through.
        assert names == ["auto-on"]
        for s in results:
            assert s.auto_load is True


# ============================================================================
# Case 8 — ensure_all clones every template
# ============================================================================


class TestEnsureAllClonesAllTemplates:

    def test_ensure_all_clones_all_templates(
        self,
        clone_service: SkillCloneService,
        skill_bank_repo: SkillBankRepository,
    ) -> None:
        """``ensure_all_skills_sync`` clones BOTH ``auto_load=True``
        and ``False`` templates.
        """
        _seed_template(skill_bank_repo, name="alpha", auto_load=True)
        _seed_template(skill_bank_repo, name="beta", auto_load=False)
        _seed_template(skill_bank_repo, name="gamma", auto_load=False)

        results = clone_service.ensure_all_skills_sync(
            agent_id="tester",
            project_id="proj-1",
        )

        names = sorted(s.name for s in results)
        assert names == ["alpha", "beta", "gamma"]

        # auto_load flags preserved per template (C2).
        by_name = {s.name: s.auto_load for s in results}
        assert by_name["alpha"] is True
        assert by_name["beta"] is False
        assert by_name["gamma"] is False


# ============================================================================
# Case 9 — explicit lineage_origin assertion
# ============================================================================


class TestLineageOriginIsBankClone:

    def test_lineage_origin_is_bank_clone(
        self,
        clone_service: SkillCloneService,
        skill_bank_repo: SkillBankRepository,
    ) -> None:
        """Cloned skill carries ``lineage_origin == 'bank_clone'``.

        Distinct from the default ``'imported'`` so analytics
        and the evolution pipeline can distinguish
        bank-cloned skills from raw imports / evolved
        descendants.
        """
        _seed_template(skill_bank_repo, name="lineage-skill")

        cloned = clone_service.clone_on_miss_sync(
            name="lineage-skill",
            agent_id="tester",
            project_id="proj-1",
        )

        assert cloned is not None
        assert cloned.lineage_origin == "bank_clone"


# ============================================================================
# Case 10 — async ensure_all wrapper
# ============================================================================


class TestAsyncEnsureAllSkills:

    @pytest.mark.asyncio
    async def test_async_ensure_all_skills(
        self,
        clone_service: SkillCloneService,
        skill_bank_repo: SkillBankRepository,
    ) -> None:
        """Async wrapper clones all templates AND is idempotent."""
        _seed_template(skill_bank_repo, name="async-alpha", auto_load=True)
        _seed_template(skill_bank_repo, name="async-beta", auto_load=False)

        first = await clone_service.ensure_all_skills_async(
            agent_id="tester",
            project_id="proj-async",
        )
        assert sorted(s.name for s in first) == [
            "async-alpha",
            "async-beta",
        ]

        # Idempotent — second invocation returns the same rows.
        second = await clone_service.ensure_all_skills_async(
            agent_id="tester",
            project_id="proj-async",
        )
        assert sorted(s.name for s in second) == [
            "async-alpha",
            "async-beta",
        ]
        first_ids = sorted(s.id for s in first)
        second_ids = sorted(s.id for s in second)
        assert first_ids == second_ids, (
            "Async wrapper produced duplicates — idempotency broken"
        )


# ============================================================================
# Case 11 — async clone_on_miss wrapper
# ============================================================================


class TestAsyncCloneOnMiss:

    @pytest.mark.asyncio
    async def test_async_clone_on_miss(
        self,
        clone_service: SkillCloneService,
        skill_bank_repo: SkillBankRepository,
    ) -> None:
        """Async ``clone_on_miss_async`` mirrors the sync path:
        template cloned on first call, existing row returned on
        second.
        """
        _seed_template(skill_bank_repo, name="async-miss", auto_load=True)

        first = await clone_service.clone_on_miss_async(
            name="async-miss",
            agent_id="tester",
            project_id="proj-async-2",
        )
        second = await clone_service.clone_on_miss_async(
            name="async-miss",
            agent_id="tester",
            project_id="proj-async-2",
        )

        assert first is not None
        assert second is not None
        assert first.id == second.id
        assert first.lineage_origin == "bank_clone"
        assert first.auto_load is True
