"""Tests for OpenSpace built-in MCP server definition.

This module tests the OpenSpaceServerDefinition class including:
- Property values (name, display_name, description, schema_version)
- Base config (STDIO default)
- Config schema (3 env fields with openspace_ prefix)
- Config building (build_config) with dual-transport support:
  * STDIO mode (default when ENS_OPENSPACE_REMOTE_URL is unset)
  * HTTP mode (when ENS_OPENSPACE_REMOTE_URL is set)
  * Credential injection for OPENSPACE_LLM_API_KEY and OPENSPACE_API_KEY
  * OPENSPACE_MCP_TRANSPORT pinning
- tool_call_timeout override (900s for long-running agent tasks)
- Env disable via MCP_DISABLE_BUILT_IN_OPENSPACE
- Registry integration under key "openspace"
- Warmup pool transport regression guard
"""

import os

import pytest

from daemon.mcp.builtin_servers.openspace import OpenSpaceServerDefinition
from daemon.mcp.builtin_servers import (
    BuiltinServerRegistry,
    get_registry,
    is_builtin_disabled,
)


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture(autouse=True)
def reset_registry():
    """Reset MCP server registry to its import-time state before each test.

    The BuiltinServerRegistry is a module-level singleton. Without this
    fixture, mutations from other test files (e.g., unregister calls) would
    leak into these tests.
    """
    from daemon.mcp.builtin_servers import _registry
    from daemon.mcp.builtin_servers.webfetch import WebFetchServerDefinition
    from daemon.mcp.builtin_servers.context7 import Context7ServerDefinition

    _registry._definitions.clear()
    _registry.register(WebFetchServerDefinition())
    _registry.register(Context7ServerDefinition())
    _registry.register(OpenSpaceServerDefinition())
    yield


@pytest.fixture(autouse=True)
def clean_openspace_env(monkeypatch):
    """Ensure OpenSpace-related env vars don't leak across tests.

    Removes ENS_OPENSPACE_REMOTE_URL, OPENSPACE_LLM_API_KEY,
    OPENSPACE_API_KEY, and MCP_DISABLE_BUILT_IN_OPENSPACE before
    every test. Tests that need to set them should do so explicitly
    via ``monkeypatch.setenv`` (preferred) or a temporary patch.dict.
    """
    for var in (
        "ENS_OPENSPACE_REMOTE_URL",
        "OPENSPACE_LLM_API_KEY",
        "OPENSPACE_API_KEY",
        "MCP_DISABLE_BUILT_IN_OPENSPACE",
    ):
        monkeypatch.delenv(var, raising=False)
    yield


@pytest.fixture
def openspace_definition():
    """Create an OpenSpaceServerDefinition instance."""
    return OpenSpaceServerDefinition()


@pytest.fixture
def registry():
    """Get the global registry."""
    return get_registry()


# =============================================================================
# Test Properties
# =============================================================================


class TestOpenSpaceProperties:
    """Tests for OpenSpaceServerDefinition property values."""

    def test_name(self, openspace_definition):
        """Test that name is 'openspace'."""
        assert openspace_definition.name == "openspace"

    def test_display_name(self, openspace_definition):
        """Test that display_name is 'OpenSpace'."""
        assert openspace_definition.display_name == "OpenSpace"

    def test_description_mentions_key_tools(self, openspace_definition):
        """Test that description mentions key OpenSpace capabilities."""
        desc = openspace_definition.description
        assert "execute_task" in desc
        assert "search_skills" in desc
        assert "skill_evolution" in desc

    def test_schema_version(self, openspace_definition):
        """Test that schema_version is '1'."""
        assert openspace_definition.schema_version == "1"

    def test_tool_call_timeout(self, openspace_definition):
        """Test that tool_call_timeout is 900s for long-running agent tasks.

        OpenSpace execute_task can take up to 15 minutes — much longer
        than the default pool timeout.
        """
        assert openspace_definition.tool_call_timeout == 900


# =============================================================================
# Test Base Config
# =============================================================================


class TestOpenSpaceBaseConfig:
    """Tests for get_base_config()."""

    def test_get_base_config_returns_stdio(self, openspace_definition):
        """Test that base config always returns STDIO transport.

        The base config is environment-independent — HTTP override only
        happens inside build_config() when ENS_OPENSPACE_REMOTE_URL is
        set. get_base_config() should always return the local STDIO form.
        """
        base = openspace_definition.get_base_config()
        assert base["transport"] == "stdio"
        assert base["command"] == "python3"
        assert base["args"] == ["-m", "openspace.mcp_server"]

    def test_get_base_config_independent_of_remote_env(self, openspace_definition):
        """Test that get_base_config ignores ENS_OPENSPACE_REMOTE_URL.

        Even if the env var is set, the base config remains STDIO. The
        HTTP switch happens only in build_config().
        """
        os.environ["ENS_OPENSPACE_REMOTE_URL"] = "https://openspace.example.com/mcp"
        try:
            base = openspace_definition.get_base_config()
            assert base["transport"] == "stdio"
            assert "url" not in base
        finally:
            os.environ.pop("ENS_OPENSPACE_REMOTE_URL", None)


# =============================================================================
# Test Config Schema
# =============================================================================


class TestOpenSpaceConfigSchema:
    """Tests for get_config_schema()."""

    def test_schema_returns_three_fields(self, openspace_definition):
        """Test that schema returns exactly 3 env fields."""
        schema = openspace_definition.get_config_schema()
        assert len(schema) == 3

    def test_schema_keys_use_openspace_prefix(self, openspace_definition):
        """Test that schema keys use the 'openspace_' prefix.

        The base class uppercases these to OPENSPACE_* env vars.
        """
        schema = openspace_definition.get_config_schema()
        keys = {f["key"] for f in schema}
        assert keys == {"openspace_model", "openspace_max_iterations", "openspace_backend_scope"}

    def test_schema_fields_use_env_section(self, openspace_definition):
        """Test that all schema fields target the env section."""
        schema = openspace_definition.get_config_schema()
        for field in schema:
            assert field["section"] == "env"
            assert field["arg_format"] == "key_value"

    def test_schema_fields_have_empty_defaults(self, openspace_definition):
        """Test that schema field defaults are empty strings.

        Empty defaults mean build_config() skips them when not provided
        by user_values — letting OpenSpace use its own internal defaults.
        """
        schema = openspace_definition.get_config_schema()
        for field in schema:
            assert field["default"] == ""
            assert field["required"] is False


# =============================================================================
# Test build_config — STDIO mode
# =============================================================================


class TestOpenSpaceBuildConfigStdio:
    """Tests for build_config() in STDIO mode (default)."""

    def test_stdio_default_returns_stdio_transport(self, openspace_definition):
        """ENS_OPENSPACE_REMOTE_URL not set → STDIO transport.

        Verifies that the warmup pool will register this server
        (warmup only handles stdio).
        """
        config = openspace_definition.build_config({})
        assert config["transport"] == "stdio"

    def test_stdio_default_uses_python_module(self, openspace_definition):
        """STDIO mode launches the local python module."""
        config = openspace_definition.build_config({})
        assert config["command"] == "python3"
        assert config["args"] == ["-m", "openspace.mcp_server"]

    def test_stdio_default_pins_openspace_mcp_transport(self, openspace_definition):
        """STDIO mode injects OPENSPACE_MCP_TRANSPORT=stdio.

        This prevents OpenSpace's subprocess TTY auto-detect from
        picking SSE and breaking the stdio transport.
        """
        config = openspace_definition.build_config({})
        assert config["env"]["OPENSPACE_MCP_TRANSPORT"] == "stdio"

    def test_stdio_default_no_url_field(self, openspace_definition):
        """STDIO mode must not include a 'url' field."""
        config = openspace_definition.build_config({})
        assert "url" not in config
        assert "headers" not in config

    def test_stdio_with_env_field_present(self, openspace_definition):
        """Schema field value is uppercased into config['env']."""
        config = openspace_definition.build_config({"openspace_model": "gpt-4o"})
        assert config["env"]["OPENSPACE_MODEL"] == "gpt-4o"

    def test_stdio_with_empty_user_values_skips_schema_fields(
        self, openspace_definition
    ):
        """Empty user_values → no schema fields injected (defaults are empty)."""
        config = openspace_definition.build_config({})
        env = config.get("env", {})
        # Only OPENSPACE_MCP_TRANSPORT should be present
        assert "OPENSPACE_MODEL" not in env
        assert "OPENSPACE_MAX_ITERATIONS" not in env
        assert "OPENSPACE_BACKEND_SCOPE" not in env

    def test_stdio_includes_all_schema_fields_when_provided(
        self, openspace_definition
    ):
        """All schema fields uppercased and added to env when supplied.

        Note: the base class coerces all env values to str() — even numeric
        ones — because env vars are stringly-typed when passed to subprocesses.
        """
        config = openspace_definition.build_config({
            "openspace_model": "claude-3-5-sonnet",
            "openspace_max_iterations": 25,
            "openspace_backend_scope": "cloud,local",
        })
        assert config["env"]["OPENSPACE_MODEL"] == "claude-3-5-sonnet"
        assert config["env"]["OPENSPACE_MAX_ITERATIONS"] == "25"
        assert config["env"]["OPENSPACE_BACKEND_SCOPE"] == "cloud,local"

    def test_stdio_user_value_overrides_empty_string_default(
        self, openspace_definition
    ):
        """Non-empty user value should be included even though default is ''."""
        config = openspace_definition.build_config({"openspace_model": "gpt-4o"})
        assert "OPENSPACE_MODEL" in config["env"]

    def test_stdio_empty_string_user_value_is_skipped(self, openspace_definition):
        """Empty string user values are skipped by the base class."""
        config = openspace_definition.build_config({"openspace_model": ""})
        assert "OPENSPACE_MODEL" not in config["env"]


# =============================================================================
# Test build_config — HTTP mode
# =============================================================================


class TestOpenSpaceBuildConfigHttp:
    """Tests for build_config() in HTTP mode (remote URL set)."""

    def test_http_mode_when_remote_url_set(
        self, openspace_definition, monkeypatch
    ):
        """ENS_OPENSPACE_REMOTE_URL set → streamable-http transport."""
        monkeypatch.setenv("ENS_OPENSPACE_REMOTE_URL", "https://openspace.example.com/mcp")

        config = openspace_definition.build_config({})
        assert config["transport"] == "streamable-http"

    def test_http_mode_includes_url_field(
        self, openspace_definition, monkeypatch
    ):
        """HTTP mode stores the URL in config['url']."""
        url = "https://openspace.example.com/mcp"
        monkeypatch.setenv("ENS_OPENSPACE_REMOTE_URL", url)

        config = openspace_definition.build_config({})
        assert config["url"] == url

    def test_http_mode_has_empty_headers(
        self, openspace_definition, monkeypatch
    ):
        """HTTP mode returns headers={} for caller to populate."""
        monkeypatch.setenv("ENS_OPENSPACE_REMOTE_URL", "https://openspace.example.com/mcp")

        config = openspace_definition.build_config({})
        assert config["headers"] == {}

    def test_http_mode_does_not_have_stdio_keys(
        self, openspace_definition, monkeypatch
    ):
        """HTTP mode must not have command/args/env keys."""
        monkeypatch.setenv("ENS_OPENSPACE_REMOTE_URL", "https://openspace.example.com/mcp")

        config = openspace_definition.build_config({})
        assert "command" not in config
        assert "args" not in config
        assert "env" not in config

    def test_http_mode_strips_whitespace_from_url(
        self, openspace_definition, monkeypatch
    ):
        """Leading/trailing whitespace in the URL is stripped."""
        monkeypatch.setenv("ENS_OPENSPACE_REMOTE_URL", "   https://openspace.example.com/mcp   ")

        config = openspace_definition.build_config({})
        assert config["url"] == "https://openspace.example.com/mcp"

    def test_http_mode_ignores_user_values(self, openspace_definition, monkeypatch):
        """HTTP mode does NOT apply schema-derived env vars.

        In HTTP mode there is no local subprocess so OPENSPACE_MODEL
        etc. have no effect through config injection — they're not
        included in the returned dict.
        """
        monkeypatch.setenv("ENS_OPENSPACE_REMOTE_URL", "https://openspace.example.com/mcp")

        config = openspace_definition.build_config({
            "openspace_model": "gpt-4o",
            "openspace_max_iterations": 25,
        })
        # HTTP config doesn't include schema-derived env at all
        assert "OPENSPACE_MODEL" not in config.get("env", {})
        assert "OPENSPACE_MAX_ITERATIONS" not in config.get("env", {})

    def test_empty_remote_url_falls_back_to_stdio(self, openspace_definition):
        """ENS_OPENSPACE_REMOTE_URL='' → STDIO (whitespace-only treated same)."""
        os.environ["ENS_OPENSPACE_REMOTE_URL"] = ""
        try:
            config = openspace_definition.build_config({})
            assert config["transport"] == "stdio"
        finally:
            os.environ.pop("ENS_OPENSPACE_REMOTE_URL", None)

    def test_whitespace_only_remote_url_falls_back_to_stdio(
        self, openspace_definition
    ):
        """ENS_OPENSPACE_REMOTE_URL='   ' → STDIO (after strip)."""
        os.environ["ENS_OPENSPACE_REMOTE_URL"] = "   "
        try:
            config = openspace_definition.build_config({})
            assert config["transport"] == "stdio"
        finally:
            os.environ.pop("ENS_OPENSPACE_REMOTE_URL", None)


# =============================================================================
# Test Credential Injection
# =============================================================================


class TestOpenSpaceCredentialInjection:
    """Tests for OPENSPACE_LLM_API_KEY / OPENSPACE_API_KEY injection.

    The MCP stdio_client uses get_default_environment() which only forwards
    6 POSIX vars. OpenSpaceServerDefinition.build_config() must explicitly
    inject credentials so the subprocess can authenticate.
    """

    def test_llm_api_key_present_in_config_env(
        self, openspace_definition, monkeypatch
    ):
        """OPENSPACE_LLM_API_KEY in os.environ → present in config['env']."""
        monkeypatch.setenv("OPENSPACE_LLM_API_KEY", "sk-llm-secret-123")

        config = openspace_definition.build_config({})
        assert config["env"]["OPENSPACE_LLM_API_KEY"] == "sk-llm-secret-123"

    def test_llm_api_key_absent_not_injected(
        self, openspace_definition
    ):
        """OPENSPACE_LLM_API_KEY NOT set → key must not appear (no empty string)."""
        # Make sure it's not set
        assert "OPENSPACE_LLM_API_KEY" not in os.environ

        config = openspace_definition.build_config({})
        assert "OPENSPACE_LLM_API_KEY" not in config["env"]

    def test_api_key_present_in_config_env(
        self, openspace_definition, monkeypatch
    ):
        """OPENSPACE_API_KEY in os.environ → present in config['env']."""
        monkeypatch.setenv("OPENSPACE_API_KEY", "sk-api-secret-456")

        config = openspace_definition.build_config({})
        assert config["env"]["OPENSPACE_API_KEY"] == "sk-api-secret-456"

    def test_api_key_absent_not_injected(
        self, openspace_definition
    ):
        """OPENSPACE_API_KEY NOT set → key must not appear."""
        assert "OPENSPACE_API_KEY" not in os.environ

        config = openspace_definition.build_config({})
        assert "OPENSPACE_API_KEY" not in config["env"]

    def test_both_keys_present_simultaneously(
        self, openspace_definition, monkeypatch
    ):
        """Both keys present → both injected."""
        monkeypatch.setenv("OPENSPACE_LLM_API_KEY", "sk-llm")
        monkeypatch.setenv("OPENSPACE_API_KEY", "sk-api")

        config = openspace_definition.build_config({})
        assert config["env"]["OPENSPACE_LLM_API_KEY"] == "sk-llm"
        assert config["env"]["OPENSPACE_API_KEY"] == "sk-api"

    def test_empty_string_credentials_skipped(
        self, openspace_definition, monkeypatch
    ):
        """OPENSPACE_LLM_API_KEY='' → not injected (no empty string values)."""
        monkeypatch.setenv("OPENSPACE_LLM_API_KEY", "")

        config = openspace_definition.build_config({})
        assert "OPENSPACE_LLM_API_KEY" not in config["env"]

    def test_whitespace_only_credentials_skipped(
        self, openspace_definition, monkeypatch
    ):
        """OPENSPACE_LLM_API_KEY='   ' → not injected (strip+empty)."""
        monkeypatch.setenv("OPENSPACE_LLM_API_KEY", "   ")

        config = openspace_definition.build_config({})
        assert "OPENSPACE_LLM_API_KEY" not in config["env"]

    def test_credentials_not_injected_in_http_mode(
        self, openspace_definition, monkeypatch
    ):
        """In HTTP mode, the config dict has no 'env' field at all.

        HTTP mode returns immediately after deciding transport — it
        does NOT run the STDIO env-injection block. Credentials are
        irrelevant for the HTTP config returned by build_config.
        """
        monkeypatch.setenv("ENS_OPENSPACE_REMOTE_URL", "https://openspace.example.com/mcp")
        monkeypatch.setenv("OPENSPACE_LLM_API_KEY", "sk-llm-secret")
        monkeypatch.setenv("OPENSPACE_API_KEY", "sk-api-secret")

        config = openspace_definition.build_config({})
        assert config["transport"] == "streamable-http"
        assert "env" not in config


# =============================================================================
# Test Warmup Pool Transport Regression Guard
# =============================================================================


class TestOpenSpaceWarmupPoolRegression:
    """Regression guard for warmup pool transport dispatch.

    The warmup pool only registers servers with transport='stdio'.
    This test ensures build_config() returns the correct transport
    value so the warmup pool logic works as expected.
    """

    def test_stdio_mode_transport_is_stdio_for_warmup(
        self, openspace_definition
    ):
        """STDIO mode → transport=='stdio' → warmup pool registers it."""
        config = openspace_definition.build_config({})
        assert config["transport"] == "stdio", (
            "Warmup pool only registers stdio transports; "
            "build_config() must return transport='stdio' when "
            "ENS_OPENSPACE_REMOTE_URL is unset"
        )

    def test_http_mode_transport_is_not_stdio_for_warmup_skip(
        self, openspace_definition, monkeypatch
    ):
        """HTTP mode → transport!='stdio' → warmup pool skips it."""
        monkeypatch.setenv("ENS_OPENSPACE_REMOTE_URL", "https://openspace.example.com/mcp")

        config = openspace_definition.build_config({})
        assert config["transport"] != "stdio", (
            "Warmup pool only handles stdio; HTTP transports must "
            "return transport != 'stdio' so the warmup pool skips them"
        )


# =============================================================================
# Test Env Disable
# =============================================================================


class TestOpenSpaceEnvDisable:
    """Tests for MCP_DISABLE_BUILT_IN_OPENSPACE disable mechanism."""

    def test_disable_returns_true_when_set_true(self, monkeypatch):
        """MCP_DISABLE_BUILT_IN_OPENSPACE=true → is_builtin_disabled returns True."""
        monkeypatch.setenv("MCP_DISABLE_BUILT_IN_OPENSPACE", "true")
        assert is_builtin_disabled("openspace") is True

    def test_disable_returns_true_case_insensitive(self, monkeypatch):
        """is_builtin_disabled is case-insensitive on 'true'."""
        monkeypatch.setenv("MCP_DISABLE_BUILT_IN_OPENSPACE", "True")
        assert is_builtin_disabled("openspace") is True

    def test_disable_returns_false_when_unset(self):
        """No env var → is_builtin_disabled returns False."""
        assert "MCP_DISABLE_BUILT_IN_OPENSPACE" not in os.environ
        assert is_builtin_disabled("openspace") is False

    def test_disable_returns_false_when_set_to_other_value(self, monkeypatch):
        """Env var set to non-'true' → False."""
        monkeypatch.setenv("MCP_DISABLE_BUILT_IN_OPENSPACE", "false")
        assert is_builtin_disabled("openspace") is False

        monkeypatch.setenv("MCP_DISABLE_BUILT_IN_OPENSPACE", "1")
        assert is_builtin_disabled("openspace") is False

        monkeypatch.setenv("MCP_DISABLE_BUILT_IN_OPENSPACE", "yes")
        assert is_builtin_disabled("openspace") is False

    def test_disable_returns_false_for_empty_string(self, monkeypatch):
        """MCP_DISABLE_BUILT_IN_OPENSPACE='' → False (after strip)."""
        monkeypatch.setenv("MCP_DISABLE_BUILT_IN_OPENSPACE", "")
        assert is_builtin_disabled("openspace") is False

    def test_disable_returns_false_for_whitespace(self, monkeypatch):
        """MCP_DISABLE_BUILT_IN_OPENSPACE='   ' → False."""
        monkeypatch.setenv("MCP_DISABLE_BUILT_IN_OPENSPACE", "   ")
        assert is_builtin_disabled("openspace") is False

    def test_disable_does_not_affect_other_servers(self, monkeypatch):
        """Disabling openspace must not affect webfetch/context7."""
        monkeypatch.setenv("MCP_DISABLE_BUILT_IN_OPENSPACE", "true")
        assert is_builtin_disabled("openspace") is True
        assert is_builtin_disabled("webfetch") is False
        assert is_builtin_disabled("context7") is False


# =============================================================================
# Test Registry Integration
# =============================================================================


class TestOpenSpaceRegistryIntegration:
    """Tests for OpenSpace registration in global registry."""

    def test_openspace_registered_in_registry(self, registry):
        """Test that OpenSpace is registered in the global registry under 'openspace'."""
        openspace = registry.get_by_name("openspace")
        assert openspace is not None
        assert isinstance(openspace, OpenSpaceServerDefinition)

    def test_registry_contains_openspace_key(self, registry):
        """Test that 'openspace' is a key in registry.definitions."""
        assert "openspace" in registry.definitions

    def test_registry_get_all_includes_openspace(self, registry):
        """Test that get_all() includes OpenSpace."""
        all_defs = registry.get_all()
        names = [d.name for d in all_defs]
        assert "openspace" in names

    def test_registry_has_all_three_servers(self, registry):
        """Test that registry has all three builtin servers (webfetch, context7, openspace)."""
        names = {d.name for d in registry.get_all()}
        assert names == {"webfetch", "context7", "openspace"}


# =============================================================================
# Test parse_config Round-Trip
# =============================================================================


class TestOpenSpaceParseConfig:
    """Tests for parse_config() round-trip with build_config()."""

    def test_parse_config_env_field_roundtrip(self, openspace_definition):
        """build_config → parse_config recovers env field values."""
        user_values = {
            "openspace_model": "gpt-4o",
            "openspace_max_iterations": 30,
            "openspace_backend_scope": "cloud",
        }
        built = openspace_definition.build_config(user_values)
        parsed = openspace_definition.parse_config(built)

        assert parsed["openspace_model"] == "gpt-4o"
        assert parsed["openspace_max_iterations"] == 30
        assert parsed["openspace_backend_scope"] == "cloud"

    def test_parse_config_skips_base_args(self, openspace_definition):
        """parse_config correctly skips base args (-m, openspace.mcp_server)."""
        built = openspace_definition.build_config({"openspace_model": "gpt-4o"})
        args = built.get("args", [])
        # Base args should be at the start
        assert args[0] == "-m"
        assert args[1] == "openspace.mcp_server"

        # Should parse user value, not confused by base args
        parsed = openspace_definition.parse_config(built)
        assert parsed["openspace_model"] == "gpt-4o"

    def test_parse_config_default_empty(self, openspace_definition):
        """parse_config with no schema values provided → no schema keys in result."""
        built = openspace_definition.build_config({})
        parsed = openspace_definition.parse_config(built)

        # Schema fields with empty defaults are NOT injected, so not parsed back
        assert "openspace_model" not in parsed
        assert "openspace_max_iterations" not in parsed
        assert "openspace_backend_scope" not in parsed


# =============================================================================
# Test End-to-End Scenarios
# =============================================================================


class TestOpenSpaceEndToEnd:
    """End-to-end scenarios combining build_config + parse_config."""

    def test_full_stdio_workflow_with_credentials(
        self, openspace_definition, monkeypatch
    ):
        """Full STDIO workflow: schema values + credentials → build → parse."""
        monkeypatch.setenv("OPENSPACE_LLM_API_KEY", "sk-llm-full")
        monkeypatch.setenv("OPENSPACE_API_KEY", "sk-api-full")

        config = openspace_definition.build_config({
            "openspace_model": "gpt-4o",
            "openspace_max_iterations": 10,
        })

        # STDIO base
        assert config["transport"] == "stdio"
        assert config["command"] == "python3"
        assert config["args"] == ["-m", "openspace.mcp_server"]

        # Schema env (numeric values are str()ed by the base class — env vars
        # are stringly-typed when passed to subprocesses)
        assert config["env"]["OPENSPACE_MODEL"] == "gpt-4o"
        assert config["env"]["OPENSPACE_MAX_ITERATIONS"] == "10"

        # OpenSpace-specific env
        assert config["env"]["OPENSPACE_MCP_TRANSPORT"] == "stdio"

        # Credentials
        assert config["env"]["OPENSPACE_LLM_API_KEY"] == "sk-llm-full"
        assert config["env"]["OPENSPACE_API_KEY"] == "sk-api-full"

    def test_full_http_workflow_overrides_stdio(
        self, openspace_definition, monkeypatch
    ):
        """Full HTTP workflow: remote URL → HTTP config, credentials ignored."""
        monkeypatch.setenv("ENS_OPENSPACE_REMOTE_URL", "https://openspace.example.com/mcp")
        # Even if these are set, HTTP mode does NOT inject them into env
        monkeypatch.setenv("OPENSPACE_LLM_API_KEY", "sk-llm-ignored")
        monkeypatch.setenv("OPENSPACE_API_KEY", "sk-api-ignored")

        config = openspace_definition.build_config({
            "openspace_model": "gpt-4o",
        })

        assert config["transport"] == "streamable-http"
        assert config["url"] == "https://openspace.example.com/mcp"
        assert config["headers"] == {}
        # No STDIO keys
        assert "command" not in config
        assert "args" not in config
        # No env block in HTTP mode (build_config returns early)
        assert "env" not in config

    def test_registry_lookup_returns_working_definition(self, registry):
        """Test that registry.get_by_name('openspace') returns a working instance."""
        openspace = registry.get_by_name("openspace")

        # Should produce a valid STDIO config
        config = openspace.build_config({})
        assert config["transport"] == "stdio"

        # Should produce a valid HTTP config when env is set
        with pytest.MonkeyPatch.context() as mp:
            mp.setenv("ENS_OPENSPACE_REMOTE_URL", "https://openspace.example.com/mcp")
            config = openspace.build_config({})
            assert config["transport"] == "streamable-http"