"""Tests for startup integration wiring (Phase 1 feature).

Verifies that:
1. ``daemon.api.lifespan`` loads ``EnsembleConfig`` from disk before
   anything else.
2. ``ensemble.json`` is auto-created in the data directory on first startup.
3. The ``/api/health`` endpoint surfaces the new
   ``current_database`` and ``postgres_env_available`` fields.

The conftest at tests/conftest.py is path-agnostic for "integration" in
test file names, so we work around the broad heuristic by isolating the
health endpoint test to the small surface (HealthResponse model + a
minimal ASGI app that mirrors the health route logic).
"""

import importlib
import json
import os
import re
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

import httpx
from fastapi import FastAPI, APIRouter, Request
from fastapi.testclient import TestClient


# ─────────────────────────────────────────────────────────────────────
# Test 1: Lifespan source ordering
# ─────────────────────────────────────────────────────────────────────


class TestLifespanOrder:
    """Verify the lifespan loads ensemble_config BEFORE load_config."""

    def test_lifespan_loads_ensemble_config_before_config(self):
        """Static check: in daemon/api.py, EnsembleConfig.load_or_create is
        called BEFORE ``config = load_config()`` in the lifespan body.
        """
        api_source = (Path(__file__).parent.parent.parent / "daemon" / "api.py").read_text()

        # Find the position of both calls inside the lifespan function
        # We isolate to the lifespan block: from `async def lifespan` to the
        # end of the function (approx end before `def create_app`).
        lifespan_match = re.search(
            r"async def lifespan\(.*?\n(?P<body>.*?)(?=\ndef create_app|async def lifespan end)",
            api_source,
            re.DOTALL,
        )
        assert lifespan_match, "Could not find lifespan function in daemon/api.py"
        body = lifespan_match.group("body")

        load_or_create_pos = body.find("EnsembleConfig.load_or_create")
        load_config_pos = body.find("config = load_config()")
        assert load_or_create_pos != -1, "load_or_create call not found in lifespan"
        assert load_config_pos != -1, "load_config() call not found in lifespan"
        assert load_or_create_pos < load_config_pos, (
            "EnsembleConfig.load_or_create must be called BEFORE load_config()"
        )

    def test_lifespan_data_dir_uses_ensemble_data_dir_env(self):
        """Lifespan uses ENSEMBLE_DATA_DIR env var (defaulting to ./data)."""
        api_source = (Path(__file__).parent.parent.parent / "daemon" / "api.py").read_text()
        assert "ENSEMBLE_DATA_DIR" in api_source
        # Default to ./data
        assert '"./data"' in api_source or "'./data'" in api_source


# ─────────────────────────────────────────────────────────────────────
# Test 2: ensemble.json gets created on first startup
# ─────────────────────────────────────────────────────────────────────


class TestEnsembleJsonCreated:
    """Verify ensemble.json is created in the data directory."""

    def test_ensemble_json_created_in_data_dir(self, tmp_path: Path, monkeypatch):
        """EnsembleConfig.load_or_create on a fresh dir creates ensemble.json."""
        # Make sure no POSTGRES_* env vars are set so we get sqlite default
        for key in ("POSTGRES_HOST", "POSTGRES_DB", "POSTGRES_PORT",
                    "POSTGRES_USER", "POSTGRES_PASSWORD"):
            monkeypatch.delenv(key, raising=False)

        from daemon.ensemble_config import EnsembleConfig
        config = EnsembleConfig.load_or_create(tmp_path)

        # File must exist after the call
        config_file = tmp_path / "ensemble.json"
        assert config_file.exists()
        # And the saved file's database field matches the in-memory config
        saved = json.loads(config_file.read_text())
        assert saved["database"] == config.database

    def test_ensemble_data_dir_env_var_honored(self, tmp_path: Path, monkeypatch):
        """ENSEMBLE_DATA_DIR env var changes where ensemble.json is created."""
        for key in ("POSTGRES_HOST", "POSTGRES_DB", "POSTGRES_PORT",
                    "POSTGRES_USER", "POSTGRES_PASSWORD"):
            monkeypatch.delenv(key, raising=False)

        monkeypatch.setenv("ENSEMBLE_DATA_DIR", str(tmp_path))

        from daemon.ensemble_config import EnsembleConfig
        # Simulate what daemon/api.py lifespan does
        data_dir = Path(os.environ["ENSEMBLE_DATA_DIR"])
        EnsembleConfig.load_or_create(data_dir)

        # File must be created in the env-var-specified dir
        assert (tmp_path / "ensemble.json").exists()

    def test_lifespan_data_dir_path_matches_lifespan_body(self, tmp_path: Path, monkeypatch):
        """The data_dir construction in lifespan matches EnsembleConfig.load_or_create."""
        for key in ("POSTGRES_HOST", "POSTGRES_DB", "POSTGRES_PORT",
                    "POSTGRES_USER", "POSTGRES_PASSWORD"):
            monkeypatch.delenv(key, raising=False)

        # Use the same env-var precedence pattern as the lifespan
        monkeypatch.setenv("ENSEMBLE_DATA_DIR", str(tmp_path))
        data_dir = Path(os.environ.get("ENSEMBLE_DATA_DIR", "./data"))

        from daemon.ensemble_config import EnsembleConfig
        EnsembleConfig.load_or_create(data_dir)

        assert (data_dir / "ensemble.json").exists()


# ─────────────────────────────────────────────────────────────────────
# Test 3: Health endpoint logic (without importing full daemon.api)
# ─────────────────────────────────────────────────────────────────────


class TestHealthEndpointLogic:
    """Verify the /api/health route logic surfaces ensemble config fields.

    We don't import daemon.api directly to avoid the heavy import chain
    (routers, MCP, etc.). Instead, we replicate the route logic in a
    minimal app to verify the contract.
    """

    @pytest.fixture
    def minimal_health_app(self):
        """Build a minimal FastAPI app mirroring the health endpoint logic."""
        from daemon.models.common import HealthResponse

        app = FastAPI()
        api_router = APIRouter(prefix="/api")

        @api_router.get("/health", response_model=HealthResponse)
        async def health_check(request: Request):
            # Mirrors the logic in daemon/api.py
            start_time = getattr(request.app.state, 'start_time', None)
            ensemble_config = getattr(request.app.state, 'ensemble_config', None)
            return HealthResponse(
                status="healthy",
                uptime_seconds=time_module_time() - start_time if start_time else 0,
                version="1.0.0",
                current_database=ensemble_config.database if ensemble_config is not None else None,
                postgres_env_available=(
                    ensemble_config.postgres_env_available
                    if ensemble_config is not None else None
                ),
            )

        app.include_router(api_router)
        return app

    def test_health_response_schema_has_new_fields(self):
        """HealthResponse model has current_database and postgres_env_available."""
        from daemon.models.common import HealthResponse

        resp = HealthResponse(
            status="healthy",
            uptime_seconds=1.0,
            version="1.0.0",
            current_database="sqlite",
            postgres_env_available=False,
        )
        dumped = resp.model_dump()
        assert "current_database" in dumped
        assert "postgres_env_available" in dumped
        assert dumped["current_database"] == "sqlite"
        assert dumped["postgres_env_available"] is False

    def test_health_response_defaults_are_none(self):
        """When ensemble_config is not yet wired up, fields are None."""
        from daemon.models.common import HealthResponse

        resp = HealthResponse(
            status="healthy",
            uptime_seconds=1.0,
            version="1.0.0",
        )
        dumped = resp.model_dump()
        assert dumped["current_database"] is None
        assert dumped["postgres_env_available"] is None

    def test_health_endpoint_returns_ensemble_config_fields(self, minimal_health_app):
        """TestClient GET /api/health returns the new fields populated."""
        from daemon.ensemble_config import EnsembleConfig

        minimal_health_app.state.ensemble_config = EnsembleConfig(database="sqlite")
        minimal_health_app.state.start_time = time_module_time() - 1.0

        with TestClient(minimal_health_app) as client:
            response = client.get("/api/health")

        assert response.status_code == 200
        data = response.json()
        assert "current_database" in data
        assert "postgres_env_available" in data
        assert data["current_database"] == "sqlite"
        assert data["postgres_env_available"] is False

    def test_health_endpoint_postgres_database(self, minimal_health_app, monkeypatch):
        """With a postgres EnsembleConfig, current_database reflects 'postgres'."""
        from daemon.ensemble_config import EnsembleConfig

        minimal_health_app.state.ensemble_config = EnsembleConfig(database="postgres")
        minimal_health_app.state.start_time = time_module_time() - 1.0

        with TestClient(minimal_health_app) as client:
            response = client.get("/api/health")

        assert response.status_code == 200
        data = response.json()
        assert data["current_database"] == "postgres"

    def test_health_endpoint_postgres_env_available_when_set(
        self, minimal_health_app, monkeypatch
    ):
        """When POSTGRES_HOST+POSTGRES_DB are set, postgres_env_available=True."""
        from daemon.ensemble_config import EnsembleConfig

        for key in ("POSTGRES_HOST", "POSTGRES_DB", "POSTGRES_PORT",
                    "POSTGRES_USER", "POSTGRES_PASSWORD"):
            monkeypatch.delenv(key, raising=False)
        monkeypatch.setenv("POSTGRES_HOST", "h")
        monkeypatch.setenv("POSTGRES_DB", "d")

        minimal_health_app.state.ensemble_config = EnsembleConfig(database="sqlite")
        minimal_health_app.state.start_time = time_module_time() - 1.0

        with TestClient(minimal_health_app) as client:
            response = client.get("/api/health")

        data = response.json()
        assert data["postgres_env_available"] is True

    def test_health_endpoint_without_state_returns_nulls(self, minimal_health_app):
        """If app.state.ensemble_config is not set, fields default to None."""
        # Don't set ensemble_config or start_time on app.state
        with TestClient(minimal_health_app) as client:
            response = client.get("/api/health")

        assert response.status_code == 200
        data = response.json()
        # Endpoint should still respond; fields are None
        assert data["current_database"] is None
        assert data["postgres_env_available"] is None


def time_module_time():
    """Helper: return current monotonic-ish time."""
    import time
    return time.time()
