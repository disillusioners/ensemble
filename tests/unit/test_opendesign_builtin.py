"""Tests for the OpenDesignMCP builtin server definition (P3-WP5 fold).

Covers the P2-gap closure: the ``opendesign`` builtin class registers
into ``BuiltinServerRegistry`` so ``configure-builtin`` can provision
it. Values pinned from P2-WP4 §5.1 (verified against installed
open-design-mcp v0.16.1):

- name ``opendesign``, transport stdio, schema_version ``0.16.1``
- no args; env ``OD_DAEMON_URL`` (default http://127.0.0.1:7456) +
  ``OD_API_TOKEN`` (optional, KMS-marker-bearing)
- class name ``OpenDesignMCP`` is load-bearing (capabilities.yaml
  ``builtin_mcp_class`` contract) — resolved by the strict production
  registry loader.

In-memory surface only — NO daemon boot, NO DB, NO subprocess.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from daemon.mcp.builtin_servers import get_registry
from daemon.mcp.builtin_servers.opendesign import (
    OD_DAEMON_URL_DEFAULT,
    OD_MCP_COMMAND_ABSOLUTE,
    OD_MCP_COMMAND_BARE,
    OD_MCP_COMMAND_ENV,
    OpenDesignMCP,
    OpenDesignServerDefinition,
    _resolve_od_command,
)
from daemon.services.capability_resolver import _default_builtin_mcp_resolver


class TestRegistryRegistration:
    """The P2 gap: opendesign must be reachable via the builtin registry."""

    def test_registered_under_server_name(self):
        definition = get_registry().get_by_name("opendesign")
        assert definition is not None
        assert isinstance(definition, OpenDesignMCP)

    def test_class_name_is_load_bearing_contract(self):
        # capabilities.yaml declares builtin_mcp_class: OpenDesignMCP —
        # the class NAME must stay byte-identical or the strict
        # production registry load fails.
        assert OpenDesignMCP.__name__ == "OpenDesignMCP"

    def test_convention_alias_points_at_same_class(self):
        assert OpenDesignServerDefinition is OpenDesignMCP

    def test_schema_version_pinned_to_mcp_package(self):
        assert get_registry().get_by_name("opendesign").schema_version == "0.16.1"


class TestBaseConfig:
    """stdio transport, no args, verified command resolution."""

    def test_transport_stdio_no_args(self):
        config = OpenDesignMCP().get_base_config()
        assert config["transport"] == "stdio"
        assert config["args"] == []
        assert isinstance(config["command"], str) and config["command"]

    def test_command_prefers_env_override(self, monkeypatch):
        monkeypatch.setenv(OD_MCP_COMMAND_ENV, "/custom/path/open-design-mcp")
        assert _resolve_od_command() == "/custom/path/open-design-mcp"

    def test_command_absolute_fallback_when_binary_exists(self, monkeypatch):
        monkeypatch.delenv(OD_MCP_COMMAND_ENV, raising=False)
        # The ops-lane install root is verified present on this host;
        # if it ever moves, the resolver must degrade to the bare name.
        if Path(OD_MCP_COMMAND_ABSOLUTE).exists():
            assert _resolve_od_command() == OD_MCP_COMMAND_ABSOLUTE
        else:
            assert _resolve_od_command() == OD_MCP_COMMAND_BARE

    def test_command_bare_when_no_override_and_no_binary(self, monkeypatch):
        monkeypatch.delenv(OD_MCP_COMMAND_ENV, raising=False)
        monkeypatch.setattr(
            "daemon.mcp.builtin_servers.opendesign.OD_MCP_COMMAND_ABSOLUTE",
            "/nonexistent/open-design-mcp",
        )
        assert _resolve_od_command() == OD_MCP_COMMAND_BARE


class TestBuildConfig:
    """Env contract: OD_DAEMON_URL default + marker-bearing OD_API_TOKEN."""

    def test_empty_values_yield_daemon_url_default_only(self):
        config = OpenDesignMCP().build_config({})
        assert config["env"] == {"OD_DAEMON_URL": OD_DAEMON_URL_DEFAULT}
        assert "OD_API_TOKEN" not in config["env"]

    def test_marker_bearing_token_passes_through_verbatim(self):
        marker = "__KMS_REF__KMS_HANDLE_abc123__"
        config = OpenDesignMCP().build_config({"od_api_token": marker})
        assert config["env"]["OD_API_TOKEN"] == marker
        # Plaintext hygiene: the ONLY secret-shaped datum in the config
        # is the marker itself.
        assert "sk-" not in config["env"]["OD_API_TOKEN"]

    def test_daemon_url_override_uppercased_into_env(self):
        config = OpenDesignMCP().build_config(
            {"od_daemon_url": "http://127.0.0.1:9999"}
        )
        assert config["env"]["OD_DAEMON_URL"] == "http://127.0.0.1:9999"

    def test_env_only_schema_no_args_leakage(self):
        config = OpenDesignMCP().build_config(
            {"od_api_token": "marker", "od_daemon_url": "http://x"}
        )
        assert config["args"] == []

    def test_parse_config_round_trip(self):
        definition = OpenDesignMCP()
        built = definition.build_config(
            {"od_daemon_url": "http://127.0.0.1:7777", "od_api_token": "m"}
        )
        values = definition.parse_config(built)
        assert values["od_daemon_url"] == "http://127.0.0.1:7777"
        assert values["od_api_token"] == "m"


class TestConfigSchema:
    """Schema fields match the P2-WP4 §5.1 env contract."""

    def test_schema_keys_and_sections(self):
        schema = {f["key"]: f for f in OpenDesignMCP().get_config_schema()}
        assert set(schema) == {"od_daemon_url", "od_api_token"}
        assert all(f["section"] == "env" for f in schema.values())

    def test_daemon_url_default_present_token_optional(self):
        schema = {f["key"]: f for f in OpenDesignMCP().get_config_schema()}
        assert schema["od_daemon_url"]["default"] == OD_DAEMON_URL_DEFAULT
        assert schema["od_api_token"]["default"] is None
        # Both optional at the schema layer: the default satisfies the
        # server's OD_DAEMON_URL requirement; loopback needs no token.
        assert schema["od_daemon_url"]["required"] is False
        assert schema["od_api_token"]["required"] is False


class TestProductionResolver:
    """builtin_mcp_class resolution (class-name OR server-name)."""

    def test_resolves_by_class_name(self):
        resolved = _default_builtin_mcp_resolver("OpenDesignMCP")
        assert isinstance(resolved, OpenDesignMCP)

    def test_resolves_by_server_name(self):
        resolved = _default_builtin_mcp_resolver("opendesign")
        assert isinstance(resolved, OpenDesignMCP)

    def test_unknown_returns_none(self):
        assert _default_builtin_mcp_resolver("NoSuchClass") is None
