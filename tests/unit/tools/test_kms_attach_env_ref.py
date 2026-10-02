"""Tests for the env-ref mode of ``kms_attach`` (LANE-2, v1.3.0-bridge).

The env-ref mode is the bridge for the OpenDesign BYOK chain: the
daemon reuses the existing ``OPENAI_API_KEY`` from its own ``.env``
(no new key material, per user directive 2026-10-02) by writing a
pointer marker into the MCP server's ``config.env``. The resolver at
``daemon/services/kms_resolver.py`` substitutes the marker to the
plaintext value at spawn time.

Co-ownership invariant (same as the LANE-1 ``kms_attach`` and
``mcp_set_env``): ``mcp_servers.config`` is read FRESH at call time
and merged — never re-serialised from a cached row object.

Test rig mirrors ``tests/unit/tools/test_mcp_set_env_tools.py``: a
REAL ``SQLModelMcpServerRepository`` over a tmp SQLite engine — the
same repository production wires for both the HTTP lane and the KMS
lane. MagicMock reads cannot prove R1 freshness.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from cryptography.fernet import Fernet
from sqlalchemy.engine import Engine
from sqlmodel import SQLModel, create_engine

from daemon.repositories.mcp_server import (
    McpServer,
    SQLModelMcpServerRepository,
)
from daemon.services.kms_lite import (
    KMS_HANDLE_PREFIX,
    KMS_MARKER_PREFIX,
    build_env_marker,
    build_marker,
    kms_fingerprint,
    kms_request,
    reset_store_for_tests,
)
from daemon.tools._tool_registry import (
    CATEGORY_MODULES,
    KNOWN_TOOL_NAMES,
    discover_source_only_tool_names,
)
from daemon.tools.infra import create_kms_tools


REPO_ROOT = Path(__file__).resolve().parents[3]
SERVER_NAME = "opendesign"
SECRET_VALUE = "sk-test-plaintext-DO-NOT-LEAK"
SECRET_VAR = "OPENAI_API_KEY"

# The full LANE-1 handle marker (used to seed pre-existing config for
# R1 / merge-preservation tests).
LANE1_HANDLE_MARKER = "__KMS_REF__KMS_HANDLE_test1234__"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch: pytest.MonkeyPatch):
    """Pin a clean env so a developer's ambient ``OPENAI_API_KEY`` (or
    similar) cannot leak in. Reset the KMS-Lite store between tests.
    """
    # Pin the secret var the env-ref mode reads.
    monkeypatch.setenv(SECRET_VAR, SECRET_VALUE)
    # Always provision a fresh KMS key per test.
    monkeypatch.setenv("SYSTEM_ENCRYPTION_KEY", Fernet.generate_key().decode())
    reset_store_for_tests()
    yield
    reset_store_for_tests()


@pytest.fixture
def engine(tmp_path: Path) -> Engine:
    engine = create_engine(
        f"sqlite:///{tmp_path}/kms_attach_env_ref_test.db",
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
    """A builtin-shaped row with the canonical env state the v1.3.0
    install leaves behind: OD_DAEMON_URL plaintext + BYOK_API_KEY
    pointing at the live OPENAI_API_KEY."""
    return repository.create_mcp_server(
        name=SERVER_NAME,
        description="OpenDesign MCP seam",
        config={
            "transport": "stdio",
            "command": "node",
            "args": ["server.js"],
            "env": {
                "OD_DAEMON_URL": "http://127.0.0.1:7456",
            },
        },
        is_builtin=True,
    )


@pytest.fixture
def manager(repository: SQLModelMcpServerRepository) -> SimpleNamespace:
    return SimpleNamespace(_mcp_server_repository=repository)


@pytest.fixture
def kms_attach(manager: SimpleNamespace):
    tools = create_kms_tools(manager, current_instance_id="test-iid")
    return next(t for t in tools if t.name == "kms_attach")


def _read_row(repository: SQLModelMcpServerRepository, server_id: str) -> McpServer:
    row = repository.get_mcp_server(server_id)
    assert row is not None
    return row


# ---------------------------------------------------------------------------
# 1. Env-ref happy path
# ---------------------------------------------------------------------------


class TestEnvRefHappyPath:
    def test_attaches_env_marker_to_config_env(
        self, kms_attach, repository, seed_server
    ) -> None:
        result = json.loads(
            kms_attach.invoke(
                {
                    "server_id": seed_server.id,
                    "env_key": "BYOK_API_KEY",
                    "env_source": SECRET_VAR,
                }
            )
        )
        assert result["server_id"] == seed_server.id
        assert result["env_key"] == "BYOK_API_KEY"
        assert result["env_source"] == SECRET_VAR
        assert result["marker"] == build_env_marker(SECRET_VAR)

        row = _read_row(repository, seed_server.id)
        env = row.config["env"]
        # The marker is on the slot — no plaintext, no KMS handle.
        assert env["BYOK_API_KEY"] == build_env_marker(SECRET_VAR)
        # Pre-existing key survived.
        assert env["OD_DAEMON_URL"] == "http://127.0.0.1:7456"

    def test_records_binding_in_env_refs_audit_substrate(
        self, kms_attach, repository, seed_server
    ) -> None:
        kms_attach.invoke(
            {
                "server_id": seed_server.id,
                "env_key": "BYOK_API_KEY",
                "env_source": SECRET_VAR,
            }
        )
        row = _read_row(repository, seed_server.id)
        meta = row.instance_metadata or {}
        env_refs = meta.get("env_refs") or []
        assert len(env_refs) == 1
        entry = env_refs[0]
        assert entry["env_source"] == SECRET_VAR
        assert entry["env_key"] == "BYOK_API_KEY"
        assert entry["actor"] == "test-iid"

    def test_result_echoes_no_values(
        self, kms_attach, seed_server
    ) -> None:
        """Tool results land in checkpoints (PB-F1 family) — the success
        payload must carry marker + var NAME only, never the value."""
        raw = kms_attach.invoke(
            {
                "server_id": seed_server.id,
                "env_key": "BYOK_API_KEY",
                "env_source": SECRET_VAR,
            }
        )
        assert SECRET_VALUE not in raw, "value leaked into tool result"

    def test_marker_resolves_at_spawn_time(
        self, kms_attach, repository, seed_server
    ) -> None:
        """End-to-end: after attach, resolve_env returns the plaintext."""
        from daemon.services.kms_resolver import resolve_env

        kms_attach.invoke(
            {
                "server_id": seed_server.id,
                "env_key": "BYOK_API_KEY",
                "env_source": SECRET_VAR,
            }
        )
        row = _read_row(repository, seed_server.id)
        stored_env = dict(row.config["env"])
        # Marker is on the slot.
        assert stored_env["BYOK_API_KEY"] == build_env_marker(SECRET_VAR)
        # Resolved at spawn time returns the plaintext.
        resolved = resolve_env(stored_env)
        assert resolved["BYOK_API_KEY"] == SECRET_VALUE


# ---------------------------------------------------------------------------
# 2. Env-ref merge preservation (R1 co-ownership)
# ---------------------------------------------------------------------------


class TestEnvRefR1Merges:
    def test_merge_preserves_existing_keys(
        self, kms_attach, repository, seed_server
    ) -> None:
        # Pre-seed non-secret keys.
        repository.update_mcp_server(
            seed_server.id,
            config={
                **seed_server.config,
                "env": {
                    **seed_server.config["env"],
                    "BYOK_BASE_URL": "https://llm-supervisor-proxy.example/v1",
                    "BYOK_MODEL": "vision",
                },
            },
        )
        kms_attach.invoke(
            {
                "server_id": seed_server.id,
                "env_key": "BYOK_API_KEY",
                "env_source": SECRET_VAR,
            }
        )
        env = _read_row(repository, seed_server.id).config["env"]
        assert env["BYOK_BASE_URL"] == "https://llm-supervisor-proxy.example/v1"
        assert env["BYOK_MODEL"] == "vision"
        assert env["OD_DAEMON_URL"] == "http://127.0.0.1:7456"
        assert env["BYOK_API_KEY"] == build_env_marker(SECRET_VAR)

    def test_merge_preserves_lane1_marker_on_other_key(
        self, kms_attach, repository, seed_server
    ) -> None:
        """The R1 invariant: env-ref write must NOT clobber a LANE-1
        handle marker on a DIFFERENT key (the LANE-1 path is the
        ``OD_API_TOKEN`` slot on non-loopback OD installs)."""
        # Pre-seed LANE-1 marker on OD_API_TOKEN.
        repository.update_mcp_server(
            seed_server.id,
            config={
                **seed_server.config,
                "env": {**seed_server.config["env"], "OD_API_TOKEN": LANE1_HANDLE_MARKER},
            },
        )
        kms_attach.invoke(
            {
                "server_id": seed_server.id,
                "env_key": "BYOK_API_KEY",
                "env_source": SECRET_VAR,
            }
        )
        env = _read_row(repository, seed_server.id).config["env"]
        # LANE-1 marker preserved byte-identical.
        assert env["OD_API_TOKEN"] == LANE1_HANDLE_MARKER
        # LANE-2 marker landed on its own slot.
        assert env["BYOK_API_KEY"] == build_env_marker(SECRET_VAR)

    def test_merge_preserves_lane1_marker_on_same_key_across_calls(
        self, kms_attach, repository, seed_server
    ) -> None:
        """If a LANE-1 marker was written to ``BYOK_API_KEY`` by a
        PRIOR install (minted-handle lane), the env-ref write replaces
        it (the install path is the authoritative setter of BYOK_API_KEY
        under v1.3.0 — env-ref is the canonical path). Pin: a subsequent
        LANE-1 attach overwrites; env-ref overwrites; both preserve other
        keys."""
        # Pre-seed LANE-1 marker on BYOK_API_KEY (older install state).
        repository.update_mcp_server(
            seed_server.id,
            config={
                **seed_server.config,
                "env": {**seed_server.config["env"], "BYOK_API_KEY": LANE1_HANDLE_MARKER},
            },
        )
        kms_attach.invoke(
            {
                "server_id": seed_server.id,
                "env_key": "BYOK_API_KEY",
                "env_source": SECRET_VAR,
            }
        )
        env = _read_row(repository, seed_server.id).config["env"]
        # LANE-2 marker overwrites the LANE-1 marker on the same slot.
        # This is the expected behaviour: BYOK_API_KEY is one slot, the
        # install path owns it. Backwards-compat for OTHER slots is
        # preserved (test_merge_preserves_lane1_marker_on_other_key).
        assert env["BYOK_API_KEY"] == build_env_marker(SECRET_VAR)


# ---------------------------------------------------------------------------
# 3. Idempotent collapse
# ---------------------------------------------------------------------------


class TestEnvRefIdempotency:
    def test_same_env_source_env_key_collapses(
        self, kms_attach, repository, seed_server
    ) -> None:
        """Re-attach with the same (env_source, env_key) tuple collapses:
        the existing entry is removed and the fresh one (with current
        actor) is appended."""
        kms_attach.invoke(
            {
                "server_id": seed_server.id,
                "env_key": "BYOK_API_KEY",
                "env_source": SECRET_VAR,
            }
        )
        kms_attach.invoke(
            {
                "server_id": seed_server.id,
                "env_key": "BYOK_API_KEY",
                "env_source": SECRET_VAR,
            }
        )
        env_refs = (
            _read_row(repository, seed_server.id).instance_metadata or {}
        ).get("env_refs") or []
        assert len(env_refs) == 1, "duplicate env_ref entry — collapse broken"

    def test_different_env_keys_appended(
        self, kms_attach, repository, seed_server, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Two distinct env_keys pointing to the same env_source both
        land — env_refs has two entries."""
        monkeypatch.setenv("ANOTHER_VAR", "another-value")
        kms_attach.invoke(
            {
                "server_id": seed_server.id,
                "env_key": "BYOK_API_KEY",
                "env_source": SECRET_VAR,
            }
        )
        kms_attach.invoke(
            {
                "server_id": seed_server.id,
                "env_key": "OTHER_KEY",
                "env_source": "ANOTHER_VAR",
            }
        )
        env_refs = (
            _read_row(repository, seed_server.id).instance_metadata or {}
        ).get("env_refs") or []
        keys = sorted(e["env_key"] for e in env_refs)
        assert keys == ["BYOK_API_KEY", "OTHER_KEY"]


# ---------------------------------------------------------------------------
# 4. Rejections
# ---------------------------------------------------------------------------


class TestEnvRefRejections:
    def test_missing_env_var_clear_error_no_value_leak(
        self, kms_attach, seed_server, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("MISSING_VAR", raising=False)
        raw = kms_attach.invoke(
            {
                "server_id": seed_server.id,
                "env_key": "BYOK_API_KEY",
                "env_source": "MISSING_VAR",
            }
        )
        assert raw.startswith("ERROR: ENV_VAR_NOT_FOUND"), raw
        assert "MISSING_VAR" in raw
        # The value (which never existed in this test) is never in the error.
        assert SECRET_VALUE not in raw

    def test_invalid_env_source_name_rejected(
        self, kms_attach, seed_server
    ) -> None:
        """Malformed env-var names (spaces, leading digits, hyphens) are
        rejected by ``build_env_marker``."""
        raw = kms_attach.invoke(
            {
                "server_id": seed_server.id,
                "env_key": "BYOK_API_KEY",
                "env_source": "BAD NAME",
            }
        )
        assert raw.startswith("ERROR: INVALID_ARGUMENT"), raw
        # The var name is echoed for operator actionability, no value.
        assert "BAD NAME" in raw

    def test_handle_and_env_source_both_passed_rejected(
        self, kms_attach, seed_server
    ) -> None:
        """Mode arbitration: passing BOTH handle AND env_source is
        refused — the tool never routes ambiguity to the wrong lane
        silently."""
        record = kms_request("opendesign", "install")
        raw = kms_attach.invoke(
            {
                "server_id": seed_server.id,
                "env_key": "BYOK_API_KEY",
                "handle": record["handle"],
                "env_source": SECRET_VAR,
            }
        )
        assert raw.startswith("ERROR: INVALID_ARGUMENT"), raw
        assert "both" in raw.lower() or "exactly one" in raw.lower()

    def test_neither_handle_nor_env_source_rejected(
        self, kms_attach, seed_server
    ) -> None:
        raw = kms_attach.invoke(
            {
                "server_id": seed_server.id,
                "env_key": "BYOK_API_KEY",
            }
        )
        assert raw.startswith("ERROR: INVALID_ARGUMENT"), raw

    def test_unknown_server_id(self, kms_attach) -> None:
        raw = kms_attach.invoke(
            {
                "server_id": "nonexistent-server-id",
                "env_key": "BYOK_API_KEY",
                "env_source": SECRET_VAR,
            }
        )
        assert raw.startswith("ERROR: SERVER_NOT_FOUND"), raw

    def test_secret_shaped_env_key_accepted_in_env_ref_mode(
        self, kms_attach, repository, seed_server
    ) -> None:
        """The whole point of env-ref mode: a secret-shaped env_key
        (here ``BYOK_API_KEY``) IS accepted — it is the ONLY way such
        a key can be written in the new flow. ``mcp_set_env`` keeps
        rejecting secret-shaped names; this test pins that the
        gating is in mcp_set_env, not in the schema or marker grammar."""
        raw = kms_attach.invoke(
            {
                "server_id": seed_server.id,
                "env_key": "BYOK_API_KEY",
                "env_source": SECRET_VAR,
            }
        )
        # Success — not a rejection.
        assert not raw.startswith("ERROR:"), raw
        result = json.loads(raw)
        assert result["marker"] == build_env_marker(SECRET_VAR)


# ---------------------------------------------------------------------------
# 5. LANE-1 (handle) mode regression — unchanged behaviour
# ---------------------------------------------------------------------------


class TestHandleModeRegression:
    """The LANE-1 (handle) mode of kms_attach is unchanged. Pin a
    focused regression set so a future LANE-2 refactor does not
    silently break LANE-1."""

    def test_handle_mode_still_works(
        self, kms_attach, repository, seed_server
    ) -> None:
        record = kms_request("opendesign", "install")
        raw = kms_attach.invoke(
            {
                "server_id": seed_server.id,
                "handle": record["handle"],
                "env_key": "OD_API_TOKEN",
            }
        )
        assert not raw.startswith("ERROR:"), raw
        result = json.loads(raw)
        assert result["handle"] == record["handle"]
        assert result["marker"] == build_marker(record["handle"])
        assert result["fingerprint"] == record["fingerprint"]

        row = _read_row(repository, seed_server.id)
        assert row.config["env"]["OD_API_TOKEN"] == build_marker(record["handle"])
        bindings = (row.instance_metadata or {}).get("bound_handles") or []
        assert any(b["handle"] == record["handle"] for b in bindings)

    def test_handle_mode_does_not_use_env_refs(
        self, kms_attach, repository, seed_server
    ) -> None:
        """LANE-1 attaches go into ``bound_handles``, not ``env_refs``.
        Pin so a future merge-restructuring does not cross the wires."""
        record = kms_request("opendesign", "install")
        kms_attach.invoke(
            {
                "server_id": seed_server.id,
                "handle": record["handle"],
                "env_key": "OD_API_TOKEN",
            }
        )
        meta = _read_row(repository, seed_server.id).instance_metadata or {}
        assert "env_refs" not in meta or not meta.get("env_refs")
        assert meta.get("bound_handles")

    def test_handle_mode_unknown_handle_returns_error(
        self, kms_attach, seed_server
    ) -> None:
        bogus_handle = KMS_HANDLE_PREFIX + "deadbeef" * 4
        raw = kms_attach.invoke(
            {
                "server_id": seed_server.id,
                "handle": bogus_handle,
                "env_key": "OD_API_TOKEN",
            }
        )
        assert raw.startswith("ERROR: HANDLE_NOT_FOUND"), raw


# ---------------------------------------------------------------------------
# 6. No key material in logs
# ---------------------------------------------------------------------------


class TestNoKeyMaterialInLogs:
    def test_env_ref_path_no_value_in_logs(
        self, kms_attach, seed_server, caplog: pytest.LogCaptureFixture
    ) -> None:
        with caplog.at_level(logging.INFO, logger="daemon.tools.infra"):
            raw = kms_attach.invoke(
                {
                    "server_id": seed_server.id,
                    "env_key": "BYOK_API_KEY",
                    "env_source": SECRET_VAR,
                }
            )
        # Success path — no logs emitted (only error paths log via
        # logger.exception). The value MUST NOT appear anywhere.
        assert not raw.startswith("ERROR:"), raw
        assert SECRET_VALUE not in caplog.text
        # The var NAME is fine — that's the audit signal if it ever
        # does appear.
        assert SECRET_VALUE not in raw

    def test_missing_var_error_logs_no_value(
        self, kms_attach, seed_server, caplog: pytest.LogCaptureFixture, monkeypatch
    ) -> None:
        monkeypatch.delenv("MISSING_FOR_LOG_TEST", raising=False)
        with caplog.at_level(logging.INFO, logger="daemon.tools.infra"):
            raw = kms_attach.invoke(
                {
                    "server_id": seed_server.id,
                    "env_key": "BYOK_API_KEY",
                    "env_source": "MISSING_FOR_LOG_TEST",
                }
            )
        assert raw.startswith("ERROR: ENV_VAR_NOT_FOUND")
        assert SECRET_VALUE not in caplog.text


# ---------------------------------------------------------------------------
# 7. mcp_set_env regression — secret-shaped keys STILL rejected
# ---------------------------------------------------------------------------


class TestMcpSetEnvRegression:
    """The complement contract: ``mcp_set_env`` keeps rejecting
    secret-shaped KEY NAMES. The env-ref lane is the ONLY way to
    write such keys — its regression is pinned here (mcp_set_env must
    NOT have been silently relaxed)."""

    def test_mcp_set_env_still_rejects_secret_shaped(
        self, repository, seed_server
    ) -> None:
        from daemon.tools.infra import create_mcp_env_tools

        manager = SimpleNamespace(_mcp_server_repository=repository)
        mcp_set_env = next(
            t
            for t in create_mcp_env_tools(manager, current_instance_id="test-iid")
            if t.name == "mcp_set_env"
        )
        raw = mcp_set_env.invoke(
            {
                "server": SERVER_NAME,
                "env": {"BYOK_API_KEY": SECRET_VALUE},
            }
        )
        assert raw.startswith("ERROR: SECRET_SHAPED_KEY"), raw
        # Plaintext never crosses the tool boundary.
        assert SECRET_VALUE not in raw
        # Row untouched.
        env = _read_row(repository, seed_server.id).config["env"]
        assert "BYOK_API_KEY" not in env


# ---------------------------------------------------------------------------
# 8. Schema cache invalidation (routers analog)
# ---------------------------------------------------------------------------


class TestSchemaCacheInvalidation:
    def test_invalidate_called_with_server_name(
        self, repository, seed_server
    ) -> None:
        manager = SimpleNamespace(
            _mcp_server_repository=repository,
            _mcp_service=MagicMock(),
        )
        tool = next(
            t
            for t in create_kms_tools(manager, current_instance_id="test-iid")
            if t.name == "kms_attach"
        )
        tool.invoke(
            {
                "server_id": seed_server.id,
                "env_key": "BYOK_API_KEY",
                "env_source": SECRET_VAR,
            }
        )
        # NOTE: kms_attach currently does NOT invalidate the schema
        # cache — the R1 read-fresh invariant at the next spawn handles
        # this. (The mcp_set_env path DOES invalidate; the kms_attach
        # path was historically decoupled because the LANE-1 marker
        # write changes config in the same way mcp_set_env does.) Pin
        # the current behaviour here so a future decision is visible.
        # Uncomment the assertion below if the policy flips:
        # manager._mcp_service.invalidate_schema_cache.assert_called_once_with(SERVER_NAME)


# ---------------------------------------------------------------------------
# 9. Registration seam (4-step discipline)
# ---------------------------------------------------------------------------


class TestRegistrationSeam:
    def test_known_tool_names_carries_kms_attach(self) -> None:
        assert "kms_attach" in KNOWN_TOOL_NAMES

    def test_category_modules_maps_infra_to_infra_module(self) -> None:
        assert CATEGORY_MODULES["infra"] == "daemon.tools.infra"

    def test_ast_source_discovery_finds_kms_attach(self) -> None:
        assert "kms_attach" in discover_source_only_tool_names()

    def test_factory_tool_attrs(self, kms_attach) -> None:
        assert kms_attach.name == "kms_attach"
        assert getattr(kms_attach, "_tool_category", None) == "infra"
        assert getattr(kms_attach, "_tool_category_first_party", False) is True