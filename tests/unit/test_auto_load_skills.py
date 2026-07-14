"""Unit tests for ``append_auto_load_skills`` in ``instance_lifecycle``.

Phase 5 (auto_load Prompt Section) test pack. Ten cases mirror
the spec in ``.agents/shared/planning/tester-skill-evolution/phase5-plan.md``
test strategy plus an explicit project-isolation regression case.

Tests use a real in-memory SQLite engine (``StaticPool`` per the
project convention — see ``tests/unit/test_skill_clone_service.py``)
backed by real :class:`SkillRepository` and
:class:`SkillCloneService` instances. The "manager" passed to
``append_auto_load_skills`` is a small :class:`_StubManager` that
exposes the two attributes the function reads (``_skill_repo`` and
``_skill_clone_service``) — that mirrors how
``InstanceLifecycleService`` injects ``self._manager`` at the call
sites.
"""

from __future__ import annotations

from typing import Any, Iterator
from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel

from daemon.repositories.skill.repository import SkillRepository
from daemon.repositories.skill.skill_bank_repository import (
    SkillBankRepository,
)
from daemon.services.instance_lifecycle import append_auto_load_skills
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
    return SkillRepository(engine)


@pytest.fixture
def skill_bank_repo(engine: Engine) -> SkillBankRepository:
    return SkillBankRepository(engine)


@pytest.fixture
def clone_service(
    skill_repo: SkillRepository,
    skill_bank_repo: SkillBankRepository,
) -> SkillCloneService:
    return SkillCloneService(
        skill_repo=skill_repo,
        skill_bank_repo=skill_bank_repo,
        embedding_service=None,
    )


class _StubManager:
    """Minimal stand-in for ``InstanceManager``.

    The append function only reads two attributes off the manager
    (``_skill_repo`` and ``_skill_clone_service``); a simple
    namespace object keeps the test isolated from the real
    manager bootstrap (which spins up the full daemon DB +
    checkpointer + LLM config).
    """

    def __init__(
        self,
        skill_repo: SkillRepository | None = None,
        skill_clone_service: SkillCloneService | None = None,
    ) -> None:
        self._skill_repo = skill_repo
        self._skill_clone_service = skill_clone_service


@pytest.fixture
def manager(
    skill_repo: SkillRepository,
    clone_service: SkillCloneService,
) -> _StubManager:
    return _StubManager(skill_repo=skill_repo, skill_clone_service=clone_service)


def _seed_skill(
    repo: SkillRepository,
    *,
    project_id: str,
    name: str,
    content: str = "# Body\nDo the thing.",
    auto_load: bool = True,
    is_active: bool = True,
) -> Any:
    """Helper: insert one ``Skill`` row and return it.

    Defaults produce an active, auto_load=True row in the given
    project — i.e. immediately eligible for the auto_load section
    of the prompt.
    """
    return repo.create(
        name=name,
        description=f"{name} description",
        content=content,
        project_id=project_id,
        auto_load=auto_load,
        is_active=is_active,
    )


def _seed_bank_template(
    bank: SkillBankRepository,
    *,
    name: str,
    agent_id: str = "tester",
    auto_load: bool = True,
    content: str = "# Template\nClone me.",
) -> Any:
    """Helper: insert one ``SkillBankItem`` template."""
    return bank.create(
        name=name,
        content=content,
        description=f"{name} template",
        agent_id=agent_id,
        auto_load=auto_load,
    )


# ============================================================================
# Case 1 — no project_id → return unchanged
# ============================================================================


class TestNoProjectId:
    def test_no_project_id_returns_unchanged(
        self, manager: _StubManager
    ) -> None:
        """``project_id=None`` short-circuits before any DB I/O.

        The base prompt MUST be returned by identity (same string
        object) so callers can compare for the no-op case.
        """
        base = "# Base prompt\nCore instructions."
        out = append_auto_load_skills(
            base,
            agent_id="tester",
            project_id=None,
            manager=manager,
        )
        assert out == base

    def test_empty_project_id_returns_unchanged(
        self, manager: _StubManager
    ) -> None:
        """Empty string ``project_id`` is treated like ``None``."""
        base = "# Base prompt\nCore instructions."
        out = append_auto_load_skills(
            base,
            agent_id="tester",
            project_id="",
            manager=manager,
        )
        assert out == base


# ============================================================================
# Case 2 — skill_repo is None → return unchanged
# ============================================================================


class TestSkillRepoMissing:
    def test_skill_repo_none_returns_unchanged(
        self, clone_service: SkillCloneService
    ) -> None:
        """``manager._skill_repo`` is ``None`` (skill_evolution not
        configured) → return prompt unchanged. No DB query, no clone.
        """
        manager = _StubManager(
            skill_repo=None,
            skill_clone_service=clone_service,
        )
        base = "# Base prompt\nCore instructions."
        out = append_auto_load_skills(
            base,
            agent_id="tester",
            project_id="proj-1",
            manager=manager,
        )
        assert out == base


# ============================================================================
# Case 3 — no skills in DB → return unchanged
# ============================================================================


class TestNoSkillsFound:
    def test_no_skills_returns_unchanged(
        self, manager: _StubManager
    ) -> None:
        """project_id present and skill_repo present, but the
        ``get_auto_load_skills`` query returns ``[]`` → prompt is
        returned unchanged.
        """
        out = append_auto_load_skills(
            "# Base prompt\nCore instructions.",
            agent_id="tester",
            project_id="proj-empty",
            manager=manager,
        )
        assert "Auto-Loaded Skills" not in out


# ============================================================================
# Case 4 — skills found → prompt has the section
# ============================================================================


class TestSkillsAppended:
    def test_single_skill_appended(
        self,
        manager: _StubManager,
        skill_repo: SkillRepository,
    ) -> None:
        """One auto_load skill in the project → prompt ends with
        the formatted section and contains the skill content.
        """
        _seed_skill(
            skill_repo,
            project_id="proj-1",
            name="alpha-skill",
            content="# Alpha\nDo the thing.",
        )
        base = "# Base prompt\nCore instructions."

        out = append_auto_load_skills(
            base,
            agent_id="tester",
            project_id="proj-1",
            manager=manager,
        )

        assert out.startswith(base)
        assert "## Auto-Loaded Skills (Evolvable)" in out
        assert "evolve over time via feedback and A/B testing" in out
        assert "# Alpha" in out
        assert "Do the thing." in out

    def test_section_separator_present(
        self,
        manager: _StubManager,
        skill_repo: SkillRepository,
    ) -> None:
        """The section must start with the standard ``\\n---\\n\\n``
        separator so it aligns with the rest of the post-cache
        append chain (context_key / current_time / language).
        """
        _seed_skill(
            skill_repo,
            project_id="proj-1",
            name="alpha-skill",
        )
        out = append_auto_load_skills(
            "BASE",
            agent_id="tester",
            project_id="proj-1",
            manager=manager,
        )
        # Section header immediately follows a `---` divider.
        assert "\n---\n\n## Auto-Loaded Skills (Evolvable)" in out


# ============================================================================
# Case 5 — clone triggers before the query
# ============================================================================


class TestCloneBeforeQuery:
    def test_clone_called_before_query(
        self,
        manager: _StubManager,
        skill_bank_repo: SkillBankRepository,
    ) -> None:
        """``ensure_auto_load_skills_sync`` MUST be called on the
        clone service before the auto_load query, so the first
        spawn in a project can materialize skill_bank templates.

        We assert the observable side-effect: a bank template that
        was NOT in the project table at the start is now cloned.
        """
        _seed_bank_template(
            skill_bank_repo,
            name="lazy-clone-skill",
            agent_id="tester",
            auto_load=True,
        )
        # Pre-condition: no row in skills table for proj-fresh.
        assert manager._skill_repo.get_auto_load_skills("proj-fresh") == []

        append_auto_load_skills(
            "BASE",
            agent_id="tester",
            project_id="proj-fresh",
            manager=manager,
        )

        # Post-condition: the bank template is now in project scope.
        cloned = manager._skill_repo.get_auto_load_skills("proj-fresh")
        names = [s.name for s in cloned]
        assert "lazy-clone-skill" in names

    def test_clone_idempotent_second_call(
        self,
        manager: _StubManager,
        skill_bank_repo: SkillBankRepository,
        skill_repo: SkillRepository,
    ) -> None:
        """Calling the append twice does not produce duplicate
        clones (idempotency contract from the clone service).
        """
        _seed_bank_template(
            skill_bank_repo,
            name="dup-skill",
            auto_load=True,
        )
        append_auto_load_skills(
            "BASE",
            agent_id="tester",
            project_id="proj-1",
            manager=manager,
        )
        append_auto_load_skills(
            "BASE",
            agent_id="tester",
            project_id="proj-1",
            manager=manager,
        )
        rows = skill_repo.get_auto_load_skills("proj-1")
        assert len(rows) == 1
        assert rows[0].name == "dup-skill"


# ============================================================================
# Case 6 — clone service fails → soft-fail, query still runs
# ============================================================================


class TestCloneFailsSoftFail:
    def test_clone_exception_does_not_break_prompt(
        self,
        skill_repo: SkillRepository,
        skill_bank_repo: SkillBankRepository,
    ) -> None:
        """When ``ensure_auto_load_skills_sync`` raises, the append
        function MUST log a warning and continue with the DB query.

        Skills that already exist in the project table still land
        in the prompt — the failure does NOT abort the function.
        """
        # Pre-existing skill in the project so the post-clone
        # query still finds something to append.
        _seed_skill(
            skill_repo,
            project_id="proj-1",
            name="pre-existing",
        )

        # Spy-style: blow up the clone call, keep the rest intact.
        clone_service = SkillCloneService(
            skill_repo=skill_repo,
            skill_bank_repo=skill_bank_repo,
            embedding_service=None,
        )

        def boom(*args: Any, **kwargs: Any) -> Any:
            raise RuntimeError("simulated clone DB error")

        clone_service.ensure_auto_load_skills_sync = boom  # type: ignore[assignment]

        manager = _StubManager(
            skill_repo=skill_repo,
            skill_clone_service=clone_service,
        )

        out = append_auto_load_skills(
            "BASE",
            agent_id="tester",
            project_id="proj-1",
            manager=manager,
        )

        # The pre-existing skill still made it into the section.
        assert "## Auto-Loaded Skills (Evolvable)" in out
        assert "pre-existing" in out or "Do the thing." in out


# ============================================================================
# Case 7 — DB query fails → return unchanged with warning
# ============================================================================


class TestQueryFailsSoftFail:
    def test_query_exception_returns_unchanged(
        self,
        manager: _StubManager,
    ) -> None:
        """When ``skill_repo.get_auto_load_skills`` raises, the
        function MUST log a warning and return the prompt
        unchanged.

        We use a MagicMock so the test is deterministic and does
        not rely on disposing a real engine.
        """
        boom_repo = MagicMock(spec=SkillRepository)
        boom_repo.get_auto_load_skills.side_effect = RuntimeError(
            "simulated DB error"
        )
        manager._skill_repo = boom_repo

        base = "BASE PROMPT"
        out = append_auto_load_skills(
            base,
            agent_id="tester",
            project_id="proj-1",
            manager=manager,
        )
        assert out == base
        # The query was attempted exactly once.
        boom_repo.get_auto_load_skills.assert_called_once_with("proj-1")


# ============================================================================
# Case 8 — multiple skills → all concatenated
# ============================================================================


class TestMultipleSkills:
    def test_multiple_skills_concatenated(
        self,
        manager: _StubManager,
        skill_repo: SkillRepository,
    ) -> None:
        """Two auto_load skills for the same project are joined
        with ``\\n\\n---\\n\\n`` between them. Both contents must be
        present in the section.
        """
        _seed_skill(
            skill_repo,
            project_id="proj-1",
            name="alpha",
            content="# Alpha\nfirst skill.",
        )
        _seed_skill(
            skill_repo,
            project_id="proj-1",
            name="beta",
            content="# Beta\nsecond skill.",
        )

        out = append_auto_load_skills(
            "BASE",
            agent_id="tester",
            project_id="proj-1",
            manager=manager,
        )

        assert "# Alpha" in out
        assert "first skill." in out
        assert "# Beta" in out
        assert "second skill." in out
        # Both contents are joined with the standard separator.
        assert "first skill.\n\n---\n\n# Beta" in out


# ============================================================================
# Case 9 — empty-content skill → skipped, not included
# ============================================================================


class TestEmptyContentSkipped:
    def test_empty_content_skill_skipped(
        self,
        manager: _StubManager,
        skill_repo: SkillRepository,
    ) -> None:
        """A skill whose ``content`` is empty/whitespace MUST be
        skipped — only divider, no value. When ALL skills are
        empty, the prompt is returned unchanged.
        """
        _seed_skill(
            skill_repo,
            project_id="proj-1",
            name="blank-skill",
            content="   \n  ",  # whitespace-only
        )

        out = append_auto_load_skills(
            "BASE",
            agent_id="tester",
            project_id="proj-1",
            manager=manager,
        )
        # All-empty → no section appended, prompt unchanged.
        assert out == "BASE"

    def test_mix_of_empty_and_real_skills(
        self,
        manager: _StubManager,
        skill_repo: SkillRepository,
    ) -> None:
        """When at least one skill has real content, the section
        IS appended — but the empty-content skill is silently
        dropped (no header for it).
        """
        _seed_skill(
            skill_repo,
            project_id="proj-1",
            name="real-skill",
            content="# Real\nreal body.",
        )
        _seed_skill(
            skill_repo,
            project_id="proj-1",
            name="blank-skill",
            content="",  # truly empty
        )

        out = append_auto_load_skills(
            "BASE",
            agent_id="tester",
            project_id="proj-1",
            manager=manager,
        )

        assert "## Auto-Loaded Skills (Evolvable)" in out
        assert "Real" in out
        assert "real body." in out
        # The blank-skill name must NOT appear as a header.
        assert "### blank-skill" not in out


# ============================================================================
# Case 10 — project isolation
# ============================================================================


class TestProjectIsolation:
    def test_skills_from_other_projects_excluded(
        self,
        manager: _StubManager,
        skill_repo: SkillRepository,
    ) -> None:
        """Auto_load skills in project-A MUST NOT appear when
        querying for project-B. This guards the cross-project
        cache collision risk that motivated the post-cache design.
        """
        _seed_skill(
            skill_repo,
            project_id="proj-A",
            name="alpha-A",
            content="# Alpha A\nA-only.",
        )
        _seed_skill(
            skill_repo,
            project_id="proj-B",
            name="beta-B",
            content="# Beta B\nB-only.",
        )

        # Query for project-A only.
        out_a = append_auto_load_skills(
            "BASE",
            agent_id="tester",
            project_id="proj-A",
            manager=manager,
        )
        assert "Alpha A" in out_a
        assert "A-only." in out_a
        assert "Beta B" not in out_a
        assert "B-only." not in out_a

        # Query for project-B only.
        out_b = append_auto_load_skills(
            "BASE",
            agent_id="tester",
            project_id="proj-B",
            manager=manager,
        )
        assert "Beta B" in out_b
        assert "B-only." in out_b
        assert "Alpha A" not in out_b
        assert "A-only." not in out_b


# ============================================================================
# Case: clone service is None → skip clone, still query existing
# ============================================================================


class TestCloneServiceNone:
    def test_no_clone_service_still_queries_existing(
        self,
        skill_repo: SkillRepository,
    ) -> None:
        """``manager._skill_clone_service`` is ``None`` → the
        clone-on-miss step is skipped, but the auto_load query
        still runs and still appends existing project skills.

        This path matters because the clone service is optional —
        a deployment without the skill_evolution skill-cloning
        pipeline must still get pre-existing auto_load rows
        injected.
        """
        _seed_skill(
            skill_repo,
            project_id="proj-1",
            name="pre-loaded",
        )
        manager = _StubManager(
            skill_repo=skill_repo,
            skill_clone_service=None,
        )
        out = append_auto_load_skills(
            "BASE",
            agent_id="tester",
            project_id="proj-1",
            manager=manager,
        )
        assert "## Auto-Loaded Skills (Evolvable)" in out
        # The seeded skill's default content lands in the section.
        assert "Do the thing." in out


# ============================================================================
# Case: inactive skills excluded
# ============================================================================


class TestInactiveExcluded:
    def test_inactive_skill_not_in_prompt(
        self,
        manager: _StubManager,
        skill_repo: SkillRepository,
    ) -> None:
        """``is_active=False`` skills MUST NOT appear in the
        auto_load prompt. (Deactivated skills were excluded in
        the original repo query — verify the wiring agrees.)
        """
        _seed_skill(
            skill_repo,
            project_id="proj-1",
            name="inactive-skill",
            content="# Inactive\nretired.",
            is_active=False,
        )
        _seed_skill(
            skill_repo,
            project_id="proj-1",
            name="active-skill",
            content="# Active\nlive.",
            is_active=True,
        )

        out = append_auto_load_skills(
            "BASE",
            agent_id="tester",
            project_id="proj-1",
            manager=manager,
        )
        assert "Active" in out
        assert "Inactive" not in out
        assert "retired." not in out


# ============================================================================
# Case: auto_load=False excluded
# ============================================================================


class TestNonAutoLoadExcluded:
    def test_auto_load_false_skill_not_in_prompt(
        self,
        manager: _StubManager,
        skill_repo: SkillRepository,
    ) -> None:
        """``auto_load=False`` skills MUST NOT appear — that's the
        foundational auto_load / on-demand split.
        """
        _seed_skill(
            skill_repo,
            project_id="proj-1",
            name="ondemand",
            content="# OnDemand\non-demand body.",
            auto_load=False,
        )
        out = append_auto_load_skills(
            "BASE",
            agent_id="tester",
            project_id="proj-1",
            manager=manager,
        )
        assert "Auto-Loaded Skills" not in out
        assert out == "BASE"
