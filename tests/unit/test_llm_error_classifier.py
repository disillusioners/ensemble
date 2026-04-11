"""Tests for LLM error classifier module."""

import pytest
from unittest.mock import MagicMock, Mock, patch
import openai

from daemon.llm_error_classifier import (
    RETRYABLE_STATUS_CODES,
    TRANSIENT_EXCEPTIONS,
    TIMEOUT_EXCEPTIONS,
    TransientAPIError,
    ContextLengthExceededError,
    classify_llm_errors,
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
        """RETRYABLE_STATUS_CODES should contain {429, 500, 502, 503, 504}."""
        assert RETRYABLE_STATUS_CODES == {429, 500, 502, 503, 504}

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
