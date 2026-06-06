# Phase 2: Tool Definitions & Factory Integration

## Objective
Create 8 LangChain tool definitions that wrap `daemon/opencode/server.py:external_opencode_send_message`, plus the factory function `create_opencode_tools()`. Wire the tools into `create_instance_tools()`.

## Coupling
- **Depends on**: Phase 1 (production code in `daemon/opencode/`)
- **Coupling type**: tight
- **Shared files with other phases**:
  - `daemon/tools/external_opencode.py` (NEW)
  - `daemon/tools/_tool_registry.py` (MODIFY)
  - `daemon/tools/instance.py` (MODIFY)
- **Why this coupling**: Tools call `external_opencode_send_message(OpenCodeRequest, registry)` and need a reference to the `OpenCodeSessionRegistry` (from `daemon/manager.py` via closure injection).

## Context
- Phase 1 production code: `daemon/opencode/server.py` provides `external_opencode_send_message(OpenCodeRequest, OpenCodeSessionRegistry) -> OpenCodeResponse`
- The `OpenCodeSessionRegistry` is a singleton on the manager (set in Phase 3)
- Follow the factory/closure pattern from `daemon/tools/knowledge_tools.py`

## Tasks

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 1 | Create `daemon/tools/external_opencode.py` with `CATEGORY_NAME`, `CATEGORY_DOC`, factory | Module skeleton matching `knowledge_tools.py` | `daemon/tools/external_opencode.py` (NEW) |
| 2 | `external_opencode_init_session` tool | Wraps `OpenCodeRequest(action="INIT_SESSION", payload={project, session_name, working_dir})` | Same file |
| 3 | `external_opencode_send_message` tool | Wraps `OpenCodeRequest(action="PROMPT", payload={agent, model, parts})` | Same file |
| 4 | `external_opencode_get_status` tool | Wraps `OpenCodeRequest(action="GET_STATUS", session_id=...)` | Same file |
| 5 | `external_opencode_wait_for_result` tool | Polls `get_status` every 30s up to 10min; returns formatted result | Same file |
| 6 | `external_opencode_wait_any` tool | Polls multiple sessions; returns when any completes | Same file |
| 7 | `external_opencode_answer_question` tool | Wraps `OpenCodeRequest(action="ANSWER", session_id=..., payload={requestID, answers})` | Same file |
| 8 | `external_opencode_resume_session` tool | Wraps `OpenCodeRequest(action="RESUME", session_id=...)` | Same file |
| 9 | `external_opencode_abort_session` tool | Wraps `OpenCodeRequest(action="ABORT_SESSION", payload={project, session_name})` | Same file |
| 10 | Set `_full_doc_` on all 8 tools | Long-form docs for `tool_help()` | Same file |
| 11 | Register in `CATEGORY_MODULES` | Add `"external_opencode": "daemon.tools.external_opencode"` | `daemon/tools/_tool_registry.py` (MODIFY) |
| 12 | Wire into `create_instance_tools()` | Call `create_opencode_tools()` **OUTSIDE** the `is_rag_enabled()` block | `daemon/tools/instance.py` (MODIFY) |

## Key Files

### NEW: `daemon/tools/external_opencode.py`

```python
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

from daemon.opencode.server import (
    external_opencode_send_message as _server_send_message,  # Blocker 1 (Rev 4): alias to avoid name collision with LangChain tool of same name
)
from ._tool_registry import register_tool_category

if TYPE_CHECKING:
    from daemon.manager import InstanceManager
    from daemon.opencode.server import OpenCodeRequest, OpenCodeResponse
    from daemon.opencode.registry import OpenCodeSessionRegistry

logger = logging.getLogger(__name__)

CATEGORY_NAME = "OpenCode"
CATEGORY_DOC = """\
OpenCode session orchestration tools for controlling the OpenCode AI coding tool.

These tools communicate with an EXTERNAL system (OpenCode at http://127.0.0.1:4095).
They manage session lifecycle: create, send messages, check status, wait for results,
answer interactive questions, resume, and abort.

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
        manager: The InstanceManager instance (provides _opencode_registry).
        current_instance_id: The ID of the current instance (unused for now
            but kept for pattern parity with knowledge_tools).
    
    Returns:
        List of 8 tool functions.
    """
    
    def _get_registry() -> "OpenCodeSessionRegistry":
        """Get the opencode session registry from manager (Phase 3 wiring)."""
        registry = getattr(manager, '_opencode_registry', None)
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
        
        req = OpenCodeRequest(
            action="PROMPT",
            session_id=session_id,
            payload={
                "agent": agent,
                "model": model_dict,
                "parts": [{"type": "text", "text": message}],
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

Returns:
    Submitted confirmation or error.

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
            response = data.get("latest_response", "Processing...")
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
                    output.append(f"  [?] {q.get('id', '')}: {q.get('questions', [])}")
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
        while loop.time() < deadline:
            from daemon.opencode.server import OpenCodeRequest
            req = OpenCodeRequest(action="GET_STATUS", session_id=session_id)
            resp = await _send(req)
            if resp.status == "ok":
                data = resp.data or {}
                state = data.get("state", "UNKNOWN")
                if state == "IDLE":
                    return f"[COMPLETED] Session completed.\n{_format_response(resp)}"
                if state == "WAITING_FOR_INPUT":
                    return f"[WAITING_FOR_INPUT] Session needs input. Use external_opencode_get_status() to see questions."
            await asyncio.sleep(30)  # Match Go's POLL_INTERVAL_S
        
        return f"[TIMEOUT] Session did not complete within {timeout}s. Use external_opencode_resume_session() to continue."
    
    external_opencode_wait_for_result._full_doc_ = """\
Block until an opencode session completes (polls every 30s, default max 10min).

Args:
    project: Project identifier
    session_name: Session name
    timeout: Max wait in seconds (default 600 = 10 min)

Returns:
    [COMPLETED] message with response data, [WAITING_FOR_INPUT] with question pointer, or [TIMEOUT] message.
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
            
            await asyncio.sleep(30)
        
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


# Import for the type hint in factory
from daemon.opencode.server import external_opencode_send_message
```

### MODIFY: `daemon/tools/_tool_registry.py`

Add to `CATEGORY_MODULES`:
```python
CATEGORY_MODULES: dict[str, str | list[str]] = {
    ...  # existing entries
    "external_opencode": "daemon.tools.external_opencode",
}
```

### MODIFY: `daemon/tools/instance.py`

Add import and wire factory call (OUTSIDE the `is_rag_enabled()` block):
```python
# Top of file — add import:
from .external_opencode import create_opencode_tools

# Inside create_instance_tools() function — after the RAG/knowledge block (~line 660),
# BEFORE the MCP tools block (which needs to come after so MCP help expansion works):

# ── OpenCode tools (external system integration, always available) ──
# NOTE: NOT inside the is_rag_enabled() block — these are always available.
opencode_tool_list = create_opencode_tools(manager, current_instance_id)
tools.extend(opencode_tool_list)
```

## Tool Specifications

| Tool | Args | Returns |
|------|------|---------|
| `external_opencode_init_session` | `project: str, session_name: str, working_dir: str` | Success message + session_id |
| `external_opencode_send_message` | `project, session_name, message, agent="orchestrator", model=None` | Submitted confirmation |
| `external_opencode_get_status` | `project, session_name` | Formatted status (state, activity, response, questions) |
| `external_opencode_wait_for_result` | `project, session_name, timeout=600` | Completed/Waiting/Timeout message |
| `external_opencode_wait_any` | `sessions: list[dict], timeout=600` | Summary + completed responses |
| `external_opencode_answer_question` | `project, session_name, request_id, answers: list[str]` | Confirmation |
| `external_opencode_resume_session` | `project, session_name` | Resumed confirmation |
| `external_opencode_abort_session` | `project, session_name` | Aborted confirmation |

## Constraints
- All tools are async (use `asyncio`)
- Tools access `manager._opencode_registry` via closure (set in Phase 3)
- **Blocker 1 (Rev 4)**: The server dispatcher is imported as `_server_send_message` at module level to avoid name collision with the `@tool`-decorated `external_opencode_send_message` function defined in the closure. `_send()` calls `_server_send_message()`, NOT the local tool.
- **Issue 4**: ALL tools use `await registry.get_session_record(project, session_name)` — public delegate, NOT `registry._repository.get()`. Verified across all 8 tools.
- **Issue 5**: `external_opencode_wait_any` uses `asyncio.gather()` for parallel status checks (not sequential loop)
- **Issue 7**: Use `asyncio.get_running_loop().time()` (not deprecated `get_event_loop().time()`)
- Each tool has `_full_doc_` attribute set after definition (W14)
- `external_opencode_send_message` calls pass `model` as nested dict with `providerID`/`modelID` (camelCase) per C1
- `external_opencode_answer_question` uses `requestID` (camelCase) in payload per C1
- `external_opencode_wait_for_result` and `external_opencode_wait_any` poll every 30s (matches `POLL_INTERVAL_S`)
- Default timeout: 600s (10 min, matches Go's `ClientTimeout`)

## Deliverables
- [ ] `daemon/tools/external_opencode.py` with 8 tool functions + factory
- [ ] All 8 tools have `_full_doc_` set
- [ ] `CATEGORY_NAME = "OpenCode"` and `CATEGORY_DOC` module constants
- [ ] `daemon/tools/_tool_registry.py` updated with `"external_opencode"` entry
- [ ] `daemon/tools/instance.py` wires `create_opencode_tools()` **outside** `is_rag_enabled()` block
