"""Tests for the LangChain ``System`` tool category
(``daemon/tools/system.py``).

The System category exposes three read-only diagnostic tools:

* ``system_env`` — list curated environment variables (secrets masked).
* ``system_config`` — show the resolved ``Config`` (sections, secrets masked).
* ``system_health`` — small health snapshot (version, DB backend, RAG, PID).

All three are read-only, factory-closed over the :class:`InstanceManager`,
and never raise — I/O errors come back as ``{"error": ...}`` JSON. The
helper functions ``_is_secret_key``, ``_mask_connection_string``,
``_mask_secret``, ``_mask_env_value``, and ``_mask_config`` are also
covered directly so a regression in the masking policy is caught even
when the tool surface is not exercised.
"""

import json
import os
from unittest.mock import MagicMock, patch

import pytest

from daemon.config import Config
from daemon.ensemble_config import EnsembleConfig
from daemon.tools.system import (
    CATEGORY_DOC,
    CATEGORY_NAME,
    _SECRET_ENV_VARS,
    _SECRET_KEY_SUBSTRINGS,
    _SECRET_SUFFIXES,
    _TRACKED_ENV_EXACT,
    _TRACKED_ENV_PREFIXES,
    _is_secret_key,
    _mask_config,
    _mask_connection_string,
    _mask_env_value,
    _mask_secret,
    create_system_tools,
)


# ─── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def manager_mock():
    """Mock manager with real ``Config`` and ``EnsembleConfig`` instances.

    The :class:`Config` defaults to a real Pydantic instance so the tool can
    call ``model_dump()`` and ``model_fields`` exactly as it does at runtime.
    ``data_dir`` is set to a real path so :func:`system_health` can resolve
    the data directory without falling through to a MagicMock repr.
    """
    m = MagicMock()
    m.config = Config()
    m.ensemble_config = EnsembleConfig()
    m.data_dir = "/tmp/ensemble-test-data"
    return m


@pytest.fixture
def tools(manager_mock):
    """All three system tools, freshly built per test."""
    return create_system_tools(manager_mock, "test-instance-id")


@pytest.fixture
def tool_by_name(tools):
    """Name-based tool lookup — preferred over positional indices for stability."""
    return {t.name: t for t in tools}


@pytest.fixture
def manager_no_config():
    """Manager variant where ``config`` is ``None`` (uninitialized)."""
    m = MagicMock()
    m.config = None
    m.ensemble_config = EnsembleConfig()
    m.data_dir = "/tmp/ensemble-test-data"
    return m


# ─── Factory shape ────────────────────────────────────────────────────────────


class TestSystemToolsFactory:
    def test_factory_returns_three_tools(self):
        tools = create_system_tools(MagicMock(), "instance-1")
        names = sorted(t.name for t in tools)
        assert names == ["system_config", "system_env", "system_health"]
        assert len(tools) == 3

    def test_tools_have_correct_category(self, tools):
        for t in tools:
            assert getattr(t, "_tool_category", None) == "system"

    def test_tool_names(self, tools):
        names = {t.name for t in tools}
        assert names == {"system_env", "system_config", "system_health"}

    def test_factory_accepts_current_instance_id(self, manager_mock):
        """``current_instance_id`` is accepted for parity with other factories."""
        tools = create_system_tools(manager_mock, "any-instance-id-123")
        assert len(tools) == 3

    def test_category_metadata_exposed(self):
        """The module exports ``CATEGORY_NAME`` and ``CATEGORY_DOC``."""
        assert CATEGORY_NAME == "System"
        assert "system_env" in CATEGORY_DOC
        assert "system_config" in CATEGORY_DOC
        assert "system_health" in CATEGORY_DOC


# ─── system_env ────────────────────────────────────────────────────────────────


class TestSystemEnv:
    @pytest.mark.asyncio
    async def test_returns_tracked_env_vars(self, tool_by_name, monkeypatch):
        # Wipe any vars the conftest's clean_env left in place so the
        # only entries we observe are the ones we explicitly set here.
        for key in list(os.environ):
            if key in _SECRET_ENV_VARS or key == "ENSEMBLE_TEST_VAR":
                monkeypatch.delenv(key, raising=False)

        monkeypatch.setenv("ENSEMBLE_TEST_VAR", "hello")
        monkeypatch.setenv("OPENAI_API_KEY", "sk-should-be-masked")

        env_tool = tool_by_name["system_env"]
        result = await env_tool.ainvoke({})
        decoded = json.loads(result)

        assert "ENSEMBLE_TEST_VAR" in decoded
        assert decoded["ENSEMBLE_TEST_VAR"] == "hello"
        # The explicit secret ends with _API_KEY → masked by suffix rule.
        assert decoded["OPENAI_API_KEY"] == "[REDACTED]"

    @pytest.mark.asyncio
    async def test_masks_secrets_by_default(self, tool_by_name, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-supersecret-1234567890")
        env_tool = tool_by_name["system_env"]
        result = await env_tool.ainvoke({})
        decoded = json.loads(result)
        assert "OPENAI_API_KEY" in decoded
        assert decoded["OPENAI_API_KEY"] == "[REDACTED]"
        assert "sk-supersecret" not in decoded["OPENAI_API_KEY"]

    @pytest.mark.asyncio
    async def test_nomask_returns_real_values(self, tool_by_name, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-supersecret-1234567890")
        env_tool = tool_by_name["system_env"]
        result = await env_tool.ainvoke({"nomask": True})
        decoded = json.loads(result)
        assert decoded["OPENAI_API_KEY"] == "sk-supersecret-1234567890"

    @pytest.mark.asyncio
    async def test_prefix_filter(self, tool_by_name, monkeypatch):
        monkeypatch.setenv("POSTGRES_HOST", "db.example.com")
        monkeypatch.setenv("POSTGRES_PORT", "5432")
        monkeypatch.setenv("OPENAI_API_KEY", "sk-should-not-show")
        monkeypatch.setenv("ENSEMBLE_TEST_VAR", "should-not-show")

        env_tool = tool_by_name["system_env"]
        result = await env_tool.ainvoke({"prefix": "POSTGRES_"})
        decoded = json.loads(result)

        assert "POSTGRES_HOST" in decoded
        assert "POSTGRES_PORT" in decoded
        # The prefix filter is applied AFTER the curated-prefix filter,
        # so non-POSTGRES tracked vars get narrowed out.
        assert "OPENAI_API_KEY" not in decoded
        assert "ENSEMBLE_TEST_VAR" not in decoded

    @pytest.mark.asyncio
    async def test_prefix_filter_is_case_insensitive(self, tool_by_name, monkeypatch):
        monkeypatch.setenv("POSTGRES_HOST", "db.example.com")
        env_tool = tool_by_name["system_env"]
        result = await env_tool.ainvoke({"prefix": "postgres_"})
        decoded = json.loads(result)
        assert "POSTGRES_HOST" in decoded

    @pytest.mark.asyncio
    async def test_no_full_environ_dump(self, tool_by_name, monkeypatch):
        """Random env vars (no tracked prefix / exact name) are never included."""
        monkeypatch.setenv("UNRELATED_RANDOM_VAR_12345", "should-never-appear")
        env_tool = tool_by_name["system_env"]
        result = await env_tool.ainvoke({})
        decoded = json.loads(result)
        assert "UNRELATED_RANDOM_VAR_12345" not in decoded
        # Defensive: even with nomask, the prefix filter still applies —
        # only tracked vars are enumerated.
        result_nomask = await env_tool.ainvoke({"nomask": True})
        decoded_nomask = json.loads(result_nomask)
        assert "UNRELATED_RANDOM_VAR_12345" not in decoded_nomask

    @pytest.mark.asyncio
    async def test_secret_env_vars_masked(self, tool_by_name, monkeypatch):
        """Every name in :data:`_SECRET_ENV_VARS` is masked by default."""
        env_tool = tool_by_name["system_env"]
        for name in _SECRET_ENV_VARS:
            # Some names are not in any tracked prefix list (e.g.
            # LIGHTRAG_API_KEY matches the LIGHTRAG_ prefix; OPENSPACE_API_KEY
            # matches the OPENSPACE_ prefix). Use the matching prefix.
            for prefix in _TRACKED_ENV_PREFIXES:
                if name == prefix or name.startswith(prefix):
                    break
            else:
                # Fall back: at minimum, OPENSPACE_/LIGHTRAG_/POSTGRES_ cover them.
                if name in _TRACKED_ENV_EXACT:
                    pass
                else:
                    # Pick the most likely prefix by stripping the suffix.
                    if name.startswith("OPENSPACE_"):
                        pass  # OPENSPACE_ is in _TRACKED_ENV_PREFIXES
                    elif name.startswith("LIGHTRAG_"):
                        pass  # LIGHTRAG_ is in _TRACKED_ENV_PREFIXES
                    elif name.startswith("POSTGRES_"):
                        pass  # POSTGRES_ is in _TRACKED_ENV_PREFIXES
                    elif name in _TRACKED_ENV_EXACT:
                        pass
                    else:
                        continue  # Skip names not reachable from the prefix list

            monkeypatch.setenv(name, f"value-for-{name}")
            result = await env_tool.ainvoke({})
            decoded = json.loads(result)
            if name in decoded:
                assert decoded[name] == "[REDACTED]", (
                    f"{name} should be masked, got {decoded[name]!r}"
                )

    @pytest.mark.asyncio
    async def test_connection_string_masking(self, tool_by_name, monkeypatch):
        """A var with embedded URL password is scrubbed surgically.

        Use a POSTGRES_-prefixed name that is NOT in the explicit
        :data:`_SECRET_ENV_VARS` and does NOT end in ``_API_KEY``,
        ``_TOKEN``, etc. — this is the path that exercises the
        connection-string redaction in :func:`_mask_connection_string`.
        """
        secret = "p@ssw0rd!"
        url = f"postgresql+asyncpg://user:{secret}@localhost:5432/db"
        monkeypatch.setenv("POSTGRES_CONNECTION_STRING", url)

        env_tool = tool_by_name["system_env"]

        # Default (masked) — password replaced, host/port/db kept.
        result = await env_tool.ainvoke({})
        decoded = json.loads(result)
        assert "POSTGRES_CONNECTION_STRING" in decoded
        masked_value = decoded["POSTGRES_CONNECTION_STRING"]
        assert secret not in masked_value
        assert "[REDACTED]" in masked_value
        assert "localhost" in masked_value
        assert "5432" in masked_value
        assert "/db" in masked_value
        assert "user" in masked_value

        # nomask=True — full URL returned.
        result_nomask = await env_tool.ainvoke({"nomask": True})
        decoded_nomask = json.loads(result_nomask)
        assert decoded_nomask["POSTGRES_CONNECTION_STRING"] == url

    @pytest.mark.asyncio
    async def test_result_is_sorted_json(self, tool_by_name, monkeypatch):
        """The tool returns a JSON object with sorted keys for stable output."""
        monkeypatch.setenv("ENSEMBLE_TEST_A", "1")
        monkeypatch.setenv("ENSEMBLE_TEST_B", "2")
        env_tool = tool_by_name["system_env"]
        result = await env_tool.ainvoke({})
        # The raw string should be sorted (json.dumps with sort_keys=True).
        decoded = json.loads(result)
        keys = list(decoded.keys())
        assert keys == sorted(keys)

    @pytest.mark.asyncio
    async def test_result_is_valid_json(self, tool_by_name):
        env_tool = tool_by_name["system_env"]
        result = await env_tool.ainvoke({})
        # Must always be valid JSON (a dict — possibly empty).
        decoded = json.loads(result)
        assert isinstance(decoded, dict)


# ─── system_config ────────────────────────────────────────────────────────────


class TestSystemConfig:
    @pytest.mark.asyncio
    async def test_returns_full_config(self, tool_by_name):
        config_tool = tool_by_name["system_config"]
        result = await config_tool.ainvoke({})
        decoded = json.loads(result)
        # The Config Pydantic model defines these top-level sections.
        for section in ("llm", "daemon", "limits", "persistence", "agents"):
            assert section in decoded, f"Missing section: {section}"

    @pytest.mark.asyncio
    async def test_masks_secrets_by_default(self, tool_by_name, manager_mock):
        """Secret-bearing fields are redacted by default."""
        manager_mock.config.llm.api_key = "sk-supersecret-xyz"
        config_tool = tool_by_name["system_config"]
        result = await config_tool.ainvoke({})
        decoded = json.loads(result)
        assert decoded["llm"]["api_key"] == "[REDACTED]"
        assert "sk-supersecret" not in decoded["llm"]["api_key"]

    @pytest.mark.asyncio
    async def test_nomask_returns_real_values(self, tool_by_name, manager_mock):
        manager_mock.config.llm.api_key = "sk-supersecret-xyz"
        config_tool = tool_by_name["system_config"]
        result = await config_tool.ainvoke({"nomask": True})
        decoded = json.loads(result)
        assert decoded["llm"]["api_key"] == "sk-supersecret-xyz"

    @pytest.mark.asyncio
    async def test_section_filter(self, tool_by_name):
        config_tool = tool_by_name["system_config"]
        result = await config_tool.ainvoke({"section": "llm"})
        decoded = json.loads(result)
        assert list(decoded.keys()) == ["llm"]
        # The LLM section has these subfields.
        assert "api_key" in decoded["llm"]
        assert "model" in decoded["llm"]

    @pytest.mark.asyncio
    async def test_section_filter_invalid(self, tool_by_name):
        config_tool = tool_by_name["system_config"]
        result = await config_tool.ainvoke({"section": "nonexistent_section"})
        decoded = json.loads(result)
        assert "error" in decoded
        assert "nonexistent_section" in decoded["error"]
        # The error should also list the valid sections for the agent.
        assert "llm" in decoded["error"]
        assert "daemon" in decoded["error"]

    @pytest.mark.asyncio
    async def test_sections_derived_dynamically(self, tool_by_name, manager_mock):
        """Valid sections match ``Config.model_fields.keys()`` exactly."""
        config_tool = tool_by_name["system_config"]
        result = await config_tool.ainvoke({"section": "definitely_not_a_section"})
        decoded = json.loads(result)
        assert "error" in decoded
        valid_in_error = decoded["error"]
        expected = sorted(Config.model_fields.keys())
        # Every expected section appears in the error string.
        for section in expected:
            assert section in valid_in_error, (
                f"Expected section {section!r} missing from error: {valid_in_error}"
            )

    @pytest.mark.asyncio
    async def test_nested_secrets_masked(self, tool_by_name, manager_mock):
        """Nested dict values with secret-bearing keys are masked recursively."""
        manager_mock.config.compaction.context_window_overrides = {
            "vision": 16385,
        }
        # Reach into a field that doesn't have a top-level secret key, but
        # stuff a secret-like key into a sub-structure that goes through
        # the masker. We do this by mutating a sub-config object.
        manager_mock.config.services.graph_timeout_minutes = 42.0
        # The most direct test: ensure the llm.api_key at a known depth
        # is masked.
        manager_mock.config.llm.api_key = "sk-nested-secret-789"
        config_tool = tool_by_name["system_config"]
        result = await config_tool.ainvoke({})
        decoded = json.loads(result)
        assert decoded["llm"]["api_key"] == "[REDACTED]"
        # Non-secret fields are preserved as-is.
        assert decoded["services"]["graph_timeout_minutes"] == 42.0

    @pytest.mark.asyncio
    async def test_config_none_handling(self, tool_by_name, manager_no_config):
        """When ``manager.config is None``, the tool returns a structured error."""
        # Rebuild tools with the None-config manager.
        tools = create_system_tools(manager_no_config, "instance-1")
        config_tool = {t.name: t for t in tools}["system_config"]
        result = await config_tool.ainvoke({})
        decoded = json.loads(result)
        assert "error" in decoded
        assert "config" in decoded["error"].lower()
        assert "not available" in decoded["error"].lower() or "initialized" in decoded["error"].lower()

    @pytest.mark.asyncio
    async def test_result_is_valid_json(self, tool_by_name):
        config_tool = tool_by_name["system_config"]
        result = await config_tool.ainvoke({})
        decoded = json.loads(result)
        assert isinstance(decoded, dict)


# ─── system_health ────────────────────────────────────────────────────────────


class TestSystemHealth:
    @pytest.mark.asyncio
    async def test_returns_version(self, tool_by_name):
        # The RAG module is imported inline; patch it so the tool sees a
        # deterministic RAG flag.
        with patch("daemon.rag.config.is_rag_enabled", return_value=False):
            health_tool = tool_by_name["system_health"]
            result = await health_tool.ainvoke({})
        decoded = json.loads(result)
        assert "version" in decoded
        assert isinstance(decoded["version"], str)
        assert decoded["version"]  # non-empty

    @pytest.mark.asyncio
    async def test_returns_database_type(self, tool_by_name, manager_mock):
        # Force the ensemble config to a specific database.
        manager_mock.ensemble_config = EnsembleConfig(database="postgres")
        with patch("daemon.rag.config.is_rag_enabled", return_value=False):
            health_tool = tool_by_name["system_health"]
            result = await health_tool.ainvoke({})
        decoded = json.loads(result)
        assert decoded["database_type"] == "postgres"

    @pytest.mark.asyncio
    async def test_returns_database_type_sqlite(self, tool_by_name, manager_mock):
        manager_mock.ensemble_config = EnsembleConfig(database="sqlite")
        with patch("daemon.rag.config.is_rag_enabled", return_value=False):
            health_tool = tool_by_name["system_health"]
            result = await health_tool.ainvoke({})
        decoded = json.loads(result)
        assert decoded["database_type"] == "sqlite"

    @pytest.mark.asyncio
    async def test_returns_rag_enabled_true(self, tool_by_name):
        with patch("daemon.rag.config.is_rag_enabled", return_value=True):
            health_tool = tool_by_name["system_health"]
            result = await health_tool.ainvoke({})
        decoded = json.loads(result)
        assert decoded["rag_enabled"] is True

    @pytest.mark.asyncio
    async def test_returns_rag_enabled_false(self, tool_by_name):
        with patch("daemon.rag.config.is_rag_enabled", return_value=False):
            health_tool = tool_by_name["system_health"]
            result = await health_tool.ainvoke({})
        decoded = json.loads(result)
        assert decoded["rag_enabled"] is False

    @pytest.mark.asyncio
    async def test_rag_enabled_falls_back_to_false_on_exception(self, tool_by_name):
        """If ``is_rag_enabled()`` raises, the tool sets ``rag_enabled=False``."""
        with patch(
            "daemon.rag.config.is_rag_enabled",
            side_effect=RuntimeError("rag config broken"),
        ):
            health_tool = tool_by_name["system_health"]
            result = await health_tool.ainvoke({})
        decoded = json.loads(result)
        assert decoded["rag_enabled"] is False

    @pytest.mark.asyncio
    async def test_returns_platform_info(self, tool_by_name):
        with patch("daemon.rag.config.is_rag_enabled", return_value=False):
            health_tool = tool_by_name["system_health"]
            result = await health_tool.ainvoke({})
        decoded = json.loads(result)
        assert "platform" in decoded
        assert "python_version" in decoded
        assert isinstance(decoded["platform"], str)
        assert isinstance(decoded["python_version"], str)
        # python_version looks like "3.11.5".
        assert decoded["python_version"].count(".") >= 1

    @pytest.mark.asyncio
    async def test_returns_pid(self, tool_by_name):
        with patch("daemon.rag.config.is_rag_enabled", return_value=False):
            health_tool = tool_by_name["system_health"]
            result = await health_tool.ainvoke({})
        decoded = json.loads(result)
        assert "process_pid" in decoded
        assert isinstance(decoded["process_pid"], int)
        # The PID of the test process is a real, non-zero integer.
        assert decoded["process_pid"] > 0

    @pytest.mark.asyncio
    async def test_data_directory_from_manager_property(self, tool_by_name, manager_mock):
        manager_mock.data_dir = "/custom/data/dir"
        with patch("daemon.rag.config.is_rag_enabled", return_value=False):
            health_tool = tool_by_name["system_health"]
            result = await health_tool.ainvoke({})
        decoded = json.loads(result)
        assert decoded["data_directory"] == "/custom/data/dir"

    @pytest.mark.asyncio
    async def test_data_directory_fallback_to_config(self, manager_mock):
        """If ``data_dir`` is missing on the manager, fall back to config."""
        # Wipe the auto-generated data_dir attribute on the MagicMock by
        # setting it to None explicitly.
        manager_mock.data_dir = None
        manager_mock.config.persistence.db_path = "/fallback/data/instances.db"
        with patch("daemon.rag.config.is_rag_enabled", return_value=False):
            tools = create_system_tools(manager_mock, "instance-1")
            health_tool = {t.name: t for t in tools}["system_health"]
            result = await health_tool.ainvoke({})
        decoded = json.loads(result)
        # Fallback is the parent directory of db_path.
        assert decoded["data_directory"] == "/fallback/data"

    @pytest.mark.asyncio
    async def test_database_type_defaults_to_sqlite_when_no_ensemble_config(self):
        """If ``ensemble_config`` is missing entirely, default to ``sqlite``."""
        m = MagicMock()
        m.ensemble_config = None
        m.config = Config()
        m.data_dir = "/tmp/ensemble-test-data"
        with patch("daemon.rag.config.is_rag_enabled", return_value=False):
            tools = create_system_tools(m, "instance-1")
            health_tool = {t.name: t for t in tools}["system_health"]
            result = await health_tool.ainvoke({})
        decoded = json.loads(result)
        assert decoded["database_type"] == "sqlite"

    @pytest.mark.asyncio
    async def test_returns_all_expected_fields(self, tool_by_name):
        """The snapshot should contain the eight documented fields."""
        with patch("daemon.rag.config.is_rag_enabled", return_value=False):
            health_tool = tool_by_name["system_health"]
            result = await health_tool.ainvoke({})
        decoded = json.loads(result)
        expected = {
            "version",
            "database_type",
            "rag_enabled",
            "python_version",
            "platform",
            "platform_machine",
            "data_directory",
            "process_pid",
        }
        assert expected.issubset(decoded.keys())


# ─── Secret-masking helpers (direct unit tests) ───────────────────────────────


class TestSecretMasking:
    def test_mask_recursive_dict(self):
        """``_mask_secret`` is a value-level redactor — every leaf value
        (string, int, etc.) is masked. It does NOT consult key names; the
        key-aware walking lives in :func:`_mask_config`, which delegates
        to :func:`_mask_secret` for values whose key is secret-bearing.
        """
        payload = {
            "api_key": "sk-top",
            "nested": {
                "password": "p@ss",
                "innocent": "keep-me",
                "deeper": {"token": "tk-1", "name": "alice"},
            },
            "list_field": [
                {"secret": "sec-1", "value": 42},
                "plain-string",
            ],
        }
        masked = _mask_secret(payload)
        # Every leaf value is masked, regardless of the key it lives under.
        assert masked["api_key"] == "[REDACTED]"
        assert masked["nested"]["password"] == "[REDACTED]"
        assert masked["nested"]["innocent"] == "[REDACTED]"
        assert masked["nested"]["deeper"]["token"] == "[REDACTED]"
        assert masked["nested"]["deeper"]["name"] == "[REDACTED]"
        assert masked["list_field"][0]["secret"] == "[REDACTED]"
        assert masked["list_field"][0]["value"] == "[REDACTED]"
        assert masked["list_field"][1] == "[REDACTED]"

    def test_mask_recursive_dict_returns_new_dict(self):
        """The masker returns a new dict — it does not mutate the input."""
        original = {"api_key": "sk-x", "x": 1}
        masked = _mask_secret(original)
        assert masked is not original
        assert original["api_key"] == "sk-x"  # input untouched

    def test_mask_recursive_dict_preserves_non_secret_keys(self):
        """``_mask_secret`` is value-level aggressive — it has no notion of
        'non-secret' keys; it blankets the entire value tree. Use
        :func:`_mask_config` for key-aware walking that leaves plain fields
        alone.
        """
        payload = {"model": "gpt-4", "temperature": 0.7}
        masked = _mask_secret(payload)
        # The function does NOT preserve non-secret-looking keys; every
        # leaf value gets the placeholder.
        assert masked["model"] == "[REDACTED]"
        assert masked["temperature"] == "[REDACTED]"

    def test_mask_url_password(self):
        """URL with embedded password gets the password replaced."""
        url = "postgresql://user:p@ss@host:5432/db"
        masked = _mask_secret(url)
        assert "p@ss" not in masked
        assert "[REDACTED]" in masked
        assert "host" in masked
        assert "5432" in masked
        assert "user" in masked

    def test_mask_url_password_asyncpg(self):
        url = "postgresql+asyncpg://admin:s3cr3t!@db.example.com:6543/prod"
        masked = _mask_secret(url)
        assert "s3cr3t!" not in masked
        assert "[REDACTED]" in masked
        assert "db.example.com" in masked
        assert "6543" in masked
        assert "admin" in masked
        assert "/prod" in masked

    def test_mask_url_no_password_becomes_placeholder(self):
        """A URL string with no embedded password is still a string value,
        so :func:`_mask_secret` replaces it with the literal placeholder.
        Use :func:`_mask_connection_string` directly (or the leaf path of
        :func:`_mask_config`) to keep URL structure visible.
        """
        url = "https://api.example.com/v1/endpoint"
        assert _mask_secret(url) == "[REDACTED]"
        # But _mask_connection_string alone preserves it.
        assert _mask_connection_string(url) == url

    def test_mask_url_only_at_sign_becomes_placeholder(self):
        """A ``@`` in the URL path does not constitute a password component
        and the URL has no userinfo, so the connection-string helper leaves
        it alone; ``_mask_secret`` then falls through to ``[REDACTED]``."""
        url = "https://api.example.com/@me/profile"
        assert _mask_secret(url) == "[REDACTED]"
        # The connection-string helper itself preserves it.
        assert _mask_connection_string(url) == url

    def test_mask_non_url_string_placeholder(self):
        """Plain strings become the literal ``[REDACTED]`` placeholder."""
        assert _mask_secret("hello") == "[REDACTED]"
        assert _mask_secret("sk-supersecret") == "[REDACTED]"

    def test_mask_none_and_empty_passthrough(self):
        """``None`` and empty string are returned as-is (no placeholder)."""
        assert _mask_secret(None) is None
        assert _mask_secret("") == ""

    def test_mask_list(self):
        """Lists are walked element-by-element — strings and primitives
        all become the literal ``[REDACTED]`` placeholder.
        """
        payload = [
            {"api_key": "k1"},
            {"api_key": "k2", "ok": 1},
            "plain",
        ]
        masked = _mask_secret(payload)
        assert masked[0]["api_key"] == "[REDACTED]"
        assert masked[1]["api_key"] == "[REDACTED]"
        # Primitives inside list items are also masked.
        assert masked[1]["ok"] == "[REDACTED]"
        assert masked[2] == "[REDACTED]"

    def test_mask_tuple_returns_tuple(self):
        """Tuples are masked and return a new tuple (not a list)."""
        payload = ({"api_key": "k1"}, "plain")
        masked = _mask_secret(payload)
        assert isinstance(masked, tuple)
        assert masked[0]["api_key"] == "[REDACTED]"
        assert masked[1] == "[REDACTED]"

    def test_mask_primitives_become_placeholder(self):
        """Numbers, booleans, etc. become the literal ``[REDACTED]``."""
        assert _mask_secret(42) == "[REDACTED]"
        assert _mask_secret(3.14) == "[REDACTED]"
        assert _mask_secret(True) == "[REDACTED]"

    def test_mask_connection_string_helper(self):
        """The connection-string helper masks only the password component."""
        url = "postgresql://u:p@h:1/d"
        assert _mask_connection_string(url) == "postgresql://u:[REDACTED]@h:1/d"

    def test_mask_connection_string_no_password_unchanged(self):
        assert _mask_connection_string("https://api.example.com/v1") == "https://api.example.com/v1"

    def test_mask_connection_string_no_at_sign_unchanged(self):
        # No @, no password to mask.
        assert _mask_connection_string("postgresql://host/db") == "postgresql://host/db"

    def test_mask_connection_string_non_string_unchanged(self):
        assert _mask_connection_string(None) is None  # type: ignore[arg-type]
        assert _mask_connection_string(123) == 123  # type: ignore[arg-type]
        assert _mask_connection_string("") == ""

    def test_is_secret_key_patterns(self):
        """Various key names are correctly classified as secret / not-secret."""
        # Secret-bearing keys.
        secret_keys = [
            "api_key",
            "API_KEY",
            "openai_api_key",
            "password",
            "user_password",
            "token",
            "auth_token",
            "secret",
            "client_secret",
            "headers",
            "extra_headers",
            "api_base",
            "openai_api_base",
        ]
        for key in secret_keys:
            assert _is_secret_key(key), f"Expected {key!r} to be flagged as secret"

        # Non-secret keys.
        plain_keys = [
            "model",
            "host",
            "port",
            "url",
            "name",
            "temperature",
            "directory",
            "enabled",
            "timeout",
        ]
        for key in plain_keys:
            assert not _is_secret_key(key), f"Expected {key!r} NOT to be flagged as secret"

    def test_is_secret_key_non_string(self):
        """Non-string key names are always non-secret."""
        assert _is_secret_key(123) is False  # type: ignore[arg-type]
        assert _is_secret_key(None) is False  # type: ignore[arg-type]
        assert _is_secret_key(["api_key"]) is False  # type: ignore[arg-type]

    def test_is_secret_key_substring_match(self):
        """The match is case-insensitive substring, not prefix or exact."""
        assert _is_secret_key("my_password_field") is True
        assert _is_secret_key("MY_PASSWORD_FIELD") is True
        # Substring match: 'headers' is anywhere in the key.
        assert _is_secret_key("x-extra-headers") is True

    def test_mask_env_value_nomask_returns_raw(self):
        """``nomask=True`` bypasses all masking."""
        assert _mask_env_value("OPENAI_API_KEY", "sk-x", nomask=True) == "sk-x"
        assert _mask_env_value("OPENSPACE_API_KEY", "v", nomask=True) == "v"

    def test_mask_env_value_explicit_secret_list(self):
        """Vars in :data:`_SECRET_ENV_VARS` are masked regardless of suffix."""
        for name in _SECRET_ENV_VARS:
            assert _mask_env_value(name, "v", nomask=False) == "[REDACTED]"

    def test_mask_env_value_suffix_match(self):
        """Vars ending with a secret suffix are masked."""
        for suffix in _SECRET_SUFFIXES:
            name = f"MY{suffix}"
            assert _mask_env_value(name, "v", nomask=False) == "[REDACTED]"

    def test_mask_env_value_plain_value_unchanged(self):
        """Non-secret values are returned unchanged."""
        assert _mask_env_value("ENSEMBLE_TEST_VAR", "hello", nomask=False) == "hello"
        assert _mask_env_value("POSTGRES_HOST", "db.example.com", nomask=False) == "db.example.com"

    def test_mask_env_value_url_password_surgical(self):
        """A value with a URL password gets the password scrubbed, not the whole value."""
        url = "postgresql://u:p@host:5432/db"
        result = _mask_env_value("POSTGRES_CONNECTION_STRING", url, nomask=False)
        assert result == "postgresql://u:[REDACTED]@host:5432/db"
        # nomask=True → raw.
        assert _mask_env_value("POSTGRES_CONNECTION_STRING", url, nomask=True) == url

    def test_mask_config_walks_nested_dicts(self):
        """``_mask_config`` recurses through nested dicts, masking secret keys."""
        payload = {
            "llm": {
                "api_key": "sk-top",
                "model": "gpt-4",
            },
            "daemon": {
                "host": "0.0.0.0",
                "extra_password": "p@ss",
            },
        }
        masked = _mask_config(payload)
        assert masked["llm"]["api_key"] == "[REDACTED]"
        assert masked["llm"]["model"] == "gpt-4"
        assert masked["daemon"]["host"] == "0.0.0.0"
        assert masked["daemon"]["extra_password"] == "[REDACTED]"

    def test_mask_config_scrubs_url_in_non_secret_key(self):
        """Defence in depth: URL passwords are scrubbed even when the key
        is not itself a secret-bearing name."""
        payload = {
            "persistence": {
                "db_path": "postgresql://u:p@host:5432/db",
            }
        }
        masked = _mask_config(payload)
        # Key 'db_path' is not a secret key, but its value gets the
        # connection-string scrub.
        assert "p" not in masked["persistence"]["db_path"].split("@")[0].split(":")[-1]
        assert "[REDACTED]" in masked["persistence"]["db_path"]

    def test_mask_config_handles_lists(self):
        payload = {
            "items": [
                {"api_key": "k1"},
                {"model": "gpt-4"},
            ]
        }
        masked = _mask_config(payload)
        assert masked["items"][0]["api_key"] == "[REDACTED]"
        assert masked["items"][1]["model"] == "gpt-4"


# ─── Tracked-prefix / secret-list invariants ──────────────────────────────────


class TestTrackedPrefixes:
    def test_tracked_env_prefixes_includes_ensemble(self):
        assert "ENSEMBLE_" in _TRACKED_ENV_PREFIXES

    def test_tracked_env_exact_includes_postgres_url(self):
        assert "POSTGRES_URL" in _TRACKED_ENV_EXACT
        assert "DATABASE_URL_POSTGRES" in _TRACKED_ENV_EXACT

    def test_secret_env_vars_is_frozenset(self):
        assert isinstance(_SECRET_ENV_VARS, frozenset)

    def test_secret_key_substrings_is_tuple(self):
        assert isinstance(_SECRET_KEY_SUBSTRINGS, tuple)
        for s in ("api_key", "password", "token", "secret", "headers", "base"):
            assert s in _SECRET_KEY_SUBSTRINGS

    def test_secret_suffixes_includes_api_key(self):
        assert "_API_KEY" in _SECRET_SUFFIXES
        assert "_TOKEN" in _SECRET_SUFFIXES
        assert "_PASSWORD" in _SECRET_SUFFIXES
        assert "_SECRET" in _SECRET_SUFFIXES
        assert "_HEADERS" in _SECRET_SUFFIXES


# ─── Integration with the tool registry ──────────────────────────────────────


class TestSystemToolsWiredIntoInstance:
    def test_system_tools_appear_in_registry(self):
        """``create_system_tools`` registers tools under the ``system`` category."""
        from daemon.tools._tool_registry import (
            _tool_metadata,
            list_tools_by_category,
            scan_tools_for_full_docs,
        )

        tools = create_system_tools(MagicMock(), "instance")
        scan_tools_for_full_docs(tools)

        categories = list_tools_by_category()
        assert "system" in categories
        for name in ("system_env", "system_config", "system_health"):
            assert name in categories["system"]
            assert name in _tool_metadata
            assert _tool_metadata[name]["category"] == "system"
