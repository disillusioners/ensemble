# Phase 3: Multi-turn Graph Resume Validation Test

## Objective
Write a test that proves multi-turn checkpoint resume works correctly: when an LLM call fails mid-execution, the retry resumes from the last completed node (not from the beginning), preserving all tool results from prior steps.

## Coupling
- **Depends on**: Phase 2 (uses error classifier and expanded retry mechanism)
- **Coupling type**: loose — test can work with Phase 2's infrastructure or with its own mocks
- **Shared files with other phases**: None (new test file only)
- **Shared APIs/interfaces**: `build_instance_graph()`, `TRANSIENT_EXCEPTIONS`, `TransientAPIError`
- **Why this coupling**: Test exercises the retry mechanism; Phase 2 provides the mechanism. But test can mock the classifier independently if Phase 2 isn't landed yet.

## Context

### How Checkpoint Resume Works (from codebase audit)

**Normal flow:**
1. `enqueue_message()` → `_process_queue()` → `_process_message_with_tracking()`
2. `graph.astream(graph_input={"messages": [message]}, config)`
3. LangGraph executes: agent → tools → agent → tools → agent → END
4. After each node, LangGraph saves checkpoint to SQLite

**Retry flow (manager.py:1136-1146):**
1. Exception caught in `_process_queue()` at line 995
2. Queue schedules retry
3. On retry, `_process_message_with_tracking(is_retry=True)`
4. `_has_checkpoint()` → True → `graph_input = None`
5. `graph.astream(None, config)` → LangGraph resumes from last checkpoint
6. The node that was interrupted re-executes; prior completed nodes are skipped

**Key property**: LangGraph checkpoints after each node completion. If the 3rd LLM call (step 5 in agent→tools→agent→tools→agent) fails:
- Steps 1-4 are checkpointed (first agent call, first tool execution, second agent call, second tool execution)
- Step 5 (third agent call) is NOT checkpointed
- Resume restarts from step 5, not step 1

### What We Need to Prove
1. Failure at step 5 does NOT lose steps 1-4
2. Retry resumes from step 5
3. Tool results from steps 2 and 4 are preserved in state
4. Final response includes all prior tool interactions

## Tasks

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 1 | Create test helper: mock LLM with call counting | Mock LLM that tracks call number, returns predefined responses, and fails on Nth call. | `tests/integration/test_checkpoint_resume.py` |
| 2 | Create test helper: mock tool that records invocations | Tool that returns canned responses and records all invocations for verification. | `tests/integration/test_checkpoint_resume.py` |
| 3 | Write test: `test_resume_after_llm_failure_preserves_state` | Main test: 5-step graph (agent→tools→agent→tools→agent), inject failure at step 5, verify resume picks up correctly. | `tests/integration/test_checkpoint_resume.py` |
| 4 | Write test: `test_resume_preserves_tool_results` | Verify tool results from steps 2 and 4 are in the checkpoint state after resume. | `tests/integration/test_checkpoint_resume.py` |
| 5 | Write test: `test_no_resume_without_checkpoint` | Verify that without a checkpoint, retry re-sends the original message (not `graph_input=None`). | `tests/integration/test_checkpoint_resume.py` |

## Key Files
- `tests/integration/test_checkpoint_resume.py` — new file (all tasks)
- `daemon/graph.py` — tested module (`build_instance_graph`, `create_agent_node`)
- `daemon/manager.py` — reference for `_has_checkpoint` and resume logic

## Constraints
- **Use mock LLM, not real API** — test must be deterministic and not require API key
- **Use real LangGraph + real SQLite checkpointer** — we're testing checkpoint behavior, not mocking it
- **Test must work in unit test environment** — conftest.py mocks langgraph for non-integration tests; this file must explicitly unmock it (follow `test_message_queue_e2e.py` pattern)
- **No flakiness** — no timing-dependent assertions

## Implementation Details

### Test Design: 5-Step Graph with Injected Failure

```
Step 1: agent call → "I'll use tool_a" (tool call: tool_a("hello"))
Step 2: tools execution → tool_a returns "result_a_1"
Step 3: agent call → "Now I'll use tool_b" (tool call: tool_b("world"))  
Step 4: tools execution → tool_b returns "result_b_1"
Step 5: agent call → 💥 INJECT FAILURE (APIConnectionError)
        ↓
    Checkpoint saved: steps 1-4 complete
        ↓
    Retry: graph_input=None → resumes from step 5
        ↓
Step 5 (retry): agent call → "Final answer based on tool_a and tool_b results"
        ↓
    END
```

### Test Structure

```python
"""
Test that LangGraph checkpoint resume works correctly under LLM failure.

This test uses a mock LLM (no real API calls) but real LangGraph + SQLite 
checkpointer to validate that:
1. Failure mid-graph preserves completed node state
2. Retry resumes from failed node, not from beginning
3. Tool results from prior steps are preserved

Run with:
    pytest tests/integration/test_checkpoint_resume.py -v -s
"""

import pytest
import asyncio
from unittest.mock import MagicMock, patch
from langchain_core.messages import HumanMessage, AIMessage, ToolMessage

# Restore real langgraph (follow test_message_queue_e2e.py pattern)
# ... module restoration code ...

class CallCountingMockLLM:
    """Mock LLM that counts calls and fails on the Nth call."""
    
    def __init__(self, fail_on_call: int, failure_exception: Exception):
        self.call_count = 0
        self.fail_on_call = fail_on_call
        self.failure_exception = failure_exception
        self.responses = []  # Predefined responses for each call
    
    def invoke(self, messages, **kwargs):
        self.call_count += 1
        if self.call_count == self.fail_on_call:
            raise self.failure_exception
        # Return predefined response for this call number
        return self.responses[self.call_count - 1]
    
    def bind_tools(self, tools):
        return self  # Return self so with_retry wraps correctly
    
    def with_retry(self, **kwargs):
        return self  # Bypass retry for controlled testing
```

### Verification Points

```python
async def test_resume_after_llm_failure_preserves_state():
    # Setup: create graph with mock LLM that fails on 3rd call (step 5)
    # Execute: run graph, catch exception, resume with graph_input=None
    
    # VERIFY:
    # 1. LLM was called 3 times total (2 initial + 1 resume)
    #    NOT 5 times (which would mean restart from scratch)
    
    # 2. After resume, graph state contains:
    #    - Original HumanMessage
    #    - AIMessage with tool_call to tool_a (step 1)
    #    - ToolMessage with result_a_1 (step 2)  
    #    - AIMessage with tool_call to tool_b (step 3)
    #    - ToolMessage with result_b_1 (step 4)
    #    - Final AIMessage with the answer (step 5 retry)
    
    # 3. Tool functions were called exactly 2 times (once each)
    #    NOT 4 times (which would mean tools re-executed)
```

### Important: Real Checkpointer Required

```python
# Use real SQLite checkpointer (not mock) to test actual checkpoint behavior
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
import tempfile, os

@pytest.fixture
async def checkpointer():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    checkpointer = AsyncSqliteSaver.from_conn_string(db_path)
    await checkpointer.setup()
    yield checkpointer
    os.unlink(db_path)
```

## Deliverables
- [ ] `test_resume_after_llm_failure_preserves_state` passes
- [ ] `test_resume_preserves_tool_results` passes  
- [ ] `test_no_resume_without_checkpoint` passes
- [ ] Test uses mock LLM (no API key needed)
- [ ] Test uses real LangGraph + real SQLite checkpointer
- [ ] All tests are deterministic (no timing dependencies)
