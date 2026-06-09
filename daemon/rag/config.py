"""RAG configuration from environment variables."""

import logging
import os
from dataclasses import dataclass
from typing import Any

import httpx

logger = logging.getLogger(__name__)

# Module-level flag for RAG enabled state (can be disabled by auto-test)
_rag_enabled: bool = True

# Auto-test timeout in seconds
AUTO_TEST_TIMEOUT: float = 15.0


class RAGRequiredError(RuntimeError):
    """Raised when RAG_IS_REQUIRED is set and the RAG auto-test fails.

    The service should exit with a non-zero status when this is raised.
    """


def _is_truthy(value: str | None) -> bool:
    """Return True if the value represents a truthy env var value."""
    if value is None:
        return False
    return value.strip().lower() in {"1", "true", "yes", "on"}


def is_rag_required() -> bool:
    """Check if RAG is required via RAG_IS_REQUIRED env var.

    When required, the service will exit with an error if the auto-test fails.
    """
    return _is_truthy(os.getenv("RAG_IS_REQUIRED"))


@dataclass(frozen=True)
class RAGConfig:
    """LightRAG connection configuration.

    All values come from environment variables.
    If LIGHTRAG_HOST is not set, RAG is disabled.
    """
    host: str | None = None
    api_key: str | None = None
    workspace: str = ""
    timeout: float = 120.0

    @property
    def is_configured(self) -> bool:
        """Check if LightRAG is properly configured."""
        return bool(self.host)

    @property
    def base_url(self) -> str:
        """Get the full base URL for the LightRAG API."""
        return self.host.rstrip("/") if self.host else ""

    @classmethod
    def from_env(cls) -> "RAGConfig":
        """Load configuration from environment variables."""
        try:
            timeout = float(os.getenv("LIGHTRAG_TIMEOUT", "120"))
        except (ValueError, TypeError):
            logger.warning(
                "Invalid LIGHTRAG_TIMEOUT value, using default of 120.0 seconds"
            )
            timeout = 120.0
        return cls(
            host=os.getenv("LIGHTRAG_HOST"),
            api_key=os.getenv("LIGHTRAG_API_KEY"),
            workspace=os.getenv("LIGHTRAG_WORKSPACE", "").strip(),
            timeout=timeout,
        )


def is_rag_enabled() -> bool:
    """Check if RAG is enabled via environment configuration.

    Returns True only if:
    1. The module-level _rag_enabled flag is True (not disabled by auto-test), AND
    2. LIGHTRAG_HOST environment variable is set
    """
    return _rag_enabled and RAGConfig.from_env().is_configured


def disable_rag() -> None:
    """Disable RAG functionality (e.g., after auto-test failure)."""
    global _rag_enabled
    _rag_enabled = False


def enable_rag() -> None:
    """Re-enable RAG after a previous auto-test failure.

    Warning: This bypasses auto-test validation. Intended for testing
    or manual recovery only — RAG backend may still be unreachable.
    """
    global _rag_enabled
    _rag_enabled = True


def _sanitize_workspace(workspace: str) -> str:
    """Match LightRAG's workspace sanitization: alphanumeric + underscore only."""
    import re
    return re.sub(r'[^a-zA-Z0-9_]', '_', workspace)


async def _attempt_auto_test(config: RAGConfig) -> str | None:
    """Run a single RAG auto-test attempt.

    Returns:
        None on success, or a human-readable failure reason string.
    """
    headers: dict[str, str] = {}
    if config.workspace:
        headers["LIGHTRAG-WORKSPACE"] = _sanitize_workspace(config.workspace)
    if config.api_key:
        headers["X-API-Key"] = config.api_key

    request_body: dict[str, Any] = {
        "query": "test",
        "mode": "mix",
        "only_need_context": True,
    }

    try:
        async with httpx.AsyncClient(
            base_url=config.base_url,
            timeout=httpx.Timeout(AUTO_TEST_TIMEOUT),
        ) as client:
            response = await client.post(
                "/query/data",
                json=request_body,
                headers=headers,
            )
            response.raise_for_status()

        return None

    except httpx.TimeoutException:
        return f"timeout after {AUTO_TEST_TIMEOUT:.1f}s"

    except httpx.HTTPStatusError as e:
        detail = ""
        try:
            error_data = e.response.json()
            detail = error_data.get("detail", str(error_data))
        except Exception:
            detail = e.response.text or str(e)
        return f"LightRAG error {e.response.status_code}: {detail}"

    except httpx.ConnectError as e:
        return f"connection refused: {e}"

    except httpx.RemoteProtocolError as e:
        return f"server disconnected: {e}"

    except Exception as e:
        return f"{type(e).__name__}: {e}"


async def auto_test_rag() -> bool:
    """Run a lightweight auto-test against the RAG backend on startup.

    Tests that RAG is actually reachable and responding correctly by making
    a simple query. This catches misconfiguration like wrong API keys (401),
    connection refused, timeouts, etc.

    When RAG_IS_REQUIRED is set, the test will retry once on failure and
    raise RAGRequiredError if both attempts fail (the service is expected
    to exit on this error).

    Returns:
        True if RAG is working correctly and should remain enabled.
        False if RAG should be disabled (test failed or not configured).

    Raises:
        RAGRequiredError: When RAG_IS_REQUIRED is set and the auto-test
            fails on both attempts.
    """
    config = RAGConfig.from_env()

    if not config.is_configured:
        logger.debug("RAG auto-test skipped: not configured (no LIGHTRAG_HOST)")
        return False

    required = is_rag_required()
    max_attempts = 2 if required else 1

    for attempt in range(1, max_attempts + 1):
        if attempt == 1:
            logger.info("Running RAG auto-test...")
        else:
            logger.info("Retrying RAG auto-test (attempt %d/%d)...", attempt, max_attempts)

        reason = await _attempt_auto_test(config)
        if reason is None:
            logger.info("RAG auto-test passed: LightRAG is reachable")
            return True

        logger.warning("RAG auto-test attempt %d/%d failed: %s", attempt, max_attempts, reason)

    # All attempts failed
    if required:
        raise RAGRequiredError(
            f"RAG_IS_REQUIRED is set but RAG auto-test failed after {max_attempts} attempts. "
            f"Last error: {reason}"
        )

    logger.warning("Disabling RAG.")
    disable_rag()
    return False
