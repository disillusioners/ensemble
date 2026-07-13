"""Tests for the User Language Preference feature (Phase 1).

Covers the GET/PUT ``/api/settings/language`` endpoints plus the underlying
``get_language_preference`` helper in :mod:`daemon.services.language_utils`.

Tests run against a live PostgreSQL database (``ensemble_test`` on
``localhost:5432`` by default) so the upsert path through
``SQLModelProjectRepository.set_metadata`` is exercised end-to-end.

Running::

    # Run only these tests (skip the project's default
    # ``-m 'not integration and not postgres'`` addopts so PG tests fire):
    python -m pytest tests/test_settings_api.py -v \\
        --override-ini="addopts="

Environment overrides (all optional, matching ``tests/postgres/conftest.py``):

* ``PG_TEST_HOST`` (default ``localhost``)
* ``PG_TEST_PORT`` (default ``5432``)
* ``PG_TEST_DB``   (default ``ensemble_test``)
* ``PG_TEST_USER`` (default ``ensemble``)
* ``PG_TEST_PASSWORD`` (default ``ensemble_dev``)
"""
from __future__ import annotations

import os
from typing import Any, Iterator
from unittest.mock import MagicMock

import httpx
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import DBAPIError, OperationalError
from sqlmodel import Session, SQLModel

# ── Ensure SQLModel metadata knows about every table referenced by the
# ── settings endpoints before ``SQLModel.metadata.create_all`` runs in the
# ── ``pg_engine`` fixture. Without these imports the projects /
# ── project_metadata_records tables would not be registered and the
# ── create_all() call would silently skip them.
from daemon.constants import SYSTEM_DEFAULT_PROJECT_NAME
from daemon.repositories.project.models import (
    Project,
    ProjectMetadataRecord,
    ProjectShortnameLink,
    ProjectStatus,
    ProjectTagLink,
    ProjectType,
)
from daemon.repositories import SQLModelProjectRepository
from daemon.services.language_utils import (
    DEFAULT_LANGUAGE,
    LANGUAGE_METADATA_KEY,
    get_language_preference,
)
from daemon.routers.settings import set_project_repository


pytestmark = pytest.mark.postgres


# ── Autouse: propagate SYSTEM_DEFAULT_PROJECT_ID into the modules that
# ── imported it via ``from daemon.constants import SYSTEM_DEFAULT_PROJECT_ID``.
# ── Python binds the imported name to the importing module's namespace, so
# ── patching ``daemon.constants.SYSTEM_DEFAULT_PROJECT_ID`` (done by the
# ── conftest autouse fixture) does NOT update already-captured references.
# ── We need to set it on every consumer module or endpoints will 503 / helpers
# ── will short-circuit to the default language.
@pytest.fixture(autouse=True)
def _propagate_system_default_project_id():
    """Mirror ``daemon.constants.SYSTEM_DEFAULT_PROJECT_ID`` onto consumer modules.

    Consumers: ``daemon.routers.settings`` (raises 503 on the PUT path if
    the constant is None) and ``daemon.services.language_utils`` (short-
    circuits to ``DEFAULT_LANGUAGE`` if the constant is None).
    """
    from daemon import constants
    from daemon.routers import settings as settings_module
    from daemon.services import language_utils as language_utils_module

    snapshot = (
        settings_module.SYSTEM_DEFAULT_PROJECT_ID,
        language_utils_module.SYSTEM_DEFAULT_PROJECT_ID,
    )
    settings_module.SYSTEM_DEFAULT_PROJECT_ID = constants.SYSTEM_DEFAULT_PROJECT_ID
    language_utils_module.SYSTEM_DEFAULT_PROJECT_ID = (
        constants.SYSTEM_DEFAULT_PROJECT_ID
    )
    try:
        yield
    finally:
        settings_module.SYSTEM_DEFAULT_PROJECT_ID, language_utils_module.SYSTEM_DEFAULT_PROJECT_ID = snapshot


# ── PG connection settings (mirror tests/postgres/conftest.py) ────────────────
PG_HOST = os.environ.get("PG_TEST_HOST", "localhost")
PG_PORT = int(os.environ.get("PG_TEST_PORT", "5432"))
PG_DB = os.environ.get("PG_TEST_DB", "ensemble_test")
PG_USER = os.environ.get("PG_TEST_USER", "ensemble")
PG_PASSWORD = os.environ.get("PG_TEST_PASSWORD", "ensemble_dev")
PG_URL = f"postgresql+psycopg://{PG_USER}:{PG_PASSWORD}@{PG_HOST}:{PG_PORT}/{PG_DB}"


def _pg_available(url: str) -> bool:
    """Return True iff the test PG accepts a SELECT 1."""
    try:
        engine = create_engine(url, pool_pre_ping=True, future=True)
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        engine.dispose()
        return True
    except (OperationalError, DBAPIError):
        return False
    except Exception:  # pragma: no cover - defensive
        return False


# ── Engine + schema management (session-scoped, mirrors tests/postgres) ──────


@pytest.fixture(scope="session")
def pg_engine() -> Iterator[Engine]:
    """Session-scoped PostgreSQL engine with full SQLModel schema.

    Skips the whole module cleanly when PG is unreachable. The autouse
    ``_pg_truncate_tables`` fixture below wipes rows between tests; the
    schema itself is left in place for the whole session so
    ``SQLModel.metadata.create_all`` runs only once.
    """
    if not _pg_available(PG_URL):
        pytest.skip(f"PostgreSQL not available at {PG_URL}")

    SQLModel.metadata.create_all(create_engine(PG_URL, future=True))
    engine = create_engine(
        PG_URL,
        pool_pre_ping=True,
        pool_size=5,
        max_overflow=10,
        future=True,
    )
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture(autouse=True)
def _pg_truncate_tables(pg_engine: Engine) -> Iterator[None]:
    """Wipe every SQLModel table before each test for isolation."""
    table_names = [t.name for t in reversed(SQLModel.metadata.sorted_tables)]
    if table_names:
        joined = ", ".join(f'"{name}"' for name in table_names)
        with pg_engine.begin() as conn:
            conn.execute(text(f"TRUNCATE TABLE {joined} RESTART IDENTITY CASCADE"))
    yield


# ── Repository + system-default project fixtures ──────────────────────────────


@pytest.fixture
def project_repo(pg_engine: Engine) -> SQLModelProjectRepository:
    """A SQLModelProjectRepository bound to the test PG engine."""
    return SQLModelProjectRepository(pg_engine)


@pytest.fixture
def system_default_project(pg_engine: Engine) -> str:
    """Create the system default project row expected by language_utils.

    The constants autouse fixture in ``tests/conftest.py`` pins
    ``SYSTEM_DEFAULT_PROJECT_ID`` to the deterministic uuid5 value
    ``71931ae0-0f25-5fbf-853b-2a78cc978d7e``. We insert a matching row
    here so ``SQLModelProjectRepository.set_metadata`` (called by the
    PUT endpoint) finds the project. Without this row, ``set_metadata``
    silently returns ``None`` because it pre-flights with
    ``session.get(Project, project_id)`` — see repository.py:836.
    """
    from daemon import constants

    project_id = constants.SYSTEM_DEFAULT_PROJECT_ID
    assert project_id is not None, (
        "SYSTEM_DEFAULT_PROJECT_ID must be set by the autouse fixture "
        "in tests/conftest.py before this fixture runs"
    )
    now_iso = "2026-07-12T00:00:00+00:00"
    with Session(pg_engine) as session:
        project = Project(
            project_id=project_id,
            name=SYSTEM_DEFAULT_PROJECT_NAME,
            project_type=ProjectType.GENERAL.value,
            status=ProjectStatus.ACTIVE.value,
            main_directory=None,
            related_directories=[],
            description="system default project (test)",
            project_metadata={},
            relationships={},
            creator_instance_id=None,
            creator_agent_id=None,
            created_at=now_iso,
            updated_at=now_iso,
        )
        session.add(project)
        session.commit()
    return project_id


@pytest.fixture
def wired_repo(
    project_repo: SQLModelProjectRepository,
    system_default_project: str,
) -> Iterator[SQLModelProjectRepository]:
    """Wire the test repository into the settings router and clean up after."""
    from daemon.routers import settings as settings_module

    set_project_repository(project_repo)
    try:
        yield project_repo
    finally:
        # Reset the module-level global so other tests start from a clean slate.
        # Direct attribute reset (rather than ``set_project_repository(None)``)
        # sidesteps the strict ``SQLModelProjectRepository`` type hint on the
        # setter signature while still leaving the global in a known state.
        settings_module._project_repo = None


# ── HTTP client fixture ──────────────────────────────────────────────────────


@pytest.fixture
async def client(wired_repo) -> Any:
    """Async HTTP client bound to the real ``daemon.api.app`` ASGI app.

    ``httpx.ASGITransport`` does NOT run the FastAPI lifespan — we wire
    repositories manually via the router setters (see ``wired_repo``).
    The autouse ``_ensure_app_state_manager`` fixture in
    ``tests/conftest.py`` supplies a default ``app.state.manager`` for
    any router that pokes at it.
    """
    from daemon.api import app

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as ac:
        yield ac


# ── Tests: HTTP endpoints ────────────────────────────────────────────────────


class TestGetLanguage:
    """GET /api/settings/language — read current preference."""

    @pytest.mark.asyncio
    async def test_returns_default_auto_when_unset(self, client):
        """With no preference stored, GET returns ``"Auto"`` (no preference)."""
        response = await client.get("/api/settings/language")

        assert response.status_code == 200
        assert response.json() == {"language": "Auto"}

    @pytest.mark.asyncio
    async def test_returns_stored_value_after_put(self, client):
        """Round-trip: PUT then GET returns the same value."""
        put_response = await client.put(
            "/api/settings/language", json={"language": "Spanish"}
        )
        assert put_response.status_code == 200
        assert put_response.json() == {"language": "Spanish"}

        get_response = await client.get("/api/settings/language")
        assert get_response.status_code == 200
        assert get_response.json() == {"language": "Spanish"}


class TestPutLanguage:
    """PUT /api/settings/language — write / update preference."""

    @pytest.mark.asyncio
    async def test_put_then_get_returns_value(self, client):
        """Stored value is visible on subsequent GET."""
        put_response = await client.put(
            "/api/settings/language", json={"language": "Spanish"}
        )
        assert put_response.status_code == 200
        assert put_response.json() == {"language": "Spanish"}

        get_response = await client.get("/api/settings/language")
        assert get_response.status_code == 200
        assert get_response.json() == {"language": "Spanish"}

    @pytest.mark.asyncio
    async def test_put_chinese_after_spanish_updates_value(self, client):
        """Second PUT overrides the first — value is not sticky."""
        await client.put("/api/settings/language", json={"language": "Spanish"})
        put_response = await client.put(
            "/api/settings/language", json={"language": "Chinese"}
        )
        assert put_response.status_code == 200
        assert put_response.json() == {"language": "Chinese"}

        get_response = await client.get("/api/settings/language")
        assert get_response.status_code == 200
        assert get_response.json() == {"language": "Chinese"}

    @pytest.mark.asyncio
    async def test_put_strips_surrounding_whitespace(self, client):
        """The router strips the value before storing / echoing."""
        put_response = await client.put(
            "/api/settings/language", json={"language": "  French  "}
        )
        assert put_response.status_code == 200
        assert put_response.json() == {"language": "French"}

        get_response = await client.get("/api/settings/language")
        assert get_response.json() == {"language": "French"}


class TestPutValidation:
    """PUT validation — Pydantic enforces min_length / max_length."""

    @pytest.mark.asyncio
    async def test_empty_string_returns_422(self, client):
        """Empty string trips ``min_length=1`` and yields 422."""
        response = await client.put("/api/settings/language", json={"language": ""})

        assert response.status_code == 422
        body = response.json()
        # FastAPI / Pydantic v2 surfaces validation errors under "detail".
        assert "detail" in body

    @pytest.mark.asyncio
    async def test_string_over_100_chars_returns_422(self, client):
        """A 101-char string trips ``max_length=100`` and yields 422."""
        too_long = "x" * 101
        response = await client.put(
            "/api/settings/language", json={"language": too_long}
        )

        assert response.status_code == 422
        body = response.json()
        assert "detail" in body

    @pytest.mark.asyncio
    async def test_string_at_100_chars_accepted(self, client):
        """A 100-char string is exactly at the boundary and accepted."""
        boundary = "x" * 100
        response = await client.put(
            "/api/settings/language", json={"language": boundary}
        )

        assert response.status_code == 200
        assert response.json() == {"language": boundary}


class TestGetLanguagePreferenceHelper:
    """Unit tests for ``get_language_preference`` — graceful failure paths."""

    def test_returns_auto_when_repo_is_none(self):
        """``None`` repo is handled — returns the default sentinel."""
        assert get_language_preference(None) == "Auto"

    def test_returns_auto_when_repo_raises(self):
        """A repo that raises during ``get_metadata_record`` falls back gracefully."""
        from daemon.services import language_utils as language_utils_module

        broken_repo = MagicMock()
        broken_repo.engine = MagicMock()
        broken_repo.get_metadata_record.side_effect = RuntimeError("boom")

        # The helper opens ``Session(repo.engine)`` BEFORE calling
        # ``get_metadata_record``. Provide a session whose ``__enter__``
        # yields a harmless object so the ``with`` block succeeds and
        # the exception is raised on the actual repo call. We patch
        # ``Session`` on the *language_utils* module — Python's
        # ``from sqlmodel import Session`` binding lives there, not on
        # the ``sqlmodel`` package.
        class _DummySession:
            def __enter__(self_inner):
                return self_inner

            def __exit__(self_inner, exc_type, exc, tb):
                return False

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(
                language_utils_module,
                "Session",
                lambda *_a, **_kw: _DummySession(),
            )
            assert get_language_preference(broken_repo) == "Auto"
        broken_repo.get_metadata_record.assert_called_once()

    def test_returns_auto_when_metadata_record_missing(
        self, project_repo: SQLModelProjectRepository
    ):
        """A live repo with no stored record returns the default."""
        assert get_language_preference(project_repo) == "Auto"

    def test_returns_stored_value_from_live_repo(
        self,
        project_repo: SQLModelProjectRepository,
        system_default_project: str,
    ):
        """Round-trip via the helper on a real PG-backed repo."""
        from daemon import constants

        project_id = constants.SYSTEM_DEFAULT_PROJECT_ID
        assert project_id is not None

        project_repo.set_metadata(project_id, LANGUAGE_METADATA_KEY, "Japanese")
        assert get_language_preference(project_repo) == "Japanese"
