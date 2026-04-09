"""Configuration loading with YAML, environment variable substitution, and Pydantic validation."""

import os
import re
from pathlib import Path
from typing import Any, Dict, Optional

import yaml
from pydantic import Field, ConfigDict, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def substitute_env_vars(value: Any) -> Any:
    """Recursively substitute environment variables in value using ${VAR:-default} syntax."""
    if isinstance(value, str):
        # Pattern matches ${VAR_NAME:-default_value} or ${VAR_NAME}
        pattern = r'\$\{([^}:]+)(?::-([^}]*))?\}'

        def replace_var(match: re.Match) -> str:
            var_name = match.group(1)
            default_value = match.group(2) if match.group(2) is not None else ""
            env_value = os.environ.get(var_name)
            return env_value if env_value is not None else default_value

        return re.sub(pattern, replace_var, value)
    elif isinstance(value, dict):
        return {k: substitute_env_vars(v) for k, v in value.items()}
    elif isinstance(value, list):
        return [substitute_env_vars(item) for item in value]
    return value


class LLMConfig(BaseSettings):
    """LLM configuration settings."""

    model_config = SettingsConfigDict(env_prefix="OPENAI_")
    
    base_url: str = Field(default="https://api.openai.com/v1")
    api_key: str = Field(default="")
    model: str = Field(default="gpt-4")
    model_title: Optional[str] = Field(default=None, description="Model for title generation (falls back to model)")
    temperature: float = Field(default=0.7)
    request_timeout: int = Field(default=660, description="Request timeout in seconds (default: 11 minutes)")
    
    @model_validator(mode="after")
    def set_title_model_fallback(self) -> "LLMConfig":
        """Ensure model_title falls back to model if not set or empty."""
        if not self.model_title:  # Handles None and empty string
            self.model_title = self.model
        return self


class DaemonConfig(BaseSettings):
    """Daemon server configuration settings."""

    model_config = SettingsConfigDict(env_prefix="DAEMON_")

    host: str = Field(default="0.0.0.0")
    port: int = Field(default=8079)


class LimitsConfig(BaseSettings):
    """Instance and rate limits configuration."""

    model_config = SettingsConfigDict(env_prefix="LIMITS_")

    max_instances: int = Field(default=100)
    max_children_per_instance: int = Field(default=10)
    instance_timeout_minutes: int = Field(default=60)
    message_rate_limit: int = Field(default=60)
    graph_recursion_limit: int = Field(default=100)
    llm_concurrency: int = Field(default=10, ge=1, description="Maximum concurrent LLM calls across all instances")


class PersistenceConfig(BaseSettings):
    """Persistence and checkpoint configuration."""

    model_config = SettingsConfigDict(env_prefix="PERSISTENCE_")

    db_path: str = Field(default="./data/instances.db")
    checkpointer_db_path: str = Field(default="./data/checkpoints.db")
    checkpoint_interval: int = Field(default=1)
    checkpoint_ttl_hours: int = Field(default=168)
    checkpoint_cleanup_interval: int = Field(default=24)
    checkpoint_max_count: int = Field(default=1000)


class QueueConfig(BaseSettings):
    """Message queue configuration settings."""

    model_config = SettingsConfigDict(env_prefix="QUEUE_")

    max_queue_size: int = Field(default=100)
    message_timeout_seconds: int = Field(default=3600)  # 1 hour
    max_retries: int = Field(default=5)
    watchdog_check_interval_seconds: int = Field(default=30)
    cleanup_completed_age_hours: int = Field(default=24)
    circuit_breaker_failure_threshold: int = Field(default=5)
    circuit_breaker_recovery_timeout_seconds: int = Field(default=300)

    # Development helper: discard all queued messages on startup
    discard_on_startup: bool = Field(default=False)

    # LLM retry configuration
    llm_max_retries: int = Field(default=3)
    llm_retry_delay_seconds: float = Field(default=10.0)
    llm_retry_exponential_base: float = Field(default=2.0)

    # Phase 3: Worker pool feature flag
    use_worker_pool: bool = Field(default=False, description="Use worker pool for message processing (Phase 3)")


class AgentsConfig(BaseSettings):
    """Agents directory configuration."""

    model_config = SettingsConfigDict(env_prefix="AGENTS_")

    directory: str = Field(default="./agents")


class CompactionConfig(BaseSettings):
    """Context compaction configuration."""

    model_config = SettingsConfigDict(env_prefix="COMPACTION_")

    enabled: bool = Field(default=True)
    threshold: float = Field(default=0.80, description="Trigger compaction when tokens exceed this fraction of context window")
    recent_message_window: int = Field(default=10, description="Number of most recent boundary GROUPS to keep intact during compaction")
    min_recent_window: int = Field(default=3, description="Hard minimum for recent window during progressive reduction")
    context_window_override: int = Field(default=0, description="Override context window size. 0 = auto-detect from model name")
    target_ratio: float = Field(default=0.40, description="Target token usage after compaction as fraction of context window")
    summarization_model: str = Field(default="", description="Model to use for summarization. Empty = use session model")
    min_messages_before_compaction: int = Field(default=10, description="Minimum number of messages before compaction is considered")
    summarization_chunk_threshold: float = Field(default=0.60, description="Fraction of context window above which summarization uses chunking")


class Config(BaseSettings):
    """Main configuration class aggregating all sections."""

    model_config = SettingsConfigDict(env_prefix="")

    llm: LLMConfig = Field(default_factory=LLMConfig)
    daemon: DaemonConfig = Field(default_factory=DaemonConfig)
    limits: LimitsConfig = Field(default_factory=LimitsConfig)
    persistence: PersistenceConfig = Field(default_factory=PersistenceConfig)
    agents: AgentsConfig = Field(default_factory=AgentsConfig)
    queue: QueueConfig = Field(default_factory=QueueConfig)
    compaction: CompactionConfig = Field(default_factory=CompactionConfig)


def load_config(config_path: Optional[str] = None) -> Config:
    """
    Load configuration from YAML file with environment variable substitution.

    Args:
        config_path: Path to config file. If None, uses ENSEMBLE_CONFIG env var
                    or defaults to ./config.yaml

    Returns:
        Validated Config instance

    Raises:
        FileNotFoundError: If config file does not exist
        ValueError: If config file is invalid
    """
    # Determine config file path
    if config_path is None:
        config_path = os.environ.get("ENSEMBLE_CONFIG", "./config.yaml")

    config_file = Path(config_path)

    if not config_file.exists():
        raise FileNotFoundError(
            f"Config file not found: {config_path}. "
            "Set ENSEMBLE_CONFIG environment variable or create config.yaml"
        )

    # Read and parse YAML
    try:
        with open(config_file, "r") as f:
            raw_config = yaml.safe_load(f)
    except yaml.YAMLError as e:
        raise ValueError(f"Failed to parse config file: {e}")

    if raw_config is None:
        raise ValueError("Config file is empty")

    # Substitute environment variables
    processed_config = substitute_env_vars(raw_config)

    # Build nested dict for Pydantic
    config_dict: Dict[str, Any] = {}

    if "llm" in processed_config:
        config_dict["llm"] = processed_config["llm"]
    if "daemon" in processed_config:
        config_dict["daemon"] = processed_config["daemon"]
    if "limits" in processed_config:
        config_dict["limits"] = processed_config["limits"]
    if "persistence" in processed_config:
        config_dict["persistence"] = processed_config["persistence"]
    if "agents" in processed_config:
        config_dict["agents"] = processed_config["agents"]
    if "queue" in processed_config:
        config_dict["queue"] = processed_config["queue"]
    if "compaction" in processed_config:
        config_dict["compaction"] = processed_config["compaction"]

    # Create and validate config
    return Config(**config_dict)


# Convenience function for getting the config
def get_config(config_path: Optional[str] = None) -> Config:
    """Get the configuration, loading it if not already loaded."""
    return load_config(config_path)
