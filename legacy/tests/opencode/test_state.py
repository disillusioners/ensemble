"""Unit tests for ``daemon.opencode.state`` — session-state enum and message helpers.

Covers every function in the module:

- ``SessionState`` enum values
- ``_derive_state_from_finish(reason, has_error)``
- ``has_message_error(msg)``
- ``get_message_finish(msg)``
- ``strip_message_bloat(msg)``

The tests are deliberately organised so every logical branch of every function
has an explicit assertion — including the "should not raise" paths and the
input-sanitisation guards (None, wrong types, missing keys).
"""

import pytest

from daemon.opencode.state import (
    SessionState,
    _derive_state_from_finish,
    has_message_error,
    get_message_finish,
    strip_message_bloat,
)


# ─────────────────────────────────────────────────────────────────────────────
# SessionState enum
# ─────────────────────────────────────────────────────────────────────────────


class TestSessionStateValues:
    def test_idle_value(self):
        assert SessionState.IDLE.value == "IDLE"

    def test_busy_value(self):
        assert SessionState.BUSY.value == "BUSY"

    def test_waiting_for_input_value(self):
        assert SessionState.WAITING_FOR_INPUT.value == "WAITING_FOR_INPUT"

    def test_is_str_enum(self):
        # Verify the enum inherits from str so it serialises to JSON cleanly.
        assert isinstance(SessionState.IDLE, str)


# ─────────────────────────────────────────────────────────────────────────────
# _derive_state_from_finish
# ─────────────────────────────────────────────────────────────────────────────


class TestDeriveStateFromFinish:
    """Direct port of the switch block in manager.go lines 179-194.

    The function maps a step-finish ``reason`` plus an error flag to a
    SessionState. The sentinel ``"<unknown>"`` is treated like any other
    non-matching reason.
    """

    # Exact-reason branches
    def test_waiting_for_input_returns_waiting_for_input(self):
        result = _derive_state_from_finish("waiting_for_input", False)
        assert result == SessionState.WAITING_FOR_INPUT

    def test_waiting_for_input_with_error_still_returns_waiting_for_input(self):
        # The error flag is ignored when reason == "waiting_for_input".
        result = _derive_state_from_finish("waiting_for_input", True)
        assert result == SessionState.WAITING_FOR_INPUT

    def test_stop_returns_idle(self):
        result = _derive_state_from_finish("stop", False)
        assert result == SessionState.IDLE

    def test_stop_with_error_returns_idle(self):
        result = _derive_state_from_finish("stop", True)
        assert result == SessionState.IDLE

    # <unknown> sentinel (no step-finish was found)
    def test_unknown_with_error_returns_idle(self):
        result = _derive_state_from_finish("<unknown>", True)
        assert result == SessionState.IDLE

    def test_unknown_without_error_returns_busy(self):
        result = _derive_state_from_finish("<unknown>", False)
        assert result == SessionState.BUSY

    # Other arbitrary reasons (must behave like <unknown>)
    def test_other_reason_without_error_returns_busy(self):
        result = _derive_state_from_finish("some_custom_reason", False)
        assert result == SessionState.BUSY

    def test_other_reason_with_error_returns_idle(self):
        result = _derive_state_from_finish("some_custom_reason", True)
        assert result == SessionState.IDLE

    # Edge: empty string is not a special reason
    def test_empty_string_without_error_returns_busy(self):
        result = _derive_state_from_finish("", False)
        assert result == SessionState.BUSY

    def test_empty_string_with_error_returns_idle(self):
        result = _derive_state_from_finish("", True)
        assert result == SessionState.IDLE


# ─────────────────────────────────────────────────────────────────────────────
# has_message_error
# ─────────────────────────────────────────────────────────────────────────────


class TestHasMessageError:
    """Direct port of manager.go lines 240-248.

    The Go version uses ``_, hasError := info["error"]`` — key *presence*
    is what matters, not the value's truthiness. A None or "" value still
    counts as an error.
    """

    # Guard clauses
    def test_none_returns_false(self):
        assert has_message_error(None) is False

    def test_non_dict_returns_false(self):
        assert has_message_error("not a dict") is False
        assert has_message_error([1, 2, 3]) is False
        assert has_message_error(42) is False

    # info absent / wrong type
    def test_missing_info_returns_false(self):
        assert has_message_error({}) is False

    def test_info_is_none_returns_false(self):
        assert has_message_error({"info": None}) is False

    def test_info_is_not_dict_returns_false(self):
        assert has_message_error({"info": "string"}) is False
        assert has_message_error({"info": [1, 2]}) is False

    # Key presence — truthiness does NOT matter (mirrors Go)
    def test_error_key_with_string_value_returns_true(self):
        assert has_message_error({"info": {"error": "timeout"}}) is True

    def test_error_key_with_none_value_returns_true(self):
        assert has_message_error({"info": {"error": None}}) is True

    def test_error_key_with_empty_string_returns_true(self):
        assert has_message_error({"info": {"error": ""}}) is True

    def test_error_key_with_zero_returns_true(self):
        assert has_message_error({"info": {"error": 0}}) is True

    # No error key
    def test_no_error_key_returns_false(self):
        msg = {"info": {"finish": "stop", "id": "msg-1"}}
        assert has_message_error(msg) is False


# ─────────────────────────────────────────────────────────────────────────────
# get_message_finish
# ─────────────────────────────────────────────────────────────────────────────


class TestGetMessageFinish:
    """Port of manager.go:221-248 (getMessageFinish + hasMessageError).

    Returns ``None`` for non-dict input so callers can distinguish "no
    message" from "message with no step-finish". When parts is missing or
    non-list, falls back to ``("<unknown>", has_message_error(msg))``.
    """

    # Guard clauses
    def test_none_returns_none(self):
        assert get_message_finish(None) is None

    def test_non_dict_returns_none(self):
        assert get_message_finish("string") is None
        assert get_message_finish([1]) is None

    # parts absent / wrong type
    def test_missing_parts_returns_unknown_with_error_flag(self):
        msg: dict = {"info": {"error": "oops"}}
        result = get_message_finish(msg)
        assert result == ("<unknown>", True)

    def test_parts_is_none_returns_unknown_with_error_flag(self):
        msg = {"parts": None, "info": {"error": "oops"}}
        result = get_message_finish(msg)
        assert result == ("<unknown>", True)

    def test_parts_is_string_returns_unknown_with_error_flag(self):
        msg = {"parts": "not a list", "info": {"error": "oops"}}
        result = get_message_finish(msg)
        assert result == ("<unknown>", True)

    # Happy path — step-finish found
    def test_extracts_reason_from_step_finish(self):
        msg = {"parts": [{"type": "step-finish", "reason": "stop"}]}
        result = get_message_finish(msg)
        assert result == ("stop", False)

    def test_extracts_reason_with_error_in_info(self):
        msg = {
            "parts": [{"type": "step-finish", "reason": "waiting_for_input"}],
            "info": {"error": "timeout"},
        }
        result = get_message_finish(msg)
        assert result == ("waiting_for_input", True)

    # Ordering / selection
    def test_uses_first_step_finish_only(self):
        msg = {
            "parts": [
                {"type": "text", "text": "hello"},
                {"type": "step-finish", "reason": "stop"},
                {"type": "step-finish", "reason": "waiting_for_input"},
            ],
        }
        result = get_message_finish(msg)
        assert result == ("stop", False)

    def test_skips_non_dict_parts(self):
        msg = {
            "parts": [
                "not a dict",
                None,
                {"type": "step-finish", "reason": "stop"},
            ],
        }
        result = get_message_finish(msg)
        assert result == ("stop", False)

    def test_skips_parts_without_step_finish_type(self):
        msg = {
            "parts": [
                {"type": "text", "text": "hello"},
                {"type": "reasoning", "text": "thinking..."},
            ],
        }
        result = get_message_finish(msg)
        assert result == ("<unknown>", False)

    # Step-finish with non-string or missing reason
    def test_unknown_when_step_finish_reason_is_missing(self):
        msg = {"parts": [{"type": "step-finish"}]}
        result = get_message_finish(msg)
        assert result == ("<unknown>", False)

    def test_unknown_when_step_finish_reason_is_not_string(self):
        msg = {"parts": [{"type": "step-finish", "reason": 42}]}
        result = get_message_finish(msg)
        assert result == ("<unknown>", False)

    def test_unknown_when_step_finish_reason_is_none(self):
        msg = {"parts": [{"type": "step-finish", "reason": None}]}
        result = get_message_finish(msg)
        assert result == ("<unknown>", False)


# ─────────────────────────────────────────────────────────────────────────────
# strip_message_bloat
# ─────────────────────────────────────────────────────────────────────────────


class TestStripMessageBloat:
    """Direct port of manager.go lines 252-311.

    The function mutates the input dict in place (matching Go semantics)
    and also returns it so callers can chain. Only the following fields
    survive:

    - ``info``: id, finish, error, time.completed, time.created
    - ``parts[i]``: type, text, reason, error
    """

    # Guard clauses — non-dicts pass through unchanged
    def test_none_returns_none(self):
        assert strip_message_bloat(None) is None

    def test_string_returns_string(self):
        assert strip_message_bloat("hello") == "hello"

    def test_list_returns_list(self):
        assert strip_message_bloat([1, 2]) == [1, 2]

    def test_empty_dict_returns_empty_dict(self):
        assert strip_message_bloat({}) == {}

    # ── info stripping ───────────────────────────────────────────────────────

    def test_keeps_info_id(self):
        msg = {"info": {"id": "msg-42"}}
        result = strip_message_bloat(msg)
        assert result["info"] == {"id": "msg-42"}

    def test_keeps_info_finish(self):
        msg = {"info": {"finish": "stop"}}
        result = strip_message_bloat(msg)
        assert result["info"] == {"finish": "stop"}

    def test_keeps_info_error_key_with_value(self):
        msg = {"info": {"error": "timeout"}}
        result = strip_message_bloat(msg)
        assert result["info"] == {"error": "timeout"}

    def test_keeps_info_error_key_with_none(self):
        msg = {"info": {"error": None}}
        result = strip_message_bloat(msg)
        assert "error" in result["info"]

    def test_removes_info_extra_fields(self):
        msg = {"info": {"id": "x", "chat_id": "c1", "snapshot": "..."}}
        result = strip_message_bloat(msg)
        assert "id" in result["info"]
        assert "chat_id" not in result["info"]
        assert "snapshot" not in result["info"]

    def test_keeps_info_time_completed_only(self):
        msg = {"info": {"time": {"completed": 12345.0, "other": 99}}}
        result = strip_message_bloat(msg)
        assert result["info"] == {"time": {"completed": 12345.0}}

    def test_keeps_info_time_created_only(self):
        msg = {"info": {"time": {"created": 67890.0, "other": 99}}}
        result = strip_message_bloat(msg)
        assert result["info"] == {"time": {"created": 67890.0}}

    def test_keeps_info_time_both(self):
        msg = {"info": {"time": {"completed": 12345.0, "created": 67890.0}}}
        result = strip_message_bloat(msg)
        assert result["info"] == {"time": {"completed": 12345.0, "created": 67890.0}}

    def test_drops_time_fields_with_non_numeric_values(self):
        # Non-numeric completed/created are not copied into the
        # stripped_time dict, so the (empty) time map is not stored.
        # Because stripped_info ends up empty, the production code
        # leaves the original info dict untouched.
        msg = {"info": {"time": {"completed": "not a number"}}}
        result = strip_message_bloat(msg)
        # The original time dict is preserved verbatim.
        assert result["info"]["time"] == {"completed": "not a number"}

    def test_leaves_info_unchanged_when_no_fields_match(self):
        # When info exists but every allowed sub-field is missing,
        # the production code leaves the original info dict in place
        # (the assignment is gated by ``if stripped_info:``).
        msg = {"info": {"chat_id": "c1"}}
        result = strip_message_bloat(msg)
        assert "info" in result
        assert "chat_id" in result["info"]

    # ── parts stripping ─────────────────────────────────────────────────────

    def test_keeps_part_type(self):
        msg = {"parts": [{"type": "text"}]}
        result = strip_message_bloat(msg)
        assert result["parts"][0] == {"type": "text"}

    def test_keeps_part_text(self):
        msg = {"parts": [{"type": "text", "text": "hello world"}]}
        result = strip_message_bloat(msg)
        assert result["parts"][0] == {"type": "text", "text": "hello world"}

    def test_keeps_part_reason(self):
        msg = {"parts": [{"type": "step-finish", "reason": "stop"}]}
        result = strip_message_bloat(msg)
        assert result["parts"][0] == {"type": "step-finish", "reason": "stop"}

    def test_keeps_part_error_key_with_value(self):
        msg = {"parts": [{"type": "error", "error": "oops"}]}
        result = strip_message_bloat(msg)
        assert result["parts"][0] == {"type": "error", "error": "oops"}

    def test_keeps_part_error_key_with_none(self):
        msg = {"parts": [{"type": "error", "error": None}]}
        result = strip_message_bloat(msg)
        assert "error" in result["parts"][0]

    def test_removes_part_extra_fields(self):
        msg = {
            "parts": [
                {
                    "type": "text",
                    "text": "hello",
                    "token_count": 1000,
                    "snapshot": "...",
                }
            ]
        }
        result = strip_message_bloat(msg)
        assert result["parts"][0] == {"type": "text", "text": "hello"}

    def test_leaves_non_dict_parts_untouched(self):
        # Non-dict entries in the parts list are skipped (not converted
        # to ``{}``). Only the dict entry at index 2 is stripped.
        msg = {"parts": ["not a dict", None, {"type": "text", "text": "ok"}]}
        result = strip_message_bloat(msg)
        assert result["parts"][0] == "not a dict"
        assert result["parts"][1] is None
        assert result["parts"][2] == {"type": "text", "text": "ok"}

    def test_strips_parts_but_keeps_non_list_parts(self):
        # When parts is not a list it is left untouched.
        msg = {"parts": "string"}
        result = strip_message_bloat(msg)
        assert result["parts"] == "string"

    # ── mutation / return-value semantics ───────────────────────────────────

    def test_mutates_input_dict_in_place(self):
        """The function modifies the input and returns the same reference."""
        msg = {"info": {"id": "x", "chat_id": "c1"}}
        result = strip_message_bloat(msg)
        assert result is msg
        assert "chat_id" not in result["info"]
