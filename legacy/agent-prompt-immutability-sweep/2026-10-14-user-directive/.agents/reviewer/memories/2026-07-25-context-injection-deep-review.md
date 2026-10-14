# 2026-07-25-context-injection-deep-review.md

## Pattern: Deep-Review of System Prompt Pipeline Changes

### Context
Reviewed `feature/context-injection` (commit 231253a9) — adds per-agent `context_injection` flag that auto-injects shared project context into system prompt at spawn/restore via new `append_context_injection` appender.

### Triggers That Fired (correctly)
1. Data Integrity / Security — system prompt injection, leakage risk
2. Cross-Cutting Changes — modifies shared `_apply_post_cache_appends` chain
3. Architecture / Workflow Changes — core prompt pipeline

### What Worked
- Direct code reading of `get_shared_context` return paths revealed dead-code guard (`if not context`) that council/opencode might have missed
- Comparing new appender against sibling `append_shared_context_metadata` exposed missing prompt-injection defenses (XML fence + "read-only data" notice)
- Spawning TWO sessions (council + standard) as redundancy paid off — council session stalled/degraded (stuck at round 28, returning truncated message), standard session delivered full verification

### Session Failure Pattern
- Council session `review-deep` stalled: kept returning same truncated "18 tests passed... 6 tests not 18" message, would not process follow-up prompts even after resume. This is a recurring opencode council degradation. Workaround: fall back to standard session with equally-detailed prompt.

### Key Findings (4 findings: 1 🔴, 2 🟡, 1 🟢 + 1 🟡 dead code)
1. 🔴 Critical: Missing prompt-injection defense — sibling uses XML fence + "read-only data" notice; new appender injects agent-writable context files RAW into system prompt
2. 🟡 Warning: Dead-code guard `if not context` (line 764) — get_shared_context NEVER returns falsy
3. 🟡 Warning: Static query string defeats auto-matching + wasted critical_notes fetch for internal audience
4. 🟢 Suggestion: Cosmetic indentation regression (21-space vs 20-space) at 3 sites

### Lesson for Future Prompt-Pipeline Reviews
ALWAYS compare new appenders against the gold-standard sibling. The defense pattern (XML fence + read-only notice + size cap) is the established contract — any new appender injecting untrusted content MUST follow it. Check `get_shared_context`-style functions for actual return contracts, not docstring claims.
