"""Session state enum and message-derivation helpers.

Direct port of the helper functions in
``.inspiration-projects/opencode_skill_src/internal/manager/manager.go``
(lines 218-311) plus the state constants at lines 15-21.

The Go code uses untyped string constants; this module exposes a proper
``SessionState`` ``str`` enum so it is usable from Pydantic models and
serializes cleanly to JSON.
"""

from __future__ import annotations

from enum import Enum
from typing import Any


class SessionState(str, Enum):
    """Session state machine. Mirrors ``manager.State`` in Go.

    See ``manager.go`` lines 15-21 for the source of truth:

        StateIdle            = "IDLE"
        StateBusy            = "BUSY"
        StateWaitingForInput = "WAITING_FOR_INPUT"
    """

    IDLE = "IDLE"
    BUSY = "BUSY"
    WAITING_FOR_INPUT = "WAITING_FOR_INPUT"


# String aliases for cases where the consumer just wants the raw value.
STATE_IDLE: str = SessionState.IDLE.value
STATE_BUSY: str = SessionState.BUSY.value
STATE_WAITING_FOR_INPUT: str = SessionState.WAITING_FOR_INPUT.value


# ─────────────────────────────────────────────────────────────────────────────
# State derivation helpers — port of manager.go:218-311
# ─────────────────────────────────────────────────────────────────────────────


def _derive_state_from_finish(reason: str, has_error: bool) -> SessionState:
    """Map an OpenCode step-finish reason (+ error flag) to a state.

    Direct port of the ``switch`` block at ``manager.go`` lines 179-194:

        switch finishReason:
            "waiting_for_input" -> StateWaitingForInput
            "stop"              -> StateIdle
            default:
                if hasError:    -> StateIdle
                else:           -> StateBusy

    Args:
        reason: Value of the ``step-finish`` part's ``reason`` field, or
            ``"<unknown>"`` when no step-finish was found.
        has_error: ``True`` if the message has a non-null ``info.error``
            field (timeouts, aborts, etc.).

    Returns:
        The derived ``SessionState``. Never returns ``WAITING_FOR_INPUT``
        unless ``reason == "waiting_for_input"`` is passed explicitly.
    """
    if reason == "waiting_for_input":
        return SessionState.WAITING_FOR_INPUT
    if reason == "stop":
        return SessionState.IDLE
    # <unknown> branch: no step-finish found
    if has_error:
        return SessionState.IDLE
    return SessionState.BUSY


def has_message_error(msg: dict[str, Any] | None) -> bool:
    """Return ``True`` if the message contains a non-null ``info.error``.

    Direct port of ``manager.go`` lines 240-248:

        if msgMap, ok := msg.(map[string]interface{}); ok {
            if info, ok := msgMap["info"].(map[string]interface{}); ok {
                _, hasError := info["error"]
                return hasError
            }
        }

    The Go version uses ``_, hasError := info["error"]`` — presence of the
    key is what counts, *not* whether the value is truthy. We preserve that
    behavior: a key with a ``None``/``""`` value still counts as an error.

    Args:
        msg: Parsed OpenCode message dictionary. May be ``None`` for safety
            — the function then returns ``False``.

    Returns:
        Whether ``info.error`` is present in the message dict.
    """
    if not isinstance(msg, dict):
        return False
    info = msg.get("info")
    if not isinstance(info, dict):
        return False
    return "error" in info


def get_message_finish(msg: dict[str, Any] | None) -> tuple[str, bool] | None:
    """Extract ``(reason, has_error)`` from an OpenCode message.

    Direct port of the loop body in ``manager.go`` lines 221-236
    (``getMessageFinish``) plus the ``hasMessageError`` check at line 240-248.

    Walks the ``parts`` array looking for a part with ``type == "step-finish"``
    and returns its ``reason`` field. Falls back to ``"<unknown>"`` when no
    step-finish is found — preserving Go's string-based sentinel.

    Args:
        msg: Parsed OpenCode message dictionary. May be ``None`` — caller
            distinguishes "no message" via ``None`` return.

    Returns:
        A ``(reason, has_error)`` tuple, or ``None`` if ``msg`` is not a
        dict. ``reason`` is ``"<unknown>"`` if no step-finish was found.
    """
    if not isinstance(msg, dict):
        return None
    parts = msg.get("parts")
    if not isinstance(parts, list):
        return ("<unknown>", has_message_error(msg))
    reason = "<unknown>"
    for part in parts:
        if not isinstance(part, dict):
            continue
        if part.get("type") == "step-finish":
            r = part.get("reason")
            if isinstance(r, str):
                reason = r
            break
    return (reason, has_message_error(msg))


def strip_message_bloat(msg: Any) -> Any:
    """Strip verbose fields from an OpenCode message response.

    Direct port of ``manager.go`` lines 252-311. Removes fields that are not
    useful to downstream agents (token counts, snapshots, internal IDs, etc.)
    and keeps only:

    - ``info``: ``id``, ``finish``, ``error``, ``time.completed``, ``time.created``
    - ``parts[i]``: ``type``, ``text``, ``reason``, ``error``

    The function mutates the input dict in place (matching Go) but also
    returns the dict so callers can chain. Pass-by-reference semantics are
    preserved so a caller that holds the original reference sees the
    stripped version — important because ``manager.go`` stores the result in
    ``sm.LatestResponse`` (line 200).

    Args:
        msg: An OpenCode message. Non-dict inputs are returned unchanged.

    Returns:
        The stripped message dict (same reference as input if it was a
        dict).
    """
    if not isinstance(msg, dict):
        return msg

    # ── Strip info bloat — keep only id/finish/error/time.{completed,created}
    # manager.go lines 259-285
    info = msg.get("info")
    if isinstance(info, dict):
        stripped_info: dict[str, Any] = {}
        if isinstance(info.get("id"), str):
            stripped_info["id"] = info["id"]
        if isinstance(info.get("finish"), str):
            stripped_info["finish"] = info["finish"]
        if "error" in info:
            # Mirrors Go: presence of key counts, even if value is nil/empty
            stripped_info["error"] = info["error"]
        time_map = info.get("time")
        if isinstance(time_map, dict):
            stripped_time: dict[str, Any] = {}
            # Go uses float64 from encoding/json; we accept either int/float
            if "completed" in time_map and isinstance(time_map["completed"], (int, float)):
                stripped_time["completed"] = time_map["completed"]
            if "created" in time_map and isinstance(time_map["created"], (int, float)):
                stripped_time["created"] = time_map["created"]
            if stripped_time:
                stripped_info["time"] = stripped_time
        if stripped_info:
            msg["info"] = stripped_info

    # ── Strip parts bloat — keep type/text/reason/error
    # manager.go lines 288-308
    parts = msg.get("parts")
    if isinstance(parts, list):
        for i, part in enumerate(parts):
            if not isinstance(part, dict):
                continue
            stripped_part: dict[str, Any] = {}
            if isinstance(part.get("type"), str):
                stripped_part["type"] = part["type"]
            if isinstance(part.get("text"), str):
                stripped_part["text"] = part["text"]
            if isinstance(part.get("reason"), str):
                stripped_part["reason"] = part["reason"]
            if "error" in part:
                stripped_part["error"] = part["error"]
            parts[i] = stripped_part

    return msg
