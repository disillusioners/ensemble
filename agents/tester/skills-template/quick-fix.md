---
version: 1.0.0
category: maintenance
auto_load: false
---

# Quick Fix Workflow

A quick fix is a small, low-risk repair applied immediately by the same session that found the issue. It avoids the round-trip of reporting back, getting approval, and spawning a new session. Quick fixes keep the test cycle fast — but only when applied with discipline.

## When to Apply

A quick fix is appropriate when **all** of the following are true:

- ✅ **Size**: < 20 lines of change
- ✅ **Scope**: single file/module
- ✅ **Complexity**: no PRODUCTION architecture change (test-code architecture fixes are allowed; use Test Architecture Fix for ≥ 20 lines)
- ✅ **Clarity**: obvious root cause with a clear fix
- ✅ **Risk**: low — won't break other functionality
- ✅ **Context**: the session has the relevant context already

If any criterion fails, escalate to the **full fix workflow** (spawn a new session).

## Quick Fix Process

When a session finds an issue during testing:

1. **Session finds the issue** — During test execution, the session identifies a failure
2. **Session assesses fixability** — Apply the criteria above. Ask: is this < 20 lines, single file, obvious fix?
3. **If quick-fixable** — Session applies the fix immediately. No need to wait for tester approval.
4. **Session verifies the fix** — Re-run the affected test/pack to confirm it now passes
5. **Session commits the change** — Mandatory: commit before reporting. Use descriptive message.
6. **Session reports back** — Return results including: what was fixed, commit hash, new test result
7. **Tester documents** — Update `.agents/tester/LESSONS/` with the quick fix details and commit reference (e.g., `quick-fix-[file]-[date].md`)

### Commit Discipline

Every quick fix MUST be committed before the session reports:

- Use a descriptive message: `test: fix [description of what was fixed]` (or `fix:` for production code, but quick fixes to production code are out of scope)
- Include the commit hash in the session's report
- A quick fix without a commit hash is incomplete — tester should not accept it

## Authorizing Quick Fixes

Quick fixes are not automatic — the session must have explicit permission. The tester grants permission upfront when spawning the session, via the task definition.

### Authorization in Task Template

When spawning a session for any testing task, include the quick fix block:

```
Quick Fix Authorization:
- You may apply quick fixes for issues you discover
- Quick fix criteria: < 20 lines, no architecture change, obvious fix
- After fixing, re-run tests to verify
- **COMMIT REQUIRED**: If you modify any files, you MUST commit before reporting
  - Use descriptive commit message: "test: fix [description of what was fixed]"
  - Include commit hash in your report
- Report what you fixed and the commit hash in your results
```

Without this authorization, sessions should report issues rather than fix them.

## Eligibility Examples

### ✅ Eligible Quick Fixes

- Fix a typo in a variable name
- Correct a conditional logic error (wrong operator, swapped branches)
- Add a missing null/nil check
- Update an error message string
- Fix a test assertion value to match actual behavior (when behavior is correct)
- Add a missing import
- Fix a port number in a test config
- Add a missing env var declaration to ensure.md documentation (separate from code)
- Fix a typo in a docstring or comment that affects test readability
- Adjust a sleep/retry parameter to a more deterministic value (and document why)

### ❌ Not Eligible (Use Full Workflow)

- Refactor error handling across multiple functions
- Change a data structure (e.g., list to map)
- Add a new interface or abstraction
- Modify an API contract
- A fix that affects multiple modules
- A fix that requires design discussion
- Any change that risks breaking other functionality
- Production code architecture changes (tester does NOT touch production architecture; escalate)

## Reusing the Session

Quick fixes are the #1 priority for session reuse (see session-management rules in rule.md):

- The session that found the issue has the most context
- Reusing avoids re-loading the codebase
- Saves time and tokens vs spawning a fresh session

### When to Reuse vs Spawn New

| Scenario | Action |
|----------|--------|
| Session found issue, quick fix possible | Reuse (highest priority) |
| First quick fix didn't fully resolve, need another small fix | Reuse |
| Related task in same testing area | Reuse |
| Task scope expanded significantly | Spawn new |
| Different testing area / different module | Spawn new |
| Session completed and closed | Spawn new |

## Escalation: When Quick Fix Is Not Enough

When an issue fails the quick-fix criteria:

1. **Session reports the issue** — full evidence: file, line, error, suspected root cause
2. **Tester decides next step** — typically spawn a new session for full investigation
3. **New session investigates** — broader context, full code understanding, architectural impact assessment
4. **New session implements the fix** — may take longer; follows standard workflow
5. **Re-test** — verify the fix in the original failing context

Do NOT push a quick-fix-shaped solution into a non-quick-fix problem. The discipline exists to keep quality high.

## Quick Fix Documentation

After the session reports a quick fix, the tester writes a LESSONS entry:

```
.quick-fix-[file]-[date].md

# Quick Fix: [brief description]

## Context
- Pack: [pack name]
- Test: [test name]
- Date: [YYYY-MM-DD]

## Issue
[What was failing; what error was observed]

## Root Cause
[Why it failed; the underlying issue]

## Fix
[What changed; which file:line]

## Verification
- Re-run result: PASS
- Commit: [hash]
- Author: [executor session ID]

## Lessons
[Anything to remember for future similar issues]
```

This creates institutional knowledge — over time, the LESSONS directory becomes a pattern catalog for recurring quick fixes.

## Anti-Patterns

Avoid these mistakes:

- **Applying quick fixes without authorization** — sessions should ask if not pre-authorized
- **Quick-fixing production code** — out of scope; escalate to full workflow
- **Skipping the commit** — every change needs a commit; uncommitted fixes are unverified
- **Bypassing the re-test** — never report a fix as good without re-running the affected test
- **Quick-fixing when the root cause is unclear** — if you can't explain the failure in one sentence, escalate
- **Quick-fixing across modules** — single file/module is the rule; cross-module = full workflow
- **Letting quick fixes grow** — a "small" 25-line fix is not a quick fix; recognize the boundary
- **Reporting without commit hash** — incomplete report; tester cannot verify

## Quick Fix Decision Flow

```
Issue found during testing
├─ Is there pre-authorized quick fix permission?
│  ├─ No → report issue; tester decides
│  └─ Yes → apply criteria:
│           ├─ < 20 lines?
│           ├─ Single file/module?
│           ├─ No production architecture change?
│           ├─ Obvious root cause?
│           ├─ Low risk?
│           └─ Session has context?
│                ├─ All yes → QUICK FIX
│                │           ├─ Apply fix
│                │           ├─ Re-run test
│                │           ├─ Commit (descriptive message)
│                │           └─ Report (with commit hash)
│                └─ Any no → ESCALATE
│                            └─ Report issue; tester spawns full fix
```