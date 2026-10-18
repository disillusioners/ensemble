"""Tests for daemon/config.py."""

import os
import pytest
from pathlib import Path
import yaml
from daemon.config import load_config, Config, substitute_env_vars


class TestSubstituteEnvVars:
    """Tests for environment variable substitution."""

    def test_env_var_substitution_string(self):
        """Test ${VAR:-default} syntax substitution in string."""
        os.environ["TEST_VAR"] = "test_value"
        result = substitute_env_vars("prefix_${TEST_VAR}_suffix")
        assert result == "prefix_test_value_suffix"

    def test_env_var_substitution_default_value(self):
        """Test default value when env var is not set."""
        result = substitute_env_vars("prefix_${UNDEFINED_VAR:-default_val}_suffix")
        assert result == "prefix_default_val_suffix"

    def test_env_var_substitution_empty_default(self):
        """Test empty default value when env var is not set."""
        result = substitute_env_vars("prefix_${UNDEFINED_VAR:-}_suffix")
        assert result == "prefix__suffix"

    def test_env_var_substitution_no_default(self):
        """Test substitution when no default is provided."""
        result = substitute_env_vars("prefix_${UNDEFINED_VAR}_suffix")
        assert result == "prefix__suffix"

    def test_env_var_substitution_dict(self):
        """Test substitution in dictionary values."""
        os.environ["TEST_VAR"] = "dict_value"
        result = substitute_env_vars({"key": "${TEST_VAR}"})
        assert result == {"key": "dict_value"}

    def test_env_var_substitution_list(self):
        """Test substitution in list items."""
        os.environ["TEST_VAR"] = "list_value"
        result = substitute_env_vars(["${TEST_VAR}", "static"])
        assert result == ["list_value", "static"]

    def test_env_var_substitution_nested(self):
        """Test substitution in nested structures."""
        os.environ["OUTER"] = "outer_value"
        os.environ["INNER"] = "inner_value"
        result = substitute_env_vars({
            "outer": {"inner": "${INNER}"},
            "list": [{"key": "${OUTER}"}]
        })
        assert result == {
            "outer": {"inner": "inner_value"},
            "list": [{"key": "outer_value"}]
        }

    def test_env_var_substitution_non_string(self):
        """Test that non-string values are returned as-is."""
        assert substitute_env_vars(123) == 123
        assert substitute_env_vars(None) is None
        assert substitute_env_vars(True) is True


class TestLoadConfig:
    """Tests for load_config function."""

    def test_load_config_default(self, tmp_path, sample_config_yaml):
        """Test loading config from default path (./config.yaml)."""
        # Create config file in tmp_path and point ENSEMBLE_CONFIG to it
        config_file = tmp_path / "config.yaml"
        with open(config_file, "w") as f:
            f.write(sample_config_yaml)
        
        # Set the environment variable to use our tmp config
        os.environ["ENSEMBLE_CONFIG"] = str(config_file)
        
        try:
            config = load_config()
            
            assert config.llm.base_url == "https://api.openai.com/v1"
            assert config.llm.api_key == "test-key"
            assert config.llm.model == "gpt-4"
            assert config.daemon.host == "0.0.0.0"
            assert config.daemon.port == 8079
            assert config.limits.max_children_per_instance == 50
        finally:
            # Cleanup env var
            if "ENSEMBLE_CONFIG" in os.environ:
                del os.environ["ENSEMBLE_CONFIG"]

    def test_load_config_custom_path(self, tmp_path, sample_config_yaml):
        """Test loading config from custom path via ENSEMBLE_CONFIG env var."""
        # Create a temporary config file
        config_file = tmp_path / "custom_config.yaml"
        with open(config_file, "w") as f:
            f.write(sample_config_yaml)
        
        # Set the environment variable
        os.environ["ENSEMBLE_CONFIG"] = str(config_file)
        
        try:
            config = load_config()
            
            assert config.llm.base_url == "https://api.openai.com/v1"
            assert config.daemon.port == 8079
        finally:
            # Cleanup env var
            del os.environ["ENSEMBLE_CONFIG"]

    def test_load_config_explicit_path(self, tmp_path, sample_config_yaml):
        """Test loading config from explicit path parameter."""
        # Create a temporary config file
        config_file = tmp_path / "explicit_config.yaml"
        with open(config_file, "w") as f:
            f.write(sample_config_yaml)
        
        config = load_config(str(config_file))
        
        assert config.llm.base_url == "https://api.openai.com/v1"
        assert config.daemon.port == 8079

    def test_env_var_substitution_in_config(self, tmp_path, sample_config_with_env_vars):
        """Test environment variable substitution in config file."""
        # Set environment variables
        os.environ["CUSTOM_LLM_URL"] = "https://custom.llm.api/v1"
        os.environ["LLM_API_KEY"] = "env-api-key"
        os.environ["DAEMON_HOST"] = "127.0.0.1"
        os.environ["DAEMON_PORT"] = "9000"
        
        # Create config file with env var placeholders
        config_file = tmp_path / "env_config.yaml"
        with open(config_file, "w") as f:
            f.write(sample_config_with_env_vars)
        
        try:
            config = load_config(str(config_file))
            
            assert config.llm.base_url == "https://custom.llm.api/v1"
            assert config.llm.api_key == "env-api-key"
            assert config.daemon.host == "127.0.0.1"
            assert config.daemon.port == 9000
        finally:
            # Cleanup env vars
            del os.environ["CUSTOM_LLM_URL"]
            del os.environ["LLM_API_KEY"]
            del os.environ["DAEMON_HOST"]
            del os.environ["DAEMON_PORT"]

    def test_missing_config_file(self, tmp_path):
        """Test error when config file doesn't exist."""
        nonexistent_path = tmp_path / "nonexistent.yaml"
        
        with pytest.raises(FileNotFoundError) as exc_info:
            load_config(str(nonexistent_path))
        
        assert "Config file not found" in str(exc_info.value)

    def test_missing_default_config_file(self, tmp_path):
        """Test error when default config file doesn't exist."""
        # Point ENSEMBLE_CONFIG to a nonexistent path in tmp_path
        nonexistent_path = tmp_path / "nonexistent.yaml"
        os.environ["ENSEMBLE_CONFIG"] = str(nonexistent_path)
        
        try:
            with pytest.raises(FileNotFoundError) as exc_info:
                load_config()
            
            assert "Config file not found" in str(exc_info.value)
        finally:
            if "ENSEMBLE_CONFIG" in os.environ:
                del os.environ["ENSEMBLE_CONFIG"]

    def test_empty_config_file(self, tmp_path):
        """Test error when config file is empty."""
        config_file = tmp_path / "empty.yaml"
        config_file.write_text("")
        
        with pytest.raises(ValueError) as exc_info:
            load_config(str(config_file))
        
        assert "empty" in str(exc_info.value).lower()

    def test_invalid_yaml_config(self, tmp_path):
        """Test error when config file has invalid YAML."""
        config_file = tmp_path / "invalid.yaml"
        config_file.write_text("invalid: yaml: content: [}")

        with pytest.raises(ValueError) as exc_info:
            load_config(str(config_file))

        assert "Failed to parse" in str(exc_info.value)


class TestWorkerPoolYamlShadow:
    """settings-worker-slots F1 (council iter001) — the two
    worker-pool-size keys (``limits.worker_pool_size``,
    ``limits.chat_worker_pool_size``) are YAML-shadowed by literals
    in ``config.yaml`` that MIRROR the constants
    (``WORKER_POOL_SIZE=5`` / ``CHAT_WORKER_POOL_SIZE=2``). The
    mechanism keeps the 4-tier ladder (DB > env > yaml > default)
    authoritative while making malformed env values
    NON-FATAL at ``load_config``:

    * pydantic binds the init_kwarg (yaml literal) instead of the
      env value — pydantic-settings priority is args/init_kwargs >
      env > defaults — so a malformed env value never reaches the
      LimitsConfig(int) validation.
    * The settings resolver reads ``os.environ[spec.env_var]``
      DIRECTLY (daemon/services/settings_service.py:377, the
      shadowing-bug fix), parses+validates with parse_and_validate,
      and on an invalid env value logs one WARNING + skips the env
      tier — the yaml tier (or default below) wins.

    Mirrors the existing ``llm_concurrency: 10`` yaml literal at
    config.yaml:208 — same mechanism, same resolver path."""

    def test_valid_env_value_wins_over_yaml(
        self, tmp_path, monkeypatch
    ):
        """Valid env value (LIMITS_WORKER_POOL_SIZE=8) bypasses the
        yaml shadow at pydantic-init time but the resolver promotes
        env over yaml — the post-apply config field is 8.

        Two-step verification (mirrors the real boot path:
        ``load_config`` → ``apply_db_settings_overrides``):

        1. After ``load_config`` alone, the field is the yaml literal
           value (5) — pydantic init_kwarg shadowed the env. This is
           the F1 mechanism's whole point: malformed env cannot reach
           the int validator.
        2. After ``apply_db_settings_overrides`` (the boot resolver),
           the field is the env value (8) — the resolver reads
           ``os.environ[spec.env_var]`` DIRECTLY and promotes env over
           yaml. The 4-tier ladder (DB > env > yaml > default) is the
           SSOT, not the pydantic field."""
        config_file = tmp_path / "pool_cfg.yaml"
        config_file.write_text(
            "limits:\n"
            "  worker_pool_size: 5\n"
            "  chat_worker_pool_size: 2\n"
        )
        monkeypatch.setenv("LIMITS_WORKER_POOL_SIZE", "8")
        monkeypatch.setenv("LIMITS_CHAT_WORKER_POOL_SIZE", "3")
        monkeypatch.setenv("ENSEMBLE_CONFIG", str(config_file))
        # Clear any pre-existing env values that might shadow.
        for k in ("OPENAI_SELECTABLE_MODELS", "OPENAI_ALLOWED_MODELS"):
            monkeypatch.delenv(k, raising=False)

        config = load_config()

        # Step 1 — pydantic init_kwarg (yaml literal) is what the
        # config field holds; the env value never reached the int
        # validator (this is the F1 shield for malformed env).
        assert config.limits.worker_pool_size == 5
        assert config.limits.chat_worker_pool_size == 2

        # Step 2 — the boot resolver promotes env over yaml.
        from daemon.config import raw_yaml_limits
        from daemon.services.settings_service import (
            apply_db_settings_overrides,
        )

        # ``db_overrides=None`` skips the DB tier; the resolver
        # promotes env (8) over yaml (5) on the same ladder.
        config, _snapshot = apply_db_settings_overrides(
            config=config,
            ensemble_config=None,
            raw_yaml_limits=raw_yaml_limits(),
        )
        assert config.limits.worker_pool_size == 8
        assert config.limits.chat_worker_pool_size == 3

    def test_malformed_env_value_is_non_fatal_at_load_config(
        self, tmp_path, monkeypatch
    ):
        """Pin F1 (the boot-robustness WARNING): with the yaml shadow
        present, ``LIMITS_WORKER_POOL_SIZE=abc`` must NOT raise at
        ``load_config`` — the resolver's documented skip/fail-open
        contract is preserved. The config field falls through to the
        yaml literal (5/2); the env tier is skipped with a WARNING."""
        config_file = tmp_path / "pool_cfg.yaml"
        config_file.write_text(
            "limits:\n"
            "  worker_pool_size: 5\n"
            "  chat_worker_pool_size: 2\n"
        )
        monkeypatch.setenv("LIMITS_WORKER_POOL_SIZE", "abc")
        monkeypatch.setenv("LIMITS_CHAT_WORKER_POOL_SIZE", "xyz")
        monkeypatch.setenv("ENSEMBLE_CONFIG", str(config_file))
        for k in ("OPENAI_SELECTABLE_MODELS", "OPENAI_ALLOWED_MODELS"):
            monkeypatch.delenv(k, raising=False)

        # The fix's whole point: this MUST NOT raise. Before the fix
        # this raised ValidationError (pydantic bound env directly
        # because no yaml shadow existed).
        config = load_config()

        # The yaml tier wins (5/2); pydantic init_kwarg shadowed the
        # malformed env, so the field holds the yaml literal.
        assert config.limits.worker_pool_size == 5
        assert config.limits.chat_worker_pool_size == 2

    def test_yaml_literal_present_in_raw_yaml_limits(
        self, tmp_path, monkeypatch
    ):
        """The yaml literal MUST be visible via ``raw_yaml_limits()``
        so the resolver's yaml tier can see it. Absence would defeat
        the F1 mechanism (the resolver reads the raw section, NOT the
        pydantic-merged field)."""
        config_file = tmp_path / "pool_cfg.yaml"
        config_file.write_text(
            "limits:\n"
            "  worker_pool_size: 5\n"
            "  chat_worker_pool_size: 2\n"
        )
        monkeypatch.setenv("ENSEMBLE_CONFIG", str(config_file))
        for k in ("OPENAI_SELECTABLE_MODELS", "OPENAI_ALLOWED_MODELS"):
            monkeypatch.delenv(k, raising=False)

        from daemon.config import raw_yaml_limits

        load_config()
        raw = raw_yaml_limits()
        assert raw is not None
        assert raw.get("worker_pool_size") == 5
        assert raw.get("chat_worker_pool_size") == 2

    def test_shipped_repo_config_yaml_literals_pinned_to_constants(self):
        """Pin the SHIPPED repo-root ``config.yaml`` artifact itself.

        The three temp-config tests above each build their own
        ``limits:`` section via ``ENSEMBLE_CONFIG`` — they prove the
        F1 mechanism but are BLIND to the shipped file: deleting
        either yaml literal (``worker_pool_size`` /
        ``chat_worker_pool_size``) from the repo-root ``config.yaml``
        would leave those suites green while real boots reinstated
        the boot-time ValidationError this commission fixed (pydantic
        would bind the env tier directly, and a malformed env value
        would be fatal again). This test loads the REAL shipped file
        (repo-root ``config.yaml``, anchored to the test tree; the
        explicit-path call never consults ``ENSEMBLE_CONFIG`` — no
        temp file involved) and asserts the raw yaml literals equal
        ``daemon.constants.WORKER_POOL_SIZE`` /
        ``CHAT_WORKER_POOL_SIZE`` — pinning the literal↔constant
        drift coupling at the same time (the yaml mirror and the
        constants must move together)."""
        from daemon import constants
        from daemon.config import raw_yaml_limits

        shipped_config = Path(__file__).resolve().parent.parent / "config.yaml"

        load_config(str(shipped_config))
        raw = raw_yaml_limits()
        assert raw is not None
        assert raw.get("worker_pool_size") == constants.WORKER_POOL_SIZE
        assert raw.get("chat_worker_pool_size") == constants.CHAT_WORKER_POOL_SIZE


class TestConfigValidation:
    """Tests for Config model validation."""

    def test_config_with_defaults(self, monkeypatch):
        """Test that Config model validates with default values.
        
        Clear environment variables to ensure we're testing actual defaults,
        not values from .env or environment variables.
        """
        # Clear LLM-related environment variables
        for key in list(os.environ.keys()):
            if key.startswith("OPENAI_") or key.lower() in ("base_url", "llm_base_url"):
                monkeypatch.delenv(key, raising=False)
        
        config = Config()
        
        assert config.llm.base_url == "https://api.openai.com/v1"
        assert config.llm.api_key == ""
        assert config.llm.model == "gpt-4"
        assert config.daemon.host == "0.0.0.0"
        assert config.daemon.port == 8079
        assert config.limits.max_children_per_instance == 50

    def test_config_with_custom_values(self):
        """Test Config with custom values."""
        config = Config(
            llm={"api_key": "custom-key", "model": "gpt-3.5-turbo"},
            daemon={"port": 9000},
            limits={"max_children_per_instance": 50},
        )

        assert config.llm.api_key == "custom-key"
        assert config.llm.model == "gpt-3.5-turbo"
        assert config.daemon.port == 9000
        assert config.limits.max_children_per_instance == 50

    def test_config_serialization(self, sample_config_yaml, tmp_path):
        """Test Config model serialization."""
        config_file = tmp_path / "test_config.yaml"
        with open(config_file, "w") as f:
            f.write(sample_config_yaml)
        
        config = load_config(str(config_file))
        
        # Test that we can convert to dict
        config_dict = config.model_dump()
        
        assert "llm" in config_dict
        assert config_dict["llm"]["base_url"] == "https://api.openai.com/v1"
        assert config_dict["daemon"]["port"] == 8079


class TestLLMConfig:
    """Tests for LLMConfig."""

    def test_llm_config_defaults(self, monkeypatch):
        """Test LLMConfig default values.

        Clear environment variables to ensure we're testing actual defaults,
        not values from .env or environment variables.
        """
        from daemon.config import LLMConfig

        # Clear LLM-related environment variables
        for key in list(os.environ.keys()):
            if key.startswith("OPENAI_") or key.lower() in ("base_url", "llm_base_url"):
                monkeypatch.delenv(key, raising=False)

        config = LLMConfig()

        assert config.base_url == "https://api.openai.com/v1"
        assert config.api_key == ""
        assert config.model == "gpt-4"
        assert config.temperature == 0.7


class TestModelKeywordsConfig:
    """Tests for the ``model_keywords`` config field.

    The keyword extraction service in ``daemon/services/keyword_extraction.py``
    reads ``config.llm.model_keywords`` and falls back to ``config.llm.model``
    when the field is unset / blank. This mirrors the existing
    ``model_title`` behavior.
    """

    def _clean(self, monkeypatch) -> None:
        """Clear all LLM env vars so defaults are observable."""
        for key in list(os.environ.keys()):
            if key.startswith("OPENAI_"):
                monkeypatch.delenv(key, raising=False)

    def test_model_keywords_falls_back_to_model_when_unset(self, monkeypatch) -> None:
        from daemon.config import LLMConfig

        self._clean(monkeypatch)
        config = LLMConfig()
        assert config.model_keywords == config.model == "gpt-4"

    def test_model_keywords_falls_back_to_model_when_empty(self, monkeypatch) -> None:
        from daemon.config import LLMConfig

        self._clean(monkeypatch)
        # Pass an explicit empty string via the constructor — should be
        # normalized to ``model`` by the model_validator.
        config = LLMConfig(model="gpt-4o", model_keywords="")
        assert config.model_keywords == "gpt-4o"

    def test_model_keywords_respects_explicit_value(self, monkeypatch) -> None:
        from daemon.config import LLMConfig

        self._clean(monkeypatch)
        config = LLMConfig(model="gpt-4o", model_keywords="quick")
        assert config.model_keywords == "quick"
        assert config.model == "gpt-4o"

    def test_model_keywords_honors_env_var(self, monkeypatch) -> None:
        from daemon.config import LLMConfig

        self._clean(monkeypatch)
        monkeypatch.setenv("OPENAI_MODEL_KEYWORDS", "quick")
        config = LLMConfig()
        assert config.model_keywords == "quick"

    def test_model_keywords_env_var_empty_falls_back(self, monkeypatch) -> None:
        from daemon.config import LLMConfig

        self._clean(monkeypatch)
        monkeypatch.setenv("OPENAI_MODEL_KEYWORDS", "")
        config = LLMConfig()
        # Empty env var should be normalized to ``model`` by the validator.
        assert config.model_keywords == config.model


class TestDaemonConfig:
    """Tests for DaemonConfig."""

    def test_daemon_config_defaults(self):
        """Test DaemonConfig default values."""
        from daemon.config import DaemonConfig
        
        config = DaemonConfig()
        
        assert config.host == "0.0.0.0"
        assert config.port == 8079


class TestLimitsConfig:
    """Tests for LimitsConfig."""

    def test_limits_config_defaults(self):
        """Test LimitsConfig default values."""
        from daemon.config import LimitsConfig

        config = LimitsConfig()

        assert config.max_children_per_instance == 50
        assert config.instance_timeout_minutes == 60
        assert config.graph_recursion_limit == 100
        assert config.llm_concurrency == 10
        # Governor Recursion Guard (2026-08-30): default-on, K=1.
        assert config.governor_recursion_guard_enabled is True
        assert config.max_governor_ancestors == 1


class TestPersistenceConfig:
    """Tests for PersistenceConfig."""

    def test_persistence_config_defaults(self):
        """Test PersistenceConfig default values."""
        from daemon.config import PersistenceConfig
        
        config = PersistenceConfig()
        
        assert config.db_path == "./data/instances.db"
        # ``checkpointer_db_path`` was removed: the checkpointer DB path is
        # owned by ``EnsembleConfig.sqlite.checkpoints_db`` (see
        # ``daemon/persistence.py``). Persisted in ``./data/ensemble.json``.
        assert config.checkpoint_interval == 1
        assert config.checkpoint_ttl_hours == 168
        assert config.checkpoint_cleanup_interval == 24
        assert config.max_instance_history == 500


class TestAgentsConfig:
    """Tests for AgentsConfig."""

    def test_agents_config_defaults(self):
        """Test AgentsConfig default values."""
        from daemon.config import AgentsConfig
        
        config = AgentsConfig()
        
        assert config.directory == "./agents"


class TestQueueConfig:
    """Tests for QueueConfig."""

    def test_queue_config_defaults(self, monkeypatch, tmp_path):
        """Test QueueConfig default values from YAML config."""
        # Clear env var so we test YAML defaults, not environment-influenced values
        monkeypatch.delenv("QUEUE_DISCARD_ON_STARTUP", raising=False)

        # Create a minimal config file
        config_file = tmp_path / "config.yaml"
        config_file.write_text("""
queue:
  discard_on_startup: false
  llm_retry_transient_attempts: 10
  llm_retry_timeout_attempts: 3
""")
        from daemon.config import load_config

        config = load_config(str(config_file))

        assert config.queue.discard_on_startup is False
        assert config.queue.llm_retry_transient_attempts == 10
        assert config.queue.llm_retry_timeout_attempts == 3

    def test_queue_config_env_var_override(self, monkeypatch, tmp_path):
        """Test that QUEUE_DISCARD_ON_STARTUP env var takes highest priority over YAML."""
        # YAML sets discard_on_startup: false
        config_file = tmp_path / "config.yaml"
        config_file.write_text("""
queue:
  discard_on_startup: false
""")
        # But env var sets it to true
        monkeypatch.setenv("QUEUE_DISCARD_ON_STARTUP", "true")

        from daemon.config import load_config

        config = load_config(str(config_file))

        assert config.queue.discard_on_startup is True

    def test_queue_config_env_var_false_override(self, monkeypatch, tmp_path):
        """Test that QUEUE_DISCARD_ON_STARTUP=false overrides YAML true."""
        # YAML sets discard_on_startup: true
        config_file = tmp_path / "config.yaml"
        config_file.write_text("""
queue:
  discard_on_startup: true
""")
        # But env var sets it to false
        monkeypatch.setenv("QUEUE_DISCARD_ON_STARTUP", "false")

        from daemon.config import load_config

        config = load_config(str(config_file))

        assert config.queue.discard_on_startup is False
