"""Commit B (reviewer-adjudicated tidier must-fix, 2026-11-02) — the
operator write routes are resolver-gated for the plane builtin.

``POST /api/mcp-servers/configure-builtin`` and
``POST /api/mcp-servers/{id}/reset-builtin`` consult
``current_plane_workspace()`` BEFORE any lookup or write when the
target server is ``plane``:

* **not allowed ⇒ house 4xx** (403 ``BUILTIN_SERVER_PROTECTED`` — the
  builtin-protection convention) with a typed message interpolating
  the resolver's reason VERBATIM — and **NO row created / NO idempotent
  replay reactivation / NO config rewrite**;
* **live is never refused** (ADJ-1 live-exempt) ⇒ live responses
  byte-identical to the pre-gate shape (pinned);
* the gate is **plane-scoped** — a refused plane resolution never
  gates a non-plane builtin.

Why the gate exists: the configure path builds its config from
``PlaneServerDefinition.get_base_config``, whose REFUSED resolution
emits an EMPTY ``x-workspace-slug`` (the ``""`` emission — the boot
path skips the row entirely on a refusal; this route was the one
write-reachable path to a persisted empty-slug row).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient

from daemon.routers.mcp_servers import (
    _compute_install_idempotency_key,
    router as mcp_servers_router,
)
from daemon.mcp.builtin_servers.plane import PlaneServerDefinition
from tests.helpers.plane_workspace_fixtures import (
    make_plane_env_fixture,
    reset_plane_workspace_cache,  # noqa: F401 — the autouse fixture
)

MCP_URL = "https://mcp.example.test/plane/http/api-key/mcp"

_NOW = "2026-11-02T00:00:00+00:00"


@dataclass
class _FakeRow:
    id: str = "row-plane"
    name: str = "plane"
    description: str | None = "Plane (test double)"
    is_builtin: bool = True
    is_active: bool = True
    config: dict = field(default_factory=dict)
    config_schema: list | None = None
    config_schema_version: str = "3"
    created_at: str = _NOW
    updated_at: str | None = _NOW
    instance_metadata: dict = field(default_factory=dict)


class _FakeRepo:
    """Router-shaped fake: records every create/update for the
    zero-mutation assertions."""

    def __init__(self, rows: list[_FakeRow] | None = None):
        self.rows: dict[str, _FakeRow] = {r.name: r for r in (rows or [])}
        self.creates: list[dict] = []
        self.updates: list[dict] = []

    def get_mcp_server(self, row_id: str) -> _FakeRow | None:
        return next((r for r in self.rows.values() if r.id == row_id), None)

    def get_mcp_server_by_name(self, name: str) -> _FakeRow | None:
        return self.rows.get(name)

    def list_mcp_servers(self, **kwargs) -> list[_FakeRow]:
        return list(self.rows.values())

    def create_mcp_server(self, **kwargs) -> _FakeRow:
        self.creates.append(kwargs)
        row = _FakeRow(
            name=kwargs["name"],
            description=kwargs.get("description"),
            config=kwargs.get("config", {}),
            is_builtin=kwargs.get("is_builtin", False),
            config_schema=kwargs.get("config_schema"),
            config_schema_version=kwargs.get("config_schema_version", "0"),
            instance_metadata={},
        )
        self.rows[row.name] = row
        return row

    def update_mcp_server(self, row_id, **kwargs) -> _FakeRow:
        self.updates.append({"id": row_id, **kwargs})
        for row in self.rows.values():
            if row.id == row_id:
                for attr in (
                    "is_active",
                    "config",
                    "config_schema",
                    "config_schema_version",
                    "instance_metadata",
                ):
                    if attr in kwargs:
                        setattr(row, attr, kwargs[attr])
        return next(r for r in self.rows.values() if r.id == row_id)


@pytest.fixture
def repo() -> _FakeRepo:
    return _FakeRepo()


@pytest.fixture
def mock_app(repo) -> FastAPI:
    app = FastAPI()
    api_router = APIRouter(prefix="/api")
    api_router.include_router(mcp_servers_router)
    app.include_router(api_router)

    manager: Any = _SimpleManager(repo)
    app.state.manager = manager
    return app


class _SimpleManager:
    """The manager surface the router touches (repo + pause flag)."""

    def __init__(self, repo: _FakeRepo):
        self._mcp_server_repository = repo
        self.is_write_paused = False
        self._mcp_service = None  # _invalidate_mcp_schema_cache no-op


@pytest.fixture
def test_client(mock_app):
    return TestClient(mock_app)


env = make_plane_env_fixture(
    (
        ("self_env", "ENSEMBLE_SELF_ENV", "dev"),
        ("db", "POSTGRES_DB", "ensemble_dev"),
        ("slug", "PLANE_MCP_WORKSPACE_SLUG", "nea"),
        (None, "PLANE_MCP_URL", MCP_URL),
        (None, "PLANE_MCP_API_KEY", "dummy-plane-mcp-key"),
        ("mcp_enabled", "PLANE_MCP_ENABLED", None),
    ),
)


def _matching_idempotency_key() -> str:
    """The key the route WOULD compute for ``plane`` under the current
    env — seeding it on a pre-existing row makes the idempotent-replay
    branch the pre-fix destination (the replay REACTIVATES an inactive
    row)."""
    definition = PlaneServerDefinition()
    generated = definition.build_config({})
    return _compute_install_idempotency_key(definition, generated)


# ── configure-builtin ────────────────────────────────────────────────────────


class TestConfigureBuiltinGate:
    def test_refused_env_creates_no_row(self, env, repo, test_client):
        """Refused resolution ⇒ 403 with the typed reason ⇒ ZERO rows
        created, zero writes of any kind."""
        env(self_env="dev", slug="nea")  # dev + 'nea' ⇒ refused

        response = test_client.post(
            "/api/mcp-servers/configure-builtin",
            json={"template_name": "plane", "values": {}},
        )

        assert response.status_code == 403
        detail = response.json()["detail"]
        assert detail["code"] == "BUILTIN_SERVER_PROTECTED"
        assert (
            "workspace 'nea' is production-only; this environment "
            "resolves to dev" in detail["message"]
        )
        assert "refused before any row is created or modified" in detail["message"]
        assert repo.rows == {}
        assert repo.creates == []
        assert repo.updates == []

    def test_refused_env_idempotent_replay_cannot_reactivate(
        self, env, repo, test_client
    ):
        """A pre-existing INACTIVE row carrying the MATCHING idempotency
        key — pre-fix the replay branch would REACTIVATE it; the gate
        refuses before the key is ever computed."""
        env(self_env="dev", slug="nea")
        row = _FakeRow(
            is_active=False,
            config={"headers": {"x-workspace-slug": "e2e"}},
            instance_metadata={
                "install_idempotency_key": _matching_idempotency_key()
            },
        )
        repo.rows["plane"] = row

        response = test_client.post(
            "/api/mcp-servers/configure-builtin",
            json={"template_name": "plane", "values": {}},
        )

        assert response.status_code == 403
        assert row.is_active is False, "the replay reactivated the row"
        assert repo.updates == [], "a refused env wrote to the row"
        assert repo.creates == []

    def test_live_configure_is_byte_identical_success(
        self, env, repo, test_client
    ):
        """Live is never refused — the configure path proceeds exactly
        as before the gate: 201, row created, canonical template config
        (slug 'nea'), response shape unchanged."""
        env(self_env="live", slug="nea", db="ensemble_prod")

        response = test_client.post(
            "/api/mcp-servers/configure-builtin",
            json={"template_name": "plane", "values": {}},
        )

        assert response.status_code == 201
        data = response.json()
        assert data["name"] == "plane"
        assert data["is_builtin"] is True
        assert data["is_active"] is True
        assert data["config_schema_version"] == "3"
        assert data["config"]["headers"]["x-workspace-slug"] == "nea"
        assert "plane" in repo.rows
        assert len(repo.creates) == 1

    def test_non_plane_builtin_is_not_gated(self, env, repo, test_client):
        """The gate is PLANE-SCOPED: a refused plane resolution never
        blocks configuring an unrelated builtin."""
        env(self_env="dev", slug="nea")  # plane refused…

        from unittest.mock import patch

        fake_definition: Any = _PlainDefinition("notplane")
        fake_registry: Any = _SingleDefinitionRegistry(fake_definition)
        with patch(
            "daemon.routers.mcp_servers.get_registry",
            return_value=fake_registry,
        ):
            response = test_client.post(
                "/api/mcp-servers/configure-builtin",
                json={"template_name": "notplane", "values": {}},
            )

        assert response.status_code == 201, response.text
        assert "notplane" in repo.rows


class _PlainDefinition:
    """A minimal non-plane builtin definition double."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.display_name = name
        self.description = f"{name} (test double)"
        self.schema_version = "1"

    def get_config_schema(self) -> list[dict]:
        return []

    def build_config(self, values: dict) -> dict:
        return {"transport": "stdio", "command": "notplane"}

    def parse_config(self, config: dict) -> dict:
        return {}


class _SingleDefinitionRegistry:
    def __init__(self, definition: Any) -> None:
        self._definition = definition

    def get_all(self) -> list[Any]:
        return [self._definition]

    def get_by_name(self, name: str) -> Any | None:
        return self._definition if name == self._definition.name else None


# ── reset-builtin ────────────────────────────────────────────────────────────


class TestResetBuiltinGate:
    def test_refused_env_rewrites_nothing(self, env, repo, test_client):
        """Refused resolution ⇒ 403 ⇒ the row's config is NOT rewritten
        (pre-fix the reset would re-materialise the template config —
        whose refused resolution carries the empty slug)."""
        env(self_env="dev", slug="nea")
        original_config = {"headers": {"x-workspace-slug": "e2e"}}
        row = _FakeRow(config=dict(original_config))
        repo.rows["plane"] = row

        response = test_client.post(
            "/api/mcp-servers/row-plane/reset-builtin"
        )

        assert response.status_code == 403
        detail = response.json()["detail"]
        assert detail["code"] == "BUILTIN_SERVER_PROTECTED"
        assert "production-only" in detail["message"]
        assert row.config == original_config
        assert repo.updates == []
        assert repo.creates == []

    def test_live_reset_succeeds(self, env, repo, test_client):
        """Live is never refused — the reset rewrites the config from
        the template exactly as before the gate."""
        env(self_env="live", slug="nea", db="ensemble_prod")
        row = _FakeRow(config={"headers": {"x-workspace-slug": "stale"}})
        repo.rows["plane"] = row

        response = test_client.post(
            "/api/mcp-servers/row-plane/reset-builtin"
        )

        assert response.status_code == 200, response.text
        data = response.json()
        assert data["config"]["headers"]["x-workspace-slug"] == "nea"
        assert row.config["headers"]["x-workspace-slug"] == "nea"
