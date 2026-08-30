"""Configuration loading with YAML, environment variable substitution, and Pydantic validation."""

# Why a single config module? LLM, job, instance, retry, blueprint, and
# skill-evolution settings all live here so operators have ONE surface to
# tune and ONE migration path (env override + YAML) per setting. Splitting
# by domain (e.g. ``llm_config.py`` / ``retry_config.py``) trades that
# clarity for marginal modularity; the file crossed 1000 lines in the HA
# fallback round and the trade-off still holds — keep centralized.
import json
import logging
import os
import re
from pathlib import Path
from typing import Annotated, Any, Callable, Dict

import yaml
from pydantic import Field, ConfigDict, model_validator, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

# Single source of truth for the non-status transient-channel pattern
# defaults (no import cycle: llm_error_classifier only imports
# .response_validation / httpx / openai / langchain_core). QueueConfig
# field defaults DERIVE from this bundle — never a second copy.
from .llm_error_classifier import (
    DEFAULT_TRANSIENT_CHANNEL_PATTERNS,
    DEFAULT_USAGE_LIMIT_PATTERNS,
)

from .constants import (
    CHECKPOINT_TTL_HOURS,
    CHECKPOINT_CLEANUP_INTERVAL_HOURS,
    MAX_INSTANCE_HISTORY,
    MAINTENANCE_CHECK_INTERVAL_MINUTES,
)

logger = logging.getLogger(__name__)


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
    # Optional HA fallback endpoint — same proxy backend in another datacenter.
    # Same API key / model names / API surface, just a different physical
    # endpoint. When unset (None), the system uses ONLY ``base_url`` and the
    # retry classifier behaves exactly as before (no failover). When set,
    # transient / timeout / IndexError failures on the primary trigger a
    # one-shot swap to this URL within the same invoke cycle. The swap is
    # sticky-on-success: after a cycle fails over and succeeds on backup,
    # the client stays on backup (both endpoints serve the same backend, so
    # lingering is harmless); the controller returns to primary after the
    # NEXT invoke's first attempt completes, regardless of whether that
    # attempt succeeds or fails on backup (see ``FailoverController``
    # docstring — "Sticky-on-success" — for the rationale). Wired via
    # OPENAI_BASE_URL_BACKUP (env_prefix="OPENAI_").
    base_url_backup: str | None = Field(
        default=None,
        description=(
            "Optional HA fallback base URL. When set, transient / timeout / "
            "IndexError failures on the primary trigger a swap to this URL "
            "within the same invoke cycle. Sticky-on-success: the client "
            "remains on backup after a successful failover; the controller "
            "returns to primary after the next invoke's first attempt "
            "completes, regardless of outcome (success or failure on "
            "backup). None = primary-only (zero behavior change)."
        ),
    )
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

    # Models for which reasoning_content echo is DISABLED: reasoning_content
    # from a previous turn is echoed back in subsequent assistant messages
    # for every model EXCEPT those whose name case-insensitively
    # substring-matches an entry here. Default: empty (all models echo).
    # Override via OPENAI_REASONING_ECHO_DISABLED_MODELS env var, e.g.
    #   OPENAI_REASONING_ECHO_DISABLED_MODELS="gpt-4o,claude"
    # The NoDecode annotation prevents pydantic-settings from auto-JSON-decoding
    # the env value, so our field_validator can handle comma-separated input.
    reasoning_echo_disabled_models: Annotated[list[str], NoDecode] = Field(
        default_factory=list,
        description=(
            "Model name patterns (case-insensitive substring match) for which "
            "reasoning_content echo is disabled. All other models echo "
            "reasoning_content back in multi-turn conversations. "
            "Default: [] (all models echo)."
        ),
    )

    # Stream the chat-completion response on the wire (Cloudflare 524 fix).
    # When True, every LangChain ``ChatOpenAI`` constructed through
    # ``daemon.graph.clean_llm_config`` sends ``stream: True`` to the
    # OpenAI-compatible backend, so chunked bytes flow back through the
    # Cloudflare proxy before its ~125s anycast read timeout can kill the
    # connection with zero response. LangChain's ``invoke()`` aggregates
    # the chunks back into the same ``AIMessage`` (content / tool_calls /
    # usage / reasoning_content all preserved), so callers see identical
    # final results. Default ON; operators can flip to False for debugging
    # or for backends that mis-handle streaming. Raw-SDK chat sites in
    # ``daemon/services/skill_{search,evolution}_service.py`` are NOT yet
    # wired for streaming (deferred — see commit message); they continue to
    # send non-streaming POSTs regardless of this flag. Embedding calls are
    # never streamed (the embeddings endpoint has no streaming surface).
    # Override via OPENAI_STREAMING env var. Precedent for the
    # OPENAI_REASONING_ECHO_DISABLED_MODELS denylist-style config chain.
    streaming: bool = Field(
        default=True,
        description=(
            "Send chat completions with stream: True on the wire so the "
            "connection survives Cloudflare's ~125s anycast proxy read "
            "timeout. LangChain invoke() aggregates chunks into the same "
            "AIMessage; callers see identical results. Default True."
        ),
    )

    @field_validator("streaming", mode="before")
    @classmethod
    def _coerce_streaming_empty_to_default(cls, value: Any) -> Any:
        """Coerce empty-string / YAML-null ``streaming`` to the default (True).

        Precedent: ``base_url_backup`` empty-guard at
        ``_coerce_base_url_backup_empty_to_none`` and the
        ``reasoning_echo_disabled_models`` empty-guard in
        ``_parse_reasoning_echo_disabled_models`` (both coerce ``""`` /
        whitespace to a sensible default instead of crashing pydantic
        bool parsing).

        ``OPENAI_STREAMING=""`` survives the ``${OPENAI_STREAMING:-true}``
        shell interpolation in some operator ``.env`` files (where an
        empty value pastes through without a substitution), and YAML
        files may carry a bare ``streaming:`` (None) when the operator
        deletes the value but leaves the key. Pydantic-settings raises
        ``ValidationError`` on bool parsing of an empty string and a
        missing-YAML-key default is None — both crash daemon boot.

        Rules:

        - ``""`` / whitespace → ``True`` (default)
        - ``None`` (YAML null) → ``True`` (default)
        - ``True`` / ``False`` → pass through unchanged
        - ``"true"`` / ``"false"`` / ``"1"`` / ``"0"`` → pydantic handles
          (delegated to bool coercion after our guard)
        """
        if value is None:
            return True
        if isinstance(value, str) and not value.strip():
            return True
        return value

    # Outbound proxy-buffering header opt-out. When True (default), every
    # LLM chat-completion request that carries the proxy identity headers
    # (``x-proxy-app`` / ``x-proxy-interleaved-thinking`` — the inline
    # ``default_headers`` sites in graph.py, compaction.py,
    # title_generation.py, keyword_extraction.py, and child_reports.py×2)
    # ALSO sends ``X-LLMProxy-Buffer-Response: true`` asking the proxy to
    # buffer the response. Set OPENAI_BUFFER_RESPONSE_HEADER=false to omit
    # the header entirely — the key is ABSENT, never sent as the literal
    # string "false" (a present-but-false header may be misread by the
    # proxy).
    # Override via OPENAI_BUFFER_RESPONSE_HEADER env var. Mirrors the
    # ``streaming`` flag directly above.
    buffer_response_header: bool = Field(
        default=True,
        description=(
            "Send the X-LLMProxy-Buffer-Response: true request header on "
            "every chat-completion request that carries the proxy identity "
            "headers, so the proxy buffers the response. Default True; set "
            "OPENAI_BUFFER_RESPONSE_HEADER=false to omit the header "
            "entirely (never sent as 'false')."
        ),
    )

    @field_validator("buffer_response_header", mode="before")
    @classmethod
    def _coerce_buffer_response_header_empty_to_default(cls, value: Any) -> Any:
        """Coerce empty-string / YAML-null to the default (True).

        Mirrors ``_coerce_streaming_empty_to_default`` above: an empty
        ``OPENAI_BUFFER_RESPONSE_HEADER=""`` pasting through the
        ``${OPENAI_BUFFER_RESPONSE_HEADER:-true}`` interpolation or a bare
        YAML ``buffer_response_header:`` (None) would otherwise crash
        daemon boot on pydantic bool parsing.
        """
        if value is None:
            return True
        if isinstance(value, str) and not value.strip():
            return True
        return value

    # Outbound LLM request-body gzip compression (opt-in). When True, the
    # LLM HTTP clients constructed by ``daemon.graph.clean_llm_config``
    # AND the raw-SDK ``_do_chat_call`` / ``_do_embed_call`` helpers in
    # ``daemon/services/{skill_search,skill_evolution,skill_embedding}_service``
    # use an httpx transport that gzip-compresses the request body and
    # stamps ``Content-Encoding: gzip`` (Content-Length auto-corrected to
    # the compressed size). Default DISABLED — when False the code path
    # runs byte-identical to pre-feature (no custom transport attached,
    # no headers injected). Response handling is completely untouched
    # (we never accept-encoding or accept gzip on the response side).
    #
    # The proxy must support request-body gzip for this to do anything
    # useful. The flag only adds the wire-level encoding; operators
    # enable it via ``OPENAI_REQUEST_GZIP=true`` to shrink outbound
    # payloads (text-heavy chat-completion bodies typically shrink 5-10x
    # with gzip). Empty / YAML-null coerces to the default (False) so an
    # ``OPENAI_REQUEST_GZIP=""`` paste-through or a bare YAML
    # ``request_gzip:`` key doesn't crash daemon boot on pydantic bool
    # parsing — same shape as the streaming / buffer-response-header
    # coercion pattern above.
    request_gzip: bool = Field(
        default=False,
        description=(
            "Outbound gzip compression of LLM HTTP request bodies. When "
            "True, request bodies are gzip-compressed on the wire and a "
            "Content-Encoding: gzip header is stamped (Content-Length "
            "auto-corrected). Default False (zero behavior change, "
            "pure passthrough). Override via OPENAI_REQUEST_GZIP env "
            "var."
        ),
    )

    @field_validator("request_gzip", mode="before")
    @classmethod
    def _coerce_request_gzip_empty_to_default(cls, value: Any) -> Any:
        """Coerce empty-string / YAML-null to the default (False).

        Mirrors ``_coerce_streaming_empty_to_default`` /
        ``_coerce_buffer_response_header_empty_to_default`` above: an
        empty ``OPENAI_REQUEST_GZIP=""`` pasting through the
        ``${OPENAI_REQUEST_GZIP:-false}`` interpolation or a bare YAML
        ``request_gzip:`` (None) would otherwise crash daemon boot on
        pydantic bool parsing.
        """
        if value is None:
            return False
        if isinstance(value, str) and not value.strip():
            return False
        return value

    @field_validator("reasoning_echo_disabled_models", mode="before")
    @classmethod
    def _parse_reasoning_echo_disabled_models(cls, value: Any) -> Any:
        """Accept comma-separated strings (and JSON arrays) from env / YAML.

        Delegates to ``_parse_csv_or_json_list`` for the shared parsing logic.
        The ``NoDecode`` annotation prevents pydantic-settings from
        auto-parsing env values, so we handle both forms here:
          - ``"gpt-4o,claude"`` → ``["gpt-4o", "claude"]``
          - ``'["gpt-4o","claude"]'`` → ``["gpt-4o", "claude"]``
          - ``["gpt-4o", "claude"]`` → unchanged (passthrough)
          - ``""`` or whitespace → ``[]``

        Env format example::

            OPENAI_REASONING_ECHO_DISABLED_MODELS="gpt-4o,claude"
        """
        return _parse_csv_or_json_list(value)

    @field_validator("base_url_backup", mode="before")
    @classmethod
    def _coerce_base_url_backup_empty_to_none(cls, value: Any) -> Any:
        """Normalize and validate ``base_url_backup`` input.

        Two rules:

        1. Coerce an empty-string ``base_url_backup`` to ``None``.
           ``config.yaml`` uses the substitution pattern
           ``base_url_backup: ${OPENAI_BASE_URL_BACKUP:-}`` which yields an
           empty string when the env var is unset. Pydantic-settings would
           otherwise store ``""`` as a valid ``str`` and the failover logic
           in :mod:`daemon.llm_error_classifier` would mistake it for a
           configured backup. Convert any whitespace-only value to ``None``
           so the "no backup configured" branch is taken (zero behavior
           change from the pre-HA system).

        2. Reject non-string values (YAML ``true`` / ``false`` / numbers).
           Pydantic's core ``str | None`` validation would reject them
           anyway, but raising HERE with a targeted message makes the
           operator's mistake legible: YAML booleans are the realistic
           footgun (``base_url_backup: true`` — the author meant to enable
           it, but there is no "enabled" boolean; the value IS the URL, and
           a bare ``true`` would otherwise be coerced by the env-var path
           into the literal string ``"true"`` — truthy enough to pass
           ``FailoverController.is_configured`` and point HTTP at an
           unresolvable host).
        """
        if value is None:
            return None
        if isinstance(value, str):
            return None if not value.strip() else value
        raise ValueError(
            f"base_url_backup must be a URL string or null/empty "
            f"(got {type(value).__name__}: {value!r}). There is no boolean "
            f"form — unset or empty means 'no backup', a URL string enables "
            f"the backup endpoint."
        )

    # Models allowed as instance model overrides at spawn time. Exact match
    # (case-insensitive) is performed against the override model name;
    # a match against ANY entry is sufficient. Empty list = all models
    # allowed (no restriction).
    #
    # SCOPE — this allowlist is consulted ONLY by the four spawn-time
    # selection flows: (1) `spawn model=` parameter override, (2) the
    # weighted `llm_models` pool filter on worker/coder, (3) the
    # `spawn_councilor` model-name validation, and (4) the
    # session-restore re-validation on a resumed checkpoint. Purpose-bound
    # models — ``model_title``, ``model_keywords`` (when set to a fixed
    # value like ``"quick"``), ``model_vision``, the compaction model,
    # and the skill evolution model — NEVER consult this list; configure
    # them independently via their own env vars / YAML keys.
    #
    # Override via the env-var pair ``OPENAI_SELECTABLE_MODELS`` (primary)
    # / ``OPENAI_ALLOWED_MODELS`` (legacy alias). Precedence and the
    # one-shot deprecation warning are wired in ``load_config`` (see
    # ``_resolve_allowed_models`` / ``warn_deprecated_allowed_models_env``)
    # — this field is never auto-mapped by pydantic-settings' env
    # mechanism because it has to honor both names with explicit ordering.
    # Example:
    #   OPENAI_SELECTABLE_MODELS="gpt-4,gpt-4o"
    # The NoDecode annotation prevents pydantic-settings from auto-JSON-decoding
    # the value, so our field_validator can handle comma-separated input.
    allowed_models: Annotated[list[str], NoDecode] = Field(
        default_factory=list,
        description=(
            "Allowed model names (case-insensitive exact match) for instance "
            "model overrides at spawn time. Empty list = all models allowed "
            "(no restriction). Scoped to the four spawn-time selection flows "
            "(spawn model= override, weighted llm_models pool filter, "
            "spawn_councilor validation, session-restore re-validation); "
            "purpose-bound models (model_title, model_keywords when set to "
            "a fixed value, model_vision, compaction, skill_evolution) are "
            "unaffected. Resolved from OPENAI_SELECTABLE_MODELS with "
            "OPENAI_ALLOWED_MODELS as a legacy alias (warn-once when the "
            "legacy name is the effective source). Default: []."
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
    graceful_shutdown_timeout_seconds: int = Field(
        default=60,
        description=(
            "Uvicorn timeout_graceful_shutdown — SCOPE IS NARROWER THAN THE "
            "NAME SUGGESTS (uvicorn 0.41.0): it bounds ONLY "
            "_wait_tasks_to_complete(), i.e. the drain of in-flight "
            "connections/requests after SIGTERM. The FastAPI lifespan "
            "shutdown that follows (all 9 steps of manager.shutdown()) is "
            "NOT bounded by this value. The real hard bound on total "
            "shutdown time is the launcher's SIGKILL (launcher.sh "
            "CHILD_STOP_WAIT_S / scripts/stop-ensemble.sh WAIT_S, default "
            "70s = this value + 10s margin; stop-ensemble.sh reads "
            "DAEMON_GRACEFUL_SHUTDOWN_TIMEOUT_SECONDS from the staged "
            "INSTALL_DIR/.env to derive its budget — single source of "
            "truth). Per-step asyncio.wait_for budgets inside "
            "manager.shutdown() are deferred hardening (pre-Phase-3). "
            "Within its scope: increase to let long SSE streams and "
            "checkpoint flushes finish, decrease to restart faster."
        ),
    )


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

    # Non-status transient-channel pattern matching
    # (docs/plans/transient-channel-retry-widening.md work unit 7).
    # Case-insensitive substring match against the exception message.
    # Applied by ``load_config`` pushing these into
    # ``daemon.llm_error_classifier.configure_transient_channel_patterns``.
    # NoDecode + field_validator accepts CSV ("a,b") or JSON ('["a","b"]')
    # from env vars (QUEUE_TRANSIENT_APIERROR_ALLOWLIST, ...) and YAML
    # lists. An explicitly-EMPTY allowlist/pattern list disables the
    # corresponding classifier branch (additive-off switch).
    #
    # DEFAULTS ARE DERIVED from the classifier's canonical corpus bundle
    # (``DEFAULT_TRANSIENT_CHANNEL_PATTERNS``) — config.yaml entries are
    # pure operator overrides. Note: REMOVING a key from config.yaml
    # reverts to the built-in defaults; disabling requires an explicit
    # empty/trimmed list.
    #
    # allowlist: bare openai.APIError messages treated as transient.
    #   Timeout-body patterns (below) route to the 3-attempt timeout
    #   budget; other hits to the 10-attempt transient budget.
    transient_apierror_allowlist: Annotated[list[str], NoDecode] = Field(
        default=list(DEFAULT_TRANSIENT_CHANNEL_PATTERNS.apierror_allowlist),
        description=(
            "Bare openai.APIError message substrings classified transient "
            "(relayed rate-limit / upstream-timeout bodies from the proxy). "
            "Blocklist entries take mandatory precedence. Empty disables "
            "the branch (pure pass-through)."
        ),
    )
    # timeout patterns: subset of the allowlist whose hits consume the
    # timeout budget (each attempt can cost the upstream's full timeout —
    # docs/retry-architecture.md §5 wall-clock amplification guard).
    # Validated at load time: must be a subset of the allowlist.
    transient_apierror_timeout_patterns: Annotated[list[str], NoDecode] = Field(
        default=list(DEFAULT_TRANSIENT_CHANNEL_PATTERNS.apierror_timeout_patterns),
        description=(
            "Allowlist subset routed to the timeout retry budget "
            "(kind='timeout_body'). Must be a subset of "
            "transient_apierror_allowlist (validated)."
        ),
    )
    # blocklist: mandatory precedence over the allowlist — quota /
    # bad-params shapes stay terminal, on BOTH the bare-APIError and the
    # ValueError channels. Auth shapes are unreachable here by design
    # (auth errors arrive as APIStatusError, caught at the status
    # branch) so they are NOT listed.
    transient_apierror_blocklist: Annotated[list[str], NoDecode] = Field(
        default=list(DEFAULT_TRANSIENT_CHANNEL_PATTERNS.apierror_blocklist),
        description=(
            "Message substrings that force non-retryable even when an "
            "allowlist/pattern entry also matches (mandatory precedence, "
            "applied to both the bare-APIError and ValueError channels). "
            "Protects quota shapes like 'Token Plan usage limit reached'."
        ),
    )
    # ValueError-body patterns: 200-body proxy errors and zero-chunk
    # SSE streams parsed by LangChain into ValueError.
    transient_valueerror_patterns: Annotated[list[str], NoDecode] = Field(
        default=list(DEFAULT_TRANSIENT_CHANNEL_PATTERNS.valueerror_patterns),
        description=(
            "ValueError message substrings classified transient "
            "(200-body proxy error dicts, zero-chunk SSE streams). "
            "'ultimate_model_retry_exhausted' is proxy-dependent — "
            "disable it by setting an explicit trimmed list once the "
            "proxy transparency update ships. Empty disables the branch."
        ),
    )
    # C3 kill-switch: whether httpx.RemoteProtocolError (peer closed
    # mid-body) is retryable. Membership in the retry set is conditional
    # on this flag — the config lever the pattern channels' empty-list
    # switches already provide for their siblings.
    transient_remote_protocol_retryable: bool = Field(
        default=DEFAULT_TRANSIENT_CHANNEL_PATTERNS.remote_protocol_retryable,
        description=(
            "Whether httpx.RemoteProtocolError (peer closed connection "
            "mid-body, incomplete chunked read) is retryable. Flip to "
            "false to stop a broken-endpoint retry loop without a "
            "redeploy."
        ),
    )
    # Quota-window shapes typed as UsageLimitError
    # (docs/plans/usage-limit-deferral-path.md W1/W7). Checked BEFORE
    # the allowlist/blocklist flow on both non-status channels; the
    # dedicated deferral path (worker seam) owns recovery. MUST stay
    # disjoint from bad-params shapes — validated at load time. An
    # explicitly-EMPTY list disables the typed wrapper entirely
    # (additive-off switch: quota shapes revert to the untyped terminal
    # blocklist re-raise).
    usage_limit_patterns: Annotated[list[str], NoDecode] = Field(
        default=list(DEFAULT_USAGE_LIMIT_PATTERNS),
        description=(
            "Message substrings typed as UsageLimitError (provider "
            "quota windows — 'Token Plan usage limit reached', corpus "
            "2056). Terminal at L1; the dedicated deferral path owns "
            "recovery. Must NOT match bad-params shapes ('invalid "
            "params', corpus 2013) — validated. Empty disables the "
            "typed wrapper."
        ),
    )

    @field_validator(
        "transient_apierror_allowlist",
        "transient_apierror_timeout_patterns",
        "transient_apierror_blocklist",
        "transient_valueerror_patterns",
        "usage_limit_patterns",
        mode="before",
    )
    @classmethod
    def _parse_transient_channel_patterns(cls, value: Any) -> Any:
        """Accept CSV / JSON-array strings and YAML lists for the
        non-status transient-channel pattern fields."""
        return _parse_csv_or_json_list(value)

    @model_validator(mode="after")
    def _validate_timeout_patterns_subset(self) -> "QueueConfig":
        """Timeout-body patterns must be a subset of the allowlist.

        A relayed-timeout pattern present in the allowlist but missing
        here would silently consume the 10-attempt transient budget at
        up to request_timeout (660s) per attempt on the uncapped hot
        path — the exact wall-clock amplification the timeout budget
        exists to prevent. Fail the config load instead.
        """
        allowlist = {p.lower() for p in self.transient_apierror_allowlist}
        stray = [
            p
            for p in self.transient_apierror_timeout_patterns
            if p.lower() not in allowlist
        ]
        if stray:
            raise ValueError(
                f"queue.transient_apierror_timeout_patterns must be a subset "
                f"of queue.transient_apierror_allowlist; stray entries: "
                f"{stray}. Add them to the allowlist or remove them from "
                f"the timeout patterns."
            )
        return self

    @model_validator(mode="after")
    def _validate_usage_limit_disjoint_from_bad_params(self) -> "QueueConfig":
        """Usage-limit patterns must never match bad-params shapes.

        A pattern that substring-matches the corpus-2013 bad-params
        message ("invalid params, tool call result does not follow tool
        call") would type a GENUINE BUG as ``UsageLimitError`` and push
        it into a 6 h auto-retry episode — the exact false-positive the
        dedicated path must never commit. Hard requirement
        (usage-limit-deferral-path W1/W7): fail the config load instead.
        """
        bad_params_shape = (
            "invalid params, tool call result does not follow tool call (2013)"
        )
        overlapping = [
            p
            for p in self.usage_limit_patterns
            if p.lower() in bad_params_shape.lower()
        ]
        if overlapping:
            raise ValueError(
                f"queue.usage_limit_patterns must stay disjoint from the "
                f"bad-params shapes ('invalid params', corpus 2013); these "
                f"entries would type a genuine bug into the 6h usage-limit "
                f"auto-retry: {overlapping}. Remove them."
            )
        return self


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
    # Dedicated usage-limit deferral path timing
    # (docs/plans/usage-limit-deferral-path.md W7). The window is the
    # RETRY HORIZON (anchor + deadline), not a timeout value — attempts
    # still fail in ~seconds; patience lives between tasks.
    usage_limit_window_seconds: int = Field(
        default=21600,
        description=(
            "Usage-limit episode horizon in seconds from the first "
            "quota sighting (anchor). 6h default — quota windows reset "
            "on the provider's schedule. Inside the window deferrals "
            "are budget-free; past it the episode terminalizes with "
            "exactly one report."
        ),
    )
    usage_limit_retry_delays_seconds: list[int] = Field(
        default=[180, 300, 600, 900],
        description=(
            "Usage-limit wake schedule step delays in seconds "
            "(3m, 5m, 10m; the final entry is the repeating cap "
            "beyond the listed slots). Elapsed-derived from the "
            "anchor, so restarts resume the window instead of "
            "restarting it."
        ),
    )
    # NOTE: unlike the queue pattern lists (where an explicitly-empty
    # list means "disable"), an EMPTY delay list is NOT a valid disable
    # switch — the schedule function requires non-empty positive
    # delays, and an empty list would strand quota-hit tasks RUNNING
    # with no retry and no terminal. Disabling the whole path is done
    # via queue.usage_limit_patterns: []. Validated below.
    usage_limit_retry_jitter_fraction: float = Field(
        default=0.1,
        description=(
            "Per-wake jitter for the usage-limit schedule, as a "
            "fraction of the slot's step delay (herd decoupling). "
            "The result is clamped to never schedule before "
            "now + 30s."
        ),
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
    # Phase 2 (pause-report-recovery, task 2.6): the periodic
    # ``ReportDeliveryRecoveryService`` sweep. S-e defaults —
    # tunable per-lane kill-switches; disabled → zero behavior
    # change.
    report_delivery_recovery_enabled: bool = Field(
        default=True,
        description=(
            "Master kill-switch for the periodic "
            "ReportDeliveryRecoveryService. When False the service "
            "is not constructed and the crash-recovery endpoint is "
            "unavailable. Defaults to True (S-e)."
        ),
    )
    report_delivery_recovery_interval_seconds: int = Field(
        default=300,
        description=(
            "Periodic sweep interval (seconds) for the "
            "ReportDeliveryRecoveryService. Default 300s (5min). "
            "S-e recommended default; tunable via env."
        ),
    )
    report_delivery_recovery_age_bound_minutes: int = Field(
        default=10,
        description=(
            "Minimum age before a DEFERRED / PENDING row is eligible "
            "for recovery (Lanes 1, 3, 4). Default 10 minutes."
        ),
    )
    report_delivery_recovery_batch_cap: int = Field(
        default=100,
        description=(
            "Maximum rows per lane per run (batch cap — MVP growth "
            "rule). Remainder logged and re-claimed next cycle."
        ),
    )
    report_delivery_recovery_retry_minutes: int = Field(
        default=1,
        description=(
            "Lane 4 retry interval (rows stamped "
            "recovery_attempted_at younger than this are skipped). "
            "S-e proposed default; flagged as proposed default."
        ),
    )
    report_delivery_recovery_lane_deferred: bool = Field(
        default=True,
        description=(
            "Lane 1 (DEFERRED rows for non-terminal parents) "
            "per-lane kill-switch. Gated by "
            "has_instance_busy(parent_id), no age bound — age "
            "filtering lives on Lanes 3+4. Defaults to True."
        ),
    )
    report_delivery_recovery_lane_no_row_backstop: bool = Field(
        default=True,
        description=(
            "Lane 2 (no-row backstop, C3 designed query) per-lane "
            "kill-switch. Defaults to True (the ONLY net under "
            "FM-11)."
        ),
    )
    report_delivery_recovery_lane_pending_age: bool = Field(
        default=True,
        description=(
            "Lane 3 (age-bounded PENDING, permanent W9) per-lane "
            "kill-switch. Defaults to True."
        ),
    )
    report_delivery_recovery_lane_recovery_retry: bool = Field(
        default=True,
        description=(
            "Lane 4 (recovery_attempted_at retry, permanent "
            "W9/FM-13) per-lane kill-switch. Defaults to True."
        ),
    )
    report_delivery_recovery_lane_orphan: bool = Field(
        default=True,
        description=(
            "Lane 5 (ORPHAN — terminal parents, W1) per-lane "
            "kill-switch. Defaults to True (NEVER silent — terminal-"
            "parent rows always reach a structured disposition)."
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
    # Pattern (f) — orphan ACTIVE JobItem recovery
    # (``.agents/shared/planning/orphan-active-job-recovery/``,
    # 802095d8 incident). The ``active`` JobItem that has NO
    # ``task`` rows AND an alive/stale instance is the
    # restart-orphan signature: the daemon restart cleared the
    # ``task`` table but left the JobItem row behind, so the
    # JobItem is now ``active`` with nothing to drive it forward.
    # The reconciler Pattern (f1) finalizes such JobItems to
    # ``admission_state='dead'`` (DEAD) — distinct from
    # Pattern (a)'s ``failed`` outcome. The 15-minute default
    # matches the leader's design: long enough to absorb a normal
    # claim cycle (the existing P1 / Pattern (a) 5-minute default
    # for stuck PENDING tasks is the tighter window, but orphan
    # active jobs are a structural-inconsistency class and need a
    # wider grace to avoid racing with a healthy ``active`` job
    # that just happens to have a slow Task-side enqueue).
    drift_reconcile_min_orphan_age_seconds: int = Field(
        default=900,
        ge=1,
        description=(
            "Minimum age (seconds) of an orphan ACTIVE JobItem "
            "(active JobItem + no Task rows + alive instance) "
            "before Pattern (f1) finalizes it as DEAD. JobItems "
            "younger than this are left alone to avoid racing "
            "with a healthy active job whose Task row is still "
            "being enqueued. Default 900s = 15 minutes — wide "
            "enough to absorb a normal enqueue-to-claim cycle "
            "but short enough to surface a true restart-orphan "
            "within one reconciler cycle (5-minute default cadence)."
        ),
    )
    # ─── WAITING_CHILDREN hang watchdog (issue #8) ───
    # The watchdog detects parents stuck in WAITING_CHILDREN because a
    # child is hung (non-terminal AND last_activity_at older than the
    # threshold) and injects a guidance notice into the parent so the
    # LangGraph turn can pick a remediation: inspect via
    # subtree_messages, one-shot revive (the agent-tool revive-once
    # guard is mechanically bounded), spawn a replacement, or
    # escalate. Defaults are conservative (1h cadence, 1h threshold)
    # so the watchdog is a quiet, infrequent background sweep that
    # only fires on genuine stalls. Set ``enabled`` to False to
    # disable globally (the lifespan task is skipped entirely).
    waiting_children_watchdog_enabled: bool = Field(
        default=True,
        description=(
            "Enable the periodic WAITING_CHILDREN hang watchdog. "
            "When False the daemon does not start the watchdog task "
            "in the lifespan (zero overhead, no DB scans). Default "
            "True. Override via SERVICES_WAITING_CHILDREN_WATCHDOG_ENABLED "
            "env var (true / false)."
        ),
    )
    waiting_children_watchdog_interval_seconds: int = Field(
        default=3600,
        ge=1,
        description=(
            "How often the WAITING_CHILDREN hang watchdog runs (seconds). "
            "Default 3600 = 1 hour. Lower = more responsive to genuine "
            "hangs but more DB scans. Must be >= 1 (0 would spin the "
            "loop); out-of-range values FAIL FAST AT BOOT — pydantic "
            "ValidationError raised at Settings instantiation inside "
            "load_config(), before the lifespan wiring (deliberate "
            "fail-fast, not a runtime disable). Override via "
            "SERVICES_WAITING_CHILDREN_WATCHDOG_INTERVAL_SECONDS env var."
        ),
    )
    waiting_children_watchdog_hang_threshold_seconds: int = Field(
        default=3600,
        ge=0,
        description=(
            "A non-terminal child whose last_activity_at is older than "
            "this (strictly greater than) is considered hung. Age is "
            "computed SQL-side via EXTRACT(EPOCH FROM (now()-col)) on "
            "PostgreSQL and julianday() on SQLite to avoid psycopg "
            "session-local-time skew. Default 3600 = 1 hour; 0 means "
            "any measurable age counts (test scenarios). Must be >= 0; "
            "negative values FAIL FAST AT BOOT — pydantic ValidationError "
            "raised at Settings instantiation inside load_config(), "
            "before the lifespan wiring (deliberate fail-fast, not a "
            "runtime disable). Override via "
            "SERVICES_WAITING_CHILDREN_WATCHDOG_HANG_THRESHOLD_SECONDS "
            "env var."
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
    readiness_refresh_interval_seconds: int = Field(
        default=10,
        description=(
            "How often the /readyz background refresher recomputes the "
            "readiness composite (database SELECT 1, queue heartbeat "
            "freshness, critical service presence). The HTTP handler "
            "itself is an O(1) memory read — this interval is the ONLY "
            "thing that touches the database for readiness."
        ),
    )
    readiness_queue_freshness_threshold_seconds: int = Field(
        default=120,
        description=(
            "Max allowed age of the newest Task.last_heartbeat_at among "
            "RUNNING tasks before the queue_freshness readiness component "
            "flips to degraded. Heartbeat cadence is 30s "
            "(task_heartbeat_interval_seconds), so 120s = 3 missed "
            "intervals + one interval of margin. An empty RUNNING set "
            "counts as fresh."
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

    @field_validator("usage_limit_retry_delays_seconds")
    @classmethod
    def _validate_usage_limit_delays(cls, v: list[int]) -> list[int]:
        """Usage-limit wake delays must be non-empty and positive.

        ``next_usage_limit_retry_at`` hard-requires this; an empty or
        non-positive list must fail AT LOAD, not strand quota-hit tasks
        RUNNING forever inside the never-raise worker handler (and
        break the stale sweep's episode-kwargs derivation the same
        way). Note this list has NO empty-disables semantics — the
        path's kill-switch is ``queue.usage_limit_patterns: []``.
        """
        if not v:
            raise ValueError(
                "services.usage_limit_retry_delays_seconds must be a "
                "non-empty list of positive seconds. To disable the "
                "usage-limit deferral path entirely, set "
                "queue.usage_limit_patterns: [] instead."
            )
        bad = [d for d in v if not isinstance(d, int) or isinstance(d, bool) or d <= 0]
        if bad:
            raise ValueError(
                f"services.usage_limit_retry_delays_seconds entries must "
                f"be positive integers; invalid: {bad}."
            )
        return v


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


# Warn-only deprecation guard for the removed reasoning-echo allowlist env
# var. The value is deliberately read into NO behavior — the denylist key
# OPENAI_REASONING_ECHO_DISABLED_MODELS is the only effective control.
_reasoning_echo_deprecation_warned = False


def warn_deprecated_reasoning_echo_env() -> None:
    """Log a single per-process warning if the old allowlist env var is set.

    ``OPENAI_REASONING_ECHO_MODELS`` stopped being read when the
    reasoning_content echo default flipped to ON for all models; its
    replacement is the denylist key ``OPENAI_REASONING_ECHO_DISABLED_MODELS``.
    Called from ``load_config`` and the startup wiring sites
    (``daemon/__main__.py``, ``daemon/api.py``); the module-level guard makes
    the warning fire at most once per process.
    """
    global _reasoning_echo_deprecation_warned
    if _reasoning_echo_deprecation_warned:
        return
    _reasoning_echo_deprecation_warned = True
    if "OPENAI_REASONING_ECHO_MODELS" not in os.environ:
        return
    logger.warning(
        "[Config] OPENAI_REASONING_ECHO_MODELS is set but no longer read; "
        "reasoning_content echo now defaults to ON for all models. Use "
        'OPENAI_REASONING_ECHO_DISABLED_MODELS (e.g. "gpt-4o,claude") to '
        "disable echo for models whose endpoint rejects the field."
    )


# Shared normalizer for env-var values read out of ``os.environ``. Bare
# ``KEY=`` lines in ``.env`` reach ``os.environ`` via
# ``launcher.sh`` ``load_env_file`` exactly as the empty string — without
# normalization, the precedence chain below would treat the empty
# string as "set", defeating the documented default-on-empty semantics.
def _clean_env_value(v: str | None) -> str | None:
    """Return the trimmed string, or ``None`` for ``None`` / empty / whitespace-only."""
    if v is None:
        return None
    stripped = v.strip()
    return stripped or None


# Warn-only deprecation guard for the legacy OPENAI_ALLOWED_MODELS env var.
# The new primary name is OPENAI_SELECTABLE_MODELS — the old name is still
# honored when the new one is unset, but every process emits exactly one
# warning at startup when the legacy name is the effective source.
_allowed_models_deprecation_warned = False


def warn_deprecated_allowed_models_env() -> None:
    """Log a single per-process warning when the legacy allowlist env var is the effective source.

    Emits exactly when BOTH conditions hold:

      * ``OPENAI_ALLOWED_MODELS`` is present in the environment AND has a
        non-empty (non-whitespace) value, AND
      * ``OPENAI_SELECTABLE_MODELS`` is unset (or present-but-empty).

    Empty / whitespace-only values are treated as UNSET for BOTH names
    so the warn function faithfully tracks the precedence winner (the
    same normalization the resolver applies). A bare ``KEY=`` line in
    ``.env`` is therefore never logged as spurious — it produces the
    documented default, not a deprecation nag. Operators who set the new
    name are also silent, even when the legacy name lingers on the same
    machine: only the effective source triggers the warning.

    Called from ``load_config`` (after the precedence is resolved) and
    the startup wiring sites (``daemon/__main__.py``,
    ``daemon/api.py``); the module-level guard makes the warning fire
    at most once per process even if the function is invoked from
    multiple entry points.
    """
    global _allowed_models_deprecation_warned
    if _allowed_models_deprecation_warned:
        return
    _allowed_models_deprecation_warned = True
    if _clean_env_value(os.environ.get("OPENAI_ALLOWED_MODELS")) is None:
        return
    if _clean_env_value(os.environ.get("OPENAI_SELECTABLE_MODELS")) is not None:
        return
    logger.warning(
        "[Config] OPENAI_ALLOWED_MODELS is set but renamed to "
        "OPENAI_SELECTABLE_MODELS — the legacy name is still honored as a "
        "fallback when the new name is unset, but please rename the env "
        "var in your deployment (.env / launcher exports) to silence this "
        "warning. The internal config field (config.llm.allowed_models) "
        "is unchanged; only the env-var-level aliasing changed."
    )


# Documented default for ``allowed_models`` when neither env var is set.
# Mirrors the legacy ``config.yaml`` default so behavior is identical to
# the pre-rename deployment when operators have not yet migrated.
_ALLOWED_MODELS_DEFAULT: tuple[str, ...] = ("agentic", "coding")


def _resolve_allowed_models(
    yaml_value: Any,
    *,
    new_var: str | None,
    old_var: str | None,
    on_legacy: Callable[[], None] | None = None,
) -> Any:
    """Pure resolver for the ``allowed_models`` precedence chain.

    Precedence (mirrors the documented contract):

      1. ``new_var`` (``OPENAI_SELECTABLE_MODELS``) — when SET and
         NON-EMPTY (empty/whitespace are treated as UNSET), wins
         outright, no warning.
      2. ``old_var`` (``OPENAI_ALLOWED_MODELS``) — when SET and
         NON-EMPTY, AND the new name is unset/empty, used as the
         effective source AND the ``on_legacy`` callback (typically
         :func:`warn_deprecated_allowed_models_env`) is invoked
         exactly once per process.
      3. ``yaml_value`` — the YAML-interpolated value. The shipped
         ``config.yaml`` now inlines the default in its interpolation
         (``${OPENAI_SELECTABLE_MODELS:-agentic,coding}``), so the YAML
         layer hands us either the new-var value or that default — not
         an empty string. The empty-string branch is retained as
         defense-in-depth for custom/programmatic yaml and direct
         resolver calls: we substitute the documented default
         ``["agentic", "coding"]`` so a no-env-var deployment matches
         the pre-rename behavior. Non-empty values (e.g. an operator
         hard-coded the value in YAML bypassing the env vars) are
         passed through untouched.

    Pure function (no ``os.environ`` access, no module-level mutation):
    tests pass the resolved env values directly, which keeps the
    precedence chain deterministic and side-effect-free. ``load_config``
    does the ``os.environ`` lookup once and calls this function with
    the resolved strings.

    Empty / whitespace-only values for ``new_var`` or ``old_var`` are
    treated as UNSET (legacy shell-style ``:-`` semantics preserved).
    ``launcher.sh`` ``load_env_file`` exports bare ``KEY=`` lines
    verbatim into ``os.environ``, so this normalization keeps a stray
    blank entry from being read as "set-but-empty" — which would
    otherwise defeat the documented default. Consequence: there is no
    env path to an unrestricted allowlist; operators who want to lift
    restrictions entirely must hardcode ``allowed_models: []`` in
    ``config.yaml``.
    """
    new_clean = _clean_env_value(new_var)
    old_clean = _clean_env_value(old_var)
    if new_clean is not None:
        return new_clean
    if old_clean is not None:
        if on_legacy is not None:
            on_legacy()
        return old_clean
    # Neither env var set. If YAML gave us an empty-string placeholder,
    # fall back to the documented default; otherwise pass the YAML value
    # through (it'll go through the CSV/JSON field validator downstream).
    if isinstance(yaml_value, str) and not yaml_value.strip():
        return ",".join(_ALLOWED_MODELS_DEFAULT)
    return yaml_value


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
    # Warn-once deprecation notice for the removed allowlist env var
    warn_deprecated_reasoning_echo_env()

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

    # Resolve the OPENAI_SELECTABLE_MODELS / OPENAI_ALLOWED_MODELS
    # precedence chain for ``llm.allowed_models``. The shipped
    # config.yaml now inlines the default in its interpolation
    # (``${OPENAI_SELECTABLE_MODELS:-agentic,coding}``), so the YAML
    # layer hands us either the new-var value or that default — not
    # an empty string. We still need explicit precedence here (and an
    # os.environ check for the legacy name) so:
    #   * both vars set → new wins, no warning
    #   * only legacy set → legacy wins + one-shot warning
    #   * neither set → documented default ("agentic,coding")
    # The "neither set" branch is defense-in-depth: it only fires when
    # a custom/programmatic yaml (or direct resolver call) presents
    # an empty yaml_value.
    # ``warn_deprecated_allowed_models_env`` is called HERE on the
    # "old-var-is-effective" branch (via the resolver callback), and
    # ALSO from the startup entry points (daemon/__main__.py,
    # daemon/api.py) so a fresh process that only goes through the
    # startup path (rare — load_config normally precedes those sites)
    # still gets the warning. The module-level guard makes the second
    # call silent.
    # See ``_resolve_allowed_models`` for the full contract.
    llm_config: Dict[str, Any] = {}
    if "llm" in processed_config:
        llm_config = processed_config["llm"].copy()
    llm_config["allowed_models"] = _resolve_allowed_models(
        llm_config.get("allowed_models", ""),
        new_var=os.environ.get("OPENAI_SELECTABLE_MODELS"),
        old_var=os.environ.get("OPENAI_ALLOWED_MODELS"),
        on_legacy=warn_deprecated_allowed_models_env,
    )
    config_dict["llm"] = llm_config
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
    config = Config(**config_dict)

    # Push the non-status transient-channel pattern lists into the
    # classifier module (docs/plans/transient-channel-retry-widening.md
    # work unit 7) so the classifier and the L2 facade share one
    # config-driven source of truth. Lazy import to avoid any import
    # cycle at module load; load_config is called rarely (startup /
    # tests), so the call cost is negligible. The install is a SINGLE
    # atomic bundle assignment, so runtime reloads (keyword extraction
    # calls load_config) can never leave a mid-classification reader
    # with a torn old/new pattern view.
    from .llm_error_classifier import configure_transient_channel_patterns

    configure_transient_channel_patterns(
        apierror_allowlist=config.queue.transient_apierror_allowlist,
        apierror_timeout_patterns=config.queue.transient_apierror_timeout_patterns,
        apierror_blocklist=config.queue.transient_apierror_blocklist,
        valueerror_patterns=config.queue.transient_valueerror_patterns,
        remote_protocol_retryable=config.queue.transient_remote_protocol_retryable,
    )

    # Quota-window typing patterns (usage-limit-deferral-path W1/W7) —
    # same config-driven single-source-of-truth convention, installed
    # beside the transient-channel bundle so a runtime reload swaps both
    # atomically-independently. An explicitly-empty list disables the
    # typed wrapper (pure pass-through to the blocklist flow).
    from .llm_error_classifier import configure_usage_limit_patterns

    configure_usage_limit_patterns(
        patterns=config.queue.usage_limit_patterns,
    )

    return config


# Convenience function for getting the config
def get_config(config_path: str | None = None) -> Config:
    """Get the configuration, loading it if not already loaded."""
    return load_config(config_path)
