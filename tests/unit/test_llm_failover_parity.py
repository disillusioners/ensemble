"""L2 facade parity tests for non-status transient channels.

docs/plans/transient-channel-retry-widening.md work unit 5 / test 5:
``daemon.services.llm_failover._classify_raw_sdk_exceptions`` must mirror
the hot-path classifier's bare-APIError / ValueError pattern branches
using the SAME helpers (imported, never duplicated).
"""

import httpx
import openai
import pytest
from unittest.mock import MagicMock

from daemon.llm_error_classifier import (
    TransientAPIError,
    TransientLLMError,
    UsageLimitError,
)
from daemon.services.llm_failover import _classify_raw_sdk_exceptions


def _bare_api_error(message: str) -> openai.APIError:
    return openai.APIError(message, request=httpx.Request("POST", "http://t/v1"), body=None)


def _run_classified(fn):
    """Run fn() through the facade classifier, returning the raised exception."""
    wrapped = _classify_raw_sdk_exceptions(fn)
    with pytest.raises(BaseException) as exc_info:
        wrapped()
    return exc_info.value


class TestFacadeParityBareAPIError:
    """C1 through the facade — same wrap as the hot path."""

    def test_c1_rate_limited_wrapped_transient(self):
        """Bare APIError('All models rate limited') → TransientLLMError
        (kind='api_error_body')."""
        original = _bare_api_error("All models rate limited")

        raised = _run_classified(lambda: (_ for _ in ()).throw(original))

        assert isinstance(raised, TransientLLMError)
        assert raised.kind == "api_error_body"
        assert raised.original is original

    def test_c1_timeout_body_routes_to_timeout_kind(self):
        """Relayed 'context deadline exceeded' → kind='timeout_body'."""
        original = _bare_api_error("context deadline exceeded (Client.Timeout)")

        raised = _run_classified(lambda: (_ for _ in ()).throw(original))

        assert isinstance(raised, TransientLLMError)
        assert raised.kind == "timeout_body"

    def test_2056_token_plan_typed_usage_limit(self):
        """Quota shape — typed UsageLimitError at the facade
        (usage-limit-deferral-path W1 facade parity: same helper, same
        typing as the hot path). The facade does NOT retry it — the
        surface is typed for logs; secondary sites' generic
        ``except Exception`` fallbacks still match (type-swap audited)."""
        original = _bare_api_error("Token Plan usage limit reached (2056)")

        raised = _run_classified(lambda: (_ for _ in ()).throw(original))

        assert isinstance(raised, UsageLimitError)
        assert raised.original is original
        assert not isinstance(raised, TransientLLMError)


class TestFacadeParityOrdering:
    """Subclass pass-through branches must precede the bare-APIError
    pattern branch (both APIConnectionError and APITimeoutError are
    APIError subclasses)."""

    def test_api_connection_error_passes_through(self):
        original = openai.APIConnectionError(
            message="Connection failed", request=MagicMock()
        )

        raised = _run_classified(lambda: (_ for _ in ()).throw(original))

        assert raised is original
        assert not isinstance(raised, TransientLLMError)

    def test_api_timeout_error_passes_through(self):
        original = openai.APITimeoutError(request=MagicMock())

        raised = _run_classified(lambda: (_ for _ in ()).throw(original))

        assert raised is original
        assert not isinstance(raised, TransientLLMError)

    def test_status_error_wrapping_unchanged(self):
        """Retryable status codes still become TransientAPIError (the
        pre-existing facade behavior)."""
        original = openai.APIStatusError(
            "server_error",
            response=httpx.Response(500, request=httpx.Request("POST", "http://t/v1")),
            body=None,
        )

        raised = _run_classified(lambda: (_ for _ in ()).throw(original))

        assert isinstance(raised, TransientAPIError)
        assert raised.original is original


class TestFacadeParityValueError:
    """C2/C4 through the facade."""

    def test_c4_no_generations_found_wrapped(self):
        original = ValueError("No generations found in stream.")

        raised = _run_classified(lambda: (_ for _ in ()).throw(original))

        assert isinstance(raised, TransientLLMError)
        assert raised.kind == "value_error_body"
        assert raised.original is original

    def test_c2_ultimate_exhausted_wrapped(self):
        original = ValueError("{'type': 'ultimate_model_retry_exhausted'}")

        raised = _run_classified(lambda: (_ for _ in ()).throw(original))

        assert isinstance(raised, TransientLLMError)
        assert raised.kind == "value_error_body"

    def test_generic_value_error_stays_terminal(self):
        original = ValueError("genuine data bug")

        raised = _run_classified(lambda: (_ for _ in ()).throw(original))

        assert raised is original
        assert not isinstance(raised, TransientLLMError)

    def test_blocklist_guards_valueerror_channel(self):
        """Blocklist precedence on the ValueError channel — a 200-body
        dict embedding quota wording stays TERMINAL through the facade
        (now typed as UsageLimitError, same as the hot path; never
        wrapped transient)."""
        poisoned = ValueError(
            "{'detail': 'usage limit exceeded', 'type': 'ultimate_model_retry_exhausted'}"
        )

        raised = _run_classified(lambda: (_ for _ in ()).throw(poisoned))

        assert isinstance(raised, UsageLimitError)
        assert raised.original is poisoned
        assert not isinstance(raised, TransientLLMError)
