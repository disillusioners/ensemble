# Phase 1: Detection System

## Objective

Build the general loop-detection infrastructure: a `LoopDetector` that scans message history for 3+ consecutive identical tool calls (any tool), backed by a `LoopBreakerSlot` (duck-typed handle mirroring `ToolThrottleSlot`) and per-instance state on `InstanceManager`. Plus config fields and constants.

## Coupling

- **Depends on**: None (root phase)
- **Coupling type**: — (root)
- **Shared files with other phases**: `daemon/graph.py` (constants + slot class), `daemon/manager.py` (state + accessors), `daemon/config.py` (config fields)
- **Shared APIs/interfaces**: `LoopDetector`, `LoopBreakerSlot`, `InstanceManager._loop_breaker_state`
- **Why this coupling**: Phase 2 imports `LoopDetector`; Phase 3 wires `LoopBreakerSlot` into `agent_node`. Defining interfaces first enables loose-coupled pipelining.

## Context

- Previous phase completed: N/A (root phase)
- Key decisions: Detection operates on message-list scanning (not just `messages[-1]`), handles parallel tool calls via signature-based grouping, keeps 1 instance of repetitive call as evidence.

## Tasks

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 1 | Add config fields | `LoopBreakerConfig` dataclass with: `enabled: bool = True`, `threshold: int = 3`, `max_repairs: int = 3`, `summarization_timeout_seconds: int = 30`, `excluded_tools: list[str] = []`, `summarization_model: str \| None = None`. Add to `config.py` LimitsConfig or as standalone section. | `daemon/config.py` |
| 2 | Add constants | `LOOP_BREAKER_DEFAULT_THRESHOLD = 3`, `LOOP_BREAKER_DEFAULT_MAX_REPAIRS = 3`, `LOOP_BREAKER_SUMMARIZATION_TIMEOUT_SECONDS = 30`, `LOOP_BREAKER_REPAIR_PREFIX = "repair-"`, `LOOP_BREAKER_SUMMARY_PROMPT` template. Near `GII_DELAY_MAP` at graph.py:35-41. | `daemon/graph.py` |
| 3 | Implement `LoopDetector` | Static class with `scan(messages: list, threshold: int, excluded_tools: list) -> LoopDetectionResult | None`. Walks backwards, groups AI+Tool units, computes signatures, counts consecutive identical ones. | `daemon/graph.py` (new class ~line 148) |
| 4 | Implement `LoopBreakerSlot` | Duck-typed handle mirroring `ToolThrottleSlot` (graph.py:112-146). Methods: `get_state(instance_id) -> dict`, `record_repair(instance_id, summary: str)`, `clear(instance_id)`, `get_repair_count(instance_id) -> int`. Uses `getattr` delegation. | `daemon/graph.py` |
| 5 | Add InstanceManager state | `self._loop_breaker_state: dict[str, dict] = {}` next to `_gii_throttle` (manager.py:731). Each entry: `{"count": int, "last_summary": str, "last_repair_at": str}`. Add 4 accessor methods next to `bump_gii_throttle` (manager.py:2028). | `daemon/manager.py` |
| 6 | Write unit tests for LoopDetector | Test: sequential identical calls detected, different args not detected, parallel tool calls handled, threshold respected, excluded tools skipped, mixed tools reset count. Mock-based (no real manager). | `tests/unit/test_loop_detector.py` |

## Key Files

- `daemon/config.py` — LoopBreakerConfig fields
- `daemon/graph.py:35-41` — Constants near GII constants
- `daemon/graph.py:112-148` — LoopDetector + LoopBreakerSlot (new classes)
- `daemon/manager.py:729-731` — `_loop_breaker_state` declaration
- `daemon/manager.py:2028-2045` — Accessor methods

## Detection Algorithm (Detailed)

### Signature Computation

```python
def _compute_tool_signature(ai_message: AIMessage) -> str:
    """Compute a canonical signature for an AIMessage's tool calls.
    
    Groups all tool_calls in the message into a sorted set of (name, args) pairs.
    Handles parallel tool calls: multiple calls in one message = one signature.
    """
    if not ai_message.tool_calls:
        return ""
    # Sort by tool name, then by args (JSON-serialized for determinism)
    pairs = []
    for tc in ai_message.tool_calls:
        name = tc.get("name", "")
        args = tc.get("args", {})
        # Canonical JSON: sorted keys, no whitespace
        args_str = json.dumps(args, sort_keys=True, separators=(",", ":"))
        pairs.append(f"{name}:{args_str}")
    pairs.sort()
    return "|".join(pairs)
```

### Scan Algorithm

```python
@dataclass
@dataclass
class LoopDetectionResult:
    """Result of loop detection scan.

    IMPORTANT: ``loop_messages`` excludes the evidence unit. The evidence
    unit (oldest matching call+result pair) is preserved so the agent has
    context about what it was doing. Only its IDs appear in
    ``evidence_message_ids`` and are excluded from removal.
    """
    tool_name: str                      # primary tool in the loop
    tool_args: dict                     # canonical args of the loop
    repetition_count: int               # how many times repeated
    loop_messages: list[BaseMessage]    # repetitive messages to REMOVE (excludes evidence)
    evidence_message_ids: list[str]     # IDs to KEEP (1 oldest call+result pair as evidence)

class LoopDetector:
    @staticmethod
    def scan(
        messages: list,
        threshold: int = 3,
        excluded_tools: list[str] = None,
    ) -> LoopDetectionResult | None:
        """Scan message tail for consecutive identical tool-call patterns.
        
        Returns LoopDetectionResult if a loop is detected, None otherwise.
        
        Algorithm:
        1. Walk backwards from messages[-1]
        2. Group (AIMessage with tool_calls + matching ToolMessages) into units
        3. Compute signature for each unit
        4. Count consecutive units with same signature
        5. If count >= threshold, return result with messages to remove
        """
        excluded_tools = excluded_tools or []
        
        # Build units walking backwards
        units = []  # list of (signature, [message_indices])
        i = len(messages) - 1
        while i >= 0:
            msg = messages[i]
            if isinstance(msg, ToolMessage):
                # Find matching AIMessage (walk back to find the AIMessage 
                # that issued this tool_call_id)
                # Group: AIMessage + all its ToolMessages
                # ... (implementation details)
                pass
            elif isinstance(msg, AIMessage) and msg.tool_calls:
                sig = LoopDetector._compute_tool_signature(msg)
                if not sig:
                    break
                # Check if all tools in this unit are excluded
                tool_names = {tc.get("name", "") for tc in msg.tool_calls}
                if tool_names.issubset(set(excluded_tools)):
                    break  # excluded tool, stop counting
                units.append((sig, i))
            else:
                break  # non-tool message breaks the consecutive chain
            i -= 1
        
        if not units:
            return None
        
        # Count consecutive identical signatures
        first_sig = units[0][0]
        consecutive = 0
        loop_indices = []
        for sig, idx in units:
            if sig == first_sig:
                consecutive += 1
                loop_indices.append(idx)
            else:
                break
        
        if consecutive < threshold:
            return None
        
        # Build result: keep FIRST (oldest) instance as evidence, remove the rest.
        # loop_indices are in reverse chronological order (newest first from
        # the backwards walk). The LAST entry in loop_indices is the oldest —
        # that's the one we keep as evidence so the agent can see what it was
        # doing before the loop. All newer duplicates are removed.
        
        # Identify the evidence unit (oldest matching unit = last in loop_indices)
        evidence_unit_idx = loop_indices[-1]  # oldest matching AIMessage
        evidence_ai_msg = messages[evidence_unit_idx]
        
        # Collect all message IDs in the evidence unit (AIMessage + its ToolMessages)
        evidence_message_ids: set[str] = set()
        if evidence_ai_msg.id:
            evidence_message_ids.add(evidence_ai_msg.id)
        # Find matching ToolMessages for the evidence AIMessage's tool_calls
        for tc in (evidence_ai_msg.tool_calls or []):
            tc_id = tc.get("id", "")
            for msg in messages:
                if isinstance(msg, ToolMessage) and getattr(msg, "tool_call_id", "") == tc_id:
                    if msg.id:
                        evidence_message_ids.add(msg.id)
        
        # Collect loop_messages: all messages in the repetitive units EXCEPT evidence
        loop_messages = []
        for idx in loop_indices[:-1]:  # skip the evidence unit (last = oldest)
            ai_msg = messages[idx]
            loop_messages.append(ai_msg)
            # Also collect its matching ToolMessages
            for tc in (ai_msg.tool_calls or []):
                tc_id = tc.get("id", "")
                for msg in messages:
                    if isinstance(msg, ToolMessage) and getattr(msg, "tool_call_id", "") == tc_id:
                        loop_messages.append(msg)
        
        # Extract tool name + args from the first detected unit for the summary
        first_ai_msg = messages[loop_indices[0]]
        first_tc = (first_ai_msg.tool_calls or [{}])[0]
        
        return LoopDetectionResult(
            tool_name=first_tc.get("name", "unknown"),
            tool_args=first_tc.get("args", {}),
            repetition_count=consecutive,
            loop_messages=loop_messages,
            evidence_message_ids=list(evidence_message_ids),
        )
```

### LoopBreakerConfig Definition

```python
# config.py

class LoopBreakerConfig(BaseModel):
    """Configuration for the general hallucination loop breaker."""
    enabled: bool = True
    threshold: int = 3                              # consecutive identical calls to trigger
    max_repairs: int = 3                            # max repair attempts per instance before giving up
    summarization_timeout_seconds: int = 30         # timeout for the repair LLM summarization call
    excluded_tools: list[str] = []                  # tools to skip during detection
    summarization_model: str | None = None          # optional model override for summarization
```

### InstanceManager Accessors

```python
# manager.py — next to bump_gii_throttle (~line 2028)

def get_loop_breaker_state(self, instance_id: str) -> dict:
    """Return loop-breaker state for instance (empty dict if unset)."""
    return self._loop_breaker_state.get(instance_id, {})

def record_loop_repair(self, instance_id: str, summary: str) -> int:
    """Record a repair event. Returns new repair count."""
    state = self._loop_breaker_state.get(instance_id, {"count": 0})
    state["count"] = state.get("count", 0) + 1
    state["last_summary"] = summary
    state["last_repair_at"] = datetime.utcnow().isoformat()
    self._loop_breaker_state[instance_id] = state
    return state["count"]

def reset_loop_breaker(self, instance_id: str) -> None:
    """Clear loop-breaker state for instance."""
    self._loop_breaker_state.pop(instance_id, None)

def get_loop_repair_count(self, instance_id: str) -> int:
    """Return current repair count (0 if unset)."""
    return self._loop_breaker_state.get(instance_id, {}).get("count", 0)
```

### LoopBreakerSlot

```python
# graph.py — next to ToolThrottleSlot (~line 148)

class LoopBreakerSlot:
    """Lightweight, mock-friendly handle around InstanceManager loop-breaker state.
    
    Mirrors ToolThrottleSlot's duck-typed getattr pattern.
    """
    
    def __init__(self, manager: Any) -> None:
        self._manager = manager
    
    def get_state(self, instance_id: str) -> dict:
        getter = getattr(self._manager, "get_loop_breaker_state", None)
        if getter is None:
            return {}
        return getter(instance_id)
    
    def record_repair(self, instance_id: str, summary: str) -> int:
        recorder = getattr(self._manager, "record_loop_repair", None)
        if recorder is None:
            return 0
        return recorder(instance_id, summary)
    
    def clear(self, instance_id: str) -> None:
        clearer = getattr(self._manager, "reset_loop_breaker", None)
        if clearer is not None:
            clearer(instance_id)
    
    def get_repair_count(self, instance_id: str) -> int:
        getter = getattr(self._manager, "get_loop_repair_count", None)
        if getter is None:
            return 0
        return getter(instance_id)
```

## Constraints

- Detection must handle parallel tool calls (multiple tool calls in one AIMessage)
- Signature must be deterministic (sorted keys, canonical JSON)
- Walking backwards must stop at any non-tool message (HumanMessage, etc.)
- Excluded tools list must be checked per-unit (if ANY tool in the unit is non-excluded, count it)
- Must be mock-friendly (duck-typed, no hard InstanceManager dependency in tests)

## Deliverables

- [ ] `LoopBreakerConfig` in config.py
- [ ] Constants in graph.py
- [ ] `LoopDetector` class with `scan()` method
- [ ] `LoopBreakerSlot` class (duck-typed handle)
- [ ] `InstanceManager._loop_breaker_state` + 4 accessor methods
- [ ] Unit tests for LoopDetector (mock-based)
