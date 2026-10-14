# Messaging System Architecture Summary

## Key Files

### Source Layer (daemon/sources/)
| File | Purpose |
|------|---------|
| `base.py` | Base interfaces: `IncomingMessage`, `OutgoingMessage`, `MessageSourceAdapter` abstract class |
| `registry.py` | `SourceRegistry` - manages adapter lifecycle, handles incoming messages, routes to InstanceManager |
| `dispatcher.py` | `ResponseDispatcher` - routes completed responses to external sources (Telegram, etc.) |
| `adapters/telegram.py` | `TelegramAdapter` - polls Telegram API or receives webhooks, sends/receives messages |
| `adapters/scheduler.py` | `SchedulerAdapter` - triggers agents on cron/interval schedules |

### Core (daemon/)
| File | Purpose |
|------|---------|
| `manager.py` | `InstanceManager` - orchestrates all agent instances, enqueues messages, processes via LangGraph |
| `graph.py` | LangGraph definition with agent/tools/nudge nodes, should_continue routing logic |
| `services/task_processor.py` | `TaskProcessor` - routes tasks to type-specific processors (ProcessMessageProcessor, etc.) |
| `services/live_event_hub.py` | `LiveEventHub` - live-only SSE streaming (no buffering) |

### Repositories (daemon/repositories/)
| Directory | Purpose |
|-----------|---------|
| `instance/` | Instance persistence, metadata |
| `source/` | Source config, session mapping, instance mapping |
| `message_queue/` | Message queue entries (DB-backed) |
| `task/` | Task entries for worker pool |
| `event/` | Event persistence for lifecycle |

---

## Full Message Flow (External Source to Response)

```
┌─────────────────────────────────────────────────────────────────────────────┐
│ EXTERNAL SOURCE                                                              │
│  Telegram (polling/webhook) ──► Scheduler (cron/interval)                   │
└────────────────────────────┬────────────────────────────────────────────────┘
                             ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│ ADAPTER LAYER                                                               │
│  TelegramAdapter / SchedulerAdapter                                        │
│  - Normalizes external format → IncomingMessage                            │
│  - Calls _emit_message(msg)                                                │
└────────────────────────────┬────────────────────────────────────────────────┘
                             ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│ SOURCE REGISTRY                                                            │
│  SourceRegistry._handle_message()                                          │
│  - Checks for duplicate messages (via check_and_mark_processed)             │
│  - Gets or creates instance via InstanceMapper                             │
│  - Formats source string: "{source_id}:{external_user_id}"                 │
│  - Calls manager.enqueue_message()                                         │
└────────────────────────────┬────────────────────────────────────────────────┘
                             ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│ INSTANCE MANAGER                                                           │
│  InstanceManager.enqueue_message()                                         │
│  - Creates MessageQueue + Task entries atomically in DB                    │
│  - Notifies worker pool                                                    │
└────────────────────────────┬────────────────────────────────────────────────┘
                             ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│ WORKER POOL                                                                │
│  WorkerPool processes tasks via TaskProcessor                              │
│  TaskProcessor → ProcessMessageProcessor                                   │
│  - Calls manager._process_message_with_tracking()                         │
└────────────────────────────┬────────────────────────────────────────────────┘
                             ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│ LANGGRAPH EXECUTION                                                        │
│  InstanceManager._process_message_with_tracking()                          │
│  - Streams events via LiveEventHub (internal SSE only)                    │
│  - Graph: agent → tools → agent → ... → END                               │
│  - Returns MessageResult with content                                      │
└────────────────────────────┬────────────────────────────────────────────────┘
                             ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│ RESPONSE DISPATCHER                                                        │
│  ResponseDispatcher.dispatch_completed()                                   │
│  - Parses source string to get source_id + external_user_id               │
│  - Skips internal sources (no ":" in source)                              │
│  - Skips sources starting with "internal_"                                 │
│  - Gets adapter from registry                                             │
│  - Calls adapter.send(OutgoingMessage)                                    │
└────────────────────────────┬────────────────────────────────────────────────┘
                             ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│ ADAPTER RESPONSE                                                           │
│  TelegramAdapter.send()                                                    │
│  - Rate limiting (TokenBucketLimiter)                                     │
│  - Circuit breaker protection                                             │
│  - Per-chat message ordering via locks                                    │
│  - Sends to Telegram Bot API                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## Progressive Delivery to External Sources

**There is NO progressive delivery to external sources.**

- **Internal SSE (LiveEventHub)**: Streams live events only to active SSE connections. If no client is listening, events are dropped silently. This is live-only with no buffering.
- **External sources (Telegram, etc.)**: The ResponseDispatcher only sends the **final** completed response after the entire LangGraph execution finishes. There is no streaming of partial responses to external sources.

The flow is:
1. LangGraph executes, streaming updates internally via LiveEventHub → SSE
2. Execution completes
3. ResponseDispatcher dispatches final response to external source

---

## Source String Format

The `source` string identifies where a message originated and is formatted as:

```
{source_id}:{external_user_id}
```

### Examples:

| Source Type | Format | Example |
|-------------|--------|---------|
| Telegram | `telegram:{chat_id}` | `telegram:123456789` |
| API (internal) | `api` | `api` (no ":" = internal) |
| Internal Report | `internal_report:{id}` | `internal_report:xxx` |
| Internal Error | `internal_error_report:{id}` | `internal_error_report:xxx` |
| Internal Agent | `internal_agent:{id}` | `internal_agent:xxx` |

### Routing Logic (from ResponseDispatcher):

```python
# No ":" means internal source - don't route to adapter
if ":" not in source:
    return  # Skip (e.g., "api")

# Split to get source_id and external_user_id
source_id, external_user_id = source.split(":", 1)

# Skip internal sources (starting with "internal_")
if source_id.startswith("internal_"):
    return

# Get adapter from registry and send
adapter = registry.get(source_id)
await adapter.send(outgoing_message)
```

---

## LangGraph Structure

### Nodes

| Node | Function | Purpose |
|------|----------|---------|
| `agent` | `create_agent_node()` | Main LLM node with tools bound |
| `tools` | `ToolNode(tools)` | Executes agent's tool calls |
| `nudge` | `nudge_node()` | Injects continuation prompt after empty response |

### Edge Routing (`should_continue`)

```
START → agent

agent:
  ├── has tool_calls? ──────► tools
  │                            │
  │                         tools → agent
  │                            │
  ├── has reasoning_content? ──► agent (continue reasoning)
  │                            │
  ├── ends with ':' (ghost)? ──► agent (retry for actual tool_call)
  │                            │
  └── empty + recent tool? ───► nudge → agent
                                │
                                └──────────────────► END
```

### Nudge Mechanism

When the LLM returns an empty response after tool execution (it ACKs but doesn't continue), the graph injects:

```
"Continue with your task, or provide your final response if you are finished."
```

This prompts the agent to either continue working or properly finish.

---

## Architecture Notes

1. **InstanceMapper**: Maps external users to agent instances per source. Each user/source pair gets a dedicated instance for session continuity.

2. **Worker Pool**: Processes tasks from DB queue in separate threads. TaskProcessor routes to ProcessMessageProcessor for execution.

3. **LiveEventHub vs EventBus**:
   - `LiveEventHub`: Live-only SSE, fire-and-forget, no buffering
   - `EventBus`: For lifecycle events that need DB persistence + notification

4. **Circuit Breaker**: TelegramAdapter uses a circuit breaker to prevent cascading failures when the Telegram API is down.

5. **Rate Limiting**: TelegramAdapter limits to 30 msg/sec per chat using TokenBucketLimiter.
