"""Unit tests for daemon/response_validation.py."""

import pytest
from unittest.mock import MagicMock

from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.messages.tool import ToolCall

from daemon.response_validation import (
    LLMResponseValidationError,
    NUDGE_MESSAGE,
    EmptyLLMResponseError,
    empty_guard_disabled,
    install_empty_guard_config,
    is_empty_llm_content,
    _is_nudge_human,
    _is_server_injected_human,
    validate_llm_response,
)
from tests.unit.empty_guard_test_helpers import (
    _real_human,
    _restore_empty_guard_defaults,  # noqa: F401  (pytest fixture, import-collected)
    _tool_result,
)


# =============================================================================
# Helper Functions
# =============================================================================


def make_ai_message(**overrides) -> AIMessage:
    """Create an AIMessage with optional overrides."""
    defaults = {
        "content": "Test response content",
        "id": "test-msg-1",
    }
    defaults.update(overrides)
    return AIMessage(**defaults)


# =============================================================================
# Test LLMResponseValidationError
# =============================================================================


class TestLLMResponseValidationError:
    """Tests for the LLMResponseValidationError exception."""

    def test_exception_stores_message(self):
        """Test that exception stores the message."""
        error = LLMResponseValidationError("Test error message")
        assert str(error) == "Test error message"

    def test_exception_stores_response(self):
        """Test that exception stores the response object."""
        response = make_ai_message()
        error = LLMResponseValidationError("Test error", response=response)
        assert error.response is response

    def test_exception_response_can_be_none(self):
        """Test that exception response can be None."""
        error = LLMResponseValidationError("Test error", response=None)
        assert error.response is None


# =============================================================================
# Test Empty Content Validation
# =============================================================================


class TestEmptyContentValidation:
    """Tests for empty content validation.

    Empty content is valid — the graph's should_continue() handles routing
    to END when the model has nothing more to say. Validation does NOT reject
    empty responses.
    """

    def test_empty_string_content_passes(self):
        """Test that empty string content passes validation (not an error)."""
        response = make_ai_message(content="")
        # Should NOT raise — empty content is valid
        validate_llm_response(response)

    def test_whitespace_only_content_passes(self):
        """Test that whitespace-only content passes validation (not an error)."""
        response = make_ai_message(content="   \n\t  ")
        # Should NOT raise — empty content is valid
        validate_llm_response(response)

    def test_empty_content_with_tool_calls_passes(self):
        """Test that empty content WITH tool_calls is valid (does not raise)."""
        response = make_ai_message(
            content="",
            tool_calls=[ToolCall(id="call_1", name="test_tool", args={})],
        )
        # Should NOT raise
        validate_llm_response(response)

    def test_empty_string_content_with_tool_calls_passes(self):
        """Test that empty string content WITH tool_calls is valid (does not raise)."""
        response = make_ai_message(
            content="",
            tool_calls=[ToolCall(id="call_1", name="test_tool", args={"param": "value"})],
        )
        # Should NOT raise
        validate_llm_response(response)


# =============================================================================
# Test Truncation Validation
# =============================================================================


class TestTruncationValidation:
    """Tests for truncation validation (raises LLMResponseValidationError)."""

    def test_finish_reason_length_raises(self):
        """Test that finish_reason='length' raises validation error."""
        response = make_ai_message(
            content="Partial response...",
            response_metadata={"finish_reason": "length"},
        )
        with pytest.raises(LLMResponseValidationError) as exc_info:
            validate_llm_response(response)
        assert "truncated" in str(exc_info.value).lower()
        assert "length" in str(exc_info.value).lower()

    def test_finish_reason_stop_passes(self):
        """Test that finish_reason='stop' passes validation."""
        response = make_ai_message(
            content="Complete response",
            response_metadata={"finish_reason": "stop"},
        )
        # Should NOT raise
        validate_llm_response(response)

    def test_missing_response_metadata_passes(self):
        """Test that missing response_metadata passes (fail-open)."""
        response = make_ai_message(content="Valid response")
        # Remove response_metadata by setting to None
        response.response_metadata = None
        # Should NOT raise (fail-open)
        validate_llm_response(response)

    def test_empty_response_metadata_passes(self):
        """Test that empty response_metadata passes (fail-open)."""
        response = make_ai_message(
            content="Valid response",
            response_metadata={},
        )
        # Should NOT raise (fail-open)
        validate_llm_response(response)

    def test_none_finish_reason_passes(self):
        """Test that None finish_reason passes (fail-open)."""
        response = make_ai_message(
            content="Valid response",
            response_metadata={"finish_reason": None},
        )
        # Should NOT raise (fail-open)
        validate_llm_response(response)


# =============================================================================
# Test Tool Call Validation
# =============================================================================


class TestToolCallValidation:
    """Tests for tool call validation (raises LLMResponseValidationError)."""

    def test_tool_call_with_empty_name_raises(self):
        """Test that tool call with empty function name raises validation error."""
        response = make_ai_message(
            content="Calling tool",
            tool_calls=[ToolCall(id="call_1", name="", args={})],
        )
        with pytest.raises(LLMResponseValidationError) as exc_info:
            validate_llm_response(response)
        assert "empty function name" in str(exc_info.value).lower()

    def test_tool_call_with_whitespace_name_raises(self):
        """Test that tool call with whitespace-only function name raises validation error."""
        response = make_ai_message(
            content="Calling tool",
            tool_calls=[ToolCall(id="call_1", name="   ", args={})],
        )
        with pytest.raises(LLMResponseValidationError) as exc_info:
            validate_llm_response(response)
        assert "empty function name" in str(exc_info.value).lower()

    def test_tool_call_dict_format_with_empty_name_raises(self):
        """Test tool call in dict format with empty function name raises error."""
        response = make_ai_message(
            content="Calling tool",
            tool_calls=[{"id": "call_1", "name": "", "args": {}}],
        )
        with pytest.raises(LLMResponseValidationError) as exc_info:
            validate_llm_response(response)
        assert "empty function name" in str(exc_info.value).lower()

    def test_tool_call_dict_format_with_whitespace_name_raises(self):
        """Test tool call in dict format with whitespace function name raises error."""
        response = make_ai_message(
            content="Calling tool",
            tool_calls=[{"id": "call_1", "name": "  ", "args": {}}],
        )
        with pytest.raises(LLMResponseValidationError) as exc_info:
            validate_llm_response(response)
        assert "empty function name" in str(exc_info.value).lower()

    def test_valid_tool_call_passes(self):
        """Test that valid tool call passes validation."""
        response = make_ai_message(
            content="Calling tool",
            tool_calls=[
                ToolCall(id="call_1", name="test_tool", args={"param": "value"}),
            ],
        )
        # Should NOT raise
        validate_llm_response(response)

    def test_multiple_tool_calls_one_invalid_raises(self):
        """Test that if any tool call is invalid, validation fails."""
        response = make_ai_message(
            content="Calling tools",
            tool_calls=[
                ToolCall(id="call_1", name="valid_tool", args={"param": "value"}),
                ToolCall(id="call_2", name="", args={}),  # Invalid: empty name
            ],
        )
        with pytest.raises(LLMResponseValidationError):
            validate_llm_response(response)

    def test_multiple_valid_tool_calls_passes(self):
        """Test that multiple valid tool calls pass validation."""
        response = make_ai_message(
            content="Calling tools",
            tool_calls=[
                ToolCall(id="call_1", name="bash", args={"command": "echo hello"}),
                ToolCall(id="call_2", name="read_file", args={"path": "/tmp/test.txt"}),
            ],
        )
        # Should NOT raise
        validate_llm_response(response)

    def test_tool_call_with_empty_args_dict_passes(self):
        """Test that tool call with empty dict args passes validation."""
        # Empty dict is valid - it just means no arguments
        response = make_ai_message(
            content="Calling tool",
            tool_calls=[ToolCall(id="call_1", name="test_tool", args={})],
        )
        # Should NOT raise - empty dict is valid
        validate_llm_response(response)


# =============================================================================
# Test Valid Responses
# =============================================================================


class TestValidResponses:
    """Tests for valid responses that should pass validation."""

    def test_normal_response_with_content_passes(self):
        """Test that normal response with content passes."""
        response = make_ai_message(
            content="Hello! How can I help you today?",
            response_metadata={"finish_reason": "stop"},
        )
        # Should NOT raise
        validate_llm_response(response)

    def test_response_with_content_and_tool_calls_passes(self):
        """Test that response with both content and tool calls passes."""
        response = make_ai_message(
            content="I'll help you with that.",
            tool_calls=[
                ToolCall(
                    id="call_1",
                    name="some_tool",
                    args={"input": "test data"},
                ),
            ],
            response_metadata={"finish_reason": "tool_calls"},
        )
        # Should NOT raise
        validate_llm_response(response)

    def test_tool_only_response_passes(self):
        """Test that response with only tool calls (no content) passes."""
        response = make_ai_message(
            content="",
            tool_calls=[
                ToolCall(
                    id="call_1",
                    name="bash",
                    args={"command": "echo hello"},
                ),
                ToolCall(
                    id="call_2",
                    name="read_file",
                    args={"path": "/tmp/test.txt"},
                ),
            ],
            response_metadata={"finish_reason": "tool_calls"},
        )
        # Should NOT raise
        validate_llm_response(response)

    def test_response_with_complex_tool_args_passes(self):
        """Test that tool call with complex arguments passes."""
        response = make_ai_message(
            content="",
            tool_calls=[
                ToolCall(
                    id="call_1",
                    name="search",
                    args={
                        "query": "python async",
                        "filters": {"language": "en", "date_range": "week"},
                        "max_results": 10,
                    },
                ),
            ],
        )
        # Should NOT raise
        validate_llm_response(response)


# =============================================================================
# Test Error Response Attribute
# =============================================================================


class TestErrorResponseAttribute:
    """Tests that LLMResponseValidationError properly stores the response."""

    def test_truncated_error_stores_response(self):
        """Test that truncation error stores the response object."""
        response = make_ai_message(
            content="Partial...",
            response_metadata={"finish_reason": "length"},
        )
        try:
            validate_llm_response(response)
            pytest.fail("Expected LLMResponseValidationError to be raised")
        except LLMResponseValidationError as e:
            assert e.response is response

    def test_malformed_tool_call_error_stores_response(self):
        """Test that malformed tool call error stores the response object."""
        response = make_ai_message(
            content="Calling tool",
            tool_calls=[ToolCall(id="call_1", name="", args={})],
        )
        try:
            validate_llm_response(response)
            pytest.fail("Expected LLMResponseValidationError to be raised")
        except LLMResponseValidationError as e:
            assert e.response is response


# =============================================================================
# Test Fail-Open Behavior
# =============================================================================


class TestFailOpenBehavior:
    """Tests for fail-open behavior when validation cannot determine validity."""

    def test_missing_tool_calls_attribute_passes(self):
        """Test that missing tool_calls attribute passes (fail-open)."""
        response = make_ai_message(content="Valid response")
        # Remove tool_calls attribute
        if hasattr(response, "tool_calls"):
            delattr(response, "tool_calls")
        # Should NOT raise (fail-open)
        validate_llm_response(response)


# =============================================================================
# Empty-response-guard Phase 1 — shared predicate truth table (doc §11b)
# =============================================================================


class TestSharedEmptinessPredicate:
    """Truth table for ``is_empty_llm_content`` (empty-response-guard §11b)."""

    def test_none_is_empty(self):
        assert is_empty_llm_content(None) is True

    def test_empty_string_is_empty(self):
        # Streaming aggregation yields "" for all-empty chunk streams
        # (langchain-openai coerces None → "" before concatenation).
        assert is_empty_llm_content("") is True

    def test_whitespace_only_string_is_empty(self):
        assert is_empty_llm_content("   \n\t ") is True

    def test_non_empty_string_is_not_empty(self):
        assert is_empty_llm_content("hello") is False

    def test_think_tag_only_string_is_deliberately_not_empty(self):
        # L3: tag chars are non-whitespace → the S1 guard must NOT own
        # this class; router rows 2-3 + the S5 cap do.
        assert is_empty_llm_content("<think>reasoning</think>") is False

    def test_empty_list_is_vacuously_empty(self):
        # The deliberate flip of the legacy router pin (doc §10): the
        # legacy `_is_empty_content` returned False for ANY list.
        assert is_empty_llm_content([]) is True

    def test_list_all_whitespace_text_blocks_is_empty(self):
        content = [{"type": "text", "text": "  "}, {"type": "text", "text": "\n"}]
        assert is_empty_llm_content(content) is True

    def test_list_with_non_whitespace_text_block_is_not_empty(self):
        content = [{"type": "text", "text": "answer"}]
        assert is_empty_llm_content(content) is False

    def test_list_with_image_block_is_not_empty(self):
        # L13: a vision response carrying a non-text block is never empty.
        content = [
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,x"}},
            {"type": "text", "text": " "},
        ]
        assert is_empty_llm_content(content) is False

    def test_list_with_audio_block_is_not_empty(self):
        content = [{"type": "audio", "audio": {"data": "x"}}]
        assert is_empty_llm_content(content) is False

    def test_list_with_unknown_block_type_fails_open(self):
        content = [{"type": "mystery_block", "payload": "x"}]
        assert is_empty_llm_content(content) is False

    def test_list_with_untyped_block_fails_open(self):
        content = [{"data": "no type key"}]
        assert is_empty_llm_content(content) is False

    def test_list_with_non_dict_entry_fails_open(self):
        content = ["just a string block"]
        assert is_empty_llm_content(content) is False

    def test_list_with_none_text_value_fails_open(self):
        # S1 (2026-09-12 review): a MALFORMED text block (text=None) must
        # fail OPEN non-empty — str(None or "") read it toward EMPTY, a
        # C1-class false-positive edge (guard would burn retry budget on
        # a provider quirk).
        assert is_empty_llm_content([{"type": "text", "text": None}]) is False

    def test_list_with_none_entry_fails_open(self):
        # S1 truth-table row: bare None entry — non-dict shape → fail open.
        assert is_empty_llm_content([None]) is False

    def test_list_with_non_string_text_values_fail_open(self):
        # Non-string text payloads are malformed blocks, never "empty".
        assert is_empty_llm_content([{"type": "text", "text": 123}]) is False

    def test_explicit_empty_string_text_block_is_still_empty(self):
        # Deliberate contrast: a WELL-FORMED text block carrying an
        # explicit empty string is a legitimately empty text payload
        # (whitespace-only rule), NOT malformed — stays EMPTY.
        assert is_empty_llm_content([{"type": "text", "text": ""}]) is True

    def test_other_shapes_fail_open(self):
        assert is_empty_llm_content({}) is False
        assert is_empty_llm_content(123) is False
        assert is_empty_llm_content(0.0) is False


# =============================================================================
# Empty-response-guard Phase 1 — S1 turn-aware raise (doc §5 Option 4)
# =============================================================================


def _tool_calling_ai():
    return AIMessage(
        content="",
        tool_calls=[ToolCall(id="call_1", name="test_tool", args={})],
    )


def _nudge_human(marker=True):
    kwargs = {"injected_message": True}
    if marker:
        kwargs["empty_response_nudge"] = True
    return HumanMessage(content=NUDGE_MESSAGE, additional_kwargs=kwargs)


def _attestation_nudge_human(legacy=True):
    """The attestation-gate deny nudge (daemon/graph.py deny path).

    ``legacy=True`` builds the PRE-C1-follow-up shape (only the
    ``attestation_nudge`` stamp) — the retroactive-heal pin; the
    post-fix constructor additionally stamps ``injected_message=True``.
    """
    kwargs = {"attestation_nudge": True, "attestation_nudge_denied_count": 1}
    if not legacy:
        kwargs["injected_message"] = True
    return HumanMessage(content="Please attest your completed work.", additional_kwargs=kwargs)


def _context_human():
    return HumanMessage(
        content="[SYSTEM CONTEXT: Blueprint]\ncontext body",
        additional_kwargs={"injected_message": True, "context_kind": "blueprint"},
    )


def _language_reminder():
    return HumanMessage(
        content="Please respond again in English.",
        additional_kwargs={"injected_message": True, "language_check_reminder": True},
    )


class TestEmptyLLMResponseErrorSubclass:
    """The typed error must sit inside the existing validation hierarchy."""

    def test_subclass_of_llm_response_validation_error(self):
        assert issubclass(EmptyLLMResponseError, LLMResponseValidationError)

    def test_carries_response(self):
        response = make_ai_message(content="")
        error = EmptyLLMResponseError("empty", response=response)
        assert error.response is response
        assert isinstance(error, LLMResponseValidationError)

    def test_transient_membership_inherited(self):
        # Retry/failover budget consumption is inherited via
        # TRANSIENT_EXCEPTIONS membership of the parent class — the
        # subclass must not break that chain.
        from daemon.llm_error_classifier import TRANSIENT_EXCEPTIONS

        assert LLMResponseValidationError in TRANSIENT_EXCEPTIONS
        assert any(
            isinstance(EmptyLLMResponseError, type) and issubclass(EmptyLLMResponseError, exc)
            for exc in TRANSIENT_EXCEPTIONS
        )


class TestEmptyResponseGuardRaiseGate:
    """Turn-aware raise matrix — the L1-L13 exemption contract (doc §6)."""

    def test_empty_as_entire_answer_raises(self):
        # Row 6 incident class: empty directly after a real human turn.
        with pytest.raises(EmptyLLMResponseError):
            validate_llm_response(
                make_ai_message(content=""), input_messages=[_real_human()]
            )

    def test_first_empty_after_tool_result_passes_router_nudge_owns_it(self):
        # L1 rung 1: the router's row-5 nudge is the cheap first rung.
        messages = [_real_human(), _tool_calling_ai(), _tool_result()]
        validate_llm_response(make_ai_message(content=""), input_messages=messages)

    def test_second_empty_after_nudge_raises(self):
        # §8.1 synthesis: empty → nudge → second empty RAISES (no longer
        # a silent END).
        messages = [
            _real_human(),
            _tool_calling_ai(),
            _tool_result(),
            _nudge_human(),
        ]
        with pytest.raises(EmptyLLMResponseError):
            validate_llm_response(make_ai_message(content=""), input_messages=messages)

    def test_second_empty_after_legacy_nudge_text_still_raises(self):
        # Checkpoints written before the dedicated marker: the exact
        # nudge text (with only the injected_message stamp) is still
        # recognized.
        messages = [
            _real_human(),
            _tool_calling_ai(),
            _tool_result(),
            _nudge_human(marker=False),
        ]
        with pytest.raises(EmptyLLMResponseError):
            validate_llm_response(make_ai_message(content=""), input_messages=messages)

    def test_attestation_nudge_not_a_user_boundary_report_already_spoken(self):
        # C1 (2026-09-12 review, MERGE-BLOCKING): the attestation-gate
        # deny nudge stamps no injected/context kwarg pre-fix, so the
        # window walk mistook it for the REAL user boundary and broke
        # there with prior_spoke=False → the guard RAISED on a turn
        # whose report already exists in the transcript (legacy: silent
        # END). Exact repro: [user, report_AI, attestation_nudge] +
        # empty must NOT raise — the attestation nudge is skipped as
        # server-injected, the walk continues to the spoke report AI
        # and the real human boundary, done-speaking suppression holds.
        messages = [
            _real_human(),
            AIMessage(content="Report: implemented the fix, all tests green."),
            _attestation_nudge_human(legacy=True),  # retroactive-heal shape
        ]
        validate_llm_response(make_ai_message(content=""), input_messages=messages)

    def test_attestation_nudge_stamped_shape_also_skipped(self):
        # Post-fix constructor shape (graph.py stamps injected_message
        # too): same no-raise outcome — both halves of the C1 fix.
        messages = [
            _real_human(),
            AIMessage(content="Report: implemented the fix, all tests green."),
            _attestation_nudge_human(legacy=False),
        ]
        validate_llm_response(make_ai_message(content=""), input_messages=messages)

    def test_tool_only_turn_response_exempt(self):
        # L5: a response carrying tool_calls is never empty-as-answer.
        messages = [_real_human()]
        validate_llm_response(
            make_ai_message(content="", tool_calls=[
                ToolCall(id="c2", name="t", args={})
            ]),
            input_messages=messages,
        )

    def test_reasoning_only_response_exempt(self):
        # L2: designed reasoning sequences must not burn retry budget.
        messages = [_real_human()]
        validate_llm_response(
            make_ai_message(
                content="", additional_kwargs={"reasoning_content": "thinking..."}
            ),
            input_messages=messages,
        )

    def test_think_tag_only_response_exempt(self):
        # L3: deliberately non-empty under the shared predicate.
        messages = [_real_human()]
        validate_llm_response(
            make_ai_message(content="<think>reasoning</think>"),
            input_messages=messages,
        )

    def test_ghost_promise_response_exempt(self):
        # L4: truthy content ending with ':' — router row 4 owns it.
        messages = [_real_human()]
        validate_llm_response(
            make_ai_message(content="Now let me write the document:"),
            input_messages=messages,
        )

    def test_done_speaking_spoke_suppression_passes(self):
        # L1: trailing empty after the assistant already spoke this turn.
        messages = [_real_human(), AIMessage(content="Here is the answer.")]
        validate_llm_response(make_ai_message(content=""), input_messages=messages)

    def test_language_check_reminder_is_not_a_boundary_and_spoke_suppresses(self):
        # L9: wrong-language reminder → re-invoke → empty passes because
        # the assistant already produced (wrong-language) content.
        messages = [
            _real_human(),
            AIMessage(content="Bonjour, voici la réponse."),
            _language_reminder(),
        ]
        validate_llm_response(make_ai_message(content=""), input_messages=messages)

    def test_context_human_is_not_a_user_boundary(self):
        # L6: context blocks are skipped; the real human below governs.
        messages = [_context_human(), _real_human()]
        with pytest.raises(EmptyLLMResponseError):
            validate_llm_response(make_ai_message(content=""), input_messages=messages)

    def test_context_only_window_with_no_marker_and_no_spoke_raises(self):
        # No real boundary found at all: empty-as-entire-answer still
        # raises (fail-loud default of the window scan).
        with pytest.raises(EmptyLLMResponseError):
            validate_llm_response(
                make_ai_message(content=""), input_messages=[_context_human()]
            )

    def test_multimodal_image_bearing_content_never_raises(self):
        # L13: image-bearing content is non-empty under the predicate.
        messages = [_real_human()]
        validate_llm_response(
            make_ai_message(
                content=[
                    {"type": "image_url", "image_url": {"url": "data:image/png;base64,x"}},
                    {"type": "text", "text": " "},
                ]
            ),
            input_messages=messages,
        )

    def test_all_whitespace_text_blocks_empty_after_human_raises(self):
        # L13 flip side: an all-empty-text vision response IS empty.
        messages = [_real_human()]
        with pytest.raises(EmptyLLMResponseError):
            validate_llm_response(
                make_ai_message(content=[{"type": "text", "text": "  "}]),
                input_messages=messages,
            )

    def test_bare_signature_fails_open(self):
        # Legacy direct callers (no input_messages) keep the historical
        # pass-through for empty content.
        validate_llm_response(make_ai_message(content=""))

    def test_non_list_input_fails_open(self):
        validate_llm_response(make_ai_message(content=""), input_messages=None)
        validate_llm_response(make_ai_message(content=""), input_messages="not-a-list")


class TestServerInjectedAndNudgeDetectionPins:
    """Predicate-level pins for the C1 follow-up + W3 hardening.

    C1: the attestation-gate deny nudge must classify as SERVER-INJECTED
    (skipped by the turn-window scan) in BOTH kwarg shapes — the bare
    pre-fix shape (retroactive heal for in-flight checkpoints) and the
    post-fix stamped shape — while NEVER matching ``_is_nudge_human``
    (a different nudge; mistaking it for the empty-response nudge would
    flip the §8.1 second-empty allowance to RAISE).

    W3: the ``_is_nudge_human`` text-fallback is CONJUNCTIVE with
    ``injected_message=True`` — pre-marker checkpoints (which always
    carried the injected stamp) stay recognized, a user literally typing
    the nudge sentence never becomes one.
    """

    def test_attestation_nudge_kwarg_is_server_injected(self):
        bare = HumanMessage(content="Please attest.", additional_kwargs={"attestation_nudge": True})
        stamped = HumanMessage(
            content="Please attest.",
            additional_kwargs={"attestation_nudge": True, "injected_message": True},
        )
        assert _is_server_injected_human(bare) is True
        assert _is_server_injected_human(stamped) is True

    def test_attestation_nudge_is_not_the_empty_response_nudge(self):
        for kwargs in (
            {"attestation_nudge": True},
            {"attestation_nudge": True, "injected_message": True},
        ):
            msg = HumanMessage(content="Please attest your completed work.", additional_kwargs=kwargs)
            assert _is_nudge_human(msg) is False
            assert _is_server_injected_human(msg) is True

    def test_nudge_text_fallback_requires_marker_or_injected_stamp(self):
        # Dedicated marker (post-marker checkpoints).
        assert _is_nudge_human(
            HumanMessage(content=NUDGE_MESSAGE, additional_kwargs={"empty_response_nudge": True})
        ) is True
        # W3: marker-absent pre-marker checkpoint — injected stamp present.
        assert _is_nudge_human(
            HumanMessage(content=NUDGE_MESSAGE, additional_kwargs={"injected_message": True})
        ) is True
        # W3 hardening: bare user LITERALLY TYPING the nudge sentence —
        # never nudge-classified (content match without the stamp).
        bare_typing = HumanMessage(content=NUDGE_MESSAGE)
        assert _is_nudge_human(bare_typing) is False
        assert _is_server_injected_human(bare_typing) is False

    def test_nudge_text_fallback_rejects_other_injected_content(self):
        # The conjunctive fallback keys on the EXACT nudge text — an
        # unrelated injected message is not a nudge.
        assert _is_nudge_human(
            HumanMessage(content="Please respond again in English.", additional_kwargs={"injected_message": True})
        ) is False


class TestEmptyResponseGuardKillSwitch:
    """Kill-switch OFF restores the legacy pass-through (doc §10 pins)."""

    def test_guard_off_disables_the_raise(self):
        install_empty_guard_config(enabled=False, compaction_skip=False)
        # Raise-shaped input passes through.
        validate_llm_response(
            make_ai_message(content=""), input_messages=[_real_human()]
        )

    def test_guard_off_keeps_checks_1_and_2(self):
        # "Byte-identical legacy": only the empty check is disabled —
        # truncation and malformed tool-call validation still fire.
        install_empty_guard_config(enabled=False, compaction_skip=False)
        truncated = make_ai_message(content="")
        truncated.response_metadata = {"finish_reason": "length"}
        with pytest.raises(LLMResponseValidationError):
            validate_llm_response(truncated, input_messages=[_real_human()])
        malformed = make_ai_message(
            content="x",
            tool_calls=[ToolCall(id="c9", name="", args={})],
        )
        with pytest.raises(LLMResponseValidationError):
            validate_llm_response(malformed, input_messages=[_real_human()])

    def test_scope_disable_only_affects_the_scope(self):
        with empty_guard_disabled():
            validate_llm_response(
                make_ai_message(content=""), input_messages=[_real_human()]
            )
        # After the scope exits, the raise is live again.
        with pytest.raises(EmptyLLMResponseError):
            validate_llm_response(
                make_ai_message(content=""), input_messages=[_real_human()]
            )

    def test_install_roundtrip(self):
        install_empty_guard_config(enabled=False, compaction_skip=True)
        from daemon.response_validation import (
            get_empty_guard_compaction_skip,
            get_empty_response_guard_enabled,
        )

        assert get_empty_response_guard_enabled() is False
        assert get_empty_guard_compaction_skip() is True
