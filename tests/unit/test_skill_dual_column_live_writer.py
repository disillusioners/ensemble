"""The LIVE dual-column writer — the re-pointed arm of the deleted
repository writer (tidier M1; plan §17).

WHY THIS FILE EXISTS.  The lane used to carry a SECOND dual-column writer
— ``SkillRepository.deactivate_with_receipt`` plus its two value-object
types (``DeactivationResult`` / ``SkillDeactivationRefused``) — with ZERO
production callers: the E4 mechanism writes through
``daemon/services/skill_deactivation.py::write_skill_pair`` (the sanctioned
writer, ONE call, BOTH columns).  The orphan was deleted; the invariants it
pinned were RE-POINTED here, onto the writer that is actually live.

THE TWO INVARIANTS RE-POINTED (the ones the E4 lane's own pin file does
NOT cover — everything else this file used to pin is covered there, and the
mapping is recorded in the lane's §17):

1. **THE PAIR AND NOTHING ELSE** — the live writer moves ``is_active`` and
   ``status`` together and leaves every OTHER column (``name``,
   ``description``, ``content``) untouched.  The E4 lane pins "the writer
   sets both columns" and "one repository call"; it does not pin that no
   THIRD column moves.
2. **A MISSING ROW RETURNS ``None``** — the writer's "no row matched"
   contract, which every caller (and the REST path's 404) depends on.

Mutation legs (pack ``skill_dual_column_deactivation_unit_test``):
``M-E4w-d`` drops the ``status`` write inside ``SkillRepository.update``
⇒ node 1 REDs (the pair no longer moves as a pair); ``M-E4w-e`` makes a
missing row return something other than ``None`` ⇒ node 2 REDs.
"""

from __future__ import annotations

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, create_engine

from daemon.repositories.skill.models import (  # noqa: F401 — create_all needs them
    Skill,
    SkillABTest,
    SkillEmbedding,
    SkillLineage,
    SkillTrigger,
    SkillUsageRecord,
)
from daemon.repositories.skill.repository import SkillRepository
from daemon.services.skill_deactivation import write_skill_pair


@pytest.fixture()
def skill_repo():
    eng = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(eng)
    repo = SkillRepository(eng)
    yield repo
    eng.dispose()


class TestTheLiveWriterMovesThePairAndNothingElse:
    """Re-pointed invariant 1 — the pair moves TOGETHER, nothing else does."""

    def test_the_pair_moves_and_every_other_column_stays_put(
        self, skill_repo
    ) -> None:
        skill = skill_repo.create(
            name="pair-only",
            description="the description",
            content="the content",
            project_id="p-live-writer",
        )
        before = skill_repo.get(skill.id)

        write_skill_pair(
            skill_repo,
            skill.id,
            is_active=False,
            status="inactive",
        )

        after = skill_repo.get(skill.id)
        assert after is not None
        # THE PAIR moved.
        assert after.is_active is False
        assert after.status == "inactive"
        # NOTHING ELSE moved.
        assert after.name == before.name
        assert after.description == before.description
        assert after.content == before.content
        assert after.project_id == before.project_id


class TestTheLiveWriterReturnsNoneForAMissingRow:
    """Re-pointed invariant 2 — the "no row matched" contract."""

    def test_a_missing_row_returns_none(self, skill_repo) -> None:
        assert (
            write_skill_pair(
                skill_repo,
                "ghost-skill-id",
                is_active=False,
                status="inactive",
            )
            is None
        )
