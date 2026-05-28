# Investigation: Injecting Resume Message into LangGraph Checkpoint Resume

**Date:** 2026-05-28
**Status:** Investigation Complete — Recommendation: **Approach A (aupdate_state)**

---

## The Problem

When resuming from a checkpoint, we need BOTH:
1. **Continue from checkpoint** (not re-run from scratch)
2. **Inject the user's "resume" message** so the LLM sees it

Current behavior:
- `graph_input=None` → checkpoint resume ✅, but user's message is LOST ❌
- `graph_input=HumanMessage("resume")` → fresh execution ❌ (not checkpoint resume)

---

## Current Architecture

### Control Flow (User clicks Resume → Graph executes)

```
User clicks "Resume"
    → API: resume_instance_cascade()
    → InstanceLifecycleService: sets RUNNING, clears paused_at
    → InstanceManager.resume_processing_job()
        ├── [Child] → enqueue_message(metadata={"resume_mode": True})
        │               → WorkerPool → ProcessMessageProcessor
        │               → is_retry = resume_mode (True)
        │               → _process_message_with_tracking(is_retry=True)
        │
        └── [Root] → direct _process_message_with_tracking(is_retry=True)
                        → has_ckpt? → graph_input = None  ← MESSAGE LOST
                        → no ckpt? → graph_input = HumanMessage(...)
                        → graph.astream(graph_input, config)
```

### Key Decision Matrix (current code)

| `is_retry` | `has_checkpoint` | `graph_input` | Result |
|---|---|---|---|
| `False` | N/A | `{"messages": [HumanMessage(...)]}` | Fresh execution ✅ |
| `True` | `True` | `None` | Checkpoint resume ✅, but NO message ❌ |
| `True` | `False` | `{"messages": [HumanMessage(...)]}` | Fallback to fresh ⚠️ |

---

## Approach A: `aupdate_state()` — **RECOMMENDED** ⭐

### How It Works

LangGraph's `aupdate_state(config, values, as_node)` creates a **new checkpoint** by applying values through a node's writer functions (reducers). For `MessagesState`, the `add_messages` reducer **APPENDS** new messages.

Two-step process:
1. Call `aupdate_state()` to inject the message into the checkpoint
2. Call `astream(None)` to resume from the updated checkpoint

### Code Example

```python
# In _process_message_with_tracking(), modify the is_retry branch:

if is_retry:
    has_ckpt = await self._has_checkpoint(instance_id)
    if has_ckpt:
        # Step 1: INJECT the resume message into the checkpoint state
        content = _build_message_content(message, images)
        await graph.aupdate_state(
            config,
            {"messages": [HumanMessage(content=content, id=message_id)]},
            as_node="agent",
        )
        # Step 2: Resume from updated checkpoint
        graph_input = None
        logger.info(f"Resuming instance {instance_id[:8]}... with injected message")
    else:
        logger.warning(f"Retry for instance {instance_id[:8]}... but no checkpoint found")
        content = _build_message_content(message, images)
        graph_input = {"messages": [HumanMessage(content=content, id=message_id)]}
else:
    # First attempt — normal flow
    content = _build_message_content(message, images)
    graph_input = {"messages": [HumanMessage(content=content, id=message_id)]}
```

### Why `as_node="agent"` Works

- LangGraph auto-adds `ChannelWrite` writers to ALL nodes during compilation
- The project already uses this exact pattern for **compaction** (`daemon/graph.py:409-411`):
  ```python
  await graph.aupdate_state(thread_config, {'messages': result.replacement_messages}, as_node='agent')
  ```
- The `add_messages` reducer APPENDS messages (new ID = new message)

### Pros
| Pro | Details |
|---|---|
| **Proven in our codebase** | Already used for compaction — same graph, same checkpointer |
| **Clean API** | Official LangGraph method, no hacks |
| **Preserves checkpoint** | Creates new checkpoint version, doesn't corrupt existing |
| **Message ordering correct** | `add_messages` appends at end of message list |
| **Small code change** | ~5 lines added to existing `is_retry` branch |
| **No versioning issues** | Proper checkpoint versioning (`step + 1`) |

### Cons
| Con | Details |
|---|---|
| **Checkpoint step incremented** | Creates intermediate checkpoint. Minor — steps are internal |
| **Potential trigger side effects** | `update_state` may trigger channel subscribers. With `as_node="agent"`, the graph's routing may re-evaluate conditional edges. Need to verify that `astream(None)` resumes correctly after this. |
| **Message appears "from agent"** | The message is attributed to the `agent` node in `versions_seen`. This is metadata only — the message itself has the correct `HumanMessage` type. |

### Complexity
**Low** — ~5 lines of code change, no new dependencies, no architecture changes.

### Risk
**Low** — The pattern is already proven in the codebase (compaction). The only risk is potential interaction with graph routing after `update_state`, which can be tested.

### Risk Mitigation
- Test with `as_node=INPUT` as an alternative (writes directly to input channels, bypasses node writers)
- If routing issues occur, try `as_node=INPUT` instead:
  ```python
  from langgraph._internal._constants import INPUT
  await graph.aupdate_state(config, {"messages": [HumanMessage(...)]}, as_node=INPUT)
  ```

---

## Approach B: Pass HumanMessage as graph_input with checkpoint config

### How It Works Technically
Call `astream(HumanMessage("resume"), config_with_checkpoint)` — providing both input and checkpoint config.

### What Actually Happens

From LangGraph source (`_loop.py:618-636`):
```python
is_resuming = bool(self.checkpoint["channel_versions"]) and bool(
    configurable.get(
        CONFIG_KEY_RESUMING,
        self.input is None  # ← input is NOT None here → False
        or isinstance(self.input, Command)
        or (...),
    )
)
```

When `input` is NOT `None` (i.e., we pass a HumanMessage):
- `is_resuming = False` (because `self.input is None` evaluates to False)
- LangGraph treats this as a **fresh execution** — it processes the input from scratch
- The checkpoint state may be partially loaded, but pending tasks are **discarded**

### Code Example
```python
# This does NOT work as intended:
graph_input = {"messages": [HumanMessage(content=message, id=message_id)]}
async for event in graph.astream(graph_input, config, stream_mode=["updates"]):
    # This starts a FRESH run, not a resume!
```

### Pros
| Pro | Details |
|---|---|
| **Simple** | Just pass input instead of None |

### Cons
| Con | Details |
|---|---|
| **DOES NOT RESUME FROM CHECKPOINT** | This is the critical flaw — LangGraph treats it as fresh execution |
| **Discards pending work** | Any in-progress tool calls or interrupted state is lost |
| **Not what we need** | This is literally the current problem we're solving |

### Complexity
N/A — doesn't work for our use case.

### Risk
**Critical failure** — This approach fundamentally doesn't achieve checkpoint resume.

### Verdict
❌ **DO NOT USE** — This is the exact problem we're trying to solve.

---

## Approach C: Manipulate checkpoint state directly

### How It Works Technically
Load the checkpoint from `AsyncSqliteSaver`, modify `channel_values.messages`, save it back.

### What's Available
```python
# BaseCheckpointSaver API:
checkpoint = await checkpointer.aget(config)
# checkpoint is a TypedDict with:
#   channel_values: dict[str, Any]  ← messages are here
#   channel_versions: ChannelVersions
#   versions_seen: dict[str, ChannelVersions]
```

### The Problem

The checkpoint data structure is **not simply modifiable**:
```python
class Checkpoint(TypedDict):
    v: int
    id: str
    ts: str
    channel_values: dict[str, Any]  # Messages stored here
    channel_versions: ChannelVersions  # Version numbers per channel
    versions_seen: dict[str, ChannelVersions]  # What each node has read
    updated_channels: list[str] | None
```

To correctly modify a checkpoint you'd need to:
1. Load checkpoint
2. Append message to `channel_values["messages"]`
3. Increment `channel_versions` for the messages channel
4. Update `versions_seen` for appropriate nodes
5. Generate new checkpoint ID
6. Save with correct parent metadata

### Code Example
```python
# Very risky manual manipulation:
checkpoint = await checkpointer.aget(config)
messages = checkpoint["channel_values"]["messages"]
messages.append(HumanMessage(content="resume", id=str(uuid.uuid4())))
checkpoint["channel_values"]["messages"] = messages
# Now need to update versions_seen, channel_versions, etc.
# Very error-prone — no API for this
```

### Pros
| Pro | Details |
|---|---|
| **Maximum control** | Can modify any aspect of checkpoint |

### Cons
| Con | Details |
|---|---|
| **No public API** | Checkpoint versioning is internal, no safe modification API |
| **Easy to break consistency** | Wrong version numbers = graph corruption |
| **Fragile** | Tied to internal implementation details that may change |
| **Unnecessary** | `update_state()` does this safely already |

### Complexity
**High** — requires deep knowledge of checkpoint internals, custom version management.

### Risk
**High** — corrupting checkpoint versioning breaks graph execution entirely.

### Verdict
❌ **DO NOT USE** — Approach A (`update_state`) does this safely.

---

## Approach D: Full History Replay (no checkpoint resume)

### How It Works Technically
1. Load all messages from the checkpoint
2. Build a fresh initial state with all historical messages + new resume message
3. Start a **fresh graph execution** with this state
4. Graph re-processes from the beginning

### Code Example
```python
# Load checkpoint messages
checkpoint = await checkpointer.aget(config)
existing_messages = checkpoint["channel_values"]["messages"]

# Append resume message to full history
all_messages = existing_messages + [HumanMessage(content="resume")]

# Start fresh execution with full history
graph_input = {"messages": all_messages}
async for event in graph.astream(graph_input, config_new_thread, stream_mode=["updates"]):
    # Graph runs from scratch with all history
```

### Pros
| Pro | Details |
|---|---|
| **Message visible** | The LLM definitely sees the resume message |
| **No special APIs needed** | Just normal graph invocation |

### Cons
| Con | Details |
|---|---|
| **Re-executes everything** | All tool calls, LLM calls, etc. are re-run from scratch |
| **Expensive** | Redundant LLM calls cost time and money |
| **Different results** | LLM may produce different responses on re-execution |
| **Tool call duplication** | Side effects (API calls, file writes) happen AGAIN |
| **Compaction lost** | Previous compaction state is reset |
| **New thread needed** | Must use new thread_id to avoid checkpoint conflicts |

### Complexity
**Medium** — need to handle new thread creation, history loading.

### Risk
**High** — Tool call duplication could cause real-world side effects (duplicate emails, duplicate file writes, etc.).

### Verdict
❌ **DO NOT USE** — Re-execution is dangerous and expensive.

---

## Approach E: Hybrid — Checkpoint Resume + Interception

### How It Works Technically
1. Resume from checkpoint normally (`astream(None)`)
2. Before the first LLM call, inject the resume message into the conversation
3. This requires modifying the graph's agent node function to check for "pending resume messages"

### Code Example
```python
# In create_agent_node or a wrapper:

async def agent_node_with_resume(state, config):
    # Check if there's a pending resume message
    pending_resume = config.get("configurable", {}).get("pending_resume_message")
    if pending_resume:
        # Inject the message before LLM call
        messages = state["messages"] + [HumanMessage(content=pending_resume)]
    else:
        messages = state["messages"]
    
    # Normal LLM call with potentially augmented messages
    response = await llm.ainvoke(messages)
    return {"messages": [response]}

# In _process_message_with_tracking:
config["configurable"]["pending_resume_message"] = message
graph_input = None  # Resume from checkpoint
```

### Pros
| Pro | Details |
|---|---|
| **True checkpoint resume** | Graph continues from interrupted point |
| **Message visible** | LLM sees the resume message |
| **No API tricks** | Uses standard config passing |

### Cons
| Con | Details |
|---|---|
| **Modifies agent node** | Requires changing core graph architecture |
| **Config pollution** | Adds non-standard config keys |
| **Timing-sensitive** | Message only available on first node execution |
| **First node only** | If agent isn't the first node to run, message won't be injected |
| **Fragile** | Depends on graph topology and execution order |
| **Not persistent** | If graph is interrupted again, the resume message is lost |

### Complexity
**Medium** — requires modifying the agent node function.

### Risk
**Medium** — Changes core graph behavior, may have unexpected interactions.

### Verdict
⚠️ **Possible but not recommended** — Approach A is cleaner and doesn't require modifying graph internals.

---

## Final Comparison

| Criteria | A: aupdate_state ⭐ | B: graph_input | C: Direct Manipulation | D: Full Replay | E: Hybrid |
|---|---|---|---|---|---|
| **Checkpoint resume** | ✅ Yes | ❌ No | ✅ Yes | ❌ No | ✅ Yes |
| **Message injected** | ✅ Yes | ✅ Yes | ✅ Yes | ✅ Yes | ✅ Yes |
| **Already proven** | ✅ Yes (compaction) | ❌ No | ❌ No | ❌ No | ❌ No |
| **Code change size** | ~5 lines | N/A | 30+ lines | 20+ lines | 15+ lines |
| **Risk level** | 🟢 Low | 🔴 N/A | 🔴 High | 🔴 High | 🟡 Medium |
| **Complexity** | 🟢 Low | — | 🔴 High | 🟡 Medium | 🟡 Medium |

---

## Recommendation: Approach A (`aupdate_state`)

### Why

1. **Already proven** — The codebase uses `aupdate_state(as_node='agent')` for compaction. Same graph, same checkpointer, same pattern.
2. **Minimal change** — ~5 lines added to the existing `is_retry` branch.
3. **Official API** — LangGraph's `update_state` is designed for this exact use case (human-in-the-loop, time-travel, state modification).
4. **Safe** — Proper checkpoint versioning, no corruption risk.
5. **The `add_messages` reducer guarantees APPEND** — New ID means new message appended to history.

### Implementation Plan

**File:** `daemon/services/instance_messaging.py`
**Location:** `_process_message_with_tracking()`, the `if is_retry:` block (~line 856)

**Change:**
```python
# BEFORE:
if is_retry:
    has_ckpt = await self._has_checkpoint(instance_id)
    if has_ckpt:
        graph_input = None
    else:
        graph_input = {"messages": [HumanMessage(...)]}

# AFTER:
if is_retry:
    has_ckpt = await self._has_checkpoint(instance_id)
    if has_ckpt:
        # Inject the resume message into the checkpoint state
        content = _build_message_content(message, images)
        if content:  # Only inject if there's actual message content
            await graph.aupdate_state(
                config,
                {"messages": [HumanMessage(content=content, id=message_id)]},
                as_node="agent",
            )
        graph_input = None  # Resume from updated checkpoint
    else:
        graph_input = {"messages": [HumanMessage(...)]}
```

### Edge Cases to Handle

1. **Silent resume** (`silent=True`): The message content may be empty. Guard with `if content:` before calling `aupdate_state`.
2. **No checkpoint found**: Already handled — falls back to fresh execution.
3. **Compaction interaction**: Compaction already uses `aupdate_state`. Both operations create separate checkpoints. No conflict since compaction runs BEFORE the retry check.
4. **Message ordering**: `add_messages` appends at end. The resume message will appear after all previous messages in chronological order. ✅

### Testing Strategy

1. **Unit test**: Create a paused instance with checkpoint, resume with a message, verify message appears in graph state.
2. **Integration test**: Full pause/resume cycle with child instances, verify parent receives resume message.
3. **Log verification**: Check that `aupdate_state` creates a checkpoint, then `astream(None)` resumes from it.
