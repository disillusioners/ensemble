"""Unit tests for the InstanceUiPrefs repository.

Validates the lazy-create / partial-update semantics and the
``pinned_at`` side-effect that the
``PUT /api/instances/{id}/ui-prefs`` endpoint relies on.

All tests run against the in-memory SQLite ``engine`` fixture (see
``tests/repositories/conftest.py``), which creates the
``instance_ui_prefs`` table via ``SQLModel.metadata.create_all``. The
``engine`` fixture also pulls in the six skill tables (and any other
SQLModel registered via the import chain), so importing
:class:`InstanceUiPrefs` at module level here is what registers the
table on ``SQLModel.metadata`` before ``create_all`` runs.
"""

from __future__ import annotations

import pytest

from daemon.repositories.instance_ui_prefs import (
    InstanceUiPrefs,
    InstanceUiPrefsRepository,
)


# Importing InstanceUiPrefs at module level ensures the table gets
# registered on SQLModel.metadata when the ``engine`` fixture calls
# ``SQLModel.metadata.create_all`` — exactly the same pattern used by
# ``test_report_injection.py`` and the skill tests.
_ = InstanceUiPrefs


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def repo(engine) -> InstanceUiPrefsRepository:
    """An :class:`InstanceUiPrefsRepository` bound to the test engine."""
    return InstanceUiPrefsRepository(engine)


# =============================================================================
# upsert — lazy create + partial update
# =============================================================================


class TestUpsertCreates:
    """Lazy first-time row creation with proper defaults."""

    def test_upsert_creates_new_row_with_defaults(self, repo, engine):
        """A plain ``upsert(id)`` (no pinned / color_tag) creates a row
        with the not-pinned defaults."""
        row = repo.upsert("inst-1")

        assert row.instance_id == "inst-1"
        assert row.pinned is False
        assert row.pinned_at is None
        assert row.color_tag is None
        assert row.icon_tag is None
        assert row.created_at is not None
        assert row.updated_at is not None

    def test_upsert_persists_row_in_db(self, repo, engine):
        """The row created by upsert is actually persisted and re-readable."""
        repo.upsert("inst-1", pinned=True, color_tag="red")

        # Drop session: a fresh get() must see the row.
        fetched = repo.get("inst-1")
        assert fetched is not None
        assert fetched.instance_id == "inst-1"
        assert fetched.pinned is True
        assert fetched.color_tag == "red"


class TestUpsertPinnedSideEffect:
    """The ``pinned_at`` automatic-stamp behavior."""

    def test_upsert_sets_pinned_at_when_pinned_true(self, repo, engine):
        """``upsert(id, pinned=True)`` stamps ``pinned_at`` with the
        current UTC timestamp (must be a non-empty ISO-8601 string)."""
        row = repo.upsert("inst-1", pinned=True)
        assert row.pinned is True
        assert row.pinned_at is not None
        assert isinstance(row.pinned_at, str)
        assert "T" in row.pinned_at  # ISO-8601 marker

    def test_upsert_clears_pinned_at_when_pinned_false(self, repo, engine):
        """``upsert(id, pinned=False)`` CLEARs ``pinned_at`` even if the
        instance was previously pinned."""
        repo.upsert("inst-1", pinned=True)
        first = repo.get("inst-1")
        assert first is not None and first.pinned_at is not None

        # Now unpin.
        row = repo.upsert("inst-1", pinned=False)
        assert row.pinned is False
        assert row.pinned_at is None

    def test_upsert_omitting_pinned_keeps_existing_pinned_at(self, repo, engine):
        """``upsert(id, color_tag=...)`` (no pinned arg) MUST leave the
        existing ``pinned`` and ``pinned_at`` untouched — partial-update
        semantics."""
        repo.upsert("inst-1", pinned=True)
        original = repo.get("inst-1")
        assert original is not None and original.pinned_at is not None
        original_pinned_at = original.pinned_at

        repo.upsert("inst-1", color_tag="blue")
        after = repo.get("inst-1")
        assert after is not None
        assert after.pinned is True
        # CRITICAL: pinned_at is NOT bumped on a color-only update.
        assert after.pinned_at == original_pinned_at
        assert after.color_tag == "blue"


class TestUpsertPartialUpdate:
    """Partial-update semantics — only the passed fields change."""

    def test_partial_update_only_pinned_keeps_color_tag(self, repo, engine):
        """Updating only ``pinned`` preserves the existing color_tag."""
        repo.upsert("inst-1", color_tag="green")
        repo.upsert("inst-1", pinned=True)

        row = repo.get("inst-1")
        assert row is not None
        assert row.pinned is True
        assert row.color_tag == "green"  # preserved
        assert row.pinned_at is not None

    def test_partial_update_only_color_tag_keeps_pinned_and_pinned_at(
        self, repo, engine
    ):
        """Updating only ``color_tag`` preserves both ``pinned`` and
        ``pinned_at`` (the latter with the SAME value — not bumped)."""
        repo.upsert("inst-1", pinned=True)
        pinned_at_before = repo.get("inst-1").pinned_at
        assert pinned_at_before is not None

        repo.upsert("inst-1", color_tag="orange")

        row = repo.get("inst-1")
        assert row is not None
        assert row.color_tag == "orange"
        assert row.pinned is True  # preserved
        assert row.pinned_at == pinned_at_before  # NOT bumped

    def test_upsert_pinned_true_on_existing_row_keeps_color_tag_and_refreshes_pinned_at(
        self, repo, engine
    ):
        """A second ``upsert(pinned=True)`` on a previously pinned row
        refreshes ``pinned_at`` (re-stamps) and keeps the color tag."""
        repo.upsert("inst-1", pinned=True, color_tag="cyan")
        original_pinned_at = repo.get("inst-1").pinned_at
        assert original_pinned_at is not None

        # Re-pin: a fresh call with pinned=True should produce a NEW
        # pinned_at (>= original — they live in the same timezone so
        # lexicographic ISO-8601 ordering matches chronological).
        row = repo.upsert("inst-1", pinned=True)
        assert row.color_tag == "cyan"
        assert row.pinned is True
        assert row.pinned_at is not None
        assert row.pinned_at >= original_pinned_at

    def test_upsert_clear_color_tag_explicit(self, repo, engine):
        """``upsert(id, color_tag=None, clear_color_tag=True)`` CLEARs the
        existing color tag to None — the explicit-clear path the frontend
        uses when sending ``{"color_tag": null}``.

        Control assertion: passing ``color_tag=None`` WITHOUT
        ``clear_color_tag=True`` must leave the existing color unchanged
        (preserves partial-update semantics).
        """
        # Seed with a color tag.
        repo.upsert("inst-1", color_tag="blue")
        seeded = repo.get("inst-1")
        assert seeded is not None and seeded.color_tag == "blue"

        # Control: color_tag=None without clear_color_tag → leave unchanged.
        repo.upsert("inst-1", color_tag=None)
        control = repo.get("inst-1")
        assert control is not None
        assert control.color_tag == "blue"  # preserved

        # Explicit clear: color_tag=None WITH clear_color_tag=True → cleared.
        repo.upsert("inst-1", color_tag=None, clear_color_tag=True)
        cleared = repo.get("inst-1")
        assert cleared is not None
        assert cleared.color_tag is None

    def test_upsert_icon_tag_create(self, repo, engine):
        """``upsert(id, icon_tag="star")`` on a non-existent instance
        creates a fresh row with ``icon_tag="star"`` (and all other
        fields at their not-pinned / no-color defaults).

        Mirrors the existing ``test_upsert_persists_row_in_db`` shape
        — verifies the lazy-create path stamps the icon_tag on first
        touch, not just on partial-update.
        """
        row = repo.upsert("inst-1", icon_tag="star")

        assert row.instance_id == "inst-1"
        assert row.icon_tag == "star"
        assert row.pinned is False  # default
        assert row.color_tag is None  # default

        # And it was actually written to the DB.
        fetched = repo.get("inst-1")
        assert fetched is not None
        assert fetched.icon_tag == "star"

    def test_upsert_icon_tag_partial_update(self, repo, engine):
        """``upsert(id, icon_tag="flag")`` on an existing row that
        already has a color_tag updates ONLY ``icon_tag`` — the
        existing ``color_tag`` is preserved (partial-update
        semantics, mirroring ``test_partial_update_only_color_tag_keeps_pinned_and_pinned_at``).
        """
        # Seed with both a color tag and an initial icon tag.
        repo.upsert("inst-1", color_tag="blue", icon_tag="star")
        seeded = repo.get("inst-1")
        assert seeded is not None
        assert seeded.color_tag == "blue"
        assert seeded.icon_tag == "star"

        # Update only the icon tag.
        repo.upsert("inst-1", icon_tag="flag")

        row = repo.get("inst-1")
        assert row is not None
        assert row.icon_tag == "flag"  # updated
        assert row.color_tag == "blue"  # preserved (the partial-update guarantee)

    def test_upsert_clear_icon_tag_explicit(self, repo, engine):
        """``upsert(id, icon_tag=None, clear_icon_tag=True)`` CLEARs the
        existing icon tag to None — the explicit-clear path the frontend
        uses when sending ``{"icon_tag": null}``.

        Control assertion: passing ``icon_tag=None`` WITHOUT
        ``clear_icon_tag=True`` must leave the existing icon tag
        unchanged (preserves partial-update semantics). This mirrors
        ``test_upsert_clear_color_tag_explicit`` and is the symmetric
        tri-state guard for the new field.
        """
        # Seed with an icon tag (and a control color tag to prove
        # clearing icon_tag does NOT touch unrelated fields).
        repo.upsert("inst-1", icon_tag="star", color_tag="red")
        seeded = repo.get("inst-1")
        assert seeded is not None and seeded.icon_tag == "star"

        # Control: icon_tag=None without clear_icon_tag → leave unchanged.
        repo.upsert("inst-1", icon_tag=None)
        control = repo.get("inst-1")
        assert control is not None
        assert control.icon_tag == "star"  # preserved
        assert control.color_tag == "red"  # untouched

        # Explicit clear: icon_tag=None WITH clear_icon_tag=True → cleared.
        repo.upsert("inst-1", icon_tag=None, clear_icon_tag=True)
        cleared = repo.get("inst-1")
        assert cleared is not None
        assert cleared.icon_tag is None
        # The color_tag must remain untouched during an icon-only clear.
        assert cleared.color_tag == "red"



# =============================================================================


class TestGet:
    """Single-row lookup behavior."""

    def test_get_returns_none_for_non_existent(self, repo, engine):
        """``get`` returns ``None`` when no row exists for the id (the
        common case — most instances have never been pinned/tagged)."""
        assert repo.get("never-existed") is None

    def test_get_returns_row_when_exists(self, repo, engine):
        """``get`` returns the row when it exists."""
        repo.upsert("inst-1", pinned=True, color_tag="red")
        row = repo.get("inst-1")
        assert row is not None
        assert row.instance_id == "inst-1"
        assert row.pinned is True
        assert row.color_tag == "red"


# =============================================================================
# get_all — batch fetch
# =============================================================================


class TestGetAll:
    """Batch lookup for the ``list_instances`` merge step."""

    def test_get_all_returns_dict_keyed_by_instance_id(self, repo, engine):
        """``get_all`` returns a ``dict`` mapping each present
        ``instance_id`` to its row; missing ids are absent (NOT
        stored as ``None``)."""
        repo.upsert("inst-1", pinned=True, color_tag="red")
        repo.upsert("inst-2", color_tag="blue")
        repo.upsert("inst-3")  # exists but with defaults

        result = repo.get_all(["inst-1", "inst-2", "inst-3"])

        assert isinstance(result, dict)
        assert set(result.keys()) == {"inst-1", "inst-2", "inst-3"}
        assert result["inst-1"].pinned is True
        assert result["inst-1"].color_tag == "red"
        assert result["inst-2"].color_tag == "blue"
        assert result["inst-2"].pinned is False  # default
        assert result["inst-3"].pinned is False
        assert result["inst-3"].color_tag is None

    def test_get_all_empty_list_returns_empty_dict_without_error(
        self, repo, engine
    ):
        """``get_all([])`` short-circuits to ``{}`` without querying.

        This is the defensive guard that keeps a degenerate page (zero
        instances) from issuing an empty ``IN ()`` query (which can
        raise on some dialects).
        """
        result = repo.get_all([])
        assert result == {}

    def test_get_all_only_returns_existing_rows(self, repo, engine):
        """Instances that have no prefs row are simply omitted from
        the result dict (the merge step treats absence as "no override")."""
        repo.upsert("inst-1", pinned=True)

        result = repo.get_all(["inst-1", "inst-missing-a", "inst-missing-b"])

        assert set(result.keys()) == {"inst-1"}
        assert result["inst-1"].pinned is True

    def test_get_all_empty_list_with_engine_containing_rows(
        self, repo, engine
    ):
        """Even when the table is non-empty, an empty input list still
        returns ``{}`` (no DB hit)."""
        repo.upsert("inst-1")
        repo.upsert("inst-2")

        assert repo.get_all([]) == {}


# =============================================================================
# get_pinned_instance_ids — bulk lookup used by CheckpointCleanupJob
# =============================================================================


class TestGetPinnedInstanceIds:
    """``get_pinned_instance_ids`` returns the set of pinned instance IDs.

    Used by :class:`CheckpointCleanupJob` to compute the set of
    protected instances that must be excluded from TTL-based and
    history-cap cleanup. The result is always a ``set`` so the cleanup
    job can do O(1) ``in protected`` checks during batch filtering.
    """

    def test_returns_set(self, repo):
        """Empty DB → empty set (not list, not None)."""
        result = repo.get_pinned_instance_ids()
        assert isinstance(result, set)
        assert result == set()

    def test_only_pinned_rows_returned(self, repo, engine):
        """Rows where ``pinned=False`` (the default) are excluded."""
        # Pin inst-1, leave inst-2 unpinned, inst-3 unpinned.
        repo.upsert("inst-1", pinned=True)
        repo.upsert("inst-2")  # not pinned
        repo.upsert("inst-3", color_tag="red")  # not pinned

        result = repo.get_pinned_instance_ids()

        assert isinstance(result, set)
        assert result == {"inst-1"}

    def test_excludes_unpinned_after_pin_then_unpin(self, repo, engine):
        """An instance that was pinned then unpinned is NOT in the result."""
        repo.upsert("inst-1", pinned=True)
        repo.upsert("inst-2", pinned=True)
        repo.upsert("inst-1", pinned=False)  # unpin

        result = repo.get_pinned_instance_ids()

        assert result == {"inst-2"}

    def test_multiple_pinned_all_returned(self, repo, engine):
        """All pinned rows appear in the set."""
        for i in range(5):
            repo.upsert(f"inst-{i}", pinned=True)
        # Add one unpinned row to verify it doesn't leak.
        repo.upsert("inst-extra", color_tag="blue")

        result = repo.get_pinned_instance_ids()

        assert result == {f"inst-{i}" for i in range(5)}


# =============================================================================
# delete — row removal
# =============================================================================


class TestDelete:
    """``delete`` returns ``True`` on a successful remove, ``False`` on miss."""

    def test_delete_removes_row_returns_true(self, repo, engine):
        """Deleting an existing row returns ``True`` and the row is
        gone on subsequent reads."""
        repo.upsert("inst-1", pinned=True, color_tag="red")
        assert repo.get("inst-1") is not None

        assert repo.delete("inst-1") is True
        assert repo.get("inst-1") is None

    def test_delete_returns_false_for_non_existent(self, repo, engine):
        """Deleting an instance that has no row is a no-op-miss: returns
        ``False`` (used by the API to report ``{"deleted": false}``)."""
        assert repo.delete("never-existed") is False

    def test_delete_does_not_affect_other_rows(self, repo, engine):
        """``delete`` only removes the targeted row; sibling rows
        survive untouched."""
        repo.upsert("inst-1", pinned=True)
        repo.upsert("inst-2", color_tag="blue")

        assert repo.delete("inst-1") is True

        # inst-2 still present.
        survivor = repo.get("inst-2")
        assert survivor is not None
        assert survivor.color_tag == "blue"

        # inst-1 truly gone.
        assert repo.get("inst-1") is None

    def test_delete_then_upsert_creates_fresh_row(self, repo, engine):
        """After a delete, the next ``upsert`` creates a fresh row with
        the lazy-create defaults (proves no zombie state)."""
        repo.upsert("inst-1", pinned=True, color_tag="red")
        repo.delete("inst-1")

        row = repo.upsert("inst-1")
        assert row.pinned is False
        assert row.color_tag is None
        assert row.pinned_at is None
