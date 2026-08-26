"""LLM error classification utilities for retry handling and context overflow detection."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any

import httpx
import openai
from langchain_core.runnables import RunnableLambda

from .response_validation import LLMResponseValidationError, validate_llm_response

logger = logging.getLogger(__name__)

# Status codes that indicate transient/retryable errors.
# Includes Cloudflare-specific 5xx codes (520-524): 524 ("A Timeout Occurred")
# is returned when an origin behind Cloudflare doesn't respond within its
# ~100s window — functionally equivalent to a 504 gateway timeout but
# without retrying it the system fails on the first attempt.
RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504, 520, 521, 522, 523, 524}


def is_retryable_status_code(status_code: int) -> bool:
    """Return whether an API status code is eligible for HA retry handling."""
    return status_code in RETRYABLE_STATUS_CODES


# Max length for error messages in logs (prevents HTML flooding)
MAX_ERROR_LEN = 300


def _truncate_error(error: Exception | str, max_len: int = MAX_ERROR_LEN) -> str:
    """Truncate error message, stripping HTML if present."""
    error_str = str(error)
    # Strip HTML tags and reduce whitespace
    if "<" in error_str and ">" in error_str:
        error_str = error_str.replace("<", " <").replace(">", "> ")
        error_str = re.sub(r"<[^>]+>", "", error_str)
        error_str = " ".join(error_str.split())
    if len(error_str) > max_len:
        return error_str[:max_len] + "..."
    return error_str


# ---------------------------------------------------------------------------
# Non-status transient-channel pattern matching
# (docs/plans/transient-channel-retry-widening.md work units 2/3/7).
#
# Four production fatality channels arrive without a retryable HTTP
# status code: bare ``openai.APIError`` bodies (relayed rate-limit /
# timeout text), 200-body ``ValueError`` shapes (proxy error dicts,
# zero-chunk SSE streams), and mid-stream ``httpx.RemoteProtocolError``.
# The classifier pattern-matches message text and wraps hits in
# TransientLLMError. Patterns are case-insensitive substrings; defaults
# come from the 2026-08-19→26 fatality corpus
# (docs/bugs/transient-llm-failures-non-retryable-instance-death.md).
#
# Config-driven: ``daemon.config.load_config`` pushes the ``queue:``
# pattern lists here via ``configure_transient_channel_patterns`` — edit
# config.yaml to widen/narrow/disable without a code change. An EMPTY
# allowlist disables the branch entirely (pure pass-through — the
# additive-off switch).
#
# SINGLE SOURCE OF DEFAULTS: ``DEFAULT_TRANSIENT_CHANNEL_PATTERNS`` is
# the canonical corpus-derived default bundle. ``daemon.config
# .QueueConfig`` derives its field defaults from it (never a second
# copy), and config.yaml entries are pure operator overrides — note
# that REMOVING a key from config.yaml reverts to these built-in
# defaults; disabling requires an explicit empty/trimmed list.
#
# Atomicity: the active state is ONE frozen bundle swapped in a single
# global assignment, so a runtime ``load_config`` reload (e.g. the
# keyword-extraction service) can never leave a reader with a torn
# old-blocklist/new-allowlist view mid-classification.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TransientChannelPatterns:
    """Immutable non-status transient-channel pattern set.

    Swapped atomically (single global assignment) by
    ``configure_transient_channel_patterns``.
    """

    # C1 — bare openai.APIError messages treated as transient.
    apierror_allowlist: tuple[str, ...] = (
        "all models rate limited",       # 21 events — proxy all-upstreams 429
        "context deadline exceeded",     # 2 events — relayed upstream timeout
    )
    # Timeout-body subset of the allowlist: these route to the 3-attempt
    # timeout budget (kind='timeout_body') instead of the 10-attempt
    # transient budget — each attempt can cost the upstream's full
    # timeout, and docs/retry-architecture.md §5 warns about wall-clock
    # amplification. QueueConfig validates subset ⊆ allowlist.
    apierror_timeout_patterns: tuple[str, ...] = (
        "context deadline exceeded",
    )
    # Mandatory-severity blocklist: an allowlist hit + blocklist hit →
    # NON-retryable — enforced on BOTH the bare-APIError and the
    # ValueError channels. Protects quota shapes ("Token Plan usage
    # limit reached", corpus event 2056) that share wording families
    # with allowlist entries, including when quota text is embedded in
    # a 200-body proxy error dict. Auth shapes are unreachable here by
    # design — auth errors arrive as APIStatusError and are caught at
    # the status branch.
    apierror_blocklist: tuple[str, ...] = (
        "token plan",
        "usage limit",
        "invalid params",
    )
    # C2/C4 — ValueError body shapes from LangChain parsing of 200-body
    # proxy errors / zero-chunk SSE streams.
    # ``ultimate_model_retry_exhausted`` is proxy-dependent: disable it
    # by setting an explicit trimmed list in config.yaml once the
    # proxy's ultimate-routing transparency update ships (bug doc RC2).
    valueerror_patterns: tuple[str, ...] = (
        "no generations found",              # 4 events — zero-chunk SSE
        "ultimate_model_retry_exhausted",    # 8 events — proxy 200-body dict
    )
    # C3 — httpx.RemoteProtocolError retryability gate (peer closed
    # mid-body). Membership in the retry set is CONDITIONAL on this
    # flag (like IndexError-on-backup), giving operators a config
    # kill-switch without a redeploy.
    remote_protocol_retryable: bool = True


# Canonical defaults — QueueConfig field defaults derive from this bundle.
DEFAULT_TRANSIENT_CHANNEL_PATTERNS = TransientChannelPatterns()

# Active pattern state — the defaults until load_config overrides.
_transient_patterns = DEFAULT_TRANSIENT_CHANNEL_PATTERNS


def _normalize_patterns(patterns: list[str] | tuple[str, ...]) -> tuple[str, ...]:
    """Strip, lowercase, and drop empties from a pattern list."""
    return tuple(p.strip().lower() for p in patterns if p.strip())


def configure_transient_channel_patterns(
    apierror_allowlist: list[str] | tuple[str, ...] | None = None,
    apierror_timeout_patterns: list[str] | tuple[str, ...] | None = None,
    apierror_blocklist: list[str] | tuple[str, ...] | None = None,
    valueerror_patterns: list[str] | tuple[str, ...] | None = None,
    remote_protocol_retryable: bool | None = None,
) -> None:
    """Override the non-status transient-channel patterns.

    Called by ``daemon.config.load_config`` with the ``queue:`` pattern
    lists (config.yaml). Only the values passed as non-None are
    overridden; an explicitly-empty allowlist/pattern list disables the
    corresponding classifier branch (pure pass-through). The new state
    is built from the current one and installed via a SINGLE global
    assignment — concurrent classification attempts always see one
    internally-consistent bundle (no torn reads during a runtime
    config reload).

    Args:
        apierror_allowlist: Bare-APIError transient message substrings.
        apierror_timeout_patterns: Subset routed to the timeout budget.
        apierror_blocklist: Mandatory-precedence terminal substrings.
        valueerror_patterns: ValueError-body transient substrings.
        remote_protocol_retryable: Whether RemoteProtocolError retries.
    """
    global _transient_patterns
    current = _transient_patterns
    _transient_patterns = TransientChannelPatterns(
        apierror_allowlist=(
            _normalize_patterns(apierror_allowlist)
            if apierror_allowlist is not None
            else current.apierror_allowlist
        ),
        apierror_timeout_patterns=(
            _normalize_patterns(apierror_timeout_patterns)
            if apierror_timeout_patterns is not None
            else current.apierror_timeout_patterns
        ),
        apierror_blocklist=(
            _normalize_patterns(apierror_blocklist)
            if apierror_blocklist is not None
            else current.apierror_blocklist
        ),
        valueerror_patterns=(
            _normalize_patterns(valueerror_patterns)
            if valueerror_patterns is not None
            else current.valueerror_patterns
        ),
        remote_protocol_retryable=(
            remote_protocol_retryable
            if remote_protocol_retryable is not None
            else current.remote_protocol_retryable
        ),
    )


def reset_transient_channel_patterns() -> None:
    """Restore the corpus-derived module defaults (test helper)."""
    global _transient_patterns
    _transient_patterns = DEFAULT_TRANSIENT_CHANNEL_PATTERNS


# ---------------------------------------------------------------------------
# Usage-limit (quota-window) typing
# (docs/plans/usage-limit-deferral-path.md work unit 1).
#
# Quota shapes ("Token Plan usage limit reached", corpus event 2056) are
# TERMINAL at L1 by design — the quota window resets on the provider's
# schedule, so second-scale retries are futile. Until now they were an
# untyped blocklist re-raise; the dedicated deferral path needs them
# TYPED so the worker seam can implement usage-limit policy (anchor,
# 6 h deadline, fixed wake schedule) without pattern-matching again.
#
# The wrapper fires BEFORE the allowlist/blocklist logic on BOTH
# non-status channels (bare APIError bodies and 200-body ValueErrors) —
# quota hits are a strict subset of today's blocklist hits, so ordering
# the check first preserves every other shape byte-identically.
#
# Config-driven like the transient channels: ``queue.usage_limit_patterns``
# (config.yaml) is pushed here via ``configure_usage_limit_patterns`` by
# ``load_config``. An EMPTY list disables the typed wrapper entirely
# (additive-off switch — quota shapes revert to the untyped blocklist
# re-raise).
#
# DISJOINTNESS (hard requirement): these patterns must never match the
# bad-params shapes ("invalid params", corpus 2013) — a genuine bug must
# not enter a 6 h auto-retry episode. Enforced at QueueConfig validation.
# ---------------------------------------------------------------------------

# Canonical defaults — QueueConfig field defaults derive from this tuple.
DEFAULT_USAGE_LIMIT_PATTERNS: tuple[str, ...] = (
    "token plan",
    "usage limit",
)

# Active pattern state — the defaults until load_config overrides.
_usage_limit_patterns = DEFAULT_USAGE_LIMIT_PATTERNS


def configure_usage_limit_patterns(
    patterns: list[str] | tuple[str, ...] | None = None,
) -> None:
    """Override the quota-window (usage-limit) pattern list.

    Called by ``daemon.config.load_config`` with the
    ``queue.usage_limit_patterns`` list (config.yaml). Only a non-None
    value is applied; an explicitly-EMPTY list disables the typed
    wrapper (quota shapes fall back to the untyped terminal blocklist
    re-raise). Installed via a single global assignment so concurrent
    classifications never see a torn view during a runtime reload.

    Args:
        patterns: Message substrings typed as ``UsageLimitError``.
    """
    global _usage_limit_patterns
    if patterns is None:
        return
    _usage_limit_patterns = _normalize_patterns(patterns)


def reset_usage_limit_patterns() -> None:
    """Restore the corpus-derived module defaults (test helper)."""
    global _usage_limit_patterns
    _usage_limit_patterns = DEFAULT_USAGE_LIMIT_PATTERNS


def _matches_usage_limit(msg: str) -> bool:
    """Case-insensitive substring test for quota-window message shapes."""
    return _any_substring(_usage_limit_patterns, msg.lower())


def _any_substring(patterns: tuple[str, ...], lowered_msg: str) -> bool:
    """Case-insensitive substring match of any pattern against a lowered message."""
    return any(p in lowered_msg for p in patterns)


def _matches_transient_apierror(msg: str) -> bool:
    """Bare-APIError transient test: allowlist hit AND NOT blocklist hit.

    The blocklist has mandatory precedence — a message matching both is
    non-retryable (quota / bad-params shapes stay terminal even when
    they share wording with an allowlist entry).
    """
    lowered = msg.lower()
    if _any_substring(_transient_patterns.apierror_blocklist, lowered):
        return False
    return _any_substring(_transient_patterns.apierror_allowlist, lowered)


def _matches_timeout_body(msg: str) -> bool:
    """Whether a bare-APIError message is a relayed upstream timeout body."""
    return _any_substring(
        _transient_patterns.apierror_timeout_patterns, msg.lower()
    )


def _matches_transient_valueerror(msg: str) -> bool:
    """ValueError-body transient test (200-body proxy errors, empty SSE).

    Same mandatory blocklist precedence as the bare-APIError channel: a
    proxy 200-body dict embedding quota wording alongside an allowlisted
    substring stays terminal.
    """
    lowered = msg.lower()
    if _any_substring(_transient_patterns.apierror_blocklist, lowered):
        return False
    return _any_substring(_transient_patterns.valueerror_patterns, lowered)


def classify_transient_apierror_body(e: BaseException) -> TransientLLMError | None:
    """Shared bare-APIError wrap decision for the hot path AND the L2 facade.

    Returns the ``TransientLLMError`` wrapper when the message pattern-
    matches (blocklist precedence enforced), else ``None`` (terminal,
    re-raise unchanged). ``kind`` is computed only on an allowlist hit.
    Single implementation so kind-routing cannot drift between
    ``classify_llm_errors`` and ``_classify_raw_sdk_exceptions``.
    """
    if not _matches_transient_apierror(str(e)):
        return None
    kind = "timeout_body" if _matches_timeout_body(str(e)) else "api_error_body"
    return TransientLLMError(kind, e)


class TransientAPIError(Exception):
    """Wrapper for APIStatusError with retryable status codes.
    
    LangChain's with_retry only matches by exception type, not by
    exception attributes. We wrap transient APIStatusErrors in this
    exception so with_retry can catch them.
    """
    
    def __init__(self, original: openai.APIStatusError):
        self.original = original
        self.status_code = original.status_code
        super().__init__(f"Transient API error: {original.status_code} — {original}")


class TransientLLMError(Exception):
    """Transient failure delivered through a non-status channel (bare
    APIError message, 200-body ValueError, stream shape). Wrapping makes
    it a TRANSIENT_EXCEPTIONS member so L1 tenacity / L2 failover treat
    it like any transient error.

    The ``kind`` field carries the budget category consumed by the
    retry predicate: ``'api_error_body'`` / ``'value_error_body'`` →
    transient; ``'timeout_body'`` → timeout (see RetryByCategory).
    Deliberately NOT a subclass of TransientAPIError — its ctor
    requires an APIStatusError and ``.status_code``.

    See docs/plans/transient-channel-retry-widening.md.
    """

    def __init__(self, kind: str, original: BaseException):
        self.kind = kind
        self.original = original
        super().__init__(f"Transient LLM error ({kind}): {original}")


class UsageLimitError(Exception):
    """Provider quota exhaustion (token plan / usage limit windows).

    Terminal at L1 by design — the window resets on the provider's
    schedule, so second-scale retries are futile. The worker seam
    routes it into the dedicated deferral path (deadline-bounded
    re-dispatch); see docs/plans/usage-limit-deferral-path.md.

    Deliberately NOT a ``TRANSIENT_EXCEPTIONS`` / ``TIMEOUT_EXCEPTIONS``
    member — tenacity never retries it, and the task-processor W3
    carve-out keeps it out of the stage-2 report cascade so the worker
    seam owns the episode decision.

    Wraps the original exception (``.original``) so the terminal report
    can carry the provider's original error text.
    """

    def __init__(self, original: BaseException):
        self.original = original
        super().__init__(f"Usage limit (quota window): {original}")


class ContextLengthExceededError(Exception):
    """Raised when LLM context window is exceeded.
    
    NOT retried by with_retry (not in TRANSIENT_EXCEPTIONS).
    Caught by agent_node for reactive compaction + single retry.
    If compaction fails or retry still exceeds context, propagates
    to manager for immediate failure (no queue retry).
    """
    
    def __init__(self, original_error: openai.BadRequestError, model: str = ""):
        self.original_error = original_error
        self.model = model
        super().__init__(
            f"Context length exceeded for model '{model}'. "
            f"Original error: {original_error}"
        )


class MalformedLLMResponseError(Exception):
    """Raised when the LLM provider returns a response body of an unexpected type.

    Incident (2026-08-15, instance f10b7694): a provider under stress
    returned a bare JSON string body instead of a ChatCompletion object.
    The OpenAI SDK's ``construct_type()`` passthrough returned the ``str``
    as-is, and LangChain's ``BaseChatOpenAI._create_chat_result`` called
    ``.model_dump()`` on it — surfacing as ``AttributeError: 'str' object
    has no attribute 'model_dump'`` from deep inside LangChain. The error
    classifier classified that AttributeError as NON-retryable, so tenacity
    never retried, the instance died, and the parent closed as COMPLETED.

    ``ThinkingChatOpenAI._create_chat_result`` (daemon/graph.py) now
    type-guards the response and raises THIS exception before the
    ``super()._create_chat_result`` call, converting the
    malformed-response path into a retryable signal: a member of
    TRANSIENT_EXCEPTIONS with an explicit classifier handler. Generic
    ``AttributeError`` classification is deliberately left untouched
    (still non-retryable) — only this exception, raised by the guard,
    is retryable.
    """

    def __init__(self, response: Any):
        self.response = response
        super().__init__(
            "expected dict or object with model_dump(), got "
            f"{type(response).__name__}"
        )


# Exceptions that with_retry should catch and retry — server/connection errors
TRANSIENT_EXCEPTIONS: tuple[type[Exception], ...] = (
    # Wrapper exception from classifier for retryable status codes
    TransientAPIError,
    # Wrapper exception from classifier for NON-status transient channels
    # (bare APIError message patterns, 200-body ValueError shapes).
    # Membership is the only lever for the transient budget; the single
    # deviation is kind='timeout_body', which RetryByCategory routes to
    # the timeout budget (see RetryByCategory.__call__).
    TransientLLMError,
    # Raw socket errors (proxy restarts) — not wrapped by OpenAI SDK
    ConnectionResetError,
    BrokenPipeError,
    ConnectionAbortedError,
    # NOTE: httpx.RemoteProtocolError (peer closed mid-body, "incomplete
    # chunked read") is intentionally NOT an unconditional member. Its
    # retryability is CONDITIONAL on the config gate
    # ``TransientChannelPatterns.remote_protocol_retryable`` (default on)
    # — see RetryByCategory.__call__, which treats it as transient only
    # while the gate is on. Same pattern as IndexError-on-backup: a
    # config kill-switch without a redeploy. The broader ProtocolError /
    # TransportError parents stay out regardless — a stray ConnectError
    # is already covered by APIConnectionError, and over-broad parents
    # risk making broken-endpoint loops burn the full budget.
    # OpenAI exceptions that DON'T get wrapped (from lower-level HTTP client)
    openai.APIConnectionError,
    # Response validation failure from Phase 1
    LLMResponseValidationError,
    # Malformed LLM response body (bare str/list/None from a stressed
    # provider) — raised by the ThinkingChatOpenAI._create_chat_result
    # type-guard (daemon/graph.py) so the retryable signal surfaces as a
    # dedicated exception instead of a generic AttributeError from inside
    # LangChain. Generic AttributeError stays NON-retryable; only this
    # exception is in the retry set.
    MalformedLLMResponseError,
    # Proxy returning non-JSON response (e.g., HTML error page)
    openai.APIResponseValidationError,
    # NOTE: IndexError is intentionally NOT in TRANSIENT_EXCEPTIONS —
    # unconditionally. LangChain's .invoke() raises IndexError on
    # choices[0] when the LLM returns choices: [], which is a malformed
    # response. Its retryability is now CONDITIONAL on controller
    # presence: when a FailoverController (backup URL) is supplied to
    # make_llm_retry_strategy, the predicate treats IndexError as
    # transient and fails over to the backup; without a controller the
    # pre-HA behavior (non-retryable, re-raised by the classifier) is
    # preserved. See _run_with_classification's except IndexError
    # handler and RetryByCategory.__call__.
)


# Timeout errors — expensive retries (each costs up to request_timeout)
TIMEOUT_EXCEPTIONS: tuple[type[Exception], ...] = (
    openai.APITimeoutError,
    httpx.TimeoutException,
    TimeoutError,
)

# Primary-phase retry caps for the HA budget-split (see
# ``make_llm_retry_strategy``). Exported as module constants so the
# strategy defaults and ``daemon.graph.build_instance_llms``'s
# ``stop_after_attempt`` ceiling derivation stay in lock-step — graph.py
# adds ``max(PRIMARY_TRANSIENT_MAX, PRIMARY_TIMEOUT_MAX)`` to the slice
# caps when computing the total attempts ceiling. If these defaults
# change, graph.py picks the new values up automatically.
#
# NOTE on def-time binding: ``make_llm_retry_strategy`` declares these
# constants as DEFAULT PARAMETER VALUES, which Python binds once at
# function-definition time. Monkeypatching ``llm_error_classifier.PRIMARY_*``
# at runtime therefore changes graph.py's ceiling derivation (it
# re-imports the names at call time) but NOT the strategy's slice caps —
# the two silently drift apart. To change the slice caps at runtime,
# pass ``primary_transient_max`` / ``primary_timeout_max`` explicitly; to
# change them permanently, edit these definitions (both consumers follow).
PRIMARY_TRANSIENT_MAX = 3  # primary tolerates 2 transient retries before swap
PRIMARY_TIMEOUT_MAX = 2  # primary tolerates 1 timeout retry before swap


def derive_ha_attempt_ceiling(
    transient_max: int,
    timeout_max: int,
    failover_active: bool = False,
) -> int:
    """Return the total attempt ceiling for the primary/backup retry cycle.

    When HA is active, the primary phase reserves the maximum primary slice
    in addition to the full operator budget used on the backup phase. Without
    HA, only the operator budget is needed.
    """
    budget = max(transient_max, timeout_max)
    if failover_active:
        return budget + max(PRIMARY_TRANSIENT_MAX, PRIMARY_TIMEOUT_MAX)
    return budget


class FailoverController:
    """Mutates a ``ChatOpenAI`` instance's underlying openai client to swap
    between primary and backup ``base_url`` values mid-flight.

    The controller holds:
      - A reference to the ``ChatOpenAI``-derived runnable (langchain
        instance) whose ``root_client`` and ``root_async_client`` we mutate.
      - The primary and backup URLs.

    Swap semantics:
      - ``swap_to_backup()`` rewrites the openai client's ``base_url`` so the
        *next* request goes to the backup. The langchain layer (``self.client =
        self.root_client.chat.completions``) reads ``self.root_client`` at
        request time, so the next call sees the new URL with no further work.
      - ``reset_to_primary()`` rewrites it back. It is called ONLY from
        the retry predicate's ``attempt_number == 1`` branch — i.e. after
        the first attempt of a new invoke cycle completes (tenacity
        evaluates the predicate after every attempt, successful or not).
        The *first request* of that cycle went out before the reset, so
        a cycle following a successful failover starts on backup and
        returns to primary from its second attempt onward. See
        "Sticky-on-success" below for why this asymmetry is intentional.
      - Idempotent: ``swap_to_backup()`` when already on backup is a no-op,
        matching the predicate's "swapped" flag. This avoids redundant log
        lines when the swap is re-asserted.

    Sticky-on-success (leader-adjudicated semantic, 2026-08-14 review W1):
      After a cycle that failed over to backup and SUCCEEDED there, the
      client URL intentionally REMAINS on backup, so the NEXT invoke's
      first request hits the backup directly — no probe of the dead
      primary. The reset-to-primary is not tied to invoke boundaries but
      to predicate evaluation: tenacity evaluates the predicate after
      EVERY attempt (successful ones included), and the predicate's
      ``attempt_number == 1`` branch calls ``reset_to_primary()`` — so
      once the next invoke's first attempt completes (success OR failure
      on backup), the client returns to primary for the attempts after
      it. Net effect during a primary outage: invocations alternate —
      one invoke served wholly by backup, the next probes primary once,
      fails over again if it is still down.

      This is deliberate: a strict non-sticky policy (an eager reset
      BEFORE the first request of every invoke) would tax EVERY invoke
      with dead-primary probe latency, while both endpoints serve the
      same backend — lingering on the backup after a successful failover
      is harmless and self-heals as soon as the client is returned to
      primary by the next predicate evaluation. Do not add an
      invoke-start reset; the adjudication is final unless re-reviewed.

    Non-goals:
      - This controller does NOT handle credentials, request signing, or model
        selection. Same proxy / same key is assumed; only the endpoint URL
        changes.
      - It does NOT touch headers, timeouts, or retry configuration — both
        endpoints share the same daemon config.

    Thread safety: ``langchain.ChatOpenAI.invoke`` is synchronous (the daemon
    uses ``asyncio.to_thread`` to offload it). The controller's mutations run
    in the same thread that owns the client, so no lock is needed. The async
    client path (``astream`` / ``ainvoke``) is exercised only inside the
    agent-node's blocking ``invoke`` — no concurrent mutations are expected.
    """

    def __init__(
        self,
        chat_client: Any,
        primary_url: str,
        backup_url: str,
    ) -> None:
        self._chat_client = chat_client
        self._primary_url = primary_url
        self._backup_url = backup_url
        self._on_backup = False

    @property
    def is_configured(self) -> bool:
        """True iff a backup URL was supplied at construction (and the
        controller is therefore actively participating in retry decisions)."""
        return bool(self._backup_url) and self._backup_url != self._primary_url

    def swap_to_backup(self) -> None:
        """Point the underlying openai client at the backup URL.

        No-op when already on backup (idempotent — the retry predicate
        may fire multiple times in a single cycle).
        """
        if self._on_backup:
            return
        self._mutate_client_base_url(self._backup_url)
        self._on_backup = True

    def reset_to_primary(self) -> None:
        """Point the underlying openai client back at the primary URL.

        Called from the retry predicate's ``attempt_number == 1`` branch
        (NOT eagerly before the first request of an invoke — see the
        "Sticky-on-success" section of the class docstring). The first
        request of the cycle already went out on the lingering URL; this
        reset governs the attempts AFTER it.
        """
        if not self._on_backup:
            return
        self._mutate_client_base_url(self._primary_url)
        self._on_backup = False

    def failover_summary(self) -> str:
        """Single-line summary of the failover for greppable WARNING logs.

        Format: ``primary=<url> -> backup=<url>`` — both URLs are included
        so operators can correlate log lines with their env-var settings
        without grepping the config.
        """
        return f"primary={self._primary_url} -> backup={self._backup_url}"

    def _mutate_client_base_url(self, new_url: str) -> None:
        """Rewrite ``base_url`` on both sync and async underlying clients.

        LangChain's ``ChatOpenAI`` constructs an ``openai.OpenAI`` and
        ``openai.AsyncOpenAI`` in :meth:`validate_environment` and stores
        them as ``root_client`` / ``root_async_client``. The request path
        dereferences ``self.root_client.chat.completions`` at call time,
        so mutating ``base_url`` on those instances affects every
        subsequent request. The async path is mutated defensively even
        though the daemon's main flow uses ``invoke`` (offloaded via
        ``asyncio.to_thread``) — keeping them in lock-step avoids
        surprises if a future caller adds async invocation.

        The PUBLIC ``client.base_url`` property setter is used
        deliberately: it normalises the value through ``URL()`` +
        ``_enforce_trailing_slash`` (openai >= 2.x types the private
        ``_base_url`` as ``httpx.URL``). Assigning a raw ``str`` directly
        to ``client._base_url`` corrupts that invariant and the NEXT
        request build crashes in ``openai._base_client._prepare_url``
        with ``AttributeError: 'str' object has no attribute
        'raw_path'`` — the first failover attempt would never reach the
        backup. The setter's normalisation is exactly what makes the
        swap work.

        If BOTH mutation attempts fail (sync AND async), the failover is
        dead while a backup is configured — one WARNING is emitted so the
        operator can see it in normal log levels (the per-attempt detail
        stays at DEBUG with the traceback).
        """
        failed: list[str] = []
        for client_attr in ("root_client", "root_async_client"):
            client = getattr(self._chat_client, client_attr, None)
            if client is None:
                continue
            try:
                client.base_url = new_url
            except Exception:
                failed.append(client_attr)
                logger.debug(
                    f"[LLM-HA] Could not set base_url on {client_attr} "
                    f"({type(client).__name__}); failover may be a no-op.",
                    exc_info=True,
                )
        if len(failed) == 2:
            # W5: both sync and async mutation attempts raised — the swap
            # silently did nothing even though a backup is configured.
            # Emit exactly one WARNING per controller per swap attempt
            # (bounded: swap_to_backup/reset_to_primary early-return when
            # already in the target state, so this cannot loop).
            logger.warning(
                f"[LLM-HA] base_url mutation FAILED on both root_client "
                f"and root_async_client; failover to {new_url} is a NO-OP "
                f"(targeting {new_url}). Check the client object shape."
            )


def make_llm_retry_strategy(
    transient_max: int,
    timeout_max: int,
    failover_controller: "FailoverController | None" = None,
    primary_transient_max: int = PRIMARY_TRANSIENT_MAX,
    primary_timeout_max: int = PRIMARY_TIMEOUT_MAX,
) -> "RetryByCategory":
    """Build the public retry strategy with per-category attempt limits.

    Uses a closure with mutable counters so that transient and timeout
    retries are tracked independently. Timeout exceptions are checked
    FIRST because openai.APITimeoutError inherits from openai.APIConnectionError
    (which is in TRANSIENT_EXCEPTIONS) — checking timeout first ensures it's
    counted in the correct category.

    Args:
        transient_max: Max counter value for transient errors. The
            predicate returns True while ``count < transient_max``, so
            ``transient_max=N`` allows ``N-1`` retries. (This matches
            the pre-HA convention verified by
            ``tests/unit/test_llm_error_classifier.py::TestRetryByCategory::test_transient_errors_limited_to_transient_max``.)
        timeout_max: Max counter value for timeout errors, same
            ``count < timeout_max`` convention.
        failover_controller: Optional ``FailoverController``. When supplied
            AND ``is_configured`` is True, the predicate performs a one-shot
            swap to the backup ``base_url`` after the primary phase exhausts
            its small slice, then resets counters and grants the FULL
            ``transient_max``/``timeout_max`` budget to the backup. When
            ``None`` (or unconfigured) the predicate behaves identically to
            the pre-HA system (zero behavior change).

            Cross-invoke semantics: sticky-on-success (leader-adjudicated).
            A cycle that swaps and succeeds on backup leaves the client on
            backup; the next cycle's first request hits backup directly and
            returns to primary after the NEXT invoke's first attempt
            completes, regardless of whether that attempt succeeds or
            fails on backup (see the ``FailoverController`` docstring for
            the rationale).
        primary_transient_max: Counter threshold on primary above which
            the swap-to-backup fires for transient errors. Same
            ``count < primary_transient_max`` convention as
            ``transient_max``: default ``PRIMARY_TRANSIENT_MAX = 3`` =
            primary tolerates 2 transient retries before swapping
            (matches spec: "primary gets a small slice, ~2 transient
            attempts"). Ignored when ``failover_controller`` is None.
        primary_timeout_max: Counter threshold on primary above which
            the swap-to-backup fires for timeout errors. Default
            ``PRIMARY_TIMEOUT_MAX = 2`` = primary tolerates 1 timeout
            retry before swapping (matches spec: "~1 timeout attempt").
            Ignored when ``failover_controller`` is None.

    Returns:
        A callable that tenacity can use as a retry predicate.
    """
    counts = {"transient": 0, "timeout": 0, "swapped": False}

    class RetryByCategory:
        """Retry predicate that tracks per-category attempt counts."""

        def __call__(self, retry_state: "RetryCallState") -> bool:
            # Reset counters + controller URL after the first attempt of
            # each new invoke cycle. tenacity creates fresh RetryCallState
            # per cycle but reuses the predicate; attempt_number == 1
            # means the first attempt of a new cycle just completed —
            # with a failure (retry decision follows below) or a success
            # (``exception is None`` returns False right after; the reset
            # has already run, which is harmless — idempotent).
            #
            # Sticky-on-success note: the reset happens AFTER that first
            # request went out on whatever URL the previous cycle ended
            # on. See the FailoverController docstring for why this is
            # the adjudicated semantic.
            if retry_state.attempt_number == 1:
                counts["transient"] = 0
                counts["timeout"] = 0
                counts["swapped"] = False
                if failover_controller is not None:
                    failover_controller.reset_to_primary()

            exception = retry_state.outcome.exception()
            if exception is None:
                return False

            # IMPORTANT: Check timeout FIRST since APITimeoutError inherits
            # from APIConnectionError (in TRANSIENT_EXCEPTIONS). Without this
            # ordering, timeouts would be misclassified as transient errors.
            #
            # TransientLLMError(kind='timeout_body') joins the timeout
            # check here: relayed upstream timeouts ("context deadline
            # exceeded") each cost the upstream's full timeout, so
            # budgeting them as transient (10 attempts) would amplify
            # wall-clock exactly the way docs/retry-architecture.md §5
            # warns about. This is the SINGLE documented predicate
            # deviation from "membership is the only lever"
            # (plan work unit 2a) — budget/backoff/ceiling wiring is
            # otherwise untouched.
            if isinstance(exception, TIMEOUT_EXCEPTIONS) or (
                isinstance(exception, TransientLLMError)
                and exception.kind == "timeout_body"
            ):
                counts["timeout"] += 1
                return self._decide_after_count(
                    category="timeout",
                    primary_cap=primary_timeout_max,
                    full_budget=timeout_max,
                )
            elif isinstance(exception, TRANSIENT_EXCEPTIONS) or (
                # HA-failover path: IndexError (empty/malformed choices[]) is
                # treated as transient ONLY when a backup is configured. With
                # no backup the pre-HA behavior is preserved (IndexError is
                # non-retryable — re-raised by the classifier and short-circuits
                # to the upstream error pipeline). See daemon/llm_error_classifier.py
                # comment block on TRANSIENT_EXCEPTIONS for the rationale.
                #
                # ``is_configured`` (not just ``is not None``) guards this:
                # a controller built without a usable backup (None or equal
                # to primary) must not make IndexError retryable — retrying
                # against the same broken endpoint just burns budget.
                failover_controller is not None
                and failover_controller.is_configured
                and isinstance(exception, IndexError)
            ) or (
                # C3 gate: httpx.RemoteProtocolError (peer closed mid-body)
                # is transient ONLY while the config gate is on
                # (``queue.transient_remote_protocol_retryable`` in
                # config.yaml, default True). Gives operators a kill-switch
                # for a broken-endpoint retry loop without a redeploy —
                # see the TRANSIENT_EXCEPTIONS comment block.
                isinstance(exception, httpx.RemoteProtocolError)
                and _transient_patterns.remote_protocol_retryable
            ):
                counts["transient"] += 1
                return self._decide_after_count(
                    category="transient",
                    primary_cap=primary_transient_max,
                    full_budget=transient_max,
                )

            # Non-retryable — don't retry (401, 403, 400, etc.)
            return False

        def _decide_after_count(
            self, category: str, primary_cap: int, full_budget: int
        ) -> bool:
            """Apply the HA budget-split rule for one category.

            Pre-HA (no controller): identical to ``counts[category] < full_budget``.

            With controller: primary gets up to ``primary_cap`` attempts, then a
            one-shot swap to the backup URL resets the counter and grants the
            full ``full_budget`` on the backup side. After swap, the predicate
            uses the same ``counts[category] < full_budget`` rule on backup.
            """
            current = counts[category]
            if failover_controller is None:
                return current < full_budget

            if counts["swapped"]:
                # Already on backup — full budget applies.
                return current < full_budget

            # W2 clamp: the operator-configured budget (``full_budget``) is
            # a CEILING — the primary slice can never exceed it. When the
            # custom budget is smaller than the default primary cap (e.g.
            # ``transient_max=2 < PRIMARY_TRANSIENT_MAX=3``), the swap
            # triggers at the budget boundary instead of never firing
            # (which would silently strand the configured backup unused).
            effective_cap = min(primary_cap, full_budget)

            if current >= effective_cap:
                # Primary exhausted for this category — swap and reset.
                failover_controller.swap_to_backup()
                counts["swapped"] = True
                # W4 cross-category reset: BOTH counters are zeroed, not
                # just the triggering category's. Failures interleave in
                # practice (transient, transient, timeout, ...); resetting
                # only one category would carry the other's primary-phase
                # count into the backup phase and silently shortchange the
                # backup's full budget.
                counts["transient"] = 0
                counts["timeout"] = 0
                logger.warning(
                    f"[LLM-HA] {failover_controller.failover_summary()}"
                )
                return True  # immediate retry on backup

            # Still on primary, within slice — continue. Note the predicate
            # is bounded by the operator ceiling even on primary
            # (``current < full_budget``) — the swap path above never
            # grants backup budget beyond what the operator configured.
            return current < full_budget

    return RetryByCategory()


def classify_llm_errors(llm_with_tools: Any) -> RunnableLambda:
    """Wrap LLM to classify exceptions before they reach with_retry.
    
    Runs validate_llm_response() INSIDE the try block so validation
    failures are caught by with_retry and trigger a retry.
    
    CRITICAL: except openai.BadRequestError MUST come BEFORE 
    except openai.APIStatusError because BadRequestError is a subclass
    of APIStatusError.
    
    Args:
        llm_with_tools: The LLM runnable to wrap.
        
    Returns:
        RunnableLambda that classifies errors.
    """
    
    def _run_with_classification(messages: list, **kwargs: Any) -> Any:
        try:
            result = llm_with_tools.invoke(messages, **kwargs)
            # Phase 1's validation runs INSIDE the retry scope
            validate_llm_response(result)
            return result
        except openai.BadRequestError as e:
            # MUST come FIRST — BadRequestError is a subclass of APIStatusError
            error_str = str(e).lower()
            if 'context_length_exceeded' in error_str or 'maximum context length' in error_str:
                logger.warning(f"[LLM] Context length exceeded (non-retryable), triggering compaction: {_truncate_error(e)}")
                raise ContextLengthExceededError(e) from e
            logger.error(f"[LLM] BadRequestError (non-retryable): {_truncate_error(e)}")
            raise  # Other BadRequestErrors (genuine bugs) — pass through
        except openai.APIStatusError as e:
            if is_retryable_status_code(e.status_code):
                logger.warning(f"[LLM] Transient API error (status={e.status_code}), will retry: {_truncate_error(e)}")
                raise TransientAPIError(e) from e
            logger.error(f"[LLM] Non-retryable API error (status={e.status_code}): {_truncate_error(e)}")
            raise  # Non-retryable status error — pass through
        except openai.APITimeoutError as e:
            logger.warning(f"[LLM] API timeout, will retry: {_truncate_error(e)}")
            raise
        except openai.APIConnectionError as e:
            logger.warning(f"[LLM] Connection error, will retry: {_truncate_error(e)}")
            raise
        except (ConnectionResetError, BrokenPipeError, ConnectionAbortedError) as e:
            logger.warning(f"[LLM] Connection error ({type(e).__name__}), will retry: {_truncate_error(e)}")
            raise
        except LLMResponseValidationError as e:
            logger.warning(f"[LLM] Response validation failed, will retry: {_truncate_error(e)}")
            raise
        except openai.APIResponseValidationError as e:
            # Proxy returned non-JSON (HTML error page) — transient
            logger.warning(f"[LLM] Response validation error (proxy issue), will retry: {_truncate_error(e)}")
            raise
        except openai.APIError as e:
            # Bare APIError — no status code channel (C1 corpus channel:
            # relayed "All models rate limited" / "context deadline
            # exceeded" bodies). PLACEMENT IS LOAD-BEARING: this branch
            # must come AFTER every APIError-subclass handler above
            # (BadRequestError, APIStatusError, APITimeoutError,
            # APIConnectionError, APIResponseValidationError — MRO
            # verified) or it shadows them. The wrap decision lives in
            # the shared ``classify_transient_apierror_body`` helper
            # (also used by the L2 facade — one kind-routing
            # implementation); misses re-raise (bad-params shapes stay
            # terminal, guarded by the mandatory blocklist).
            #
            # Quota-window check FIRST (usage-limit-deferral-path W1):
            # quota hits are a subset of today's blocklist hits, so
            # typing them before the allowlist/blocklist flow preserves
            # every other shape byte-identically.
            if _matches_usage_limit(str(e)):
                logger.error(f"[LLM] Usage limit (quota window) — typed terminal, deferral path takes over: {_truncate_error(e)}")
                raise UsageLimitError(e) from e
            wrapper = classify_transient_apierror_body(e)
            if wrapper is not None:
                logger.warning(f"[LLM] Transient API error (bare, pattern-matched), will retry: {_truncate_error(e)}")
                raise wrapper from e
            logger.error(f"[LLM] Non-retryable API error: {_truncate_error(e)}")
            raise
        except MalformedLLMResponseError as e:
            # Provider returned a response body of an unexpected type
            # (e.g. a bare JSON string instead of a ChatCompletion
            # object). Raised by the ThinkingChatOpenAI._create_chat_result
            # type-guard (daemon/graph.py) BEFORE super() so the
            # AttributeError never surfaces from inside LangChain. A member
            # of TRANSIENT_EXCEPTIONS — the retry predicate treats it as
            # transient (RetryByCategory), so tenacity retries it.
            logger.warning(f"[LLM] Malformed response (retryable): {_truncate_error(e)}")
            raise
        except IndexError as e:
            # Malformed LLM response (e.g., empty choices array). LangChain's
            # .invoke() crashes on choices[0] when the provider returns
            # choices: []. With no backup configured this is non-retryable —
            # retrying typically hits the same malformed payload, so we
            # re-raise to let the upstream error pipeline handle it
            # (instance_messaging / task_processor). When a backup IS
            # configured the retry predicate treats IndexError as transient
            # and fails over (see RetryByCategory), so the wording below
            # stays condition-neutral: the classifier itself never retries,
            # it only classifies.
            logger.error(
                f"[LLM] Malformed LLM response (IndexError, likely empty "
                f"choices array): {_truncate_error(e)}"
            )
            raise
        except ValueError as e:
            # 200-body proxy errors / stream aggregation shapes (C2/C4
            # corpus channels): LangChain parses a proxy error dict or
            # a zero-chunk SSE stream into a ValueError. Only
            # pattern-matched messages become retryable; a genuine data
            # bug re-raises unchanged (non-retryable). Note
            # LLMResponseValidationError (also a ValueError subclass,
            # if it is one) is handled by its own earlier branch.
            #
            # Quota-window check FIRST (usage-limit-deferral-path W1):
            # the cc753c2f §review guard proved quota text can ride
            # 200-body dicts — the same typing as the bare-APIError
            # channel, before the transient pattern match.
            if _matches_usage_limit(str(e)):
                logger.error(f"[LLM] Usage limit in 200-body (ValueError channel) — typed terminal, deferral path takes over: {_truncate_error(e)}")
                raise UsageLimitError(e) from e
            if _matches_transient_valueerror(str(e)):
                logger.warning(f"[LLM] Transient error body (ValueError, pattern-matched), will retry: {_truncate_error(e)}")
                raise TransientLLMError("value_error_body", e) from e
            raise
        except httpx.RemoteProtocolError as e:
            # Peer closed the connection mid-body ("peer closed
            # connection without sending complete message body
            # (incomplete chunked read)") — C3 corpus channel.
            # Retryability is CONDITIONAL on the config gate
            # (``remote_protocol_retryable``, default on) and decided by
            # the retry predicate, so the wording stays
            # condition-neutral — the classifier only classifies.
            logger.warning(f"[LLM] Remote protocol error (peer closed connection mid-body): {_truncate_error(e)}")
            raise
        except Exception as e:
            logger.error(f"[LLM] Unexpected error (will not retry): {type(e).__name__}: {_truncate_error(e)}")
            raise  # Everything else passes through (including socket errors)
    
    return RunnableLambda(func=_run_with_classification)
