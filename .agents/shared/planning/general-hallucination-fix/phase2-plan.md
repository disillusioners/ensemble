# Phase 2: Message Repair Engine

## Objective

Build the `LoopRepairer` class that performs the actual message repair when a loop is detected: removes repetitive messages, calls an LLM to summarize what happened, constructs a repair `SystemMessage`, and applies the state update via `graph.aupdate_state`. This reuses the compaction system's patterns (`RemoveMessage`, `_build_replacement_messages`, `_call_summarization_llm`).

## Coupling

- **Depends on**: Phase 1 (uses `LoopDetector`, `LoopDetectionResult`, `LoopBreakerSlot`)
- **Coupling type**: loose — Phase 2 imports Phase 1's classes but doesn't modify the same code regions
- **Shared files with other phases**: `daemon/graph.py` (new `LoopRepairer` class)
- **Shared APIs/interfaces**: `LoopRepairer.repair()`
- **Why this coupling**: Phase 2 depends on the detection result structure from Phase 1, but operates independently (separate class, separate concerns)

## Context

- Previous phase completed: Phase 1 provides `LoopDetector`, `LoopDetectionResult`, `LoopBreakerSlot`, `InstanceManager._loop_breaker_state`
- Key decisions: Reuse compaction patterns (`RemoveMessage`, `_call_summarization_llm`, `aupdate_state`). Fallback to static message if LLM summary fails. Always use fresh UUID for repair message.

## Tasks

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 1 | Implement `LoopRepairer` class | Core repair engine. Constructor takes `llm_config`, `graph_ref`, `compactor` (for LLM reuse). Method `repair(context) -> RepairResult`. | `daemon/graph.py` (new class) |
| 2 | Implement message removal logic | Given `LoopDetectionResult`, build `RemoveMessage(id=X)` list for repetitive messages (keep 1 instance as evidence). | `daemon/graph.py` (inside LoopRepairer) |
| 3 | Implement LLM summarization | Call LLM with focused prompt: summarize what the agent was doing, which tool it was calling repeatedly, and why it might be stuck. Reuse `clean_llm_config` pattern. Fallback to static message on error. | `daemon/graph.py` (inside LoopRepairer) |
| 4 | Implement repair message construction | Build `SystemMessage` with fresh UUID (`f"repair-{uuid4()}"`) containing summary + instruction to try different approach. | `daemon/graph.py` (inside LoopRepairer) |
| 5 | Implement state update + re-read | Call `graph.aupdate_state(thread_config, replacement, as_node='agent')`. Re-read state via `graph.aget_state`. Return updated messages for re-invocation. | `daemon/graph.py` (inside LoopRepairer) |
| 6 | Write unit tests for LoopRepairer | Test: removal list correct, LLM summary called with right prompt, repair message has fresh UUID, state update called correctly, fallback on LLM error, injected_msg re-append. Mock LLM and graph. | `tests/unit/test_loop_repairer.py` |

## Key Files

- `daemon/graph.py` — `LoopRepairer` class (new, ~line 200)
- `daemon/compaction.py` — Reference for `_call_summarization_llm`, `RemoveMessage`, `_build_replacement_messages` patterns
- `daemon/graph.py:899-958` — Reference for reactive compaction `aupdate_state` + re-read pattern

## Repair Algorithm (Step-by-Step)

### 1. Message Removal

```python
def _build_removal_list(
    detection: LoopDetectionResult,
) -> list[RemoveMessage]:
    """Build RemoveMessage sentinels for repetitive messages.
    
    Keeps the FIRST instance of the repetitive pattern as evidence
    (so the agent can see what it was doing), removes subsequent duplicates.
    """
    removals = []
    # detection.loop_messages contains all repetitive messages
    # detection.evidence_message_ids contains IDs to KEEP (first instance)
    for msg in detection.loop_messages:
        if msg.id and msg.id not in detection.evidence_message_ids:
            removals.append(RemoveMessage(id=msg.id))
    return removals
```

### 2. LLM Summarization

```python
REPAIR_SUMMARIZATION_PROMPT = """You are analyzing an AI agent's conversation history that has entered a repetitive loop.

The agent repeatedly called the tool "{tool_name}" with these arguments:
{tool_args}

This happened {count} times consecutively without making progress.

Recent conversation context:
{conversation_excerpt}

Please provide a concise summary (2-3 sentences) of:
1. What the agent was trying to accomplish
2. Why it appears to be stuck in a loop
3. What alternative approach it should try

Be specific and actionable."""

async def _summarize_loop(
    self,
    detection: LoopDetectionResult,
    messages: list,
    llm_config: dict,
    timeout_seconds: int = 30,
) -> str:
    """Call LLM to summarize the loop for repair message.

    Has a strict timeout (default 30s). If the LLM call times out or fails,
    falls back to a static truncation summary instead of blocking agent_node.
    """
    from .graph import ThinkingChatOpenAI, clean_llm_config

    # Build conversation excerpt (last 10 messages, text only)
    excerpt = self._build_excerpt(messages, max_messages=10)

    prompt = REPAIR_SUMMARIZATION_PROMPT.format(
        tool_name=detection.tool_name,
        tool_args=json.dumps(detection.tool_args, indent=2)[:500],
        count=detection.repetition_count,
        conversation_excerpt=excerpt,
    )

    # Static fallback summary — used on timeout or any error
    fallback_summary = (
        f"The agent called {detection.tool_name} {detection.repetition_count} times "
        f"with the same arguments without progress."
    )

    try:
        config = clean_llm_config(llm_config)
        llm = ThinkingChatOpenAI(**config)

        # CRITICAL: Wrap in asyncio.wait_for with a timeout.
        # Without this, a hung summarization call blocks agent_node indefinitely,
        # freezing the agent. On timeout we fall back to the static summary.
        response = await asyncio.wait_for(
            asyncio.to_thread(
                llm.invoke,
                [
                    SystemMessage(content="You are a helpful assistant that analyzes conversation patterns."),
                    HumanMessage(content=prompt),
                ],
            ),
            timeout=timeout_seconds,
        )
        return _extract_text_from_content(response.content)

    except asyncio.TimeoutError:
        logger.warning(
            f"[LoopRepairer] Summarization timed out after {timeout_seconds}s, "
            f"using truncation fallback"
        )
        return fallback_summary

    except Exception as e:
        logger.warning(f"[LoopRepairer] Summarization failed: {e}, using fallback")
        return fallback_summary
```

### 3. Repair Message Construction

```python
async def _build_repair_message(
    self,
    detection: LoopDetectionResult,
    summary: str,
) -> SystemMessage:
    """Construct the repair SystemMessage with fresh UUID."""
    repair_content = (
        f"[LOOP BREAKER — Repetitive tool call detected]\n\n"
        f"You have called the tool '{detection.tool_name}' {detection.repetition_count} "
        f"times consecutively with the same arguments. This indicates you may be "
        f"stuck in a loop.\n\n"
        f"Summary of what happened:\n{summary}\n\n"
        f"Please try a DIFFERENT approach. Consider:\n"
        f"- Using a different tool\n"
        f"- Changing the arguments\n"
        f"- Reviewing the available information before acting\n"
        f"- If the task is complete, provide your final response\n"
    )
    return SystemMessage(
        content=repair_content,
        id=f"repair-{uuid.uuid4()}",  # CRITICAL: fresh UUID, never reuse
    )
```

### 4. Full Repair Flow

```python
@dataclass
class RepairContext:
    """Context for a repair operation."""
    detection: LoopDetectionResult
    messages: list[BaseMessage]
    thread_config: dict
    graph: Any  # compiled graph
    llm_config: dict
    system_prompt: str
    injected_msg: BaseMessage | None = None  # re-append after repair
    summarization_timeout_seconds: int = 30   # LLM summarization call timeout

@dataclass
class RepairResult:
    """Result of a repair operation."""
    success: bool
    repaired_messages: list[BaseMessage]  # messages to use for LLM re-invocation
    summary: str
    repair_message_id: str
    error: str | None = None

class LoopRepairer:
    """Repairs message history when a hallucination loop is detected.
    
    Removes repetitive messages, summarizes via LLM, injects repair message,
    and applies state update via graph.aupdate_state.
    """
    
    def __init__(self, llm_config: dict | None = None, timeout_seconds: int = 30):
        self._llm_config = llm_config or {}
        self._timeout_seconds = timeout_seconds
    
    async def repair(self, context: RepairContext) -> RepairResult:
        """Execute the full repair flow."""
        try:
            # Step 1: Build removal list
            removals = self._build_removal_list(context.detection)
            logger.info(
                f"[LoopRepairer] Removing {len(removals)} repetitive messages "
                f"for tool '{context.detection.tool_name}'"
            )
            
            # Step 2: LLM summarization (with timeout — falls back to static on timeout)
            timeout = self._timeout_seconds or 30
            summary = await self._summarize_loop(
                context.detection,
                context.messages,
                context.llm_config,
                timeout_seconds=timeout,
            )
            
            # Step 3: Build repair message
            repair_msg = await self._build_repair_message(
                context.detection,
                summary,
            )
            
            # Step 4: Apply state update
            # Order: RemoveMessage sentinels FIRST, then repair message
            replacement = removals + [repair_msg]
            await context.graph.aupdate_state(
                context.thread_config,
                {'messages': replacement},
                as_node='agent',
            )
            logger.info(
                f"[LoopRepairer] State updated, repair message {repair_msg.id[:16]}... injected"
            )
            
            # Step 5: Re-read state
            updated_state = await context.graph.aget_state(context.thread_config)
            repaired_messages = updated_state.values.get('messages', [])
            
            # Step 6: Re-append injected_msg if present (C3 pattern)
            if context.injected_msg is not None:
                repaired_messages = list(repaired_messages) + [context.injected_msg]
            
            return RepairResult(
                success=True,
                repaired_messages=repaired_messages,
                summary=summary,
                repair_message_id=repair_msg.id,
            )
            
        except Exception as e:
            logger.error(f"[LoopRepairer] Repair failed: {e}", exc_info=True)
            return RepairResult(
                success=False,
                repaired_messages=context.messages,  # fallback to original
                summary="",
                repair_message_id="",
                error=str(e),
            )
```

## Constraints

- **Fresh UUID always**: Repair message MUST use `f"repair-{uuid4()}"`. Reusing an existing ID replaces instead of appends (LangGraph `add_messages` reducer behavior).
- **RemoveMessage order**: Sentinels must come BEFORE the repair message in the replacement list (reducer processes left-to-right).
- **LLM config cleaning**: MUST call `clean_llm_config()` before constructing `ThinkingChatOpenAI` (strips `model_vision`).
- **LLM summarization timeout**: The `_summarize_loop` call MUST be wrapped in `asyncio.wait_for(timeout=timeout_seconds)`. A hung summarization call would block `agent_node` indefinitely, freezing the agent. On timeout, fall back to a static truncation summary instead of the LLM-generated one. Default timeout: 30s (configurable via `LoopBreakerConfig.summarization_timeout_seconds`).
- **Fallback on error**: If summarization fails or times out, use static fallback message. If full repair fails, return original messages unchanged.
- **Injected message re-append**: MUST re-append `injected_msg` after state re-read (C3 pattern from graph.py:944-950).
- **No mutation of preserved messages**: Don't mutate message content in-place (unlike `_build_replacement_messages` which flattens multimodal).
- **State update must use `as_node='agent'`**: Same as reactive compaction (graph.py:928).

## Deliverables

- [ ] `LoopRepairer` class with `repair()` method
- [ ] Message removal logic (RemoveMessage list builder)
- [ ] LLM summarization with prompt + fallback
- [ ] Repair message construction (fresh UUID)
- [ ] State update + re-read flow
- [ ] Unit tests for LoopRepairer (mock LLM + graph)
