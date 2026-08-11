"""Configuration loading with YAML, environment variable substitution, and Pydantic validation."""

import json
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


def _parse_csv_or_json_list(value: Any) -> Any:
    """Parse a comma-separated string or JSON-array string into a list[str].

    Accepts:
      - ``"gpt-4,gpt-4o"`` → ``["gpt-4", "gpt-4o"]`` (CSV)
      - ``'["gpt-4","gpt-4o"]'`` → ``["gpt-4", "gpt-4o"]`` (JSON array)
      - ``["gpt-4", " gpt-4o "]`` → ``["gpt-4", "gpt-4o"]`` (list — each
        entry stripped; falsy/whitespace-only entries filtered)
      - ``""`` or whitespace → ``[]``
      - ``"[oops"`` (malformed JSON) → falls through to CSV split

    Regression note: list inputs were previously returned unchanged, so a
    YAML entry like ``"gpt-4 "`` (trailing space) would be stored verbatim
    and never match a stripped candidate ``"gpt-4"`` — silently rejecting
    valid models. Fix 3 strips each list entry.
    """
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return []
        if stripped.startswith("[") and stripped.endswith("]"):
            try:
                parsed = json.loads(stripped)
                if isinstance(parsed, list):
                    return [str(i).strip() for i in parsed if str(i).strip()]
            except json.JSONDecodeError:
                pass
        return [i.strip() for i in stripped.split(",") if i.strip()]
    if isinstance(value, list):
        # List inputs come from YAML/JSON parsed structures — strip each
        # entry to align with the string-input path so trailing/leading
        # whitespace doesn't cause silent model-rejection mismatches.
        return [str(item).strip() for item in value if str(item).strip()]
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
    request_timeout: int = Field(default=610, description="Request timeout in seconds (default: 11 minutes)")

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

        Delegates to ``_parse_csv_or_json_list`` for the shared parsing logic.
        The ``NoDecode`` annotation prevents pydantic-settings from
        auto-parsing env values, so we handle both forms here:
          - ``"deepseek,glm"`` → ``["deepseek", "glm"]``
          - ``'["deepseek", "glm"]'`` → ``["deepseek", "glm"]``
          - ``["deepseek", "glm"]`` → unchanged (passthrough)
          - ``""`` or whitespace → ``[]``
        """
        return _parse_csv_or_json_list(value)

    # Models allowed as instance model overrides at spawn time. Exact match
    # (case-insensitive) is performed against the override model name;
    # a match against ANY entry is sufficient. Empty list = all models
    # allowed (no restriction). Override via OPENAI_ALLOWED_MODELS env var,
    # e.g.   OPENAI_ALLOWED_MODELS="gpt-4,gpt-4o"
    # The NoDecode annotation prevents pydantic-settings from auto-JSON-decoding
    # the env value, so our field_validator can handle comma-separated input.
    allowed_models: Annotated[list[str], NoDecode] = Field(
        default_factory=list,
        description=(
            "Allowed model names (case-insensitive exact match) for instance "
            "model overrides at spawn time. Empty list = all models allowed "
            "(no restriction). Default: []."
        ),
    )

    @field_validator("allowed_models", mode="before")
    @classmethod
    def _parse_allowed_models(cls, value: Any) -> Any:
        """Accept comma-separated strings (and JSON arrays) from env / YAML.

        Delegates to ``_parse_csv_or_json_list`` for the shared parsing logic.
        The ``NoDecode`` annotation prevents pydantic-settings from
        auto-parsing env values, so we handle both forms here:
          - ``"gpt-4,gpt-4o"`` → ``["gpt-4", "gpt-4o"]``
          - ``'["gpt-4", "gpt-4o"]'`` → ``["gpt-4", "gpt-4o"]``
          - ``["gpt-4", "gpt-4o"]`` → unchanged (passthrough)
          - ``""`` or whitespace → ``[]``
        """
        return _parse_csv_or_json_list(value)

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

    # Safe "backlog clear" on startup. When enabled, only UNSTARTED /
    # terminal work is discarded (PENDING tasks + their messages);
    # RUNNING (in-flight) and PAUSED (resumable) tasks — and the
    # messages backing them — are preserved, so a paused instance
    # still blocks system_defer_queue and can still be resumed across a
    # restart. Safe to leave enabled in dev for a clean backlog slate.
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
    context_window_overrides: dict[str, int] = Field(
        default_factory=dict,
        description=(
            "Per-model context window overrides (model_name_substring -> tokens). "
            "Substring match against the active model name; longest key wins. "
            "Takes priority over the built-in MODEL_CONTEXT_LIMITS registry. "
            "Example: {'vision': 16385} caps any model name containing 'vision'."
        ),
    )

    @field_validator("context_window_overrides")
    @classmethod
    def _validate_overrides(cls, v: dict[str, int]) -> dict[str, int]:
        """Reject empty keys and non-positive values to fail fast on bad config."""
        cleaned: dict[str, int] = {}
        for key, value in v.items():
            if not isinstance(key, str) or not key.strip():
                raise ValueError(
                    f"context_window_overrides keys must be non-empty strings, got {key!r}"
                )
            if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
                raise ValueError(
                    f"context_window_overrides[{key!r}] must be a positive integer, got {value!r}"
                )
            cleaned[key] = value
        return cleaned
    context_window_default: int = Field(
        default=0,
        ge=0,
        description=(
            "Fallback context window used when neither context_window_overrides "
            "nor the built-in MODEL_CONTEXT_LIMITS registry match the active model. "
            "0 = fall through to the hard-coded DEFAULT_CONTEXT_LIMIT (180k)."
        ),
    )
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
        default=125.0,
        description=(
            "Maximum time a task can run before being cancelled (minutes). "
            "This is the OUTER ceiling enforced via CancellationToken; "
            "should be >= graph_timeout_minutes + small grace. Set to 0 to "
            "disable timeout."
        )
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
        default=30,
        description=(
            "Seconds to wait for graceful shutdown after requesting task "
            "cancellation in stale task recovery. Increased from 10s to 30s "
            "so a long-running graph can flush its final checkpoint token "
            "before the recovery sweeper force-cancels and creates a retry."
        ),
    )
    stale_task_recovery_threshold_minutes: int = Field(
        default=10,
        description=(
            "Minutes after which a RUNNING task is considered stale and "
            "recovered (transitioned to CANCELLED, with a retry task). "
            "Sized to limit how long sibling tasks for the same instance "
            "are blocked when a worker crashes (Fix B makes sibling-block "
            "the dominant visible symptom). Lower than task_timeout_minutes. "
            "Increased from 5 to 10 min to accommodate the longer 2h graph "
            "ceiling; the task's heartbeat is still refreshed every 30s so a "
            "live task's heartbeat is at most one interval old."
        ),
    )
    # Phase 3 (defer-seam bugfix, F5/F10) — periodic drift
    # reconciler interval. The reconciler detects and repairs
    # ``job_queue_items`` ↔ ``task`` drift states that arise at
    # runtime (P1 stuck pending, F10 zombie task). Bypasses the
    # ``MaintenanceService._is_idle`` gate — drift appears *during*
    # active work, which is precisely when the idle-gated loop skips.
    drift_reconcile_interval_seconds: int = Field(
        default=300,
        description=(
            "Interval (seconds) for the periodic dual-table drift "
            "reconciler (F5/F10). Default 300s (5min) — drift is rare "
            "so a slower cadence keeps the logs quiet."
        ),
    )
    drift_reconcile_min_pending_age_seconds: int = Field(
        default=300,
        description=(
            "Minimum age (seconds) for a PENDING task to be "
            "considered drift-eligible by the reconciler. Tasks "
            "younger than this are left alone to avoid racing with "
            "a freshly-enqueued worker. Default 300s = 5 minutes."
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
    lease_heartbeat_interval_seconds: float = Field(
        default=30.0,
        description=(
            "How often the in-process Execution Gate heartbeat task "
            "refreshes a lease's heartbeat_at column while a "
            "graph.astream call is in flight. Defaults to match "
            "task_heartbeat_interval_seconds. Keep this at least "
            "5-10x smaller than DEFAULT_STALE_LEASE_SECONDS (300 s) "
            "so a few missed beats don't false-positive flag a live "
            "lease as stale."
        ),
    )
    graph_timeout_minutes: float = Field(
        default=120.0,
        description=(
            "Hard timeout for LangGraph execution via MainLoopBridge (minutes). "
            "Increased from 55 to 120 min so long-running tasks (e.g. multi-"
            "phase refactors that spawn several explorer children and run "
            "dozens of LLM turns) can complete without hitting the safety "
            "net. The CancellationToken path (task_timeout_minutes, "
            "default 125 min) remains 5 min longer so a graceful "
            "OperationCancelledError usually fires before the thread-side "
            "TimeoutError; if the coroutine still completes within a few "
            "seconds of the safety timeout, the worker_pool's "
            "_handle_cancellation path now detects the already-COMPLETED "
            "message and skips the retry. Set to 0 to disable."
        ),
    )


class JobSystemConfig(BaseSettings):
    """Configuration for the job system.

    The DependencyBus is the SOLE completion authority for parent-waits-for-children.
    There is no fallback or rollback path; the CorrelationManager was fully removed.
    """

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

    # Phase 5 cutover: every public/external entry point creates a
    # JobItem (``job_type='message'``) alongside the Task row via
    # :meth:`InstanceManager.enqueue_message_job`. The raw
    # :meth:`InstanceManager.enqueue_message` path remains as
    # internal-only (reports, nudges, ``[JOB_EVENT]`` delivery,
    # compaction, ``invoke_and_wait``) and is intentionally invisible
    # to the WorkResolver facade.

    # Phase 7: the WorkResolverService is the only read path. Legacy
    # per-table primitives (``get_job`` / ``list_jobs`` / ``cancel_job``)
    # are retained for internal callers but no longer gated by a config
    # flag.


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
    tool_call_timeout: int = Field(
        default=120,
        ge=0,
        le=3600,
        description="Timeout in seconds for individual MCP tool call executions. "
        "Applies to all transport types (STDIO, SSE, Streamable HTTP). "
        "Set to 0 to disable timeout.",
    )


class EmbeddingConfig(BaseSettings):
    """Shared embedding configuration for all subsystems (skills, blueprints, future).

    Subclasses set their own ``env_prefix`` (e.g. ``SKILL_EVOLUTION_``) and may
    override individual field defaults. The ``_shared_embedding_fallback``
    validator applies the shared ``EMBEDDING_*`` environment variables as a
    fallback when no prefix-specific env var was set and the field still equals
    its base default (``None`` for optional fields, ``1536`` for
    ``embedding_dimensions``).

    Precedence (highest → lowest):

    1. ``{SUBSYSTEM_PREFIX}_EMBEDDING_*`` env var
       (e.g. ``SKILL_EVOLUTION_EMBEDDING_MODEL``)
    2. Shared ``EMBEDDING_*`` env var
    3. Field default

    Subclass non-None defaults (e.g. ``SkillEvolutionConfig.embedding_model =
    "text-embedding-3-small"``) are preserved — the operator who wants the
    shared value must set the prefix-specific var. Rationale: a subclass that
    picks a concrete default is asserting a deliberate choice; silently
    shadowing it with ``EMBEDDING_*`` would be surprising.
    """

    embedding_model: str | None = Field(
        default=None,
        description=(
            "Embedding model name. Subclasses may override the default "
            "(e.g. SkillEvolutionConfig uses 'text-embedding-3-small')."
        ),
    )
    embedding_dimensions: int = Field(
        default=1536,
        description=(
            "Embedding vector dimensions. OpenAI text-embedding-3-* uses 1536; "
            "older models (text-embedding-ada-002) also 1536."
        ),
    )
    embedding_base_url: str | None = Field(
        default=None,
        description=(
            "Override the embeddings API base URL. When unset, embedding calls "
            "fall back to LLMConfig.base_url."
        ),
    )
    embedding_api_key: str | None = Field(
        default=None,
        description=(
            "Override the embeddings API key. When unset, embedding calls "
            "fall back to LLMConfig.api_key."
        ),
    )

    @model_validator(mode="before")
    @classmethod
    def _shared_embedding_fallback(cls, values: Any) -> Any:
        """Apply shared ``EMBEDDING_*`` env vars as fallback.

        Runs after pydantic-settings has merged prefix-specific env vars and
        field defaults into ``values``. We inject the shared ``EMBEDDING_*``
        env var only when the field still matches its BASE default:

        * ``None`` for optional fields (``embedding_model``,
          ``embedding_base_url``, ``embedding_api_key``)
        * ``1536`` for ``embedding_dimensions`` (never ``None``)

        See class docstring for the full precedence rules and rationale.

        NOTE on pydantic-settings behavior: when the subclass sets its own
        ``env_prefix`` (e.g. ``SKILL_EVOLUTION_``), pydantic-settings does
        NOT look up unprefixed ``EMBEDDING_*`` env vars. So ``values`` may
        arrive here as an empty dict — the subclass default has not been
        merged yet. We therefore must consult the SUBCLASS's own field
        defaults (``cls.model_fields[field].default``) to decide whether
        the shared fallback should apply.
        """
        if not isinstance(values, dict):
            return values

        # Resolve the EFFECTIVE default for each field on the current class
        # (which may be a subclass that re-declared the field with a non-None
        # default). Pydantic-settings doesn't merge subclass defaults into
        # ``values`` before this validator runs, so we must read them
        # ourselves.
        effective_defaults = {
            name: cls.model_fields[name].default
            for name in ("embedding_model", "embedding_dimensions",
                         "embedding_base_url", "embedding_api_key")
            if name in cls.model_fields
        }

        # Optional fields: fallback only when the field is absent from
        # ``values`` AND the subclass default is None. This preserves
        # subclass non-None defaults like
        # ``SkillEvolutionConfig.embedding_model = "text-embedding-3-small"``
        # while still resolving shared ``EMBEDDING_*`` for subclasses (like
        # ``BlueprintConfig``) that default to None.
        for field_key, env_key in (
            ("embedding_model", "EMBEDDING_MODEL"),
            ("embedding_base_url", "EMBEDDING_BASE_URL"),
            ("embedding_api_key", "EMBEDDING_API_KEY"),
        ):
            if field_key in values:
                # Prefix-specific env var already populated this field;
                # it wins over the shared fallback.
                continue
            if effective_defaults.get(field_key) is not None:
                # Subclass has a non-None default — respect it; the operator
                # who wants the shared value must set the prefix-specific
                # var explicitly.
                continue
            shared = os.environ.get(env_key)
            if shared:
                values[field_key] = shared

        # embedding_dimensions defaults to 1536 (int, never None) — fallback
        # when value is still the default AND the subclass hasn't overridden
        # it. Malformed env vars are silently ignored (the field default
        # stands).
        if "embedding_dimensions" not in values:
            if effective_defaults.get("embedding_dimensions") == 1536:
                shared = os.environ.get("EMBEDDING_DIMENSIONS")
                if shared:
                    try:
                        values["embedding_dimensions"] = int(shared)
                    except ValueError:
                        pass

        return values


class SkillEvolutionConfig(EmbeddingConfig):
    """Configuration for the skill evolution system."""

    model_config = SettingsConfigDict(env_prefix="SKILL_EVOLUTION_")

    # Embedding fields are inherited from EmbeddingConfig. The
    # ``embedding_model`` default is re-declared here to preserve the
    # existing string default + non-optional type (the test at
    # tests/test_skill_evolution_config.py:35 pins this to
    # "text-embedding-3-small"). ``embedding_dimensions``,
    # ``embedding_base_url``, and ``embedding_api_key`` use the base
    # defaults (1536, None, None) — unchanged from the pre-refactor
    # behavior.
    embedding_model: str = Field(default="text-embedding-3-small")

    # Evolution models
    evolution_model: str | None = Field(default=None)  # Falls back to main model
    analysis_model: str | None = Field(default=None)  # Cheap model for Tier 2

    # Injection
    max_inject_skills: int = Field(default=2)
    min_score_full_inject: float = Field(default=0.7)
    min_score_low_match: float = Field(default=0.3)
    bm25_top_k: int = Field(default=10)
    llm_select_top_k: int = Field(default=5)

    # Triggers
    default_task_count_threshold: int = Field(default=20)
    default_daily_scan_hour: int = Field(default=3)  # 3 AM

    # Phase 4: how often the ``skill_metric_scan`` maintenance job
    # runs (hours). Defaults to daily (24h). The actual run-time gate
    # lives in ``MaintenanceService._is_idle`` so the scan waits
    # until the system has no in-flight work.
    metric_scan_interval_hours: float = Field(default=24.0)

    # A/B testing
    ab_sample_size: int = Field(default=20)  # Changed from 10 (D15 — silent upgrade)
    ab_min_difference: float = Field(default=0.15)  # Loser must be at least 15% worse
    max_extensions: int = Field(default=3)

    # ── Multi-metric composite scoring (Milestone 2 Phase 3) ──
    # Weights for the 5-metric composite A/B winner score.
    # All weights should sum to 1.0.
    ab_weight_completion: float = Field(default=0.35)
    ab_weight_applied: float = Field(default=0.20)
    ab_weight_efficiency: float = Field(default=0.20)
    ab_weight_fallback: float = Field(default=0.15)
    ab_weight_speed: float = Field(default=0.10)

    # Capture
    capture_min_iterations: int = Field(default=5)
    capture_min_duration_seconds: int = Field(default=60)


class LoopBreakerConfig(BaseSettings):
    """Configuration for the general hallucination loop breaker.

    The loop breaker detects consecutive identical tool-call patterns (any
    tool, parallel-aware) and triggers a repair cycle that removes the
    repetitive messages and re-injects a fresh summary. Detection runs in
    ``agent_node`` before the LLM call; repair is wired in Phase 3.

    State storage is RAM-only (``InstanceManager._loop_breaker_state``)
    following the existing ``_gii_throttle`` pattern — see
    ``.agents/shared/planning/general-hallucination-fix/decisions.md`` D4.
    """

    model_config = SettingsConfigDict(env_prefix="LOOP_BREAKER_")

    enabled: bool = Field(default=True, description="Enable general hallucination loop breaker")
    threshold: int = Field(default=3, description="Consecutive identical tool calls required to trigger detection")
    max_repairs: int = Field(default=3, description="Maximum repair attempts per instance before giving up")
    summarization_timeout_seconds: int = Field(default=120, description="Timeout for the repair LLM summarization call")
    excluded_tools: list[str] = Field(default_factory=list, description="Tool names to skip during detection (e.g. legitimately polled resources)")


class ReportRepairConfig(BaseSettings):
    """Configuration for unhappy-path report repair.

    When a child instance's last assistant message is much shorter than
    its earlier messages, the LLM repair node re-composed the report from
    the last 3 assistant messages. If the LLM fails or times out, the 3
    messages are combined into one report.

    The factor-5 size ratio threshold (default) is an accuracy guard
    to prevent false positives on legitimately-concise reports —
    an earlier message must be at least 5× the last message's word
    count before repair is triggered. Was 2.0 prior to 2026-08-11; a
    prod incident (governor 36-word final message after a 143-word
    prior turn) showed that factor 2 fired on intentional short
    reports. Factor 5 absorbs intentional concision while still
    catching mid-sentence truncation.
    """

    model_config = SettingsConfigDict(env_prefix="REPORT_REPAIR_")

    enabled: bool = Field(default=True, description="Enable unhappy-path report repair")
    # Factor-5 accuracy guard (was 2.0 pre-2026-08-11). Intentional short
    # reports (e.g., governor's 36-word final message after a 143-word
    # prior turn) are NOT repaired — only mid-sentence truncation is.
    size_ratio_threshold: float = Field(default=5.0, ge=1.0, description="Word-count ratio (earlier/last) that triggers repair")
    # Agent IDs whose reports are NEVER repaired. Exploration agents
    # (wanderer, explorer) naturally produce short, legitimately-concise
    # reports — repairing them wastes LLM time and corrupts the report
    # with hallucinated content. Override via REPORT_REPAIR_EXCLUDED_AGENTS
    # env var (comma-separated) to add or remove IDs.
    repair_excluded_agents: set[str] = Field(
        default_factory=lambda: {"wanderer", "explorer"},
        description="Agent IDs whose reports are never repaired (exploration agents naturally produce short reports)",
    )
    # W2: tighter default timeout (30s instead of 120s) — repair should be
    # fast; on timeout we fall back to combine. 120s is excessive given the
    # prompt is bounded to recent messages.
    timeout_seconds: int = Field(default=30, description="Timeout for the repair LLM call")
    # S2: validator — must be >=1 message.
    lookback_messages: int = Field(default=5, ge=1, description="Number of recent assistant messages to pass to LLM repair")


class LanguageConfig(BaseSettings):
    """Language check configuration."""

    model_config = SettingsConfigDict(env_prefix="LANGUAGE_")

    check_enabled: bool = Field(
        default=False,
        description="Enable language check node — adds up to 3× LLM cost per turn when wrong language detected. Set to true to enable."
    )


class VSCodeConfig(BaseSettings):
    """Configuration for the VS Code Server editor integration."""

    model_config = SettingsConfigDict(env_prefix="VSCODE_")

    allow_remote: bool = Field(default=False)  # C1: default to localhost-only binding
    binary_path: str | None = Field(default=None)  # null = use PATH lookup (shutil.which)
    user_data_dir: str | None = Field(default=None)  # null = data/vscode-user-data
    extensions: list[str] = Field(default_factory=list)  # extensions to pre-install


class BlueprintConfig(EmbeddingConfig):
    """Configuration for the Project Blueprint matching system.

    Defaults: bm25_weight (alpha) = 0.4, vector_weight (beta) = 0.6,
    match_threshold = 0.30, max_results = 5. Tuned in Phase 6.

    Embedding fields are inherited from :class:`EmbeddingConfig` and
    default to ``None`` / ``1536`` — matching the pre-refactor behavior.
    The ``_shared_embedding_fallback`` validator resolves shared
    ``EMBEDDING_*`` env vars when the prefix-specific ``BLUEPRINT_EMBEDDING_*``
    is unset.
    """

    model_config = SettingsConfigDict(env_prefix="BLUEPRINT_")

    bm25_weight: float = Field(default=0.4)
    vector_weight: float = Field(default=0.6)
    match_threshold: float = Field(default=0.30)
    max_results: int = Field(default=5)
    # G8: statuses eligible for matcher loading. The repository's
    # ``search_candidates`` uses a hardcoded ``"published"`` filter
    # for now; this option is reserved for future flexibility
    # (e.g. phased rollouts, ``"review"`` for human approval
    # checkpoints). Drafts are excluded by default.
    matchable_statuses: list[str] = Field(
        default_factory=lambda: ["published"],
        description="Blueprint statuses eligible for matching (G8). Drafts excluded by default.",
    )
    # C7 / Phase 3: gate for automated blueprint triggers (daily scan,
    # post-experience sidecars). Manual triggers always work. Default ON —
    # set BLUEPRINT_AUTO_REBUILD_ENABLED=false to disable.
    auto_rebuild_enabled: bool = Field(
        default=True,
        description="Gate for automated blueprint triggers (daily scan, "
                    "post-experience). Manual triggers always work. "
                    "Default ON — set BLUEPRINT_AUTO_REBUILD_ENABLED=false to disable.",
    )
    # Phase 4 / Doc Maintenance: opt-in gate for doc-maintainer workers.
    # Two flags compose the doc-maintenance trust ladder:
    #
    # * ``doc_maintenance_enabled`` — doc-maintainer workers WRITE docs/ and
    #   code comments at all. Off by default — operators must explicitly
    #   opt in to having a background agent touch project docs.
    # * ``doc_maintenance_commit_enabled`` — atomic build-validation +
    #   git-commit step runs after doc writes. Off by default — operators
    #   can dry-run the writes first and commit manually.
    #
    # Both default to False. Set BLUEPRINT_DOC_MAINTENANCE_ENABLED=true and
    # BLUEPRINT_DOC_MAINTENANCE_COMMIT_ENABLED=true to enable. The flags
    # compose: commit_enabled implies enabled.
    doc_maintenance_enabled: bool = Field(
        default=False,
        description="Opt-in gate for doc-maintenance writes. When false, "
                    "doc-maintainer workers are not dispatched. Default OFF — "
                    "set BLUEPRINT_DOC_MAINTENANCE_ENABLED=true to enable.",
    )
    doc_maintenance_commit_enabled: bool = Field(
        default=False,
        description="Opt-in gate for the atomic build-validation + git-commit "
                    "step. When false, doc-maintenance changes stay in the "
                    "working tree for manual review. Default OFF — set "
                    "BLUEPRINT_DOC_MAINTENANCE_COMMIT_ENABLED=true to enable "
                    "(requires doc_maintenance_enabled=true).",
    )
    doc_maintenance_build_cmd: str | None = Field(
        default=None,
        description="Optional override for the build/test command. If set, "
                    "replaces the detected command (npm test, pytest -x, etc.). "
                    "Parsed via shlex.split. Override via "
                    "BLUEPRINT_DOC_MAINTENANCE_BUILD_CMD or per-project metadata.",
    )


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
    skill_evolution: SkillEvolutionConfig = Field(default_factory=SkillEvolutionConfig)
    loop_breaker: LoopBreakerConfig = Field(default_factory=LoopBreakerConfig)
    report_repair: ReportRepairConfig = Field(default_factory=ReportRepairConfig)
    language: LanguageConfig = Field(default_factory=LanguageConfig)
    vscode: VSCodeConfig = Field(default_factory=VSCodeConfig)
    blueprint: BlueprintConfig = Field(default_factory=BlueprintConfig)


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
    if "skill_evolution" in processed_config:
        # Drop keys whose YAML value is ``null`` (None). pydantic-settings
        # treats an explicitly-passed init kwarg — even ``None`` — as taking
        # priority over environment variables, so a YAML ``embedding_base_url:
        # null`` would shadow ``SKILL_EVOLUTION_EMBEDDING_BASE_URL`` and force
        # the embedding service to fall back to ``llm.base_url`` (a chat-only
        # endpoint with no ``/embeddings`` route -> "404 page not found").
        # Stripping None lets the BaseSettings env-var source fill these in,
        # matching the documented contract (``.env.example`` /
        # ``config.yaml`` comments: "Falls back to llm.* if null", with env
        # vars overriding YAML).
        se_raw = processed_config["skill_evolution"]
        config_dict["skill_evolution"] = {
            k: v for k, v in se_raw.items() if v is not None
        }
    if "blueprint" in processed_config:
        # Drop keys whose YAML value is ``null`` (None). pydantic-settings
        # treats an explicitly-passed init kwarg — even ``None`` — as taking
        # priority over environment variables, so a YAML ``embedding_model:
        # null`` would shadow ``BLUEPRINT_EMBEDDING_MODEL`` and prevent the
        # embedding service from resolving the right model. Stripping None
        # lets the BaseSettings env-var source fill these in, matching the
        # same contract as ``skill_evolution`` (env vars override YAML).
        bp_raw = processed_config["blueprint"]
        config_dict["blueprint"] = {
            k: v for k, v in bp_raw.items() if v is not None
        }
    if "vscode" in processed_config:
        config_dict["vscode"] = processed_config["vscode"]

    # Create and validate config
    return Config(**config_dict)


# Convenience function for getting the config
def get_config(config_path: str | None = None) -> Config:
    """Get the configuration, loading it if not already loaded."""
    return load_config(config_path)
