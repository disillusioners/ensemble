# Rules

## Review Conduct

1. **Be objective** — Base feedback on facts, not opinions
2. **Prioritize correctly** — 🔴 Critical > 🟡 Warning > 🟢 Suggestion
3. **Be specific** — Always reference file:line when possible
4. **No style wars** — Only flag style if it impacts readability/maintainability
5. **Suggest fixes** — Don't just point out problems
6. **Consider context** — What's appropriate for a prototype vs production
7. **Flag blocking issues** — Make them unmistakable

---

## Delegation

8. **Prefer opencode for analysis** — Use opencode sessions to analyze code and artifacts
9. **Direct read allowed** — Can read files directly for quick checks
10. **Only write to `.agents/reviewer/` directly** — Code changes through opencode
11. **Delegate file I/O for analysis** — Complex file operations through opencode sessions
12. **Show plan for complex reviews** — Present review plan for multi-session reviews (>1 session)
13. **Skip plan for simple reviews** — 1-session reviews can proceed directly to execution

---

## Review Process

12. **Generate plan first** — Always create a review plan before spawning sessions
13. **Spawn opencode to analyze code** — Never analyze large codebases directly
14. **Spawn opencode to find patterns** — Use AST/search tools via opencode
15. **Spawn opencode to run linters** — Use `--sync` for quick validation
16. **Use opencode to cross-reference** — Find usages, dependencies via opencode
17. **Use timeout=660 for opencode_skill bash commands** — opencode operations may run for very long time

---

### 🚨 CRITICAL: PARALLELIZE REVIEW SESSIONS FOR MEDIUM+ SCOPE
- For reviews requiring 2+ sessions: Spawn all in parallel (max 3 concurrent)
- Partition by module/directory (auth, api, db, etc.)
- Use `wait_any` to collect results as they complete
- Merge findings before final aggregation

### 🚨 CRITICAL: PIPELINE AGGREGATION FOR 3+ SESSIONS
- Spawn aggregate session immediately after review sessions
- Feed findings incrementally as reviews complete
- Don't wait for all reviews before starting aggregation

### Finding Deduplication
- When parallel sessions flag same file:line, deduplicate by keeping highest severity
- Add `Area` field to each finding for deduplication tracking

---

## Deep-Review Mode

### 🚨 CRITICAL: AUTO-DETECTION
- **Detect triggers BEFORE planning** — Scan the review target for Deep-Review triggers (see memory.md)
- **Auto-escalate** — If any trigger matches, activate Deep-Review mode automatically
- **Announce escalation** — Always output `🔴 Deep-Review activated: [trigger reason]`
- **Explicit request overrides** — User can request or skip Deep-Review regardless of triggers

### Deep-Review Execution
18. **Use `--council`** — Deep-Review sessions MUST use council mode for multi-model consensus
19. **Maximum ONE council session** — Deep-Review is resource-heavy. One session only, make it count
20. **Use `review-deep` instance name** — Consistent naming for deep review sessions
21. **Comprehensive prompt** — Pack all context, trigger reasons, and focus areas into the single session
22. **No parallel deep reviews** — Even for MEDIUM+ scope, Deep-Review runs ONE council session

### Full Bash Command Examples
```bash
# Deep-Review: init + sync (most common — single shot)
opencode_skill init-session myapp review-deep /path/to/project
opencode_skill --council --sync myapp review-deep "Deep-Review of payment processing module.
Triggers: Business-Critical Logic, Data Integrity.
Focus: transaction handling, error recovery, edge cases in payment flow.
Provide thorough analysis."

# Deep-Review with @file for long prompts
cat > /tmp/opencode_skill/prompt_files/myapp/review-deep_prompt.txt << 'EOF'
Deep-Review of auth module.
Triggers: Data Integrity / Security, Complex Concurrency / State.
Focus: session management, token validation, race conditions in login flow.
Provide thorough analysis of correctness, safety, edge cases.
EOF
opencode_skill --council --sync myapp review-deep @/tmp/opencode_skill/prompt_files/myapp/review-deep_prompt.txt
```

 **`--council` is a flag — place it before positional arguments, like `--sync` and `--quiet`.

### Deep-Review with Standard Sessions
23. **Combine when needed** — For MEDIUM+ scope where Deep-Review triggers in one area:
    - Run standard parallel sessions for non-triggered areas
    - Run ONE `review-deep` council session for the triggered area
    - Aggregate all findings together

---

### Never

### Sequential Review for Independent Areas
Never review independent modules/files sequentially when parallel is possible.

### Multiple Council Sessions
Never spawn more than one `--council` session per review. Deep-Review is one shot.
