"""Unit tests for ``mcp_set_env`` (self-provisioning Stage 1, 2026-10-02).

Covers the recon-mandated behaviors:

1. Happy path — merge into existing ``config.env`` preserving other
   keys AND ``__KMS_REF__`` markers byte-identical (R1 co-ownership).
2. Unknown server name → clear ``SERVER_NOT_FOUND`` error.
3. Secret-shaped key rejection (KEY/TOKEN/SECRET/PASSWORD substring)
   → error points at ``kms_request`` / ``kms_attach``; row untouched.
4. R1 freshness — a marker written by a concurrent lane (simulated by
   a direct repo write BETWEEN two tool invocations) is NOT clobbered
   by the second invocation. Proves the tool reads the row FRESH at
   call time (no factory-time or first-call snapshot).
5. Schema-cache invalidation is called with the server NAME (routers
   analog — cache is name-keyed).
6. No key material in logs — audit lines carry key NAMES + counts
   only; values never appear (and the tool result echoes no values
   either — tool results land in checkpoints, PB-F1 family).

Plus an abbreviated registration-seam pin (4-step discipline from the
Task A ``test_ens_env_registration.py`` precedent): KNOWN_TOOL_NAMES,
CATEGORY_MODULES, instance.py list-extend, worker ``infra`` opt-in,
and AST-source discovery.

Test rig mirrors ``tests/unit/test_p3_e2e_cycle.py``: a REAL
``SQLModelMcpServerRepository`` over a tmp SQLite engine — the same
repository production wires for both the HTTP lane and the KMS lane —
rather than MagicMock reads (a mock cannot prove R1 freshness).
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from sqlalchemy.engine import Engine
from sqlmodel import SQLModel, create_engine

from daemon.repositories.mcp_server import (
    McpServer,
    SQLModelMcpServerRepository,
)
from daemon.tools._tool_registry import (
    CATEGORY_MODULES,
    KNOWN_TOOL_NAMES,
    discover_source_only_tool_names,
)
from daemon.tools.infra import (
    _env_key_is_secret_shaped,
    create_mcp_env_tools,
)


REPO_ROOT = Path(__file__).resolve().parents[3]
SERVER_NAME = "opendesign"
MARKER = "__KMS_REF__KMS_HANDLE_test1234__"
BASE_URL_VALUE = "https://llm-supervisor-proxy.example/v1"
MODEL_VALUE = "vision"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def engine(tmp_path: Path) -> Engine:
    engine = create_engine(
        f"sqlite:///{tmp_path}/mcp_set_env_test.db",
        connect_args={"check_same_thread": False},
    )
    SQLModel.metadata.create_all(engine)
    yield engine
    engine.dispose()


@pytest.fixture
def repository(engine: Engine) -> SQLModelMcpServerRepository:
    return SQLModelMcpServerRepository(engine)


@pytest.fixture
def seed_server(repository: SQLModelMcpServerRepository) -> McpServer:
    """A builtin-shaped row whose env already carries a non-secret key
    AND a KMS marker (the post-``kms_attach`` state mcp_set_env must
    preserve)."""
    return repository.create_mcp_server(
        name=SERVER_NAME,
        description="OpenDesign MCP seam",
        config={
            "transport": "stdio",
            "command": "node",
            "args": ["server.js"],
            "env": {
                "OD_DAEMON_URL": "http://127.0.0.1:7456",
                "BYOK_API_KEY": MARKER,
            },
        },
        is_builtin=True,
    )


@pytest.fixture
def manager(repository: SQLModelMcpServerRepository) -> SimpleNamespace:
    """Bare manager namespace — exactly the attributes the tool
    dereferences. ``_mcp_service`` starts ABSENT so the cache guard's
    no-op path is the default; the cache test swaps one in."""
    return SimpleNamespace(_mcp_server_repository=repository)


@pytest.fixture
def mcp_set_env(manager: SimpleNamespace):
    tools = create_mcp_env_tools(manager, current_instance_id="test-iid")
    tool = next(t for t in tools if t.name == "mcp_set_env")
    return tool


def _read_row(repository: SQLModelMcpServerRepository, server_id: str) -> McpServer:
    row = repository.get_mcp_server(server_id)
    assert row is not None
    return row


# ---------------------------------------------------------------------------
# 1. Happy path
# ---------------------------------------------------------------------------


class TestHappyPath:
    def test_merge_preserves_existing_keys_and_markers(
        self, mcp_set_env, repository, seed_server
    ) -> None:
        result = json.loads(
            mcp_set_env.invoke(
                {
                    "server": SERVER_NAME,
                    "env": {"BYOK_BASE_URL": BASE_URL_VALUE, "BYOK_MODEL": MODEL_VALUE},
                }
            )
        )

        assert result["ok"] is True
        assert result["server_name"] == SERVER_NAME
        assert result["server_id"] == seed_server.id
        assert result["env_keys_set"] == ["BYOK_BASE_URL", "BYOK_MODEL"]

        row = _read_row(repository, seed_server.id)
        env = row.config["env"]
        # New keys landed...
        assert env["BYOK_BASE_URL"] == BASE_URL_VALUE
        assert env["BYOK_MODEL"] == MODEL_VALUE
        # ...pre-existing non-secret key survived...
        assert env["OD_DAEMON_URL"] == "http://127.0.0.1:7456"
        # ...and the KMS marker is byte-identical (R1).
        assert env["BYOK_API_KEY"] == MARKER

    def test_result_echoes_no_values(self, mcp_set_env, seed_server) -> None:
        """Tool results land in checkpoints (PB-F1 family) — the success
        payload must carry key NAMES only, never values."""
        raw = mcp_set_env.invoke(
            {"server": SERVER_NAME, "env": {"BYOK_BASE_URL": BASE_URL_VALUE}}
        )
        assert BASE_URL_VALUE not in raw

    def test_instance_metadata_untouched(
        self, mcp_set_env, repository, seed_server
    ) -> None:
        """``instance_metadata`` (bound_handles) is KMS-owned — the
        write path must leave absent fields unmodified."""
        repository.update_mcp_server(
            seed_server.id, instance_metadata={"bound_handles": [{"handle": "h1"}]}
        )
        mcp_set_env.invoke(
            {"server": SERVER_NAME, "env": {"BYOK_MODEL": MODEL_VALUE}}
        )
        row = _read_row(repository, seed_server.id)
        assert row.instance_metadata == {
            "bound_handles": [{"handle": "h1"}]
        }

    def test_resolve_by_id_fallback(self, mcp_set_env, repository, seed_server) -> None:
        """kms_attach only takes server_id — a caller that has the id
        (e.g. from a previous mcp_set_env result) must land too."""
        result = json.loads(
            mcp_set_env.invoke({"server": seed_server.id, "env": {"BYOK_MODEL": MODEL_VALUE}})
        )
        assert result["ok"] is True
        assert result["server_name"] == SERVER_NAME

    def test_reconnect_note_present(self, mcp_set_env, seed_server) -> None:
        """Open sessions keep the old env until close_server/new spawn —
        the note is part of the success contract."""
        raw = mcp_set_env.invoke(
            {"server": SERVER_NAME, "env": {"BYOK_MODEL": MODEL_VALUE}}
        )
        assert "close_server" in raw
        assert "reconnect" in raw


# ---------------------------------------------------------------------------
# 2. Unknown server
# ---------------------------------------------------------------------------


class TestUnknownServer:
    def test_unknown_name_clear_error(self, mcp_set_env, seed_server) -> None:
        raw = mcp_set_env.invoke(
            {"server": "no-such-server", "env": {"BYOK_MODEL": MODEL_VALUE}}
        )
        assert raw.startswith("ERROR: SERVER_NOT_FOUND")
        assert "no-such-server" in raw

    def test_empty_env_rejected(self, mcp_set_env, seed_server) -> None:
        raw = mcp_set_env.invoke({"server": SERVER_NAME, "env": {}})
        assert raw.startswith("ERROR: INVALID_ARGUMENT")


# ---------------------------------------------------------------------------
# 3. Secret-shaped rejection
# ---------------------------------------------------------------------------


class TestSecretShapedRejection:
    @pytest.mark.parametrize(
        "key",
        ["BYOK_API_KEY", "API_TOKEN", "CLIENT_SECRET", "DB_PASSWORD", "openai_key"],
    )
    def test_secret_shaped_key_rejected_points_to_kms_attach(
        self, mcp_set_env, repository, seed_server, key
    ) -> None:
        raw = mcp_set_env.invoke(
            {"server": SERVER_NAME, "env": {key: "sk-plaintext-DO-NOT-STORE"}}
        )
        assert raw.startswith("ERROR: SECRET_SHAPED_KEY"), raw
        assert key in raw
        # The error must route the caller to the marker lane.
        assert "kms_request" in raw
        assert "kms_attach" in raw
        # Row untouched — no plaintext written. The seed row may
        # legitimately already carry the key as a MARKER (post-
        # kms_attach state); the invariant is that the plaintext never
        # lands and any pre-existing value is unchanged.
        row = _read_row(repository, seed_server.id)
        env = row.config["env"]
        assert "sk-plaintext-DO-NOT-STORE" not in env.values()
        if key in env:
            assert env[key] == MARKER

    def test_byok_base_url_is_writable_despite_read_redaction(
        self, mcp_set_env, repository, seed_server
    ) -> None:
        """``BASE`` is a READ-side redaction marker only — BYOK_BASE_URL
        is exactly the value this tool exists to write."""
        raw = mcp_set_env.invoke(
            {"server": SERVER_NAME, "env": {"BYOK_BASE_URL": BASE_URL_VALUE}}
        )
        assert raw.startswith("{")  # success JSON, not an error
        assert (
            _read_row(repository, seed_server.id).config["env"]["BYOK_BASE_URL"]
            == BASE_URL_VALUE
        )

    def test_marker_classifier(self) -> None:
        assert _env_key_is_secret_shaped("BYOK_API_KEY")
        assert _env_key_is_secret_shaped("api_token")
        assert not _env_key_is_secret_shaped("BYOK_BASE_URL")
        assert not _env_key_is_secret_shaped("BYOK_MODEL")
        assert not _env_key_is_secret_shaped("OD_DAEMON_URL")


# ---------------------------------------------------------------------------
# 4. R1 freshness
# ---------------------------------------------------------------------------


class TestR1Freshness:
    def test_concurrent_marker_write_not_clobbered(
        self, mcp_set_env, repository, seed_server
    ) -> None:
        """A marker landing between two invocations (the interleaving a
        concurrent ``kms_attach`` produces) must survive the second
        write. A stale/cached row would resurrect the pre-marker env
        and silently drop the marker."""
        # Call 1: non-secret write — seeded marker must survive.
        mcp_set_env.invoke(
            {"server": SERVER_NAME, "env": {"BYOK_BASE_URL": BASE_URL_VALUE}}
        )
        env_after_call1 = _read_row(repository, seed_server.id).config["env"]
        assert env_after_call1["BYOK_API_KEY"] == MARKER
        assert env_after_call1["BYOK_BASE_URL"] == BASE_URL_VALUE

        # Concurrent lane: a SECOND kms_attach lands a new marker
        # (different handle) between the two invocations (direct repo
        # write on a fresh row — exactly what kms_attach does).
        fresh = _read_row(repository, seed_server.id)
        env = dict(fresh.config["env"])
        second_marker = "__KMS_REF__KMS_HANDLE_concurrent_lane__"
        env["BYOK_API_KEY"] = second_marker
        repository.update_mcp_server(seed_server.id, config={**fresh.config, "env": env})

        # Call 2: another non-secret write AFTER the marker landed.
        mcp_set_env.invoke({"server": SERVER_NAME, "env": {"BYOK_MODEL": MODEL_VALUE}})

        env_final = _read_row(repository, seed_server.id).config["env"]
        assert env_final["BYOK_API_KEY"] == second_marker, (
            "mcp_set_env clobbered a marker written by a concurrent lane "
            "— the R1 read-fresh invariant is broken"
        )
        assert env_final["BYOK_BASE_URL"] == BASE_URL_VALUE
        assert env_final["BYOK_MODEL"] == MODEL_VALUE

    def test_row_read_fresh_per_invocation_not_factory_snapshot(
        self, mcp_set_env, repository, seed_server
    ) -> None:
        """The row is read at CALL time — mutating it after factory
        build (before the first call) must still be visible."""
        # Row mutated after the factory ran but before any invocation.
        repository.update_mcp_server(
            seed_server.id,
            config={**seed_server.config, "env": {"LATE_KEY": "late-value"}},
        )
        mcp_set_env.invoke({"server": SERVER_NAME, "env": {"BYOK_MODEL": MODEL_VALUE}})
        env = _read_row(repository, seed_server.id).config["env"]
        assert env["LATE_KEY"] == "late-value"
        assert env["BYOK_MODEL"] == MODEL_VALUE


# ---------------------------------------------------------------------------
# 5. Schema-cache invalidation
# ---------------------------------------------------------------------------


class TestSchemaCacheInvalidation:
    def test_invalidate_called_with_server_name(self, repository, seed_server) -> None:
        manager = SimpleNamespace(
            _mcp_server_repository=repository,
            _mcp_service=MagicMock(),
        )
        tool = next(
            t for t in create_mcp_env_tools(manager, current_instance_id="test-iid")
            if t.name == "mcp_set_env"
        )
        tool.invoke({"server": SERVER_NAME, "env": {"BYOK_MODEL": MODEL_VALUE}})
        manager._mcp_service.invalidate_schema_cache.assert_called_once_with(SERVER_NAME)

    def test_noop_without_mcp_service(self, mcp_set_env, seed_server) -> None:
        """Legacy fixtures mock the manager without ``_mcp_service`` —
        the getattr guard must no-op, not raise."""
        raw = mcp_set_env.invoke(
            {"server": SERVER_NAME, "env": {"BYOK_MODEL": MODEL_VALUE}}
        )
        assert json.loads(raw)["ok"] is True


# ---------------------------------------------------------------------------
# 6. No key material in logs
# ---------------------------------------------------------------------------


class TestNoKeyMaterialInLogs:
    def test_audit_logs_carry_names_not_values(
        self, mcp_set_env, seed_server, caplog: pytest.LogCaptureFixture
    ) -> None:
        with caplog.at_level(logging.INFO, logger="daemon.tools.infra"):
            mcp_set_env.invoke(
                {
                    "server": SERVER_NAME,
                    "env": {"BYOK_BASE_URL": BASE_URL_VALUE, "BYOK_MODEL": MODEL_VALUE},
                }
            )
        text = caplog.text
        assert "mcp_set_env" in text
        assert "test-iid" in text  # actor attribution
        assert BASE_URL_VALUE not in text, "value leaked into logs"
        assert MODEL_VALUE not in text, "value leaked into logs"

    def test_error_path_leaks_no_values(
        self, mcp_set_env, seed_server, caplog: pytest.LogCaptureFixture
    ) -> None:
        with caplog.at_level(logging.INFO, logger="daemon.tools.infra"):
            raw = mcp_set_env.invoke(
                {"server": SERVER_NAME, "env": {"BYOK_API_KEY": "sk-plaintext-xyz"}}
            )
        assert raw.startswith("ERROR: SECRET_SHAPED_KEY")
        assert "sk-plaintext-xyz" not in caplog.text
        assert "sk-plaintext-xyz" not in raw


# ---------------------------------------------------------------------------
# Registration seam (4-step discipline, Task A precedent)
# ---------------------------------------------------------------------------


class TestRegistrationSeam:
    def test_known_tool_names_carries_mcp_set_env(self) -> None:
        assert "mcp_set_env" in KNOWN_TOOL_NAMES

    def test_category_modules_maps_infra_to_infra_module(self) -> None:
        assert CATEGORY_MODULES["infra"] == "daemon.tools.infra"

    def test_ast_source_discovery_finds_mcp_set_env(self) -> None:
        assert "mcp_set_env" in discover_source_only_tool_names()

    def test_factory_tool_attrs(self, mcp_set_env) -> None:
        assert mcp_set_env.name == "mcp_set_env"
        assert getattr(mcp_set_env, "_tool_category", None) == "infra"
        assert getattr(mcp_set_env, "_tool_category_first_party", False) is True

    def test_instance_py_list_extend_wiring(self) -> None:
        source = (REPO_ROOT / "daemon" / "tools" / "instance.py").read_text(
            encoding="utf-8"
        )
        assert "create_mcp_env_tools" in source
        assert "tools.extend(mcp_env_tool_list)" in source

    def test_worker_already_opts_into_infra(self) -> None:
        """KMS-trio precedent: worker reaches the tool through the
        EXISTING ``infra`` entry — no meta.json change, no new
        category."""
        import json as _json

        meta = _json.loads(
            (REPO_ROOT / "agents" / "worker" / "meta.json").read_text(encoding="utf-8")
        )
        assert "infra" in meta["tools"]["allow"]

    def test_trailing_newline_task_a_files(self) -> None:
        """MINOR-1 fold-in: the three Task-A files must end with \\n."""
        for rel in (
            "daemon/tools/ens_env_tools.py",
            "tests/unit/tools/test_ens_env_tools.py",
            "tests/unit/tools/test_ens_env_registration.py",
        ):
            data = (REPO_ROOT / rel).read_bytes()
            assert data.endswith(b"\n"), f"{rel} missing trailing newline"
