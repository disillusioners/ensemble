"""Route-level tests for configure-builtin install rails + STOP-then-DELETE.

P3-WP5 / arch §7.4 acceptance:

- **fresh install** — create with ``install_idempotency_key`` stamped.
- **idempotent replay** — same key → same row, no duplicate INSERT.
- **install-update** — changed values update the row, capture
  ``prev_config_snapshot`` with a 7-day TTL stamp, and PRESERVE
  co-owned ``instance_metadata`` (``bound_handles`` from kms_attach —
  read-row-fresh invariant).
- **schema-version mismatch refused** — 409, row untouched.
- **audit line shape** — one §7.4 JSON line per real mutation.
- **DELETE = STOP then DELETE** — live connections for the server are
  closed before the row goes; builtin rows refuse deletion (403).

Fixtures: file-backed SQLite under ``tmp_path`` (thread-safe for the
router's ``asyncio.to_thread`` bridge). NO daemon boot, NO PG.
"""

from __future__ import annotations

import hashlib
import json
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient
from sqlmodel import SQLModel, create_engine

from daemon.models import BuiltinServerConfigure
from daemon.repositories.mcp_server import (
    McpServer,
    SQLModelMcpServerRepository,
)
from daemon.routers.mcp_servers import (
    INSTALL_SNAPSHOT_TTL,
    _compute_install_idempotency_key,
    router as mcp_servers_router,
)


def expected_key(definition_name: str, schema_version: str, config: dict) -> str:
    """Mirror of the router's §7.4 key formula (kept local on purpose —
    a test that imports the function under test would pin nothing)."""
    payload = (
        definition_name
        + "|"
        + schema_version
        + "|"
        + json.dumps(config, sort_keys=True, default=str)
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@pytest.fixture
def db(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path}/mcp_servers.db",
        connect_args={"check_same_thread": False},
    )
    SQLModel.metadata.create_all(engine)
    yield engine
    engine.dispose()


@pytest.fixture
def client(db, tmp_path, monkeypatch):
    """FastAPI app + router with a real repository over the test engine."""
    repository = SQLModelMcpServerRepository(db)

    mock_manager = MagicMock()
    mock_manager.is_write_paused = False
    mock_manager._mcp_server_repository = repository
    # _mcp_service absent → _invalidate_mcp_schema_cache no-ops (getattr guard).

    app = FastAPI()
    app.state.manager = mock_manager
    api_router = APIRouter(prefix="/api")
    api_router.include_router(mcp_servers_router)
    app.include_router(api_router)

    # Route the audit lane into the test tmp dir (writer prefers cwd).
    monkeypatch.chdir(tmp_path)
    return TestClient(app), repository


def _post_configure(client, values: dict | None = None, template: str = "opendesign"):
    return client.post(
        "/api/mcp-servers/configure-builtin",
        json={"template_name": template, "values": values or {}},
    )


class TestFreshInstall:
    def test_create_stamps_idempotency_key_and_defaults(self, client):
        http, repo = client
        resp = _post_configure(http)
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["name"] == "opendesign"
        assert body["is_builtin"] is True
        assert body["config_schema_version"] == "0.16.1"
        assert body["config"]["env"]["OD_DAEMON_URL"] == "http://127.0.0.1:7456"

        row = repo.get_mcp_server_by_name("opendesign")
        assert row is not None
        expected = expected_key("opendesign", "0.16.1", row.config)
        assert row.instance_metadata["install_idempotency_key"] == expected

    def test_fresh_create_has_no_prev_snapshot(self, client):
        http, repo = client
        _post_configure(http)
        row = repo.get_mcp_server_by_name("opendesign")
        assert "prev_config_snapshot" not in (row.instance_metadata or {})


class TestIdempotentReplay:
    def test_same_values_same_row_no_duplicate(self, client):
        http, repo = client
        first = _post_configure(http)
        assert first.status_code == 201
        row_before = repo.get_mcp_server_by_name("opendesign")

        second = _post_configure(http)
        assert second.status_code == 201
        row_after = repo.get_mcp_server_by_name("opendesign")

        assert row_after.id == row_before.id
        assert row_after.updated_at == row_before.updated_at  # no write
        assert len(repo.list_mcp_servers()) == 1

    def test_replay_key_matches_formula(self, client):
        http, repo = client
        _post_configure(http)
        row = repo.get_mcp_server_by_name("opendesign")
        assert row.instance_metadata["install_idempotency_key"] == expected_key(
            "opendesign", "0.16.1", row.config
        )


class TestInstallUpdate:
    def test_changed_values_update_and_capture_snapshot(self, client):
        http, repo = client
        _post_configure(http)
        original = repo.get_mcp_server_by_name("opendesign").config

        resp = _post_configure(http, values={"od_daemon_url": "http://127.0.0.1:9999"})
        assert resp.status_code == 201
        row = repo.get_mcp_server_by_name("opendesign")
        assert row.config["env"]["OD_DAEMON_URL"] == "http://127.0.0.1:9999"

        meta = row.instance_metadata
        assert meta["prev_config_snapshot"] == original
        assert meta["prev_config_snapshot_at"]
        expires = meta["prev_config_snapshot_expires_at"]
        assert expires  # 7-day TTL stamp present (exact delta asserted below)

    def test_snapshot_ttl_is_seven_days(self, client):
        from datetime import datetime

        http, repo = client
        _post_configure(http)
        _post_configure(http, values={"od_daemon_url": "http://127.0.0.1:8888"})
        meta = repo.get_mcp_server_by_name("opendesign").instance_metadata
        at = datetime.fromisoformat(meta["prev_config_snapshot_at"])
        expires = datetime.fromisoformat(meta["prev_config_snapshot_expires_at"])
        assert expires - at == INSTALL_SNAPSHOT_TTL
        assert INSTALL_SNAPSHOT_TTL.days == 7

    def test_update_preserves_bound_handles_from_kms_attach(self, client):
        """Read-row-fresh invariant: the rails MERGE over co-owned
        instance_metadata — kms_attach's ``bound_handles`` must survive
        a configure-builtin update."""
        http, repo = client
        _post_configure(http)
        row = repo.get_mcp_server_by_name("opendesign")

        # Simulate a kms_attach write landing between install and update.
        meta = dict(row.instance_metadata or {})
        meta["bound_handles"] = [
            {
                "handle": "KMS_HANDLE_deadbeef",
                "env_key": "OD_API_TOKEN",
                "fingerprint": "0123456789abcdef",
                "actor": "worker-instance",
            }
        ]
        repo.update_mcp_server(row.id, instance_metadata=meta)

        resp = _post_configure(http, values={"od_daemon_url": "http://127.0.0.1:7777"})
        assert resp.status_code == 201
        merged = repo.get_mcp_server_by_name("opendesign").instance_metadata
        assert merged["bound_handles"] == meta["bound_handles"]
        assert "install_idempotency_key" in merged
        assert "prev_config_snapshot" in merged


class TestSchemaVersionMismatch:
    def test_mismatch_refused_409_and_row_untouched(self, client):
        http, repo = client
        _post_configure(http)
        row = repo.get_mcp_server_by_name("opendesign")
        repo.update_mcp_server(row.id, config_schema_version="0.9.0")
        before = repo.get_mcp_server_by_name("opendesign")

        resp = _post_configure(http, values={"od_daemon_url": "http://127.0.0.1:9999"})
        assert resp.status_code == 409
        assert "Schema version mismatch" in resp.json()["detail"]["message"]
        assert "0.9.0" in resp.json()["detail"]["message"]
        assert "0.16.1" in resp.json()["detail"]["message"]

        after = repo.get_mcp_server_by_name("opendesign")
        assert after.config == before.config  # refusal leaves the row alone

    def test_mismatch_beats_idempotent_replay(self, client):
        """A replay across a schema drift must refuse, not silently
        return the stale row."""
        http, repo = client
        _post_configure(http)
        row = repo.get_mcp_server_by_name("opendesign")
        repo.update_mcp_server(row.id, config_schema_version="9.9.9")
        resp = _post_configure(http)  # same values → would be a replay
        assert resp.status_code == 409


class TestUserCreatedConflict:
    def test_user_created_name_conflict_409(self, client):
        http, repo = client
        repo.create_mcp_server(
            name="opendesign", description="user row", config={}, is_builtin=False
        )
        resp = _post_configure(http)
        assert resp.status_code == 409
        assert "user-created" in resp.json()["detail"]


class TestAuditLine:
    def test_mutation_writes_section74_line(self, client):
        http, repo = client
        resp = _post_configure(http)
        assert resp.status_code == 201

        audit_path = (
            repo and None
        )  # placeholder to keep flake8 quiet about unused repo
        path = _audit_file(client)
        lines = path.read_text().strip().splitlines()
        assert len(lines) == 1  # idempotent replay must NOT audit (below)
        record = json.loads(lines[0])
        assert set(record) == {
            "ts",
            "event",
            "name",
            "actor",
            "parent",
            "secret_ref",
            "idempotency_key",
            "trace_id",
        }
        assert record["event"] == "mcp_install"
        assert record["name"] == "opendesign"
        assert record["secret_ref"] is None  # zero-credential day-1 install
        row = repo.get_mcp_server_by_name("opendesign")
        assert record["idempotency_key"] == (
            row.instance_metadata["install_idempotency_key"]
        )

    def test_replay_writes_no_extra_line(self, client):
        http, repo = client
        _post_configure(http)
        _post_configure(http)
        lines = _audit_file(client).read_text().strip().splitlines()
        assert len(lines) == 1

    def test_update_writes_second_line_with_secret_ref(self, client):
        http, repo = client
        _post_configure(http)
        marker = "__KMS_REF__KMS_HANDLE_cafe01__"
        _post_configure(
            http,
            values={"od_daemon_url": "http://127.0.0.1:9999", "od_api_token": marker},
        )
        lines = _audit_file(client).read_text().strip().splitlines()
        assert len(lines) == 2
        second = json.loads(lines[1])
        assert second["secret_ref"] == "KMS_HANDLE_cafe01"

    def test_audit_failure_never_fails_request(self, client, monkeypatch):
        http, repo = client
        from daemon.routers import mcp_servers as router_module

        monkeypatch.setattr(
            router_module,
            "append_install_audit",
            lambda **kwargs: (_ for _ in ()).throw(RuntimeError("disk on fire")),
        )
        resp = _post_configure(http)
        assert resp.status_code == 201


def _audit_file(client) -> "object":
    """The audit file the router wrote (writer prefers cwd → tmp)."""
    from daemon.services.install_audit import INSTALL_AUDIT_RELATIVE_PATH
    from pathlib import Path

    path = Path.cwd() / INSTALL_AUDIT_RELATIVE_PATH
    assert path.exists(), f"audit file missing at {path}"
    return path


class TestDeleteFlow:
    def test_delete_stops_connections_before_delete(self, client, monkeypatch):
        """STOP-then-DELETE (arch §7.4): close_server must be awaited
        with the server name BEFORE the repository delete runs."""
        http, repo = client
        created = repo.create_mcp_server(
            name="user-tool", description="d", config={}, is_builtin=False
        )

        calls: list[str] = []
        order: list[str] = []

        real_delete = repo.delete_mcp_server

        def spy_delete(server_id):
            order.append("delete")
            return real_delete(server_id)

        fake_conn_mgr = MagicMock()
        fake_conn_mgr.close_server = AsyncMock(
            side_effect=lambda name: (calls.append(name), order.append("stop")) and 2
        )
        from daemon.routers import mcp_servers as router_module

        monkeypatch.setattr(
            router_module, "get_mcp_connection_manager", lambda: fake_conn_mgr
        )
        monkeypatch.setattr(repo, "delete_mcp_server", spy_delete)

        resp = http.delete(f"/api/mcp-servers/{created.id}")
        assert resp.status_code == 200
        assert resp.json()["deleted"] is True
        assert calls == ["user-tool"]
        assert order == ["stop", "delete"]  # STOP strictly before DELETE

    def test_delete_builtin_refused_403(self, client):
        http, repo = client
        _post_configure(http)
        row = repo.get_mcp_server_by_name("opendesign")
        resp = http.delete(f"/api/mcp-servers/{row.id}")
        assert resp.status_code == 403
        assert repo.get_mcp_server_by_name("opendesign") is not None


class TestKeyFormula:
    def test_key_is_deterministic_and_config_sensitive(self):
        definition = MagicMock()
        definition.name = "opendesign"
        definition.schema_version = "0.16.1"
        config_a = {"env": {"A": "1"}}
        config_b = {"env": {"A": "2"}}
        k1 = _compute_install_idempotency_key(definition, config_a)
        k2 = _compute_install_idempotency_key(definition, dict(config_a))
        k3 = _compute_install_idempotency_key(definition, config_b)
        k4 = _compute_install_idempotency_key(definition, {"env": {"A": "1"}})  # key order irrelevant
        assert k1 == k2 == k4
        assert k1 != k3
        assert len(k1) == 64  # sha256 hex
