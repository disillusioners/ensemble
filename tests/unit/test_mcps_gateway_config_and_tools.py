"""Unit tests for the remote-hands configuration and the agent tools.

Two things live here:

* the knobs of ``daemon/services/mcps_gateway/config.py`` — every knob is
  read at CALL time (never at import time), so a test can flip one with
  ``monkeypatch.setenv``;
* the four agent-facing tools of ``daemon/tools/mcp_rpc_tools.py`` — the
  in-body ``agent_id`` gate (the category gate is not sufficient: a
  LITERAL tool name in an allow list bypasses it) and the refusal while
  the transport is off.
"""

from __future__ import annotations

import pytest

from daemon.services.mcps_gateway.config import (
    DEFAULT_CLIENT_DEAD_DETECT_MS,
    DEFAULT_COMMAND_TIMEOUT_MS,
    DEFAULT_HEARTBEAT_INTERVAL_MS,
    DEFAULT_PATH,
    DEFAULT_SESSION_IDLE_TIMEOUT_MS,
    DEFAULT_SESSION_TTL_MS,
    ENV_PREFIX,
    gateway_enabled,
    load_gateway_config,
)
from daemon.services.mcps_gateway.protocol import (
    CODE_INTERNAL,
    CODE_MACHINE_NOT_ENROLLED,
)
from daemon.services.mcps_gateway.service import McpGatewayService
from daemon.tools.mcp_rpc_tools import (
    MCP_RPC_AUTHORIZED_AGENTS,
    create_mcp_rpc_tools,
)


# ─────────────────────────────────────────────────────────────────────────────
# The configuration
# ─────────────────────────────────────────────────────────────────────────────


class TestGatewayConfig:
    def test_the_defaults_are_the_spec_defaults(self, monkeypatch):
        for name in (
            "ENABLED",
            "PATH",
            "TOKENS",
            "HEARTBEAT_INTERVAL_MS",
            "CLIENT_DEAD_DETECT_MS",
            "COMMAND_TIMEOUT_MS",
            "AUDIT_ENABLED",
            "ALLOWED_MACHINES",
            "SESSION_TTL_MS",
            "SESSION_IDLE_TIMEOUT_MS",
        ):
            monkeypatch.delenv(ENV_PREFIX + name, raising=False)
        config = load_gateway_config()
        assert config.enabled is False
        assert config.path == DEFAULT_PATH
        assert config.tokens == ()
        assert config.heartbeat_interval_ms == DEFAULT_HEARTBEAT_INTERVAL_MS
        assert config.client_dead_detect_ms == DEFAULT_CLIENT_DEAD_DETECT_MS
        assert config.heartbeat_timeout_ms == DEFAULT_CLIENT_DEAD_DETECT_MS
        assert config.command_timeout_ms == DEFAULT_COMMAND_TIMEOUT_MS
        assert config.audit_enabled is True
        assert config.allowed_machines == ()
        assert config.session_ttl_ms == DEFAULT_SESSION_TTL_MS
        assert config.session_idle_timeout_ms == DEFAULT_SESSION_IDLE_TIMEOUT_MS

    def test_the_kill_switch_is_default_off(self, monkeypatch):
        monkeypatch.delenv(ENV_PREFIX + "ENABLED", raising=False)
        assert gateway_enabled() is False

    @pytest.mark.parametrize("value", ["1", "true", "yes", "on", "TRUE"])
    def test_the_kill_switch_can_be_turned_on(self, monkeypatch, value):
        monkeypatch.setenv(ENV_PREFIX + "ENABLED", value)
        assert gateway_enabled() is True

    @pytest.mark.parametrize("value", ["0", "false", "no", "off", "OFF"])
    def test_the_kill_switch_can_be_turned_off_explicitly(self, monkeypatch, value):
        monkeypatch.setenv(ENV_PREFIX + "ENABLED", value)
        assert gateway_enabled() is False

    def test_multiple_tokens_are_supported(self, monkeypatch):
        monkeypatch.setenv(ENV_PREFIX + "TOKENS", "one, two ,three")
        config = load_gateway_config()
        assert config.tokens == ("one", "two", "three")
        assert config.token_is_valid("two") is True
        assert config.token_is_valid("four") is False

    def test_an_empty_token_set_means_nobody_may_enroll(self, monkeypatch):
        monkeypatch.setenv(ENV_PREFIX + "TOKENS", "")
        config = load_gateway_config()
        assert config.has_enrollment_tokens is False
        assert config.token_is_valid("anything") is False

    def test_an_empty_allowlist_allows_every_machine(self, monkeypatch):
        monkeypatch.setenv(ENV_PREFIX + "ALLOWED_MACHINES", "")
        config = load_gateway_config()
        assert config.machine_allowed("mach_anything") is True

    def test_a_non_empty_allowlist_is_an_allowlist(self, monkeypatch):
        monkeypatch.setenv(ENV_PREFIX + "ALLOWED_MACHINES", "mach_a,mach_b")
        config = load_gateway_config()
        assert config.machine_allowed("mach_a") is True
        assert config.machine_allowed("mach_c") is False

    def test_the_knobs_are_read_at_call_time(self, monkeypatch):
        monkeypatch.setenv(ENV_PREFIX + "HEARTBEAT_INTERVAL_MS", "1234")
        assert load_gateway_config().heartbeat_interval_ms == 1234
        monkeypatch.setenv(ENV_PREFIX + "HEARTBEAT_INTERVAL_MS", "4321")
        assert load_gateway_config().heartbeat_interval_ms == 4321

    def test_the_knobs_are_env_overridable_for_fast_tests(self, monkeypatch):
        """The integration tests need short heartbeats."""
        monkeypatch.setenv(ENV_PREFIX + "HEARTBEAT_INTERVAL_MS", "200")
        monkeypatch.setenv(ENV_PREFIX + "CLIENT_DEAD_DETECT_MS", "600")
        monkeypatch.setenv(ENV_PREFIX + "COMMAND_TIMEOUT_MS", "500")
        monkeypatch.setenv(ENV_PREFIX + "SESSION_TTL_MS", "1000")
        monkeypatch.setenv(ENV_PREFIX + "SESSION_IDLE_TIMEOUT_MS", "900")
        config = load_gateway_config()
        assert config.heartbeat_interval_ms == 200
        assert config.client_dead_detect_ms == 600
        assert config.command_timeout_ms == 500
        assert config.session_ttl_ms == 1000
        assert config.session_idle_timeout_ms == 900

    def test_an_unusable_value_is_reported_and_the_default_is_used(self, monkeypatch):
        monkeypatch.setenv(ENV_PREFIX + "HEARTBEAT_INTERVAL_MS", "not-a-number")
        config = load_gateway_config()
        assert config.heartbeat_interval_ms == DEFAULT_HEARTBEAT_INTERVAL_MS
        assert config.warnings

    def test_a_timeout_below_three_intervals_is_reported(self, monkeypatch):
        monkeypatch.setenv(ENV_PREFIX + "HEARTBEAT_INTERVAL_MS", "5000")
        monkeypatch.setenv(ENV_PREFIX + "CLIENT_DEAD_DETECT_MS", "6000")
        config = load_gateway_config()
        assert any("3x" in w for w in config.warnings)


# ─────────────────────────────────────────────────────────────────────────────
# The agent-facing tools
# ─────────────────────────────────────────────────────────────────────────────


class _StubService:
    """A stand-in for the gateway service — the tools only call four methods."""

    def __init__(self, *, enabled: bool = True, machines=None, error=None):
        self.enabled = enabled
        self.machines = machines if machines is not None else []
        self.error = error
        self.calls: list[tuple[str, dict]] = []

    def roster(self):
        self.calls.append(("roster", {}))
        return self.machines

    async def connect(self, **kwargs):
        self.calls.append(("connect", kwargs))
        if self.error:
            raise self.error
        return {"session_id": "sess_1", "machine_id": kwargs["machine_id"], "tools": []}

    async def command(self, **kwargs):
        self.calls.append(("command", kwargs))
        if self.error:
            raise self.error
        return {"content": [{"type": "text", "text": "ok"}]}

    async def disconnect(self, **kwargs):
        self.calls.append(("disconnect", kwargs))
        if self.error:
            raise self.error
        return {"session_id": "sess_1", "ended": True}


def _tools(service, agent_id: str):
    return {
        tool.name: tool
        for tool in create_mcp_rpc_tools(
            service, current_instance_id="inst-1", agent_id=agent_id
        )
    }


class TestMcpRpcTools:
    def test_the_four_tools_are_built(self):
        tools = _tools(_StubService(), "ari")
        assert set(tools) == {
            "mcp_rpc_list_machine",
            "mcp_rpc_connect",
            "mcp_rpc_command",
            "mcp_rpc_disconnect",
        }

    def test_the_tools_carry_the_category_stamp(self):
        tools = _tools(_StubService(), "ari")
        for tool in tools.values():
            assert tool._tool_category == "mcp_rpc"

    @pytest.mark.parametrize("agent_id", ["ari", "leader"])
    async def test_the_authorized_agents_may_drive_a_machine(self, agent_id):
        service = _StubService(machines=[{"machine_id": "mach_1", "presence": "online"}])
        tools = _tools(service, agent_id)
        result = await tools["mcp_rpc_list_machine"].ainvoke({})
        assert result["count"] == 1

    @pytest.mark.parametrize("agent_id", ["worker", "tester", "watcher", "maintenancer", ""])
    async def test_the_other_agents_are_refused(self, agent_id):
        service = _StubService()
        tools = _tools(service, agent_id)
        result = await tools["mcp_rpc_list_machine"].ainvoke({})
        assert result["error"]["code"] == CODE_INTERNAL
        assert "not permitted" in result["error"]["message"]
        # The service was never called.
        assert service.calls == []

    async def test_the_authorized_set_is_ari_and_leader(self):
        assert MCP_RPC_AUTHORIZED_AGENTS == frozenset({"ari", "leader"})

    async def test_a_typed_refusal_of_the_gateway_is_surfaced(self):
        from daemon.services.mcps_gateway.protocol import GatewayProtocolError

        service = _StubService(
            error=GatewayProtocolError(
                CODE_MACHINE_NOT_ENROLLED, "not enrolled", {"machine_id": "mach_x"}
            )
        )
        tools = _tools(service, "ari")
        result = await tools["mcp_rpc_connect"].ainvoke({"machine_id": "mach_x"})
        assert result["error"]["code"] == CODE_MACHINE_NOT_ENROLLED
        assert result["error"]["data"]["machine_id"] == "mach_x"

    async def test_every_tool_refuses_while_the_transport_is_off(self):
        """R2: ALL FOUR tools refuse while the transport is off.

        The tool layer must not look healthy while the transport is dead:
        a roster that answers while the gateway is disabled would tell the
        agent that the surface is alive when it is not.
        """
        from daemon.services.mcps_gateway.config import GatewayConfig

        service = McpGatewayService(GatewayConfig(enabled=False))
        tools = _tools(service, "ari")

        calls = [
            ("mcp_rpc_list_machine", {}),
            ("mcp_rpc_connect", {"machine_id": "mach_1"}),
            ("mcp_rpc_command", {"tool": "screenshot"}),
            ("mcp_rpc_disconnect", {}),
        ]
        for name, args in calls:
            result = await tools[name].ainvoke(args)
            assert result["error"]["code"] == CODE_INTERNAL, name
            assert "disabled" in result["error"]["message"], name
            assert result["error"]["data"]["enabled"] is False, name

    async def test_the_command_forwards_the_arguments_untouched(self):
        service = _StubService()
        tools = _tools(service, "ari")
        await tools["mcp_rpc_command"].ainvoke(
            {"tool": "click", "arguments": {"x": 1, "y": 2}}
        )
        name, kwargs = service.calls[-1]
        assert name == "command"
        assert kwargs["tool"] == "click"
        assert kwargs["arguments"] == {"x": 1, "y": 2}
        assert kwargs["agent_id"] == "ari"
        assert kwargs["instance_id"] == "inst-1"
