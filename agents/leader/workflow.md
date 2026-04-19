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

```raw
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

## Git Flow

**The leader manages git via a dedicated giter instance. This instance is reused ONLY for git operations throughout the entire task lifecycle.**

### Base Branch: `latest`

- **`latest` is the integration branch** — all features merge here when complete
- If `latest` branch does not exist, create it first (from `main` or current HEAD)
- Feature branches are always created from `latest`

### Flow

```raw
1. BEFORE any workflow:
   - Spawn giter instance (dedicated, reused for all git operations)
   - giter: "Ensure 'latest' branch exists (create from main if needed). Create feature branch '[branch-name]' from latest. If branch exists, switch to it."
   - ⛔ WAIT for git branch creation to COMPLETE before proceeding

2. DURING workflows:
   - Other agents (coder, reviewer, tester) work normally
   - They may commit as needed (their own logic, not leader's concern)

3. AFTER everything completed:
   - giter: "Check git status. Commit any uncommitted changes with message '[type]: [summary]'."
   - Wait for result
   - giter: "Merge feature branch into latest. Push latest and feature branch to remote."
   - Wait for result
   - Terminate giter instance
```

### ⚠️ CRITICAL: Git Setup is NOT Parallelizable

**Git branch creation MUST complete before any coding begins.** This is a hard dependency because:

1. **Branch must exist first** — Coding happens ON the feature branch. If the branch doesn't exist yet, code changes go to wrong branch.
2. **Atomic git operations** — Git commands modify shared repository state and cannot run concurrently.
3. **Wrong branch = lost work** — If coders start before branch exists, their commits go to `latest` or `main`, not the feature branch.

**❌ WRONG sequence (broken):**
```raw
1. Spawn giter → send_message(create branch)
2. Spawn coder → send_message(start coding)
3. (both running in parallel) ❌ BROKEN
```

**✅ CORRECT sequence:**
```raw
1. Spawn giter → send_message(create branch)
2. Wait for giter completion report
3. ✅ Branch confirmed created
4. Spawn coder → send_message(start coding)
```

### Key Rules
- **ALWAYS merge to latest after feature is done** — this keeps latest as the current state
- Push both `latest` and the feature branch to keep them in sync
- `latest` should always contain the latest completed features

### When Git Flow Applies
| Scope | Git Flow |
|-------|----------|
| **Tiny** | ❌ Skip — too small for branching |
| **Small** | ✅ Branch before, push after |
| **Big** | ✅ Branch before, push after all phases |
| **Huge** | ✅ Branch before, push after all phases |

---

## Planning Workflow

**Purpose:** Create a structured plan. Only markdown files change.

**When to use:** User wants planning, analysis, roadmap, strategy — no code changes yet.

### Flow

```raw
1. Spawn Planner: "Create a plan for [goal]. Scope: [scope]. Key requirements: [details]."
2. Wait for planner result
3. Spawn Reviewer: "Review this plan for [goal]. Check completeness, feasibility, risks."
4. Leader Decision on review:
   - Critical gaps/issues → send_message to same Planner with feedback → loop back to step 2
   - Optional improvements → Note but don't block
   - Approved → Continue to step 5 (Approver check)
5. Leader assesses plan complexity for Approver:
   ├─ SMALL scope → Skip Approver (plan is simple, Reviewer sufficient)
   └─ BIG+ scope OR complex plan → Spawn Approver:
      - Provide ONLY the plan file/summary — no planning history, no Reviewer's notes
      - Include plan name for tracking: "Plan: [plan-name] | File: [path/to/plan.md]"
      - Message example: "Evaluate this plan. Plan: My Feature Plan | File: .agents/shared/planning/my-feature/plan.md. Approve or reject."
      - ⚠️ DO NOT guide the Approver — let it evaluate independently
      - Approver Decision:
         - REJECTED → Review rejection reasons → back to Planner with specific feedback → loop back to step 2
         - APPROVED → Plan is ready
6. Terminate Planner, Reviewer, and Approver instances
7. Report approved plan to user
```

**Instance reuse:** The same Planner and Reviewer instances are reused across loop iterations. This preserves context — the Planner remembers what it planned before, and the Reviewer knows what issues it flagged.

**Approver:** ALWAYS spawn a fresh approver instance. Never reuse.

### Loop Limit
**Max 3 cycles** of (Planner → Reviewer). After 3 cycles, present best plan to user with notes.

### Phase Design Principle

**When planning, group components into phases by shared context.** Each phase should contain related work that shares architectural decisions, codebase area, and conventions. This maximizes the benefit of instance reuse — agents accumulate relevant context within a phase.

```raw
✅ GOOD phase: "Backend API for notifications" (all components share API patterns, data models)
❌ BAD phase:  "Fix login bug + add docs + refactor styles" (unrelated work, no shared context)
```

### Scope Behavior

| Scope | Planning Depth |
|-------|---------------|
| **Tiny** | Usually no planning needed — skip to Implementation |
| **Small** | Brief plan — single component, quick Planner pass |
| **Big** | Detailed plan — break into phases with shared context, components per phase, dependencies |
| **Huge** | Strategic plan — phases with shared context, roadmap, priorities, user collaboration |

### Reviewer Decision Protocol

| Reviewer Feedback | Leader Action |
|-------------------|---------------|
| **Critical gap** (missing requirement, infeasible approach) | **ACCEPT** — Back to Planner with specific feedback |
| **Optional improvement** (nice-to-have, alternative approach) | **DEFER** — Note but don't block |
| **Scope expansion** ("Could also plan for X") | **REJECT** — Stay focused on original goal |
| **Approved** | **PROCEED** — If BIG+ scope → spawn Approver for double-check. Else → Plan is ready |

### Approver Decision Protocol

| Approver Verdict | Leader Action |
|------------------|---------------|
| **REJECTED** (blocking issues) | **ACCEPT** — Back to Planner with Approver's specific rejection reasons |
| **APPROVED** | **PROCEED** — Plan is ready |

**Note:** When Approver rejects, rejection reasons are tracked in `.agents/approver/{plan-slug}-tracking.md`. Include the tracking file path when sending feedback to the Planner so it can reference previous issues.

### Approver Call Limit
**Max 3 calls** to Approver per plan. After 3 rejections:
- **TrueAuto mode:** Proceed with implementation — planning cannot foresee everything, and fixing issues during implementation is the best solution
- **Normal mode:** Present best plan to user with notes and let user decide

---

## Implementation Workflow

**Purpose:** Execute code changes. Involves code, tests, scripts.

**When to use:** User wants code changes, bug fixes, features, refactoring — anything that changes non-markdown files.

### Flow — Complexity-Based Review

**The leader uses judgment to decide when review is needed, not rigid rules.**

**⚠️ TrueAuto Override:** In TrueAuto mode, always use Reviewer and Tester for any scope except Tiny. Skip complexity assessment — always run full review cycle.

```raw
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
   - Approved → Continue to Tidy check (step 4b)

4b. Tidy quality check (Medium+ complexity only):
   - Skip Tidy for Low complexity (already skipped review) or small fixes
   - Spawn Tidy: "Review code quality for [goal]. Task plan: [plan]. Changed files: [list]."
   - Tidy Decision:
     - High issues → Back to Coder with specific fixes → Return to step 3 (Reviewer)
     - Medium issues → Defer unless clearly impacting maintainability
     - Approved → Continue to step 5
   - If Coder modified logic (not just formatting), invoke Reviewer on changed sections for regression check
   - **Combined loop limit with Reviewer: max 3 total cycles across both phases**

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

### Tester Escalation: `TESTER_CANT_OPTIMIZE_TEST_PACK`

**When Tester reports this code:**

1. **In TrueAuto mode:**
   - Craft a quick plan to fix the test time issue
   - Re-delegate to Tester with the optimization plan
   - If Tester still fails after optimization:
     - Report to user with details
     - Stop workflow

2. **Not in TrueAuto mode:**
   - Report to user immediately
   - Stop workflow
```

### Loop Limit
**Max 3 total cycles** across all review phases (Reviewer + Tidy combined). After 3 cycles, escalate to user with remaining issues listed.

Rationale: 6 total iterations (3×2 phases) is excessive — signals task is poorly specified or too large.

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

| Scope | Code Review | Tidy | Test | Test Review |
|-------|-------------|------|------|-------------|
| **Tiny** | ❌ Skip | ❌ Skip | ❌ Skip | ❌ Skip |
| **Small + Low complexity** | ❌ Skip | ❌ Skip | ✅ Yes | ❌ Skip |
| **Small + Medium complexity** | ✅ Yes | ✅ Yes | ✅ Yes | ❌ Skip |
| **Small + High complexity** | ✅ Yes | ✅ Yes | ✅ Yes | ✅ If test is complex |
| **Big** | ✅ Yes (per component) | ✅ Yes (per component) | ✅ Yes (per component) | ✅ Leader judges per component |
| **Huge** | ✅ Yes (per component) | ✅ Yes (per component) | ✅ Yes (per component) | ✅ Leader judges per component |

### Instance Lifecycle — Reuse by Phase

**Instances are reused within a phase and refreshed across phases.**

```raw
PHASE 1:
  Spawn: coder-1, reviewer-1, tester-1
  Component A: coder-1 → reviewer-1 → tester-1
  Component B: coder-1 → reviewer-1 → tester-1  (same instances, shared context)
  Component C: coder-1 → reviewer-1 → tester-1  (same instances, shared context)
  Phase 1 complete → Terminate all instances

PHASE 2:
  Spawn: coder-2, reviewer-2, tester-2  (fresh instances, new context)
  Component D: coder-2 → reviewer-2 → tester-2
  ...
  Phase 2 complete → Terminate all instances
```

**Why reuse within phase:** Components in the same phase share architectural decisions, codebase state, and conventions. Reusing instances preserves this accumulated context.

**Why fresh across phases:** New phases may involve different context, different architectural decisions, or different areas of the codebase.

**For SMALL scope (single phase, single component):** Spawn instances as needed, terminate when done.

---

## Phase Scheduling — Parallelism & Pipelining

**The leader MUST assess dependencies between phases and schedule them intelligently. Never default to fully sequential if parallelism is possible.**

### Prerequisite: Git Setup Must Complete First

**Before ANY phase scheduling, git setup must complete:**
1. Spawn giter instance
2. Create feature branch from `latest`
3. ✅ WAIT for branch creation to complete
4. ONLY THEN schedule phases

The giter instance is excluded from the phase scheduling table — it runs before and after phases, not during them.

### Hard Constraint: Max Concurrent Instances = 3

The system allows at most 3 agent instances running simultaneously. The leader must schedule within this budget at all times.

### Step 1: Build Dependency Graph

After planning, assess each phase pair:

| Relationship | Signal | Schedule |
|-------------|--------|----------|
| **Independent** | Different files, different modules, no shared APIs | ✅ **Parallel** — run coders simultaneously |
| **Loosely coupled** | Phase N+1 uses Phase N's planned interfaces, not its implementation | ✅ **Pipeline** — start N+1 coder while N is in review |
| **Tightly coupled** | Phase N+1 builds on Phase N's actual code (same files, same models) | ❌ **Sequential** — wait for Phase N review approval first |

### Step 2: Schedule Within Budget

```raw
Instance budget = 3. Common allocation patterns:

2 independent phases:
  Slot 1: coder-1        Slot 1: reviewer-1    Slot 1: tester-1
  Slot 2: coder-2        Slot 2: reviewer-2    Slot 2: tester-2
  Slot 3: (free)         Slot 3: (free)        Slot 3: (free)

3 independent phases:
  Slot 1: coder-1        Slot 1: reviewer-1    Slot 1: tester-1
  Slot 2: coder-2   →    Slot 2: reviewer-2   → Slot 2: tester-2
  Slot 3: coder-3        Slot 3: reviewer-3    Slot 3: tester-3
                         (stagger: start reviews as coders finish)

Pipeline (coupled phases):
  Slot 1: coder-1        Slot 1: reviewer-1    Slot 1: tester-1
  Slot 2: (free)         Slot 2: coder-2       Slot 2: reviewer-2
  Slot 3: (free)         Slot 3: (free)        Slot 3: tester-2
```

**Rule: Prioritize coders first.** Run as many coders in parallel as budget and independence allow, then stagger review/test as slots free up.

### Step 3: Handle Risks

**When review finds critical issues on a phase that has downstream dependents already running:**
- Leader assesses blast radius
- **Contained fix** (unrelated to dependent's work) → Let dependent continue, apply fix as follow-up
- **Architectural change** (affects dependent's code) → Abort dependent, apply corrections, restart with updated context
- **When unsure → abort dependent.** Wasted work is cheaper than broken work.

---

## Sequential Workflow Example

A user may invoke Planning first, then Implementation in the same session:

```raw
User: "Plan and implement a notification system"

1. LEADER: Scope = BIG, Workflow = Planning first
2. GIT FLOW — Setup:
   - Spawn giter (dedicated for git operations)
   - giter: "Ensure 'latest' branch exists (create from main if needed). Create branch 'feature/notifications' from latest."
   - Wait for confirmation

3. PLANNING WORKFLOW:
   - Spawn planner-1, reviewer-plan-1
   - Leader → planner-1: "Create plan for notification system"
   - planner-1 → produces plan
   - Leader → reviewer-plan-1: "Review this plan"
   - reviewer-plan-1 → approves with minor notes
   - Leader → User: "Plan approved. Starting implementation."
   - Terminate planner-1, reviewer-plan-1

4. IMPLEMENTATION — Phase 1: Backend (using approved plan):
   - Spawn coder-1, reviewer-1, tester-1
   - Leader → coder-1: "Implement notification backend per plan component 1"
   - coder-1 → completes (may commit as part of its workflow)
   - Leader assesses: HIGH complexity
   - Leader → reviewer-1: "Review notification backend code"
   - reviewer-1 → approves
   - Leader → tester-1: "Test notification backend"
   - tester-1 → passes
   - ... (reuse coder-1, reviewer-1, tester-1 for remaining components)
   - Phase 1 complete → Terminate coder-1, reviewer-1, tester-1

5. IMPLEMENTATION — Phase 2: Frontend:
   - Spawn coder-2, reviewer-2, tester-2 (fresh instances)
   - ... (reuse for all frontend components)
   - Phase 2 complete → Terminate coder-2, reviewer-2, tester-2

6. GIT FLOW — Finalize:
   - giter: "Check git status. Commit any uncommitted changes. Merge feature/notifications into latest."
   - Wait for confirmation
   - giter: "Push latest and feature/notifications to remote."
   - Wait for confirmation
   - Terminate giter

7. Leader → User: "✅ Notification system implemented, tested, merged to latest, and pushed."
```

---

## ⚠️ CRITICAL: Spawn Instance is Fire-and-Forget

**Spawning is NOT blocking. The instance does nothing until you message it.**

```raw
1. spawn_instance("coder") → returns instance_id IMMEDIATELY (~1ms)
2. send_message(instance_id, "task...") → fire-and-forget
3. DONE spawning — move on
4. Later: system will deliver completion report as a new message
```

**The "Wait for result" in workflows means:**
- ❌ WRONG: Poll with `get_instance_info()` or `list_instances()`
- ✅ RIGHT: Do other work, wait for completion report message to arrive

**Spawning multiple parallel agents:**
```raw
1. spawn coder-1 → send_message(task A)
2. spawn coder-2 → send_message(task B)  
3. spawn coder-3 → send_message(task C)
4. (all spawned) → wait for completion reports to arrive
```

---

## ⚠️ CRITICAL: Instance Communication

**USE `send_message()` to respond to agent instances. ALWAYS.**

```raw
Agent instance asks: "Shall I proceed?"
❌ WRONG: Type "Proceed" in my output → message NEVER reaches agent → workflow BROKEN
✅ RIGHT: send_message(instance_id, "Proceed") → message delivered → workflow works
```

**NO EXCEPTIONS. Even for "ok" or "proceed" — use `send_message()`.**

---

## Anti-Patterns

### ❌ Using Reviewer/Tester for Tiny Scope
```raw
WRONG: "Change button color. Reviewer: review. Tester: test." (Overkill)
RIGHT: "Scope: TINY. Coder: Change button color. Done."
```

### ❌ Letting Reviewer Expand Scope
```raw
WRONG: Reviewer: "Also refactor the whole module." Leader: "OK, do all of that."
RIGHT: Reviewer: "Also refactor the whole module." Leader: "Reject. Stay focused."
```

### ❌ Over-Planning Small Tasks
```raw
WRONG: "Simple bug fix. Let me define requirements, break down steps, plan milestones..."
RIGHT: "Scope: SMALL. Coder: Fix the bug. Assess complexity. Review if needed. Test. Done."
```

### ❌ Reviewing Everything Rigidly
```raw
WRONG: Always forcing Coder → Reviewer → Tester regardless of complexity
RIGHT: Leader assesses complexity and skips review when appropriate
```

### ❌ Polling for Instance Status
```raw
WRONG: "Spawned coder, let me check status with get_instance_info()..."
WRONG: "Is coder done yet? Let me list_instances()..."
WRONG: "Waiting for coder... checking progress..."

✅ RIGHT: "Spawned coder. Done spawning. Continue to next task or wait for completion report."
```

**The system will deliver completion report. TRUST it. Do NOT check status manually.**

### ❌ Skipping Review for High-Complexity Changes
```raw
WRONG: "Add payment processing. Coder: Do it. Tester: Test. Done." (No code review for security-sensitive code)
RIGHT: "Add payment processing. Coder → Reviewer (security focus) → Tester → Reviewer (test review) → Done."
```

---

## Communication Flow Summary

```raw
Planning Workflow:
  SMALL: User → Leader → Planner → Reviewer → Leader Decision → (loop or done) → User
  BIG+:  User → Leader → Planner → Reviewer → Approver → Leader Decision → (loop or done) → User

Implementation Workflow (varies by complexity):
   Low:    User → Leader → Coder → Tester → Done → User
   Medium: User → Leader → Coder → Reviewer → Tidy → Tester → Done → User
   High:   User → Leader → Coder → Reviewer → Tidy → Tester → Reviewer → Done → User
   Tiny:   User → Leader → Coder → Done → User
```
