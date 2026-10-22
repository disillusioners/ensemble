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
    DEFAULT_HEARTBEAT_TIMEOUT_MS,
    DEFAULT_PATH,
    DEFAULT_PING_INTERVAL_MS,
    DEFAULT_SESSION_IDLE_TIMEOUT_MS,
    DEFAULT_SESSION_TTL_MS,
    ENV_PREFIX,
    GatewayConfig,
    gateway_enabled,
    load_gateway_config,
)
from daemon.services.mcps_gateway.protocol import (
    CENTER_EMITTED_CODES,
    CODE_CAPABILITY_MISSING,
    CODE_HEARTBEAT_TIMEOUT,
    CODE_INTERNAL,
    CODE_MACHINE_BUSY,
    CODE_MACHINE_NOT_ENROLLED,
    CODE_PROTOCOL_VERSION_UNSUPPORTED,
    CODE_SESSION_EXPIRED,
    CODE_SESSION_NOT_FOUND,
    CODE_TOOL_TIMEOUT,
    RELAYED_CODES,
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
# The credential comparison (§4.1)
# ─────────────────────────────────────────────────────────────────────────────


class TestTheCredentialComparison:
    """FIX 2 — a garbage credential must be a clean refusal.

    ``hmac.compare_digest`` raises ``TypeError`` when a ``str`` argument
    holds a NON-ASCII character, and an HTTP header value is latin-1
    decoded: before the fix, a credential with any byte above 0x7F
    escaped ``authenticate`` as an UNHANDLED exception instead of the
    clean 401 of §4.1.
    """

    def test_a_non_ascii_candidate_is_refused_and_never_raises(self):
        config = GatewayConfig(tokens=("tok-alpha", "tok-beta"))
        assert config.token_is_valid("tökén") is False
        assert config.token_is_valid("tok-ÿ") is False
        assert config.token_is_valid("令牌") is False

    def test_the_service_refuses_a_non_ascii_bearer_cleanly(self):
        """The upgrade auth answers a garbage credential with a refusal —
        never with an exception."""
        service = McpGatewayService(GatewayConfig(enabled=True, tokens=("tok-alpha",)))
        assert service.authenticate("Bearer t\xf6ken") is False
        assert service.authenticate("Bearer tok-\u00ff") is False
        # The legitimate credential still works.
        assert service.authenticate("Bearer tok-alpha") is True

    def test_every_token_is_compared_even_after_a_match(self, monkeypatch):
        """The anti-timing design: the loop NEVER short-circuits.

        The comparison time must not leak WHICH token was presented, so
        every configured token is compared — the match does not stop the
        loop.
        """
        import hmac

        calls: list[tuple[bytes, bytes]] = []
        real_compare = hmac.compare_digest

        def counting_compare(a, b):
            calls.append((a, b))
            return real_compare(a, b)

        monkeypatch.setattr(hmac, "compare_digest", counting_compare)
        config = GatewayConfig(tokens=("tok-alpha", "tok-beta", "tok-gamma"))
        assert config.token_is_valid("tok-beta") is True
        assert len(calls) == 3, (
            f"only {len(calls)} of the 3 tokens were compared — the loop "
            "short-circuits on the match and leaks WHICH token was used"
        )
        # The comparison is over BYTES (a str comparison raises on a
        # non-ASCII candidate).
        assert all(isinstance(a, bytes) and isinstance(b, bytes) for a, b in calls)


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


# ─────────────────────────────────────────────────────────────────────────────
# T1 — the DEFAULT_* constants are pinned to their contract literals
# ─────────────────────────────────────────────────────────────────────────────


class TestDefaultConstantsArePinned:
    """T1: the knob DEFAULTS are part of the contract.

    Experiment-proven drift: changing ``DEFAULT_HEARTBEAT_INTERVAL_MS``
    from 15000 to 99000 leaves every behavioural test green — nothing
    in the suite pinned the literal. These pins close the code↔doc
    chain: the values below are the ones the ops document and the
    protocol authority state.
    """

    def test_the_heartbeat_defaults(self):
        assert DEFAULT_HEARTBEAT_INTERVAL_MS == 15000
        assert DEFAULT_HEARTBEAT_TIMEOUT_MS == 45000
        assert DEFAULT_CLIENT_DEAD_DETECT_MS == 45000

    def test_the_command_and_session_defaults(self):
        assert DEFAULT_COMMAND_TIMEOUT_MS == 30000
        assert DEFAULT_SESSION_TTL_MS == 3600000
        assert DEFAULT_SESSION_IDLE_TIMEOUT_MS == 300000

    def test_the_ping_default_is_the_reference_cadence(self):
        assert DEFAULT_PING_INTERVAL_MS == 200

    def test_the_route_path_default(self):
        assert DEFAULT_PATH == "/api/mcps-gateway/ws"

    def test_the_enabled_default_is_off(self):
        config = load_gateway_config()
        assert config.enabled is False
        assert gateway_enabled() is False

    def test_the_token_and_machine_defaults_are_empty(self):
        config = load_gateway_config()
        assert config.tokens == ()
        assert config.has_enrollment_tokens is False
        assert config.allowed_machines == ()
        # An empty allowlist means every AUTHENTICATED machine is
        # enrolled.
        assert config.machine_allowed("mach_anything") is True


# ─────────────────────────────────────────────────────────────────────────────
# T2 — the wire strings are pinned
# ─────────────────────────────────────────────────────────────────────────────


class TestWireCodesArePinned:
    """T2: the typed error codes are WIRE STRINGS.

    A drift in one of them is invisible to the default pytest run (the
    conformance suite that would catch it is behind the ``integration``
    marker). These tripwires make the drift visible in the DEFAULT run.
    """

    def test_the_center_emitted_codes(self):
        assert CODE_PROTOCOL_VERSION_UNSUPPORTED == "PROTOCOL_VERSION_UNSUPPORTED"
        assert CODE_CAPABILITY_MISSING == "CAPABILITY_MISSING"
        assert CODE_MACHINE_NOT_ENROLLED == "MACHINE_NOT_ENROLLED"
        assert CODE_MACHINE_BUSY == "MACHINE_BUSY"
        assert CODE_SESSION_NOT_FOUND == "SESSION_NOT_FOUND"
        assert CODE_SESSION_EXPIRED == "SESSION_EXPIRED"
        assert CODE_HEARTBEAT_TIMEOUT == "HEARTBEAT_TIMEOUT"
        assert CODE_TOOL_TIMEOUT == "TOOL_TIMEOUT"
        assert CODE_INTERNAL == "INTERNAL"

    def test_the_relayed_codes(self):
        assert "TOOL_NOT_FOUND" in RELAYED_CODES
        assert "TOOL_ERROR" in RELAYED_CODES
        assert "PAYLOAD_TOO_LARGE" in RELAYED_CODES
        assert "RATE_LIMITED" in RELAYED_CODES

    def test_the_reserved_code_is_never_emitted(self):
        """``TOOL_DENIED`` is reserved for v0.2+ (§6.5)."""
        assert "TOOL_DENIED" not in CENTER_EMITTED_CODES
        assert "TOOL_DENIED" not in RELAYED_CODES

    def test_the_only_overlap_of_the_two_code_sets_is_the_timeout(self):
        """``TOOL_TIMEOUT`` has TWO origins: the machine's own deadline
        (relayed verbatim) and the CENTER's deadline on the round-trip
        (the center must bound its own wait). Every other code has
        exactly one origin."""
        overlap = CENTER_EMITTED_CODES & RELAYED_CODES
        assert overlap == {"TOOL_TIMEOUT"}, overlap
        # The codes that are relayed are never invented by the center.
        assert not (RELAYED_CODES - {"TOOL_TIMEOUT"} & CENTER_EMITTED_CODES)
