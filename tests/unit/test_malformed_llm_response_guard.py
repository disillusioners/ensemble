"""Tests for the MalformedLLMResponseError guard in ThinkingChatOpenAI.

Incident (2026-08-15, instance f10b7694): a provider under stress
returned a bare JSON string body instead of a ChatCompletion object.
The OpenAI SDK's construct_type() passthrough returned the ``str``
as-is, LangChain's BaseChatOpenAI._create_chat_result called
``.model_dump()`` on it → ``AttributeError: 'str' object has no
attribute 'model_dump'``, classified NON-retryable → instance died.

These tests verify the fix:
1. The type-guard in ``ThinkingChatOpenAI._create_chat_result`` raises
   ``MalformedLLMResponseError`` BEFORE ``super()._create_chat_result``
   for any non-dict / non-model_dump response shape.
2. Valid dict responses and real SDK objects (with ``model_dump``) pass
   through to super() unchanged.
3. ``MalformedLLMResponseError`` is RETRYABLE: member of
   TRANSIENT_EXCEPTIONS, retry predicate returns True, classifier logs
   the retryable pattern and re-raises unchanged.
4. Regression guard: a GENERIC ``AttributeError`` (any other one raised
   in the call chain) is still NON-retryable — the net was not widened.
5. Causal pin: the REAL LangChain ``BaseChatOpenAI._create_chat_result``,
   called directly with a malformed response and no guard, raises the
   exact ``AttributeError`` from the incident — proving the guard fires
   BEFORE ``super()`` and pre-empts precisely this failure.
"""

import pytest
from types import SimpleNamespace
from unittest.mock import MagicMock

import openai
from openai.types.chat import ChatCompletion

from daemon.graph import ThinkingChatOpenAI
from daemon.llm_error_classifier import (
    MalformedLLMResponseError,
    TRANSIENT_EXCEPTIONS,
    classify_llm_errors,
    make_llm_retry_strategy,
)


def _make_chat_completion() -> ChatCompletion:
    """Build a minimal real SDK ChatCompletion object."""
    return ChatCompletion.model_validate({
        "id": "chatcmpl-test",
        "object": "chat.completion",
        "created": 1,
        "model": "glm-5",
        "choices": [{
            "index": 0,
            "finish_reason": "stop",
            "message": {"role": "assistant", "content": "Hello world"},
        }],
    })


class TestMalformedLLMResponseError:
    """Tests for the MalformedLLMResponseError exception class."""

    def test_message_includes_offending_type(self):
        """The exception message must name the offending type."""
        error = MalformedLLMResponseError('{"choices": []}')

        assert "str" in str(error)
        assert "model_dump" in str(error)
        assert "expected dict or object with model_dump(), got str" == str(error)

    def test_stores_original_response(self):
        """The raw malformed response is kept for diagnostics."""
        raw = '{"unexpected": "shape"}'

        error = MalformedLLMResponseError(raw)

        assert error.response is raw

    def test_message_names_each_type(self):
        """Type name is interpolated, not hardcoded to 'str'."""
        assert "list" in str(MalformedLLMResponseError([]))
        assert "NoneType" in str(MalformedLLMResponseError(None))
        assert "int" in str(MalformedLLMResponseError(42))


class TestGuardRaisesOnMalformedResponse:
    """The type-guard in _create_chat_result must reject anything that is
    neither a dict nor an object exposing model_dump()."""

    def _make_llm(self) -> ThinkingChatOpenAI:
        return ThinkingChatOpenAI(model="test-model", api_key="test-key")

    @pytest.mark.parametrize(
        "malformed",
        [
            pytest.param('{"id": "chatcmpl-x"}', id="bare-json-str-body"),
            pytest.param([{"choices": []}], id="list"),
            pytest.param(None, id="none"),
            pytest.param(42, id="int"),
            pytest.param(True, id="bool"),
        ],
    )
    def test_guard_raises_before_super(self, malformed):
        """Malformed response shapes raise MalformedLLMResponseError
        BEFORE super()._create_chat_result can touch .model_dump()."""
        llm = self._make_llm()

        with pytest.raises(MalformedLLMResponseError) as exc_info:
            llm._create_chat_result(malformed)

        # The offending type must be named in the message.
        assert type(malformed).__name__ in str(exc_info.value)
        assert exc_info.value.response is malformed


class TestGuardPassesValidResponses:
    """Valid response shapes flow through to super() untouched."""

    def test_dict_response_passes_through(self):
        """A dict response reaches super() and returns a ChatResult."""
        llm = ThinkingChatOpenAI(model="glm-5", api_key="test-key")
        response = {
            "id": "chatcmpl-test",
            "object": "chat.completion",
            "created": 1,
            "model": "glm-5",
            "choices": [{
                "index": 0,
                "finish_reason": "stop",
                "message": {"role": "assistant", "content": "Hello"},
            }],
        }

        result = llm._create_chat_result(response)

        assert len(result.generations) == 1
        assert result.generations[0].message.content == "Hello"

    def test_object_with_model_dump_passes_through(self):
        """A real SDK ChatCompletion (has model_dump) passes the guard and
        is handled by the real LangChain super() path."""
        llm = ThinkingChatOpenAI(model="glm-5", api_key="test-key")
        response = _make_chat_completion()

        result = llm._create_chat_result(response)

        assert len(result.generations) == 1
        assert result.generations[0].message.content == "Hello world"

    def test_guard_does_not_disturb_reasoning_extraction(self):
        """The existing reasoning_content extraction below the guard keeps
        working for a valid dict response (regression for the override)."""
        llm = ThinkingChatOpenAI(model="glm-5", api_key="test-key")
        response = {
            "id": "chatcmpl-test",
            "object": "chat.completion",
            "created": 1,
            "model": "glm-5",
            "choices": [{
                "index": 0,
                "finish_reason": "stop",
                "message": {
                    "role": "assistant",
                    "content": "Hello",
                    "reasoning_content": "user wants a greeting",
                },
            }],
        }

        result = llm._create_chat_result(response)

        reasoning = result.generations[0].message.additional_kwargs.get(
            "reasoning_content"
        )
        assert reasoning == "user wants a greeting"


class TestMalformedResponseRetryableClassification:
    """MalformedLLMResponseError must be RETRYABLE end-to-end."""

    def test_in_transient_exceptions(self):
        """The exception is a member of TRANSIENT_EXCEPTIONS (the retry set)."""
        assert MalformedLLMResponseError in TRANSIENT_EXCEPTIONS

    def _create_mock_llm_raising(self, exc):
        """Helper: LLM whose .invoke() raises `exc`."""
        mock_llm = MagicMock()
        mock_llm.invoke.side_effect = exc
        return mock_llm

    def test_classifier_re_raises_unchanged(self):
        """The classifier's explicit handler re-raises the SAME instance
        (no wrapping), so the retry predicate sees the real type."""
        original = MalformedLLMResponseError("not a completion")

        classified = classify_llm_errors(self._create_mock_llm_raising(original))

        with pytest.raises(MalformedLLMResponseError) as exc_info:
            classified.invoke([])

        assert exc_info.value is original

    def test_classifier_logs_retryable_pattern(self, caplog):
        """The classifier logs the '[LLM] Malformed response (retryable)'
        pattern at WARNING level, mirroring sibling retryable handlers."""
        original = MalformedLLMResponseError('{"bad": "shape"}')

        classified = classify_llm_errors(self._create_mock_llm_raising(original))

        with caplog.at_level("WARNING", logger="daemon.llm_error_classifier"):
            with pytest.raises(MalformedLLMResponseError):
                classified.invoke([])

        matched = [
            r for r in caplog.records
            if r.levelname == "WARNING"
            and "Malformed response (retryable)" in r.getMessage()
        ]
        assert matched, (
            f"Expected a WARNING-level 'Malformed response (retryable)' log; "
            f"got: {[r.getMessage() for r in caplog.records]}"
        )

    def _make_mock_retry_state(self, exception, attempt_number=1):
        """Create a mock RetryCallState (pattern from TestRetryByCategory)."""
        from tenacity import RetryCallState

        outcome = MagicMock()
        outcome.exception.return_value = exception

        retry_state = MagicMock(spec=RetryCallState)
        retry_state.outcome = outcome
        retry_state.attempt_number = attempt_number

        return retry_state

    def test_retry_predicate_retries(self):
        """The tenacity predicate (RetryByCategory) returns True — the
        malformed-response error is actually retried."""
        error = MalformedLLMResponseError("not a completion")
        strategy = make_llm_retry_strategy(transient_max=3, timeout_max=2)

        state = self._make_mock_retry_state(error, attempt_number=1)
        assert strategy(state) is True  # count=1, 1<3 — retry
        state = self._make_mock_retry_state(error, attempt_number=2)
        assert strategy(state) is True  # count=2, 2<3 — retry
        state = self._make_mock_retry_state(error, attempt_number=3)
        assert strategy(state) is False  # count=3, 3<3 — exhausted

    def test_retry_predicate_without_failover(self):
        """Retryable even with NO failover controller configured — unlike
        IndexError, this malformed-body path is unconditionally retryable."""
        error = MalformedLLMResponseError(None)
        strategy = make_llm_retry_strategy(transient_max=8, timeout_max=3)

        state = self._make_mock_retry_state(error, attempt_number=1)
        assert strategy(state) is True


class TestGenericAttributeErrorStaysNonRetryable:
    """Regression guard: the retry net was NOT widened. A generic
    AttributeError (raised anywhere else in the call chain) must remain
    NON-retryable, exactly as before this change."""

    def test_not_in_transient_exceptions(self):
        """AttributeError is not a member of the retry set."""
        assert AttributeError not in TRANSIENT_EXCEPTIONS

    def test_model_dump_attribute_error_not_retried(self):
        """The exact incident signature — AttributeError about
        'model_dump' — is not itself retried; only the dedicated
        MalformedLLMResponseError is."""
        error = AttributeError("'str' object has no attribute 'model_dump'")
        strategy = make_llm_retry_strategy(transient_max=8, timeout_max=3)

        state = MagicMock()
        state.attempt_number = 1
        state.outcome.exception.return_value = error

        assert strategy(state) is False  # never retried

    def test_classifier_passes_generic_attribute_error_through(self, caplog):
        """A generic AttributeError hits the catch-all handler, logs
        'will not retry', and is re-raised unchanged (no wrapping)."""
        original = AttributeError("'str' object has no attribute 'model_dump'")

        mock_llm = MagicMock()
        mock_llm.invoke.side_effect = original
        classified = classify_llm_errors(mock_llm)

        with caplog.at_level("ERROR", logger="daemon.llm_error_classifier"):
            with pytest.raises(AttributeError) as exc_info:
                classified.invoke([])

        assert exc_info.value is original
        assert not isinstance(exc_info.value, MalformedLLMResponseError)

        matched = [
            r for r in caplog.records
            if r.levelname == "ERROR"
            and "will not retry" in r.getMessage()
            and "AttributeError" in r.getMessage()
        ]
        assert matched, (
            f"Expected an ERROR-level 'will not retry' log for the generic "
            f"AttributeError; got: {[r.getMessage() for r in caplog.records]}"
        )


class TestUnguardedSuperWouldRaiseAttributeError:
    """Causal pin: WITHOUT the guard, the REAL LangChain
    ``BaseChatOpenAI._create_chat_result`` raises the exact ``AttributeError``
    from the incident on every malformed shape.

    WHY this test exists: the guard in ``ThinkingChatOpenAI._create_chat_result``
    is justified as "pre-empting the ``AttributeError`` from super()". This
    class locks that causal story by calling the REAL upstream method
    directly (unbound, with a minimal dummy ``self`` — the method touches
    ``response.model_dump()`` on its first statement, before any ``self``
    attribute) and asserting it raises ``AttributeError`` naming
    ``model_dump`` and the offending type. If LangChain ever changes the
    upstream shape handling (e.g. starts coercing bare strings), this test
    failing is the signal that the guard may no longer be pre-empting a
    real failure — the incident story needs re-examination."""

    @pytest.mark.parametrize(
        "malformed",
        [
            pytest.param('{"id": "chatcmpl-x"}', id="bare-json-str-body"),
            pytest.param([{"choices": []}], id="list"),
            pytest.param(None, id="none"),
            pytest.param(42, id="int"),
            pytest.param(True, id="bool"),
        ],
    )
    def test_real_super_raises_attribute_error(self, malformed):
        """The unguarded upstream method raises the exact incident
        AttributeError: ``'<Type>' object has no attribute 'model_dump'``."""
        from langchain_openai.chat_models import base as lc_base

        # Minimal dummy self: the failure happens on the first statement
        # (``response.model_dump()``), before any self attribute is read.
        dummy_self = SimpleNamespace()

        with pytest.raises(AttributeError) as exc_info:
            lc_base.BaseChatOpenAI._create_chat_result(dummy_self, malformed)

        message = str(exc_info.value)
        assert "model_dump" in message
        assert type(malformed).__name__ in message
