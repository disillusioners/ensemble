# Plan Tracking: User Message Injection on Running Instance

## Iteration 001 — 2026-07-12

**Verdict: REJECTED**

### Blocking Issues

1. **C3 False Precedent — `language_check_reminder` pattern does NOT exist in compaction.py**
   - Expected: Plan claims "In compaction.py, add a check for additional_kwargs.get("injected_message") to skip injected messages from summarization. Follow the exact same pattern as language_check_reminder."
   - Found: `language_check_reminder` appears ONLY in graph.py (lines 463, 493, 522) — in the language_check_node for counter reset logic. It does NOT appear anywhere in compaction.py. The compaction system uses `identify_boundary_groups()` which groups messages by type (tool sequences, singles) — it has NO concept of flag-based preservation. The C3 proactive compaction fix requires a NEW pattern (modifying `identify_boundary_groups()` or `select_compactable_groups()` to treat flagged messages as non-compactable), not copying an existing one.
   - Impact: Implementer will be misled into thinking a simple flag-check suffices. The actual change is more invasive — it touches the grouping logic, not just a filter.

2. **Reactive compaction re-trigger risk not fully addressed**
   - Expected: Clear handling of what happens when ContextLengthExceededError triggers AFTER the injected message is appended to full_messages but BEFORE the return value persists it to checkpoint.
   - Found: Plan Task 20 says "re-append the injected_msg to compact_messages before re-invoke" but doesn't address: (a) whether the compact_messages built from checkpoint state already includes or excludes the injected_msg, (b) what the return value should be if the re-invoke ALSO triggers ContextLengthExceededError (nested compaction), (c) whether the injected_msg should be in the compacted state going forward or only for this invocation.

### Notes (Non-blocking)

- Two call sites for `build_instance_graph()` exist (instance_lifecycle.py:889 and :1897). Plan Task 16 says "Find where build_instance_graph() is called" but doesn't explicitly enumerate both. Minor gap — could lead to one being missed.
- Thread safety claim is reasonable (CPython dict atomicity + main event loop access) but the plan says "Verify no cross-thread access from WorkerPool" without confirming. The LLM invoke runs via `run_in_executor` (thread pool) but injection check/clear happens before that in async context.
- instance_id IS available in agent_node via `config.get('configurable', {}).get('thread_id')` — plan Task 15 says "verify" but it's confirmed available.
- stream_message() already accepts `event_type` parameter — no change needed to LiveEventHub. Plan correctly identifies this.
