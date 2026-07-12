# Plan Tracking: User Language Preference

## Iteration 001 — 2026-07-12 02:10

**Status**: REJECTED

### Blocking Issues Found

#### Issue 1: C1/C2 Streaming Contradiction (BLOCKING)
- C1 deferred dispatch only buffered when `user_language != "English"`, but C2 made detection active for English too → English users get double-dispatch
- **Expected**: Buffering tied to whether language_check is active, not language value
- **Found**: `user_language != "English"` guard excludes majority user segment

#### Issue 2: `should_continue()` + Config Flag — Missing Mechanism (BLOCKING)
- `should_continue()` is module-level with no config access; plan required conditional returns but specified no mechanism
- Conditional edges mapping at compile time can't reference non-existent node
- **Expected**: Plan specifies HOW function accesses flag
- **Found**: Plan silent on mechanism

### Non-Blocking Notes
- Phase 1 (Backend API) and Phase 3 (Frontend) well-structured
- W1, W4, S5/W-C, W3 fixes all sound
- W2 test impact analysis accurate

---

## Iteration 002 — 2026-07-12 02:20

**Status**: APPROVED

### Verification Method
- Read all updated plan files (phase2-plan.md, plan-overview.md, decisions.md, notes.md)
- Council session (approve-check-2) verified both fixes against actual source code

### Issue 1 Resolution — VERIFIED
- Predicate changed from `user_language != "English"` to `language_check_active` (config flag `language.check_enabled`)
- Council verified all 5 return paths of `should_continue()` against the predicate:
  - "tools" (has tool_calls) → not buffered ✓
  - "agent" (thinking-only, empty content) → not buffered ✓
  - "agent" (ghost promise, content ends with `:`) → buffered, overwritten by next AIMessage ✓
  - "nudge" (empty content) → not buffered ✓
  - END (normal final response) → buffered, dispatched at END ✓
- All users with language check enabled get deferred dispatch — including English users
- C1/C2 contradiction fully resolved

### Issue 2 Resolution — VERIFIED
- `should_continue()` NOT modified — closure factory `create_should_continue(language_check_enabled)` inside `build_instance_graph()`
- When enabled: wraps `should_continue()`, replaces `END → "end_candidate"`, adds `language_check` node, includes `"end_candidate": "language_check"` in mapping
- When disabled: passes original `should_continue` directly, no node, `END: END` in mapping — identical to pre-feature behavior
- Council confirmed: `should_continue()` returns exactly 4 values ("tools", "agent", "nudge", END) — wrapper only intercepts END
- All 4 test assertions in `test_nudge_behavior.py` call `should_continue()` directly → pass unchanged
- `build_instance_graph()` signature backward-compatible (new kwargs have defaults)
- Conditional edges mapping built per-branch, both self-consistent

### Non-Blocking Notes
- Council raised minor implementation detail: CancelledError in streaming loop should drop buffer (not dispatch). This is an implementation concern, not a plan-level gap.
- LanguageConfig class to be created in Task 15 — already specified.
- Overall plan is internally consistent, technically feasible, and addresses all requirements.
