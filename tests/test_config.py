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
            assert config.limits.max_instances == 100
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
        assert config.limits.max_instances == 100

    def test_config_with_custom_values(self):
        """Test Config with custom values."""
        config = Config(
            llm={"api_key": "custom-key", "model": "gpt-3.5-turbo"},
            daemon={"port": 9000},
            limits={"max_instances": 50},
        )
        
        assert config.llm.api_key == "custom-key"
        assert config.llm.model == "gpt-3.5-turbo"
        assert config.daemon.port == 9000
        assert config.limits.max_instances == 50

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
        
        assert config.max_instances == 100
        assert config.max_children_per_instance == 10
        assert config.instance_timeout_minutes == 60
        assert config.message_rate_limit == 60
        assert config.graph_recursion_limit == 100
        assert config.llm_concurrency == 10


class TestPersistenceConfig:
    """Tests for PersistenceConfig."""

    def test_persistence_config_defaults(self):
        """Test PersistenceConfig default values."""
        from daemon.config import PersistenceConfig
        
        config = PersistenceConfig()
        
        assert config.db_path == "./data/instances.db"
        assert config.checkpointer_db_path == "./data/checkpoints.db"
        assert config.checkpoint_interval == 1
        assert config.checkpoint_ttl_hours == 168
        assert config.checkpoint_cleanup_interval == 24
        assert config.checkpoint_max_count == 1000


class TestAgentsConfig:
    """Tests for AgentsConfig."""

    def test_agents_config_defaults(self):
        """Test AgentsConfig default values."""
        from daemon.config import AgentsConfig
        
        config = AgentsConfig()
        
        assert config.directory == "./agents"


class TestQueueConfig:
    """Tests for QueueConfig."""

    def test_queue_config_defaults(self):
        """Test QueueConfig default values."""
        from daemon.config import QueueConfig
        
        config = QueueConfig()
        
        assert config.discard_on_startup is False
        assert config.llm_retry_transient_attempts == 8
        assert config.llm_retry_timeout_attempts == 3
