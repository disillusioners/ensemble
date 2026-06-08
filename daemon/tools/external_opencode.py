"""Native Python tools for opencode session orchestration.

Replaces the Go binary `opencode_skill`. All tools use `external_opencode_*`
prefix and category `external_opencode`. The factory function
`create_opencode_tools()` follows the same closure-injection pattern as
`daemon/tools/knowledge_tools.py`.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from langchain_core.tools import tool

from daemon.opencode.constants import COUNCIL_HINT, POLL_INTERVAL_S
from daemon.opencode.server import (
    external_opencode_send_message as _server_send_message,  # Blocker 1 (Rev 4): alias to avoid name collision with LangChain tool of same name
)
from daemon.services.context_injection import get_shared_context
from daemon.services.keyword_extraction import (
    _heuristic_keywords,
    _normalize_keywords,
    extract_keywords,
)
from ._tool_registry import register_tool_category

logger = logging.getLogger(__name__)

# Control messages that bypass auto-preload (dispatch signals, not tasks).
_OPENCODE_CONTROL_MESSAGES = frozenset({"continue", "retry", "abort", "start-work"})

if TYPE_CHECKING:
    from daemon.manager import InstanceManager
    from daemon.opencode.server import OpenCodeRequest, OpenCodeResponse
    from daemon.opencode.registry import OpenCodeSessionRegistry

CATEGORY_NAME = "OpenCode"
CATEGORY_DOC = """\
OpenCode session orchestration tools for controlling the OpenCode AI coding tool.

These tools communicate with an EXTERNAL system (OpenCode at http://127.0.0.1:4095).
They manage session lifecycle: create, send messages, check status, wait for results,
answer interactive questions, resume, and abort.

**Auto-Preload Context**: `external_opencode_send_message` automatically prepends
the top-matching shared-context files (scored against a focused query) before
sending. The query is resolved through a 3-step chain: the caller-supplied
`related_context_keywords` first, then an LLM extract of the outgoing message
(uses `model_keywords`, 40s timeout), then a local heuristic. This saves the
remote agent from having to re-discover the caller's project context. Control
commands (`continue`, `retry`, `abort`, `start-work`) bypass auto-preload.

**Workflow**:
1. `external_opencode_init_session` — Create or replace a named session
2. `external_opencode_send_message` — Send prompt (fire-and-forget)
3. `external_opencode_wait_for_result` — Block until completion (or `external_opencode_wait_any` for parallel)
4. `external_opencode_get_status` — Non-blocking status check
5. `external_opencode_answer_question` — Answer interactive questions
6. `external_opencode_resume_session` — Resume after timeout
7. `external_opencode_abort_session` — Abort and reset to IDLE
"""


def create_opencode_tools(
    manager: "InstanceManager",
    current_instance_id: str,
) -> list:
    """Create opencode orchestration tools with injected manager reference.
    
    Args:
        manager: The InstanceManager instance (provides opencode_registry).
        current_instance_id: The ID of the current instance (unused for now
            but kept for pattern parity with knowledge_tools).

    Returns:
        List of 8 tool functions.
    """

    def _get_registry() -> "OpenCodeSessionRegistry":
        """Get the opencode session registry from manager (Phase 3 wiring)."""
        registry = getattr(manager, 'opencode_registry', None)
        if registry is None:
            raise RuntimeError(
                "OpenCode session registry not initialized. "
                "Check daemon startup logs for 'opencode' configuration."
            )
        return registry
    
    async def _send(request: "OpenCodeRequest") -> "OpenCodeResponse":
        """Dispatch an OpenCodeRequest and return the response."""
        # Blocker 1 (Rev 4): Call the server dispatcher via the aliased import,
        # NOT the local LangChain tool `external_opencode_send_message` which
        # would receive an OpenCodeRequest as a positional arg (wrong function).
        return await _server_send_message(request, _get_registry())
    
    def _format_response(resp: "OpenCodeResponse") -> str:
        """Format an OpenCodeResponse for agent consumption."""
        if resp.status == "ok":
            return resp.message or f"[OK] {resp.data or ''}"
        return f"[ERROR] {resp.message}"

    def _format_timeout(
        last_resp: "OpenCodeResponse | None",
        timeout: int,
    ) -> str:
        """Build a TIMEOUT message that includes the last observed snapshot.

        On timeout the session may still be BUSY. The snapshot contains
        ``latest_response`` (the most recent opencode message), which gives
        the calling agent visibility into in-flight progress without having
        to issue a separate ``external_opencode_get_status`` call.
        """
        fallback = (
            f"[TIMEOUT] Session did not complete within {timeout}s. "
            "Use external_opencode_resume_session() to continue."
        )
        if last_resp is None or last_resp.status != "ok":
            return fallback
        data = last_resp.data or {}
        state = data.get("state", "UNKNOWN")
        latest = data.get("latest_response")
        parts: list[str] = [
            f"[TIMEOUT] Session did not complete within {timeout}s.",
            f"[STATE] {state}",
        ]
        if latest:
            rendered = (
                latest.get("result", latest)
                if isinstance(latest, dict)
                else latest
            )
            parts.append("[LAST MESSAGE]")
            parts.append(str(rendered))
        parts.append(
            "Use external_opencode_resume_session() to continue or "
            "external_opencode_get_status() for more details."
        )
        return "\n".join(parts)

    async def _preload_shared_context(
        message: str,
        related_context_keywords: list[str] | None = None,
    ) -> str:
        """Auto-match shared context files against the outgoing message.

        Mirrors ``explore()``'s preload behavior: resolves the caller's
        ``context_key`` from the instance tree, scores existing shared-context
        files against a focused query, and returns a tiered injection string
        (capped at ``INJECTION_TOKEN_CAP`` inside ``get_shared_context``).

        The query is resolved through a 3-step chain so the matcher always
        sees something better than the full raw prompt:

        1. **Agent-provided keywords** (``related_context_keywords``) — best
           path; the caller knows its own intent.
        2. **LLM extraction** — one-shot call to ``model_keywords`` (defaults
           to ``model``; ops typically pin to ``"quick"``). Bounded by
           ``KEYWORD_EXTRACTION_TIMEOUT_S`` (40s). Best-effort, never raises.
        3. **Deterministic heuristic** — pure-Python fallback (backtick terms,
           CamelCase tokens, first line) for when the LLM is unavailable.

        The prepended hint line uses the hosted MCP tool names
        (``ensemble_context_list`` / ``ensemble_context_read``) because the
        remote opencode session reaches the context directory through MCP,
        not through the internal LangChain tool category.

        Project metadata (``project_id`` / ``project_name``) and the
        project's critical notes are resolved from the current instance and
        forwarded to the external MCP RAG hint so the remote agent can
        scope ``ensemble_kb_*`` tool calls and respect pinned warnings.

        Args:
            message: The outgoing prompt — used as the fallback extraction
                source when the agent does not provide keywords.
            related_context_keywords: Optional list of short topic keywords
                the calling agent knows are relevant. Preferred path; the
                heuristic and LLM layers only run when this is ``None`` /
                empty after normalization.

        Returns:
            Injection string to prepend to the message, or ``""`` to skip
            injection (no context, no match, or any failure). Never raises.
        """
        try:
            context_key = manager._instance_repository.get_tree_root_id(current_instance_id)
            if not context_key:
                context_key = current_instance_id
        except Exception:
            context_key = current_instance_id

        # Resolve project_id from the current instance, then look up the
        # project name and critical notes. Every step is best-effort — the
        # preload must never raise.
        project_id: str | None = None
        project_name: str | None = None
        critical_notes: list[dict] = []
        try:
            instance_meta = manager._instance_repository.get(current_instance_id)
            if instance_meta is not None:
                project_id = getattr(instance_meta, "project_id", None)
        except Exception:
            pass
        if project_id and hasattr(manager, "_project_repository"):
            try:
                proj = manager._project_repository.get(project_id)
                if proj is not None:
                    project_name = getattr(proj, "name", None)
            except Exception:
                pass
            try:
                notes = manager._project_repository.list_critical_notes(project_id)
                critical_notes = [n.to_dict() for n in notes]
            except Exception:
                pass

        # 3-step keyword resolution: agent-provided → LLM → heuristic → skip.
        keywords = _normalize_keywords(related_context_keywords)
        if keywords:
            logger.debug(
                "[OpenCode] Using %d agent-provided keyword(s) for context preload",
                len(keywords),
            )
        else:
            keywords = await extract_keywords(message)
            if keywords:
                logger.debug(
                    "[OpenCode] LLM extracted %d keyword(s) for context preload",
                    len(keywords),
                )
            else:
                keywords = _heuristic_keywords(message)
                if keywords:
                    logger.debug(
                        "[OpenCode] Heuristic extracted %d keyword(s) for context preload",
                        len(keywords),
                    )
                else:
                    logger.debug(
                        "[OpenCode] No keywords resolved; skipping context preload",
                    )
                    return ""

        query = " ".join(keywords)

        try:
            injection = await asyncio.to_thread(
                get_shared_context,
                context_key,
                query,
                "external",
                project_id=project_id,
                project_name=project_name,
                critical_notes=critical_notes or None,
            )
            return injection or ""
        except Exception as e:
            logger.debug("[OpenCode] Preload shared context failed: %s", e)
            return ""

    # ── Tool 1: Init Session ────────────────────────────────────────
    
    @register_tool_category("external_opencode")
    @tool
    async def external_opencode_init_session(
        project: str,
        session_name: str,
        working_dir: str,
    ) -> str:
        """Initialize a new opencode session. Replaces existing if one exists.
        
        Use tool_help("external_opencode_init_session") for details.
        """
        from daemon.opencode.server import OpenCodeRequest
        
        req = OpenCodeRequest(
            action="INIT_SESSION",
            payload={"project": project, "session_name": session_name, "working_dir": working_dir},
        )
        resp = await _send(req)
        if resp.status == "ok":
            return f"[SUCCESS] Session '{session_name}' initialized with ID {resp.session_id} in {working_dir}"
        return _format_response(resp)
    
    external_opencode_init_session._full_doc_ = """\
Initialize a new opencode session for a project. Replaces existing session if one exists.

Args:
    project: Project identifier (e.g. "myapp")
    session_name: Human-readable name (e.g. "feature-login")
    working_dir: Absolute path to the working directory

Returns:
    Success message with session ID, or error message.

The session ID is auto-generated by OpenCode. Use this session name in all
subsequent calls to other opencode_* tools.

Conflicts with existing sessions are resolved by aborting the old remote
session and deleting its registry entry before creating the new one.
"""
    
    # ── Tool 2: Send Message ────────────────────────────────────────
    
    @register_tool_category("external_opencode")
    @tool
    async def external_opencode_send_message(
        project: str,
        session_name: str,
        message: str,
        agent: str = "orchestrator",
        model: str | None = None,
        council: bool = False,
        related_context_keywords: list[str] | None = None,
    ) -> str:
        """Send a prompt to an opencode session (fire-and-forget).

        Use tool_help("external_opencode_send_message") for details.
        """
        from daemon.opencode.server import OpenCodeRequest

        # Parse model "provider/model" format
        model_dict = {"providerID": "litellm", "modelID": "coding"}
        if model and "/" in model:
            provider, model_id = model.split("/", 1)
            model_dict = {"providerID": provider, "modelID": model_id}
        elif model:
            model_dict = {"providerID": "litellm", "modelID": model}

        # Look up session_id from registry via PUBLIC delegate (Issue 4)
        registry = _get_registry()
        record = await registry.get_session_record(project, session_name)
        if record is None:
            return f"[ERROR] Session '{session_name}' not found in project '{project}'"
        session_id = record.get("id", "")

        # Council mode appends the COUNCIL_HINT trailer to the prompt so the
        # receiving agent is nudged to delegate to the @council subagent-tool
        # for critical-path review. Mirrors the old Go binary's --council flag.
        full_text = message + COUNCIL_HINT if council else message

        # Auto-preload shared context (skipped for control messages which are
        # dispatch signals, not tasks). Mirrors explore()'s context injection.
        if message.strip().lower() not in _OPENCODE_CONTROL_MESSAGES:
            injection = await _preload_shared_context(
                message, related_context_keywords,
            )
            if injection:
                full_text = f"{injection}\n\n{full_text}"
                logger.info(
                    "[OpenCode] Preloaded shared context (%d chars) into message for %s:%s",
                    len(injection), project, session_name,
                )

        req = OpenCodeRequest(
            action="PROMPT",
            session_id=session_id,
            payload={
                "agent": agent,
                "model": model_dict,
                "parts": [{"type": "text", "text": full_text}],
            },
        )
        resp = await _send(req)
        if resp.status == "ok":
            return f"[SUBMITTED] Message sent. Run external_opencode_get_status('{project}', '{session_name}') to check progress."
        return _format_response(resp)
    
    external_opencode_send_message._full_doc_ = """\
Send a prompt to an opencode session (fire-and-forget).

Args:
    project: Project identifier
    session_name: Session name
    message: The text message to send
    agent: Agent name (default "orchestrator")
    model: Optional model in "provider/model" format (default "litellm/coding")
    council: If True, append the COUNCIL_HINT trailer to the prompt so the
        receiving agent delegates critical-path work to the @council
        subagent-tool. Replaces the old Go binary's --council flag.
    related_context_keywords: Optional list of short topic keywords (3-8)
        that describe the context files this task is likely to need.
        Strongly recommended for long, prose-heavy prompts where full-message
        matching would dilute the score. When omitted (or empty), the daemon
        falls back to a one-shot LLM extract (uses `model_keywords`, default
        40s timeout) and then a local heuristic. Pass `None` to defer to the
        fallback chain.

Returns:
    Submitted confirmation or error.

Auto-Preload Context:
    Before sending, the caller's shared-context directory is scanned and the
    top-matching files (scored against the resolved query) are prepended to
    the prompt, capped at INJECTION_TOKEN_CAP (2000 tokens). The query is
    resolved through a 3-step chain:

    1. `related_context_keywords` (agent-provided) — best path.
    2. LLM extract from `message` (uses `model_keywords`; 40s timeout).
    3. Local heuristic extract from `message` (backtick terms, CamelCase,
       first line, high-signal tokens).

    This saves the remote agent from re-discovering context the caller
    already has. Control messages ("continue", "retry", "abort", "start-work")
    bypass auto-preload because they are dispatch signals, not tasks. Failures
    in the preload step are logged and the message is sent unchanged
    (graceful degradation).

Special prompts (bypass BUSY check):
- "start-work" — also locks agent to "atlas"
- "continue" — routes through RESUME (hardcoded prompt)
- "retry" — routes through RESUME
- "abort" — bypasses BUSY (use external_opencode_abort_session for the real action)
"""
    
    # ── Tool 3: Get Status ──────────────────────────────────────────
    
    @register_tool_category("external_opencode")
    @tool
    async def external_opencode_get_status(
        project: str,
        session_name: str,
    ) -> str:
        """Get current status of an opencode session (non-blocking).
        
        Use tool_help("external_opencode_get_status") for details.
        """
        from daemon.opencode.server import OpenCodeRequest
        
        registry = _get_registry()
        record = await registry.get_session_record(project, session_name)
        if record is None:
            return f"[ERROR] Session '{session_name}' not found in project '{project}'"
        session_id = record.get("id", "")
        
        req = OpenCodeRequest(action="GET_STATUS", session_id=session_id)
        resp = await _send(req)
        if resp.status == "ok":
            data = resp.data or {}
            state = data.get("state", "UNKNOWN")
            response = data.get("latest_response")
            questions = data.get("questions", [])
            
            output = [
                f"State: {state}",
                f"Last Activity: {record.get('last_activity', '')}",
                "",
                "Latest Response:",
                str(response) if response else "(none)",
            ]
            if questions:
                output.append("")
                output.append("Questions:")
                for q in questions:
                    qid = q.get("id", "") if hasattr(q, "get") else getattr(q, "id", "")
                    qtext = q.get("questions", []) if hasattr(q, "get") else getattr(q, "questions", [])
                    output.append(f"  [?] {qid}: {qtext}")
            return "\n".join(output)
        return _format_response(resp)
    
    external_opencode_get_status._full_doc_ = """\
Get current status of an opencode session (non-blocking).

Args:
    project: Project identifier
    session_name: Session name

Returns:
    Formatted status including state, last activity, latest response, and pending questions.
"""
    
    # ── Tool 4: Wait for Result ─────────────────────────────────────
    
    @register_tool_category("external_opencode")
    @tool
    async def external_opencode_wait_for_result(
        project: str,
        session_name: str,
        timeout: int = 600,
    ) -> str:
        """Block until an opencode session completes.
        
        Use tool_help("external_opencode_wait_for_result") for details.
        """
        registry = _get_registry()
        record = await registry.get_session_record(project, session_name)
        if record is None:
            return f"[ERROR] Session '{session_name}' not found in project '{project}'"
        session_id = record.get("id", "")
        
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        last_resp: "OpenCodeResponse | None" = None
        while loop.time() < deadline:
            from daemon.opencode.server import OpenCodeRequest
            req = OpenCodeRequest(action="GET_STATUS", session_id=session_id)
            resp = await _send(req)
            if resp.status == "ok":
                last_resp = resp
                data = resp.data or {}
                state = data.get("state", "UNKNOWN")
                if state == "IDLE":
                    return f"[COMPLETED] Session completed.\n{_format_response(resp)}"
                if state == "WAITING_FOR_INPUT":
                    return f"[WAITING_FOR_INPUT] Session needs input. Use external_opencode_get_status() to see questions."
            await asyncio.sleep(POLL_INTERVAL_S)

        return _format_timeout(last_resp, timeout)
    
    external_opencode_wait_for_result._full_doc_ = """\
Block until an opencode session completes (polls every 30s, default max 10min).

Args:
    project: Project identifier
    session_name: Session name
    timeout: Max wait in seconds (default 600 = 10 min)

Returns:
    [COMPLETED] message with response data, [WAITING_FOR_INPUT] with question pointer,
    or [TIMEOUT] message that includes the last observed state and latest_response so the
    caller can see in-flight progress without a separate status call.
"""
    
    # ── Tool 5: Wait Any ────────────────────────────────────────────
    
    @register_tool_category("external_opencode")
    @tool
    async def external_opencode_wait_any(
        sessions: list[dict[str, str]],
        timeout: int = 600,
    ) -> str:
        """Block until ANY of multiple opencode sessions completes.
        
        Use tool_help("external_opencode_wait_any") for details.
        """
        if not sessions:
            return "[ERROR] sessions list is empty"
        
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        registry = _get_registry()
        
        # Resolve session_id for each — in parallel via asyncio.gather (Issue 5)
        async def _resolve(s: dict[str, str]) -> dict[str, str] | None:
            project = s.get("project", "")
            session_name = s.get("session_name", "")
            record = await registry.get_session_record(project, session_name)
            if record is None:
                return None
            return {
                "project": project,
                "session_name": session_name,
                "session_id": record.get("id", ""),
            }
        
        resolved = await asyncio.gather(*[_resolve(s) for s in sessions])
        session_data = [r for r in resolved if r is not None]
        
        if not session_data:
            return "[ERROR] No valid sessions found"
        
        while loop.time() < deadline:
            from daemon.opencode.server import OpenCodeRequest
            
            # Poll all sessions in PARALLEL (Issue 5: was sequential → now gather)
            async def _check_status(sd: dict[str, str]) -> tuple[dict[str, str], OpenCodeResponse]:
                req = OpenCodeRequest(action="GET_STATUS", session_id=sd["session_id"])
                resp = await _send(req)
                return (sd, resp)
            
            results = await asyncio.gather(*[_check_status(sd) for sd in session_data])
            
            completed = []
            still_running = []
            for sd, resp in results:
                if resp.status == "ok":
                    state = (resp.data or {}).get("state", "UNKNOWN")
                    if state in ("IDLE", "WAITING_FOR_INPUT"):
                        completed.append((sd, resp))
                    else:
                        still_running.append(sd)
            
            if completed:
                lines = [f"[SUMMARY] {len(completed)}/{len(session_data)} sessions completed", ""]
                for sd in session_data:
                    marker = "✓" if any(c[0] == sd for c in completed) else "..."
                    lines.append(f"  {marker} {sd['project']}:{sd['session_name']}")
                lines.append("")
                lines.append("─" * 60)
                lines.append("  COMPLETED RESPONSES")
                lines.append("─" * 60)
                for sd, resp in completed:
                    lines.append(f"\n[{sd['project']}:{sd['session_name']}]")
                    lines.append(_format_response(resp))
                return "\n".join(lines)
            
            await asyncio.sleep(POLL_INTERVAL_S)

        return f"[TIMEOUT] No session completed within {timeout}s. Use external_opencode_get_status() to check each."
    
    external_opencode_wait_any._full_doc_ = """\
Block until ANY of multiple opencode sessions completes (polls every 30s).

Args:
    sessions: List of {"project": "...", "session_name": "..."} objects (max 3 recommended)
    timeout: Max wait in seconds (default 600 = 10 min)

Returns:
    Summary with completed sessions and their responses.
"""
    
    # ── Tool 6: Answer Question ─────────────────────────────────────
    
    @register_tool_category("external_opencode")
    @tool
    async def external_opencode_answer_question(
        project: str,
        session_name: str,
        request_id: str,
        answers: list[str],
    ) -> str:
        """Answer interactive questions from an opencode session.
        
        Use tool_help("external_opencode_answer_question") for details.
        """
        from daemon.opencode.server import OpenCodeRequest
        
        registry = _get_registry()
        record = await registry.get_session_record(project, session_name)  # Issue 4: public delegate
        if record is None:
            return f"[ERROR] Session '{session_name}' not found in project '{project}'"
        session_id = record.get("id", "")
        
        req = OpenCodeRequest(
            action="ANSWER",
            session_id=session_id,
            payload={
                "requestID": request_id,  # camelCase per OpenCode API
                "answers": [answers],  # nested: one row per question, multiple answers per row
            },
        )
        resp = await _send(req)
        if resp.status == "ok":
            return f"[ANSWERED] Submitted answers for request {request_id}: {answers}"
        return _format_response(resp)
    
    external_opencode_answer_question._full_doc_ = """\
Answer interactive questions from an opencode session.

Args:
    project: Project identifier
    session_name: Session name
    request_id: The question ID from external_opencode_get_status()
    answers: List of answer strings (one per sub-question)

Returns:
    Confirmation of submitted answers.

Use external_opencode_get_status() to see pending questions and their IDs.
"""
    
    # ── Tool 7: Resume Session ──────────────────────────────────────
    
    @register_tool_category("external_opencode")
    @tool
    async def external_opencode_resume_session(
        project: str,
        session_name: str,
    ) -> str:
        """Resume a timed-out opencode session.
        
        Use tool_help("external_opencode_resume_session") for details.
        """
        from daemon.opencode.server import OpenCodeRequest
        
        registry = _get_registry()
        record = await registry.get_session_record(project, session_name)  # Issue 4: public delegate
        if record is None:
            return f"[ERROR] Session '{session_name}' not found in project '{project}'"
        session_id = record.get("id", "")
        
        req = OpenCodeRequest(action="RESUME", session_id=session_id)
        resp = await _send(req)
        if resp.status == "ok":
            return f"[RESUMED] Session resumed. Use external_opencode_wait_for_result() to wait for completion."
        return _format_response(resp)
    
    external_opencode_resume_session._full_doc_ = """\
Resume a timed-out opencode session.

Sends a hardcoded resume prompt: agent="orchestrator", model="litellm/coding", text="resume".

Args:
    project: Project identifier
    session_name: Session name

Returns:
    Confirmation or error.
"""
    
    # ── Tool 8: Abort Session ───────────────────────────────────────
    
    @register_tool_category("external_opencode")
    @tool
    async def external_opencode_abort_session(
        project: str,
        session_name: str,
    ) -> str:
        """Abort a running opencode session and reset to IDLE.
        
        Use tool_help("external_opencode_abort_session") for details.
        """
        from daemon.opencode.server import OpenCodeRequest
        
        req = OpenCodeRequest(
            action="ABORT_SESSION",
            payload={"project": project, "session_name": session_name},
        )
        resp = await _send(req)
        if resp.status == "ok":
            return f"[ABORTED] {resp.message or 'Session aborted and ready for new input.'}"
        return _format_response(resp)
    
    external_opencode_abort_session._full_doc_ = """\
Abort a running opencode session and reset to IDLE.

Best-effort remote abort (logs failures), 3-second settle delay, then resets
local state via abort_task().

Args:
    project: Project identifier
    session_name: Session name

Returns:
    Confirmation or error.
"""
    
    return [
        external_opencode_init_session,
        external_opencode_send_message,
        external_opencode_get_status,
        external_opencode_wait_for_result,
        external_opencode_wait_any,
        external_opencode_answer_question,
        external_opencode_resume_session,
        external_opencode_abort_session,
    ]
