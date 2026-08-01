# Context Injection Bug Reproduction Report
**Date:** 2026-07-28
**Feature:** Context Injection Restructure (`[SYSTEM CONTEXT: ...]` HumanMessages)
**Status:** 🔴 BUG CONFIRMED — Context injection silently disabled for 22/28 agents

---

## Executive Summary

The Context Injection Restructure feature (deployed 2026-07-28) is **silently broken** for the majority of agents. After commit `cc9ea7cc` ("flip context_injection_mode default to human_messages"), all agents default to `human_messages` mode correctly. However, the actual message-building gate inside `assemble_context_messages()` still checks the **legacy `context_injection` boolean flag** (not the mode). Since this boolean was never set on most agents, the function returns `[]` — no context messages are ever produced.

**Impact:** 22 out of 28 agents receive zero `[SYSTEM CONTEXT: ...]` messages. Affected agents include all critical leader-team members: `leader`, `developer`, `reviewer`, `planner`, `explorer`, `wanderer`, `tidier`, `approver`, `giter`, `doc-writer`.

---

## Root Cause

### The Dual-Flag Disconnect

The code has **two independent gates** that must both pass:

**Gate 1 (mode resolution) — WORKS CORRECTLY:**
```python
# daemon/services/instance_lifecycle.py:1088-1093
def _resolve_injection_mode(agent_meta):
    if agent_meta is None:
        return ContextInjectionMode.HUMAN_MESSAGES  # default
    mode = getattr(agent_meta, "context_injection_mode", None)
    if mode in VALID_INJECTION_MODES:
        return mode
    return ContextInjectionMode.HUMAN_MESSAGES  # default
```
→ Correctly resolves to `"human_messages"` for all agents after `cc9ea7cc`.

**Gate 2 (feature-flag gate) — BROKEN:**
```python
# daemon/services/context_messages.py:965-969
context_enabled = bool(getattr(agent_meta, "context_injection", False))  # ← legacy boolean
skills_enabled = bool(getattr(agent_meta, "skill_injection", False))
if not context_enabled and not skills_enabled:
    return []  # ← returns NO context messages
```
→ Returns `[]` for any agent without `context_injection: true` OR `skill_injection: true`.

### Commit Chain That Introduced the Bug

1. **`1acaa7cd`** (17:40) — Added `context_injection_mode: "human_messages"` to 13 agents. Developer got the flag but NEVER had `context_injection: true`.
2. **`cc9ea7cc`** (18:52) — Removed `context_injection_mode` from all 13 agents (making `human_messages` the implicit default). Did NOT add `context_injection: true`.

**Result:** Mode resolution says "human_messages" → `ContextSlot.assemble()` does NOT early-return → calls `assemble_context_messages()` → **gate returns `[]` because `context_injection` boolean is absent**.

### Where the Code Path Breaks

```
ContextSlot.assemble() [graph.py:430-435]
  → mode = _resolve_injection_mode(agent_meta)  → "human_messages" ✅
  → if mode != "human_messages": return []      → does NOT return ✅
  → return await assemble_context_messages(...)  → enters orchestrator
      → context_enabled = getattr(agent_meta, "context_injection", False)  → False ❌
      → skills_enabled = getattr(agent_meta, "skill_injection", False)     → False ❌
      → return []  ← SILENT FAILURE
```

---

## Evidence — API Responses

### Scenario 1: Root Leader Instance (CORRECT by design)
- **Instance:** `7765240f...` (leader, project=agents-ensemble)
- **Messages:** 0 context messages
- **System prompt:** 58983 chars — persona + documentation only
- **Verdict:** ✅ Correct — `leader` explicitly sets `context_injection: false`

### Scenario 2: Child Developer (Leader-spawned) — 🔴 BUG
- **Instance:** `040828fb-24f1-4f5a-917b-e4336a9a9929` (developer, parent=leader)
- **Messages:** 7 total, **0 context messages**
- **System prompt:** Does NOT contain `## Related Project` or any injected context
- **AI behavior:** Agent had to manually fetch context — said "Let me find the project directory" instead of having it injected
- **Verdict:** 🔴 BUG — developer should receive project + shared context

### Scenario 3: Direct Developer Instance — 🔴 BUG
- **Instance:** `ce370efd-32c7-4908-b038-45a4a510e36a` (developer, direct spawn)
- **Messages:** 4 total, **0 context messages**
- **Message structure:**
  ```
  [0] type=system    | is_synthetic=True  | context_kind=None  | persona only (no project data)
  [1] type=human     | is_synthetic=None  | context_kind=None  | "What files are in this project?"
  [2] type=ai        | content="Tôi sẽ kiểm tra ngữ cảnh dự án trước..." (I'll check project context first)
  [3] type=ai        | content="Đây là dự án Agents Ensemble..." (fetched manually via explore)
  ```
- **System prompt length:** 58983 chars (persona + docs, no injected context)
- **`[SYSTEM CONTEXT: ...]` in system prompt:** Only the 3-dot documentation placeholder, NOT actual context
- **Verdict:** 🔴 BUG — developer should receive project + shared context

### Control: Tester Instance (CORRECT — proves orchestrator works)
- **Instance:** `e1f911af-eee6-4257-bb9d-19bcc52e26ca` (tester, direct spawn)
- **Messages:** 5 total, **1 context message** ✅
- **Message structure:**
  ```
  [0] type=system    | is_synthetic=True  | context_kind=None  | system prompt
  [1] type=human     | is_synthetic=True  | context_kind=skills | [SYSTEM CONTEXT: Skills] ... ← INJECTED!
  [2] type=human     | is_synthetic=None  | context_kind=None  | "What test packs exist?"
  [3] type=ai        | ...
  [4] type=ai        | ...
  ```
- **Context message format (the "gold" pattern):**
  ```json
  {
    "message_id": "synthetic-context-skills-{iid}-0",
    "type": "human",
    "role": "user",
    "is_synthetic": true,
    "context_kind": "skills",
    "content": "[SYSTEM CONTEXT: Skills]\n\n📋 **Skill: test-pack-execution**..."
  }
  ```
- **Verdict:** ✅ Correct — tester has `skill_injection: true`, so the skills context message is injected. Proves the orchestrator works when a flag is present.

---

## Affected Agents (22 of 28)

| Agent | `context_injection` | `skill_injection` | Gets Context? |
|-------|:---:|:---:|:---:|
| leader | false | false | ❌ NO (by design) |
| **developer** | false | false | ❌ **NO (BUG)** |
| **reviewer** | false | false | ❌ **NO (BUG)** |
| **planner** | false | false | ❌ **NO (BUG)** |
| **explorer** | false | false | ❌ **NO (BUG)** |
| **wanderer** | false | false | ❌ **NO (BUG)** |
| **tidier** | false | false | ❌ **NO (BUG)** |
| **approver** | false | false | ❌ **NO (BUG)** |
| **giter** | false | false | ❌ **NO (BUG)** |
| **doc-writer** | false | false | ❌ **NO (BUG)** |
| coder | true | false | ✅ YES |
| developer[v2] | true | false | ✅ YES |
| devops | false | true | ✅ YES |
| governor | true | false | ✅ YES |
| reviewer[v2] | true | true | ✅ YES |
| tester | false | true | ✅ YES |
| worker | false | true | ✅ YES |

---

## Fix Direction

### Option 1 (Recommended): Gate on mode, not legacy boolean
Replace the feature-flag gate in `assemble_context_messages()` (`daemon/services/context_messages.py:965-969`):

```python
# CURRENT (broken):
context_enabled = bool(getattr(agent_meta, "context_injection", False))
skills_enabled = bool(getattr(agent_meta, "skill_injection", False))
if not context_enabled and not skills_enabled:
    return []

# FIX: Always build project/shared context in human_messages mode;
# skills still gated on skill_injection flag
mode = _resolve_injection_mode(agent_meta)
if mode != ContextInjectionMode.HUMAN_MESSAGES:
    return []
context_enabled = True  # mode gate is sufficient
skills_enabled = bool(getattr(agent_meta, "skill_injection", False))
if not context_enabled and not skills_enabled:
    return []
```

**Rationale:** The mode resolver already determines behavior. The legacy boolean gate is vestigial — it was the pre-restructure opt-in for system-prompt injection. In `human_messages` mode, project + shared context should always be delivered.

### Option 2 (Band-aid): Add `context_injection: true` to affected agents
Re-add the boolean to the 13 agents that had `context_injection_mode` removed. This re-introduces the confusing dual-flag state and doesn't fix the underlying design flaw.

---

## Additional Notes

- **Old instances (pre-feature):** Developer instances created before 2026-07-28 show context **prepended to user message content** (legacy behavior). This is NOT the bug — those instances used the old `system_prompt` mode before the restructure.
- **No daemon errors logged:** The failure is silent — `assemble_context_messages` returns `[]` without logging any warning. There is no error to surface; the gate simply returns empty.
- **GET /messages reconstruction:** The read path (`_build_context_dicts_for_response`) also calls `assemble_context_messages`, so the same bug affects the API response — context messages are never visible in GET /messages either.
