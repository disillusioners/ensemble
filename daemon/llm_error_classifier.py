"""LLM error classification utilities for retry handling and context overflow detection."""

import logging
import re
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


# Exceptions that with_retry should catch and retry — server/connection errors
TRANSIENT_EXCEPTIONS: tuple[type[Exception], ...] = (
    # Wrapper exception from classifier for retryable status codes
    TransientAPIError,
    # Raw socket errors (proxy restarts) — not wrapped by OpenAI SDK
    ConnectionResetError,
    BrokenPipeError,
    ConnectionAbortedError,
    # OpenAI exceptions that DON'T get wrapped (from lower-level HTTP client)
    openai.APIConnectionError,
    # Response validation failure from Phase 1
    LLMResponseValidationError,
    # Proxy returning non-JSON response (e.g., HTML error page)
    openai.APIResponseValidationError,
    # NOTE: IndexError is intentionally NOT in TRANSIENT_EXCEPTIONS —
    # unconditionally. LangChain's .invoke() raises IndexError on
    # choices[0] when the LLM returns choices: [], which is a malformed
    # response. Its retryability is now CONDITIONAL on controller
    # presence: when a FailoverController (backup URL) is supplied to
    # _make_llm_retry_strategy, the predicate treats IndexError as
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
# ``_make_llm_retry_strategy``). Exported as module constants so the
# strategy defaults and ``daemon.graph.build_instance_llms``'s
# ``stop_after_attempt`` ceiling derivation stay in lock-step — graph.py
# adds ``max(PRIMARY_TRANSIENT_MAX, PRIMARY_TIMEOUT_MAX)`` to the slice
# caps when computing the total attempts ceiling. If these defaults
# change, graph.py picks the new values up automatically.
PRIMARY_TRANSIENT_MAX = 3  # primary tolerates 2 transient retries before swap
PRIMARY_TIMEOUT_MAX = 2  # primary tolerates 1 timeout retry before swap


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
      - ``reset_to_primary()`` rewrites it back. Called at the start of every
        new invoke cycle so behavior is non-sticky across invokes.
      - Idempotent: ``swap_to_backup()`` when already on backup is a no-op,
        matching the predicate's "swapped" flag. This avoids redundant log
        lines when the swap is re-asserted.

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

        Called at the start of each new invoke cycle to enforce the
        non-sticky contract (next invoke starts on primary regardless of
        where the previous one ended).
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
        """
        for client_attr in ("root_client", "root_async_client"):
            client = getattr(self._chat_client, client_attr, None)
            if client is None:
                continue
            try:
                client.base_url = new_url
            except Exception:
                logger.debug(
                    f"[LLM-HA] Could not set base_url on {client_attr} "
                    f"({type(client).__name__}); failover may be a no-op.",
                    exc_info=True,
                )


def _make_llm_retry_strategy(
    transient_max: int,
    timeout_max: int,
    failover_controller: "FailoverController | None" = None,
    primary_transient_max: int = PRIMARY_TRANSIENT_MAX,
    primary_timeout_max: int = PRIMARY_TIMEOUT_MAX,
):
    """Create a retry strategy with separate per-category attempt limits.

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
        failover_controller: Optional ``FailoverController``. When supplied,
            the predicate performs a one-shot swap to the backup ``base_url``
            after the primary phase exhausts its small slice, then resets
            counters and grants the FULL ``transient_max``/``timeout_max``
            budget to the backup. When ``None`` the predicate behaves
            identically to the pre-HA system (zero behavior change).
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

        def __call__(self, retry_state) -> bool:
            # Reset counters at the start of each new invoke cycle.
            # tenacity creates fresh RetryCallState per cycle but reuses the predicate.
            # attempt_number == 1 means first failure of a new cycle.
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
            if isinstance(exception, TIMEOUT_EXCEPTIONS):
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
                failover_controller is not None
                and isinstance(exception, IndexError)
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

            if current >= primary_cap:
                # Primary exhausted for this category — swap and reset.
                failover_controller.swap_to_backup()
                counts["swapped"] = True
                counts[category] = 0
                logger.warning(
                    f"[LLM-HA] {failover_controller.failover_summary()}"
                )
                return True  # immediate retry on backup

            # Still on primary, within slice — continue.
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
            if e.status_code in RETRYABLE_STATUS_CODES:
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
        except IndexError as e:
            # Malformed LLM response (e.g., empty choices array). LangChain's
            # .invoke() crashes on choices[0] when the provider returns
            # choices: []. This is non-retryable — retrying typically hits the
            # same malformed payload, so we re-raise to let the upstream
            # error pipeline handle it (instance_messaging / task_processor).
            logger.error(
                f"[LLM] Malformed LLM response (IndexError, likely empty "
                f"choices array, will not retry): {_truncate_error(e)}"
            )
            raise
        except Exception as e:
            logger.error(f"[LLM] Unexpected error (will not retry): {type(e).__name__}: {_truncate_error(e)}")
            raise  # Everything else passes through (including socket errors)
    
    return RunnableLambda(func=_run_with_classification)
