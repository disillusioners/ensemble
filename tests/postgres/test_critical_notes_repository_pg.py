"""PostgreSQL parity test for critical notes repository — reference=None preservation.

Regression pin (round-3 reviewer finding, 2026-09-10): the unit-test mock
bakes the repo None-guard in, so a repo-layer regression (guard removed →
None clears reference) is invisible at the unit-test layer. This PG-backed
test exercises ``SQLModelProjectRepository.update_critical_note`` against
a real PostgreSQL engine so the None-skip guard is pinned at the real
repository layer.

Run with::

    pytest tests/postgres/test_critical_notes_repository_pg.py \\
        -v -m postgres --override-ini="addopts="

The ``pg_engine`` fixture in ``tests/postgres/conftest.py`` skips the
entire module cleanly when PostgreSQL is unreachable, so this file is
safe to collect even on machines without a running PG.
"""
from __future__ import annotations

import pytest

from daemon.repositories.project.repository import SQLModelProjectRepository


# Auto-apply the postgres marker so ``pytest -m postgres`` selects these
# tests and the default ``-m 'not integration and not postgres'`` addopts
# skips them unless overridden.
pytestmark = pytest.mark.postgres


class TestUpdateCriticalNoteReferenceNone:
    """Pin the repo-layer None-skip guard against a real PostgreSQL engine."""

    def test_reference_none_preserves_stored_reference(self, pg_repository_factory):
        """update_critical_note(reference=None) must NOT clear the stored
        reference. Pins the None-skip guard in
        ``SQLModelProjectRepository.update_critical_note`` against a
        regression that would treat None as "set to NULL".
        """
        repo = pg_repository_factory(SQLModelProjectRepository)

        # Need a project to satisfy any FK / project-scoped lookups.
        project = repo.create(
            name="cn_reference_none_pg_test",
            project_type="software",
            main_directory="/tmp/cn_reference_none_pg_test",
            description="PG-backed reference=None preservation test",
        )

        # Add an entry WITH a reference.
        added = repo.add_critical_note(
            project_id=project.project_id,
            source_agent="seed",
            category="convention",
            priority="medium",
            summary="original summary for reference-none guard",
            reference="https://original.example.com/path",
        )
        original_reference = added.reference
        assert original_reference == "https://original.example.com/path"

        # Update with reference=None alongside other field changes.
        # The None-skip guard must leave reference untouched.
        updated = repo.update_critical_note(
            project.project_id,
            added.id,
            priority="high",
            summary="updated summary text",
            reference=None,
            source_agent="updater",
        )

        assert updated is not None, "update_critical_note returned None"
        # Reference MUST be preserved — guard pinned.
        assert updated.reference == original_reference, (
            f"reference=None cleared stored reference; "
            f"got {updated.reference!r}, expected {original_reference!r}"
        )
        # The other fields WERE updated — proves the call was processed.
        assert updated.priority == "high"
        assert updated.summary == "updated summary text"
        assert updated.source_agent == "updater"

    def test_reference_explicit_value_replaces_stored_reference(
        self, pg_repository_factory
    ):
        """Companion: passing a non-None reference DOES replace the
        stored value (so the guard does not accidentally swallow real
        updates). Confirms the None-skip is a None-specific guard.
        """
        repo = pg_repository_factory(SQLModelProjectRepository)

        project = repo.create(
            name="cn_reference_replace_pg_test",
            project_type="software",
            main_directory="/tmp/cn_reference_replace_pg_test",
            description="PG-backed reference-replace companion test",
        )

        added = repo.add_critical_note(
            project_id=project.project_id,
            source_agent="seed",
            category="convention",
            priority="medium",
            summary="another original summary",
            reference="https://first.example.com",
        )

        updated = repo.update_critical_note(
            project.project_id,
            added.id,
            reference="https://second.example.com/new",
        )

        assert updated is not None
        assert updated.reference == "https://second.example.com/new"
