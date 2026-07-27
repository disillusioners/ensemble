"""Tests for default-version resolution in the ``spawn_instance`` tool.

Covers:
- ``_resolve_default_version_tag`` helper (direct unit tests on a
  real ``SQLModelProjectRepository`` bound to an in-memory SQLite
  engine — works on both SQLite and PostgreSQL).
- The ``spawn_instance`` tool's tool-body integration: verifies the
  resolved tag is forwarded to ``manager.spawn_instance(version_tag=...)``.

The default-version feature is a single global scope stored under
``SYSTEM_DEFAULT_PROJECT_ID``; the helper mirrors the read pattern in
``daemon/routers/settings.py:323-353``.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from daemon import constants
from daemon.repositories import SQLModelProjectRepository
from daemon.repositories.project.models import (
    Project,
    ProjectMetadataRecord,
    ProjectShortnameLink,
)
from daemon.tools.instance import _resolve_default_version_tag


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def system_default_project_id():
    """Pin ``SYSTEM_DEFAULT_PROJECT_ID`` for the duration of a test.

    Both the helper and the ``default_agent_versions`` metadata record
    key off this constant; tests must set it before exercising the
    helper and restore it on teardown so the global stays clean.
    """
    original = constants.SYSTEM_DEFAULT_PROJECT_ID
    pid = "00000000-0000-0000-0000-000000000001"
    constants.SYSTEM_DEFAULT_PROJECT_ID = pid
    try:
        yield pid
    finally:
        constants.SYSTEM_DEFAULT_PROJECT_ID = original


@pytest.fixture
def engine():
    """In-memory SQLite engine with project + project_metadata tables.

    Uses ``StaticPool`` so the in-memory DB survives across threads,
    mirroring ``tests/tools/conftest.py``. Imports the models so
    SQLModel.metadata knows about every table before ``create_all``.
    """
    eng = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    # Touch models so they register on SQLModel.metadata
    _ = (Project, ProjectMetadataRecord, ProjectShortnameLink)
    SQLModel.metadata.create_all(eng)
    yield eng
    eng.dispose()


@pytest.fixture
def repo(engine):
    """``SQLModelProjectRepository`` bound to the test engine."""
    return SQLModelProjectRepository(engine)


def _seed_default_versions(
    repo: SQLModelProjectRepository, project_id: str, mapping: dict
) -> None:
    """Seed the ``default_agent_versions`` metadata record as JSON text.

    Goes through ``set_metadata`` (the production write path) so the
    upsert + project-exists guard is exercised exactly as the real
    API would.
    """
    repo.set_metadata(
        project_id,
        constants.DEFAULT_AGENT_VERSIONS_METADATA_KEY,
        json.dumps(mapping),
    )


def _seed_corrupt_default_versions(
    engine, project_id: str, raw_value: str
) -> None:
    """Insert a ``project_metadata_records`` row with a non-JSON value.

    Used for the corrupt-JSON test — we cannot go through
    ``set_metadata`` (which would JSON-encode the value), so we
    insert the raw bad string directly into the table.
    """
    now = datetime.now(timezone.utc).isoformat()
    with Session(engine) as session:
        record = ProjectMetadataRecord(
            project_id=project_id,
            meta_key=constants.DEFAULT_AGENT_VERSIONS_METADATA_KEY,
            meta_value=raw_value,
            created_at=now,
            updated_at=now,
        )
        session.add(record)
        session.commit()


def _create_project_row(engine, project_id: str) -> None:
    """Insert a minimal ``projects`` row so the FK on metadata is satisfied.

    ``set_metadata`` requires the project row to exist; tests that
    seed metadata directly via SQL still need the row or the FK will
    fail.
    """
    now = datetime.now(timezone.utc).isoformat()
    with Session(engine) as session:
        session.add(
            Project(
                project_id=project_id,
                name=f"project-{project_id[:8]}",
                project_type="general",
                status="active",
                created_at=now,
                updated_at=now,
            )
        )
        session.commit()


# ─────────────────────────────────────────────────────────────────────────────
# Tests 1-5: _resolve_default_version_tag (direct helper tests)
# ─────────────────────────────────────────────────────────────────────────────


class TestResolveDefaultVersionTag:
    """Direct unit tests for ``_resolve_default_version_tag``.

    Each test seeds a ``projects`` row + metadata row, then calls the
    helper with a real ``SQLModelProjectRepository`` and asserts on
    the resolved ``version_tag``.
    """

    def test_1_default_configured_for_agent(
        self, repo, engine, system_default_project_id
    ):
        """Default configured: {"developer": "v2"} → resolves to "v2"."""
        _create_project_row(engine, system_default_project_id)
        _seed_default_versions(repo, system_default_project_id, {"developer": "v2"})

        result = _resolve_default_version_tag(repo, "developer")

        assert result == "v2"

    def test_2_no_default_configured(
        self, repo, engine, system_default_project_id
    ):
        """No default configured (missing record) → returns None."""
        _create_project_row(engine, system_default_project_id)
        # No metadata seeded: get_metadata_record returns None.

        result = _resolve_default_version_tag(repo, "developer")

        assert result is None

    def test_3_default_explicitly_null(
        self, repo, engine, system_default_project_id
    ):
        """Default explicitly null: {"developer": None} → returns None."""
        _create_project_row(engine, system_default_project_id)
        _seed_default_versions(repo, system_default_project_id, {"developer": None})

        result = _resolve_default_version_tag(repo, "developer")

        assert result is None

    def test_4_default_for_other_agent_only(
        self, repo, engine, system_default_project_id
    ):
        """Default set for tester only; spawn developer → None."""
        _create_project_row(engine, system_default_project_id)
        _seed_default_versions(repo, system_default_project_id, {"tester": "v2"})

        result = _resolve_default_version_tag(repo, "developer")

        assert result is None

    def test_5_corrupt_json_returns_none(
        self, repo, engine, system_default_project_id
    ):
        """Corrupt JSON in meta_value → resolver returns None, no exception."""
        _create_project_row(engine, system_default_project_id)
        _seed_corrupt_default_versions(engine, system_default_project_id, "{not json")

        # Must not raise.
        result = _resolve_default_version_tag(repo, "developer")

        assert result is None

    def test_system_default_project_id_none_returns_none(self, repo):
        """When SYSTEM_DEFAULT_PROJECT_ID is None (daemon not booted) → None.

        The helper must not touch the repo. Sanity check that this
        contract holds even before any seeding.
        """
        original = constants.SYSTEM_DEFAULT_PROJECT_ID
        constants.SYSTEM_DEFAULT_PROJECT_ID = None
        try:
            result = _resolve_default_version_tag(repo, "developer")
            assert result is None
        finally:
            constants.SYSTEM_DEFAULT_PROJECT_ID = original

    def test_db_error_returns_none(
        self, system_default_project_id
    ):
        """If the repo raises on get_metadata_record, helper returns None.

        Mirrors the production pattern (any DB failure must not break
        the spawn path; the agent falls back to the base version).
        """
        repo = MagicMock()
        repo.engine = MagicMock()
        # Force Session(...) to succeed but the get_metadata_record call to raise.
        repo.get_metadata_record.side_effect = RuntimeError("simulated DB outage")

        result = _resolve_default_version_tag(repo, "developer")

        assert result is None


# ─────────────────────────────────────────────────────────────────────────────
# Test 6: spawn_instance tool forwards the resolved tag to manager
# ─────────────────────────────────────────────────────────────────────────────


class TestSpawnInstanceForwardsTag:
    """Spy test: assert ``manager.spawn_instance`` receives the resolved tag.

    Builds a real ``create_instance_tools(...)`` closure (so the actual
    tool body runs), but stubs the downstream manager + auth path so
    the assertion is purely about the ``version_tag=`` forwarding.
    """

    @pytest.mark.asyncio
    async def test_6_manager_receives_resolved_version_tag(
        self, repo, engine, system_default_project_id
    ):
        """Seed default {"developer": "v2"} → spawn_instance calls
        ``manager.spawn_instance(..., version_tag="v2")``.
        """
        _create_project_row(engine, system_default_project_id)
        _seed_default_versions(repo, system_default_project_id, {"developer": "v2"})

        # Mock manager: real ``_project_repository`` (so the resolver
        # reads actual metadata), stubbed ``spawn_instance`` so we can
        # capture the kwargs, and a no-op fallback-notice helper.
        manager = MagicMock()
        manager._project_repository = repo
        # Auto-inherit-from-parent path: have the parent-instance
        # lookup return None so the tool falls through to the system
        # default project via ``normalize_project_id(None)``.
        manager._instance_repository.get.return_value = None
        manager.spawn_instance = MagicMock(
            return_value=("new-instance-id", None)
        )
        manager._lifecycle_service._format_model_fallback_notice = MagicMock(
            return_value=""
        )

        # Bypass the team-membership auth gate — this test is about
        # the version_tag forwarding, not authorization. The patch
        # scope must wrap BOTH create_instance_tools AND the awaited
        # call so the closure resolves to the patched function at
        # call time.
        with patch(
            "daemon.tools.instance._check_team_membership",
            return_value=None,
        ):
            from daemon.tools.instance import create_instance_tools
            tools = create_instance_tools(
                manager, current_instance_id="parent-iid", agent_id="tester"
            )

            # Find the spawn_instance tool among the returned tools.
            spawn_tool = next(
                t for t in tools if getattr(t, "name", "") == "spawn_instance"
            )

            result = await spawn_tool.coroutine(
                agent_id="developer", project_id=None
            )

        # Assert manager.spawn_instance was called with version_tag="v2"
        assert manager.spawn_instance.called, (
            f"manager.spawn_instance was not invoked (result={result!r})"
        )
        call_kwargs = manager.spawn_instance.call_args.kwargs
        assert call_kwargs.get("version_tag") == "v2"
        assert call_kwargs.get("agent_id") == "developer"
        # Sanity: the tool returned the spawned instance id.
        assert "new-instance-id" in result

    @pytest.mark.asyncio
    async def test_manager_receives_none_when_no_default(
        self, repo, engine, system_default_project_id
    ):
        """No default seeded → manager.spawn_instance receives version_tag=None.

        Confirms the "no default configured" branch falls through to
        the base version (the lifecycle service handles the
        base→lex-smallest fallback, see
        ``daemon/services/instance_lifecycle.py:1183-1202``).
        """
        _create_project_row(engine, system_default_project_id)
        # No metadata seeded.

        manager = MagicMock()
        manager._project_repository = repo
        manager._instance_repository.get.return_value = None
        manager.spawn_instance = MagicMock(
            return_value=("new-instance-id", None)
        )
        manager._lifecycle_service._format_model_fallback_notice = MagicMock(
            return_value=""
        )

        with patch(
            "daemon.tools.instance._check_team_membership",
            return_value=None,
        ):
            from daemon.tools.instance import create_instance_tools
            tools = create_instance_tools(
                manager, current_instance_id="parent-iid", agent_id="tester"
            )

            spawn_tool = next(
                t for t in tools if getattr(t, "name", "") == "spawn_instance"
            )
            await spawn_tool.coroutine(agent_id="developer", project_id=None)

        call_kwargs = manager.spawn_instance.call_args.kwargs
        assert call_kwargs.get("version_tag") is None
