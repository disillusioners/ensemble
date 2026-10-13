"""External OpenCode message dispatcher.

This module exposes the public function ``external_opencode_send_message``
which the ensemble daemon's tool/call layer invokes to talk to a running
OpenCode session. It is the Python port of the ``PROMPT``/``COMMAND``/
``ANSWER``/``RESUME``/``INIT_SESSION``/``ABORT_SESSION`` switch block in
``.inspiration-projects/opencode_skill_src/internal/daemon/server.go``
(lines 230-507).

The Go binary handled these actions over a TCP JSON protocol. In the
ensemble daemon we expose a single Python function that takes a typed
``Request`` and returns a typed ``Response``. The router / tool layer
adapts incoming requests into this function.

Key Go→Python differences:

- The Go binary's ``conn.Read`` + JSON unmarshal is replaced by Python
  argument parsing.
- The "break" inside a Go ``switch`` becomes a ``return`` of the
  response dict in Python.
- The Go ``action in {"PING", "START_SESSION", ...}`` pattern becomes a
  top-level ``match`` (3.10+) or chained ``isinstance`` checks.
- The TCP I/O loop in the Go binary is replaced by direct function
  calls — the daemon's HTTP router handles the request lifecycle.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from pydantic import BaseModel, Field

from .client import AnswerRequest, CommandRequest, PromptRequest
from .constants import (
    DEFAULT_AGENT,
    RESUME_AGENT,
    SPECIAL_PROMPTS,
    START_WORK_AGENT,
)
from .registry import OpenCodeSessionRegistry
from .session_manager import Request as ManagerRequest, IDLE

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Public DTOs
# ─────────────────────────────────────────────────────────────────────────────


class OpenCodeRequest(BaseModel):
    """The public-facing request envelope.

    The shape mirrors the Go JSON protocol fields
    (``server.go:215-218``):

        action    string
        session_id string
        payload   map[string]interface{}

    For PROMPT the payload must contain a ``parts`` array (with at least
    one ``text`` part). For COMMAND it must contain a ``command`` string.
    For INIT_SESSION/ABORT_SESSION it must contain ``project`` and
    ``session_name``. For ANSWER it must contain ``requestID`` and
    ``answers``.
    """

    model_config = {"extra": "ignore"}

    action: str
    """One of ``PING``, ``INIT_SESSION``, ``ABORT_SESSION``, ``PROMPT``,
    ``COMMAND``, ``ANSWER``, ``RESUME``, ``LIST_SESSIONS``, ``GET_SESSION``,
    ``GET_STATUS``."""
    session_id: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


class OpenCodeResponse(BaseModel):
    """Public-facing response envelope. Mirrors the Go response shape."""

    model_config = {"extra": "ignore"}

    status: str
    message: str = ""
    data: Any | None = None
    session_id: str | None = None


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────


def _normalize_text(text: str) -> str:
    """Strip a leading ``/`` and lowercase. Mirrors server.go:432-433.

    Args:
        text: Raw user input.

    Returns:
        Normalized form. ``"/start-work"`` → ``"start-work"``.
    """
    if text.startswith("/"):
        text = text[1:]
    return text.lower()


def _is_special_prompt(normalized: str) -> bool:
    """Return True if the prompt bypasses BUSY rejection.

    Mirrors the inline check at ``server.go:454-457``:

        isSpecial := false
        if normalizedText == "start-work" ||
           normalizedText == "continue" ||
           normalizedText == "abort" ||
           normalizedText == "retry" {
            isSpecial = true
        }

    The canonical list is also exposed as ``constants.SPECIAL_PROMPTS``.
    """
    return normalized in SPECIAL_PROMPTS


def _extract_target_text(action: str, payload: dict[str, Any]) -> str:
    """Pull the text/command string out of a PROMPT/COMMAND payload.

    Mirrors the inline extraction in ``server.go:417-430``:

        targetText := ""
        if req.Action == "PROMPT":
            if parts, ok := req.Payload["parts"].([]interface{}); ok {
                if partMap, ok := parts[0].(map[string]interface{}); ok {
                    if text, ok := partMap["text"].(string); ok {
                        targetText = text
                    }
                }
            }
        } else if req.Action == "COMMAND" {
            if cmd, ok := req.Payload["command"].(string); ok {
                targetText = cmd
            }
        }
    """
    if action == "PROMPT":
        parts = payload.get("parts")
        if isinstance(parts, list) and parts:
            first = parts[0]
            if isinstance(first, dict):
                text = first.get("text")
                if isinstance(text, str):
                    return text
        return ""
    if action == "COMMAND":
        cmd = payload.get("command")
        return cmd if isinstance(cmd, str) else ""
    return ""


# ─────────────────────────────────────────────────────────────────────────────
# Main entry point
# ─────────────────────────────────────────────────────────────────────────────


async def external_opencode_send_message(
    request: OpenCodeRequest,
    registry: OpenCodeSessionRegistry,
) -> OpenCodeResponse:
    """Dispatch a request to the appropriate OpenCode handler.

    Direct port of the ``switch req.Action { ... }`` block in
    ``server.go:230-507``. Returns an ``OpenCodeResponse`` describing
    the outcome. Failure modes (session not found, BUSY, invalid JSON)
    all return ``status="error"`` with a descriptive ``message``.

    Args:
        request: The request envelope.
        registry: The in-process session registry. The function does
            not acquire any long-lived locks; all synchronization
            happens inside the registry/manager.

    Returns:
        An ``OpenCodeResponse``.
    """
    action = request.action
    session_id = request.session_id or ""
    payload = request.payload or {}

    logger.debug("OpenCode request: action=%s session=%s", action, session_id)

    # ── PING (server.go:231-236) ─────────────────────────────────────────
    if action == "PING":
        from datetime import datetime, timezone

        return OpenCodeResponse(
            status="ok",
            message="PONG",
            data={"start_time": datetime.now(timezone.utc).isoformat()},
        )

    # ── LIST_SESSIONS (server.go:376-382) ─────────────────────────────────
    if action == "LIST_SESSIONS":
        try:
            sessions = await registry.list_sessions()
        except Exception as exc:
            return OpenCodeResponse(
                status="error",
                message=f"Failed to list sessions: {exc}",
            )
        return OpenCodeResponse(status="ok", data={"sessions": sessions})

    # ── GET_SESSION (server.go:384-408) ───────────────────────────────────
    if action == "GET_SESSION":
        project = payload.get("project", "")
        session_name = payload.get("session_name", "")
        if not project or not session_name:
            return OpenCodeResponse(
                status="error",
                message="project and session_name are required",
            )
        record = await registry.get_session_record(project, session_name)
        if record is None:
            return OpenCodeResponse(status="error", message="not found")
        # Load into memory if not present
        sid = record.get("id")
        if sid:
            manager = await registry.get_manager(sid)
            if manager is None:
                await registry.load_session_into_memory(sid)
        return OpenCodeResponse(status="ok", data={"session": record})

    # ── INIT_SESSION (server.go:298-336) ──────────────────────────────────
    if action == "INIT_SESSION":
        project = payload.get("project", "")
        session_name = payload.get("session_name", "")
        working_dir = payload.get("working_dir", "")
        if not project or not session_name:
            return OpenCodeResponse(
                status="error",
                message="project and session_name are required",
            )
        try:
            new_id = await registry.create_new(
                project=project,
                session_name=session_name,
                working_dir=working_dir or "",
            )
        except Exception as exc:
            return OpenCodeResponse(status="error", message=str(exc))
        return OpenCodeResponse(status="ok", message="", session_id=new_id)

    # ── ABORT_SESSION (server.go:338-374) ─────────────────────────────────
    if action == "ABORT_SESSION":
        project = payload.get("project", "")
        session_name = payload.get("session_name", "")
        if not project or not session_name:
            return OpenCodeResponse(
                status="error",
                message="project and session_name are required",
            )
        result = await registry.abort_session(project, session_name)
        return OpenCodeResponse(**result)

    # ── GET_STATUS (server.go:258-268) ────────────────────────────────────
    if action == "GET_STATUS":
        if not session_id:
            return OpenCodeResponse(status="error", message="session_id is required")
        manager = await registry.get_manager(session_id)
        if manager is None:
            return OpenCodeResponse(status="error", message="Session not found")
        # server.go:263: SyncStateWithOpenCode first to ensure accuracy
        snapshot = await manager.sync_state_with_open_code()
        return OpenCodeResponse(status="ok", data=snapshot)

    # ── PROMPT / COMMAND / ANSWER / RESUME (server.go:410-507) ───────────
    if action in ("PROMPT", "COMMAND", "ANSWER", "RESUME"):
        if not session_id:
            return OpenCodeResponse(status="error", message="session_id is required")
        manager = await registry.get_manager(session_id)
        if manager is None:
            # server.go:503-506: try loading on demand (only for session-bound
            # actions; the Go server does not load-on-demand here, but it's
            # a sensible fallback for the Python port).
            manager = await registry.load_session_into_memory(session_id)
        if manager is None:
            return OpenCodeResponse(status="error", message="Session not found")

        # ── Extract text for special handling (server.go:417-430) ─────
        target_text = _extract_target_text(action, payload)
        normalized = _normalize_text(target_text)

        # ── /start-work: lock agent to "atlas" (server.go:436-444) ────
        if normalized == "start-work":
            record = await registry.find_by_id(session_id)
            if record is not None:
                project = record.get("project", "")
                session_name = record.get("session_name", "")
                if project and session_name:
                    await registry.handle_start_work(
                        project=project,
                        session_name=session_name,
                        agent=START_WORK_AGENT,
                    )

        # ── BUSY rejection (server.go:447-463) ──────────────────────────
        if action == "PROMPT":
            # server.go:448-462
            snapshot = manager.get_snapshot()
            current_state = snapshot.get("state")
            # `isSpecial` lets "start-work" / "continue" / "abort" / "retry"
            # through even when the worker is busy.
            is_special = _is_special_prompt(normalized)
            if current_state == "BUSY" and not is_special:
                return OpenCodeResponse(
                    status="error",
                    message=(
                        "Session is busy. Please wait for the previous "
                        "message result before sending a new message."
                    ),
                )

        # ── Agent lock override (server.go:465-472) ────────────────────
        if action in ("PROMPT", "COMMAND"):
            record = await registry.find_by_id(session_id)
            if record is not None:
                if record.get("is_agent_locked") and record.get("last_agent"):
                    # server.go:468: override the agent in the payload
                    payload["agent"] = record["last_agent"]
                    logger.info(
                        "Using locked agent '%s' for session %s",
                        record["last_agent"],
                        session_id,
                    )

        # ── Special prompt: override agent for continue/retry (server.go equivalent)
        # The Go server doesn't explicitly override the agent for
        # "continue"/"retry", but the original behaviour is to send the
        # resume hardcoded prompt. We do the same: route the request
        # through the RESUME path which uses the hardcoded prompt.
        if action == "PROMPT" and normalized in ("continue", "retry"):
            # Convert to RESUME
            action = "RESUME"
            logger.info("routing %s as RESUME for session %s", normalized, session_id)

        # ── Convert payload to typed request (server.go:474-490) ───────
        internal_payload: Any = None
        if action == "PROMPT":
            try:
                internal_payload = PromptRequest.model_validate(payload)
            except Exception as exc:
                return OpenCodeResponse(
                    status="error",
                    message=f"invalid PROMPT payload: {exc}",
                )
        elif action == "COMMAND":
            try:
                internal_payload = CommandRequest.model_validate(payload)
            except Exception as exc:
                return OpenCodeResponse(
                    status="error",
                    message=f"invalid COMMAND payload: {exc}",
                )
        elif action == "ANSWER":
            try:
                internal_payload = AnswerRequest.model_validate(payload)
            except Exception as exc:
                return OpenCodeResponse(
                    status="error",
                    message=f"invalid ANSWER payload: {exc}",
                )
        # RESUME has no payload conversion (server.go:494-496)

        # ── Submit (server.go:492-501) ──────────────────────────────────
        manager_req = ManagerRequest(
            type_=action,
            payload=internal_payload,
        )
        manager.submit_request(manager_req)
        return OpenCodeResponse(status="ok", message="Request submitted")

    # ── Default: unknown action (server.go:228) ───────────────────────────
    return OpenCodeResponse(status="error", message="Unknown action")
