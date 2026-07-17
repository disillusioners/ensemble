# Phase 1: Backend Core — Manager + Tool + Pause Hook

## Objective
Create the `QuestionManager` service (in-memory, per-instance, thread-safe), the `question` tool, tool registration, and the **pause-from-within-graph** mechanism via a conditional post-tools edge + `question_pause_node`. After this phase, an agent can call `question(questions)`, the pack is stored, the instance pauses, and SSE emits a pending event.

## Coupling
- **Depends on**: None (root phase)
- **Coupling type**: — (root)
- **Shared files with other phases**: `daemon/manager.py` (InstanceManager — shared with Phase 2 for answer API access), `daemon/services/live_event_hub.py` (LiveEventHub — shared with Phase 2)
- **Shared APIs/interfaces**: `QuestionManager` class (consumed by Phase 2's Answer API)
- **Why this coupling**: Phase 2's Answer API must call `QuestionManager.set_answers()` and `InstanceManager.clear_question_pause_requested()`. These are defined in Phase 1.

## Context
- This is the foundational phase. All backend logic lives here.
- The **pause-from-within-tool** is the key architectural challenge (see D2 in overview).
- Reference pattern: TodoManager (`daemon/services/todo_manager.py`) for the manager, `todo_tools.py` for the tool factory.

## Tasks

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 1 | Create QuestionManager service | In-memory dict keyed by instance_id. `QuestionPack` + `Question` dataclasses. Thread-safe with `threading.Lock`. Methods: `set_question_pack`, `get_question_pack`, `clear_question_pack`, `set_answers`. **Reject duplicate pending packs** (F8/F11). | `daemon/services/question_manager.py` *(new)* |
| 2 | Create `question` tool module | `@register_tool_category("question")` + `create_question_tools(manager, current_instance_id, live_event_hub)` factory. One tool: `question(questions)`. Stores pack, emits SSE, sets pause flag, **echoes question text in placeholder** (F7). | `daemon/tools/question_tools.py` *(new)* |
| 3 | Register tool category | Add `"question": "daemon.tools.question"` to `CATEGORY_MODULES` in `_tool_registry.py`. Do NOT add to `INNATE_SKILL_TOOL_CATEGORIES`. | `daemon/tools/_tool_registry.py` |
| 4 | Wire tool factory | In `create_instance_tools()` in `instance.py`, call `create_question_tools(manager, current_instance_id, live_event_hub)` and add to the tool list. Follow the exact pattern used for todo tools. | `daemon/tools/instance.py` |
| 5 | Add pause-requested flag to InstanceManager | Add `_question_pause_requested: dict[str, bool]` dict. Methods: `set_question_pause_requested(instance_id)`, `is_question_pause_requested(instance_id)`, `clear_question_pause_requested(instance_id)`. Clear on resume/terminate/cleanup. | `daemon/manager.py` |
| 6 | Add QuestionManager singleton + cleanup to InstanceManager | Initialize `self._question_manager = QuestionManager()` in `__init__` (near line ~716 where `_todo_manager` is). **Add cleanup to `_cleanup_instance_state`** (~line 1909): `self._question_manager.clear_question_pack(instance_id)` (F5). Single hook covers terminate/release/hard-delete. | `daemon/manager.py` |
| 7 | Add conditional post-tools edge + `question_pause_node` (F1) | Convert `graph.add_edge("tools", "agent")` at `graph.py:1226` to `add_conditional_edges`. Thread `manager` into `build_instance_graph`. Add `question_pause_node` with **try/finally** (F2) and **defense-in-depth exception handling** (F4). | `daemon/graph.py` |

## Detailed Design Notes

### Task 1: QuestionManager

```python
# daemon/services/question_manager.py
from dataclasses import dataclass, field
from datetime import datetime, timezone
import threading
import uuid

@dataclass
class Question:
    id: str
    text: str
    options: list[str] = field(default_factory=list)
    allow_custom: bool = True
    required: bool = True
    answer: str | None = None

@dataclass
class QuestionPack:
    instance_id: str
    questions: list[Question]
    status: str = "pending"  # "pending" | "answered"
    answers: dict = field(default_factory=dict)
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

class QuestionManager:
    def __init__(self):
        self._packs: dict[str, QuestionPack] = {}
        self._lock = threading.Lock()

    def set_question_pack(self, instance_id: str, questions: list[dict]) -> QuestionPack | None:
        """Store a question pack. Returns None if a pack is already pending (F8/F11)."""
        with self._lock:
            existing = self._packs.get(instance_id)
            if existing and existing.status == "pending":
                return None  # Reject duplicate — at most one pending pack
            # Auto-generate IDs for questions missing them
            qs = []
            for q in questions:
                qid = q.get("id") or str(uuid.uuid4())
                qs.append(Question(
                    id=qid,
                    text=q["text"],
                    options=q.get("options", []),
                    allow_custom=q.get("allow_custom", True),
                    required=q.get("required", True),
                ))
            pack = QuestionPack(instance_id=instance_id, questions=qs)
            self._packs[instance_id] = pack
            return pack

    def get_question_pack(self, instance_id: str) -> QuestionPack | None:
        with self._lock:
            return self._packs.get(instance_id)

    def set_answers(self, instance_id: str, answers: dict) -> QuestionPack | None:
        with self._lock:
            pack = self._packs.get(instance_id)
            if pack is None:
                return None
            pack.status = "answered"
            pack.answers = answers  # any JSON shape
            return pack

    def clear_question_pack(self, instance_id: str) -> None:
        with self._lock:
            self._packs.pop(instance_id, None)
```

### Task 2: question tool (with F7 compaction-safe placeholder)

```python
# daemon/tools/question_tools.py
@register_tool_category("question")
def create_question_tools(manager, current_instance_id, live_event_hub):
    @tool(name="question", ...)
    def question(questions: list[dict]) -> str:
        """
        Ask the user a batch of questions. Pauses the instance until the user answers.
        ...
        """
        # 1. Store pack via QuestionManager (rejects duplicate pending packs — F8/F11)
        pack = manager._question_manager.set_question_pack(current_instance_id, questions)
        if pack is None:
            return "Already have a pending question pack for this instance. Wait for answers before asking more."

        # 2. Emit SSE: best-effort
        try:
            await live_event_hub.stream_question_pack(current_instance_id, pack_to_dict(pack))
        except Exception:
            pass

        # 3. Set pause flag
        manager.set_question_pause_requested(current_instance_id)

        # 4. Return placeholder — ECHO QUESTION TEXT for compaction safety (F7)
        #    After compaction, the AIMessage with tool_calls may be lost.
        #    Echoing Q text lets the LLM correlate Q<->A even after compaction.
        q_summary = " | ".join(f"Q{i+1}: {q.text}" for i, q in enumerate(pack.questions))
        return f"Asked the user: {q_summary}. The instance will pause until the user answers."
    return [question]
```

### Task 5: Pause-requested flag on InstanceManager

```python
# daemon/manager.py — in __init__
self._question_pause_requested: dict[str, bool] = {}
self._question_manager = QuestionManager()  # Task 6

# Methods
def set_question_pause_requested(self, instance_id: str) -> None:
    self._question_pause_requested[instance_id] = True

def is_question_pause_requested(self, instance_id: str) -> bool:
    return self._question_pause_requested.get(instance_id, False)

def clear_question_pause_requested(self, instance_id: str) -> None:
    self._question_pause_requested.pop(instance_id, None)
```

### Task 6: Cleanup in `_cleanup_instance_state` (F5)

Add to `_cleanup_instance_state` in `manager.py` (~line 1909), the single cleanup hook that covers terminate/release/hard-delete:

```python
def _cleanup_instance_state(self, instance_id: str):
    # ... existing cleanup ...
    self._question_manager.clear_question_pack(instance_id)
    self.clear_question_pause_requested(instance_id)
```

### Task 7: Conditional post-tools edge + `question_pause_node` (F1 + F2 + F4)

**Step A: Thread `manager` into `build_instance_graph`**

The graph factory already receives `injection_slot` and `live_hub` via closure parameters. Add `manager` as a parameter (or it may already be accessible — verify the signature of `build_instance_graph`).

**Step B: Convert unconditional edge to conditional edge**

At `graph.py:1226`, replace:
```python
graph.add_edge("tools", "agent")
```

With:
```python
# Conditional post-tools edge: route to question_pause_node if pause requested (F1)
graph.add_conditional_edges(
    "tools",
    create_post_tools_router(manager),  # closure factory
    {"agent": "agent", "question_pause_node": "question_pause_node"},
)
graph.add_node("question_pause_node", question_pause_node)
graph.add_edge("question_pause_node", END)
```

**Step C: `create_post_tools_router` closure**

```python
def create_post_tools_router(manager: InstanceManager):
    def post_tools_router(state) -> str:
        instance_id = state.get("instance_id")  # or extract from config
        if manager.is_question_pause_requested(instance_id):
            return "question_pause_node"
        return "agent"
    return post_tools_router
```

**Step D: `question_pause_node` with try/finally (F2) + defense-in-depth (F4)**

```python
async def question_pause_node(state, config=None):
    instance_id = config.get("configurable", {}).get("thread_id")
    try:
        await manager.pause_instance_cascade(instance_id)
    except asyncio.CancelledError:
        # pause_instance_cascade's success path: graph_task.cancel() raises CancelledError
        # Re-raise — do NOT swallow. Flag is cleared in finally.
        raise
    except Exception as e:
        # Defense-in-depth: non-CancelledError failures (F4)
        logger.error(f"[question_pause_node] pause cascade failed: {e}")
        manager.clear_question_pause_requested(instance_id)
        raise
    finally:
        # ALWAYS clear the flag, even on CancelledError path (F2)
        # The success path of pause_instance_cascade raises CancelledError at the
        # next await, so code after the await is UNREACHABLE. The finally block
        # is the ONLY reliable place to clear the flag.
        manager.clear_question_pause_requested(instance_id)
    return {}
```

**⚠️ Critical: Why `finally` and not code-after-await (F2)**

`pause_instance_cascade()` cancels the graph task via `graph_task.cancel()`. This raises `CancelledError` at the next `await` point. Any code after `await manager.pause_instance_cascade(...)` is **unreachable**. Without the `finally` block, the pause-requested flag would stay set forever, causing a stuck loop when the instance resumes (it would immediately pause again on the first tool call routing).

The `finally` block executes during exception unwinding, BEFORE the CancelledError propagates. This is the only reliable way to clear the flag.

### Task 8: SSE `stream_question_pack()` method

New method in LiveEventHub, following the exact pattern of `stream_todo_update()`:

```python
# daemon/services/live_event_hub.py
async def stream_question_pack(self, instance_id: str, pack: dict):
    """Emit question_pack SSE event. Best-effort — failures don't break callers."""
    try:
        event = {
            "instance_id": instance_id,
            "event_type": "question_pack",
            "event_id": "",  # or generated
            "message": pack,
            "checkpoint_id": None,
        }
        await self._stream_to_connections(instance_id, event)
    except Exception:
        pass  # best-effort
```

**⚠️ SSE timing note (F3)**: The `question_pack` (status=pending) SSE event is emitted by the **tool** (before the pause cascade), NOT by post-commit code. This is critical because the pause cascade cancels the graph task mid-execution, which skips any post-commit SSE emission. The tool emits SSE synchronously before setting the pause flag, so it always fires.

## Key Files
- `daemon/services/question_manager.py` *(new)* — QuestionManager + QuestionPack + Question dataclasses
- `daemon/tools/question_tools.py` *(new)* — question tool + factory
- `daemon/tools/_tool_registry.py` — add category
- `daemon/tools/instance.py` — wire factory
- `daemon/manager.py` — singleton + pause flag + cleanup in `_cleanup_instance_state`
- `daemon/graph.py` — conditional edge + `question_pause_node` + `create_post_tools_router`
- `daemon/services/live_event_hub.py` — SSE method
- `daemon/services/instance_lifecycle.py` — (read-only reference for pause_instance_cascade API)

## Constraints
- Follow the TodoManager pattern EXACTLY for the manager (threading.Lock, dict keyed by instance_id, singleton on InstanceManager).
- The `question` tool must return a string (never raise exceptions — per codebase convention).
- SSE emission must be best-effort (try/except) so SSE failure doesn't break the tool.
- Clear pause flag in `finally` block of `question_pause_node` (NOT after the await — unreachable on CancelledError path).
- Do NOT add a DB table — QuestionManager is in-memory only.
- `manager` must be threaded into `build_instance_graph` (like `injection_slot`/`live_hub` already are).
- The conditional edge must default to `"agent"` when flag is False — non-question tool calls route normally.

## Verification Items (F9/F12)

- [ ] **Terminate path hits `_cleanup_instance_state`**: verify that `terminate_instance()`, `release_instance()`, and hard-delete paths all call `_cleanup_instance_state`, which clears the question pack + pause flag. Add a test that calls terminate on an instance with a pending pack and confirms `get_question_pack()` returns None.
- [ ] **`_request_registry` cleaned after in-graph pause**: verify that `pause_instance_cascade()` (called from `question_pause_node`) cancels active LLM requests via `_request_registry.cancel_by_instance()`. Add a test that confirms `_request_registry` has no entries for the instance after the pause node runs.
- [ ] **Conditional edge regression test**: verify that non-question tool calls (e.g., `todo_view`) route to `"agent"` normally (flag is False).
- [ ] **Pause node try/finally test**: simulate CancelledError from `pause_instance_cascade` and confirm the flag is cleared.

## Deliverables
- [ ] `daemon/services/question_manager.py` created with QuestionManager + dataclasses
- [ ] `daemon/tools/question_tools.py` created with `question` tool + factory
- [ ] `"question"` added to `CATEGORY_MODULES`
- [ ] Factory wired in `create_instance_tools()`
- [ ] Pause-requested flag + QuestionManager singleton on InstanceManager
- [ ] Cleanup added to `_cleanup_instance_state` (single hook)
- [ ] Conditional post-tools edge implemented in graph.py (F1)
- [ ] `question_pause_node` with try/finally (F2) + defense-in-depth (F4)
- [ ] `stream_question_pack()` SSE method in LiveEventHub
- [ ] Unit tests for QuestionManager (set/get/clear/answers, **duplicate rejection**)
- [ ] Unit test: calling `question` tool stores pack + sets flag + echoes text (F7)
- [ ] Unit test: second `question` call while pack pending returns error (F8/F11)
- [ ] Verification: terminate path hits `_cleanup_instance_state` (F9)
- [ ] Verification: `_request_registry` cleaned after in-graph pause (F12)
- [ ] Verification: conditional edge routes non-question tools normally
