"""Unit tests for auto_load skill metrics tracking."""

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


@pytest.fixture
def engine() -> Iterator[Engine]:
    """Create an in-memory SQLite engine with all skill tables."""
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
    """Minimal stand-in exposing the repositories used by the appender."""

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


@pytest.fixture
def instance_repository() -> MagicMock:
    """Create a mock instance repository with mutable metadata."""
    mock_repo = MagicMock()
    mock_inst = MagicMock()
    mock_inst.instance_metadata = {}
    mock_repo.get.return_value = mock_inst
    return mock_repo


def _seed_skill(
    repo: SkillRepository,
    *,
    project_id: str,
    name: str,
    content: str = "# Body\nDo the thing.",
    auto_load: bool = True,
    is_active: bool = True,
) -> Any:
    """Insert one skill row and return it."""
    return repo.create(
        name=name,
        description=f"{name} description",
        content=content,
        project_id=project_id,
        auto_load=auto_load,
        is_active=is_active,
    )


def test_auto_load_skills_tracked_in_metadata(
    manager: _StubManager,
    skill_repo: SkillRepository,
    instance_repository: MagicMock,
) -> None:
    """Auto-load skill IDs are written to instance metadata."""
    skill = _seed_skill(
        skill_repo,
        project_id="proj-1",
        name="tracked-skill",
    )

    out = append_auto_load_skills(
        "BASE",
        agent_id="tester",
        project_id="proj-1",
        manager=manager,
        instance_id="instance-1",
        instance_repository=instance_repository,
        mode="legacy",
    )

    assert "## Auto-Loaded Skills (Evolvable)" in out
    instance_repository.set_metadata.assert_called_once_with(
        "instance-1",
        "last_injected_skill_ids",
        [str(skill.id)],
    )


def test_auto_load_dedup_merge(
    manager: _StubManager,
    skill_repo: SkillRepository,
    instance_repository: MagicMock,
) -> None:
    """Existing explicit IDs remain first when auto-load IDs are merged."""
    skill = _seed_skill(
        skill_repo,
        project_id="proj-1",
        name="auto-load-skill",
    )
    instance_repository.get.return_value.instance_metadata = {
        "last_injected_skill_ids": ["explicit_skill_id"]
    }

    append_auto_load_skills(
        "BASE",
        agent_id="tester",
        project_id="proj-1",
        manager=manager,
        instance_id="instance-1",
        instance_repository=instance_repository,
        mode="legacy",
    )

    instance_repository.set_metadata.assert_called_once_with(
        "instance-1",
        "last_injected_skill_ids",
        ["explicit_skill_id", str(skill.id)],
    )


def test_auto_load_no_instance_id_skips_tracking(
    manager: _StubManager,
    skill_repo: SkillRepository,
    instance_repository: MagicMock,
) -> None:
    """Omitting instance_id skips tracking without skipping prompt injection."""
    _seed_skill(
        skill_repo,
        project_id="proj-1",
        name="untracked-skill",
    )

    out = append_auto_load_skills(
        "BASE",
        agent_id="tester",
        project_id="proj-1",
        manager=manager,
        instance_id=None,
        instance_repository=instance_repository,
        mode="legacy",
    )

    assert "## Auto-Loaded Skills (Evolvable)" in out
    instance_repository.set_metadata.assert_not_called()


def test_auto_load_skips_explicitly_replaced_ids(
    manager: _StubManager,
    skill_repo: SkillRepository,
    instance_repository: MagicMock,
) -> None:
    """Explicitly replaced skills stay in the prompt but are not tracked."""
    skill_a = _seed_skill(
        skill_repo,
        project_id="proj-1",
        name="skill-a",
        content="# Skill A\nReplaced metadata skill.",
    )
    skill_b = _seed_skill(
        skill_repo,
        project_id="proj-1",
        name="skill-b",
        content="# Skill B\nTrackable metadata skill.",
    )
    instance_repository.get.return_value.instance_metadata = {
        "explicitly_replaced_ids": [str(skill_a.id)]
    }

    out = append_auto_load_skills(
        "BASE",
        agent_id="tester",
        project_id="proj-1",
        manager=manager,
        instance_id="instance-1",
        instance_repository=instance_repository,
        mode="legacy",
    )

    assert "# Skill A" in out
    assert "# Skill B" in out
    instance_repository.set_metadata.assert_called_once_with(
        "instance-1",
        "last_injected_skill_ids",
        [str(skill_b.id)],
    )


def test_auto_load_preserves_explicit_skills(
    manager: _StubManager,
    skill_repo: SkillRepository,
    instance_repository: MagicMock,
) -> None:
    """C3 invariant: explicit and auto-load skill IDs coexist."""
    skill = _seed_skill(
        skill_repo,
        project_id="proj-1",
        name="additive-auto-load-skill",
    )
    instance_repository.get.return_value.instance_metadata = {
        "last_injected_skill_ids": ["explicit_x"]
    }

    append_auto_load_skills(
        "BASE",
        agent_id="tester",
        project_id="proj-1",
        manager=manager,
        instance_id="instance-1",
        instance_repository=instance_repository,
        mode="legacy",
    )

    instance_repository.set_metadata.assert_called_once_with(
        "instance-1",
        "last_injected_skill_ids",
        ["explicit_x", str(skill.id)],
    )
