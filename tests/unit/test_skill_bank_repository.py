"""Unit tests for ``SkillBankRepository``.

The Skill Bank is an isolated, user-facing CRUD store backed by a
single SQLModel table (``skill_bank``). These tests exercise the
synchronous repository surface — ``create`` / ``get`` /
``list_items`` / ``update`` / ``delete`` / ``count`` — using an
in-memory SQLite engine.

The repository methods are synchronous by design; the FastAPI
router (separate suite) wraps them in ``asyncio.to_thread``. We
deliberately do NOT use ``asyncio`` here — these are pure unit
tests of the repository's data layer.

Engine fixture mirrors the patterns from
``tests/message_queue_redesign/conftest.py`` (StaticPool +
``check_same_thread=False``) so the engine could be shared with
asyncio.to_thread-based tests in the future.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Iterator

import pytest
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel

from daemon.repositories.skill.models import SkillBankItem
from daemon.repositories.skill.skill_bank_repository import SkillBankRepository


# ============================================================================
# Fixtures (module-scoped function default — clean state per test)
# ============================================================================


@pytest.fixture
def engine() -> Iterator[Engine]:
    """In-memory SQLite engine with the ``skill_bank`` table created.

    Uses ``StaticPool`` + ``check_same_thread=False`` so the same
    in-memory database is visible from every thread — required if
    future router tests wrap these calls in ``asyncio.to_thread``.

    Note: ``SQLModel.metadata.create_all(engine)`` is called rather
    than ``SkillBankItem.__table__.create(...)`` so any other
    SQLModel tables that happen to be registered (none currently,
    but defensively) are also created. For this test that is just
    the ``skill_bank`` table.
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
def repository(engine: Engine) -> SkillBankRepository:
    """``SkillBankRepository`` wired to the in-memory ``engine``."""
    return SkillBankRepository(engine)


def _parse_iso(ts: str) -> datetime:
    """Parse an ISO-8601 timestamp into a ``datetime`` for ordering checks."""
    return datetime.fromisoformat(ts)


# ============================================================================
# create / get round-trip
# ============================================================================


class TestCreate:
    """``SkillBankRepository.create`` and its round-trip with ``get``."""

    def test_create_with_defaults_round_trips(self, repository: SkillBankRepository) -> None:
        """Minimal insert (only required args) persists all defaults."""
        item = repository.create(name="default-skill", content="do the thing")

        fetched = repository.get(item.id)

        assert fetched is not None
        assert isinstance(fetched, SkillBankItem)
        assert fetched.id == item.id
        assert fetched.name == "default-skill"
        assert fetched.content == "do the thing"
        assert fetched.project_id is None
        assert fetched.description == ""
        assert fetched.category == "workflow"

    def test_create_with_explicit_project_description_category(
        self, repository: SkillBankRepository
    ) -> None:
        """All optional kwargs are stored verbatim."""
        item = repository.create(
            name="proj-skill",
            content="body",
            project_id="proj-abc",
            description="handy utility",
            category="linting",
        )

        fetched = repository.get(item.id)

        assert fetched is not None
        assert fetched.project_id == "proj-abc"
        assert fetched.description == "handy utility"
        assert fetched.category == "linting"
        assert fetched.name == "proj-skill"
        assert fetched.content == "body"

    def test_create_with_explicit_none_project_id_persists_as_null(
        self, repository: SkillBankRepository
    ) -> None:
        """Explicit ``project_id=None`` is stored as SQL NULL."""
        item = repository.create(
            name="global-skill",
            content="shared",
            project_id=None,
        )

        fetched = repository.get(item.id)

        assert fetched is not None
        assert fetched.project_id is None

    def test_create_populates_both_timestamps_as_iso(
        self, repository: SkillBankRepository
    ) -> None:
        """``created_at`` and ``updated_at`` are non-empty ISO strings."""
        item = repository.create(name="ts", content="x")

        assert isinstance(item.created_at, str) and item.created_at
        assert isinstance(item.updated_at, str) and item.updated_at

        # Round-trip through get() — these come back from SQLite
        # as plain strings and must still parse as ISO timestamps.
        assert _parse_iso(item.created_at) is not None
        assert _parse_iso(item.updated_at) is not None

    def test_create_sets_created_at_equal_to_updated_at(
        self, repository: SkillBankRepository
    ) -> None:
        """On insert, the two timestamps must be identical."""
        item = repository.create(name="ts-eq", content="x")

        assert item.created_at == item.updated_at

    def test_create_assigns_unique_ids(
        self, repository: SkillBankRepository
    ) -> None:
        """Two creates produce distinct primary keys."""
        a = repository.create(name="a", content="x")
        b = repository.create(name="b", content="y")

        assert a.id != b.id
        assert a.id  # non-empty
        assert b.id  # non-empty

    def test_create_returns_fully_refreshed_instance(
        self, repository: SkillBankRepository
    ) -> None:
        """``create()`` returns the row as stored — ``get`` sees the same data."""
        item = repository.create(name="refreshed", content="body", project_id="p1")

        assert repository.get(item.id) is not None
        assert repository.get(item.id).to_dict() == item.to_dict()


# ============================================================================
# get
# ============================================================================


class TestGet:
    """``SkillBankRepository.get`` behaviour."""

    def test_get_returns_none_for_missing_id(
        self, repository: SkillBankRepository
    ) -> None:
        """Unknown UUID4 returns ``None`` (not raises)."""
        assert repository.get("does-not-exist") is None

    def test_get_round_trips_after_create(
        self, repository: SkillBankRepository
    ) -> None:
        """A created row is fetchable by its assigned id."""
        created = repository.create(name="rt", content="body")

        fetched = repository.get(created.id)

        assert fetched is not None
        assert fetched.id == created.id
        assert fetched.name == "rt"
        assert fetched.content == "body"

    def test_get_returns_skill_bank_item_instance(
        self, repository: SkillBankRepository
    ) -> None:
        """The fetched object is a real ``SkillBankItem``, not a Row."""
        created = repository.create(name="type", content="t")

        fetched = repository.get(created.id)

        assert isinstance(fetched, SkillBankItem)


# ============================================================================
# list_items
# ============================================================================


class TestListItems:
    """``SkillBankRepository.list_items`` filtering and ordering."""

    def test_list_items_empty_db_returns_empty_list(
        self, repository: SkillBankRepository
    ) -> None:
        """No rows -> ``[]`` (not ``None``, not an exception)."""
        assert repository.list_items() == []

    def test_list_items_returns_all_when_no_filter(
        self, repository: SkillBankRepository
    ) -> None:
        """Without filters, every row is returned."""
        for i in range(3):
            repository.create(name=f"s{i}", content="x")

        rows = repository.list_items()

        assert len(rows) == 3
        names = {r.name for r in rows}
        assert names == {"s0", "s1", "s2"}

    def test_list_items_ordered_by_created_at_desc(
        self, repository: SkillBankRepository
    ) -> None:
        """Newer rows appear first (created_at DESC)."""
        first = repository.create(name="first", content="x")
        # Ensure a distinguishable timestamp on the next row. SQLite
        # text sort of ISO timestamps matches chronological order,
        # but we add a small sleep to avoid microsecond collisions.
        time.sleep(0.002)
        second = repository.create(name="second", content="x")
        time.sleep(0.002)
        third = repository.create(name="third", content="x")

        rows = repository.list_items()

        assert [r.id for r in rows] == [third.id, second.id, first.id]

    def test_list_items_filter_by_project_id(
        self, repository: SkillBankRepository
    ) -> None:
        """Only rows matching ``project_id`` are returned."""
        repository.create(name="a", content="x", project_id="p1")
        repository.create(name="b", content="x", project_id="p2")
        repository.create(name="c", content="x", project_id="p1")

        rows = repository.list_items(project_id="p1")

        assert {r.name for r in rows} == {"a", "c"}

    def test_list_items_filter_by_category(
        self, repository: SkillBankRepository
    ) -> None:
        """Only rows matching ``category`` are returned."""
        repository.create(name="a", content="x", category="workflow")
        repository.create(name="b", content="x", category="linting")
        repository.create(name="c", content="x", category="linting")

        rows = repository.list_items(category="linting")

        assert {r.name for r in rows} == {"b", "c"}

    def test_list_items_filter_by_project_and_category_is_and(
        self, repository: SkillBankRepository
    ) -> None:
        """Combined filters use AND semantics — both must match."""
        repository.create(name="a", content="x", project_id="p1", category="workflow")
        repository.create(name="b", content="x", project_id="p1", category="linting")
        repository.create(name="c", content="x", project_id="p2", category="workflow")
        repository.create(name="d", content="x", project_id="p2", category="linting")

        rows = repository.list_items(project_id="p1", category="workflow")

        assert {r.name for r in rows} == {"a"}

    def test_list_items_project_id_none_returns_across_all_projects(
        self, repository: SkillBankRepository
    ) -> None:
        """``project_id=None`` is a "no project filter" — global + scoped both come back."""
        repository.create(name="global", content="x", project_id=None)
        repository.create(name="proj-a", content="x", project_id="p1")
        repository.create(name="proj-b", content="x", project_id="p2")

        rows = repository.list_items(project_id=None)

        assert {r.name for r in rows} == {"global", "proj-a", "proj-b"}

    def test_list_items_category_none_means_no_filter(
        self, repository: SkillBankRepository
    ) -> None:
        """``category=None`` returns rows from every category."""
        repository.create(name="a", content="x", category="workflow")
        repository.create(name="b", content="x", category="linting")
        repository.create(name="c", content="x", category="deploy")

        rows = repository.list_items(category=None)

        assert {r.name for r in rows} == {"a", "b", "c"}

    def test_list_items_limit_caps_result_count(
        self, repository: SkillBankRepository
    ) -> None:
        """``limit`` truncates the result list."""
        for i in range(5):
            repository.create(name=f"s{i}", content="x")

        rows = repository.list_items(limit=2)

        assert len(rows) == 2

    def test_list_items_offset_skips_rows(
        self, repository: SkillBankRepository
    ) -> None:
        """``offset`` skips the first N rows of the (ordered) result set."""
        ids = []
        for i in range(4):
            ids.append(repository.create(name=f"s{i}", content="x").id)
            time.sleep(0.001)  # ensure distinct created_at ordering

        # Default ordering is created_at DESC → ids reversed.
        expected_after_offset = list(reversed(ids))[2:]

        rows = repository.list_items(offset=2)

        assert [r.id for r in rows] == expected_after_offset

    def test_list_items_limit_and_offset_combined(
        self, repository: SkillBankRepository
    ) -> None:
        """``offset`` skips first N, ``limit`` caps the remaining window."""
        ids = []
        for i in range(5):
            ids.append(repository.create(name=f"s{i}", content="x").id)
            time.sleep(0.001)

        # DESC order: ids[4], ids[3], ids[2], ids[1], ids[0].
        rows = repository.list_items(limit=2, offset=1)

        assert [r.id for r in rows] == [ids[3], ids[2]]

    def test_list_items_limit_zero_returns_empty(
        self, repository: SkillBankRepository
    ) -> None:
        """``limit=0`` returns an empty list (documented behavior)."""
        repository.create(name="only", content="x")

        rows = repository.list_items(limit=0)

        assert rows == []


# ============================================================================
# update
# ============================================================================


class TestUpdate:
    """``SkillBankRepository.update`` semantics."""

    def test_update_changes_name(
        self, repository: SkillBankRepository
    ) -> None:
        """Single-field update changes only that field."""
        created = repository.create(name="old", content="x")
        original_updated_at = created.updated_at

        time.sleep(0.002)  # ensure bumped timestamp differs
        updated = repository.update(created.id, name="new")

        assert updated is not None
        assert updated.name == "new"
        fetched = repository.get(created.id)
        assert fetched.name == "new"

        # updated_at must be strictly later than the original.
        assert _parse_iso(updated.updated_at) > _parse_iso(original_updated_at)

    def test_update_multiple_fields_only_changes_those(
        self, repository: SkillBankRepository
    ) -> None:
        """Multi-field update: untouched fields keep their values."""
        created = repository.create(
            name="orig",
            content="orig-content",
            description="orig-desc",
            category="workflow",
            project_id="p1",
        )

        updated = repository.update(
            created.id,
            name="new-name",
            description="new-desc",
        )

        assert updated is not None
        assert updated.name == "new-name"
        assert updated.description == "new-desc"
        # Untouched fields preserved.
        assert updated.content == "orig-content"
        assert updated.category == "workflow"
        assert updated.project_id == "p1"

    def test_update_unknown_field_raises_attribute_error(
        self, repository: SkillBankRepository
    ) -> None:
        """Unknown columns raise ``AttributeError`` (not silently dropped)."""
        created = repository.create(name="x", content="x")

        with pytest.raises(AttributeError):
            repository.update(created.id, not_a_column="foo")

    def test_update_protected_id_field_is_silently_ignored(
        self, repository: SkillBankRepository
    ) -> None:
        """``id`` is a protected key — passed in ``**fields`` it is dropped."""
        created = repository.create(name="x", content="x")

        updated = repository.update(created.id, id="forged-uuid", name="renamed")

        assert updated is not None
        assert updated.id == created.id  # NOT replaced
        assert updated.name == "renamed"  # other fields still applied

    def test_update_protected_created_at_field_is_silently_ignored(
        self, repository: SkillBankRepository
    ) -> None:
        """``created_at`` is a protected key — ``updated_at`` bumps instead."""
        created = repository.create(name="x", content="x")
        original_created_at = created.created_at
        original_updated_at = created.updated_at

        time.sleep(0.002)
        # Try to overwrite both with bogus values.
        updated = repository.update(
            created.id,
            created_at="1970-01-01T00:00:00+00:00",
            updated_at="1970-01-01T00:00:00+00:00",
            name="renamed",
        )

        assert updated is not None
        assert updated.created_at == original_created_at  # unchanged
        # updated_at is bumped to current time (NOT the bogus value).
        assert _parse_iso(updated.updated_at) > _parse_iso(original_updated_at)
        assert updated.name == "renamed"

    def test_update_bumps_updated_at_even_with_no_actual_field_changes(
        self, repository: SkillBankRepository
    ) -> None:
        """An update call always advances ``updated_at`` (per-repo contract)."""
        created = repository.create(name="x", content="x")
        original_updated_at = created.updated_at

        time.sleep(0.002)
        # Pass only a no-op-style valid field that doesn't really mutate.
        # We use ``description`` set to its current value to exercise
        # the code path without changing application-visible state.
        updated = repository.update(created.id, description=created.description)

        assert updated is not None
        assert _parse_iso(updated.updated_at) > _parse_iso(original_updated_at)

    def test_update_nonexistent_id_returns_none(
        self, repository: SkillBankRepository
    ) -> None:
        """Updating a missing id is a no-op that returns ``None``."""
        assert repository.update("missing-id", name="new") is None

    def test_update_persists_changes_across_get(
        self, repository: SkillBankRepository
    ) -> None:
        """Subsequent ``get`` returns the updated row."""
        created = repository.create(name="x", content="x")

        repository.update(created.id, name="y", content="z")

        fetched = repository.get(created.id)
        assert fetched is not None
        assert fetched.name == "y"
        assert fetched.content == "z"

    def test_update_does_not_mutate_other_rows(
        self, repository: SkillBankRepository
    ) -> None:
        """Updating one row leaves siblings untouched."""
        a = repository.create(name="a", content="x")
        b = repository.create(name="b", content="x")

        repository.update(a.id, name="a-renamed")

        assert repository.get(b.id).name == "b"


# ============================================================================
# delete
# ============================================================================


class TestDelete:
    """``SkillBankRepository.delete`` semantics."""

    def test_delete_existing_returns_true(
        self, repository: SkillBankRepository
    ) -> None:
        """Deleting an existing row returns ``True``."""
        created = repository.create(name="x", content="x")

        assert repository.delete(created.id) is True

    def test_delete_existing_then_get_returns_none(
        self, repository: SkillBankRepository
    ) -> None:
        """After delete, ``get`` returns ``None`` (hard delete)."""
        created = repository.create(name="x", content="x")

        repository.delete(created.id)

        assert repository.get(created.id) is None

    def test_delete_nonexistent_returns_false(
        self, repository: SkillBankRepository
    ) -> None:
        """Deleting a missing id returns ``False`` (no raise)."""
        assert repository.delete("missing-id") is False

    def test_delete_removes_row_from_list_items(
        self, repository: SkillBankRepository
    ) -> None:
        """After delete, ``list_items`` does not include the row."""
        keep = repository.create(name="keep", content="x")
        gone = repository.create(name="gone", content="x")

        repository.delete(gone.id)

        names = {r.name for r in repository.list_items()}
        assert names == {"keep"}
        # And specifically the deleted id is gone.
        assert all(r.id != gone.id for r in repository.list_items())
        # Sanity: the kept id is still there.
        assert keep.id in {r.id for r in repository.list_items()}

    def test_delete_is_idempotent_only_first_succeeds(
        self, repository: SkillBankRepository
    ) -> None:
        """Second delete of the same id returns ``False``."""
        created = repository.create(name="x", content="x")

        assert repository.delete(created.id) is True
        assert repository.delete(created.id) is False


# ============================================================================
# count
# ============================================================================


class TestCount:
    """``SkillBankRepository.count`` semantics."""

    def test_count_empty_db_returns_zero(
        self, repository: SkillBankRepository
    ) -> None:
        """No rows -> 0."""
        assert repository.count() == 0

    def test_count_no_filters_matches_list_items_length(
        self, repository: SkillBankRepository
    ) -> None:
        """``count()`` and ``len(list_items())`` agree with no filters."""
        for i in range(4):
            repository.create(name=f"s{i}", content="x")

        assert repository.count() == len(repository.list_items())
        assert repository.count() == 4

    def test_count_by_project_id(
        self, repository: SkillBankRepository
    ) -> None:
        """Counts only rows matching ``project_id``."""
        repository.create(name="a", content="x", project_id="p1")
        repository.create(name="b", content="x", project_id="p1")
        repository.create(name="c", content="x", project_id="p2")

        assert repository.count(project_id="p1") == 2
        assert repository.count(project_id="p2") == 1

    def test_count_by_category(
        self, repository: SkillBankRepository
    ) -> None:
        """Counts only rows matching ``category``."""
        repository.create(name="a", content="x", category="workflow")
        repository.create(name="b", content="x", category="linting")
        repository.create(name="c", content="x", category="linting")

        assert repository.count(category="workflow") == 1
        assert repository.count(category="linting") == 2

    def test_count_by_project_and_category_is_and(
        self, repository: SkillBankRepository
    ) -> None:
        """Combined filters AND together."""
        repository.create(name="a", content="x", project_id="p1", category="workflow")
        repository.create(name="b", content="x", project_id="p1", category="linting")
        repository.create(name="c", content="x", project_id="p2", category="workflow")

        assert repository.count(project_id="p1", category="workflow") == 1
        assert repository.count(project_id="p1", category="linting") == 1
        assert repository.count(project_id="p2", category="workflow") == 1

    def test_count_project_id_none_counts_across_all_projects(
        self, repository: SkillBankRepository
    ) -> None:
        """``project_id=None`` is a "no project filter" — global + scoped both counted."""
        repository.create(name="global", content="x", project_id=None)
        repository.create(name="proj-a", content="x", project_id="p1")
        repository.create(name="proj-b", content="x", project_id="p2")
        repository.create(name="proj-c", content="x", project_id="p1")

        assert repository.count(project_id=None) == 4

    def test_count_after_delete_decrements(
        self, repository: SkillBankRepository
    ) -> None:
        """``count()`` reflects hard deletes."""
        a = repository.create(name="a", content="x")
        repository.create(name="b", content="x")

        assert repository.count() == 2
        repository.delete(a.id)
        assert repository.count() == 1


# ============================================================================
# Edge cases — cross-cutting behaviors
# ============================================================================


class TestEdgeCases:
    """Cross-cutting edge cases for the repository surface."""

    def test_get_missing_id_is_none(self, repository: SkillBankRepository) -> None:
        """``get`` on an unknown id is ``None`` (no raise)."""
        assert repository.get("not-a-real-uuid") is None

    def test_update_missing_id_is_none(self, repository: SkillBankRepository) -> None:
        """``update`` on an unknown id returns ``None`` (no raise)."""
        assert repository.update("not-a-real-uuid", name="x") is None

    def test_delete_missing_id_is_false(self, repository: SkillBankRepository) -> None:
        """``delete`` on an unknown id returns ``False`` (no raise)."""
        assert repository.delete("not-a-real-uuid") is False

    def test_round_trip_create_get_update_get(
        self, repository: SkillBankRepository
    ) -> None:
        """Full lifecycle: create → get → update → get reflects all changes."""
        created = repository.create(name="orig", content="orig-body")

        first_get = repository.get(created.id)
        assert first_get.name == "orig"
        assert first_get.content == "orig-body"

        time.sleep(0.002)
        updated = repository.update(
            created.id,
            name="updated",
            content="updated-body",
            description="with-desc",
            category="linting",
        )
        assert updated is not None

        second_get = repository.get(created.id)
        assert second_get.name == "updated"
        assert second_get.content == "updated-body"
        assert second_get.description == "with-desc"
        assert second_get.category == "linting"
        # Original immutable timestamp preserved.
        assert second_get.created_at == created.created_at
        # updated_at bumped past the original.
        assert _parse_iso(second_get.updated_at) > _parse_iso(created.updated_at)

    def test_to_dict_round_trip_matches_repository_state(
        self, repository: SkillBankRepository
    ) -> None:
        """``SkillBankItem.to_dict()`` exposes every persisted column."""
        created = repository.create(
            name="d",
            content="body",
            project_id="p1",
            description="desc",
            category="cat",
        )

        d = created.to_dict()

        assert d == {
            "id": created.id,
            "project_id": "p1",
            "name": "d",
            "description": "desc",
            "content": "body",
            "category": "cat",
            "template_version": "1.0.0",
            "agent_id": None,
            "auto_load": False,
            "created_at": created.created_at,
            "updated_at": created.updated_at,
        }

    def test_repository_isolated_per_engine(
        self, engine: Engine
    ) -> None:
        """Two repositories on the same engine share the same data
        (sanity check that the engine fixture is wired correctly).

        This is a meta-test of the fixture itself — not the repo —
        but it's a useful guard against future regressions where the
        engine fixture might accidentally be module-scoped and bleed
        state between tests.
        """
        repo_a = SkillBankRepository(engine)
        repo_b = SkillBankRepository(engine)

        created = repo_a.create(name="shared", content="x")

        assert repo_b.get(created.id) is not None


# ============================================================================
# Phase 2: template_version + agent_id + auto_load fields (skill evolution)
# ============================================================================


class TestCreateWithPhase2Fields:
    """``create()`` accepts and persists ``template_version``,
    ``agent_id``, and ``auto_load`` (Phase 2 of tester-skill-evolution).
    """

    def test_create_with_template_version_persists(
        self, repository: SkillBankRepository
    ) -> None:
        """Explicit ``template_version`` survives the round-trip."""
        item = repository.create(
            name="vtest",
            content="body",
            template_version="2.3.4",
        )

        fetched = repository.get(item.id)

        assert fetched is not None
        assert fetched.template_version == "2.3.4"

    def test_create_with_agent_id_persists(
        self, repository: SkillBankRepository
    ) -> None:
        """Explicit ``agent_id`` survives the round-trip."""
        item = repository.create(
            name="agentest",
            content="body",
            agent_id="tester",
        )

        fetched = repository.get(item.id)

        assert fetched is not None
        assert fetched.agent_id == "tester"

    def test_create_with_auto_load_persists(
        self, repository: SkillBankRepository
    ) -> None:
        """Explicit ``auto_load=True`` survives the round-trip."""
        item = repository.create(
            name="autoloadtest",
            content="body",
            auto_load=True,
        )

        fetched = repository.get(item.id)

        assert fetched is not None
        assert fetched.auto_load is True

    def test_create_defaults_template_version_is_1_0_0(
        self, repository: SkillBankRepository
    ) -> None:
        """Default ``template_version`` is the documented sentinel."""
        item = repository.create(name="default-v", content="body")

        fetched = repository.get(item.id)

        assert fetched is not None
        assert fetched.template_version == "1.0.0"

    def test_create_defaults_agent_id_is_none(
        self, repository: SkillBankRepository
    ) -> None:
        """Default ``agent_id`` is ``None`` (generic/shared template)."""
        item = repository.create(name="default-a", content="body")

        fetched = repository.get(item.id)

        assert fetched is not None
        assert fetched.agent_id is None

    def test_create_defaults_auto_load_is_false(
        self, repository: SkillBankRepository
    ) -> None:
        """Default ``auto_load`` is ``False`` (on-demand only)."""
        item = repository.create(name="default-l", content="body")

        fetched = repository.get(item.id)

        assert fetched is not None
        assert fetched.auto_load is False

    def test_create_with_all_phase2_fields_round_trips(
        self, repository: SkillBankRepository
    ) -> None:
        """All three Phase 2 fields set together round-trip cleanly."""
        item = repository.create(
            name="all",
            content="body",
            template_version="3.1.0",
            agent_id="tester",
            auto_load=True,
        )

        fetched = repository.get(item.id)

        assert fetched is not None
        assert fetched.template_version == "3.1.0"
        assert fetched.agent_id == "tester"
        assert fetched.auto_load is True

    def test_update_auto_load_field(
        self, repository: SkillBankRepository
    ) -> None:
        """``update()`` can flip ``auto_load`` on an existing row."""
        created = repository.create(name="upd-l", content="x", auto_load=False)

        updated = repository.update(created.id, auto_load=True)

        assert updated is not None
        assert updated.auto_load is True
        # And the value is persisted (not just returned from memory).
        fetched = repository.get(created.id)
        assert fetched is not None
        assert fetched.auto_load is True

    def test_update_template_version_field(
        self, repository: SkillBankRepository
    ) -> None:
        """``update()`` can bump ``template_version`` on an existing row."""
        created = repository.create(
            name="upd-v", content="x", template_version="1.0.0"
        )

        updated = repository.update(created.id, template_version="1.0.1")

        assert updated is not None
        assert updated.template_version == "1.0.1"

    def test_update_agent_id_field(
        self, repository: SkillBankRepository
    ) -> None:
        """``update()`` can set ``agent_id`` on an existing row."""
        created = repository.create(name="upd-a", content="x", agent_id=None)

        updated = repository.update(created.id, agent_id="developer")

        assert updated is not None
        assert updated.agent_id == "developer"


class TestGetByNameAndAgent:
    """``SkillBankRepository.get_by_name_and_agent``."""

    def test_returns_correct_record_for_match(
        self, repository: SkillBankRepository
    ) -> None:
        """Returns the row whose name + agent_id both match."""
        repository.create(name="alpha", content="x", agent_id="tester")
        target = repository.create(
            name="beta", content="y", agent_id="tester"
        )
        repository.create(name="beta", content="z", agent_id="developer")

        fetched = repository.get_by_name_and_agent("beta", "tester")

        assert fetched is not None
        assert fetched.id == target.id
        assert fetched.content == "y"

    def test_returns_none_for_wrong_agent(
        self, repository: SkillBankRepository
    ) -> None:
        """Wrong agent_id returns ``None`` — not a row from another agent."""
        repository.create(name="gamma", content="x", agent_id="tester")

        fetched = repository.get_by_name_and_agent("gamma", "developer")

        assert fetched is None

    def test_returns_none_for_wrong_name(
        self, repository: SkillBankRepository
    ) -> None:
        """Wrong name returns ``None`` even when the agent_id exists."""
        repository.create(name="delta", content="x", agent_id="tester")

        fetched = repository.get_by_name_and_agent(
            "non-existent", "tester"
        )

        assert fetched is None

    def test_returns_none_when_table_empty(
        self, repository: SkillBankRepository
    ) -> None:
        """Empty bank returns ``None`` (not raise)."""
        assert (
            repository.get_by_name_and_agent("anything", "tester") is None
        )


class TestGetByNameAnyAgent:
    """``SkillBankRepository.get_by_name_any_agent``.

    The name-only fallback used by the clone-on-miss path when
    the exact ``(name, agent_id)`` lookup misses — covers the
    dispatcher-spawns-child-from-another-agent case (e.g. tester
    dispatches ``load_skill="unit-test"`` onto a worker instance).
    """

    def test_returns_row_regardless_of_owning_agent(
        self, repository: SkillBankRepository
    ) -> None:
        """Lookup without an agent filter finds any matching row."""
        target = repository.create(
            name="unit-test", content="x", agent_id="tester"
        )

        fetched = repository.get_by_name_any_agent("unit-test")

        assert fetched is not None
        assert fetched.id == target.id

    def test_returns_none_when_no_name_match(
        self, repository: SkillBankRepository
    ) -> None:
        """When no row matches the name at all, returns ``None``."""
        repository.create(name="other", content="x", agent_id="tester")

        assert repository.get_by_name_any_agent("missing") is None

    def test_returns_none_when_table_empty(
        self, repository: SkillBankRepository
    ) -> None:
        """Empty bank returns ``None`` (not raise)."""
        assert repository.get_by_name_any_agent("anything") is None

    def test_newest_wins_on_collision(
        self, repository: SkillBankRepository
    ) -> None:
        """When multiple agents own the same name, the most
        recently created row is returned (deterministic choice).
        """
        repository.create(
            name="shared", content="older", agent_id="tester"
        )
        newest = repository.create(
            name="shared", content="newer", agent_id="developer"
        )

        fetched = repository.get_by_name_any_agent("shared")

        assert fetched is not None
        assert fetched.id == newest.id
        assert fetched.content == "newer"


class TestGetAutoLoadByAgent:
    """``SkillBankRepository.get_auto_load_by_agent``."""

    def test_returns_only_auto_load_true(
        self, repository: SkillBankRepository
    ) -> None:
        """Only rows with ``auto_load=True`` are returned."""
        on = repository.create(
            name="on", content="x", agent_id="tester", auto_load=True
        )
        repository.create(
            name="off", content="x", agent_id="tester", auto_load=False
        )

        rows = repository.get_auto_load_by_agent("tester")

        assert len(rows) == 1
        assert rows[0].id == on.id

    def test_filters_by_agent(
        self, repository: SkillBankRepository
    ) -> None:
        """Rows for other agents are excluded."""
        repository.create(
            name="other-on",
            content="x",
            agent_id="developer",
            auto_load=True,
        )
        target = repository.create(
            name="self-on",
            content="x",
            agent_id="tester",
            auto_load=True,
        )

        rows = repository.get_auto_load_by_agent("tester")

        assert len(rows) == 1
        assert rows[0].id == target.id

    def test_returns_empty_list_when_no_match(
        self, repository: SkillBankRepository
    ) -> None:
        """Empty bank returns ``[]`` (not raise, not ``None``)."""
        rows = repository.get_auto_load_by_agent("tester")

        assert rows == []

    def test_returns_all_when_all_auto_load(
        self, repository: SkillBankRepository
    ) -> None:
        """When every row is auto_load=True, all are returned."""
        for i in range(3):
            repository.create(
                name=f"all-{i}",
                content="x",
                agent_id="tester",
                auto_load=True,
            )

        rows = repository.get_auto_load_by_agent("tester")

        assert len(rows) == 3


class TestListByAgent:
    """``SkillBankRepository.list_by_agent``."""

    def test_returns_all_rows_for_agent(
        self, repository: SkillBankRepository
    ) -> None:
        """All rows for an agent are returned regardless of ``auto_load``."""
        repository.create(
            name="a", content="x", agent_id="tester", auto_load=True
        )
        repository.create(
            name="b", content="x", agent_id="tester", auto_load=False
        )

        rows = repository.list_by_agent("tester")

        assert len(rows) == 2
        names = {r.name for r in rows}
        assert names == {"a", "b"}

    def test_excludes_other_agents(
        self, repository: SkillBankRepository
    ) -> None:
        """Rows for other agents are not returned."""
        repository.create(name="x", content="x", agent_id="tester")
        repository.create(name="y", content="x", agent_id="developer")

        rows = repository.list_by_agent("tester")

        assert len(rows) == 1
        assert rows[0].name == "x"

    def test_returns_empty_list_when_no_match(
        self, repository: SkillBankRepository
    ) -> None:
        """Empty bank returns ``[]``."""
        rows = repository.list_by_agent("tester")

        assert rows == []

    def test_excludes_rows_with_null_agent_id(
        self, repository: SkillBankRepository
    ) -> None:
        """Generic/shared rows (``agent_id IS NULL``) are not returned."""
        repository.create(
            name="global", content="x", agent_id=None, auto_load=True
        )
        repository.create(
            name="scoped", content="x", agent_id="tester", auto_load=True
        )

        rows = repository.list_by_agent("tester")

        names = {r.name for r in rows}
        assert names == {"scoped"}