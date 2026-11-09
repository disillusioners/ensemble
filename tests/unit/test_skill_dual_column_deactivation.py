"""I4 — the E4 REPOSITORY-SIDE DUAL-COLUMN WRITER (registered pack
``skill_dual_column_deactivation_unit_test``).

WHY A REPOSITORY WRITER EXISTS AT ALL.  ``skills.is_active`` and
``skills.status`` are TWO DESYNCABLE COLUMNS.  A real production row carries
``(is_active=False, status='ab_testing')`` — the desync proof: a
single-column write leaves a half-dead row that the census reports in one
bucket and the readers in another.  ``deactivate_with_receipt`` therefore
writes BOTH columns in ONE assignment and surfaces the PRIOR pair so the
caller's receipt can restore it exactly.

THE THREE PROPERTIES THIS MODULE PINS (one per required mutation leg):

1. **BOTH COLUMNS** — ``is_active=False`` AND ``status='inactive'``.
   ``M-E4w-a`` writes only ``is_active`` ⇒ RED.
2. **THE A/B GUARD** — a row guarded by an OPEN ``skill_ab_tests`` row is
   REFUSED with a TYPED exception, and the guard is SELF-CONTAINED (the
   repository resolves the A/B state; the caller passes only a skill id).
   ``M-E4w-b`` drops the guard ⇒ RED.
3. **REVERSIBILITY** — the PRIOR ``(is_active, status)`` pair is surfaced, so
   a receipt can restore it exactly (including from a desynced pair).
   ``M-E4w-c`` returns the POST-write pair instead ⇒ RED.

The guard is exercised from OUTSIDE (only a skill id in hand), which is how
the E4 lane's refusal-proving leg calls it.
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
from daemon.repositories.skill.repository import (
    SkillABTestRepository,
    SkillDeactivationRefused,
    SkillRepository,
)


@pytest.fixture()
def engine():
    eng = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(eng)
    yield eng
    eng.dispose()


@pytest.fixture()
def skill_repo(engine):
    return SkillRepository(engine)


@pytest.fixture()
def ab_repo(engine):
    return SkillABTestRepository(engine)


def _skill(repo, name="w", *, project="p", **kwargs):
    return repo.create(name=name, description="d", content=f"c-{name}", project_id=project, **kwargs)


def _open_test(ab_repo, group, old_id, new_id):
    return ab_repo.create_ab_test(
        ab_test_group=group, skill_id_old=old_id, skill_id_new=new_id
    )


class TestBothColumnsAreWrittenTogether:
    def test_a_single_call_writes_both_columns(self, skill_repo) -> None:
        skill = _skill(skill_repo, "both")
        assert skill.is_active is True and skill.status == "active"

        result = skill_repo.deactivate_with_receipt(skill.id)

        assert result is not None
        assert result.row.is_active is False
        assert result.row.status == "inactive"
        # …and the write PERSISTED on both columns.
        fetched = skill_repo.get(skill.id)
        assert fetched.is_active is False
        assert fetched.status == "inactive"

    def test_a_desynced_row_is_fully_normalized(self, skill_repo) -> None:
        """The production shape: ``(False, 'ab_testing')`` — one column was
        already flipped, the other was not. One call must fix BOTH."""
        skill = _skill(skill_repo, "desync")
        skill_repo.update(skill.id, is_active=False, status="ab_testing")
        before = skill_repo.get(skill.id)
        assert (before.is_active, before.status) == (False, "ab_testing")

        result = skill_repo.deactivate_with_receipt(skill.id)

        assert (result.row.is_active, result.row.status) == (False, "inactive")

    def test_the_plain_deactivate_wrapper_still_works(self, skill_repo) -> None:
        """The pre-existing public surface is untouched."""
        skill = _skill(skill_repo, "legacy")
        updated = skill_repo.deactivate(skill.id)
        assert updated.is_active is False and updated.status == "inactive"


class TestThePriorPairIsSurfaced:
    def test_the_prior_pair_is_the_PRE_write_pair(self, skill_repo) -> None:
        skill = _skill(skill_repo, "prior")
        result = skill_repo.deactivate_with_receipt(skill.id)
        assert result.prior_pair() == {"is_active": True, "status": "active"}

    def test_a_desynced_prior_pair_is_preserved_verbatim(self, skill_repo) -> None:
        """The reversal must be able to restore the DESYNCED state exactly."""
        skill = _skill(skill_repo, "prior-desync")
        skill_repo.update(skill.id, is_active=False, status="ab_testing")
        result = skill_repo.deactivate_with_receipt(skill.id)
        assert result.prior_pair() == {"is_active": False, "status": "ab_testing"}

    def test_the_pair_round_trips_through_a_restore(self, skill_repo) -> None:
        """The receipt's contract: restore writes the pair back verbatim."""
        skill = _skill(skill_repo, "roundtrip")
        skill_repo.update(skill.id, is_active=False, status="ab_testing")
        result = skill_repo.deactivate_with_receipt(skill.id)
        prior = result.prior_pair()

        # The reversal (what the E4 receipt does with the surfaced pair).
        skill_repo.update(
            skill.id, is_active=prior["is_active"], status=prior["status"]
        )
        restored = skill_repo.get(skill.id)
        assert (restored.is_active, restored.status) == (False, "ab_testing")

    def test_a_missing_row_returns_none(self, skill_repo) -> None:
        assert skill_repo.deactivate_with_receipt("ghost-id") is None


class TestTheAbGuardRefuses:
    """The guard is SELF-CONTAINED: only a skill id is passed in."""

    def test_a_row_carrying_an_open_test_group_is_refused(
        self, skill_repo, ab_repo
    ) -> None:
        old = _skill(skill_repo, "guarded-old")
        new = _skill(skill_repo, "guarded-new", generation=1)
        skill_repo.update(old.id, ab_test_group="grp-open")
        _open_test(ab_repo, "grp-open", old.id, new.id)

        with pytest.raises(SkillDeactivationRefused) as excinfo:
            skill_repo.deactivate_with_receipt(old.id)

        assert excinfo.value.reason == "open_ab_test"
        assert excinfo.value.ab_test_group == "grp-open"
        assert excinfo.value.skill_id == old.id
        # …and the row is UNTOUCHED (the refusal is not a partial write).
        still = skill_repo.get(old.id)
        assert (still.is_active, still.status) == (True, "active")

    def test_a_variant_discovered_only_from_the_test_side_is_refused(
        self, skill_repo, ab_repo
    ) -> None:
        """A variant row may carry NO ``ab_test_group`` of its own.

        The guard must find the test from the TEST side, or it would let a
        guarded variant through — exactly the drift class the dual-column
        writer exists to stop.
        """
        variant = _skill(skill_repo, "test-side-variant")
        other = _skill(skill_repo, "test-side-other", generation=1)
        assert variant.ab_test_group is None
        _open_test(ab_repo, "grp-test-side", variant.id, other.id)

        with pytest.raises(SkillDeactivationRefused):
            skill_repo.deactivate_with_receipt(variant.id)

        assert skill_repo.get(variant.id).is_active is True

    def test_a_resolved_test_does_not_block(self, skill_repo, ab_repo) -> None:
        """The guard keys on ``resolved_at IS NULL`` — a resolved test frees
        the row (that is the pair-resolver's whole purpose)."""
        old = _skill(skill_repo, "resolved-old")
        new = _skill(skill_repo, "resolved-new", generation=1)
        skill_repo.update(old.id, ab_test_group="grp-resolved")
        _open_test(ab_repo, "grp-resolved", old.id, new.id)
        ab_repo.resolve(
            ab_test_group="grp-resolved", winner_skill_id=new.id
        )

        result = skill_repo.deactivate_with_receipt(old.id)
        assert result is not None
        assert (result.row.is_active, result.row.status) == (False, "inactive")

    def test_an_unguarded_row_is_not_blocked_by_another_skills_test(
        self, skill_repo, ab_repo
    ) -> None:
        guarded = _skill(skill_repo, "other-guarded")
        other = _skill(skill_repo, "other-partner", generation=1)
        skill_repo.update(guarded.id, ab_test_group="grp-other")
        _open_test(ab_repo, "grp-other", guarded.id, other.id)

        free = _skill(skill_repo, "free-row")
        result = skill_repo.deactivate_with_receipt(free.id)
        assert result is not None
        assert result.row.status == "inactive"

    def test_the_refusal_is_typed_and_matchable(self, skill_repo, ab_repo) -> None:
        old = _skill(skill_repo, "typed-old")
        new = _skill(skill_repo, "typed-new", generation=1)
        skill_repo.update(old.id, ab_test_group="grp-typed")
        _open_test(ab_repo, "grp-typed", old.id, new.id)
        with pytest.raises(SkillDeactivationRefused) as excinfo:
            skill_repo.deactivate_with_receipt(old.id)
        # A caller can match on the class OR the stable token.
        assert type(excinfo.value).__name__ == "SkillDeactivationRefused"
        assert SkillDeactivationRefused.reason == "open_ab_test"


class TestTheWriterTouchesNoOtherColumns:
    def test_name_and_content_are_untouched(self, skill_repo) -> None:
        skill = _skill(skill_repo, "keys")
        skill_repo.deactivate_with_receipt(skill.id)
        after = skill_repo.get(skill.id)
        assert (after.name, after.content) == (skill.name, skill.content)
        assert after.id == skill.id
