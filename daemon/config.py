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
from pydantic import AliasChoices, BaseModel, Field, ConfigDict, model_validator, field_validator
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
    ENSEMBLE_KV_AMBIENT_SYSTEM_DEFAULT_ENABLED,
    ENSEMBLE_VSCODE_WEBVIEW_CSP_FIX,
    MAX_INSTANCE_HISTORY,
    MAINTENANCE_CHECK_INTERVAL_MINUTES,
    REPORT_REPAIR_EXCLUDED_AGENTS,
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


# M5 — single source of truth for the documented default of
# ``llm.spawn_intelligence_tier_high_model``. Declared here (BEFORE
# ``LLMConfig``) so the ``Field(default=...)`` at :425 can reference
# it directly without a NameError at class-construction time.
# Consumed by:
#   (a) the Pydantic ``Field(default=...)`` at :425 (this module),
#   (b) ``_resolve_intelligence_tier_high_model`` at the ~:2340
#       zone (returns it as the empty=unset normalization target),
#   (c) the tool layer's
#       ``getattr(..., default=_SPAWN_INTELLIGENCE_TIER_HIGH_DEFAULT)``
#       fallback at ``daemon/tools/instance.py`` (Feature #1
#       resolver block — imports from this module). The "~:2340
#       zone" M5 cross-ref comment is retained below the second
#       occurrence; this early declaration is the load-bearing one.
_SPAWN_INTELLIGENCE_TIER_HIGH_DEFAULT: str = "agentic"


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

    # LLM stream-liveness watchdog threshold (llm-stream-stall-hardening
    # L2). When a streaming (SSE) LLM response delivers no bytes —
    # content OR heartbeat keep-alive — for longer than this many
    # wall-clock seconds, the watchdog force-aborts the blocked read
    # (transport shutdown) and the forced abort rides the existing
    # timeout retry budget via StreamStalledError (an
    # httpx.ReadTimeout subclass). Healthy streams always carry bytes
    # within ~one heartbeat interval (~5.0s on both prod proxies,
    # 2026-09-15 probe; max legit inter-batch gap ~3.9s), so
    # 45s (~9x cadence) discriminates a dead transport without
    # false-aborting long thinking pauses. ALWAYS-ON — this is a
    # tuning-only knob (repo fix/flag policy: no disable value; the
    # ge=10 floor keeps the value honest). Override via
    # OPENAI_STREAM_STALL_THRESHOLD_SECONDS.
    stream_stall_threshold_seconds: int = Field(
        default=45,
        ge=10,
        description=(
            "Seconds of SSE byte-silence (wall clock) before the stream "
            "watchdog force-aborts the stalled response. Floor 10s; "
            "default 45s. Probe-evidenced 2026-09-15 (round 2): both "
            "proxies heartbeat at ~5.0s cadence (max observed 5.8s), "
            "max legitimate inter-content gap 3.9s — 45s ≈ 9x the "
            "measured cadence, kept as default."
        ),
    )

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

    # Feature #1 (spawn-time intelligence override). The configured
    # high-tier model name that ``spawn_instance(model_tier="high")``
    # resolves to. Resolved at boot by ``load_config`` from the
    # ``SPAWN_INTELLIGENCE_TIER_HIGH_MODEL`` env var (single env read
    # at boot — A6 hard rule; per-spawn reads FORBIDDEN to avoid
    # split-brain with the ``allowed_models`` boot snapshot). Default
    # ``"agentic"`` (mirrors the first element of
    # ``_ALLOWED_MODELS_DEFAULT``). The tool layer reads this via
    # ``manager.config.llm.spawn_intelligence_tier_high_model``; no
    # per-spawn ``os.environ`` access. If the resolved value is NOT in
    # ``allowed_models``, ``load_config`` emits a one-shot WARNING and
    # every ``model_tier="high"`` call raises loud until the env is
    # re-pointed and the daemon restarted.
    spawn_intelligence_tier_high_model: str = Field(
        default=_SPAWN_INTELLIGENCE_TIER_HIGH_DEFAULT,
        # M5 — this default MUST stay in lockstep with
        # ``_SPAWN_INTELLIGENCE_TIER_HIGH_DEFAULT`` (the single
        # source defined at daemon/config.py:~2340). The tool layer
        # at ``daemon/tools/instance.py`` (Feature #1 resolver
        # block) imports the same constant for its ``getattr``
        # fallback; ``_resolve_intelligence_tier_high_model``
        # (helper at daemon/config.py:2340-zone) returns it as the
        # empty=unset normalization target. Do not re-declare the
        # literal here.
        description=(
            "Boot-snapshot of the high-tier model that "
            "spawn_instance(model_tier='high') resolves to. Read once "
            "from SPAWN_INTELLIGENCE_TIER_HIGH_MODEL env at "
            "load_config; default 'agentic'."
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

    max_children_per_instance: int = Field(default=50)
    instance_timeout_minutes: int = Field(default=60)
    graph_recursion_limit: int = Field(default=100)
    llm_concurrency: int = Field(default=10, ge=1, description="Maximum concurrent LLM calls across all instances")

    # ── Governor recursive-spawn guard (Governor Recursion Guard, 2026-08-30) ──
    # Default ON (matches the locked design decision). The guard refuses to
    # spawn a governor instance when the prospective parent's chain (parent
    # ∪ ancestors) already contains ≥ K governors. K=1 → only one governor
    # in any chain; K=0 disables.
    #
    # Dual-read (decision made): BOTH this YAML field and the env const
    # ``LIMITS_GOVERNOR_RECURSION_GUARD_ENABLED=0`` are honored; the
    # Pydantic field is intentionally KEPT — the env resolver
    # (``_resolve_governor_recursion_guard_enabled`` in
    # ``daemon.repositories.instance.repository``) reads the override
    # directly and the call site gates on ``cfg_enabled AND env_enabled``.
    # Restart-required to pick up a flip from either side (matches the
    # ``ENSEMBLE_CASCADE_LINEAGE`` precedent).
    governor_recursion_guard_enabled: bool = Field(default=True)
    max_governor_ancestors: int = Field(default=1, ge=0)

    # ── S5 degenerate re-invoke cap (empty-response-guard Phase 1) ──────
    # Max consecutive reasoning-only / <think>-tag-only AIMessages the
    # router will re-invoke over before falling through to nudge/END
    # (``EMPTY_DEGENERATE_REINVOKE_CAP`` in daemon/graph.py — installed
    # at boot by the daemon entry points). Default 3 matches the
    # LoopDetector repetition threshold. Without the cap those branches
    # burned ~100 LLM calls until GraphRecursionError. Restart-required
    # (the graph module global is installed once at startup).
    empty_degenerate_reinvoke_cap: int = Field(default=3, ge=1)


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

    # ── Per-thread retention cap (Op D prune, daemon/services/maintenance.py) ─
    # Max checkpoints to keep per (thread_id, checkpoint_ns). The cleanup job
    # in ``_prune_per_thread_checkpoints`` reads this value via
    # ``self._config.checkpoint_max_per_thread`` and prunes the oldest
    # checkpoints while preserving the latest N. Default 3 (was the hardcoded
    # ``daemon.constants.CHECKPOINT_MAX_PER_THREAD`` value of 50; lowered to 3
    # so long-running instances don't accumulate a multi-week checkpoint tail
    # they never resume against). Floor 1 enforced by ``ge=1`` — 0 / negative
    # would prune EVERY checkpoint (including the latest) and break resume;
    # pydantic raises ``ValidationError`` at config load when violated. Override via env: ``CHECKPOINT_MAX_PER_THREAD``.
    # ``validation_alias`` is explicit (no ``PERSISTENCE_`` prefix) so the
    # env name matches the historical Python constant and is discoverable
    # by operators who know the constant name. YAML key remains
    # ``persistence.checkpoint_max_per_thread``.
    checkpoint_max_per_thread: int = Field(
        default=3,
        ge=1,
        validation_alias=AliasChoices(
            "checkpoint_max_per_thread",
            "CHECKPOINT_MAX_PER_THREAD",
        ),
        description=(
            "Max checkpoints to keep per thread (parent chain preserved). "
            "Default 3. Floor 1 (0/negative fails loud at config load — "
            "would prune ALL checkpoints including the latest, breaking "
            "resume). Override via env: CHECKPOINT_MAX_PER_THREAD."
        ),
    )


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


# Permissive bool-spelling vocabularies for ``proactive_enabled``.
# Shared by ``CompactionConfig._parse_proactive_enabled`` (the
# field validator — empty/raw env-var normalization) AND
# ``_resolve_proactive_enabled`` (the load_config resolver — the
# only production caller today, but the field validator must stay
# in sync so a direct ``CompactionConfig()`` construction (e.g. a
# test, an internal caller, a future second boot path) does NOT
# diverge from the boot path). Centralized here so the two sites
# cannot drift (cycle-3 cleanup — pre-cycle-3 each site had its
# own copy of these literals; the validator was unaware of the
# resolver's permissiveness and vice-versa).
_PROACTIVE_FALSE_BOOLS: frozenset[str] = frozenset({"0", "false", "no", "off"})
_PROACTIVE_TRUE_BOOLS: frozenset[str] = frozenset({"1", "true", "yes", "on"})


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
            "0 = fall through to the hard-coded DEFAULT_CONTEXT_LIMIT (700k)."
        ),
    )
    target_ratio: float = Field(default=0.40, description="Target token usage after compaction as fraction of context window")
    model: str = Field(
        default="",
        description=(
            "Model used by the compaction engine (summarization LLM calls AND "
            "context-window math). Empty = session model (current behavior). "
            "Settable via env COMPACTION_MODEL or YAML compaction.model; "
            "precedence COMPACTION_MODEL > compaction.model, resolved "
            "explicitly in load_config (_resolve_compaction_model), NOT by "
            "pydantic layering. When both this and the legacy "
            "summarization_model alias are set, this field wins."
        ),
    )
    summarization_model: str = Field(default="", description="Legacy alias for ``model``. Kept for backwards compatibility; ignored when ``model`` is set")
    min_messages_before_compaction: int = Field(default=10, description="Minimum number of messages before compaction is considered")
    summarization_chunk_threshold: float = Field(default=0.60, description="Fraction of context window above which summarization uses chunking")

    # ── Proactive-compaction kill-switch (Phase 1 of proactive-compaction-fix) ─
    # Single-source flag governing BOTH auto triggers: the pre-dispatch
    # proactive gate (P1) AND the 95% pre-call reactive hook (P1b,
    # ``daemon/graph.py::_maybe_precall_compact_95``) — per ADDENDUM §A.8
    # of ``.agents/shared/planning/proactive-compaction-fix/architecture-recommendation.md``
    # (one feature, one seam, one kill-switch; no second flag). The CLE
    # trigger stays ungated.
    #
    # Widened semantics (live since P1b): the field name says
    # "proactive" but the same flag governs the reactive 95% hook. Name
    # accepted without churn; semantics documented here so the surface
    # is honest about its scope.
    #
    # Default ON (per ADDENDUM §A.2 — supersedes the main body's §3.7
    # OFF default). The env name is intentionally NOT
    # ``COMPACTION_PROACTIVE_ENABLED`` (the section's
    # ``env_prefix="COMPACTION_"``); the
    # ``validation_alias`` keeps the documented
    # ``ENSEMBLE_PROACTIVE_COMPACTION`` env name working without
    # pydantic layering churn. Setting the env to ``"0"`` /
    # ``"false"`` / ``"False"`` / ``"no"`` disables the trigger (the
    # field is parsed through ``_parse_proactive_enabled`` for the
    # documented permissive semantics).
    proactive_enabled: bool = Field(
        default=True,
        validation_alias=AliasChoices(
            "proactive_enabled",
            "ENSEMBLE_PROACTIVE_COMPACTION",
        ),
        description=(
            "Enable the proactive context-compaction trigger. "
            "Default ON. Env: ENSEMBLE_PROACTIVE_COMPACTION (0/false "
            "disables). Governs the P1 pre-dispatch gate AND the P1b "
            "95% pre-call reactive hook (single kill-switch, A.8)."
        ),
    )

    @field_validator("proactive_enabled", mode="before")
    @classmethod
    def _parse_proactive_enabled(cls, value: Any) -> Any:
        """Permissive bool parser for ``ENSEMBLE_PROACTIVE_COMPACTION``.

        Bare ``KEY=`` lines in ``.env`` reach pydantic as the empty
        string. Pre-cycle-3 the validator normalized empty →
        ``None`` (pydantic treats ``None`` as an explicit value and
        raises ``bool_type`` validation error — boot crashes).
        Cycle 3 / W-1 fix: empty / whitespace-only values are
        normalized to the documented ``True`` default DIRECTLY so a
        direct ``CompactionConfig()`` construction (test, internal
        caller, future boot path) does NOT diverge from the
        resolver path. ``"0"`` / ``"false"`` / ``"no"`` /
        ``"off"`` (any case) → False; ``"1"`` / ``"true"`` /
        ``"yes"`` / ``"on"`` → True; non-empty unrecognized strings
        pass through (pydantic raises a clear type error so a typo
        is caught at startup).

        The spellings live in module-level
        :data:`_PROACTIVE_TRUE_BOOLS` / :data:`_PROACTIVE_FALSE_BOOLS`
        — shared with :func:`_resolve_proactive_enabled` so the
        field validator cannot drift from the resolver.
        """
        if value is None or (isinstance(value, str) and not value.strip()):
            # Empty / whitespace-only → documented ON default.
            # Returning the default explicitly (rather than ``None``)
            # is required: in a ``mode="before"`` validator, ``None``
            # is an EXPLICIT value (pydantic treats it as a user-set
            # value and applies the field type check), NOT a signal
            # to fall back to the Field default. The resolver at
            # :func:`_resolve_proactive_enabled` has the same
            # empty-string normalization contract — this validator
            # mirrors it so a direct ``CompactionConfig()``
            # construction (bypassing ``load_config``) does NOT
            # crash boot with a stray ``.env`` ``KEY=`` line.
            return True
        if isinstance(value, str):
            v = value.strip().lower()
            if v in _PROACTIVE_FALSE_BOOLS:
                return False
            if v in _PROACTIVE_TRUE_BOOLS:
                return True
            # Anything else is passed through; pydantic raises.
        return value

    # ── Adaptive LLM timeout (Phase 1 / WS-3) ──────────────────────────────────
    # Adaptive formula (per-call): ``min(cap, base + (tokens/100_000)*per_100k)``
    # applied at every ``_call_summarization_llm`` site. Replaces the prior
    # hard-coded ``timeout=30.0`` literal so merge/condense prompts (tiny)
    # get the base timeout instead of an oversized conversation-scale cap.
    timeout_base_s: float = Field(
        default=90.0,
        description=(
            "Adaptive LLM timeout base (seconds) for the per-call summarization "
            "wait. Combined with tokens-estimated scaling; result is min'd "
            "against timeout_cap_s. Default 90s replaces the prior 30s literal."
        ),
    )
    timeout_per_100k_tokens_s: float = Field(
        default=60.0,
        description=(
            "Adaptive LLM timeout scaling: extra seconds added per 100,000 "
            "tokens of the prompt being sent. Default 60s/100k."
        ),
    )
    timeout_cap_s: float = Field(
        default=300.0,
        description=(
            "Hard ceiling for the adaptive per-call LLM timeout (seconds). "
            "Default 300s."
        ),
    )
    timeout_facade_margin_s: float = Field(
        default=5.0,
        description=(
            "Wall-clock margin (seconds) added to the inner per-call cap when "
            "threaded into the HA failover facade's ``wall_clock_cap_s``. "
            "PINNED to 5s by architect §9.8 so tenacity retries stay inside "
            "the outer cap and the site-level TimeoutError trips first."
        ),
    )
    operation_budget_s: float = Field(
        default=300.0,
        description=(
            "Whole-operation budget (seconds) spanning all chunk calls in a "
            "single ``_summarize_chunked`` run. Cumulative clock enforced as "
            "a shared deadline around the parallel batch pool; on expiry the "
            "engine cancels in-flight batches, keeps the completed "
            "summaries, and proceeds to the partial/truncate fallback. The "
            "deadline lives entirely inside ``_summarize_chunked`` — never "
            "between the two ``aupdate_state`` persistence calls."
        ),
    )
    chunk_concurrency: int = Field(
        default=3,
        ge=1,
        le=32,
        description=(
            "Max batch-summarization calls running in parallel inside one "
            "chunked ``_summarize_chunked`` run (asyncio.Semaphore bound "
            "around the batch pool). 1 = effectively serial (pre-parallel "
            "behavior). Env knob: COMPACTION_CHUNK_CONCURRENCY (this config "
            "block's env_prefix; env-only — no yaml for this knob). Default "
            "3 is the conservative leader decision for local LLM proxies "
            "with no backup URL: N-way parallelism multiplies in-flight "
            "tenacity retries (worst case N x transient_max) against a "
            "single endpoint. Batches are independent (static per-batch "
            "prompt; merge runs after the pool), so this is a pure "
            "throughput ceiling."
        ),
    )


class SlashCommandConfig(BaseSettings):
    """Slash-command subsystem configuration (Phase 1 / WS-7).

    Surfaced to the operator via ``SLASH_COMMANDS_*`` environment variables.
    A later phase (compact_executor / command_dispatcher) consumes this
    config; engine code paths do NOT read it. Exact names here are a contract.
    """

    model_config = SettingsConfigDict(env_prefix="SLASH_COMMANDS_")

    enabled: bool = Field(
        default=True,
        description=(
            "Master switch. False disables slash-command parsing entirely — "
            "messages starting with / are treated as plain text (used as the "
            "kill-switch and for the WS-8 'no regression when feature off' "
            "test)."
        ),
    )
    escape_prefix: str = Field(
        default="//",
        description=(
            "Escape prefix checked BEFORE the leading / parse. Leading ``//`` "
            "strips one / and treats the rest as plain text (Slack convention, "
            "architect O-B1 ratified)."
        ),
    )
    min_interval_s: int = Field(
        default=10,
        description=(
            "Per-instance rate-limit minimum interval (seconds). The O-B13 "
            "abuse guard — second POST inside this window returns "
            "``rate_limited``. Checked BEFORE ExecutionGate acquisition."
        ),
    )
    noop_floor_ratio: float = Field(
        default=0.05,
        description=(
            "Executor noop floor as fraction of the resolved per-instance "
            "context window. Estimated tokens below this ratio of the window "
            "→ ``success + noop + reason=below_floor``; engine never invoked. "
            "5% is a tuning guess (architect §2: 'expect adjustment')."
        ),
    )
    state_ttl_s: int = Field(
        default=600,
        description=(
            "TTL (seconds) for in-memory command-state entries used by the "
            "GET ``/commands/active`` fallback. Mirrors the ``ttl_seconds`` "
            "field on the POST command-ack envelope."
        ),
    )
    max_state_per_instance: int = Field(
        default=20,
        description=(
            "Maximum number of terminal command-state entries retained in "
            "the daemon-wide ring per instance. Once the bound is reached, "
            "oldest terminal entry is evicted (LRU)."
        ),
    )


class ServiceToolConfig(BaseModel):
    """Knobs for the ``service`` tool category (service-tool Phase 1).

    Mounted on :class:`ServicesConfig` as ``service_tool`` so the
    operator surface is ``services.service_tool.*`` (yaml) and the
    ``ENSEMBLE_SERVICE_TOOL_*`` env family (resolved explicitly in
    :func:`load_config` — the init-kwarg-beats-env inversion trap is
    closed the same way as ``compaction.proactive_enabled``).

    A8 naming note: the reconciliation cadence is NOT a member here —
    it is the flat sibling field
    ``ServicesConfig.service_tool_reconcile_interval_seconds``
    (house ``{name}_interval_seconds`` convention, real
    ``Field(ge=1)`` fail-fast, read directly by the api lifespan —
    precedent ``eligible_pending_sweep_interval_seconds`` /
    ``job_lock_sweep_interval_seconds``).
    """

    enabled: bool = Field(
        default=True,
        description=(
            "Service-tool kill-switch (ENSEMBLE_SERVICE_TOOL_ENABLED, "
            "default ON). OFF = byte-identical pre-feature behavior: "
            "the ServiceToolManager gate is closed and the "
            "ServiceReconciliationService never starts. Restart to "
            "flip (resolved once at config-load time)."
        ),
    )
    max_concurrent: int = Field(
        default=10,
        ge=1,
        description=(
            "Daemon-global cap on concurrent (starting|running) "
            "services (ENSEMBLE_SERVICE_TOOL_MAX_CONCURRENT, default "
            "10 — matches the proc_tools MAX_PROCESSES_PER_INSTANCE "
            "precedent). Enforced by ServiceToolManager.start BEFORE "
            "the spawn; the value is fail-fast at boot via ge=1."
        ),
    )


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
    # restart-orphan signature: no Task is linked to the JobItem
    # via ``work_id``, so the JobItem is now ``active`` with
    # nothing to drive it forward. The reconciler Pattern (f1)
    # finalizes such JobItems to ``admission_state='dead'``
    # (DEAD) — distinct from Pattern (a)'s ``failed`` outcome.
    # The 15-minute default
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
    # Pattern (f1) subtree-alive guard — tree-activity window
    # (f1-misfire batch, incident 2026-08-31, JobItem 69a34b35).
    # ``last_activity_at`` freezes on waiting_children parents, so
    # the guard aggregates MAX(last_activity_at) over the whole
    # permanent lineage: a tree with activity inside this window is
    # ALIVE and must never be DEAD-finalized by f1, even when no
    # Task links to the JobItem via work_id. Also gated by the
    # ENSEMBLE_ORPHAN_F1_ENABLED kill-switch (env-only, default ON).
    f1_tree_activity_max_age_seconds: int = Field(
        default=900,
        ge=1,
        description=(
            "Subtree-alive window (seconds) for Pattern (f1)'s "
            "tree guard: when the JobItem's lineage tree reports "
            "MAX(last_activity_at) within this window, the f1 "
            "DEAD finalize is skipped (live work exists under a "
            "different work_id — the f1-misfire class). Default "
            "900s = 15 minutes, matching the f1 grace."
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
    eligible_pending_sweep_interval_seconds: int = Field(
        default=90,
        ge=1,
        description=(
            "Batch A — A3 (2026-09-11): how often the eligible-PENDING "
            "sweep runs (seconds). Default 90s (midpoint of the 60-120s "
            "brief range). The sweep is ALWAYS-ON infrastructure (no "
            "kill-switch env var — per the project owner's HARD POLICY "
            "on Batch A); the interval knob tunes responsiveness vs DB "
            "load. Lower = more responsive but more DB scans; the floor "
            "is 1s to prevent spin. Out-of-range values FAIL FAST AT BOOT "
            "via pydantic ValidationError at Settings instantiation "
            "(deliberate fail-fast, not a runtime disable). Override "
            "via SERVICES_ELIGIBLE_PENDING_SWEEP_INTERVAL_SECONDS env var."
        ),
    )
    eligible_pending_sweep_min_pending_age_seconds: int = Field(
        default=60,
        ge=0,
        description=(
            "Batch A — A3 (2026-09-11): minimum age (seconds) for a "
            "PENDING task to be considered eligible for the wake. "
            "Default 60s (the brief lower bound) — fresh enqueues are "
            "left alone to avoid racing with the natural claim path. "
            "The atomic claim guard on the worker-pool side prevents "
            "double-dispatch even when the sweep heals a row that is "
            "about to be claimed anyway. Out-of-range values FAIL FAST "
            "AT BOOT. Override via "
            "SERVICES_ELIGIBLE_PENDING_SWEEP_MIN_PENDING_AGE_SECONDS."
        ),
    )
    orphan_watcher_sweep_interval_seconds: int = Field(
        default=90,
        ge=1,
        description=(
            "Batch C — C2 (2026-09-11): how often the orphan-watcher "
            "sweep runs (seconds). Default 90s — shares the A3 cadence. "
            "The sweep is ALWAYS-ON infrastructure (no kill-switch env "
            "var — per the project owner's HARD POLICY on Batch A); "
            "the interval knob tunes responsiveness vs DB load. The "
            "sweep is the steady-state companion to the startup-time "
            "``DependencyBus.start()`` sweep: the startup sweep cleans "
            "the restart-window, the periodic sweep cleans orphans "
            "that accumulate mid-run (mid-run force-cancel, mid-run "
            "task death). Lower = more responsive but more DB scans; "
            "the floor is 1s to prevent spin. Out-of-range values FAIL "
            "FAST AT BOOT via pydantic ValidationError at Settings "
            "instantiation (deliberate fail-fast, not a runtime "
            "disable). Override via "
            "SERVICES_ORPHAN_WATCHER_SWEEP_INTERVAL_SECONDS env var."
        ),
    )
    orphan_watcher_sweep_grace_seconds: int = Field(
        default=30,
        ge=0,
        description=(
            "Batch C — W-C (2026-09-11): grace window (seconds) that "
            "protects young PENDING watchers from the orphan sweep "
            "(``DependencyBus._sweep_orphan_watchers``). Watchers "
            "whose ``created_at`` is newer than ``now - grace`` are "
            "NOT cancelled, even if their ``source_task_id`` is no "
            "longer in the active-task set — the natural "
            "``emit_terminal`` path needs the commit→emit window to "
            "transition the watcher to FIRED before the sweep races "
            "it. Default 30s comfortably exceeds the worst-case "
            "commit→emit latency observed in production. Floor 0 "
            "(= disable grace; the sweep cancels every orphan "
            "regardless of age — same as the pre-W-C behavior, kept "
            "for operators who want the unbounded race surface "
            "during incident triage). Out-of-range values FAIL FAST "
            "AT BOOT via pydantic ValidationError. The grace is "
            "ALWAYS-ON infrastructure (no kill-switch env var — "
            "per the project owner's HARD POLICY on Batch A); the "
            "knob tunes responsiveness vs the commit→emit race. "
            "Override via SERVICES_ORPHAN_WATCHER_SWEEP_GRACE_SECONDS "
            "env var.\n\n            **SCOPE — periodic sweep only.** "
            "This knob governs the steady-state sweep tick driven "
            "by ``OrphanWatcherSweepService.sweep_once`` (wired in "
            "``daemon/api.py``). The STARTUP sweep in "
            "``DependencyBus.start()`` (around "
            "``daemon/services/dependency_bus.py:1553``) hard-"
            "defaults to ``DEFAULT_ORPHAN_SWEEP_GRACE_SECONDS`` "
            "(30s) regardless of this knob — the startup path "
            "calls ``_sweep_orphan_watchers()`` with no argument, "
            "so the config value is NOT threaded into the boot-"
            "time cleanup. This is deliberate: the startup sweep "
            "is a one-shot restart-window cleanup, not a tunable "
            "steady-state behavior; tightening the knob for "
            "incidents does NOT change what survives the restart."
        ),
    )
    # F3 (joblock-leak fix): periodic sweep cadence for
    # ``JobLockSweepService``. The startup-time
    # ``recover_stale_job_locks`` (in ``daemon/api.py``) clears
    # orphans from a previous process that died mid-execution; this
    # knob governs the steady-state companion that reclaims locks
    # left behind by the live daemon (F1 inline writers that won
    # the SQL guard race before ``_finalize_job_db_sync`` could
    # release the lock; F2 R6/R7 cancellations that aborted
    # mid-release; F5 ``force_finalize_orphan`` reaps). The sweep
    # is ALWAYS-ON infrastructure (no kill-switch env var — per
    # the project owner's HARD POLICY on Batch A); the interval
    # knob tunes reclaim responsiveness vs DB load. Default 90s
    # shares the A3 cadence. Lower = faster reclaim but more DB
    # scans; floor 1s prevents spin. Out-of-range values FAIL FAST
    # AT BOOT via pydantic ValidationError. Override via
    # SERVICES_JOB_LOCK_SWEEP_INTERVAL_SECONDS.
    job_lock_sweep_interval_seconds: int = Field(
        default=90,
        ge=1,
        description=(
            "F3 — joblock-leak fix (2026-09-14): how often the "
            "``JobLockSweepService`` reclaim tick runs (seconds). "
            "Default 90s shares the A3 cadence. The sweep calls "
            "``JobLockManager.cleanup_terminal_job_locks`` which "
            "delegates to ``LockRepository.clear_terminal_job_locks`` "
            "(DELETEs rows whose job is no longer in {queued, active}). "
            "Reclaims locks orphaned by F1 inline writer races, F2 "
            "cancellation mid-release, and F5 orphan reaps. "
            "ALWAYS-ON infrastructure (no kill-switch env var — per "
            "the project owner's HARD POLICY on Batch A); the "
            "interval knob tunes responsiveness vs DB load. "
            "Floor 1s; out-of-range values FAIL FAST AT BOOT. "
            "Override via SERVICES_JOB_LOCK_SWEEP_INTERVAL_SECONDS."
        ),
    )
    # Phase 3 of plane-integration-revival — retry machinery. Knobs for
    # ``PlaneSyncWatchdogService``. Mirrors the F3 / job-lock-sweep
    # convention (no kill-switch — ALWAYS-ON infrastructure; the only
    # lever is the interval). Defaults match
    # ``daemon/constants.py:PLANE_SYNC_WATCHDOG_*`` so the two stay
    # in lock-step.
    plane_sync_watchdog_interval_seconds: int = Field(
        default=300,
        ge=5,
        description=(
            "Phase 3 / plane-integration-revival (2026-09-20): how "
            "often ``PlaneSyncWatchdogService`` runs its re-drive "
            "sweep for projects in error/drift states (seconds). "
            "Default 300s — 5min cadence matches the SaaS API rate "
            "envelope (Plane does not need sub-minute retry). Lower = "
            "faster recovery for stuck projects but more API traffic; "
            "floor 5s prevents spin. Out-of-range values FAIL FAST AT "
            "BOOT via pydantic ValidationError. ALWAYS-ON (no kill-"
            "switch — per project owner's HARD POLICY on Batch A). "
            "Override via SERVICES_PLANE_SYNC_WATCHDOG_INTERVAL_SECONDS."
        ),
    )
    plane_sync_watchdog_backoff_base_seconds: int = Field(
        default=60,
        ge=1,
        description=(
            "Phase 3 / plane-integration-revival: per-project "
            "exponential backoff base (seconds). attempt_count=2 → "
            "BASE wait; attempt_count=3 → 2*BASE; etc., capped at "
            "plane_sync_watchdog_backoff_max_seconds. Default 60s. "
            "Override via SERVICES_PLANE_SYNC_WATCHDOG_BACKOFF_BASE_SECONDS."
        ),
    )
    plane_sync_watchdog_backoff_max_seconds: int = Field(
        default=1800,
        ge=1,
        description=(
            "Phase 3 / plane-integration-revival: per-project "
            "exponential backoff ceiling (seconds). Default 1800s "
            "(30min) — long outages do not push retry delay into the "
            "hours. Override via SERVICES_PLANE_SYNC_WATCHDOG_BACKOFF_MAX_SECONDS."
        ),
    )
    plane_sync_watchdog_max_attempts: int = Field(
        default=5,
        ge=1,
        description=(
            "Phase 3 / plane-integration-revival: consecutive-failure "
            "threshold above which the watchdog stops re-driving a "
            "project (writes a terminal dead-letter hint to "
            "plane_last_error). The operator can call POST "
            "/api/plane/sync/{project_id} to reset attempt_count and "
            "re-drive. Default 5 ≈ 25min of steady-state sweeps before "
            "quarantine. Override via SERVICES_PLANE_SYNC_WATCHDOG_MAX_ATTEMPTS."
        ),
    )
    # clipboard-image-chat Phase 1 / Task 2b (architect amendment #3):
    # hard cap on the tmp-image store's total disk usage. The store
    # performs a walkdir sum BEFORE each write and raises
    # ``TmpImageStoreFull`` if ``current + new_size > max_bytes``;
    # the router turns that into HTTP 507. This is the ONLY safeguard
    # against unbounded fill while phase 3's sweep service is
    # pending — per architect ruling the store is the choke point
    # (no kill-switch, no per-upload override). Default 1 GiB is
    # generous enough for ~10k typical clipboard pastes; raise via
    # SERVICES_TMP_IMAGE_STORE_MAX_BYTES. Floor 1 MiB — out-of-range
    # values FAIL FAST AT BOOT via pydantic ValidationError.
    tmp_image_store_max_bytes: int = Field(
        default=1024 ** 3,
        ge=1024 ** 2,
        description=(
            "clipboard-image-chat Phase 1 (Task 2b, architect "
            "amendment #3): hard cap on the tmp-image store's total "
            "disk usage in bytes. The store walks the directory "
            "before each write and refuses the write (HTTP 507) if "
            "``current_total + new_size > max_bytes``. Default 1 GiB; "
            "override via SERVICES_TMP_IMAGE_STORE_MAX_BYTES. Floor "
            "1 MiB — out-of-range values FAIL FAST AT BOOT. NO "
            "kill-switch — the cap is the safety net until phase 3 "
            "ships the retention sweep."
        ),
    )
    # clipboard-image-chat Phase 3 / Tasks 2 + 3 (architect amendment
    # #15): cadence + retention window for ``TmpImageCleanupService``
    # — the always-on sweep that reaps ``data/tmp_images/`` entries
    # older than the retention window. Two knobs, NOT three: the
    # kill-switch (``tmp_image_cleanup_enabled`` /
    # ``SERVICES_TMP_IMAGE_CLEANUP_ENABLED``) is DELETED per amendment
    # #15 — it contradicts the project owner's HARD POLICY on
    # always-on infrastructure codified in ``job_lock_sweep.py`` (the
    # unique failure mode of a toggle is silent permanent storage
    # growth when flipped by accident). The retention-days knob IS
    # the operator lever (set it very large to effectively disable
    # reaping without removing the service).
    tmp_image_cleanup_interval_seconds: int = Field(
        default=3600,
        ge=1,
        description=(
            "clipboard-image-chat Phase 3 (Task 2, architect §7): how "
            "often ``TmpImageCleanupService`` runs its retention "
            "sweep over ``data/tmp_images/`` (seconds). Default "
            "3600s — PINNED hourly (was 86400 in the draft plan; "
            "hourly wins because deletion latency ≤ retention + "
            "interval and the scan is a cheap filesystem stat-walk, "
            "no DB). The sweep is ALWAYS-ON infrastructure (no "
            "kill-switch env var — per the project owner's HARD "
            "POLICY on Batch A); the interval knob tunes deletion "
            "latency vs scan frequency. Floor 1s; out-of-range "
            "values FAIL FAST AT BOOT via pydantic ValidationError. "
            "Restart to flip (resolved once at config-load time). "
            "Override via SERVICES_TMP_IMAGE_CLEANUP_INTERVAL_SECONDS."
        ),
    )
    tmp_image_cleanup_retention_days: int = Field(
        default=30,
        ge=1,
        description=(
            "clipboard-image-chat Phase 3 (Task 2, architect §7): "
            "age threshold for the tmp-image retention sweep — "
            "entries in ``data/tmp_images/`` older than this many "
            "days (age source: the sidecar's ``uploaded_at`` "
            "timestamp, falling back to the file's mtime when the "
            "sidecar is missing or unparseable) are reaped as a "
            "blob+sidecar pair. Default 30 days. This knob IS the "
            "operator lever for the ALWAYS-ON sweep (no kill-switch "
            "env var — per the project owner's HARD POLICY on Batch "
            "A): set it to a very large value to effectively "
            "disable reaping without removing the service. Floor 1 "
            "day — 0 would delete everything on the first tick. "
            "Out-of-range values FAIL FAST AT BOOT via pydantic "
            "ValidationError. Restart to flip. Override via "
            "SERVICES_TMP_IMAGE_CLEANUP_RETENTION_DAYS."
        ),
    )
    # service-tool Phase 1 (A8): reconciliation cadence for
    # ``ServiceReconciliationService`` — the D6 boot sweep +
    # periodic PID-liveness / start-time-match reconcile that marks
    # dead or recycled services EXITED. House
    # ``{name}_interval_seconds`` convention (real ``Field(ge=1)``
    # fail-fast knob read DIRECTLY by ``daemon/api.py`` — no getattr
    # fallback); precedent
    # ``eligible_pending_sweep_interval_seconds`` above. Default 90s
    # shares the A3/eligible/orphan/job-lock sweep cadence. The
    # enabled / max_concurrent knobs live in the nested
    # ``ServiceToolConfig`` block (``service_tool.``) — the interval
    # is flat here so the lifespan read matches the house pattern.
    service_tool_reconcile_interval_seconds: int = Field(
        default=90,
        ge=1,
        description=(
            "service-tool Phase 1 (A8): how often the "
            "``ServiceReconciliationService`` reconcile tick runs "
            "(seconds). Default 90s shares the A3 cadence. The sweep "
            "scans ``service_tracking`` rows in ('starting','running') "
            "and marks dead / PID-recycled rows EXITED (NO re-spawn). "
            "Override via ENSEMBLE_SERVICE_TOOL_RECONCILE_INTERVAL "
            "(resolved explicitly in load_config) or the "
            "SERVICES_SERVICE_TOOL_RECONCILE_INTERVAL_SECONDS "
            "BaseSettings binding. Floor 1s; out-of-range values FAIL "
            "FAST AT BOOT via pydantic ValidationError."
        ),
    )
    # service-tool Phase 1: kill-switch + cap block (mounted).
    service_tool: ServiceToolConfig = Field(
        default_factory=ServiceToolConfig,
        description=(
            "service-tool kill-switch (``enabled``) and daemon-global "
            "concurrent-services cap (``max_concurrent``). The "
            "reconciliation interval is the flat "
            "``service_tool_reconcile_interval_seconds`` sibling "
            "field (A8 house naming)."
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

    model_config = SettingsConfigDict(env_prefix="REPORT_REPAIR_", populate_by_name=True)

    enabled: bool = Field(default=True, description="Enable unhappy-path report repair")
    # Factor-5 accuracy guard (was 2.0 pre-2026-08-11). Intentional short
    # reports (e.g., governor's 36-word final message after a 143-word
    # prior turn) are NOT repaired — only mid-sentence truncation is.
    size_ratio_threshold: float = Field(default=5.0, ge=1.0, description="Word-count ratio (earlier/last) that triggers repair")
    # Agent IDs whose reports are NEVER repaired (and never carry the (c)
    # sanity marker). The default DERIVES from the shared constant
    # ``daemon.constants.REPORT_REPAIR_EXCLUDED_AGENTS`` (NR-2 lift,
    # C2-D2.15 LOCKED) — one source of truth; text-only-by-design agents
    # (wanderer, explorer, watcher) belong there, documented at the
    # constant. Override via the REPORT_REPAIR_EXCLUDED_AGENTS env var
    # (comma-separated, REPLACES the set — add or remove IDs, e.g. drop
    # ``watcher``). NR-2 fix note: the env name was previously dead —
    # ``env_prefix`` + the field name resolved to
    # ``REPORT_REPAIR_REPAIR_EXCLUDED_AGENTS`` (silently ignored), and
    # ``set[str]`` env parsing was JSON-only (comma strings crashed).
    # ``NoDecode`` + the ``_parse_repair_excluded_agents`` validator +
    # ``validation_alias`` make the documented name work; empty string →
    # empty set (explicit "no exclusions", mirroring
    # ``reasoning_echo_disabled_models``).
    repair_excluded_agents: Annotated[set[str], NoDecode] = Field(
        default_factory=lambda: set(REPORT_REPAIR_EXCLUDED_AGENTS),
        validation_alias=AliasChoices(
            "REPORT_REPAIR_EXCLUDED_AGENTS", "repair_excluded_agents"
        ),
        description="Agent IDs whose reports are never repaired (text-only-by-design agents naturally produce short zero-tool reports)",
    )

    @field_validator("repair_excluded_agents", mode="before")
    @classmethod
    def _parse_repair_excluded_agents(cls, value: Any) -> Any:
        """Accept comma-separated strings (and JSON arrays) from env / YAML.

        Delegates to ``_parse_csv_or_json_list`` for the shared parsing
        logic; the ``NoDecode`` annotation prevents pydantic-settings from
        auto-JSON-decoding env values, so we handle both forms here:
          - ``"gamma,delta"`` → ``{"gamma", "delta"}``
          - ``'["gamma"]'`` → ``{"gamma"}``
          - ``{"gamma"}`` / ``["gamma"]`` → passthrough → set
          - ``""`` or whitespace → empty set (explicit "no exclusions")

        Env format example::

            REPORT_REPAIR_EXCLUDED_AGENTS="wanderer,explorer"
        """
        if isinstance(value, (set, frozenset)):
            return set(value)
        parsed = _parse_csv_or_json_list(value)
        if isinstance(parsed, list):
            return set(parsed)
        return parsed

    # W2: tighter default timeout (30s instead of 120s) — repair should be
    # fast; on timeout we fall back to combine. 120s is excessive given the
    # prompt is bounded to recent messages.
    timeout_seconds: int = Field(default=30, description="Timeout for the repair LLM call")
    # S2: validator — must be >=1 message.
    lookback_messages: int = Field(default=5, ge=1, description="Number of recent assistant messages to pass to LLM repair")


class ReportIntegrityConfig(BaseSettings):
    """Report-integrity gate configuration (wc-wake-report-integrity).

    Hosts the (b) terminal-child-aware waiting guard's kill-switch
    (``WC_REPORT_INTEGRITY_B_TERMINAL_WAITING_GUARD_ENABLED`` — the
    name is single-homed in ``daemon/constants.py``; the derived env
    binding below MUST equal it, pinned by
    ``tests/unit/services/test_b_kill_switch_registry.py``).

    Flip semantics (decisions.md C2-D2.5-FLIP, leader-CONFIRMED
    2026-08-30 — OPERATOR-OWNED, no auto-flip exists anywhere):

    * **OFF (default, ship state)** — log-only mode: the stage-ii
      ``[ReportIntegrityGuard]`` WARNING still fires at the
      completion-stamp sites; NO notice is ever injected.
    * **ON** — enforcement: when the same-tx evaluation finds a
      declared-waiting violation at a parent-COMPLETED stamp, ONE
      adjudication notice is injected to the parent via the durable
      enqueue path (``system:report-integrity-guard``). It NEVER
      blocks completion (C2-D2.6 fail-OPEN) and never touches the
      stamp transaction.
    * **Restart required** — the resolver reads + caches the env once
      at boot; flipping mid-flight has no effect until restart.
      Truthy: ``1``/``true``/``yes``/``on``; falsy:
      ``0``/``false``/``no``/``off``; unset/blank/unknown → OFF
      (blanking the env + restart is the revert path). Soak/flip
      policy: ≤2-week stage-ii log soak, then the OPERATOR flips ON
      on first deploy; withheld on false-fires; immediate flip on
      any silent-death incident.

    The reserved candidate-(a) kill-switch name (see
    ``daemon/constants.py``, the ``WC_REPORT_INTEGRITY_A_*`` constant)
    deliberately has NO field here — reserved-unused per
    C2-D2.2/D2.3 LOCKED (and its literal must not appear in this
    module — pinned by the B.S.8 registry test).
    """

    model_config = SettingsConfigDict(env_prefix="WC_REPORT_INTEGRITY_")

    # Dual-read mirror of the ``LIMITS_GOVERNOR_RECURSION_GUARD_ENABLED``
    # precedent (config.py:~484 ``LimitsConfig.governor_recursion_guard_enabled``):
    # this Pydantic field is the declarative binding (env + optional YAML
    # override surface) while the runtime gate
    # (``daemon/services/report_integrity_guard.is_report_integrity_b_enforcement_active``)
    # reads the env via the constants NAME and ANDs this field — an
    # explicit YAML ``false`` vetoes an env flip (defense-in-depth);
    # a YAML ``true`` alone never enables (the env flip is the
    # documented operator path).
    b_terminal_waiting_guard_enabled: bool = Field(
        default=False,
        description=(
            "(b) terminal-child-aware waiting guard: OFF = log-only "
            "(stage-ii [ReportIntegrityGuard] WARNING still fires); ON = "
            "enforcement (adjudication notice injected to the parent at "
            "the completion stamp, never blocks). Restart required to "
            "flip. Operator-owned flip per C2-D2.5-FLIP."
        ),
    )


class ContextMessagesConfig(BaseSettings):
    """Context-message builder configuration (kv-ambient-awareness-fix C2).

    Hosts the DEFECT 3 kill-switch
    (``ENSEMBLE_KV_AMBIENT_SYSTEM_DEFAULT_ENABLED`` — the name is
    single-homed in ``daemon/constants.py``; the binding below uses
    that NAME constant so no literal env-name fork can appear —
    B.S.8 registry discipline, mirroring
    :class:`ReportIntegrityConfig` above).

    Shape A (decisions.md D5 RATIFIED): a daemon-config concern with
    an immediate same-class peer — ``ENSEMBLE_PROACTIVE_COMPACTION``
    (``CompactionConfig.proactive_enabled``) solves the same class of
    bug (silent ambient suppression; behavior-BUG fix default ON) and
    shares the empty-string-safe bool vocabulary
    (:data:`_PROACTIVE_TRUE_BOOLS` / :data:`_PROACTIVE_FALSE_BOOLS`).

    Polarity (decisions.md D8 RATIFIED): **default ON**; ``=0`` (or
    ``=false``/``=no``/``=off``) disables. The un-fixed behavior IS
    the bug — every default-project instance silently dropped ambient
    shared-meta-KV (phase3-plan Root Cause) — so ``=0`` restores the
    legacy suppression as the incident-revert path, not the default.

    Flip semantics: **restart-to-flip**. ``load_config`` resolves the
    effective bool once (env > yaml > default, via
    :func:`_resolve_kv_ambient_from_sources`), installs it into the
    module cache (:func:`_install_kv_ambient_system_default_enabled`)
    and emits the boot INFO line naming the resolved state; the
    runtime gate in ``daemon/services/context_messages.py`` reads the
    cache via :func:`_resolve_kv_ambient_system_default_enabled`.
    Flipping the env mid-flight has no effect until restart.
    """

    model_config = SettingsConfigDict(env_prefix="CONTEXT_MESSAGES_")

    kv_ambient_system_default_enabled: bool = Field(
        default=True,
        validation_alias=AliasChoices(
            "kv_ambient_system_default_enabled",
            ENSEMBLE_KV_AMBIENT_SYSTEM_DEFAULT_ENABLED,
        ),
        description=(
            "Surface ambient shared-meta-KV on the system-default "
            "project path (standalone [SYSTEM CONTEXT: Shared Meta KV] "
            "block when the tree-root partition has rows). Default ON. "
            "Env: ENSEMBLE_KV_AMBIENT_SYSTEM_DEFAULT_ENABLED (0/false/"
            "no/off disables — restores the legacy skip-the-DB-read "
            "suppression). Restart required to flip."
        ),
    )

    @field_validator("kv_ambient_system_default_enabled", mode="before")
    @classmethod
    def _parse_kv_ambient_system_default_enabled(cls, value: Any) -> Any:
        """Permissive bool parser (mirror of
        ``CompactionConfig._parse_proactive_enabled``).

        Bare ``KEY=`` lines in ``.env`` reach pydantic as the empty
        string; empty / whitespace-only values normalize to the
        documented ``True`` default DIRECTLY (in a ``mode="before"``
        validator ``None`` is an EXPLICIT value and would raise
        ``bool_type`` — boot crash). ``0``/``false``/``no``/``off``
        (any case) → False; ``1``/``true``/``yes``/``on`` → True;
        non-empty unrecognized strings pass through (pydantic raises a
        clear type error so a typo is caught at startup). The
        spellings live in the shared module-level
        :data:`_PROACTIVE_TRUE_BOOLS` / :data:`_PROACTIVE_FALSE_BOOLS`
        vocabularies so this validator cannot drift from
        :func:`_resolve_kv_ambient_from_sources` (Risk 7).
        """
        if value is None or (isinstance(value, str) and not value.strip()):
            return True
        if isinstance(value, str):
            v = value.strip().lower()
            if v in _PROACTIVE_FALSE_BOOLS:
                return False
            if v in _PROACTIVE_TRUE_BOOLS:
                return True
        return value


class CriticalNotesConfig(BaseModel):
    """Critical-notes tiered loading + maintenance knobs (Phase 1).

    Deliberately a PLAIN ``BaseModel`` — NOT ``BaseSettings`` — so the
    knob set has NO environment-variable binding of any kind. D4 (user
    ruling, 2026-09-15): the feature is always-on and tuned at the
    config layer ONLY; the ``ENSEMBLE_*`` env family is NOT extended
    (there is no kill-switch; rollback = redeploy the previous build).
    A ``BaseSettings`` subclass would mechanically mint
    ``CRITICAL_NOTES_*`` env names via ``env_prefix`` — an env layer in
    everything but spelling — so the stronger no-env-by-construction
    shape is used instead.

    Knobs (§4.7 as amended by leader ruling N3 — the v1 ``tiered`` knob
    is DROPPED entirely; always-on has no shape switch):

    Phase-1 consumers:
    * ``core_cap`` (8) — max pinned notes in the always-injected core
      tier; the 9th pin REJECTS with demotion candidates named.
    * ``reference_max`` (500) — tool-reject bound (authoritative) +
      injection-side truncation bound (defensive backstop only).
    * ``stale_days`` (90) — STALE marking + archive-candidate horizon
      in the list/housekeeping surface.

    Reserved Phase-2/3 fields — defaults ONLY, no consumer machinery
    ships in Phase 1: ``tail_cap``, ``section_char_cap``,
    ``fusion_bm25_weight``, ``fusion_vector_weight``,
    ``fusion_threshold``, ``floor_count``, ``query_max_chars``,
    ``mint_cap_per_read``.

    N7 (D4 compliance): the reserved Phase-3 knob ``llm_select`` gets
    NO ``ENSEMBLE_*`` env var — like every knob in this section it is
    config.yaml-only, and this class's plain-BaseModel shape makes an
    env binding structurally impossible.
    """

    core_cap: int = Field(
        default=8, ge=1,
        description="Max pinned notes in the always-injected core tier (reject-don't-evict beyond this).",
    )
    tail_cap: int = Field(
        default=6, ge=0,
        description="RESERVED Phase 2: max selected tail notes per first turn. No Phase-1 consumer.",
    )
    section_char_cap: int = Field(
        default=12000, ge=0,
        description="RESERVED Phase 2: hard char cap on the notes section of the injected block. No Phase-1 consumer.",
    )
    fusion_bm25_weight: float = Field(
        default=0.4, ge=0.0, le=1.0,
        description="RESERVED Phase 2: BM25 fusion weight (BlueprintMatcher parity). No Phase-1 consumer.",
    )
    fusion_vector_weight: float = Field(
        default=0.6, ge=0.0, le=1.0,
        description="RESERVED Phase 2: vector fusion weight. No Phase-1 consumer.",
    )
    fusion_threshold: float = Field(
        default=0.30, ge=0.0, le=1.0,
        description="RESERVED Phase 2: minimum fusion score for tail selection. No Phase-1 consumer.",
    )
    floor_count: int = Field(
        default=2, ge=0,
        description="RESERVED Phase 2: priority floor size when fusion under-selects. No Phase-1 consumer.",
    )
    reference_max: int = Field(
        default=500, ge=1,
        description="Tool-reject bound for reference (authoritative) + injection truncation bound (defensive).",
    )
    query_max_chars: int = Field(
        default=2000, ge=1,
        description="RESERVED Phase 2: query truncation for embed/BM25 input. No Phase-1 consumer.",
    )
    stale_days: int = Field(
        default=90, ge=1,
        description="Staleness horizon for STALE marks + archive-candidate proposals in list surfaces.",
    )
    mint_cap_per_read: int = Field(
        default=10, ge=0,
        description="RESERVED Phase 2: lazy embedding mint cap per first-turn read. No Phase-1 consumer.",
    )
    llm_select: bool = Field(
        default=False,
        description=(
            "RESERVED Phase 3 (C2 quick-LLM stage). Ships false and "
            "unimplemented. NO env var exists for this knob (D4)."
        ),
    )


class LongToolCallNudgeConfig(BaseSettings):
    """Configuration for the long-tool-call nudge scanner.

    Nested ``BaseSettings`` per the ``LoopBreakerConfig`` precedent
    (the modern house style for grouped infra-loop knobs). Env vars
    derive mechanically from ``env_prefix`` + field names:

    * ``LONG_TOOL_NUDGE_ENABLED`` (default ON) — kill-switch. When
      OFF: no scanner loop, no nudge delivery, and (phase 3) no
      ``set_instance_tunable`` writes. The wrapper's stamping and the
      per-completion ``[LongToolNudge] TOOL_COMPLETED`` log line
      CONTINUE by design — stamp/log presence ≠ delivery (SC9).
    * ``LONG_TOOL_NUDGE_INTERVAL_SECONDS`` (default 60, ge=1) —
      scanner tick cadence.
    * ``LONG_TOOL_NUDGE_DEFAULT_THRESHOLD_SECONDS`` (default 900,
      ge=1, le=1800) — per-child fallback when the
      ``long_tool_call_threshold_seconds`` metadata key is absent or
      invalid (read-side floor 60, AD-38).

    Canonical threshold precedence chain (AD-41): kill-switch →
    per-child metadata key → this env default → ``min(·, 1800)``
    clamp → strict ``>`` comparison in the scanner.
    """

    model_config = SettingsConfigDict(env_prefix="LONG_TOOL_NUDGE_")

    enabled: bool = Field(
        default=True,
        description="Enable the long-tool-call nudge scanner and delivery (kill-switch)",
    )
    # Empty-string convention (ladder-family precedent — mirrors
    # ``CompactionConfig._parse_proactive_enabled``). A bare
    # ``LONG_TOOL_NUDGE_ENABLED=`` line in .env reaches pydantic as the
    # empty string and would otherwise raise ``bool_parsing`` at boot
    # (kill-switch crash on a typo-free config). Empty / whitespace-only
    # → documented True default. ``"0"`` / ``"false"`` / ``"no"`` /
    # ``"off"`` → False; ``"1"`` / ``"true"`` / ``"yes"`` / ``"on"`` →
    # True; ANY other non-empty string still raises (fail-loud
    # contract preserved — 'maybe' / '2' / typos continue to crash
    # boot loud per the kill-switch convention).
    @field_validator("enabled", mode="before")
    @classmethod
    def _parse_long_tool_nudge_enabled(cls, value: Any) -> Any:
        if value is None or (isinstance(value, str) and not value.strip()):
            return True  # documented default ON (restart-pending activation)
        if isinstance(value, str):
            v = value.strip().lower()
            if v in _PROACTIVE_FALSE_BOOLS:
                return False
            if v in _PROACTIVE_TRUE_BOOLS:
                return True
            # Anything else passes through; pydantic raises with
            # a clear type error so a typo is caught at startup.
        return value

    interval_seconds: int = Field(
        default=60,
        ge=1,
        description="Seconds between long-tool-nudge scanner ticks",
    )
    default_threshold_seconds: int = Field(
        default=900,
        ge=1,
        description="Default per-child long-tool threshold in seconds (hard max 1800)",
    )

    @field_validator("default_threshold_seconds")
    @classmethod
    def _enforce_hard_max_from_canonical_home(cls, v: int) -> int:
        # Lazy import (validator-run time). The ``daemon.services``
        # package __init__ imports ``daemon.config`` at runtime
        # (job_retry_engine -> JobSystemConfig; context_messages ->
        # _resolve_kv_ambient_system_default_enabled), so a module-top
        # import here would partial-initialize config into a cycle.
        # By the time this validator runs (load_config),
        # ``daemon.config`` is fully initialized and the import
        # resolves cleanly. The constant object is STILL the
        # canonical one from ``daemon.services.long_tool_nudge``
        # (AD-30) — only the import mechanism is deferred.
        from daemon.services.long_tool_nudge import (
            HARD_MAX_THRESHOLD_SECONDS,
        )

        if v > HARD_MAX_THRESHOLD_SECONDS:
            raise ValueError(
                "default_threshold_seconds must be <= "
                f"{HARD_MAX_THRESHOLD_SECONDS} "
                f"(HARD_MAX_THRESHOLD_SECONDS); got {v}"
            )
        return v


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

    # fix-vscode-image-preview Step 1 — meta-CSP rewrite gate. When
    # ON (default), the proxy rewrites the strict meta-CSP inside the
    # webview HTML response so extension resources on the
    # ``vscode-remote+<port>.vscode-resource.vscode-cdn.net`` virtual
    # host (e.g. media-preview imagePreview.{css,js}, image bytes) are
    # permitted to load through the daemon's /vscode proxy. The
    # kill-switch ``ENSEMBLE_VSCODE_WEBVIEW_CSP_FIX`` overrides this
    # default at runtime; the env var is resolved EXPLICITLY in
    # ``load_config`` (init-kwarg-beats-env inversion trap class —
    # mirrors ``_resolve_kv_ambient_from_sources``). Restart-to-flip.
    # ``=0`` + restart restores the exact pre-fix blocking behavior
    # (incident-revert path). See ``daemon/routers/vscode_proxy.py``
    # for the rewrite seam and the resolver in this module
    # (``_resolve_vscode_webview_csp_fix_from_sources``) for the
    # precedence + empty-string contract.
    webview_csp_fix: bool = Field(default=True)


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
    slash_commands: SlashCommandConfig = Field(default_factory=SlashCommandConfig)
    services: ServicesConfig = Field(default_factory=ServicesConfig)
    job_system: JobSystemConfig = Field(default_factory=JobSystemConfig)
    mcp_pool: McpPoolConfig = Field(default_factory=McpPoolConfig)
    skill_evolution: SkillEvolutionConfig = Field(default_factory=SkillEvolutionConfig)
    loop_breaker: LoopBreakerConfig = Field(default_factory=LoopBreakerConfig)
    long_tool_nudge: LongToolCallNudgeConfig = Field(default_factory=LongToolCallNudgeConfig)
    report_repair: ReportRepairConfig = Field(default_factory=ReportRepairConfig)
    report_integrity: ReportIntegrityConfig = Field(default_factory=ReportIntegrityConfig)
    language: LanguageConfig = Field(default_factory=LanguageConfig)
    vscode: VSCodeConfig = Field(default_factory=VSCodeConfig)
    blueprint: BlueprintConfig = Field(default_factory=BlueprintConfig)
    context_messages: ContextMessagesConfig = Field(
        default_factory=ContextMessagesConfig
    )
    critical_notes: CriticalNotesConfig = Field(
        default_factory=CriticalNotesConfig
    )


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


# ─── Spawn Intelligence (Feature #1) ──────────────────────────────────────────
# Operator-side configuration for the ``spawn_instance(model_tier="high")``
# opt-in surface. The env var ``SPAWN_INTELLIGENCE_TIER_HIGH_MODEL`` overrides
# the high-tier model name; default is ``"agentic"`` (mirrors the first
# element of ``_ALLOWED_MODELS_DEFAULT``). Process-lifetime config (A6 / D8) —
# the boot-snapshot is read ONCE in ``load_config`` (no per-spawn
# ``os.environ`` reads) and installed as ``llm.spawn_intelligence_tier_high_model``.
#
# M5 — ``_SPAWN_INTELLIGENCE_TIER_HIGH_DEFAULT`` is the SINGLE SOURCE of
# truth for the documented default. The constant itself is declared at
# module top (just before ``LLMConfig`` — so the ``Field(default=...)``
# at :425 can reference it directly at class-construction time). This
# block retains the cross-ref comment so future readers know the
# canonical home is upstream of LLMConfig. Consumed by:
#   (a) the Pydantic ``Field(default=...)`` at daemon/config.py:425,
#   (b) ``_resolve_intelligence_tier_high_model`` below (returns it as
#       the empty=unset normalization target),
#   (c) the tool layer's
#       ``getattr(..., default=_SPAWN_INTELLIGENCE_TIER_HIGH_DEFAULT)``
#       fallback at ``daemon/tools/instance.py`` (Feature #1 resolver
#       block). Changing the constant ripples to all three call sites
#       automatically. Do not re-declare the literal elsewhere.


def _resolve_intelligence_tier_high_model(env_value: str | None) -> str:
    """Resolve the configured high-tier model for ``model_tier="high"`` spawns.

    Pure function — caller passes the env-var string (or ``None``); we
    apply the ``_clean_env_value`` empty/whitespace normalization and
    fall back to the documented default. No ``os.environ`` reads here;
    ``load_config`` does the single env lookup and calls this helper.

    Args:
        env_value: Raw env-var string (or ``None``).

    Returns:
        Trimmed non-empty value if the env var is set; otherwise the
        documented default ``"agentic"``.
    """
    cleaned = _clean_env_value(env_value)
    if cleaned is None:
        return _SPAWN_INTELLIGENCE_TIER_HIGH_DEFAULT
    return cleaned


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


def _resolve_compaction_model(yaml_value: Any, *, env_value: str | None) -> str:
    """Pure resolver for the ``compaction.model`` precedence chain.

    Precedence (documented contract for the compaction-model setting):

      1. ``env_value`` (``COMPACTION_MODEL``) — when SET and NON-EMPTY
         (empty/whitespace treated as UNSET), wins outright.
      2. ``yaml_value`` (``compaction.model``) — when non-blank.
      3. Unset — empty string, which the engine treats as "no override"
         (session-model accessor + ``context_window_overrides``, the
         pre-existing behavior).

    Pure function (no ``os.environ`` access): ``load_config`` does the
    env lookup once and calls this with the resolved string, mirroring
    ``_resolve_allowed_models``. The explicit resolution exists because
    passing the YAML ``compaction`` dict straight through as pydantic
    init kwargs would give YAML silent priority over the env var —
    the OPPOSITE of the documented order (see ``skill_evolution`` /
    ``blueprint`` None-strip comments in ``load_config`` for the same
    pydantic-settings init-kwarg-beats-env trap).

    ``None`` (yaml key absent or explicit ``null``) and blank strings
    normalize to ``""`` so the ``str`` field never receives ``None``
    and "unset" always means the empty string.
    """
    env_clean = _clean_env_value(env_value)
    if env_clean is not None:
        return env_clean
    if yaml_value is None:
        return ""
    if isinstance(yaml_value, str) and not yaml_value.strip():
        return ""
    return yaml_value


# agent-snapshot v1 — PR2 — ``SNAPSHOT_MODEL`` precedence chain.
# Mirrors :func:`_resolve_compaction_model` for the env-only knob
# that overrides the effective snapshot-summarization model. The
# chain documented in design-exploration.md §2.3 item 4 /
# feasibility-notes §A.3 is ``SNAPSHOT_MODEL > COMPACTION_MODEL >
# session model``. The ``SNAPSHOT_MODEL`` env is env-only
# (mirroring the operator-only "cheap tier" surface area — no
# YAML ``snapshot.model`` knob by design) and overrides the
# compaction model chain at the top of the resolution. When
# ``SNAPSHOT_MODEL`` is unset/empty, the chain delegates to
# :func:`_resolve_compaction_model` (which itself returns the
# compaction YAML/env model, or ``""`` for "no override" = session
# model).
#
# The bare-``""`` semantics — a missed env UNSET, NOT a fallback to
# session model — is the load-bearing invariant called out in
# design §2.3 item 4 ("a bare ``""`` must NOT fall straight to the
# session model — that would make every snapshot a main-model call"):
# the chain DELIBERATELY threads through the compaction resolver,
# so unset-env snapshots inherit whatever the operator pinned on
# the compaction tier (cheap by default) instead of leaping to the
# session model.
def _resolve_snapshot_model(env_value: str | None) -> str:
    """Pure resolver for the ``SNAPSHOT_MODEL`` env-only override.

    Precedence (documented contract for the snapshot-model setting —
    agent-snapshot v1, design-exploration §2.3 / feasibility-notes
    §A.3):

      1. ``env_value`` (``SNAPSHOT_MODEL``) — when SET and NON-EMPTY
         (empty/whitespace treated as UNSET, like every other env
         override in this module — see :func:`_clean_env_value`),
         wins outright. A snapshot-side operator who pinned the
         cheap-tier model explicitly gets cheap snapshot calls.
      2. Unset — empty string (``""``). The chain in
         :func:`daemon.compaction.resolve_snapshot_model`
         interprets ``""`` as "fall through to the compaction chain,
         then session model". This is THE explicit ``""``-never-means-
         session-model bullet from design §2.3 item 4 — the empty
         string here is a signal to the chain resolver, NOT a
         session-model request.

    Pure function (no ``os.environ`` access). The boot path
    (``:func:`_install_snapshot_model_for_boot`` and
    :func:`load_config`) reads ``os.environ`` ONCE and threads the
    string through to the resolver; the runtime caller of
    :func:`daemon.compaction.resolve_snapshot_model` does the same
    so the snapshot service in Wave 1b has access to the resolved
    value at call time.

    ``None`` and blank strings normalize to ``""`` so the ``str``
    channel never receives ``None`` and "unset" always means the
    empty string.
    """
    cleaned = _clean_env_value(env_value)
    if cleaned is None:
        return ""
    return cleaned


# Module-level resolved value for the snapshot-model chain. Boot
# path installs it once via :func:`_install_snapshot_model_for_boot`;
# runtime callers (the snapshot service in Wave 1b) read it via
# :func:`get_snapshot_model_env_resolved` (no-arg, cached, mirrors
# ``_VSCODE_WEBVIEW_CSP_FIX`` precedent). Tests use
# :func:`_reset_snapshot_model_resolved_for_tests` to go back to cold
# between cases.
_SNAPSHOT_MODEL_RESOLVED: str | None = None


def _install_snapshot_model_for_boot(env_value: str | None) -> None:
    """Install the resolved ``SNAPSHOT_MODEL`` env value into the
    module cache (boot path).

    Called by :func:`load_config` after the env is read once. The
    installed value is the post-resolution field value so the boot
    log and the runtime gate can never disagree.

    Mirrors the existing module-cached install pattern
    (:func:`_install_vscode_webview_csp_fix`,
    :func:`_install_proactive_enabled`).
    """
    global _SNAPSHOT_MODEL_RESOLVED
    _SNAPSHOT_MODEL_RESOLVED = _resolve_snapshot_model(env_value)


def get_snapshot_model_env_resolved() -> str:
    """Read the resolved snapshot-model env value (no-arg, cached).

    Returns the operator-resolved string the boot path installed.
    Empty string (``""``) when the env was unset/blank — the
    intended "chain continues to the compaction resolver"
    sentinel, NOT a session-model request.
    """
    return _SNAPSHOT_MODEL_RESOLVED or ""


def _reset_snapshot_model_resolved_for_tests() -> None:
    """Reset the resolved snapshot-model cached value to ``None``.

    Test-only helper — production callers never invoke this. Used
    by the unit trio + the hot-reload test path to ensure
    "unset at boot" tests see the cold state, NOT a previous
    test's accidentally-leaked env value.
    """
    global _SNAPSHOT_MODEL_RESOLVED
    _SNAPSHOT_MODEL_RESOLVED = None


# Permissive parse for proactive_enabled env values. Mirrors the legacy
# ``_parse_proactive_enabled`` field validator but raises a clear
# ``ValueError`` on an unrecognized string so a typo is caught at boot
# (the field validator relied on pydantic's downstream type error;
# here, the resolver is called from ``load_config`` BEFORE the model is
# constructed, so the explicit error is more operator-friendly).
#
# Cycle 3 — the bool-spelling vocabularies now live at module scope
# (see :data:`_PROACTIVE_TRUE_BOOLS` / :data:`_PROACTIVE_FALSE_BOOLS`
# at the top of this module) and are SHARED between this resolver
# and the field validator. Pre-cycle-3 each site had its own copy
# of the literals; a future spelling added to one site but not
# the other would silently diverge (e.g. accepting "yes" at the
# field but rejecting it at the resolver).
def _parse_proactive_str(v: str) -> bool:
    """Parse a permissive proactive_enabled env value to ``bool``.

    Accepts (case-insensitive, leading/trailing whitespace ignored):
    ``"0"`` / ``"false"`` / ``"no"`` / ``"off"`` → ``False``;
    ``"1"`` / ``"true"`` / ``"yes"`` / ``"on"`` → ``True``.

    Any other non-empty string raises :class:`ValueError` with a
    message naming the bad value (caught by ``load_config`` and
    re-raised so the operator sees a clear boot failure).
    """
    s = v.strip().lower()
    if s in _PROACTIVE_FALSE_BOOLS:
        return False
    if s in _PROACTIVE_TRUE_BOOLS:
        return True
    raise ValueError(
        f"Invalid proactive_enabled value {v!r} — expected one of "
        f"0/false/no/off (disable) or 1/true/yes/on (enable)"
    )


def _parse_bool_switch(v: str, *, setting: str) -> bool:
    """Parse a permissive boolean env switch value to ``bool``.

    Generic sibling of :func:`_parse_proactive_str` (same accepted
    vocabulary, same strictness) parameterized by the setting name so
    the ValueError names the offending knob. Accepts (case-insensitive,
    whitespace-trimmed): ``"0"``/``"false"``/``"no"``/``"off"`` →
    ``False``; ``"1"``/``"true"``/``"yes"``/``"on"`` → ``True``. Any
    other non-empty string raises :class:`ValueError` (boot fails loud
    — the kill-switch convention).
    """
    s = v.strip().lower()
    if s in _PROACTIVE_FALSE_BOOLS:
        return False
    if s in _PROACTIVE_TRUE_BOOLS:
        return True
    raise ValueError(
        f"Invalid {setting} value {v!r} — expected one of "
        f"0/false/no/off (disable) or 1/true/yes/on (enable)"
    )


def _resolve_empty_response_guard_enabled(env_value: str | None) -> bool:
    """Pure resolver for the ``ENSEMBLE_EMPTY_RESPONSE_GUARD`` kill-switch.

    Empty-response-guard Phase 1 item 5. Mirrors the
    :func:`_resolve_proactive_enabled` env-first contract, minus a YAML
    field (the knob is env-only by design — no config section exists for
    it; documented default ON). ``load_config`` calls this and installs
    the result into ``daemon.response_validation`` via
    :func:`daemon.response_validation.install_empty_guard_config`, so
    pydantic-settings never re-reads the env and a bare ``KEY=`` line in
    .env (empty string) normalizes to the default instead of crashing
    boot. An operator typo MUST fail boot loud (ValueError) per the
    kill-switch convention.
    """
    cleaned = _clean_env_value(env_value)
    if cleaned is None:
        return True  # documented default ON (restart-pending activation)
    return _parse_bool_switch(cleaned, setting="ENSEMBLE_EMPTY_RESPONSE_GUARD")


def _resolve_empty_guard_compaction_skip(env_value: str | None) -> bool:
    """Pure resolver for ``ENSEMBLE_EMPTY_GUARD_COMPACTION_SKIP``.

    Empty-response-guard Phase 1 item 5. Default OFF = the S1 guard
    stays ACTIVE on compaction summarizer calls (leader decision): an
    empty summary retries then lands in the existing truncation
    fallback. ON = the compaction call sites opt out via
    ``response_validation.empty_guard_disabled``. Same env-only +
    fail-loud contract as :func:`_resolve_empty_response_guard_enabled`.
    """
    cleaned = _clean_env_value(env_value)
    if cleaned is None:
        return False  # documented default OFF (guard active on compaction)
    return _parse_bool_switch(cleaned, setting="ENSEMBLE_EMPTY_GUARD_COMPACTION_SKIP")


# ── Hallucination-recovery ladder kill-switches (phase 1) ───────────────────
#
# ADR-0008 convention: master + per-class sub-flags; OFF = byte-identical
# ROUTING with telemetry intentionally KEPT (W1 precedent — OFF-mode storms
# must stay visible during an OFF soak). Resolver contract mirrors
# :func:`_resolve_empty_response_guard_enabled`: pure env-first resolver,
# empty/whitespace normalizes to the documented default (a bare ``KEY=``
# line in .env must never brick boot), any other unrecognized value raises
# :class:`ValueError` naming the knob (boot fails loud — kill-switch
# convention). The RESOLVED values are installed once at config-resolution
# time (``load_config``) so the runtime gates and the boot log can never
# disagree; restart-required to flip (install happens at boot only).
#
# * Master ``ENSEMBLE_SYMPTOM_REPAIR_LADDER`` — default ON,
#   restart-pending. OFF ⇒ every new branch is gated BEFORE behavior:
#   the durable repair path is a no-op pass-through, the shipped
#   ``LoopRepairer`` transient routing + WARN+continue exhaustion are
#   preserved byte-identically (P-11 / T-8 golden routing pins).
# * Sub ``ENSEMBLE_REPAIR_LOOP_DURABLE`` — phase-1 enrollment granularity
#   for the loop class (ADR-0002), default ON. Surgical disable of the
#   durable carrier without killing the whole ladder surface.

_SYMPTOM_REPAIR_LADDER_ENABLED: bool | None = None
_REPAIR_LOOP_DURABLE_ENABLED: bool | None = None


def _resolve_symptom_repair_ladder(env_value: str | None) -> bool:
    """Pure resolver for the ``ENSEMBLE_SYMPTOM_REPAIR_LADDER`` master kill-switch.

    Hallucination-recovery ladder phase 1 (F-1). Mirrors
    :func:`_resolve_empty_response_guard_enabled` (env-only knob — no
    config section exists for it; documented default ON, restart-pending).
    """
    cleaned = _clean_env_value(env_value)
    if cleaned is None:
        return True  # documented default ON (restart-pending activation)
    return _parse_bool_switch(cleaned, setting="ENSEMBLE_SYMPTOM_REPAIR_LADDER")


def _resolve_repair_loop_durable(env_value: str | None) -> bool:
    """Pure resolver for the ``ENSEMBLE_REPAIR_LOOP_DURABLE`` sub kill-switch.

    Phase-1 loop-class enrollment granularity (ADR-0008). Same contract
    as :func:`_resolve_symptom_repair_ladder`; default ON.
    """
    cleaned = _clean_env_value(env_value)
    if cleaned is None:
        return True  # documented default ON (restart-pending activation)
    return _parse_bool_switch(cleaned, setting="ENSEMBLE_REPAIR_LOOP_DURABLE")


def get_symptom_repair_ladder_enabled() -> bool:
    """Read the resolved master ladder kill-switch (no-arg, cached).

    Warm cache (``load_config`` already ran): return the installed value.
    Cold cache (tests / programmatic boots that never call
    ``load_config``): resolve ONCE from the env directly and cache —
    an unrecognized non-empty value raises (fail loud, never silently
    defaulted). SILENT either way — the boot INFO line is owned by
    ``load_config``.
    """
    global _SYMPTOM_REPAIR_LADDER_ENABLED
    if _SYMPTOM_REPAIR_LADDER_ENABLED is None:
        raw = _clean_env_value(os.environ.get("ENSEMBLE_SYMPTOM_REPAIR_LADDER"))
        _SYMPTOM_REPAIR_LADDER_ENABLED = (
            True
            if raw is None
            else _parse_bool_switch(raw, setting="ENSEMBLE_SYMPTOM_REPAIR_LADDER")
        )
    return _SYMPTOM_REPAIR_LADDER_ENABLED


def get_repair_loop_durable_enabled() -> bool:
    """Read the resolved loop-durable sub kill-switch (no-arg, cached)."""
    global _REPAIR_LOOP_DURABLE_ENABLED
    if _REPAIR_LOOP_DURABLE_ENABLED is None:
        raw = _clean_env_value(os.environ.get("ENSEMBLE_REPAIR_LOOP_DURABLE"))
        _REPAIR_LOOP_DURABLE_ENABLED = (
            True
            if raw is None
            else _parse_bool_switch(raw, setting="ENSEMBLE_REPAIR_LOOP_DURABLE")
        )
    return _REPAIR_LOOP_DURABLE_ENABLED


def _install_symptom_repair_ladder_config(
    *, ladder_enabled: bool, loop_durable_enabled: bool
) -> None:
    """Install the resolved ladder knobs (boot path — called by load_config).

    Mirrors ``install_empty_guard_config``: the RESOLVED values are
    installed once at config-resolution time so the runtime gates and the
    boot log can never disagree. Restart-required to pick up an env flip.
    """
    global _SYMPTOM_REPAIR_LADDER_ENABLED, _REPAIR_LOOP_DURABLE_ENABLED
    _SYMPTOM_REPAIR_LADDER_ENABLED = bool(ladder_enabled)
    _REPAIR_LOOP_DURABLE_ENABLED = bool(loop_durable_enabled)


def _reset_symptom_repair_ladder_for_tests() -> None:
    """Clear the cached kill-switch state (unit-test isolation only)."""
    global _SYMPTOM_REPAIR_LADDER_ENABLED, _REPAIR_LOOP_DURABLE_ENABLED
    _SYMPTOM_REPAIR_LADDER_ENABLED = None
    _REPAIR_LOOP_DURABLE_ENABLED = None


def _resolve_proactive_enabled(
    yaml_value: Any,
    *,
    ens_value: str | None,
    cpe_value: str | None,
) -> bool:
    """Pure resolver for the ``compaction.proactive_enabled`` kill-switch.

    Cycle 2 of ``feature/proactive-compaction-fix`` (review W-1 + W-2).
    Explicit resolution for the SAME reason as
    :func:`_resolve_compaction_model` (pydantic-settings treats a
    passed-in init kwarg as taking priority over env vars — a YAML
    ``proactive_enabled: true`` would silently defeat an operator
    ``ENSEMBLE_PROACTIVE_COMPACTION=0`` kill-switch and weaken the
    incident-revert path). ``load_config`` reads the env once, calls
    this function, and passes the resolved ``bool`` as an init kwarg
    so pydantic-settings never re-reads the env itself.

    Precedence (documented contract for the kill-switch):

      1. ``ens_value`` (``ENSEMBLE_PROACTIVE_COMPACTION``) — when SET
         and NON-EMPTY (empty/whitespace treated as UNSET, per
         :func:`_clean_env_value` shell-style ``:-`` semantics), wins
         outright. This is the documented env name.
      2. ``cpe_value`` (``COMPACTION_PROACTIVE_ENABLED``) — when SET
         and NON-EMPTY AND ``ens_value`` is unset/empty. The legacy
         alias kept for back-compat with operators who use the
         section's ``env_prefix="COMPACTION_"``-style name; no
         deprecation warning (single kill-switch — minimal surprise).
      3. ``yaml_value`` (``compaction.proactive_enabled``) — when env
         is unset/empty, used as-is if it's already a ``bool``;
         parsed via :func:`_parse_proactive_str` if it's a string.
      4. Default ``True`` (documented ON; ADDENDUM §A.2) — only
         reached when env is unset/empty AND yaml is absent or
         explicit ``None``.

    Empty env normalization (W-1): a bare ``KEY=`` line in ``.env``
    (re-exported as empty string via ``launcher.sh``
    ``load_env_file``) used to crash the daemon at boot —
    pydantic-settings converts the empty string to ``None`` for a
    ``bool`` field, which the field validator cannot recover into the
    documented default. Resolving in ``load_config`` first means the
    model field receives the explicit resolved ``bool``, bypassing
    pydantic-settings env-var handling entirely. An operator typo on
    the kill-switch itself MUST NEVER brick boot — the empty string
    falls through to the yaml value (or the documented ON default).
    """
    ens_clean = _clean_env_value(ens_value)
    if ens_clean is not None:
        return _parse_proactive_str(ens_clean)
    cpe_clean = _clean_env_value(cpe_value)
    if cpe_clean is not None:
        return _parse_proactive_str(cpe_clean)
    # Env unset / empty → yaml or default.
    if isinstance(yaml_value, bool):
        return yaml_value
    if yaml_value is None:
        return True  # documented default ON
    if isinstance(yaml_value, str):
        if not yaml_value.strip():
            # Defensive — yaml shipped an empty string. Same as unset.
            return True
        return _parse_proactive_str(yaml_value)
    # Anything else (int, etc.) — coerce via truthiness; the field
    # type is ``bool`` and the upstream pydantic layer is the
    # canonical truthy check site. Booleans and strings cover the
    # realistic yaml shapes; an int is treated as truthy to match
    # the prior validator's pass-through.
    return bool(yaml_value)


# ── service-tool knobs (Phase 1 of service-tool) ────────────────────────────
#
# Resolved-once cache (restart-to-flip; Shape-A precedent —
# ``_KV_AMBIENT_SYSTEM_DEFAULT_ENABLED`` /
# ``_VSCODE_WEBVIEW_CSP_FIX``). ``load_config`` reads each
# ``ENSEMBLE_SERVICE_TOOL_*`` env ONCE at TOP LEVEL (outside the
# ``"services" in processed_config`` guard — the section-absent
# review-MAJOR-2 class), resolves via the ``_resolve_service_tool_*``
# functions below, installs the kill-switch here via
# :func:`_install_service_tool_enabled`, and emits the one boot INFO
# line naming the resolved state (S13 reviewer gate: emitted AT
# CONFIG-RESOLUTION time — a lazy first-call emit would make a
# quiet-daemon boot-log grep false-fail).
_SERVICE_TOOL_ENABLED: bool | None = None

#: Env var names — kept as module constants so the boot probe, the
#: resolvers, and tests all name the operator surface from one place.
ENSEMBLE_SERVICE_TOOL_ENABLED = "ENSEMBLE_SERVICE_TOOL_ENABLED"
ENSEMBLE_SERVICE_TOOL_MAX_CONCURRENT = "ENSEMBLE_SERVICE_TOOL_MAX_CONCURRENT"
ENSEMBLE_SERVICE_TOOL_RECONCILE_INTERVAL = "ENSEMBLE_SERVICE_TOOL_RECONCILE_INTERVAL"


def _resolve_service_tool_enabled(ens_value: str | None, yaml_value: Any) -> bool:
    """Pure resolver for the ``ENSEMBLE_SERVICE_TOOL_ENABLED`` kill-switch.

    Precedence: env > yaml (``services.service_tool.enabled``) >
    default ``True``. Empty-string safe (a bare ``KEY=`` line in
    ``.env`` normalizes to UNSET — the ``_clean_env_value``
    contract); an unrecognized NON-empty value raises
    :class:`ValueError` naming the env (kill-switch fail-loud
    convention — ``_parse_bool_switch``).
    """
    cleaned = _clean_env_value(ens_value)
    if cleaned is not None:
        return _parse_bool_switch(cleaned, setting=ENSEMBLE_SERVICE_TOOL_ENABLED)
    if isinstance(yaml_value, bool):
        return yaml_value
    if yaml_value is None:
        return True  # documented default ON (D7)
    if isinstance(yaml_value, str):
        if not yaml_value.strip():
            return True  # defensive — yaml shipped an empty string
        return _parse_bool_switch(yaml_value, setting="services.service_tool.enabled")
    return bool(yaml_value)


def _parse_service_tool_int(v: Any, *, env_name: str) -> int:
    """Strict int parse for the service-tool knob envs.

    Non-integer (or non-positive-integer-looking) strings raise
    :class:`ValueError` naming the offending env so an operator typo
    fails boot loud instead of silently falling back to the default.
    """
    if isinstance(v, bool):
        raise ValueError(
            f"Invalid {env_name} value {v!r} — expected an integer"
        )
    if isinstance(v, int):
        return v
    if isinstance(v, str):
        try:
            return int(v.strip())
        except (TypeError, ValueError):
            raise ValueError(
                f"Invalid {env_name} value {v!r} — expected an integer"
            ) from None
    raise ValueError(
        f"Invalid {env_name} value {v!r} — expected an integer"
    )


def _resolve_service_tool_max_concurrent(ens_value: str | None, yaml_value: Any) -> int:
    """Pure resolver for ``ENSEMBLE_SERVICE_TOOL_MAX_CONCURRENT``.

    Precedence: env > yaml (``services.service_tool.max_concurrent``)
    > default 10 (the ``proc_tools.MAX_PROCESSES_PER_INSTANCE``
    precedent). Empty-string safe; non-int raises
    :class:`ValueError` naming the env. The ``ge=1`` floor is
    enforced by the ``ServiceToolConfig.max_concurrent`` pydantic
    constraint at model validation (fail-fast at boot).
    """
    cleaned = _clean_env_value(ens_value)
    if cleaned is not None:
        return _parse_service_tool_int(
            cleaned, env_name=ENSEMBLE_SERVICE_TOOL_MAX_CONCURRENT
        )
    if yaml_value is None:
        return 10  # documented default (D5)
    if isinstance(yaml_value, str) and not yaml_value.strip():
        return 10  # defensive — yaml shipped an empty string
    return _parse_service_tool_int(
        yaml_value, env_name="services.service_tool.max_concurrent"
    )


def _resolve_service_tool_reconcile_interval(ens_value: str | None, yaml_value: Any) -> int:
    """Pure resolver for ``ENSEMBLE_SERVICE_TOOL_RECONCILE_INTERVAL``.

    Precedence: env > yaml > default 90 (shares the A3 sweep
    cadence). Empty-string safe; non-int raises
    :class:`ValueError` naming the env. The ``ge=1`` floor is
    enforced by the ``ServicesConfig.service_tool_reconcile_interval_seconds``
    pydantic constraint at model validation (fail-fast at boot).
    """
    cleaned = _clean_env_value(ens_value)
    if cleaned is not None:
        return _parse_service_tool_int(
            cleaned, env_name=ENSEMBLE_SERVICE_TOOL_RECONCILE_INTERVAL
        )
    if yaml_value is None:
        return 90  # documented default (D6)
    if isinstance(yaml_value, str) and not yaml_value.strip():
        return 90  # defensive — yaml shipped an empty string
    return _parse_service_tool_int(
        yaml_value, env_name="services.service_tool_reconcile_interval_seconds"
    )


def _install_service_tool_enabled(value: bool) -> None:
    """Install the RESOLVED kill-switch into the module cache.

    Called from ``load_config`` only (after the boot probe's source
    values are final). The runtime consumer (Phase 2
    ``ServiceToolManager`` gate and the api lifespan DISABLED branch)
    reads the cache via :func:`service_tool_enabled`.
    """
    global _SERVICE_TOOL_ENABLED
    _SERVICE_TOOL_ENABLED = bool(value)


def service_tool_enabled() -> bool:
    """Read the resolved service-tool kill-switch (no-arg, cached).

    SILENT — the boot INFO line is owned by ``load_config`` (S13
    reviewer gate). Warm cache: return the installed value
    (restart-to-flip). Cold cache (tests / programmatic boots that
    never call ``load_config``): resolve ONCE from the env var
    directly; unset/empty → the documented ``True`` default.
    """
    global _SERVICE_TOOL_ENABLED
    if _SERVICE_TOOL_ENABLED is None:
        raw = os.environ.get(ENSEMBLE_SERVICE_TOOL_ENABLED)
        _SERVICE_TOOL_ENABLED = _resolve_service_tool_enabled(raw, None)
    return _SERVICE_TOOL_ENABLED


def _reset_service_tool_for_tests() -> None:
    """Clear the cached kill-switch state (test-only reset)."""
    global _SERVICE_TOOL_ENABLED
    _SERVICE_TOOL_ENABLED = None


# ── kv-ambient ambient KV gate (C2 — kv-ambient-awareness-fix, Shape A) ──────
#
# Resolved-once cache (restart-to-flip; phase3-plan Kill-Switch Design →
# Restart-to-flip). ``load_config`` resolves the effective bool via
# :func:`_resolve_kv_ambient_from_sources`, installs it here via
# :func:`_install_kv_ambient_system_default_enabled`, and emits the
# one boot INFO line naming the resolved state (S13: the line is
# emitted AT CONFIG-RESOLUTION TIME — a lazy first-call emit would
# make quiet-daemon boot-log grep false-fail). The runtime gate
# (``daemon/services/context_messages.py::assemble_context_messages``)
# reads the cache via :func:`_resolve_kv_ambient_system_default_enabled`
# — flipping the env mid-flight has no effect until restart.
#
# Cache discipline (mirrors the Shape-B ``ENSEMBLE_WC_WAKE_ENQUEUE``
# precedent — instance_messaging.py:110-197 — for the caching half
# only; the CONFIG surface here is Shape A per decisions.md D5):
# ``None`` = cold (no ``load_config`` yet in this process — tests /
# programmatic boots). The cold path resolves ONCE from the env var
# directly (same vocabulary, SILENT — the boot INFO is owned by
# ``load_config``, never by the per-call accessor) so a direct
# assembler call without a boot neither crashes nor logs.
_KV_AMBIENT_SYSTEM_DEFAULT_ENABLED: bool | None = None


def _parse_kv_ambient_env_value(v: str) -> bool:
    """Parse a permissive ``ENSEMBLE_KV_AMBIENT_SYSTEM_DEFAULT_ENABLED``
    value to ``bool``.

    Accepts (case-insensitive, leading/trailing whitespace ignored):
    ``"0"`` / ``"false"`` / ``"no"`` / ``"off"`` → ``False``;
    ``"1"`` / ``"true"`` / ``"yes"`` / ``"on"`` → ``True``. Shares the
    module-level ``_PROACTIVE_TRUE_BOOLS`` / ``_PROACTIVE_FALSE_BOOLS``
    vocabularies (Risk 7 — one vocabulary, two consumers: this parser
    and the ``ContextMessagesConfig`` field validator).

    Any other non-empty string raises :class:`ValueError` with a
    message naming the bad value (caught by ``load_config`` and
    re-raised so the operator sees a clear boot failure).
    """
    s = v.strip().lower()
    if s in _PROACTIVE_FALSE_BOOLS:
        return False
    if s in _PROACTIVE_TRUE_BOOLS:
        return True
    raise ValueError(
        f"Invalid {ENSEMBLE_KV_AMBIENT_SYSTEM_DEFAULT_ENABLED} value "
        f"{v!r} — expected one of 0/false/no/off (disable) or "
        f"1/true/yes/on (enable)"
    )


def _resolve_kv_ambient_from_sources(
    yaml_value: Any,
    *,
    ens_value: str | None,
) -> bool:
    """Pure resolver for the ``context_messages`` KV-ambient kill-switch.

    Mirror of :func:`_resolve_proactive_enabled` (minus the legacy
    alias layer — this flag has no legacy alias). Explicit resolution
    for the SAME reason: pydantic-settings treats a passed-in init
    kwarg as taking priority over env vars, so a YAML
    ``kv_ambient_system_default_enabled: true`` would silently defeat
    an operator ``ENSEMBLE_KV_AMBIENT_SYSTEM_DEFAULT_ENABLED=0``
    kill-switch and weaken the incident-revert path. ``load_config``
    reads the env once, calls this function, and passes the resolved
    ``bool`` as an init kwarg so pydantic-settings never re-reads the
    env itself.

    Precedence (documented contract):

      1. ``ens_value`` (``ENSEMBLE_KV_AMBIENT_SYSTEM_DEFAULT_ENABLED``)
         — when SET and NON-EMPTY (empty/whitespace treated as UNSET
         per :func:`_clean_env_value`), wins outright.
      2. ``yaml_value`` (``context_messages.kv_ambient_system_default_enabled``)
         — when env is unset/empty, used as-is if it's already a
         ``bool``; parsed via :func:`_parse_kv_ambient_env_value` if
         it's a string.
      3. Default ``True`` (documented ON; decisions.md D8) — only
         reached when env is unset/empty AND yaml is absent or
         explicit ``None``.

    An operator typo on the kill-switch itself MUST NEVER brick boot
    via the empty-string crash class (W-1 precedent): a bare ``KEY=``
    line falls through to the yaml value (or the documented ON
    default); an unrecognized NON-empty value raises here so the
    typo is caught at startup with a flag-naming error.
    """
    ens_clean = _clean_env_value(ens_value)
    if ens_clean is not None:
        return _parse_kv_ambient_env_value(ens_clean)
    if isinstance(yaml_value, bool):
        return yaml_value
    if yaml_value is None:
        return True  # documented default ON
    if isinstance(yaml_value, str):
        if not yaml_value.strip():
            # Defensive — yaml shipped an empty string. Same as unset.
            return True
        return _parse_kv_ambient_env_value(yaml_value)
    # Anything else (int, etc.) — coerce via truthiness, mirroring
    # :func:`_resolve_proactive_enabled`.
    return bool(yaml_value)


def _install_kv_ambient_system_default_enabled(value: bool) -> None:
    """Install the resolved flag into the module cache (boot path).

    Called by ``load_config`` after the ``Config`` model is
    constructed — the installed value is the post-validation field
    value, so the boot log and the runtime gate can never disagree.
    Production callers only; tests use
    :func:`_reset_kv_ambient_for_tests` to go back to cold.
    """
    global _KV_AMBIENT_SYSTEM_DEFAULT_ENABLED
    _KV_AMBIENT_SYSTEM_DEFAULT_ENABLED = bool(value)


def _resolve_kv_ambient_system_default_enabled() -> bool:
    """Read the resolved KV-ambient kill-switch (no-arg, cached).

    This is the runtime gate's ONLY read path — the assembler imports
    it from this module and calls it per assembly, so it must be
    cheap and SILENT (the boot INFO line is owned by ``load_config``;
    this accessor never logs — S13 reviewer gate).

    Warm cache (``load_config`` already ran in this process): return
    the installed value. Restart-to-flip semantics — env changes
    mid-flight are invisible.

    Cold cache (tests / programmatic boots that never call
    ``load_config``): resolve ONCE from the env var directly — same
    permissive vocabulary as :func:`_parse_kv_ambient_env_value` — and
    cache the result. Unset / empty env → the documented ``True``
    default (an unrecognized non-empty value raises, matching the
    boot-path fail-loud posture).
    """
    global _KV_AMBIENT_SYSTEM_DEFAULT_ENABLED
    if _KV_AMBIENT_SYSTEM_DEFAULT_ENABLED is None:
        raw = os.environ.get(ENSEMBLE_KV_AMBIENT_SYSTEM_DEFAULT_ENABLED)
        if raw is None or not raw.strip():
            _KV_AMBIENT_SYSTEM_DEFAULT_ENABLED = True
        else:
            _KV_AMBIENT_SYSTEM_DEFAULT_ENABLED = _parse_kv_ambient_env_value(raw)
    return _KV_AMBIENT_SYSTEM_DEFAULT_ENABLED


def _reset_kv_ambient_for_tests() -> None:
    """Clear the cached kill-switch state so tests can re-resolve after
    mutating the env. Test-only — production code never invokes this
    (mirror of ``_reset_wc_wake_enqueue_for_tests``)."""
    global _KV_AMBIENT_SYSTEM_DEFAULT_ENABLED
    _KV_AMBIENT_SYSTEM_DEFAULT_ENABLED = None


# ── VSCode webview-CSP-rewrite kill-switch (Shape A) ────────────────────────
#
# Resolved-once cache (restart-to-flip; mirrors the
# ``ENSEMBLE_KV_AMBIENT_SYSTEM_DEFAULT_ENABLED`` / ``ENSEMBLE_WC_WAKE_ENQUEUE``
# Shape-A precedent). ``load_config`` resolves the effective bool via
# :func:`_resolve_vscode_webview_csp_fix_from_sources`, installs it here
# via :func:`_install_vscode_webview_csp_fix`, and emits the one boot
# INFO line naming the resolved state. The runtime gate
# (``daemon/routers/vscode_proxy.py::_buffer_and_maybe_rewrite_webview``,
# which reads :func:`_resolve_vscode_webview_csp_fix`) — flipping the
# env mid-flight has no effect until restart.
# flipping the env mid-flight has no effect until restart.
#
# Cache discipline: ``None`` = cold (no ``load_config`` yet in this
# process — tests / programmatic boots). The cold path resolves ONCE
# from the env var directly (same vocabulary as
# :func:`_parse_kv_ambient_env_value`) so a direct accessor call without
# a boot neither crashes nor logs. The boot INFO line is owned by
# ``load_config``, NEVER by the per-call accessor (S13 reviewer gate).
_VSCODE_WEBVIEW_CSP_FIX: bool | None = None


def _resolve_vscode_webview_csp_fix_from_sources(
    yaml_value: Any,
    *,
    ens_value: str | None,
) -> bool:
    """Pure resolver for the ``ENSEMBLE_VSCODE_WEBVIEW_CSP_FIX`` kill-switch.

    Mirrors :func:`_resolve_kv_ambient_from_sources` (Shape A; init-kwarg
    beats env inversion is the same trap class — pydantic-settings treats
    a passed-in init kwarg as taking priority over env vars, so a YAML
    ``vscode.webview_csp_fix: true`` would silently defeat an operator
    ``ENSEMBLE_VSCODE_WEBVIEW_CSP_FIX=0`` kill-switch and weaken the
    incident-revert path). ``load_config`` reads the env once, calls this
    function, and passes the resolved ``bool`` as an init kwarg so
    pydantic-settings never re-reads the env itself.

    Precedence (documented contract for the kill-switch):

      1. ``ens_value`` (``ENSEMBLE_VSCODE_WEBVIEW_CSP_FIX``) — when SET
         and NON-EMPTY (empty/whitespace treated as UNSET per
         :func:`_clean_env_value`), wins outright.
      2. ``yaml_value`` (``vscode.webview_csp_fix``) — when env is
         unset/empty, used as-is if it's already a ``bool``; parsed via
         :func:`_parse_kv_ambient_env_value` if it's a string.
      3. Default ``True`` (documented ON) — only reached when env is
         unset/empty AND yaml is absent or explicit ``None``.

    Empty-string normalization (W-1 precedent): a bare ``KEY=`` line in
    ``.env`` falls through to the yaml value (or the documented ON
    default); an unrecognized NON-empty value raises here so the typo is
    caught at startup with a flag-naming error.
    """
    ens_clean = _clean_env_value(ens_value)
    if ens_clean is not None:
        return _parse_kv_ambient_env_value(ens_clean)
    if isinstance(yaml_value, bool):
        return yaml_value
    if yaml_value is None:
        return True  # documented default ON
    if isinstance(yaml_value, str):
        if not yaml_value.strip():
            # Defensive — yaml shipped an empty string. Same as unset.
            return True
        return _parse_kv_ambient_env_value(yaml_value)
    # Anything else (int, etc.) — coerce via truthiness, mirroring
    # :func:`_resolve_kv_ambient_from_sources`.
    return bool(yaml_value)


def _resolve_critical_notes_llm_select(yaml_value: Any) -> bool:
    """Explicit resolver for the reserved ``critical_notes.llm_select`` knob.

    Mirror of the ``_resolve_compaction_model`` pattern, adapted to a
    knob that has NO env source: pydantic-settings gives a passed-in
    init kwarg priority over env vars (the inversion trap), so the
    value is resolved EXPLICITLY in ``load_config`` and handed to the
    model as a normalized bool instead of a raw ``None`` (an explicit
    yaml ``llm_select: null`` would otherwise raise ``bool_type`` and
    crash boot).

    N7 / D4 compliance: this knob — like every ``critical_notes.``
    knob — gets NO ``ENSEMBLE_*`` env var. ``CriticalNotesConfig`` is a
    plain ``BaseModel`` (not ``BaseSettings``), so no env binding exists
    at all; the only sources are config.yaml and the documented default
    (``False`` until Phase 3).

    Accepted inputs: ``bool`` passthrough; ``None`` / blank string →
    default ``False``; the shared permissive bool vocabulary
    (:data:`_PROACTIVE_TRUE_BOOLS` / :data:`_PROACTIVE_FALSE_BOOLS`)
    for string spellings; anything else raises so a yaml typo is caught
    at startup.
    """
    if isinstance(yaml_value, bool):
        return yaml_value
    if yaml_value is None:
        return False  # documented default — reserved knob, Phase 3
    if isinstance(yaml_value, str):
        s = yaml_value.strip().lower()
        if not s or s in _PROACTIVE_FALSE_BOOLS:
            return False
        if s in _PROACTIVE_TRUE_BOOLS:
            return True
    raise ValueError(
        f"Invalid critical_notes.llm_select value {yaml_value!r} — "
        f"expected a boolean (0/false/no/off or 1/true/yes/on)"
    )


def _resolve_vscode_binary_path(
    yaml_value: str | None,
    *,
    env_value: str | None,
) -> str | None:
    """Pure resolver for ``VSCODE_BINARY_PATH`` (string Shape A).

    Mirrors :func:`_resolve_compaction_model` /
    :func:`_resolve_vscode_webview_csp_fix_from_sources`: pydantic-settings
    gives a passed-in init kwarg priority over env vars, so the YAML
    ``vscode.binary_path`` passthrough in ``load_config`` silently defeated
    an operator ``VSCODE_BINARY_PATH`` (live-proven: a yaml
    ``binary_path: null`` init kwarg dead the env knob, and the manager
    fell back to ``shutil.which("code-server")`` — the deprecated brew
    binary — even with the env pointing at a standalone code-server).

    Precedence (documented contract):

      1. ``env_value`` (``VSCODE_BINARY_PATH``) — when SET and NON-EMPTY
         (empty/whitespace treated as UNSET per
         :func:`_clean_env_value`), wins outright — ALWAYS, including
         over a NON-null yaml value.
      2. ``yaml_value`` (``vscode.binary_path``) — when env is
         unset/empty, used as-is if non-blank.
      3. ``None`` — env unset AND yaml null/absent/blank: the manager
         (``vscode_server_manager._resolve_binary``) then PATH-looks-up
         via ``shutil.which`` (pre-existing fallback, unchanged).

    Pure function (no ``os.environ`` access): ``load_config`` reads the
    env once and passes the resolved string-or-``None`` as the init
    kwarg, so pydantic-settings never re-reads the env itself. String
    resolver — deliberately NOT the bool parser
    (:func:`_parse_kv_ambient_env_value`); only
    :func:`_clean_env_value` empty-string normalization is shared.

    The literal env name ``VSCODE_BINARY_PATH`` at the ``load_config``
    call site MUST stay in sync with ``VSCodeConfig`` (``env_prefix=
    "VSCODE_"`` + field ``binary_path``); the unit test pins both sides.
    """
    env_clean = _clean_env_value(env_value)
    if env_clean is not None:
        return env_clean
    if yaml_value is None:
        return None
    if isinstance(yaml_value, str) and not yaml_value.strip():
        # Defensive — yaml shipped an empty string. Same as unset.
        return None
    return yaml_value


def _install_vscode_webview_csp_fix(value: bool) -> None:
    """Install the resolved flag into the module cache (boot path).

    Called by ``load_config`` — the installed value is the
    post-validation field value, so the boot log and the runtime gate
    can never disagree. Production callers only; tests use
    :func:`_reset_vscode_webview_csp_fix_for_tests` to go back to cold.
    """
    global _VSCODE_WEBVIEW_CSP_FIX
    _VSCODE_WEBVIEW_CSP_FIX = bool(value)


def _resolve_vscode_webview_csp_fix() -> bool:
    """Read the resolved webview-CSP-fix kill-switch (no-arg, cached).

    This is the runtime gate's ONLY read path — the proxy imports it
    from this module and calls it per request (cheap), so it must be
    SILENT (the boot INFO line is owned by ``load_config``; this
    accessor never logs).

    Warm cache (``load_config`` already ran in this process): return
    the installed value. Restart-to-flip semantics — env changes
    mid-flight are invisible.

    Cold cache (tests / programmatic boots that never call
    ``load_config``): resolve ONCE from the env var directly — same
    permissive vocabulary as :func:`_parse_kv_ambient_env_value` — and
    cache the result. Unset / empty env → the documented ``True``
    default; an unrecognized non-empty value raises.
    """
    global _VSCODE_WEBVIEW_CSP_FIX
    if _VSCODE_WEBVIEW_CSP_FIX is None:
        raw = os.environ.get(ENSEMBLE_VSCODE_WEBVIEW_CSP_FIX)
        if raw is None or not raw.strip():
            _VSCODE_WEBVIEW_CSP_FIX = True
        else:
            _VSCODE_WEBVIEW_CSP_FIX = _parse_kv_ambient_env_value(raw)
    return _VSCODE_WEBVIEW_CSP_FIX


def _reset_vscode_webview_csp_fix_for_tests() -> None:
    """Clear the cached kill-switch state so tests can re-resolve after
    mutating the env. Test-only — production code never invokes this
    (mirror of ``_reset_kv_ambient_for_tests``)."""
    global _VSCODE_WEBVIEW_CSP_FIX
    _VSCODE_WEBVIEW_CSP_FIX = None


def resolve_injected_notes_absorb() -> bool:
    """Resolve the ``ENSEMBLE_INJECTED_NOTES_ABSORB`` kill-switch.

    Injected-notes hoisting fix follow-up. Governs the answered-note
    absorb contract in ``daemon/compaction.py`` — when ON (default), a
    bare-flag injected note with a later ``AIMessage`` joins the
    selectable pool and is absorbed into the compacted span; when OFF,
    the absorbed-id set is empty and EVERY bare-flag note returns to
    the legacy preserve-forever hoisting (the pre-fix two-bucket
    behavior). ``context_kind`` messages are permanent in BOTH states
    (different contract, not governed by this flag).

    Env-only resolver — deliberately NOT a ``CompactionConfig`` field:
    the flag is consulted at the single boundary site
    (``_injected_note_absorbed_ids``) at compaction time, and adding a
    pydantic-settings bool field without an explicit ``_resolve_*`` in
    ``load_config`` would reintroduce the init-kwarg-beats-env
    inversion trap (a YAML/init value silently defeating the operator
    kill-switch). Mirrors the ``ENSEMBLE_PROACTIVE_COMPACTION``
    falsy-parsing convention: empty/whitespace (bare ``KEY=`` in
    ``.env``) is treated as UNSET → documented ON default;
    ``0``/``false``/``no``/``off`` (any case) → OFF;
    ``1``/``true``/``yes``/``on`` (any case) → ON; any other
    non-empty string raises :class:`ValueError` naming the flag (the
    compaction path fails open and the typo is loud, never silently
    defaulted).

    Not coupled to ``ENSEMBLE_PROACTIVE_COMPACTION`` — that flag arms
    the auto-trigger ladder (P1/P1b); this one flips the absorb
    contract itself.
    """
    raw = _clean_env_value(os.environ.get("ENSEMBLE_INJECTED_NOTES_ABSORB"))
    if raw is None:
        return True  # documented default ON
    s = raw.strip().lower()
    if s in _PROACTIVE_FALSE_BOOLS:
        return False
    if s in _PROACTIVE_TRUE_BOOLS:
        return True
    raise ValueError(
        f"Invalid ENSEMBLE_INJECTED_NOTES_ABSORB value {raw!r} — expected "
        f"one of 0/false/no/off (disable) or 1/true/yes/on (enable)"
    )


# Emit-once guard for the ``spawn_intelligence_tier_high_model`` boot
# WARNING (daemon/config.py:3254-3264). The boot path invokes
# ``load_config`` twice (daemon/api.py:245 lifespan startup +
# daemon/services/attestation_resolver.py:513 judge-model boot-log
# resolution); both calls re-enter the WARNING emit. Ops grep-count
# WARNINGs as health signals — a permanent 2x count breaks exactly-N
# checks. Mirrors the sibling precedent ``_allowed_models_deprecation_warned``
# at daemon/config.py:2309 (same module-level flag checked-and-set idiom).
_spawn_intelligence_tier_boot_warned = False


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

    # Review-council follow-up MAJOR 2 — hoist the
    # ``ENSEMBLE_VSCODE_WEBVIEW_CSP_FIX`` env read to the top level
    # so it applies whether or not the yaml has a ``vscode:``
    # section. Pydantic natively binds ``VSCODE_WEBVIEW_CSP_FIX`` via
    # ``VSCodeConfig.env_prefix="VSCODE_"`` — it does NOT bind the
    # ``ENSEMBLE_*`` form — so a section-less custom config would
    # silently ignore the documented kill-switch and break the
    # incident-revert path. The hoisted value is consumed inside
    # the ``if "vscode" in processed_config:`` guard below; we ALSO
    # feed it through to the field default via
    # ``VSCodeConfig.webview_csp_fix`` so section-less configs
    # still honour the env override at pydantic-init time.
    _resolved_vscode_webview_csp_fix_env_value: str | None = (
        os.environ.get(ENSEMBLE_VSCODE_WEBVIEW_CSP_FIX)
    )

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
    # Feature #1 (spawn-time intelligence override) — single ``os.environ``
    # read at boot (A6 hard rule: per-spawn reads FORBIDDEN to avoid
    # split-brain with the ``allowed_models`` boot snapshot above). The
    # resolved value is installed as ``llm.spawn_intelligence_tier_high_model``
    # for the tool layer (``manager.config.llm.spawn_intelligence_tier_high_model``).
    spawn_intelligence_tier_high_model = _resolve_intelligence_tier_high_model(
        os.environ.get("SPAWN_INTELLIGENCE_TIER_HIGH_MODEL"),
    )
    llm_config["spawn_intelligence_tier_high_model"] = spawn_intelligence_tier_high_model
    # R-A6 boot WARNING (owner-ratified 2026-09-14; W4 verbatim pin):
    # if the resolved tier-default is NOT in the boot-snapshot
    # ``allowed_models``, emit ONE WARNING so operators can re-point
    # the env var. WARNING, NOT boot-fail — the mismatch is semantic
    # (well-formed string, wrong list), not malformed. Per-spawn loud
    # ``ValueError`` remains the parent-facing contract (D2).
    # NOTE: ``llm_config["allowed_models"]`` is the pre-pydantic raw value
    # (CSV string from YAML interpolation or list from env); use the
    # shared parser to mirror what pydantic's field validator will do.
    _allowed_raw = llm_config.get("allowed_models")
    _parsed_allowed_for_warn = _parse_csv_or_json_list(_allowed_raw)
    # Emit-once guard (D-1): the boot path runs ``load_config`` twice
    # (daemon/api.py:245 + daemon/services/attestation_resolver.py:513);
    # the WARNING must still fire exactly once per process. Mirrors the
    # sibling precedent at :2309 (``_allowed_models_deprecation_warned``).
    global _spawn_intelligence_tier_boot_warned
    if not _spawn_intelligence_tier_boot_warned and (
        _parsed_allowed_for_warn
        and spawn_intelligence_tier_high_model not in _parsed_allowed_for_warn
    ):
        _spawn_intelligence_tier_boot_warned = True
        logger.warning(
            "[Config] spawn_intelligence_tier_high_model resolves to '%s', "
            "which is NOT in allowed_models %s; model_tier='high' spawns "
            "will raise until the env is re-pointed.",
            spawn_intelligence_tier_high_model,
            _parsed_allowed_for_warn,
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

    # compaction.model precedence (env COMPACTION_MODEL > yaml
    # compaction.model > unset) is resolved EXPLICITLY here, not left to
    # pydantic layering: a plain passthrough of the YAML ``compaction``
    # dict would pass ``model`` as an init kwarg, and pydantic-settings
    # gives init kwargs priority over env vars — silently inverting the
    # documented order. The section is now ALWAYS present in
    # ``config_dict`` so an env-only deployment (no ``compaction:`` key
    # in yaml) still resolves ``COMPACTION_MODEL``. See
    # ``_resolve_compaction_model`` for the contract.
    compaction_config: Dict[str, Any] = {}
    if "compaction" in processed_config:
        compaction_config = processed_config["compaction"].copy()
    compaction_config["model"] = _resolve_compaction_model(
        compaction_config.get("model", ""),
        env_value=os.environ.get("COMPACTION_MODEL"),
    )

    # agent-snapshot v1 — PR2 — SNAPSHOT_MODEL precedence chain.
    # Env-only override (design-exploration.md §2.3 item 4 / feasibility-notes
    # §A.3): the snapshot service's summarizer calls consult this resolver
    # BEFORE the compaction-model chain, so an operator who pinned the
    # cheap compaction tier gets cheap snapshot calls by default. Bare
    # ``""`` does NOT fall through to the session model — the snapshot
    # chain resolver continues to the compaction chain on empty,
    # preserving the documented chain ``SNAPSHOT_MODEL > COMPACTION_MODEL
    # > session model`` even when both env and yaml overrides are unset
    # (because ``_resolve_compaction_model`` returns ``""`` → snapshot
    # chain continues to "session model" semantics).
    #
    # Install pattern mirrors :func:`_install_vscode_webview_csp_fix`:
    # the boot path reads the env ONCE, hands the resolved string to
    # the module cache, and runtime callers read the cached value. No
    # per-snapshot re-reads of ``os.environ`` (cost model: cheap, but
    # never gratuitous).
    _install_snapshot_model_for_boot(os.environ.get("SNAPSHOT_MODEL"))

    # Cycle 2 (proactive-compaction-fix review W-1 + W-2) — explicit
    # resolution for ``proactive_enabled`` mirrors
    # ``_resolve_compaction_model`` above. pydantic-settings treats an
    # init kwarg as beating the env var, so a YAML
    # ``proactive_enabled: true`` would silently defeat an operator
    # ``ENSEMBLE_PROACTIVE_COMPACTION=0`` kill-switch and weaken the
    # incident-revert path. Resolving in load_config passes the
    # effective bool as an init kwarg; pydantic-settings never
    # re-reads the env. The resolver also normalizes a bare ``KEY=``
    # in .env (empty string) to the documented default — the
    # previously-unhandled crash on ``ENSEMBLE_PROACTIVE_COMPACTION=``
    # (W-1) is fixed here. See ``_resolve_proactive_enabled`` for the
    # precedence + empty-string normalization contract.
    compaction_config["proactive_enabled"] = _resolve_proactive_enabled(
        compaction_config.get("proactive_enabled"),
        ens_value=os.environ.get("ENSEMBLE_PROACTIVE_COMPACTION"),
        cpe_value=os.environ.get("COMPACTION_PROACTIVE_ENABLED"),
    )
    # kv-ambient C2 (kv-ambient-awareness-fix) — explicit resolution
    # for ``context_messages.kv_ambient_system_default_enabled``
    # mirrors ``_resolve_proactive_enabled`` directly above
    # (init-kwarg-beats-env inversion + empty-string normalization).
    # The section is ALWAYS present in ``config_dict`` so an env-only
    # deployment (no ``context_messages:`` key in yaml) still
    # resolves the kill-switch. See ``_resolve_kv_ambient_from_sources``
    # for the precedence + empty-string contract.
    context_messages_config: Dict[str, Any] = {}
    if "context_messages" in processed_config:
        context_messages_config = processed_config["context_messages"].copy()
    context_messages_config["kv_ambient_system_default_enabled"] = (
        _resolve_kv_ambient_from_sources(
            context_messages_config.get("kv_ambient_system_default_enabled"),
            ens_value=os.environ.get(
                ENSEMBLE_KV_AMBIENT_SYSTEM_DEFAULT_ENABLED
            ),
        )
    )
    config_dict["context_messages"] = context_messages_config

    # critical-notes-retrieval Phase 1 (D4): the section is ALWAYS
    # present in ``config_dict`` so an env-free deployment (no
    # ``critical_notes:`` key in yaml) gets the documented defaults.
    # ``llm_select`` is resolved EXPLICITLY (mirroring the resolvers
    # above) so a yaml ``null`` normalizes to the reserved default
    # instead of crashing pydantic bool validation. There is NO env
    # read here BY DESIGN — D4: no ``ENSEMBLE_*`` env var exists for
    # any knob in this section (see CriticalNotesConfig docstring).
    critical_notes_config: Dict[str, Any] = {}
    raw_critical_notes = processed_config.get("critical_notes")
    if raw_critical_notes is None:
        # Section absent — fall through to documented defaults below
        # (test_section_absent_yields_documented_defaults pins this).
        pass
    elif isinstance(raw_critical_notes, dict):
        # Drop null values so partial yaml sections (``key:`` with no
        # value) fall through to documented defaults rather than
        # crashing nested-model validation (skill_evolution/blueprint
        # None-strip precedent). A section that is present but entirely
        # null (``critical_notes:`` with no keys) is treated as absent.
        critical_notes_config = {
            k: v for k, v in raw_critical_notes.items() if v is not None
        }
    else:
        # Loud guard (2026-09-15): a non-dict ``critical_notes:`` section
        # (e.g. ``critical_notes: 5`` or a bare string) used to silently
        # fall through to defaults — masking an operator typo. Match the
        # crash-loud idiom of sibling config sections: name the offending
        # value so the operator sees the actual misconfiguration. Absent
        # / None still yields documented defaults above.
        raise ValueError(
            f"config.critical_notes must be a mapping (got "
            f"{type(raw_critical_notes).__name__}: {raw_critical_notes!r}). "
            f"Fix the yaml — e.g. ``critical_notes:\\n  core_cap: 8``."
        )
    critical_notes_config["llm_select"] = _resolve_critical_notes_llm_select(
        critical_notes_config.get("llm_select")
    )
    config_dict["critical_notes"] = critical_notes_config
    # Boot-time validation for the injected-notes absorb kill-switch
    # (``ENSEMBLE_INJECTED_NOTES_ABSORB``). The resolver is read-at-call by
    # ``daemon/compaction.py::_injected_note_absorbed_ids``; invoking it
    # here on every ``load_config`` makes any unrecognized value (e.g.
    # ``purple``) raise during daemon startup with the flag-naming
    # ValueError, so the mid-flight CLE recovery turn can never inherit a
    # garbage env. Mirrors the ``_resolve_proactive_enabled`` /
    # ``_resolve_compaction_model`` precedent above — env-only, deliberately
    # NOT a ``CompactionConfig`` field (re-adding it as a pydantic bool
    # would reintroduce the init-kwarg-beats-env inversion trap this call
    # site was carved out to avoid; see ``resolve_injected_notes_absorb``
    # docstring). The resolver's return is discarded — the side effect
    # (raising on bad input) is the contract; read-at-call in
    # ``daemon/compaction.py`` is the live source.
    resolve_injected_notes_absorb()
    config_dict["compaction"] = compaction_config
    if "slash_commands" in processed_config:
        # Phase 1 / WS-7: operators may set SLASH_COMMANDS_* via YAML; let
        # the nested section fall through to ``SlashCommandConfig`` so the
        # ``SLASH_COMMANDS_*`` env-var prefix still wins (default-factory
        # would otherwise shadow YAML → env precedence; mirroring the
        # ``blueprint`` / ``skill_evolution`` None-strip pattern keeps the
        # order intentional and grep-able).
        sc_raw = processed_config["slash_commands"]
        config_dict["slash_commands"] = {
            k: v for k, v in sc_raw.items() if v is not None
        }
    if "services" in processed_config:
        # service-tool Phase 1 (1.C.10): the three ENSEMBLE_SERVICE_TOOL_*
        # env reads sit at TOP LEVEL — they run whether or not the yaml
        # carries a ``services:`` section (section-absent review-MAJOR-2
        # class: an env-only deployment must still see the operator
        # kill-switch). The resolved values are injected into
        # ``services_config`` (below) as init kwargs so pydantic-settings
        # never re-reads the env (init-kwarg-beats-env inversion trap).
        services_config: Dict[str, Any] = dict(processed_config["services"]) \
            if isinstance(processed_config["services"], dict) else {}
    else:
        services_config = {}
    # Extract the yaml-side values (nested ``service_tool:`` block for
    # enabled/max_concurrent; the flat A8-named interval field).
    _st_yaml_block = services_config.get("service_tool")
    _st_yaml_block = _st_yaml_block if isinstance(_st_yaml_block, dict) else {}
    services_config["service_tool"] = {
        "enabled": _resolve_service_tool_enabled(
            os.environ.get(ENSEMBLE_SERVICE_TOOL_ENABLED),
            _st_yaml_block.get("enabled"),
        ),
        "max_concurrent": _resolve_service_tool_max_concurrent(
            os.environ.get(ENSEMBLE_SERVICE_TOOL_MAX_CONCURRENT),
            _st_yaml_block.get("max_concurrent"),
        ),
    }
    services_config["service_tool_reconcile_interval_seconds"] = (
        _resolve_service_tool_reconcile_interval(
            os.environ.get(ENSEMBLE_SERVICE_TOOL_RECONCILE_INTERVAL),
            services_config.get("service_tool_reconcile_interval_seconds"),
        )
    )
    config_dict["services"] = services_config
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
        # fix-vscode-image-preview Step 1 — explicit resolution for
        # ``webview_csp_fix`` mirrors ``_resolve_proactive_enabled`` /
        # ``_resolve_kv_ambient_from_sources`` (init-kwarg-beats-env
        # inversion + empty-string normalization). A YAML
        # ``vscode.webview_csp_fix: true`` would silently defeat an
        # operator ``ENSEMBLE_VSCODE_WEBVIEW_CSP_FIX=0`` kill-switch
        # and weaken the incident-revert path. Resolving in load_config
        # passes the effective bool as an init kwarg; pydantic-settings
        # never re-reads the env. The resolver also normalizes a bare
        # ``KEY=`` in .env (empty string) to the documented default.
        # See ``_resolve_vscode_webview_csp_fix_from_sources`` for the
        # precedence + empty-string contract.
        #
        # Review-council follow-up MAJOR 2 — the env read sits at the
        # TOP LEVEL (outside this guard) so section-less configs
        # (``vscode:`` absent from yaml) still see the
        # ``ENSEMBLE_VSCODE_WEBVIEW_CSP_FIX`` env var. Pydantic
        # natively binds only ``VSCODE_WEBVIEW_CSP_FIX`` via
        # ``env_prefix="VSCODE_"`` (does NOT bind the
        # ``ENSEMBLE_*`` form), so without this hoist, a
        # custom-config that omits the ``vscode`` section would
        # silently ignore the documented incident-revert kill-switch.
        vs_raw = processed_config["vscode"].copy()
        vs_raw["webview_csp_fix"] = _resolve_vscode_webview_csp_fix_from_sources(
            vs_raw.get("webview_csp_fix"),
            ens_value=_resolved_vscode_webview_csp_fix_env_value,
        )
        # fix-vscode-image-preview Step 2 — same inversion, string flavor:
        # the yaml ``binary_path`` passthrough (including the common
        # ``binary_path: null``) would land as an init kwarg and beat an
        # operator ``VSCODE_BINARY_PATH``. Resolved EXPLICITLY here so
        # env > yaml > None (None → manager ``shutil.which`` PATH
        # fallback, unchanged). Literal env name must stay in sync with
        # ``VSCodeConfig`` (``env_prefix="VSCODE_"`` + field
        # ``binary_path``) — pinned in
        # ``tests/unit/test_vscode_binary_path_config.py``.
        vs_raw["binary_path"] = _resolve_vscode_binary_path(
            vs_raw.get("binary_path"),
            env_value=os.environ.get("VSCODE_BINARY_PATH"),
        )
        config_dict["vscode"] = vs_raw
    elif _resolved_vscode_webview_csp_fix_env_value is not None:
        # Review-council follow-up MAJOR 2 — section-less configs
        # (``vscode:`` absent from yaml) MUST still see the
        # ``ENSEMBLE_VSCODE_WEBVIEW_CSP_FIX`` env override. Pydantic
        # binds only ``VSCODE_WEBVIEW_CSP_FIX`` via
        # ``env_prefix="VSCODE_"``, so we have to seed
        # ``config_dict["vscode"]`` ourselves with at least the
        # resolved bool. Without this branch the kill-switch env
        # silently no-ops on custom configs that omit the section
        # — the documented incident-revert path breaks.
        #
        # No import of ``VSCodeConfig`` here — the field defaults
        # (``allow_remote=False``, ``binary_path=None``,
        # ``user_data_dir=None``, ``extensions=[]``) are populated
        # by ``VSCodeConfig``'s own default-factory on the
        # pydantic-init pass below; we only need to inject the
        # operator-overridable ``webview_csp_fix``.
        config_dict["vscode"] = {
            "webview_csp_fix": _resolve_vscode_webview_csp_fix_from_sources(
                None,
                ens_value=_resolved_vscode_webview_csp_fix_env_value,
            ),
        }

    # Create and validate config
    config = Config(**config_dict)

    # service-tool Phase 1 (1.C.10) — install the RESOLVED kill-switch
    # into the module cache and emit the boot INFO line HERE, at
    # config-resolution time (S13 reviewer gate: the line MUST stay on
    # the boot path so a quiet-daemon grep never false-fails; EXACT
    # format pinned by plan task 1.C.10). Operators verify the live
    # state via: grep '\[ServiceTool\]' data/logs/ensemble.log
    _install_service_tool_enabled(config.services.service_tool.enabled)
    logger.info(
        "[ServiceTool] service_tool_enabled=%s (env ENSEMBLE_SERVICE_TOOL_ENABLED), "
        "max_concurrent=%s, reconcile_interval=%ss",
        config.services.service_tool.enabled,
        config.services.service_tool.max_concurrent,
        config.services.service_tool_reconcile_interval_seconds,
    )

    # kv-ambient C2 (S13 reviewer gate) — install the RESOLVED flag
    # into the module cache and emit the boot INFO line HERE, at
    # config-resolution time. This line MUST stay on the boot path:
    # moving it into the per-call accessor
    # (``_resolve_kv_ambient_system_default_enabled``) would make a
    # quiet-daemon boot-log grep false-fail (no traffic since restart
    # → line never printed → operator misreads the flag as OFF).
    # Operators verify the live state via: grep
    # 'kv_ambient_system_default_enabled' data/logs/ensemble.log
    _install_kv_ambient_system_default_enabled(
        config.context_messages.kv_ambient_system_default_enabled
    )
    logger.info(
        "[ContextMessages] kv_ambient_system_default_enabled=%s "
        "(env %s)",
        config.context_messages.kv_ambient_system_default_enabled,
        ENSEMBLE_KV_AMBIENT_SYSTEM_DEFAULT_ENABLED,
    )

    # VSCode webview-CSP-rewrite kill-switch (fix-vscode-image-preview
    # Step 1) — install the RESOLVED flag into the module cache and
    # emit the boot INFO line HERE, at config-resolution time. Same
    # S13 reviewer gate as the KV-ambient flag: this line MUST stay on
    # the boot path; moving it into the per-call accessor would make a
    # quiet-daemon boot-log grep false-fail (no traffic since restart
    # → line never printed → operator misreads the flag as OFF).
    # Operators verify the live state via: grep
    # 'webview_csp_fix' data/logs/ensemble.log
    _install_vscode_webview_csp_fix(
        config.vscode.webview_csp_fix
    )
    logger.info(
        "[VSCode] webview_csp_fix=%s (env %s)",
        config.vscode.webview_csp_fix,
        ENSEMBLE_VSCODE_WEBVIEW_CSP_FIX,
    )

    # critical-notes-retrieval Phase 1 — install the Phase-1 consumer
    # knobs into the tool + render module caches and emit the boot INFO
    # state line HERE, at config-resolution time (same S13
    # reviewer-gate rationale as the KV-ambient / vscode-CSP installs:
    # the line MUST stay on the boot path so a quiet-daemon boot-log
    # grep never false-fails). Lazy imports — config.py must not import
    # tool/graph-adjacent modules at module load. D4: this line is
    # state VISIBILITY, not a switch — the feature is always-on and the
    # ENSEMBLE_* env family is NOT extended (rollback = redeploy the
    # previous build).
    from .tools.critical_notes import install_critical_notes_config
    from .services.context_messages import install_critical_notes_render_config
    from .services.critical_notes_selection_orchestrator import (
        install_critical_notes_selection_config,
    )

    install_critical_notes_config(
        core_cap=config.critical_notes.core_cap,
        reference_max=config.critical_notes.reference_max,
        stale_days=config.critical_notes.stale_days,
    )
    install_critical_notes_render_config(
        reference_max=config.critical_notes.reference_max,
    )
    install_critical_notes_selection_config(
        tail_cap=config.critical_notes.tail_cap,
        section_char_cap=config.critical_notes.section_char_cap,
        fusion_bm25_weight=config.critical_notes.fusion_bm25_weight,
        fusion_vector_weight=config.critical_notes.fusion_vector_weight,
        fusion_threshold=config.critical_notes.fusion_threshold,
        floor_count=config.critical_notes.floor_count,
        query_max_chars=config.critical_notes.query_max_chars,
        mint_cap_per_read=config.critical_notes.mint_cap_per_read,
    )
    _cn = config.critical_notes
    # Phase-1 ACTIVE knobs on the primary line (ops-grep target, pinned
    # by tests/test_critical_notes_migrations.py and the runtime
    # anchor grep). Phase-2 ADDS new knobs via the dedicated
    # follow-up line so the primary line stays short and meaningful
    # for the always-on path.
    logger.info(
        "[CriticalNotes] core_cap=%s reference_max=%s stale_days=%s "
        "(always-on per D4 — no ENSEMBLE_* env flag)",
        _cn.core_cap,
        _cn.reference_max,
        _cn.stale_days,
    )
    logger.info(
        "[CriticalNotes:reserved] tail_cap=%s section_char_cap=%s "
        "floor_count=%s query_max_chars=%s mint_cap_per_read=%s "
        "fusion_bm25_weight=%s fusion_vector_weight=%s fusion_threshold=%s "
        "llm_select=%s (all CONSUMED Phase-2; defaults only — "
        "no ENSEMBLE_* env flag, D4)",
        _cn.tail_cap,
        _cn.section_char_cap,
        _cn.floor_count,
        _cn.query_max_chars,
        _cn.mint_cap_per_read,
        _cn.fusion_bm25_weight,
        _cn.fusion_vector_weight,
        _cn.fusion_threshold,
        _cn.llm_select,
    )
    # Phase-2 boot-state probe (architect §4.2 [#8]): moved OUT of
    # ``load_config`` (B2 fix — the original inline probe imported a
    # nonexistent ``get_db_engine`` from the repositories factory, so
    # it raised ImportError on EVERY boot and permanently logged the
    # deferred branch). The probe needs the LIVE engine, which does
    # not exist at config-load time; it is now injected from the api
    # lifespan via :func:`probe_critical_notes_boot_state` once
    # ``manager.initialize()`` has built ``manager.engine``.

    # Empty-response-guard Phase 1 (item 5) — install the RESOLVED
    # guard knobs into the response_validation module cache and emit
    # the boot INFO lines HERE, at config-resolution time (same S13
    # reviewer-gate rationale as the KV-ambient / vscode-CSP installs:
    # the lines MUST stay on the boot path so a quiet-daemon grep never
    # false-fails). Kill-switch contract: OFF restores the pre-guard
    # pass-through byte-identically; restart-required either way (the
    # install runs once at boot). Lazy import — config.py must not
    # import langchain-adjacent modules at module load.
    from .response_validation import install_empty_guard_config

    _empty_guard_enabled = _resolve_empty_response_guard_enabled(
        os.environ.get("ENSEMBLE_EMPTY_RESPONSE_GUARD")
    )
    _empty_guard_compaction_skip = _resolve_empty_guard_compaction_skip(
        os.environ.get("ENSEMBLE_EMPTY_GUARD_COMPACTION_SKIP")
    )
    install_empty_guard_config(
        enabled=_empty_guard_enabled,
        compaction_skip=_empty_guard_compaction_skip,
    )
    logger.info(
        "[ResponseValidation] empty_response_guard=%s "
        "(env ENSEMBLE_EMPTY_RESPONSE_GUARD), "
        "empty_guard_compaction_skip=%s (env ENSEMBLE_EMPTY_GUARD_COMPACTION_SKIP)",
        _empty_guard_enabled,
        _empty_guard_compaction_skip,
    )

    # Hallucination-recovery ladder phase 1 (F-1/F-2 boot probe) — resolve
    # + install the ladder kill-switches and emit the boot INFO line HERE,
    # at config-resolution time (same S13 reviewer-gate rationale as the
    # empty-guard / KV-ambient / vscode-CSP installs: the line MUST stay
    # on the boot path so a quiet-daemon grep never false-fails).
    # Kill-switch contract (ADR-0008): both default ON restart-pending;
    # OFF = byte-identical routing (shipped transient repair + WARN+continue
    # exhaustion preserved), telemetry stays (W1 KEEP).
    _symptom_ladder_enabled = _resolve_symptom_repair_ladder(
        os.environ.get("ENSEMBLE_SYMPTOM_REPAIR_LADDER")
    )
    _repair_loop_durable_enabled = _resolve_repair_loop_durable(
        os.environ.get("ENSEMBLE_REPAIR_LOOP_DURABLE")
    )
    _install_symptom_repair_ladder_config(
        ladder_enabled=_symptom_ladder_enabled,
        loop_durable_enabled=_repair_loop_durable_enabled,
    )
    logger.info(
        "[SymptomRepair] symptom_repair_ladder=%s "
        "(env ENSEMBLE_SYMPTOM_REPAIR_LADDER), "
        "repair_loop_durable=%s (env ENSEMBLE_REPAIR_LOOP_DURABLE)",
        _symptom_ladder_enabled,
        _repair_loop_durable_enabled,
    )

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


def probe_critical_notes_boot_state(*, engine: Any) -> None:
    """Emit the Phase-2 ``[CriticalNotes:state]`` boot-state probe line.

    Reports the ``projects_with_pins=N/M total_pinned=K`` segment the
    architecture recommendation §4.2 [#8] defines: operators grep this
    line to confirm the tiered-activation gate status at startup
    (gating active when ``projects_with_pins >= 1``; projects without
    pins fall back to the render-all shape).

    B2 wiring choice — INJECTION, not construction: the probe lives at
    the api lifespan (``daemon/api.py``), called right after
    ``manager.initialize()`` with the manager's LIVE shared engine.
    The original in-``load_config`` site could not receive an engine
    (config load runs before any engine exists) and its fallback
    imported a nonexistent ``get_db_engine``, so the probe deferred on
    every boot. Injecting the live reference avoids constructing a
    second long-lived engine purely for a probe.

    Best-effort / never raises: any failure logs the deferred ``?/?``
    variant under the same canonical prefix. State VISIBILITY, not a
    gate — the feature is always-on regardless of probe outcome.

    Args:
        engine: The live shared SQLAlchemy engine (``manager.engine``).
    """
    try:
        from .repositories.project.repository import (
            SQLModelProjectRepository,
        )

        proj_repo = SQLModelProjectRepository(engine)
        all_projects = proj_repo.list_projects()
        projects_with_pins = 0
        total_pinned = 0
        for proj in all_projects:
            pid = getattr(proj, "project_id", None)
            if not pid:
                continue
            count = int(
                proj_repo.count_pinned_critical_notes(pid)
            )
            total_pinned += count
            if count > 0:
                projects_with_pins += 1
        logger.info(
            "[CriticalNotes:state] tiered=true projects_with_pins=%d/%d "
            "total_pinned=%d (gating active when projects_with_pins>=1 per §4.2 #8; "
            "projects without pins fall back to render-all)",
            projects_with_pins,
            len(all_projects),
            total_pinned,
        )
    except Exception as e:
        # Best-effort probe. Boot-state visibility is a nice-to-have,
        # not a gate; the feature is always-on regardless of probe
        # outcome. A ``projects_with_pins=N/M total_pinned=K`` follow-up
        # line will appear in the FIRST first-turn log instead.
        logger.info(
            "[CriticalNotes:state] tiered=true projects_with_pins=?/? "
            "total_pinned=? (probe deferred — %s: %s)",
            type(e).__name__,
            e,
        )


# Convenience function for getting the config
def get_config(config_path: str | None = None) -> Config:
    """Get the configuration, loading it if not already loaded."""
    return load_config(config_path)
