"""LLM error classification utilities for retry handling and context overflow detection."""

import logging
from typing import Any

import httpx
import openai
from langchain_core.runnables import RunnableLambda

from .response_validation import LLMResponseValidationError, validate_llm_response

logger = logging.getLogger(__name__)

# Status codes that indicate transient/retryable errors
RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}


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
)


# Timeout errors — expensive retries (each costs up to request_timeout)
TIMEOUT_EXCEPTIONS: tuple[type[Exception], ...] = (
    openai.APITimeoutError,
    httpx.TimeoutException,
    TimeoutError,
)


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
                logger.warning(f"[LLM] Context length exceeded (non-retryable), triggering compaction: {e}")
                raise ContextLengthExceededError(e) from e
            logger.error(f"[LLM] BadRequestError (non-retryable): {e}")
            raise  # Other BadRequestErrors (genuine bugs) — pass through
        except openai.APIStatusError as e:
            if e.status_code in RETRYABLE_STATUS_CODES:
                logger.warning(f"[LLM] Transient API error (status={e.status_code}), will retry: {e}")
                raise TransientAPIError(e) from e
            logger.error(f"[LLM] Non-retryable API error (status={e.status_code}): {e}")
            raise  # Non-retryable status error — pass through
        except openai.APITimeoutError as e:
            logger.warning(f"[LLM] API timeout, will retry: {e}")
            raise
        except openai.APIConnectionError as e:
            logger.warning(f"[LLM] Connection error, will retry: {e}")
            raise
        except (ConnectionResetError, BrokenPipeError, ConnectionAbortedError) as e:
            logger.warning(f"[LLM] Connection error ({type(e).__name__}), will retry: {e}")
            raise
        except LLMResponseValidationError as e:
            logger.warning(f"[LLM] Response validation failed, will retry: {e}")
            raise
        except Exception as e:
            logger.error(f"[LLM] Unexpected error (will not retry): {type(e).__name__}: {e}")
            raise  # Everything else passes through (including socket errors)
    
    return RunnableLambda(func=_run_with_classification)
