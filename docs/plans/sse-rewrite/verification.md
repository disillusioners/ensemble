# Pre-Implementation Verification

Run these before starting to scope the impact:

```bash
# 0. daemon/utils.py should NOT exist yet (will be created in Step 0)
ls daemon/utils.py 2>/dev/null && echo "EXISTS - remove/rename first" || echo "OK - will create in Step 0"

# 1. parse_think_tags() currently lives in manager.py (not in utils)
grep -rn "def parse_think_tags\|THINK_PATTERN" daemon/ --include="*.py"

# 2. Frontend message_id references
grep -r "message_id" frontend/src --include="*.ts" -l | wc -l

# 3. parse_think_tags() call sites
grep -rn "parse_think_tags" daemon/ --include="*.py"

# 4. TaskProcessor EventBus usage — find ALL create_*_event calls
grep -rn "create_processing_started_event\|create_processing_completed_event\|create_processing_failed_event\|create_error_event\|create_instance_completed_event\|create_child_completed_event\|create_child_failed_event" daemon/ --include="*.py"

# 5. MessageService call sites — ALL should be deleted
grep -rn "self._message_service\|on_assistant_message_completed\|on_child_completion_report\|on_child_error_report" daemon/ --include="*.py"

# 6. send_message() call sites
grep -rn "send_message" daemon/ --include="*.py"

# 7. ResponseDispatcher integration
grep -rn "_broadcast_to_global\|subscribe_all" daemon/sources/

# 8. broadcast_sync() callers — simplify to completed-only routing
grep -rn "broadcast_sync" daemon/ --include="*.py"

# 9. _send_error_report() second EventBus call
grep -n "create_child_failed_event" daemon/manager.py

# 10. task_processor.py create_processing_started_event call (Phase 3b addition)
grep -n "create_processing_started_event" daemon/task_processor.py

# 11. ResponseDispatcher checkpoint filtering — VERIFY before Phase 4
grep -rn "event_type" daemon/sources/dispatcher.py | grep -i "filter\|checkpoint\|completed"

# 12. Sequence counter restart behavior — verify code comment exists
grep -rn "restart\|reset\|counter" daemon/utils.py

# 13. _create_completion_events() call sites
grep -rn "_create_completion_events" daemon/ --include="*.py"

# 14. LangGraph version (for stream format verification)
grep "langgraph" pyproject.toml

# 15. Extract thinking call sites (verify consolidation plan)
grep -rn "additional_kwargs.*thinking\|reasoning_content\|msg.thinking" daemon/ --include="*.py"
```

---

## Step 3.5: LangGraph Stream Format Verification

> **⚠️ CRITICAL**: This is a **MANDATORY** pre-requisite before Phase 1. The checkpoint-based
> approach depends on correctly extracting messages from `stream_mode=["updates"]`. If the
> format is wrong, all downstream code is broken.

> **Naming**: This is called "Step 3.5" because it must be completed BEFORE Phase 1 begins,
> but it depends on Phase 0's `utils.py` creation. In practice, treat it as **Phase 0.5**.

Write and run a verification script against the project's LangGraph version:

```python
# verify_langgraph_stream.py — run against production pyproject.toml LangGraph version
import asyncio
from langgraph.checkpoint.sqlite import AsyncSqliteSaver
import aiosqlite

async def verify_stream_format():
    db_path = "data/ensemble.db"
    async with aiosqlite.connect(db_path) as conn:
        checkpointer = AsyncSqliteSaver(conn)
        
        # Import the actual graph from the project
        from daemon.graph import build_graph
        graph = build_graph()
        
        # Run with a simple test input
        config = {"configurable": {"thread_id": "test-verify"}}
        graph_input = {"messages": [{"role": "user", "content": "test"}]}
        
        async for event in graph.astream(graph_input, config, stream_mode=["updates"]):
            print("=== EVENT ===")
            print(f"type: {type(event)}")
            print(f"value: {event}")
            if isinstance(event, tuple):
                mode, data = event
                print(f"mode: {mode}")
                print(f"data keys: {data.keys() if hasattr(data, 'keys') else 'N/A'}")
                for node_name, node_data in data.items():
                    print(f"  node: {node_name}, data type: {type(node_data)}")
                    if hasattr(node_data, 'keys'):
                        print(f"  data keys: {node_data.keys()}")
                        for k, v in node_data.items():
                            print(f"    {k}: {type(v)} = {repr(v)[:200]}")

# Run: python verify_langgraph_stream.py
```

**What to verify:**
- Does `mode == "updates"`? What is `data` structure?
- Are messages accessible via `node_data.get("messages", [])`?
- What is the shape of each message object?
- Does each message have an `.id` attribute? Is it populated?
- Does each checkpoint include a `checkpoint_sequence` number?
- What is the full call chain from astream → checkpoint → SSE?

**If the format is different**, update the extraction code in Step 3 (`_process_message_with_tracking`) before proceeding.
