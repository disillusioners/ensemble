"""Test the startup backfill that opts in projects with existing blueprints."""
import pytest
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel

from daemon.repositories.blueprint.repository import BlueprintRepository
from daemon.repositories.project.repository import SQLModelProjectRepository
from daemon.constants import BLUEPRINT_ACTIVE_METADATA_KEY


@pytest.fixture
def engine():
    e = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(e)
    return e


@pytest.fixture
def bp_repo(engine):
    return BlueprintRepository(engine)


@pytest.fixture
def proj_repo(engine):
    return SQLModelProjectRepository(engine)


def _run_backfill(proj_repo, bp_repo):
    """Simulate ``_backfill_blueprint_active`` inline (mirrors manager.py logic)."""
    projects = proj_repo.list_projects(limit=10_000)
    for project in projects:
        pid = getattr(project, "project_id", None) or getattr(project, "id", None)
        if not pid:
            continue
        if getattr(project, "name", None) == "__system_default__":
            continue
        existing = proj_repo.get_metadata(pid, BLUEPRINT_ACTIVE_METADATA_KEY)
        if existing is not None:
            continue
        blueprints = bp_repo.list_by_project(pid, active_only=True)
        if blueprints:
            proj_repo.set_metadata(pid, BLUEPRINT_ACTIVE_METADATA_KEY, True)


def test_backfill_opts_in_project_with_blueprints(bp_repo, proj_repo):
    """A project with active blueprints gets blueprint_active=true."""
    # Create a project + a blueprint for it
    proj_repo.create(name="test-proj", project_type="general")
    project = proj_repo.list_projects()[0]

    bp_repo.create(
        project_id=project.project_id,
        slug="core",
        name="Core",
        kind="core",
        content="# Core",
    )

    # Before backfill: no metadata key
    assert proj_repo.get_metadata(project.project_id, BLUEPRINT_ACTIVE_METADATA_KEY) is None

    # Run backfill logic inline (simulates _backfill_blueprint_active)
    _run_backfill(proj_repo, bp_repo)

    # After: opted in
    assert proj_repo.get_metadata(project.project_id, BLUEPRINT_ACTIVE_METADATA_KEY) is True


def test_backfill_skips_project_without_blueprints(bp_repo, proj_repo):
    """A project with no blueprints does NOT get opted in."""
    proj_repo.create(name="empty-proj", project_type="general")
    project = proj_repo.list_projects()[0]

    # No blueprints → no backfill
    _run_backfill(proj_repo, bp_repo)

    # Metadata should remain absent
    assert proj_repo.get_metadata(project.project_id, BLUEPRINT_ACTIVE_METADATA_KEY) is None


def test_backfill_does_not_overwrite_existing_key(bp_repo, proj_repo):
    """If the key already exists (even false), don't overwrite."""
    proj_repo.create(name="explicit-off", project_type="general")
    project = proj_repo.list_projects()[0]
    bp_repo.create(
        project_id=project.project_id,
        slug="core",
        name="Core",
        kind="core",
        content="# Core",
    )

    # Operator explicitly disabled
    proj_repo.set_metadata(project.project_id, BLUEPRINT_ACTIVE_METADATA_KEY, False)

    # Backfill should NOT overwrite (existing is not None)
    _run_backfill(proj_repo, bp_repo)

    # Operator's choice preserved
    assert proj_repo.get_metadata(project.project_id, BLUEPRINT_ACTIVE_METADATA_KEY) is False


def test_backfill_skips_system_default_project(bp_repo, proj_repo):
    """The system default project is never opted in, even if it has blueprints."""
    # Create a project named like the system default
    proj_repo.create(name="__system_default__", project_type="software")
    project = proj_repo.list_projects()[0]
    bp_repo.create(
        project_id=project.project_id,
        slug="core",
        name="Core",
        kind="core",
        content="# Core",
    )

    _run_backfill(proj_repo, bp_repo)

    # System default is excluded from backfill
    assert proj_repo.get_metadata(project.project_id, BLUEPRINT_ACTIVE_METADATA_KEY) is None


def test_backfill_idempotent_when_already_opted_in(bp_repo, proj_repo):
    """Re-running the backfill is safe — it does not duplicate or re-set."""
    proj_repo.create(name="already-on", project_type="general")
    project = proj_repo.list_projects()[0]
    bp_repo.create(
        project_id=project.project_id,
        slug="core",
        name="Core",
        kind="core",
        content="# Core",
    )
    proj_repo.set_metadata(project.project_id, BLUEPRINT_ACTIVE_METADATA_KEY, True)

    # Re-run backfill twice
    _run_backfill(proj_repo, bp_repo)
    _run_backfill(proj_repo, bp_repo)

    # Still True (idempotent)
    assert proj_repo.get_metadata(project.project_id, BLUEPRINT_ACTIVE_METADATA_KEY) is True
