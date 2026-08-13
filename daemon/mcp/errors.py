"""Exception hierarchy for MCP-layer error classification.

Used by the resilience layer (daemon/mcp/resilience.py) to classify raw
exceptions raised by the underlying MCP session / transport and to drive
retry / circuit-breaker / graceful-degradation decisions.

Each subclass has a single, fixed retryability contract:

- ``McpAuthError`` — non-retryable. The API key is wrong/expired; retrying
  just spams the server with the same bad credentials. Surface to the
  caller as a ``ToolException`` so the operator notices.
- ``McpTransientError`` — retryable. Transient failures (timeout, 5xx,
  connection reset). The ``RetryPolicy`` in resilience.py retries these
  with exponential backoff + jitter.
- ``McpUnavailableError`` — non-retryable in the *retry* sense, but
  ``_lazy_coroutine`` still handles it specially: it returns the
  configured fallback JSON (graceful degradation) instead of raising.
  Raised internally when the circuit breaker is OPEN.
- ``McpToolError`` — non-retryable. The tool ran but the server returned
  an error result. Surfacing this to the agent is the right behavior —
  retrying won't change the outcome.

The base ``McpError`` is reserved for ``except`` clauses that should
catch any MCP-layer error; production code should narrow to a
specific subclass when it can act differently per kind.
"""


class McpError(Exception):
    """Base exception for MCP-layer errors.

    Catch this in callers that want to handle ANY MCP-layer failure
    uniformly. Code that distinguishes retryable from non-retryable
    should catch a specific subclass (``McpTransientError`` vs the
    others).
    """


class McpAuthError(McpError):
    """Authentication failure (401/403).

    Non-retryable — the API key is wrong/expired/missing. Retrying
    just spams the server with the same bad credentials and burns the
    failure budget in the circuit breaker. Surface to the caller so
    the operator notices and rotates the key.
    """


class McpTransientError(McpError):
    """Transient failure (timeout, 5xx, connection reset).

    Retryable. The ``RetryPolicy`` retries with exponential backoff
    + jitter up to ``max_attempts``. After all attempts are exhausted
    the exception bubbles to ``_lazy_coroutine``, which records a
    circuit-breaker failure and returns the configured fallback
    (when present) instead of raising.
    """


class McpUnavailableError(McpError):
    """Server unavailable (circuit OPEN, server down).

    Reserved — the circuit-OPEN path in ``_lazy_coroutine`` currently
    returns fallback JSON directly without raising this exception.
    Callers that want exception-based degradation can raise it
    themselves.

    Non-retryable in the *retry* sense — the circuit breaker already
    knows the server is down. The natural caller pattern is to
    raise ``McpUnavailableError`` from a custom resilience layer
    that wants explicit exception-based degradation instead of the
    default "return fallback JSON" path. The base ``McpError``
    catch-all in ``_lazy_coroutine`` handles the case where a
    custom layer does raise it (the error is surfaced as a
    ``ToolException`` so LangGraph's ToolNode routes it to the
    agent).

    Distinguished from ``McpTransientError`` so callers can pick:
    a transient blip retries, a sustained outage degrades
    immediately.
    """


class McpToolError(McpError):
    """Tool execution error (the tool ran but returned an error result).

    Non-retryable. The MCP server successfully invoked the tool but
    the tool's own logic reported a failure (bad input, business-rule
    violation, etc.). Retrying won't change the outcome — surface
    the message to the agent so it can adapt its call.
    """
