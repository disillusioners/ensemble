# Re-Review: Context Compaction Plan v2

## Review Summary
**Needs Work** — [10 findings: 3 critical, 4 warnings, 3 suggestions]

The v2 revisions demonstrate strong responsiveness to the original review. CRIT-1 (RemoveMessage) is correctly fixed and experimentally verified. CRIT-2 and CRIT-3 have sound strategies but have implementation gaps. One new critical issue was discovered: the dedup mechanism (WARN-2) is completely non-functional due to state schema mismatch.

## Scope
v2 revisions of all 6 planning files, with experimental verification against the actual LangGraph runtime.

## Verification Results

### ✅ CRIT-1 (RemoveMessage) — FIXED CORRECTLY

The RemoveMessage sentinel pattern is correct. Experimentally verified:

1. `add_messages` reducer handles `RemoveMessage` by deleting messages with matching IDs
2. Preserved messages re-added with same IDs are deduplicated (not duplicated)
3. New messages (summary) are correctly appended
4. Full pattern: `[RemoveMessage(h1), RemoveMessage(a1), SystemMessage(summary), HumanMessage(h3, same_id)]` → correctly produces 3 messages

**One caveat:** The plan's `_build_replacement_messages()` must remove ALL messages in compactable groups (including AI responses, not just HumanMessages). The current design at phase2-plan.md:308-311 iterates all messages in each group, which is correct.

### ⚠️ CRIT-2 (Progressive Window Reduction) — PARTIALLY FIXED

The progressive reduction strategy is sound, but two issues remain:

### ⚠️ CRIT-3 (Chunked Summarization) — STRATEGY CORRECT, IMPLEMENTATION GAPS

Two helper methods are called but never defined.

### ✅ WARN-1 (SystemMessage) — FIXED CORRECTLY

Summary now uses `SystemMessage` with `[Conversation Summary]` marker. Semantically correct and unambiguous.

### ❌ WARN-2 (Dedup) — NOT FIXED — NEW CRITICAL ISSUE FOUND

### ✅ WARN-5 (Retry Skip) — FIXED CORRECTLY

`if not is_retry:` guard at phase3-plan.md:140 is correct and well-placed.

### ✅ WARN-6 (Config Wiring) — FIXED CORRECTLY

Explicit `load_config()` wiring noted in phase1-plan.md:109-118.

---

## Findings

### 🔴 Critical

#### REV-CRIT-1: Dedup mechanism is completely non-functional
**Area:** Phase 3 / Decision 9  
**Evidence:** phase3-plan.md:109-114 (write), phase3-plan.md:221-223 (read)

The `compacted_at` timestamp is stored via `aupdate_state({"compacted_at": ...})`. But `MessagesState` only defines a `messages` channel — no `compacted_at` field. **Experimentally confirmed**: the key is silently dropped. The read at `state.metadata.get("compacted_at")` also looks in the wrong place — `aupdate_state` writes to `state.values`, not `state.metadata`.

Result: `_get_last_compacted_at()` always returns `None` → dedup never fires → compaction runs on every single message → wasted LLM calls.

**Fix options:**
1. **Extend state schema**: Create `CompactionState(MessagesState)` with `compacted_at: Optional[str] = None` — requires changing `StateGraph(MessagesState)` to `StateGraph(CompactionState)` in `graph.py`
2. **Embed in messages**: Store a hidden `SystemMessage` with `[CompactionMarker]` as a marker — ugly but works with current schema
3. **Use checkpoint metadata**: LangGraph's `put_metadata` API (if available in installed version)

Option 1 is cleanest but touches `graph.py`. Option 3 is best if the LangGraph version supports it.

---

#### REV-CRIT-2: `_truncate_batch_to_fit()` and `_merge_summaries()` are undefined
**Area:** Phase 2 / Chunked Summarization  
**Evidence:** phase2-plan.md:250, phase2-plan.md:265

Two methods are called in the chunked summarization flow but have no design, no pseudocode, and no task entry:
- `_truncate_batch_to_fit()` at line 250 — truncates messages within a batch to fit threshold
- `_merge_summaries()` at line 265 and line 384 — merges N partial summaries into one final summary

These are non-trivial methods. `_truncate_batch_to_fit` must decide how to truncate (drop messages? trim content?). `_merge_summaries` could itself overflow the context window if there are many partial summaries.

**Recommendation:** Add task entries and design sections for both methods. For `_merge_summaries`, consider iterative pairwise merging to prevent overflow.

---

#### REV-CRIT-3: Infinite loop when session has ≤ `min_recent_window` groups exceeding threshold
**Area:** Phase 2 / `compact_state()`  
**Evidence:** phase2-plan.md:201-205, phase2-plan.md:372

When `len(groups) <= min_recent_window` (e.g., 3 groups on a very small model), `select_compactable_groups` returns `compactable = groups[:-min_window]` which is `[]` (empty). Then `compact_state()` line 372 returns `None`. But tokens still exceed threshold → LLM call fails → next message → same scenario → infinite failure loop.

This is the same root cause as the original CRIT-2, just pushed to a lower boundary. The progressive reduction helps for sessions with MANY groups, but doesn't help when there are VERY FEW groups that are individually large.

**Recommendation:** When `compactable` is empty but tokens exceed threshold, the plan should either:
1. Force-truncate within the preserved groups (trim ToolMessage content, which is often the largest)
2. Log a warning and accept the overflow (let the LLM error naturally)
3. Set a "compaction_failed" flag to avoid re-checking on every message

---

### 🟡 Warnings

#### REV-WARN-1: Hardcoded 0.80 threshold in `select_compactable_groups`
**Area:** Phase 2  
**Evidence:** phase2-plan.md:192

`threshold = context_window * 0.80` is hardcoded. But the triggering threshold at line 353 uses `context.config.threshold`. These are inconsistent — if a user sets `threshold: 0.70` in config, the progressive reduction still targets 80%.

**Recommendation:** Pass `threshold_ratio` as a parameter from config.

---

#### REV-WARN-2: Two separate `aupdate_state` calls for messages + metadata
**Area:** Phase 3  
**Evidence:** phase3-plan.md:102-114

Two `aupdate_state` calls: one for messages, one for `compacted_at`. Each creates a checkpoint. This is unnecessary overhead and introduces a window where the state is partially updated.

**Recommendation:** After fixing REV-CRIT-1 (state schema), merge into a single call: `aupdate_state(config, {"messages": ..., "compacted_at": ...})`.

---

#### REV-WARN-3: `_merge_summaries` could overflow context window
**Area:** Phase 2  
**Evidence:** phase2-plan.md:265

If there are 10+ batches, each producing a 200-400 token summary, the merge call receives 2000-4000 tokens of partial summaries plus the merge prompt. This could overflow on small-context models.

**Recommendation:** Use hierarchical/iterative merging (merge pairs, then merge results) or cap partial summary sizes.

---

#### REV-WARN-4: Integration test uses `create_session` instead of `spawn_session`
**Area:** Phase 4  
**Evidence:** phase4-plan.md:83

The actual method is `spawn_session()` (manager.py), not `create_session()`.

---

### 🟢 Suggestions

#### REV-SUGG-1: Message without ID guard is overly defensive
**Area:** Phase 2  
**Evidence:** phase2-plan.md:311

LangGraph's `add_messages` auto-assigns UUIDs to all messages. The `if msg.id:` check is safe but unnecessary. Consider removing or converting to an assertion for clarity.

---

#### REV-SUGG-2: Config description now correctly says "groups"
**Area:** Phase 1  
**Evidence:** phase1-plan.md:57-59

Good fix — `recent_message_window` description now clarifies it counts boundary groups. The default was also lowered from 20 to 10, which is more appropriate for small-context models.

---

#### REV-SUGG-3: Risks table updated well
**Area:** plan-overview.md  
**Evidence:** plan-overview.md:93-107

New risks added for the original review findings (RemoveMessage, progressive reduction, chunked summarization, dedup, retry). Well organized.

---

## Summary Table

| ID | Area | Severity | Status |
|----|------|----------|--------|
| CRIT-1 | RemoveMessage pattern | ✅ Fixed | Verified experimentally |
| CRIT-2 | Progressive reduction | ⚠️ Partial | REV-CRIT-3: still loops at min_window |
| CRIT-3 | Chunked summarization | ⚠️ Partial | REV-CRIT-2: undefined methods |
| CRIT-4 | Graph continuation test | ✅ Fixed | Phase 4 has proper test plan |
| WARN-1 | SystemMessage summary | ✅ Fixed | Correct and clear |
| WARN-2 | Dedup guard | ❌ Broken | REV-CRIT-1: state schema mismatch |
| WARN-5 | Retry skip | ✅ Fixed | Correct guard placement |
| WARN-6 | Config wiring | ✅ Fixed | Explicit load_config wiring |
| REV-CRIT-1 | Dedup non-functional | 🔴 Critical | Must fix state schema |
| REV-CRIT-2 | Undefined methods | 🔴 Critical | Design gaps |
| REV-CRIT-3 | Min-window loop | 🔴 Critical | Edge case at floor |
| REV-WARN-1 | Hardcoded threshold | 🟡 Warning | Inconsistency |
| REV-WARN-2 | Double aupdate_state | 🟡 Warning | Overhead |
| REV-WARN-3 | Merge overflow | 🟡 Warning | Missing guard |
| REV-WARN-4 | create_session naming | 🟡 Warning | Wrong method name |

## Recommendations (Priority Order)

### Must Fix Before Implementation
1. **REV-CRIT-1**: Fix dedup storage. Either extend `MessagesState` to `CompactionState` with `compacted_at` field, or use LangGraph checkpoint metadata API. Without this, compaction fires on every message.
2. **REV-CRIT-2**: Add design for `_truncate_batch_to_fit()` and `_merge_summaries()` methods.
3. **REV-CRIT-3**: Add escape hatch for when `len(groups) <= min_recent_window` and tokens exceed threshold.

### Should Fix Before Implementation
4. **REV-WARN-1**: Use `config.threshold` instead of hardcoded `0.80` in `select_compactable_groups`.
5. **REV-WARN-2**: Merge two `aupdate_state` calls into one.
6. **REV-WARN-4**: Fix `create_session` → `spawn_session` in test plan.
