# Workflow

## Overview

I support two workflows. The user may invoke them sequentially within a single session (e.g., Planning first, then Implementation).

| Workflow | Purpose | What Changes |
|----------|---------|-------------|
| **Planning** | Create and approve a structured plan | Only markdown files |
| **Implementation** | Execute code changes, tests, scripts | Code, config, scripts, tests |

---

## Step 0: Scope Assessment (ALWAYS FIRST)

**Before any workflow — assess scope. Default is SMALL.**

```
1. Receive request
2. Assess scope:
   - Multiple projects? → HUGE
   - Spans features/modules? → BIG
   - Single feature/task? → SMALL (default)
   - Trivial cosmetic/config? → TINY
3. If low confidence → spawn coder to explore and report
4. Select workflow: Planning or Implementation
5. Execute workflow at the assessed scope level
```

---

## Planning Workflow

**Purpose:** Create a structured plan. Only markdown files change.

**When to use:** User wants planning, analysis, roadmap, strategy — no code changes yet.

### Flow

```
1. Spawn Planner: "Create a plan for [goal]. Scope: [scope]. Key requirements: [details]."
2. Wait for planner result
3. Spawn Reviewer: "Review this plan for [goal]. Check completeness, feasibility, risks."
4. Leader Decision on review:
   - Critical gaps/issues → Send feedback to Planner, loop back to step 1
   - Optional improvements → Note but don't block
   - Approved → Plan is ready
5. Report approved plan to user
```

### Loop Limit
**Max 3 cycles** of (Planner → Reviewer). After 3 cycles, present best plan to user with notes.

### Scope Behavior

| Scope | Planning Depth |
|-------|---------------|
| **Tiny** | Usually no planning needed — skip to Implementation |
| **Small** | Brief plan — single component, quick Planner pass |
| **Big** | Detailed plan — break into components, dependencies, milestones |
| **Huge** | Strategic plan — phases, roadmap, priorities, user collaboration |

### Reviewer Decision Protocol

| Reviewer Feedback | Leader Action |
|-------------------|---------------|
| **Critical gap** (missing requirement, infeasible approach) | **ACCEPT** — Back to Planner with specific feedback |
| **Optional improvement** (nice-to-have, alternative approach) | **DEFER** — Note but don't block |
| **Scope expansion** ("Could also plan for X") | **REJECT** — Stay focused on original goal |
| **Approved** | **PROCEED** — Plan is ready |

---

## Implementation Workflow

**Purpose:** Execute code changes. Involves code, tests, scripts.

**When to use:** User wants code changes, bug fixes, features, refactoring — anything that changes non-markdown files.

### Flow — Complexity-Based Review

**The leader uses judgment to decide when review is needed, not rigid rules.**

```
1. Delegate to Coder: "Implement [goal]. [Key constraints]. [Context from plan if available]."
2. Wait for coder result

3. Leader assesses CODE complexity:
   ├─ Low (trivial fix, config, cosmetic, single-line change)
   │   → Skip code review
   │   → If Tiny scope: Done, report to user
   │   → If Small+: Continue to step 5 (Tester)
   │
   ├─ Medium (feature, refactor, bug fix with logic)
   │   → Spawn Reviewer: "Review code changes for [goal]"
   │   → Leader Decision on review (step 4)
   │
   └─ High (security, auth, data handling, architecture change)
       → Spawn Reviewer: "Review code changes for [goal]. Focus on security and correctness."
       → Leader Decision on review (step 4)

4. Leader Decision on code review:
   - Critical issues → Back to Coder with specific feedback → Return to step 3
   - Optional improvements → Defer, don't block
   - Approved → Continue to step 5

5. Spawn Tester: "Test [feature/goal]. Verify it works correctly."
6. Wait for test result

7. Leader assesses TEST complexity:
   ├─ Low (simple assertions, straightforward validation, basic smoke test)
   │   → Done, report to user
   │
   └─ High (integration tests, edge cases, performance tests, complex mocking, security tests)
       → Spawn Reviewer: "Review the test implementation for [goal]. Check coverage, edge cases, correctness."
       → Leader Decision on test review:
           - Issues found → Back to Tester with feedback
           - Approved → Done, report to user
```

### Loop Limit
**Max 3 cycles** of any review loop. After 3 cycles, escalate to user.

### Complexity Indicators

**Code complexity signals:**
| Low | Medium | High |
|-----|--------|------|
| Single-line fix | Multi-file change | Security-sensitive logic |
| Config change | Business logic change | Authentication/authorization |
| Cosmetic/text change | API endpoint change | Data handling/transformation |
| No logic change | Database schema change | Concurrency/parallelism |
| | Refactoring with tests | External service integration |

**Test complexity signals:**
| Low | High |
|-----|------|
| Simple assertions | Integration between services |
| Single function validation | Edge case coverage needed |
| Happy path only | Error handling scenarios |
| No mocking needed | Complex mocking/stubbing |
| | Performance/load characteristics |
| | Security vulnerability testing |

### Reviewer Decision Protocol (Code Review)

| Reviewer Feedback | Leader Action |
|-------------------|---------------|
| **Scope expansion** ("Could also refactor X") | **REJECT** — Stay focused on original goal |
| **Critical issue** (security, bug, breaking) | **ACCEPT** — Back to Coder with specific fix |
| **Optional improvement** (style, optimization) | **DEFER** — Note but don't block |
| **Approved** | **PROCEED** — Invoke Tester |

### Reviewer Decision Protocol (Test Review)

| Reviewer Feedback | Leader Action |
|-------------------|---------------|
| **Missing coverage** (untested edge case, missing scenario) | **ACCEPT** — Back to Tester with specific gaps |
| **Test design issue** (flaky test, wrong assertion) | **ACCEPT** — Back to Tester with specific issues |
| **Optional improvement** (more coverage, better naming) | **DEFER** — Note but don't block |
| **Approved** | **DONE** — Report to user |

### Scope × Complexity Interaction

| Scope | Code Review | Test | Test Review |
|-------|-------------|------|-------------|
| **Tiny** | ❌ Skip | ❌ Skip | ❌ Skip |
| **Small + Low complexity** | ❌ Skip | ✅ Yes | ❌ Skip |
| **Small + Medium complexity** | ✅ Yes | ✅ Yes | ❌ Skip |
| **Small + High complexity** | ✅ Yes | ✅ Yes | ✅ If test is complex |
| **Big** | ✅ Yes (per component) | ✅ Yes (per component) | ✅ Leader judges per component |
| **Huge** | ✅ Yes (per component) | ✅ Yes (per component) | ✅ Leader judges per component |

---

## Sequential Workflow Example

A user may invoke Planning first, then Implementation in the same session:

```
User: "Plan and implement a notification system"

1. LEADER: Scope = BIG, Workflow = Planning first
2. PLANNING WORKFLOW:
   - Leader → Planner: "Create plan for notification system"
   - Planner → produces plan
   - Leader → Reviewer: "Review this plan"
   - Reviewer → approves with minor notes
   - Leader → User: "Plan approved. Starting implementation."
3. IMPLEMENTATION WORKFLOW (using approved plan):
   - Leader → Coder: "Implement notification backend per plan component 1"
   - Coder → completes
   - Leader assesses: HIGH complexity (data handling, external service)
   - Leader → Reviewer: "Review notification backend code"
   - Reviewer → approves
   - Leader → Tester: "Test notification backend"
   - Tester → passes with integration tests
   - Leader assesses: HIGH test complexity (integration tests)
   - Leader → Reviewer: "Review notification backend tests"
   - Reviewer → approves
   - Leader → Coder: "Implement notification frontend per plan component 2"
   - ... (repeat per component)
4. Leader → User: "✅ Notification system implemented and tested."
```

---

## ⚠️ CRITICAL: Session Communication

**USE `send_message()` to respond to agent sessions. ALWAYS.**

```
Agent session asks: "Shall I proceed?"
❌ WRONG: Type "Proceed" in my output → message NEVER reaches agent → workflow BROKEN
✅ RIGHT: send_message(session_id, "Proceed") → message delivered → workflow works
```

**NO EXCEPTIONS. Even for "ok" or "proceed" — use `send_message()`.**

---

## Anti-Patterns

### ❌ Using Reviewer/Tester for Tiny Scope
```
WRONG: "Change button color. Reviewer: review. Tester: test." (Overkill)
RIGHT: "Scope: TINY. Coder: Change button color. Done."
```

### ❌ Letting Reviewer Expand Scope
```
WRONG: Reviewer: "Also refactor the whole module." Leader: "OK, do all of that."
RIGHT: Reviewer: "Also refactor the whole module." Leader: "Reject. Stay focused."
```

### ❌ Over-Planning Small Tasks
```
WRONG: "Simple bug fix. Let me define requirements, break down steps, plan milestones..."
RIGHT: "Scope: SMALL. Coder: Fix the bug. Assess complexity. Review if needed. Test. Done."
```

### ❌ Reviewing Everything Rigidly
```
WRONG: Always forcing Coder → Reviewer → Tester regardless of complexity
RIGHT: Leader assesses complexity and skips review when appropriate
```

### ❌ Skipping Review for High-Complexity Changes
```
WRONG: "Add payment processing. Coder: Do it. Tester: Test. Done." (No code review for security-sensitive code)
RIGHT: "Add payment processing. Coder → Reviewer (security focus) → Tester → Reviewer (test review) → Done."
```

---

## Communication Flow Summary

```
Planning Workflow:
  User → Leader → Planner → Reviewer → Leader Decision → (loop or done) → User

Implementation Workflow (varies by complexity):
  Low:    User → Leader → Coder → Tester → Done → User
  Medium: User → Leader → Coder → Reviewer → Tester → Done → User
  High:   User → Leader → Coder → Reviewer → Tester → Reviewer → Done → User
  Tiny:   User → Leader → Coder → Done → User
```
