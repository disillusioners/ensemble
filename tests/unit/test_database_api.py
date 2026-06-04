"""Unit tests for ``daemon.routers.database.router``.

The database switch router rewrites ``ensemble.json`` and signals that a
restart is required to pick up the new backend. We test it against a
real :class:`EnsembleConfig` instance mounted on ``app.state`` plus a
temporary data directory so the atomic-write behavior is exercised
end-to-end.

Test surface
------------
* ``POST /api/database/switch`` happy path on both backends
* Validation: invalid target, already-on-target (no-op), PG ENV not set
* Lifecycle: the persisted ``ensemble.json`` reflects the new backend
* 500 when ``app.state`` is not wired up (lifespan did not run)
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

from daemon.ensemble_config import EnsembleConfig
from daemon.routers.database import router


# ──────────────────────────────────────────────────────────────────────────────
# Fixtures
# ──────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    """A fresh data directory for each test — never touches the real one."""
    return tmp_path


def _make_app_with_config(
    data_dir: Path,
    *,
    database: str = "sqlite",
    pg_env: bool = True,
) -> tuple[FastAPI, EnsembleConfig]:
    """Build a FastAPI app with the database router and a wired config.

    The PostgreSQL ENV contract used by ``EnsembleConfig.postgres_env_available``
    is ``POSTGRES_HOST`` + ``POSTGRES_DB``. We monkey-patch ``os.environ``
    in the test (not here) so the test author can control whether PG
    ENV looks configured without leaking across tests.

    Returns:
        A tuple of ``(app, ensemble_config)`` — the config is also
        mounted on ``app.state`` so the router can find it.
    """
    if pg_env:
        os.environ["POSTGRES_HOST"] = "localhost"
        os.environ["POSTGRES_DB"] = "ensemble"
    else:
        os.environ.pop("POSTGRES_HOST", None)
        os.environ.pop("POSTGRES_DB", None)

    # Persist a starting ensemble.json so the test mirrors the real
    # filesystem layout (load_or_create will find it on next load).
    initial = EnsembleConfig(database=database)
    initial.save(data_dir)

    config = EnsembleConfig.load_or_create(data_dir)
    assert config.database == database, (
        f"Fixture setup: expected {database}, got {config.database}"
    )

    app = FastAPI()
    app.include_router(router, prefix="/api")
    app.state.ensemble_config = config
    app.state.data_dir = data_dir
    return app, config


@pytest.fixture
def sqlite_app(data_dir: Path) -> tuple[FastAPI, EnsembleConfig]:
    """App wired to a SQLite ensemble.json with PG ENV set."""
    return _make_app_with_config(data_dir, database="sqlite", pg_env=True)


@pytest.fixture
def postgres_app(data_dir: Path) -> tuple[FastAPI, EnsembleConfig]:
    """App wired to a postgres ensemble.json with PG ENV set."""
    return _make_app_with_config(data_dir, database="postgres", pg_env=True)


@pytest.fixture
def sqlite_no_pg_env_app(data_dir: Path) -> tuple[FastAPI, EnsembleConfig]:
    """App on SQLite but with no PG ENV (simulates fresh install)."""
    return _make_app_with_config(data_dir, database="sqlite", pg_env=False)


# ──────────────────────────────────────────────────────────────────────────────
# /switch — happy paths
# ──────────────────────────────────────────────────────────────────────────────


class TestSwitchEndpoint:
    """``POST /api/database/switch`` rewrites ``ensemble.json`` and
    returns ``requires_restart=True``."""

    def test_switch_sqlite_to_postgres(self, sqlite_app, data_dir):
        """Switching from SQLite to PostgreSQL persists the new value."""
        app, config = sqlite_app
        assert config.database == "sqlite"

        with TestClient(app) as c:
            response = c.post("/api/database/switch", json={"database": "postgres"})

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["requires_restart"] is True
        assert "postgres" in body["message"].lower()

        # In-memory config is updated for downstream consumers
        # (e.g. the health endpoint) without a restart.
        assert config.database == "postgres"

        # On-disk file is updated atomically.
        persisted = json.loads((data_dir / "ensemble.json").read_text())
        assert persisted["database"] == "postgres"

    def test_switch_postgres_to_sqlite(self, postgres_app, data_dir):
        """Switching from PostgreSQL back to SQLite always works."""
        app, config = postgres_app
        assert config.database == "postgres"

        with TestClient(app) as c:
            response = c.post("/api/database/switch", json={"database": "sqlite"})

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["requires_restart"] is True
        assert config.database == "sqlite"

        persisted = json.loads((data_dir / "ensemble.json").read_text())
        assert persisted["database"] == "sqlite"

    def test_switch_postgres_to_sqlite_no_pg_env(
        self, postgres_app, data_dir, monkeypatch
    ):
        """Switching PG→SQLite must NOT require PG ENV (sqlite is default)."""
        # Remove PG env to verify the switch doesn't gate on it.
        monkeypatch.delenv("POSTGRES_HOST", raising=False)
        monkeypatch.delenv("POSTGRES_DB", raising=False)
        # ``EnsembleConfig.postgres_env_available`` is computed at call
        # time, so the removal takes effect immediately.

        app, config = postgres_app
        with TestClient(app) as c:
            response = c.post("/api/database/switch", json={"database": "sqlite"})

        assert response.status_code == 200, response.text
        assert config.database == "sqlite"


# ──────────────────────────────────────────────────────────────────────────────
# /switch — validation & errors
# ──────────────────────────────────────────────────────────────────────────────


class TestSwitchValidation:
    """``POST /api/database/switch`` rejects invalid input with 400."""

    def test_invalid_target_value(self, sqlite_app):
        """Pydantic schema rejects anything but ``sqlite``/``postgres``."""
        app, _ = sqlite_app
        with TestClient(app) as c:
            response = c.post("/api/database/switch", json={"database": "mysql"})

        assert response.status_code == 422  # Pydantic validation

    def test_missing_target_field(self, sqlite_app):
        """Missing ``database`` field is a schema error."""
        app, _ = sqlite_app
        with TestClient(app) as c:
            response = c.post("/api/database/switch", json={})

        assert response.status_code == 422

    def test_noop_already_on_sqlite(self, sqlite_app, data_dir):
        """Switching to the same backend is a 400 no-op."""
        app, config = sqlite_app
        original = (data_dir / "ensemble.json").read_text()

        with TestClient(app) as c:
            response = c.post("/api/database/switch", json={"database": "sqlite"})

        assert response.status_code == 400
        assert "sqlite" in response.json()["detail"].lower()

        # File should be unchanged (no spurious write on no-op).
        assert (data_dir / "ensemble.json").read_text() == original

    def test_noop_already_on_postgres(self, postgres_app, data_dir):
        """Switching to postgres while already on postgres is a 400 no-op."""
        app, _ = postgres_app
        original = (data_dir / "ensemble.json").read_text()

        with TestClient(app) as c:
            response = c.post("/api/database/switch", json={"database": "postgres"})

        assert response.status_code == 400
        assert (data_dir / "ensemble.json").read_text() == original

    def test_pg_env_missing_for_postgres_target(
        self, sqlite_no_pg_env_app, data_dir
    ):
        """Switching to postgres without PG ENV is 400 (env precondition)."""
        app, config = sqlite_no_pg_env_app
        assert config.database == "sqlite"

        with TestClient(app) as c:
            response = c.post("/api/database/switch", json={"database": "postgres"})

        assert response.status_code == 400
        detail = response.json()["detail"].lower()
        assert "postgres_host" in detail or "postgres" in detail

        # No write should have occurred.
        persisted = json.loads((data_dir / "ensemble.json").read_text())
        assert persisted["database"] == "sqlite"


# ──────────────────────────────────────────────────────────────────────────────
# /switch — lifespan-missing guard
# ──────────────────────────────────────────────────────────────────────────────


class TestSwitchLifespanGuard:
    """When ``app.state`` is not wired, the endpoint fails fast with 500."""

    def test_500_when_ensemble_config_missing(self, data_dir):
        app = FastAPI()
        app.include_router(router, prefix="/api")
        # Deliberately don't set app.state.ensemble_config
        app.state.data_dir = data_dir

        with TestClient(app) as c:
            response = c.post("/api/database/switch", json={"database": "sqlite"})

        assert response.status_code == 500

    def test_500_when_data_dir_missing(self, tmp_path: Path, monkeypatch):
        # Build a valid config but do not set data_dir on app.state.
        monkeypatch.setenv("POSTGRES_HOST", "localhost")
        monkeypatch.setenv("POSTGRES_DB", "ensemble")
        cfg = EnsembleConfig(database="postgres")
        cfg.save(tmp_path)

        app = FastAPI()
        app.include_router(router, prefix="/api")
        app.state.ensemble_config = cfg
        # No data_dir set

        with TestClient(app) as c:
            response = c.post("/api/database/switch", json={"database": "sqlite"})

        assert response.status_code == 500
