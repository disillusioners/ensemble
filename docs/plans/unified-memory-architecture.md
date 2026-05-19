# Unified Memory Architecture — Requirements

## Problem Statement

### Problem 1: Target Ambiguity

`inner_soul` exposes two distinct memory targets that are semantically indistinguishable to the calling agent:

| target value | Storage | When used |
|---|---|---|
| `"memory"` | Single `memory.md` file, appended bullet list | Agent must know internal implementation |
| `"memories"` | `memories/YYYYMMDD_slug.md` timestamped files | Agent must know internal implementation |

**Current routing** (`inner_soul.py:304-315`):
- `target="memory"` → only `memory.md` updated
- `intent="remember"` + no target → only `memories/` updated
- `intent="learn"` + no target → both updated

**Problem**: The agent should not need to know the difference. A request to "remember something" should go to the right place automatically. The LLM cannot meaningfully choose between `memory` and `memories` — they represent the same semantic intent.

**Evidence**: Your input used `target: "memory"` → wrote to `memory.md`. If you had used `target: "memories"` or omitted the target, it would have gone to `memories/` directory. Both are called "memory" from the agent's perspective.

---

### Problem 2: No Compaction — Hard Rejection on Write

When `memory.md` reaches `max_memory_words` (default 2000 words), new writes are **hard rejected**:

```python
# inner_soul.py:512-517
if word_count >= max_words:
    return {
        "success": False,
        "target": "memory",
        "error": f"memory.md at {word_count} words (max {max_words}). Saved to memories/ instead."
    }
```

**Problems**:
1. "Saved to memories/ instead" is a **lie** — the code returns an error, it doesn't actually save anywhere
2. The write is lost — the agent's intention to remember is discarded
3. No compaction attempt — no deduplication, no summarization, no archival
4. `rule.md` says 500 words max, `growth.md` parsing returns 2000 — inconsistent limits

---

### Problem 3: Unbounded Growth of `memories/` — Invisible Memories

`memories/` directory is **append-only, never cleaned up**:

- Only 5 most recent filenames appear in the system prompt (`loader.py:181-200`)
- All older memories are completely invisible to the agent at runtime
- No TTL, no archival, no consolidation
- The "recent memories" section shows filenames, not content — agent must call `access_memory()` to read each one

**Problems**:
1. Older memories become permanently inaccessible
2. No lifecycle management — memories from 2 years ago keep accumulating
3. Agent cannot learn from past observations without manually calling `access_memory()`

---

### Problem 4: Regex-Based Classification is Brittle

`inner_soul.py:34-172` defines 11 classification types using regex patterns:

- `"knowledge"` patterns: `remember that`, `note that`, `important`, `don't forget`, `keep in mind`, `i learned that`, `i now know`
- `"pattern"` patterns: `pattern:`, `i noticed that when`, `always when`, `it seems like`
- etc.

**Problems**:
1. Natural phrasing outside these exact patterns falls through to default `event → memories/`
2. Example: `"Context7 is built-in MCP server"` — no regex matches → goes to `memories/` as "event"
3. No handling for compound requests like `"remember my name AND that tests are important"`
4. Classification drives storage decision but the agent has no visibility into this

---

### Problem 5: Dead Documentation in `agents/_inner_soul/`

The `agents/_inner_soul/` directory contains `soul.md`, `rule.md`, and `workflow.md` describing "inner_soul" as an intelligent agent that classifies requests semantically.

**Reality**: These files are never loaded into any LLM's context. The directory starts with `_` and is in `SKIP_DIRS` in `registry.py`. The "smart agent" described in the docs is actually a 771-line Python function with regex-based if/else logic.

**Problem**: The documentation describes behavior that doesn't exist. This is misleading for anyone reading the codebase.

---

## Requirements

### REQ-1: Unified Memory Interface

The agent-facing interface should present a **single memory concept**. The agent calls `inner_soul(request="...", intent="remember")` without specifying `target`. The tool decides internally:

1. Where to store the information (`memory.md` vs `memories/`)
2. Whether to compact before writing
3. Whether to archive old memories

**The agent should never need to choose between `memory` and `memories`.**

Rationale: Agents cannot meaningfully distinguish these internal storage mechanisms. The distinction is an implementation detail that should be hidden.

### REQ-2: Graceful Degradation with Compaction

When `memory.md` approaches its size limit, the system should **compact before rejecting**:

1. **Deduplicate** — Remove bullets that are exact or near-duplicates
2. **Archive** — Move low-priority/old bullets to `memories/` before writing new ones
3. **Summarize** (if needed) — Replace groups of related bullets with a single concise bullet

**Write operations should never silently fail.** If the agent asks to remember something, it gets remembered one way or another.

### REQ-3: Memory Lifecycle Management

`memories/` directory needs lifecycle management:

1. **TTL-based archival** — Memories older than N days (configurable, default 30) should be moved to `memories/archive/`
2. **Consolidation** — Archived memories should be merged into monthly summary files
3. **Visibility** — Archived memories should remain accessible via `access_memory()` and referenced in prompt
4. **Configurable limits** — Archive threshold should be configurable per-agent via `growth.md`

### REQ-4: Smarter Classification

Classification should handle:

1. **Natural phrasing** — Requests that don't match regex patterns should still be correctly classified
2. **Compound requests** — "Remember X and Y" should update both memories, correctly classified
3. **Importance weighting** — High-importance items (identity, lessons learned, patterns) should go to `memory.md`. Events and observations should go to `memories/`.
4. **Fallback** — When regex fails, use LLM-based classification (configurable, off by default for performance)

### REQ-5: Accurate Documentation or Deletion

Either:
- **`Option A`**: Delete `agents/_inner_soul/` entirely — it describes non-existent behavior
- **`Option B`**: Actually implement `inner_soul` as a real sub-agent that reads those files

Recommendation: **Option A** for now. The tool's intelligence belongs in Python code, not markdown files that no LLM reads.

### REQ-6: Backward Compatibility

All existing `inner_soul` calls must continue to work:
- `inner_soul(intent="remember", target="memory", request="...")` → works
- `inner_soul(intent="learn", request="...")` → works (updates both)
- `inner_soul(intent="change", target="workflow", request="...")` → works (unchanged)

The unified memory interface should be an **additional capability**, not a breaking change.

---

## Open Questions

1. **Who triggers compaction?** Should it happen:
   - On every write that approaches the limit (blocks the tool call)
   - As a background task after writes (async, doesn't block)
   - On a schedule (e.g., daily cleanup job)

2. **What defines "importance" for routing?**
   - Should `memory.md` store only persistent self-knowledge, and `memories/` store everything else?
   - Or should `memory.md` store the most recent/important items regardless of type?

3. **Should `memories/` content be inlined in the prompt?**
   - Currently only filenames (5 most recent) are shown
   - Should we show content of all memories? Or a subset?
   - Tradeoff: prompt token size vs agent access to information

4. **Compaction strategy:**
   - Deduplication: exact match vs semantic similarity?
   - Summarization: LLM-based or rule-based?
   - Who approves compaction changes (agent? user? automatic)?

---

## Out of Scope

- Conversation history compaction (handled by `daemon/compaction.py`)
- RAG/Knowledge Base integration (handled by `daemon/rag/`)
- Soul/user/workflow file management (already have rate limits, keep existing behavior)
