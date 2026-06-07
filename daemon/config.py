"""Configuration loading with YAML, environment variable substitution, and Pydantic validation."""

import os
import re
from pathlib import Path
from typing import Annotated, Any, Dict

import yaml
from pydantic import Field, ConfigDict, model_validator, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

from .constants import (
    CHECKPOINT_TTL_HOURS,
    CHECKPOINT_CLEANUP_INTERVAL_HOURS,
    MAX_INSTANCE_HISTORY,
    MAINTENANCE_CHECK_INTERVAL_MINUTES,
)


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
    model_title: str | None = Field(default=None, description="Model for title generation (falls back to model)")
    model_keywords: str | None = Field(
        default=None,
        description=(
            "Model for keyword extraction from outgoing opencode prompts "
            "(falls back to model). Set to 'quick' to mirror the explorer agent's "
            "llm_model for cost/speed."
        ),
    )
    model_vision: str | None = Field(default=None, description="Model for vision/image processing (e.g., gpt-4o)")
    temperature: float = Field(default=0.7)
    request_timeout: int = Field(default=660, description="Request timeout in seconds (default: 11 minutes)")

    # Models for which reasoning_content from a previous turn must be echoed
    # back in subsequent assistant messages. Substring match is performed
    # against the model name (case-insensitive). Default: DeepSeek (required
    # by their thinking-mode API for tool-calling turns).
    # Override via OPENAI_REASONING_ECHO_MODELS env var, e.g.
    #   OPENAI_REASONING_ECHO_MODELS="deepseek,glm,zai"
    # The NoDecode annotation prevents pydantic-settings from auto-JSON-decoding
    # the env value, so our field_validator can handle comma-separated input.
    reasoning_echo_models: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["deepseek"],
        description=(
            "Model name patterns (case-insensitive substring match) for which "
            "reasoning_content must be echoed back in multi-turn conversations. "
            "Default: ['deepseek']."
        ),
    )

    @field_validator("reasoning_echo_models", mode="before")
    @classmethod
    def _parse_reasoning_echo_models(cls, value: Any) -> Any:
        """Accept comma-separated strings (and JSON arrays) from env / YAML.

        The ``NoDecode`` annotation prevents pydantic-settings from
        auto-parsing env values, so we handle both forms here:
          - ``"deepseek,glm"`` → ``["deepseek", "glm"]``
          - ``'["deepseek", "glm"]'`` → ``["deepseek", "glm"]``
          - ``["deepseek", "glm"]`` → unchanged (passthrough)
          - ``""`` or whitespace → ``[]``
        """
        if isinstance(value, str):
            stripped = value.strip()
            if not stripped:
                return []
            # JSON array form
            if stripped.startswith("[") and stripped.endswith("]"):
                import json
                try:
                    parsed = json.loads(stripped)
                    if isinstance(parsed, list):
                        return [str(item).strip() for item in parsed if str(item).strip()]
                except json.JSONDecodeError:
                    pass
                # Fall through to comma-split
            # Comma-separated form (also covers "a, b , c")
            return [item.strip() for item in stripped.split(",") if item.strip()]
        return value

    @model_validator(mode="after")
    def set_title_model_fallback(self) -> "LLMConfig":
        """Ensure model_title and model_keywords fall back to model if not set or empty."""
        if not self.model_title:  # Handles None and empty string
            self.model_title = self.model
        if not self.model_keywords:  # Handles None and empty string
            self.model_keywords = self.model
        return self


class DaemonConfig(BaseSettings):
    """Daemon server configuration settings."""

    model_config = SettingsConfigDict(env_prefix="DAEMON_")

    host: str = Field(default="0.0.0.0")
    port: int = Field(default=8079)


class LimitsConfig(BaseSettings):
    """Instance and rate limits configuration."""

    model_config = SettingsConfigDict(env_prefix="LIMITS_")

    # Unused: global limit removed, per-parent limit used instead
    max_instances: int = Field(default=100)
    max_children_per_instance: int = Field(default=50)
    instance_timeout_minutes: int = Field(default=60)
    message_rate_limit: int = Field(default=60)
    graph_recursion_limit: int = Field(default=100)
    llm_concurrency: int = Field(default=10, ge=1, description="Maximum concurrent LLM calls across all instances")


class PersistenceConfig(BaseSettings):
    """Persistence and checkpoint configuration."""

    model_config = SettingsConfigDict(env_prefix="PERSISTENCE_")

    db_path: str = Field(default="./data/instances.db")
    # NOTE: The historical ``checkpointer_db_path`` field has been removed.
    # The runtime checkpointer path is owned by ``EnsembleConfig.sqlite.checkpoints_db``
    # and read in ``daemon.persistence.get_checkpointer``. The lifespan in
    # ``daemon/api.py`` resolves the data directory from ``ENSEMBLE_DATA_DIR``
    # (with a ``DATA_DIR`` fallback) before loading ``ensemble.json``, so
    # there is no longer a second config knob to keep in sync.
    checkpoint_interval: int = Field(default=1)
    checkpoint_ttl_hours: int = Field(default=CHECKPOINT_TTL_HOURS)
    checkpoint_cleanup_interval: int = Field(default=CHECKPOINT_CLEANUP_INTERVAL_HOURS)
    maintenance_check_interval_minutes: int = Field(default=MAINTENANCE_CHECK_INTERVAL_MINUTES)
    max_instance_history: int = Field(default=MAX_INSTANCE_HISTORY)


class QueueConfig(BaseSettings):
    """Message queue configuration settings."""

    model_config = SettingsConfigDict(env_prefix="QUEUE_")

    # Development helper: discard all queued messages on startup
    # Note: This field is handled specially in load_config to ensure env var
    # QUEUE_DISCARD_ON_STARTUP takes highest priority over YAML config.
    discard_on_startup: bool | None = None

    # LLM retry configuration — per error category
    # Transient errors (500/502/503/429): fail fast, more retries fit in time budget
    llm_retry_transient_attempts: int = Field(default=10)  # ~17 min total retry time
    # Timeout errors: each attempt costs up to request_timeout (660s = 11 min)
    llm_retry_timeout_attempts: int = Field(default=3)


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


class ServicesConfig(BaseSettings):
    """Worker pool and background service configuration."""

    model_config = SettingsConfigDict(env_prefix="SERVICES_")

    worker_poll_interval: float = Field(
        default=0.5,
        description="How often workers poll for tasks (seconds). Lower = more responsive but more CPU/DB load."
    )
    stale_task_recovery_interval: int = Field(
        default=60,
        description="How often to check for stale tasks and recover them (seconds)."
    )
    
    # Task timeout and retry configuration
    task_timeout_minutes: float = Field(
        default=60.0,
        description="Maximum time a task can run before being cancelled (minutes). Set to 0 to disable timeout."
    )
    max_task_retries: int = Field(
        default=3,
        description="Maximum number of retry attempts for failed/timed-out tasks. Set to 0 to disable retries."
    )
    task_retry_backoff_base: int = Field(
        default=60,
        description="Base delay for exponential backoff between retries (seconds). Actual delay: base * 2^retry_count."
    )
    task_retry_backoff_max: int = Field(
        default=3600,
        description="Maximum delay between retries (seconds). Default: 1 hour."
    )
    stale_task_cancel_grace_seconds: int = Field(
        default=10,
        description="Seconds to wait for graceful shutdown after requesting task cancellation in stale task recovery."
    )
    stale_task_recovery_threshold_minutes: int = Field(
        default=5,
        description=(
            "Minutes after which a RUNNING task is considered stale and "
            "recovered (transitioned to CANCELLED, with a retry task). "
            "Sized to limit how long sibling tasks for the same instance "
            "are blocked when a worker crashes (Fix B makes sibling-block "
            "the dominant visible symptom). Lower than task_timeout_minutes."
        ),
    )
    task_heartbeat_interval_seconds: int = Field(
        default=30,
        description=(
            "How often the per-worker heartbeat thread updates a task's "
            "last_heartbeat_at column while the task is in flight. The "
            "recovery service compares last_heartbeat_at against "
            "stale_task_recovery_threshold_minutes; a live task's heartbeat "
            "is at most one interval old, a crashed worker's heartbeat is "
            "the time of the last successful update. Keep this at least "
            "5x smaller than the stale threshold so a few missed beats "
            "don't false-positive flag live tasks."
        ),
    )
    graph_timeout_minutes: float = Field(
        default=55.0,
        description="Hard timeout for LangGraph execution via MainLoopBridge (minutes). Set to 0 to disable."
    )


class JobSystemConfig(BaseSettings):
    """Configuration for the job system improvements."""

    model_config = SettingsConfigDict(env_prefix="ENSEMBLE_JOB_SYSTEM_")
    
    default_max_retries: int = Field(default=3, description="Default max retry attempts for failed jobs")
    retry_backoff_base_seconds: int = Field(default=60, description="Base delay in seconds for exponential backoff")
    retry_backoff_max_seconds: int = Field(default=3600, description="Maximum delay in seconds for retry backoff")
    retry_backoff_multiplier: float = Field(default=2.0, description="Exponential multiplier for backoff (2^retry_count * multiplier)")
    dlq_enabled: bool = Field(default=True, description="Enable dead letter queue functionality")
    event_dispatch_enabled: bool = Field(default=True, description="Enable event-based job dispatch")
    observer_health_check_interval_seconds: int = Field(default=300, description="Interval in seconds for observer health checks")
    idempotency_key_ttl_hours: int = Field(default=24, description="TTL in hours for idempotency key deduplication")
    job_retry_scheduler_enabled: bool | None = Field(default=None, description="Enable background retry scheduler. None/empty = disabled.")


class McpPoolConfig(BaseSettings):
    """MCP warm-up connection pool configuration."""

    model_config = SettingsConfigDict(env_prefix="MCP_POOL_")

    enabled: bool = Field(default=True, description="Enable MCP warm-up pool for faster tool access")
    default_pool_size: int = Field(default=1, ge=1, description="Default number of pre-warmed connections per server")
    servers: dict[str, int] = Field(
        default_factory=dict,
        description="Per-server pool size overrides (server_name → pool_size)"
    )
    health_check_interval: int = Field(default=60, ge=10, description="Health check interval in seconds")
    health_check_timeout: int = Field(default=5, ge=1, description="Health check timeout per connection in seconds")


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
    services: ServicesConfig = Field(default_factory=ServicesConfig)
    job_system: JobSystemConfig = Field(default_factory=JobSystemConfig)
    mcp_pool: McpPoolConfig = Field(default_factory=McpPoolConfig)


def load_config(config_path: str | None = None) -> Config:
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
        with open(config_file, "r", encoding="utf-8") as f:
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

    # Handle queue config with env var priority for discard_on_startup
    queue_config: Dict[str, Any] = {}
    if "queue" in processed_config:
        queue_config = processed_config["queue"].copy()

    # Env var QUEUE_DISCARD_ON_STARTUP has highest priority
    if "QUEUE_DISCARD_ON_STARTUP" in os.environ:
        env_val = os.environ["QUEUE_DISCARD_ON_STARTUP"].lower()
        queue_config["discard_on_startup"] = env_val in ("true", "1", "yes")

    config_dict["queue"] = queue_config

    # Handle persistence config - env vars take priority over YAML
    # This allows dev.sh to override paths via PERSISTENCE_DB_PATH.
    persistence_config: Dict[str, Any] = {}
    if "persistence" in processed_config:
        persistence_config = processed_config["persistence"].copy()
    if "PERSISTENCE_DB_PATH" in os.environ:
        persistence_config["db_path"] = os.environ["PERSISTENCE_DB_PATH"]
    else:
        persistence_config.setdefault("db_path", "./data/instances.db")
    # ``checkpointer_db_path`` was removed (see PersistenceConfig above).
    # Silently drop it from the YAML dict so old configs keep loading.
    persistence_config.pop("checkpointer_db_path", None)
    config_dict["persistence"] = persistence_config

    if "compaction" in processed_config:
        config_dict["compaction"] = processed_config["compaction"]
    if "services" in processed_config:
        config_dict["services"] = processed_config["services"]
    if "job_system" in processed_config:
        config_dict["job_system"] = processed_config["job_system"]
    if "mcp_pool" in processed_config:
        config_dict["mcp_pool"] = processed_config["mcp_pool"]

    # Create and validate config
    return Config(**config_dict)


# Convenience function for getting the config
def get_config(config_path: str | None = None) -> Config:
    """Get the configuration, loading it if not already loaded."""
    return load_config(config_path)
