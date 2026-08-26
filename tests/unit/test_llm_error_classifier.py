"""Tests for LLM error classifier module."""

import httpx
import pytest
from unittest.mock import MagicMock, Mock, patch
import openai

from daemon.llm_error_classifier import (
    RETRYABLE_STATUS_CODES,
    TRANSIENT_EXCEPTIONS,
    TIMEOUT_EXCEPTIONS,
    TransientAPIError,
    TransientLLMError,
    ContextLengthExceededError,
    UsageLimitError,
    classify_llm_errors,
    configure_transient_channel_patterns,
    configure_usage_limit_patterns,
    make_llm_retry_strategy,
    reset_transient_channel_patterns,
    reset_usage_limit_patterns,
)
from daemon.response_validation import LLMResponseValidationError


class TestTransientAPIError:
    """Tests for TransientAPIError exception."""

    def test_creation_with_status_code(self):
        """TransientAPIError wraps APIStatusError and stores status_code."""
        mock_response = MagicMock()
        mock_response.status_code = 502
        mock_response.text = "Bad Gateway"
        original = openai.APIStatusError(
            "Bad Gateway", response=mock_response, body=None
        )

        error = TransientAPIError(original)

        assert error.original is original
        assert error.status_code == 502
        assert "502" in str(error)

    def test_creation_with_429_status_code(self):
        """TransientAPIError wraps 429 rate limit error."""
        mock_response = MagicMock()
        mock_response.status_code = 429
        mock_response.text = "Rate limit exceeded"
        original = openai.APIStatusError(
            "Rate limit", response=mock_response, body=None
        )

        error = TransientAPIError(original)

        assert error.status_code == 429
        assert "429" in str(error)
        assert "Rate limit" in str(error)

    def test_creation_with_503_status_code(self):
        """TransientAPIError wraps 503 service unavailable error."""
        mock_response = MagicMock()
        mock_response.status_code = 503
        mock_response.text = "Service unavailable"
        original = openai.APIStatusError(
            "Service unavailable", response=mock_response, body=None
        )

        error = TransientAPIError(original)

        assert error.status_code == 503
        assert "503" in str(error)

    def test_stores_original_exception(self):
        """TransientAPIError stores reference to original exception."""
        mock_response = MagicMock()
        mock_response.status_code = 500
        original = openai.APIStatusError(
            "Internal Server Error",
            response=mock_response,
            body=None
        )

        error = TransientAPIError(original)

        assert error.original is original
        assert isinstance(error.original, openai.APIStatusError)


class TestContextLengthExceededError:
    """Tests for ContextLengthExceededError exception."""

    def test_creation_with_model(self):
        """ContextLengthExceededError stores model and original error."""
        mock_response = MagicMock()
        mock_response.status_code = 400
        mock_response.text = "context_length_exceeded"
        original = openai.BadRequestError(
            "Context length exceeded",
            response=mock_response,
            body=None
        )

        error = ContextLengthExceededError(original, model="gpt-4")

        assert error.original_error is original
        assert error.model == "gpt-4"
        assert "gpt-4" in str(error)

    def test_creation_without_model(self):
        """ContextLengthExceededError works without model specified."""
        mock_response = MagicMock()
        mock_response.status_code = 400
        original = openai.BadRequestError(
            "context_length_exceeded",
            response=mock_response,
            body=None
        )

        error = ContextLengthExceededError(original)

        assert error.original_error is original
        assert error.model == ""
        assert "Context length exceeded" in str(error)

    def test_creation_stores_original_error(self):
        """ContextLengthExceededError stores the original BadRequestError."""
        mock_response = MagicMock()
        mock_response.status_code = 400
        original = openai.BadRequestError(
            "maximum context length exceeded",
            response=mock_response,
            body=None
        )

        error = ContextLengthExceededError(original, model="gpt-4o")

        assert error.original_error is original
        assert isinstance(error.original_error, openai.BadRequestError)
        assert "gpt-4o" in str(error)


class TestRetryableStatusCodes:
    """Tests for RETRYABLE_STATUS_CODES constant."""

    def test_contains_expected_codes(self):
        """RETRYABLE_STATUS_CODES should contain standard + Cloudflare transient codes."""
        assert RETRYABLE_STATUS_CODES == {429, 500, 502, 503, 504, 520, 521, 522, 523, 524}

    def test_contains_429_rate_limit(self):
        """RETRYABLE_STATUS_CODES should contain 429."""
        assert 429 in RETRYABLE_STATUS_CODES

    def test_contains_500_server_error(self):
        """RETRYABLE_STATUS_CODES should contain 500."""
        assert 500 in RETRYABLE_STATUS_CODES

    def test_contains_502_bad_gateway(self):
        """RETRYABLE_STATUS_CODES should contain 502."""
        assert 502 in RETRYABLE_STATUS_CODES

    def test_contains_503_service_unavailable(self):
        """RETRYABLE_STATUS_CODES should contain 503."""
        assert 503 in RETRYABLE_STATUS_CODES

    def test_contains_504_gateway_timeout(self):
        """RETRYABLE_STATUS_CODES should contain 504."""
        assert 504 in RETRYABLE_STATUS_CODES

    def test_does_not_contain_401(self):
        """RETRYABLE_STATUS_CODES should NOT contain 401."""
        assert 401 not in RETRYABLE_STATUS_CODES

    def test_does_not_contain_403(self):
        """RETRYABLE_STATUS_CODES should NOT contain 403."""
        assert 403 not in RETRYABLE_STATUS_CODES

    def test_does_not_contain_400(self):
        """RETRYABLE_STATUS_CODES should NOT contain 400."""
        assert 400 not in RETRYABLE_STATUS_CODES


class TestTransientExceptions:
    """Tests for TRANSIENT_EXCEPTIONS tuple."""

    def test_contains_required_types(self):
        """TRANSIENT_EXCEPTIONS should contain all required exception types (except timeout)."""
        required = (
            TransientAPIError,
            LLMResponseValidationError,
            ConnectionResetError,
            BrokenPipeError,
            ConnectionAbortedError,
            openai.APIConnectionError,
        )
        for exc_type in required:
            assert exc_type in TRANSIENT_EXCEPTIONS

    def test_contains_transient_api_error(self):
        """TRANSIENT_EXCEPTIONS should contain TransientAPIError."""
        assert TransientAPIError in TRANSIENT_EXCEPTIONS

    def test_contains_llm_response_validation_error(self):
        """TRANSIENT_EXCEPTIONS should contain LLMResponseValidationError."""
        assert LLMResponseValidationError in TRANSIENT_EXCEPTIONS

    def test_contains_api_response_validation_error(self):
        """TRANSIENT_EXCEPTIONS should contain openai.APIResponseValidationError.

        When proxy returns HTML instead of JSON, SDK raises this error.
        """
        assert openai.APIResponseValidationError in TRANSIENT_EXCEPTIONS

    def test_contains_connection_reset_error(self):
        """TRANSIENT_EXCEPTIONS should contain ConnectionResetError."""
        assert ConnectionResetError in TRANSIENT_EXCEPTIONS

    def test_contains_broken_pipe_error(self):
        """TRANSIENT_EXCEPTIONS should contain BrokenPipeError."""
        assert BrokenPipeError in TRANSIENT_EXCEPTIONS

    def test_contains_connection_aborted_error(self):
        """TRANSIENT_EXCEPTIONS should contain ConnectionAbortedError."""
        assert ConnectionAbortedError in TRANSIENT_EXCEPTIONS

    def test_does_not_contain_api_timeout_error(self):
        """TRANSIENT_EXCEPTIONS should NOT contain openai.APITimeoutError (it's in TIMEOUT_EXCEPTIONS)."""
        assert openai.APITimeoutError not in TRANSIENT_EXCEPTIONS

    def test_contains_api_connection_error(self):
        """TRANSIENT_EXCEPTIONS should contain openai.APIConnectionError."""
        assert openai.APIConnectionError in TRANSIENT_EXCEPTIONS

    def test_does_not_contain_context_length_exceeded_error(self):
        """TRANSIENT_EXCEPTIONS should NOT contain ContextLengthExceededError."""
        assert ContextLengthExceededError not in TRANSIENT_EXCEPTIONS

    def test_is_tuple(self):
        """TRANSIENT_EXCEPTIONS should be a tuple."""
        assert isinstance(TRANSIENT_EXCEPTIONS, tuple)


class TestTimeoutExceptions:
    """Tests for TIMEOUT_EXCEPTIONS tuple."""

    def test_contains_required_types(self):
        """TIMEOUT_EXCEPTIONS should contain all timeout-related exception types."""
        import httpx
        required = (
            openai.APITimeoutError,
            httpx.TimeoutException,
            TimeoutError,
        )
        for exc_type in required:
            assert exc_type in TIMEOUT_EXCEPTIONS

    def test_contains_api_timeout_error(self):
        """TIMEOUT_EXCEPTIONS should contain openai.APITimeoutError."""
        assert openai.APITimeoutError in TIMEOUT_EXCEPTIONS

    def test_contains_httpx_timeout_exception(self):
        """TIMEOUT_EXCEPTIONS should contain httpx.TimeoutException."""
        import httpx
        assert httpx.TimeoutException in TIMEOUT_EXCEPTIONS

    def test_contains_timeout_error(self):
        """TIMEOUT_EXCEPTIONS should contain TimeoutError."""
        assert TimeoutError in TIMEOUT_EXCEPTIONS

    def test_does_not_contain_transient_api_error(self):
        """TIMEOUT_EXCEPTIONS should NOT contain TransientAPIError."""
        assert TransientAPIError not in TIMEOUT_EXCEPTIONS

    def test_is_tuple(self):
        """TIMEOUT_EXCEPTIONS should be a tuple."""
        assert isinstance(TIMEOUT_EXCEPTIONS, tuple)


class TestClassifyLLErrors:
    """Tests for classify_llm_errors function."""

    def _create_mock_llm(self, side_effect):
        """Helper to create mock LLM that raises given exception."""
        mock_llm = MagicMock()
        mock_llm.invoke.side_effect = side_effect
        return mock_llm

    def _mock_successful_response(self):
        """Create a mock successful LLM response that passes validation."""
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text="Hello, world!")]
        mock_response.tool_calls = None
        return mock_response

    def test_wraps_429_as_transient_api_error(self):
        """HTTP 429 (rate limit) should be wrapped as TransientAPIError."""
        mock_response = MagicMock()
        mock_response.status_code = 429
        original = openai.APIStatusError(
            "Rate limit", response=mock_response, body=None
        )

        mock_llm = self._create_mock_llm(original)
        classified = classify_llm_errors(mock_llm)

        with pytest.raises(TransientAPIError) as exc_info:
            classified.invoke([])

        assert exc_info.value.status_code == 429
        assert exc_info.value.original is original

    def test_wraps_500_as_transient_api_error(self):
        """HTTP 500 (server error) should be wrapped as TransientAPIError."""
        mock_response = MagicMock()
        mock_response.status_code = 500
        original = openai.APIStatusError(
            "Internal Server Error",
            response=mock_response,
            body=None
        )

        mock_llm = self._create_mock_llm(original)
        classified = classify_llm_errors(mock_llm)

        with pytest.raises(TransientAPIError) as exc_info:
            classified.invoke([])

        assert exc_info.value.status_code == 500
        assert exc_info.value.original is original

    def test_wraps_502_as_transient_api_error(self):
        """HTTP 502 (bad gateway) should be wrapped as TransientAPIError."""
        mock_response = MagicMock()
        mock_response.status_code = 502
        original = openai.APIStatusError(
            "Bad Gateway", response=mock_response, body=None
        )

        mock_llm = self._create_mock_llm(original)
        classified = classify_llm_errors(mock_llm)

        with pytest.raises(TransientAPIError) as exc_info:
            classified.invoke([])

        assert exc_info.value.status_code == 502
        assert exc_info.value.original is original

    def test_wraps_503_as_transient_api_error(self):
        """HTTP 503 (service unavailable) should be wrapped as TransientAPIError."""
        mock_response = MagicMock()
        mock_response.status_code = 503
        original = openai.APIStatusError(
            "Service Unavailable",
            response=mock_response,
            body=None
        )

        mock_llm = self._create_mock_llm(original)
        classified = classify_llm_errors(mock_llm)

        with pytest.raises(TransientAPIError) as exc_info:
            classified.invoke([])

        assert exc_info.value.status_code == 503
        assert exc_info.value.original is original

    def test_wraps_504_as_transient_api_error(self):
        """HTTP 504 (gateway timeout) should be wrapped as TransientAPIError."""
        mock_response = MagicMock()
        mock_response.status_code = 504
        original = openai.APIStatusError(
            "Gateway Timeout", response=mock_response, body=None
        )

        mock_llm = self._create_mock_llm(original)
        classified = classify_llm_errors(mock_llm)

        with pytest.raises(TransientAPIError) as exc_info:
            classified.invoke([])

        assert exc_info.value.status_code == 504
        assert exc_info.value.original is original

    def test_wraps_524_cloudflare_timeout_as_transient_api_error(self):
        """HTTP 524 (Cloudflare origin timeout) should be wrapped as TransientAPIError.

        524 is a Cloudflare-specific code returned when an origin behind the
        proxy doesn't respond within its window; without retry it causes the
        instance to fail on the first attempt.
        """
        mock_response = MagicMock()
        mock_response.status_code = 524
        original = openai.APIStatusError(
            "A Timeout Occurred", response=mock_response, body=None
        )

        mock_llm = self._create_mock_llm(original)
        classified = classify_llm_errors(mock_llm)

        with pytest.raises(TransientAPIError) as exc_info:
            classified.invoke([])

        assert exc_info.value.status_code == 524
        assert exc_info.value.original is original

    def test_detects_context_length_exceeded_error(self):
        """'context_length_exceeded' in error should raise ContextLengthExceededError."""
        mock_response = MagicMock()
        mock_response.status_code = 400
        original = openai.BadRequestError(
            "Error: context_length_exceeded",
            response=mock_response,
            body=None
        )

        mock_llm = self._create_mock_llm(original)
        classified = classify_llm_errors(mock_llm)

        with pytest.raises(ContextLengthExceededError):
            classified.invoke([])

    def test_detects_maximum_context_length(self):
        """'maximum context length' in error should raise ContextLengthExceededError."""
        mock_response = MagicMock()
        mock_response.status_code = 400
        original = openai.BadRequestError(
            "maximum context length is 8192 tokens",
            response=mock_response,
            body=None
        )

        mock_llm = self._create_mock_llm(original)
        classified = classify_llm_errors(mock_llm)

        with pytest.raises(ContextLengthExceededError):
            classified.invoke([])

    def test_context_length_case_insensitive(self):
        """Context length detection should be case-insensitive."""
        mock_response = MagicMock()
        mock_response.status_code = 400
        original = openai.BadRequestError(
            "CONTEXT_LENGTH_EXCEEDED",
            response=mock_response,
            body=None
        )

        mock_llm = self._create_mock_llm(original)
        classified = classify_llm_errors(mock_llm)

        with pytest.raises(ContextLengthExceededError):
            classified.invoke([])

    def test_401_passes_through(self):
        """HTTP 401 should pass through unchanged (not wrapped)."""
        mock_response = MagicMock()
        mock_response.status_code = 401
        original = openai.APIStatusError(
            "Unauthorized", response=mock_response, body=None
        )

        mock_llm = self._create_mock_llm(original)
        classified = classify_llm_errors(mock_llm)

        with pytest.raises(openai.APIStatusError) as exc_info:
            classified.invoke([])

        assert exc_info.value.status_code == 401
        # Should NOT be wrapped
        assert not isinstance(exc_info.value, TransientAPIError)

    def test_403_passes_through(self):
        """HTTP 403 should pass through unchanged (not wrapped)."""
        mock_response = MagicMock()
        mock_response.status_code = 403
        original = openai.APIStatusError(
            "Forbidden", response=mock_response, body=None
        )

        mock_llm = self._create_mock_llm(original)
        classified = classify_llm_errors(mock_llm)

        with pytest.raises(openai.APIStatusError) as exc_info:
            classified.invoke([])

        assert exc_info.value.status_code == 403
        # Should NOT be wrapped
        assert not isinstance(exc_info.value, TransientAPIError)

    def test_404_passes_through(self):
        """HTTP 404 should pass through unchanged (not wrapped)."""
        mock_response = MagicMock()
        mock_response.status_code = 404
        original = openai.APIStatusError(
            "Not Found", response=mock_response, body=None
        )

        mock_llm = self._create_mock_llm(original)
        classified = classify_llm_errors(mock_llm)

        with pytest.raises(openai.APIStatusError) as exc_info:
            classified.invoke([])

        assert exc_info.value.status_code == 404
        assert not isinstance(exc_info.value, TransientAPIError)

    def test_api_connection_error_passes_through(self):
        """APIConnectionError should pass through unchanged."""
        # APIConnectionError requires a request object
        mock_request = MagicMock()
        original = openai.APIConnectionError(
            message="Connection failed",
            request=mock_request
        )

        mock_llm = self._create_mock_llm(original)
        classified = classify_llm_errors(mock_llm)

        with pytest.raises(openai.APIConnectionError):
            classified.invoke([])

    def test_api_timeout_error_passes_through(self):
        """APITimeoutError should pass through unchanged."""
        original = openai.APITimeoutError("Request timed out")

        mock_llm = self._create_mock_llm(original)
        classified = classify_llm_errors(mock_llm)

        with pytest.raises(openai.APITimeoutError):
            classified.invoke([])

    def test_connection_reset_error_passes_through(self):
        """ConnectionResetError should pass through for with_retry to catch."""
        mock_llm = self._create_mock_llm(
            ConnectionResetError("Connection reset by peer")
        )
        classified = classify_llm_errors(mock_llm)

        with pytest.raises(ConnectionResetError):
            classified.invoke([])

    def test_broken_pipe_error_passes_through(self):
        """BrokenPipeError should pass through for with_retry to catch."""
        mock_llm = self._create_mock_llm(
            BrokenPipeError("Broken pipe")
        )
        classified = classify_llm_errors(mock_llm)

        with pytest.raises(BrokenPipeError):
            classified.invoke([])

    def test_connection_aborted_error_passes_through(self):
        """ConnectionAbortedError should pass through for with_retry to catch."""
        mock_llm = self._create_mock_llm(
            ConnectionAbortedError("Connection aborted")
        )
        classified = classify_llm_errors(mock_llm)

        with pytest.raises(ConnectionAbortedError):
            classified.invoke([])

    def test_validation_error_passes_through(self):
        """LLMResponseValidationError should pass through for with_retry to catch."""
        original = LLMResponseValidationError("Invalid response format")

        mock_llm = self._create_mock_llm(original)
        classified = classify_llm_errors(mock_llm)

        with pytest.raises(LLMResponseValidationError):
            classified.invoke([])

    def test_api_response_validation_error_retried(self):
        """APIResponseValidationError (proxy returning HTML) should be retried.

        When a proxy returns HTML instead of JSON (e.g., 502/503 error page),
        the OpenAI SDK raises APIResponseValidationError. This should be
        caught and retried like other transient errors.
        """
        request = httpx.Request("POST", "http://llm-proxy.example.com/v1/chat")
        mock_response = httpx.Response(502, text="<html>Bad Gateway</html>", request=request)
        original = openai.APIResponseValidationError(
            response=mock_response,
            body=None,
            message="Failed to parse response"
        )

        mock_llm = self._create_mock_llm(original)
        classified = classify_llm_errors(mock_llm)

        # Should raise APIResponseValidationError for with_retry to catch
        with pytest.raises(openai.APIResponseValidationError):
            classified.invoke([])

    def test_bad_request_error_context_overflow_caught_as_context_exceeded(
        self,
    ):
        """CRITICAL: BadRequestError with context_length_exceeded should be
        ContextLengthExceededError, NOT TransientAPIError.

        This tests that except order is correct: BadRequestError BEFORE APIStatusError.
        If APIStatusError catches first, context overflow would become TransientAPIError
        and with_retry would retry it instead of triggering reactive compaction.
        """
        mock_response = MagicMock()
        mock_response.status_code = 400
        original = openai.BadRequestError(
            "context_length_exceeded",
            response=mock_response,
            body=None
        )

        mock_llm = self._create_mock_llm(original)
        classified = classify_llm_errors(mock_llm)

        # Should be ContextLengthExceededError, NOT TransientAPIError
        with pytest.raises(ContextLengthExceededError):
            classified.invoke([])

        # Verify it's NOT TransientAPIError
        try:
            classified.invoke([])
        except ContextLengthExceededError:
            pass  # Expected
        except TransientAPIError:
            pytest.fail(
                "Got TransientAPIError instead of ContextLengthExceededError. "
                "This means except order is wrong (APIStatusError before BadRequestError)."
            )

    def test_other_bad_request_error_passes_through(self):
        """BadRequestError without context overflow should pass through."""
        mock_response = MagicMock()
        mock_response.status_code = 400
        original = openai.BadRequestError(
            "Invalid request: missing required field",
            response=mock_response,
            body=None
        )

        mock_llm = self._create_mock_llm(original)
        classified = classify_llm_errors(mock_llm)

        with pytest.raises(openai.BadRequestError):
            classified.invoke([])

    def test_successful_response_passes_through(self):
        """Successful LLM response should pass through unchanged."""
        expected_response = self._mock_successful_response()
        mock_llm = MagicMock()
        mock_llm.invoke.return_value = expected_response

        with patch(
            "daemon.llm_error_classifier.validate_llm_response",
            return_value=None,
        ):
            classified = classify_llm_errors(mock_llm)
            result = classified.invoke([])

        assert result == expected_response
        mock_llm.invoke.assert_called_once()

    def test_validation_called_inside_classifier(self):
        """validate_llm_response should be called inside the classifier."""
        expected_response = self._mock_successful_response()
        mock_llm = MagicMock()
        mock_llm.invoke.return_value = expected_response

        with patch(
            "daemon.llm_error_classifier.validate_llm_response"
        ) as mock_validate:
            classified = classify_llm_errors(mock_llm)
            result = classified.invoke([])

            mock_validate.assert_called_once_with(expected_response)
            assert result == expected_response

    def test_validation_error_caught_inside_classifier(self):
        """LLMResponseValidationError from validation should be raised."""
        mock_llm = MagicMock()
        mock_llm.invoke.return_value = {"invalid": "response"}

        validation_error = LLMResponseValidationError("Missing required field")
        with patch(
            "daemon.llm_error_classifier.validate_llm_response",
            side_effect=validation_error,
        ):
            classified = classify_llm_errors(mock_llm)

            with pytest.raises(LLMResponseValidationError):
                classified.invoke([])

    def test_calls_llm_with_same_messages(self):
        """Classifier should pass messages to LLM unchanged."""
        mock_llm = MagicMock()
        mock_llm.invoke.return_value = self._mock_successful_response()

        test_messages = [{"role": "user", "content": "hello"}]

        with patch(
            "daemon.llm_error_classifier.validate_llm_response",
            return_value=None,
        ):
            classified = classify_llm_errors(mock_llm)
            classified.invoke(test_messages)

        mock_llm.invoke.assert_called_once_with(test_messages)

    def test_calls_llm_with_kwargs(self):
        """Classifier should pass additional kwargs to LLM."""
        mock_llm = MagicMock()
        mock_llm.invoke.return_value = self._mock_successful_response()

        with patch(
            "daemon.llm_error_classifier.validate_llm_response",
            return_value=None,
        ):
            classified = classify_llm_errors(mock_llm)
            classified.invoke([], temperature=0.7, max_tokens=100)

        mock_llm.invoke.assert_called_once()
        call_kwargs = mock_llm.invoke.call_args[1]
        assert call_kwargs["temperature"] == 0.7
        assert call_kwargs["max_tokens"] == 100

    def test_returns_runnable_lambda(self):
        """classify_llm_errors should return a RunnableLambda."""
        mock_llm = MagicMock()
        mock_llm.invoke.return_value = self._mock_successful_response()

        with patch(
            "daemon.llm_error_classifier.validate_llm_response",
            return_value=None,
        ):
            result = classify_llm_errors(mock_llm)

        assert result is not None
        # RunnableLambda has an invoke method
        assert hasattr(result, "invoke")


class TestRetryByCategory:
    """Tests for make_llm_retry_strategy per-category retry limits."""

    def _make_mock_retry_state(self, exception, attempt_number=1):
        """Create a mock RetryCallState with the given exception and attempt number."""
        from unittest.mock import MagicMock
        from tenacity import RetryCallState

        outcome = MagicMock()
        outcome.exception.return_value = exception

        retry_state = MagicMock(spec=RetryCallState)
        retry_state.outcome = outcome
        retry_state.attempt_number = attempt_number

        return retry_state

    def test_transient_errors_limited_to_transient_max(self):
        """Transient errors should be retried up to transient_max times."""
        from daemon.llm_error_classifier import make_llm_retry_strategy

        # Create a transient error using openai.APIConnectionError
        error = openai.APIConnectionError(message="Connection failed", request=MagicMock())

        strategy = make_llm_retry_strategy(transient_max=3, timeout_max=2)

        state = self._make_mock_retry_state(error, attempt_number=1)
        assert strategy(state) is True   # count=1, 1<3
        state = self._make_mock_retry_state(error, attempt_number=2)
        assert strategy(state) is True   # count=2, 2<3
        state = self._make_mock_retry_state(error, attempt_number=3)
        assert strategy(state) is False  # count=3, 3<3=False - exhausted
        state = self._make_mock_retry_state(error, attempt_number=4)
        assert strategy(state) is False  # count=4, 4<3=False - still exhausted

    def test_timeout_errors_limited_to_timeout_max(self):
        """Timeout errors should be retried up to timeout_max times."""
        from daemon.llm_error_classifier import make_llm_retry_strategy

        strategy = make_llm_retry_strategy(transient_max=3, timeout_max=2)

        error = openai.APITimeoutError(request=MagicMock())
        state = self._make_mock_retry_state(error, attempt_number=1)
        assert strategy(state) is True   # timeout count=1, 1<2
        state = self._make_mock_retry_state(error, attempt_number=2)
        assert strategy(state) is False  # timeout count=2, 2<2=False - exhausted
        state = self._make_mock_retry_state(error, attempt_number=3)
        assert strategy(state) is False  # count=3, 3<2=False - still exhausted

    def test_api_timeout_error_counted_as_timeout_not_transient(self):
        """APITimeoutError inherits from APIConnectionError (in TRANSIENT_EXCEPTIONS).
        It must be counted as a timeout error, not transient."""
        from daemon.llm_error_classifier import make_llm_retry_strategy

        # Allow many transient retries but only 1 timeout retry
        strategy = make_llm_retry_strategy(transient_max=10, timeout_max=1)

        error = openai.APITimeoutError(request=MagicMock())
        state = self._make_mock_retry_state(error, attempt_number=2)

        # APITimeoutError is checked against TIMEOUT_EXCEPTIONS first (not reset on attempt_number=2)
        assert strategy(state) is False  # 1st timeout: count=1, 1<1=False - exhausted

    def test_mixed_errors_tracked_independently(self):
        """Transient and timeout errors should not interfere with each other.
        
        With transient_max=2 and timeout_max=2:
        - Transient: 2 retry attempts allowed
        - Timeout: 2 retry attempts allowed
        - They are tracked in separate counters.
        """
        from daemon.llm_error_classifier import make_llm_retry_strategy

        strategy = make_llm_retry_strategy(transient_max=2, timeout_max=2)

        # 1st transient error: count=1, returns True (1 < 2)
        t_error = openai.APIConnectionError(message="Connection failed", request=MagicMock())
        t_state1 = self._make_mock_retry_state(t_error, attempt_number=1)
        assert strategy(t_state1) is True

        # 1st timeout error: count=1, returns True (1 < 2); attempt_number=2 avoids reset
        to_error = openai.APITimeoutError(request=MagicMock())
        to_state = self._make_mock_retry_state(to_error, attempt_number=2)
        assert strategy(to_state) is True

        # 2nd transient error: count=2, returns False (2 < 2 is False) - exhausted
        t_state2 = self._make_mock_retry_state(t_error, attempt_number=2)
        assert strategy(t_state2) is False

        # 2nd timeout error: count=2, returns False (2 < 2 is False) - exhausted
        to_state2 = self._make_mock_retry_state(to_error, attempt_number=3)
        assert strategy(to_state2) is False

        # 3rd transient error: count=3, returns False (3 < 2 is False)
        t_state3 = self._make_mock_retry_state(t_error, attempt_number=3)
        assert strategy(t_state3) is False

        # 3rd timeout error: count=3, returns False (3 < 2 is False)
        to_state3 = self._make_mock_retry_state(to_error, attempt_number=4)
        assert strategy(to_state3) is False

    def test_non_retryable_error_returns_false(self):
        """Non-retryable errors (401, 403, 400) should never retry."""
        from daemon.llm_error_classifier import make_llm_retry_strategy

        strategy = make_llm_retry_strategy(transient_max=10, timeout_max=10)

        # 401 AuthenticationError
        error = openai.AuthenticationError(
            message="Invalid API key",
            response=MagicMock(),
            body=None,
        )
        state = self._make_mock_retry_state(error)
        assert strategy(state) is False

    def test_no_exception_returns_false(self):
        """If there's no exception, should not retry."""
        from daemon.llm_error_classifier import make_llm_retry_strategy

        strategy = make_llm_retry_strategy(transient_max=3, timeout_max=3)

        state = self._make_mock_retry_state(None)
        assert strategy(state) is False

    def test_counters_reset_between_invoke_cycles(self):
        """Verify retry counters reset between invoke cycles."""
        from daemon.llm_error_classifier import make_llm_retry_strategy

        strategy = make_llm_retry_strategy(transient_max=3, timeout_max=2)

        # Cycle 1: exhaust timeout retries
        error = openai.APITimeoutError(request=MagicMock())
        state = self._make_mock_retry_state(error, attempt_number=1)
        assert strategy(state) is True   # timeout count=1, 1<2 → True
        state = self._make_mock_retry_state(error, attempt_number=2)
        assert strategy(state) is False  # timeout count=2, 2<2 → False (exhausted)

        # Cycle 2: attempt_number resets to 1 → counters should reset
        state = self._make_mock_retry_state(error, attempt_number=1)
        assert strategy(state) is True   # timeout count=1 (reset!)
        state = self._make_mock_retry_state(error, attempt_number=2)
        assert strategy(state) is False  # timeout count=2, exhausted again


class TestIndexErrorHandler:
    """Tests for IndexError handling — empty/malformed choices[] from LLM.

    LangChain's BaseChatModel._generate() (chat_models.py:402) indexes
    choices[0] unconditionally. When the LLM proxy returns a structurally
    malformed response (e.g. choices: []), LangChain raises IndexError
    ("list index out of range") out of .invoke(). The classifier must:

    - log the error clearly at ERROR level so production diagnostics can
      distinguish a malformed-LLM-response crash from a generic bug
    - re-raise the IndexError unchanged so the upstream error pipeline
      (instance_messaging / task_processor) can finalize the instance
    - NOT retry — retrying likely hits the same malformed payload
    """

    def _create_mock_llm_raising(self, exc):
        """Helper: LLM whose .invoke() raises `exc`."""
        mock_llm = MagicMock()
        mock_llm.invoke.side_effect = exc
        return mock_llm

    def test_index_error_propagates_unchanged(self):
        """IndexError raised by the LLM must be re-raised as IndexError,
        not wrapped or converted to a different type."""
        original = IndexError("list index out of range")

        mock_llm = self._create_mock_llm_raising(original)
        classified = classify_llm_errors(mock_llm)

        with pytest.raises(IndexError) as exc_info:
            classified.invoke([])

        # Must be exactly the same IndexError instance (not a wrapper).
        assert exc_info.value is original
        assert isinstance(exc_info.value, IndexError)
        assert "list index out of range" in str(exc_info.value)

    def test_index_error_message_preserved_on_re_raise(self):
        """The original IndexError message must survive re-raise so callers
        (and the upstream pipeline) can see 'list index out of range'."""
        original = IndexError("list index out of range")

        mock_llm = self._create_mock_llm_raising(original)
        classified = classify_llm_errors(mock_llm)

        with pytest.raises(IndexError) as exc_info:
            classified.invoke([])

        assert str(exc_info.value) == "list index out of range"

    def test_index_error_logs_at_error_level(self, caplog):
        """The handler must log the IndexError at ERROR level with the
        'Malformed LLM response' / IndexError tag so production
        log-scrapers can identify this crash signature.

        Wording note (review round 2, suggestion 3): the message is
        deliberately condition-neutral — the classifier itself never
        retries, it only classifies. Whether a retry happens is decided
        by the retry predicate (IndexError is retryable-with-failover
        only when a backup is configured), so the log must NOT claim
        "will not retry"."""
        original = IndexError("list index out of range")

        mock_llm = self._create_mock_llm_raising(original)
        classified = classify_llm_errors(mock_llm)

        with caplog.at_level("ERROR", logger="daemon.llm_error_classifier"):
            with pytest.raises(IndexError):
                classified.invoke([])

        # Find the error log record tagged with the malformed-response marker.
        error_records = [
            r for r in caplog.records
            if r.levelname == "ERROR"
            and "Malformed LLM response" in r.getMessage()
            and "IndexError" in r.getMessage()
        ]
        assert error_records, (
            f"Expected an ERROR-level 'Malformed LLM response' log entry; "
            f"got: {[r.getMessage() for r in caplog.records]}"
        )
        # The log must NOT claim retry intent in either direction —
        # retryability is the predicate's decision, not the classifier's.
        assert not any("will not retry" in r.getMessage() for r in error_records)

    def test_index_error_does_not_pollute_validation(self):
        """IndexError must short-circuit before validate_llm_response() runs.
        A malformed response should never reach downstream validation."""
        mock_llm = MagicMock()
        mock_llm.invoke.side_effect = IndexError("list index out of range")

        with patch(
            "daemon.llm_error_classifier.validate_llm_response"
        ) as mock_validate:
            classified = classify_llm_errors(mock_llm)

            with pytest.raises(IndexError):
                classified.invoke([])

            mock_validate.assert_not_called()

    def test_index_error_not_in_transient_exceptions(self):
        """IndexError is treated as NON-retryable. It must NOT appear in
        TRANSIENT_EXCEPTIONS — otherwise tenacity's with_retry would loop
        on the same malformed payload."""
        assert IndexError not in TRANSIENT_EXCEPTIONS

    def test_retry_strategy_skips_index_error(self):
        """make_llm_retry_strategy must return False for IndexError — the
        malformed-response error class must not be retried."""
        from daemon.llm_error_classifier import make_llm_retry_strategy

        strategy = make_llm_retry_strategy(transient_max=5, timeout_max=5)
        retry_state = MagicMock()
        retry_state.outcome.exception.return_value = IndexError("list index out of range")
        retry_state.attempt_number = 1

        assert strategy(retry_state) is False

    def test_empty_choices_indexerror_simulation(self):
        """Simulate the production incident exactly: LangChain's chat_models
        raises 'list index out of range' when choices is empty. The
        classifier must propagate and log."""
        # Build the exact IndexError shape that langchain_core raises.
        langchain_index_error = IndexError("list index out of range")

        mock_llm = MagicMock()
        mock_llm.invoke.side_effect = langchain_index_error

        with patch(
            "daemon.llm_error_classifier.validate_llm_response"
        ):
            classified = classify_llm_errors(mock_llm)

            # The classify_llm_errors Runnable must NOT swallow the error.
            with pytest.raises(IndexError) as exc_info:
                classified.invoke([])

        # The exact exception class and message must propagate.
        assert exc_info.type is IndexError
        assert "list index out of range" in str(exc_info.value)


@pytest.fixture(autouse=False)
def restore_default_patterns():
    """Reset the transient-channel AND usage-limit pattern state around
    each test that overrides it (isolation — pattern config is
    module-global)."""
    yield
    reset_transient_channel_patterns()
    reset_usage_limit_patterns()


def _bare_api_error(message: str) -> openai.APIError:
    """Construct a bare openai.APIError (no status code channel)."""
    return openai.APIError(message, request=httpx.Request("POST", "http://t/v1"), body=None)


class TestTransientLLMError:
    """Tests for the TransientLLMError wrapper (plan work unit 1)."""

    def test_creation_stores_kind_and_original(self):
        """TransientLLMError stores kind and the original exception."""
        original = _bare_api_error("All models rate limited")

        error = TransientLLMError("api_error_body", original)

        assert error.kind == "api_error_body"
        assert error.original is original
        assert "api_error_body" in str(error)
        assert "All models rate limited" in str(error)

    def test_is_transient_exceptions_member(self):
        """Membership in TRANSIENT_EXCEPTIONS is the L1/L2 lever — the
        predicate counts members as transient (except kind='timeout_body',
        routed to the timeout budget by RetryByCategory)."""
        assert TransientLLMError in TRANSIENT_EXCEPTIONS

    def test_not_subclass_of_transient_api_error(self):
        """Must NOT subclass TransientAPIError (its ctor requires an
        APIStatusError and .status_code)."""
        assert not issubclass(TransientLLMError, TransientAPIError)

    def test_remote_protocol_error_retryability_is_config_gated(self):
        """C3: httpx.RemoteProtocolError is NOT an unconditional
        TRANSIENT_EXCEPTIONS member — retryability is gated on
        ``remote_protocol_retryable`` (default on), giving operators a
        config kill-switch without a redeploy (same pattern as
        IndexError-on-backup)."""
        assert httpx.RemoteProtocolError not in TRANSIENT_EXCEPTIONS

    def test_broader_httpx_parents_not_added(self):
        """Over-broad parents (ProtocolError / TransportError) must NOT be
        members — a broken-endpoint loop would burn the full budget."""
        assert httpx.ProtocolError not in TRANSIENT_EXCEPTIONS
        assert httpx.TransportError not in TRANSIENT_EXCEPTIONS


class TestTransientChannelClassification:
    """C1–C4 corpus channels through classify_llm_errors (plan units 2–4).

    Corpus: docs/bugs/transient-llm-failures-non-retryable-instance-death.md
    (44 transient-with-zero-retries instance deaths, 2026-08-19→26).
    """

    def _classified_llm(self, exc):
        mock_llm = MagicMock()
        mock_llm.invoke.side_effect = exc
        return classify_llm_errors(mock_llm)

    # --- Channel tests (C1–C4): must wrap as TransientLLMError ---

    def test_c1_all_models_rate_limited_wrapped_transient(self):
        """C1 (21 events): relayed rate-limit body → TransientLLMError,
        transient category."""
        original = _bare_api_error("All models rate limited")

        with pytest.raises(TransientLLMError) as exc_info:
            self._classified_llm(original).invoke([])

        assert exc_info.value.kind == "api_error_body"
        assert exc_info.value.original is original

    def test_c1_relayed_timeout_routes_to_timeout_kind(self):
        """C1 (2 events): relayed 'context deadline exceeded' → wrapped with
        kind='timeout_body' so the predicate budgets it as a timeout."""
        original = _bare_api_error(
            "context deadline exceeded (Client.Timeout exceeded while awaiting headers)"
        )

        with pytest.raises(TransientLLMError) as exc_info:
            self._classified_llm(original).invoke([])

        assert exc_info.value.kind == "timeout_body"

    def test_c2_ultimate_model_retry_exhausted_valueerror_wrapped(self):
        """C2 (8 events): 200-body proxy dict parsed as ValueError →
        wrapped transient."""
        original = ValueError(
            "{'code': 'exhausted', 'detail': 'no model succeeded', "
            "'type': 'ultimate_model_retry_exhausted'}"
        )

        with pytest.raises(TransientLLMError) as exc_info:
            self._classified_llm(original).invoke([])

        assert exc_info.value.kind == "value_error_body"
        assert exc_info.value.original is original

    def test_c4_no_generations_found_wrapped(self):
        """C4 (4 events): zero-chunk SSE stream → ValueError wrapped."""
        with pytest.raises(TransientLLMError) as exc_info:
            self._classified_llm(ValueError("No generations found in stream.")).invoke([])

        assert exc_info.value.kind == "value_error_body"

    def test_c3_remote_protocol_error_re_raised_for_predicate(self):
        """C3 (7 events): RemoteProtocolError re-raised unchanged — a
        TRANSIENT_EXCEPTIONS member, so the predicate retries it."""
        original = httpx.RemoteProtocolError(
            "peer closed connection without sending complete message body "
            "(incomplete chunked read)"
        )

        with pytest.raises(httpx.RemoteProtocolError) as exc_info:
            self._classified_llm(original).invoke([])

        assert exc_info.value is original

    def test_pattern_match_is_case_insensitive(self):
        """Allowlist matching is case-insensitive substring."""
        with pytest.raises(TransientLLMError):
            self._classified_llm(_bare_api_error("ALL MODELS RATE LIMITED")).invoke([])

    # --- Regression tests: must stay NON-retryable ---

    def test_2056_token_plan_typed_usage_limit(self):
        """2056 quota shape: typed UsageLimitError at attempt 1
        (usage-limit-deferral-path W1). Still TERMINAL — never wrapped
        transient, never fast-retried; the blocklist's mandatory-
        precedence intent is preserved via the typing."""
        original = _bare_api_error(
            "Token Plan usage limit reached for model group (2056)"
        )

        with pytest.raises(UsageLimitError) as exc_info:
            self._classified_llm(original).invoke([])

        assert exc_info.value.original is original
        assert not isinstance(exc_info.value, TransientLLMError)

    def test_2013_invalid_params_terminal(self):
        """2013 bad-params shape: no allowlist hit → non-retryable,
        unchanged."""
        original = _bare_api_error(
            "invalid params, tool call result does not follow tool call (2013)"
        )

        with pytest.raises(openai.APIError) as exc_info:
            self._classified_llm(original).invoke([])

        assert exc_info.value is original

    def test_non_pattern_bare_api_error_terminal(self):
        """Any other bare APIError message → non-retryable, unchanged."""
        original = _bare_api_error("something novel and terminal")

        with pytest.raises(openai.APIError) as exc_info:
            self._classified_llm(original).invoke([])

        assert exc_info.value is original

    def test_generic_value_error_stays_terminal(self):
        """Genuine data-bug ValueError must NOT become retryable."""
        original = ValueError("genuine data bug")

        with pytest.raises(ValueError) as exc_info:
            self._classified_llm(original).invoke([])

        assert exc_info.value is original
        assert not isinstance(exc_info.value, TransientLLMError)

    def test_attribute_error_stays_terminal(self):
        """AttributeError (generic bug shape) — unchanged, non-retryable."""
        with pytest.raises(AttributeError):
            self._classified_llm(AttributeError("'str' object has no attribute 'model_dump'")).invoke([])

    def test_context_length_exceeded_not_shadowed(self):
        """BadRequestError context-length must still classify as
        ContextLengthExceededError — the new APIError branch must not
        shadow earlier subclass handlers."""
        mock_response = MagicMock()
        mock_response.status_code = 400
        original = openai.BadRequestError(
            "Error: context_length_exceeded",
            response=mock_response,
            body=None,
        )

        with pytest.raises(ContextLengthExceededError):
            self._classified_llm(original).invoke([])

    # --- Blocklist precedence (plan test 3) ---

    def test_blocklist_overrides_allowlist(self):
        """Synthetic message matching BOTH lists → terminal, now typed
        UsageLimitError (usage-limit typing runs BEFORE the
        allowlist/blocklist flow; still never wrapped transient)."""
        both = _bare_api_error("all models rate limited because usage limit exceeded")

        with pytest.raises(UsageLimitError) as exc_info:
            self._classified_llm(both).invoke([])

        assert not isinstance(exc_info.value, TransientLLMError)

    def test_blocklist_also_guards_valueerror_channel(self):
        """Blocklist precedence extends to the ValueError channel: a
        200-body proxy dict embedding quota wording alongside an
        allowlisted substring stays terminal — typed UsageLimitError
        (usage-limit typing before the transient pattern match)."""
        poisoned = ValueError(
            "{'detail': 'usage limit exceeded; ultimate_model_retry_exhausted', "
            "'type': 'ultimate_model_retry_exhausted'}"
        )

        with pytest.raises(UsageLimitError) as exc_info:
            self._classified_llm(poisoned).invoke([])

        assert exc_info.value.original is poisoned
        assert not isinstance(exc_info.value, TransientLLMError)

    # --- Ordering (plan test 4): subclass handlers keep precedence ---

    @pytest.mark.parametrize("make_exc", [
        # (label, factory) — each subclass must hit its OWN branch, not
        # the bare-APIError pattern branch.
        lambda: openai.APITimeoutError(request=MagicMock()),
        lambda: openai.APIConnectionError(message="Connection failed", request=MagicMock()),
    ])
    def test_subclass_handlers_precede_bare_apierror_branch(self, make_exc):
        """APITimeoutError / APIConnectionError must pass through
        unchanged (their own handlers, timeout/transient semantics)."""
        original = make_exc()

        with pytest.raises(type(original)) as exc_info:
            self._classified_llm(original).invoke([])

        assert exc_info.value is original
        assert not isinstance(exc_info.value, TransientLLMError)

    def test_api_response_validation_error_not_shadowed(self):
        """APIResponseValidationError (direct APIError subclass — MRO
        verified) must hit its own retryable handler, NOT the bare-APIError
        branch (plan review §2.1 — placement is load-bearing)."""
        request = httpx.Request("POST", "http://t/v1")
        response = httpx.Response(502, text="<html>Bad Gateway</html>", request=request)
        original = openai.APIResponseValidationError(
            response=response, body=None, message="Failed to parse response"
        )

        with pytest.raises(openai.APIResponseValidationError) as exc_info:
            self._classified_llm(original).invoke([])

        assert exc_info.value is original
        assert not isinstance(exc_info.value, TransientLLMError)

    def test_429_status_error_still_transient_api_error(self):
        """Status-channel 429 keeps its TransientAPIError wrapper — the
        new branches change nothing for the status path."""
        mock_response = MagicMock()
        mock_response.status_code = 429
        original = openai.APIStatusError("Rate limit", response=mock_response, body=None)

        with pytest.raises(TransientAPIError) as exc_info:
            self._classified_llm(original).invoke([])

        assert exc_info.value.status_code == 429

    # --- Log-anchor change (plan review §3.2) ---

    def test_non_matching_bare_apierror_log_anchor(self, caplog):
        """Non-matching bare APIError logs '[LLM] Non-retryable API error'
        (the new evidence anchor — bug-doc extraction must grep both
        this and the legacy 'Unexpected error (will not retry)'})."""
        with caplog.at_level("ERROR", logger="daemon.llm_error_classifier"):
            with pytest.raises(openai.APIError):
                self._classified_llm(_bare_api_error("novel terminal")).invoke([])

        assert any(
            "[LLM] Non-retryable API error" in r.getMessage()
            for r in caplog.records
        )


class TestTransientChannelPredicate:
    """RetryByCategory budget routing for TransientLLMError kinds
    (plan unit 2a / tests 1 and 4a)."""

    def _make_mock_retry_state(self, exception, attempt_number=1):
        from tenacity import RetryCallState

        outcome = MagicMock()
        outcome.exception.return_value = exception
        retry_state = MagicMock(spec=RetryCallState)
        retry_state.outcome = outcome
        retry_state.attempt_number = attempt_number
        return retry_state

    def test_timeout_body_consumes_timeout_budget(self):
        """kind='timeout_body' consumes the 3-attempt timeout budget, not
        the 10-attempt transient budget (wall-clock amplification guard)."""
        strategy = make_llm_retry_strategy(transient_max=10, timeout_max=3)
        error = TransientLLMError("timeout_body", Exception("context deadline exceeded"))

        results = [strategy(self._make_mock_retry_state(error, n)) for n in range(1, 5)]
        # timeout_max=3 → True while count < 3, False from count 3 on
        assert results == [True, True, False, False]

    def test_api_error_body_consumes_transient_budget(self):
        """kind='api_error_body' consumes the transient budget."""
        strategy = make_llm_retry_strategy(transient_max=3, timeout_max=1)
        error = TransientLLMError("api_error_body", Exception("rate limited"))

        results = [strategy(self._make_mock_retry_state(error, n)) for n in range(1, 5)]
        assert results == [True, True, False, False]

    def test_value_error_body_consumes_transient_budget(self):
        """kind='value_error_body' consumes the transient budget."""
        strategy = make_llm_retry_strategy(transient_max=2, timeout_max=1)
        error = TransientLLMError("value_error_body", ValueError("no generations"))

        results = [strategy(self._make_mock_retry_state(error, n)) for n in range(1, 4)]
        assert results == [True, False, False]

    def test_remote_protocol_error_counts_transient(self):
        """C3 gate (default on): RemoteProtocolError increments the
        transient counter (drives L2 failover slice)."""
        strategy = make_llm_retry_strategy(transient_max=2, timeout_max=5)
        error = httpx.RemoteProtocolError("incomplete chunked read")

        # transient_max=2 → True while count < 2, False from count 2 on
        assert strategy(self._make_mock_retry_state(error, 1)) is True
        assert strategy(self._make_mock_retry_state(error, 2)) is False
        assert strategy(self._make_mock_retry_state(error, 3)) is False

    def test_remote_protocol_error_kill_switch(
        self, restore_default_patterns
    ):
        """C3 gate (off): with remote_protocol_retryable=False the same
        exception is non-retryable — the config kill-switch."""
        configure_transient_channel_patterns(remote_protocol_retryable=False)
        strategy = make_llm_retry_strategy(transient_max=5, timeout_max=5)
        error = httpx.RemoteProtocolError("incomplete chunked read")

        assert strategy(self._make_mock_retry_state(error, 1)) is False

    def test_timeout_kind_drives_failover_swap(self):
        """timeout_body attempts drive the timeout primary-slice cap —
        with a configured backup, reaching PRIMARY_TIMEOUT_MAX swaps."""
        from daemon.llm_error_classifier import PRIMARY_TIMEOUT_MAX, FailoverController

        controller = MagicMock(spec=FailoverController)
        controller.is_configured = True
        strategy = make_llm_retry_strategy(
            transient_max=10, timeout_max=3, failover_controller=controller
        )
        error = TransientLLMError("timeout_body", Exception("context deadline exceeded"))

        # Exhaust the primary timeout slice → swap fires
        for n in range(1, PRIMARY_TIMEOUT_MAX + 1):
            assert strategy(self._make_mock_retry_state(error, n)) is True
        controller.swap_to_backup.assert_called_once()
        controller.reset_to_primary.assert_called_once()  # attempt-1 reset


class TestTransientChannelEndToEnd:
    """Spirit check (plan test 7): the Aug-26 06:51 storm shape exhausts
    the full transient budget under Retrying instead of dying on
    attempt 1."""

    def test_storm_shape_yields_full_transient_budget(self):
        """A fake invoke raising bare APIError('All models rate limited')
        runs 10 attempts (1 + 9 retries) under Retrying, not 1."""
        from tenacity import Retrying, wait_fixed

        calls = {"n": 0}

        def _storm(*args, **kwargs):
            calls["n"] += 1
            raise _bare_api_error("All models rate limited")

        mock_llm = MagicMock()
        mock_llm.invoke.side_effect = _storm
        classified = classify_llm_errors(mock_llm)

        retrying = Retrying(
            stop=stop_after_attempt_10(),
            wait=wait_fixed(0),
            retry=make_llm_retry_strategy(transient_max=10, timeout_max=3),
            reraise=True,
        )
        with pytest.raises(TransientLLMError):
            retrying(classified.invoke, [])

        assert calls["n"] == 10

    def test_disabled_allowlist_kills_after_attempt_1(self, restore_default_patterns):
        """The additive-off switch: an empty allowlist disables the branch —
        the same storm shape dies on attempt 1 again (pre-plan behavior)."""
        from tenacity import Retrying, wait_fixed

        configure_transient_channel_patterns(apierror_allowlist=[])
        calls = {"n": 0}

        def _storm(*args, **kwargs):
            calls["n"] += 1
            raise _bare_api_error("All models rate limited")

        mock_llm = MagicMock()
        mock_llm.invoke.side_effect = _storm
        classified = classify_llm_errors(mock_llm)

        retrying = Retrying(
            stop=stop_after_attempt_10(),
            wait=wait_fixed(0),
            retry=make_llm_retry_strategy(transient_max=10, timeout_max=3),
            reraise=True,
        )
        with pytest.raises(openai.APIError):
            retrying(classified.invoke, [])

        assert calls["n"] == 1


def stop_after_attempt_10():
    from tenacity import stop_after_attempt

    return stop_after_attempt(10)


class TestTransientChannelConfig:
    """Pattern configuration (plan work unit 7 / test 6)."""

    def test_configure_overrides_and_reset_restores(self, restore_default_patterns):
        from daemon.llm_error_classifier import _matches_transient_apierror

        configure_transient_channel_patterns(
            apierror_allowlist=["custom outage"],
            valueerror_patterns=["custom body"],
        )
        assert _matches_transient_apierror("Custom OUTAGE happened")
        assert not _matches_transient_apierror("All models rate limited")

        reset_transient_channel_patterns()
        assert _matches_transient_apierror("All models rate limited")
        assert not _matches_transient_apierror("custom outage happened")

    def test_empty_allowlist_disables_branch(self, restore_default_patterns):
        from daemon.llm_error_classifier import _matches_transient_apierror

        configure_transient_channel_patterns(apierror_allowlist=[])
        assert not _matches_transient_apierror("all models rate limited")

    def test_queue_config_csv_and_json_list_forms(self):
        """QueueConfig accepts CSV / JSON-array strings and YAML lists."""
        from daemon.config import QueueConfig

        cfg = QueueConfig(
            transient_apierror_allowlist="all models rate limited, context deadline exceeded",
            transient_apierror_timeout_patterns='["context deadline exceeded"]',
            transient_apierror_blocklist=["token plan", " usage limit "],
            transient_valueerror_patterns="",
        )
        assert cfg.transient_apierror_allowlist == ["all models rate limited", "context deadline exceeded"]
        assert cfg.transient_apierror_timeout_patterns == ["context deadline exceeded"]
        assert cfg.transient_apierror_blocklist == ["token plan", "usage limit"]
        assert cfg.transient_valueerror_patterns == []

    def test_queue_config_defaults_match_corpus(self):
        """Defaults ship with the corpus patterns (config-removable)."""
        from daemon.config import QueueConfig

        cfg = QueueConfig()
        assert "all models rate limited" in cfg.transient_apierror_allowlist
        assert "context deadline exceeded" in cfg.transient_apierror_timeout_patterns
        assert "token plan" in cfg.transient_apierror_blocklist
        assert "ultimate_model_retry_exhausted" in cfg.transient_valueerror_patterns

    def test_load_config_pushes_patterns_into_classifier(self, restore_default_patterns, tmp_path):
        """load_config wires the yaml lists into the classifier module."""
        from daemon.config import load_config
        import daemon.llm_error_classifier as lec

        config_file = tmp_path / "config.yaml"
        config_file.write_text(
            "llm:\n  base_url: http://t/v1\n"
            "queue:\n"
            "  transient_apierror_allowlist: ['custom yaml pattern']\n"
            "  transient_apierror_timeout_patterns: []\n"
            "  transient_valueerror_patterns: []\n"
            "  transient_remote_protocol_retryable: false\n"
        )
        load_config(str(config_file))

        assert lec._transient_patterns.apierror_allowlist == ("custom yaml pattern",)
        assert lec._transient_patterns.valueerror_patterns == ()
        assert lec._transient_patterns.remote_protocol_retryable is False

    def test_queue_config_rejects_timeout_pattern_missing_from_allowlist(self):
        """A timeout pattern not in the allowlist would silently consume
        the 10-attempt transient budget at up to 660s per attempt — the
        config load must fail instead."""
        from daemon.config import QueueConfig

        with pytest.raises(ValueError, match="subset"):
            QueueConfig(
                transient_apierror_allowlist=["all models rate limited"],
                transient_apierror_timeout_patterns=["context deadline exceeded"],
            )

    def test_queue_config_defaults_derive_from_classifier_bundle(self):
        """Single-sourcing: QueueConfig defaults ARE the classifier's
        canonical corpus bundle — no second copy to drift."""
        from daemon.config import QueueConfig
        from daemon.llm_error_classifier import DEFAULT_TRANSIENT_CHANNEL_PATTERNS

        cfg = QueueConfig()
        assert cfg.transient_apierror_allowlist == list(
            DEFAULT_TRANSIENT_CHANNEL_PATTERNS.apierror_allowlist
        )
        assert cfg.transient_valueerror_patterns == list(
            DEFAULT_TRANSIENT_CHANNEL_PATTERNS.valueerror_patterns
        )
        assert cfg.transient_remote_protocol_retryable == (
            DEFAULT_TRANSIENT_CHANNEL_PATTERNS.remote_protocol_retryable
        )


class TestUsageLimitClassification:
    """Quota-window typing through classify_llm_errors
    (docs/plans/usage-limit-deferral-path.md W1 — typing, ordering,
    disable switch, disjointness)."""

    def _classified_llm(self, exc):
        mock_llm = MagicMock()
        mock_llm.invoke.side_effect = exc
        return classify_llm_errors(mock_llm)

    def test_2056_typed_at_attempt_1_no_fast_retries(self):
        """Typing: corpus 2056 shape raises UsageLimitError on the very
        first classification — no tenacity fast-retry membership."""
        original = _bare_api_error(
            "Token Plan usage limit reached for model group (2056)"
        )

        with pytest.raises(UsageLimitError) as exc_info:
            self._classified_llm(original).invoke([])

        assert exc_info.value.original is original
        assert "usage limit" in str(exc_info.value).lower()

    def test_valueerror_channel_quota_text_typed(self):
        """Quota text riding a 200-body ValueError (the cc753c2f §review
        guard shape) types identically to the bare-APIError channel."""
        original = ValueError(
            "{'code': 429, 'detail': 'Token Plan usage limit reached'}"
        )

        with pytest.raises(UsageLimitError) as exc_info:
            self._classified_llm(original).invoke([])

        assert exc_info.value.original is original

    def test_typing_precedes_transient_allowlist(self):
        """Ordering: a message matching BOTH the transient allowlist and
        the usage-limit patterns types UsageLimitError — never wrapped
        transient (the wrap sites check usage-limit FIRST)."""
        original = _bare_api_error(
            "All models rate limited due to Token Plan usage limit"
        )

        with pytest.raises(UsageLimitError) as exc_info:
            self._classified_llm(original).invoke([])

        assert not isinstance(exc_info.value, TransientLLMError)

    def test_invalid_params_2013_stays_untyped_terminal(self):
        """Disjointness regression: the bad-params shape (corpus 2013)
        is an untyped terminal re-raise — a genuine bug must never
        enter the 6h auto-retry episode."""
        original = _bare_api_error(
            "invalid params, tool call result does not follow tool call (2013)"
        )

        with pytest.raises(openai.APIError) as exc_info:
            self._classified_llm(original).invoke([])

        assert exc_info.value is original
        assert not isinstance(exc_info.value, UsageLimitError)
        assert not isinstance(exc_info.value, TransientLLMError)

    def test_not_transient_or_timeout_member(self):
        """UsageLimitError is in NEITHER retry set — the predicate never
        retries it (terminal at L1 by design)."""
        assert UsageLimitError not in TRANSIENT_EXCEPTIONS
        assert UsageLimitError not in TIMEOUT_EXCEPTIONS

    def test_empty_pattern_list_disables_typed_wrapper(self, restore_default_patterns):
        """Additive-off switch: an explicitly-empty usage_limit_patterns
        reverts quota shapes to the untyped terminal blocklist
        re-raise."""
        configure_usage_limit_patterns(patterns=[])
        original = _bare_api_error("Token Plan usage limit reached (2056)")

        with pytest.raises(openai.APIError) as exc_info:
            self._classified_llm(original).invoke([])

        assert exc_info.value is original
        assert not isinstance(exc_info.value, UsageLimitError)

    def test_configure_overrides_and_reset_restores(self, restore_default_patterns):
        from daemon.llm_error_classifier import _matches_usage_limit

        configure_usage_limit_patterns(patterns=["monthly quota burn"])
        assert _matches_usage_limit("Monthly Quota BURN hit")
        assert not _matches_usage_limit("Token Plan usage limit reached")

        reset_usage_limit_patterns()
        assert _matches_usage_limit("Token Plan usage limit reached")
        assert not _matches_usage_limit("monthly quota burn hit")

    def test_queue_config_usage_limit_patterns_csv_and_default(self):
        """QueueConfig accepts the same CSV/JSON/YAML list forms; the
        default derives from the classifier's canonical tuple."""
        from daemon.config import QueueConfig
        from daemon.llm_error_classifier import DEFAULT_USAGE_LIMIT_PATTERNS

        cfg = QueueConfig(usage_limit_patterns="token plan, usage limit")
        assert cfg.usage_limit_patterns == ["token plan", "usage limit"]

        default_cfg = QueueConfig()
        assert default_cfg.usage_limit_patterns == list(DEFAULT_USAGE_LIMIT_PATTERNS)

    def test_queue_config_rejects_bad_params_overlap(self):
        """A usage-limit pattern that substring-matches the corpus-2013
        bad-params shape would type a genuine bug into the 6h
        auto-retry episode — the config load must fail."""
        from daemon.config import QueueConfig

        with pytest.raises(ValueError, match="disjoint"):
            QueueConfig(usage_limit_patterns=["invalid params"])

    def test_load_config_pushes_usage_limit_patterns(self, restore_default_patterns, tmp_path):
        """load_config wires the yaml usage_limit_patterns into the
        classifier module (same convention as the transient bundle)."""
        from daemon.config import load_config
        import daemon.llm_error_classifier as lec

        config_file = tmp_path / "config.yaml"
        config_file.write_text(
            "llm:\n  base_url: http://t/v1\n"
            "queue:\n"
            "  usage_limit_patterns: ['custom quota window']\n"
        )
        load_config(str(config_file))

        assert lec._usage_limit_patterns == ("custom quota window",)

    def test_load_config_empty_patterns_disable_wrapper(self, restore_default_patterns, tmp_path):
        from daemon.config import load_config
        import daemon.llm_error_classifier as lec

        config_file = tmp_path / "config.yaml"
        config_file.write_text(
            "llm:\n  base_url: http://t/v1\n"
            "queue:\n"
            "  usage_limit_patterns: []\n"
        )
        load_config(str(config_file))

        assert lec._usage_limit_patterns == ()
