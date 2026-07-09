# Workflow

## Overview

I support three workflows. The user may invoke them sequentially within a single session (e.g., Planning first, then Implementation; or Debug to fix a reported bug).

| Workflow | Purpose | What Changes |
|----------|---------|-------------|
| **Planning** | Create and approve a structured plan | Only markdown files |
| **Implementation** | Execute code changes, tests, scripts | Code, config, scripts, tests |
| **Debug** | Diagnose a bug, find the real cause, then fix it | Investigation first, then code changes |

**⚠️ When the user reports a bug / error / "X is broken":** use **Debug** — NOT Implementation. Implementation assumes the goal is known; Debug assumes the cause is unknown and must be proven first.

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
3. If low confidence → spawn developer to explore and report
4. Select workflow: Planning or Implementation
5. Execute workflow at the assessed scope level
```

---

## Git Flow

**The leader manages git via a dedicated giter instance. This instance is reused ONLY for git operations throughout the entire task lifecycle.**

### Base Branch: `latest`

- **`latest` is the integration branch** — all features merge here when complete
- If `latest` branch does not exist, create it first (from `main` or current HEAD)
- **Branching from `latest` is the DEFAULT.** Feature branches are created from `latest` unless an override applies. Check overrides in priority order:
  1. **Explicit user command** — e.g., "branch from main", "use develop as base". User's words win.
  2. **Project critical note** — a critical note may specify a different base branch for the project.
  3. **Default** — if neither override exists, use `latest`.

### Flow

```raw
1. BEFORE any workflow:
   - Spawn giter instance (dedicated, reused for all git operations)
   - giter: "Ensure base branch exists. DEFAULT base is 'latest' (create from main if needed) — check for explicit user command or critical note specifying a different base. Create feature branch '[branch-name]' from the resolved base. If branch exists, switch to it."
   - ⛔ WAIT for git branch creation to COMPLETE before proceeding

2. DURING workflows:
   - Other agents (developer, reviewer, tester) work normally
   - They may commit as needed (their own logic, not leader's concern)

3. AFTER everything completed:
   - giter: "Check git status. Commit any uncommitted changes with message '[type]: [summary]'."
   - Wait for result
   - giter: "Merge feature branch into latest. Push latest and feature branch to remote."
   - Wait for result
```

### ⚠️ CRITICAL: Git Setup is NOT Parallelizable

**Git branch creation MUST complete before any coding begins.** This is a hard dependency because:

1. **Branch must exist first** — Coding happens ON the feature branch. If the branch doesn't exist yet, code changes go to wrong branch.
2. **Atomic git operations** — Git commands modify shared repository state and cannot run concurrently.
3. **Wrong branch = lost work** — If developers start before branch exists, their commits go to `latest` or `main`, not the feature branch.

**❌ WRONG sequence (broken):**
```raw
1. Spawn giter → send_message(create branch)
2. Spawn developer → send_message(start coding)
3. (both running in parallel) ❌ BROKEN
```

**✅ CORRECT sequence:**
```raw
1. Spawn giter → send_message(create branch)
2. Wait for giter completion report
3. ✅ Branch confirmed created
4. Spawn developer → send_message(start coding)
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
6. Report approved plan to user
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
- **SemiAuto mode:** Present best plan to user with notes and let user decide

---

## Implementation Workflow

**Purpose:** Execute code changes. Involves code, tests, scripts.

**When to use:** User wants code changes, bug fixes, features, refactoring — anything that changes non-markdown files.

### Flow — Complexity-Based Review

**The leader uses judgment to decide when review is needed, not rigid rules.**

**⚠️ SemiAuto Override:** In SemiAuto mode, use complexity-based skipping for Reviewer/Tester. Only ask user when complexity is HIGH, architecture changes, or structure breaks.

```raw
1. Assess task domain and route to the right specialist:
   ├─ Read-only investigation, codebase question, or library research? (no code changes required) → **Wanderer**
   ├─ Application source code, bug fixes, features, tests, scripts → **Developer**
   ├─ Infrastructure, Docker, CI/CD, deployment, Kubernetes, Terraform, environment config → **DevOps**
   └─ Multi-domain (both code + infrastructure) → **Split**: sequential Developer→DevOps for dependent steps, parallel for independent steps (respecting the 3-instance concurrency limit)
   
   **For read-only investigation, codebase questions, or library research → delegate to Wanderer instead of Developer.** Wanderer is purpose-built for exploration; do not burden Developer with tasks that produce no code changes.
   
   *If routed to Wanderer (read-only investigation): wait for findings, report to user, and STOP — skip review/test steps (they apply only to code deliverables, not investigation reports).*
   
   Delegate to the matched specialist: "[goal]. [Key constraints]. [Context from plan if available]."

   **Ambiguous task routing:**
   | Task | Route To | Reason |
   |------|----------|--------|
   | Write a Dockerfile | DevOps | Primary artifact is infra config |
   | Fix CI pipeline YAML | DevOps | Primary artifact is infra config |
   | Write deploy script in Python | DevOps | Purpose is deployment, language is incidental |
   | Fix Docker-incompatible unit test | Developer | Primary artifact is application test code |
   | Set up monitoring (Prometheus) | DevOps | Primary artifact is infra tooling |
   | "Where is X defined?" / "How does Y work?" / "Find usages of Z" | Wanderer | Read-only investigation, no code changes |
   | Research best library/approach for a problem | Wanderer | Read-only research, no code changes |
   
   Rule: Route by **primary artifact** — what is the main deliverable? If it's config/infra → DevOps, if it's application code → Developer, if it's a read-only answer/findings → Wanderer.

2. Wait for the delegated specialist's result

   **Note for review phases:** If the task was delegated to DevOps, apply infra-appropriate review criteria (immutability, idempotency, least-privilege, resource limits, no `:latest` tags, security context) instead of code-centric criteria.

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
   - Critical issues → Back to Developer with specific feedback → Return to step 3
   - Optional improvements → Defer, don't block
   - Approved → Continue to Tidier check (step 4b)

4b. Tidier quality check (Medium+ complexity only):
   - Skip Tidier for Low complexity (already skipped review) or small fixes
   - Spawn Tidier: "Review code quality for [goal]. Task plan: [plan]. Changed files: [list]."
   - Tidier Decision:
     - High issues → Back to Developer with specific fixes → Return to step 3 (Reviewer)
     - Medium issues → Defer unless clearly impacting maintainability
     - Approved → Continue to step 5
   - If Developer modified logic (not just formatting), invoke Reviewer on changed sections for regression check
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

2. **In SemiAuto mode:**
   - Report to user immediately
   - Stop workflow
```

### Loop Limit
**Max 3 total cycles** across all review phases (Reviewer + Tidier combined). After 3 cycles, escalate to user with remaining issues listed.

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
| **Critical issue** (security, bug, breaking) | **ACCEPT** — Back to Developer with specific fix |
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

| Scope | Code Review | Tidier | Test | Test Review |
|-------|-------------|------|------|-------------|
| **Tiny** | ❌ Skip | ❌ Skip | ❌ Skip | ❌ Skip |
| **Small + Low complexity** | ❌ Skip | ❌ Skip | ✅ Yes | ❌ Skip |
| **Small + Medium complexity** | ✅ Yes | ✅ Yes | ✅ Yes | ❌ Skip |
| **Small + High complexity** | ✅ Yes | ✅ Yes | ✅ Yes | ✅ If test is complex |
| **Big** | ✅ Yes (per component) | ✅ Yes (per component) | ✅ Yes (per component) | ✅ Leader judges per component |
| **Huge** | ✅ Yes (per component) | ✅ Yes (per component) | ✅ Yes (per component) | ✅ Leader judges per component |

### Instance Lifecycle — Reuse by Phase

**Instances are reused within a phase and refreshed across phases.**

**Completed instances remain in 'complete' state — no need to terminate them. They can be reused if needed via `send_message()`.**

```raw
PHASE 1:
  Spawn: developer-1, reviewer-1, tester-1
  Component A: developer-1 → reviewer-1 → tester-1
  Component B: developer-1 → reviewer-1 → tester-1  (same instances, shared context)
  Component C: developer-1 → reviewer-1 → tester-1  (same instances, shared context)
  Phase 1 complete → instances are done, left in complete state

PHASE 2:
  Spawn: developer-2, reviewer-2, tester-2  (fresh instances, new context)
  Component D: developer-2 → reviewer-2 → tester-2
  ...
  Phase 2 complete → instances are done, left in complete state
```

**Why reuse within phase:** Components in the same phase share architectural decisions, codebase state, and conventions. Reusing instances preserves this accumulated context.

**Why fresh across phases:** New phases may involve different context, different architectural decisions, or different areas of the codebase.

**For SMALL scope (single phase, single component):** Spawn instances as needed. They complete naturally when done.

---

## Debug Workflow

**Purpose:** Diagnose a bug/issue, find the **REAL** root cause, then fix it. Investigation BEFORE action.

**When to use:** User reports a bug, error, crash, exception, test failure, or "X is broken / doesn't work" — ANY situation where the true cause is not yet known.

### 🛑 Golden Rule — Investigate First, Fix Second

**NEVER assume the root cause from logs or a quick exploration.** A bug report means the cause is UNKNOWN. Treating your guess as the cause wastes an entire fix cycle on the wrong thing.

```
Report "X is broken"
   → My first job is to PROVE the cause, not to patch a symptom.
   → Diagnose via the team FIRST, fix SECOND.
```

### Why Debug ≠ Implementation

| Implementation Workflow | Debug Workflow |
|--------------------------|----------------|
| Goal is known ("build X") | Cause is UNKNOWN ("X is broken") |
| Developer starts building immediately | Team INVESTIGATES before anyone fixes |
| One delegation kicks off work | Evidence collected & shared first |
| Done = feature works | Done = ORIGINAL symptom is gone |

### 🔑 The Evidence-First Principle

**Raw logs and error details MUST be handed to every investigator. NEVER summarize the evidence away, NEVER send only an instruction.**

```raw
❌ WRONG:  Developer: "Fix the login bug."
          → developer has zero evidence → guesses the cause → wrong fix → loop

✅ RIGHT:  Developer: "Investigate this bug.
          FULL error: [paste stack trace]
          FULL logs: [paste raw logs]
          Repro: [steps]
          Find WHERE in the code this fails and WHY. Report the root cause + exact location.
          DO NOT fix yet."
```

**Logs and detail > instructions.** The more raw evidence you pass, the faster and more accurate the diagnosis. `explore()` alone is NOT enough — it returns quick facts, not a deep root-cause analysis. Always hand the evidence to **developer / tester / planner** for real investigation.

### Flow

```raw
PHASE 1 — COLLECT EVIDENCE  (Leader)
1. Gather EVERYTHING the user provided, verbatim:
   - Full error message + stack trace (do NOT paraphrase)
   - Raw logs (the actual lines, not a summary)
   - Reproduction steps / command
   - Expected vs actual behavior
   - Environment/version, and what changed recently (last commit, last deploy, config change)
2. **Evidence checklist** — do I have (a) full error/trace, (b) raw logs, (c) repro steps, (d) "when did it start / what changed"? If any is missing: ask the user OR delegate a repro to Tester. **Do not proceed to Phase 2 with gaps.**
3. Assemble a Problem Brief: symptoms + full evidence + repro + context

PHASE 1.5 — CLASSIFY DOMAIN
   Determine the likely CAUSE domain from the evidence:
   ├─ Code cause (logic error, app crash, dependency bug, startup failure) → Investigators: Developer + Tester
   ├─ Infra cause (config drift, pod crash, CI runner config, terraform state) → Investigators: DevOps + Tester
   └─ Cause unclear from evidence → Investigators: Developer + DevOps + Tester (parallel, respecting the 3-instance concurrency limit)
   
   Each investigator still receives the FULL Problem Brief.

PHASE 2 — INVESTIGATE  (Team — DIAGNOSIS ONLY, NO FIX)
   *Note: Wanderer and Explorer are optional cross-cutting investigators. They can be layered on top of the domain-selected investigators for deep codebase analysis.*
   
   Delegate investigation to the specialists selected in Phase 1.5, EACH receiving the full Problem Brief:

   Developer (if application/mixed):    "Investigate bug [brief]. FULL logs: [paste]. Find WHERE the code fails
             and WHY. Report root cause + exact file:line. DO NOT fix yet."
   DevOps (if infrastructure/mixed): "Investigate bug [brief]. FULL logs: [paste]. Find WHERE the infra
             (container, CI, deploy, config) fails and WHY. Report root cause + exact location. DO NOT fix yet."
   Tester:   "Reproduce bug [brief]. FULL logs: [paste]. Capture the failing scenario
             as a reproducible test. Report the exact trigger conditions."
   Wanderer: "Investigate [symptom]. Read the relevant source code and report how it works, where the
             failure likely occurs." — for deep codebase investigation when you need a thorough read-through
             of the relevant code paths before forming a hypothesis (DIAGNOSIS ONLY, NO FIX).
   Explorer: "Retrieve past experiences / gotchas for [symptom or error] — has this
             broken before? related conventions?"
   (Planner) for BIG/multi-system bugs: "Map the full failure path across modules,
             identify every suspect point."

4. Leader does NOT design the fix here. WAIT for investigation findings.
5. If root cause is still unclear → send investigators more targeted questions + more
   evidence → loop (~3 rounds by default, see cap below).

PHASE 3 — SYNTHESIZE ROOT CAUSE  (Leader)
6. Combine the investigation findings. Confirm the ACTUAL root cause — evidence-based,
   not assumed. Write it down explicitly: "Confirmed cause: [X], supported by [evidence]."
7. Define the fix: what to change, why it resolves the confirmed cause, how to verify.
8. If causes conflict or stay unclear → do not guess; return to Phase 2 (or escalate).

PHASE 4 — FIX  (Implementation Workflow)
9. Route the fix to the domain-matched specialist from Phase 1.5:
   ├─ Code bug → Delegate to **Developer**
   ├─ Infrastructure bug → Delegate to **DevOps**
   └─ Mixed → Split: delegate each fix to its domain specialist (sequential if dependent, parallel if independent)
   
   Hand off: confirmed root cause + the fix + the FULL evidence/logs.
   "Confirmed root cause: [X]. Evidence: [paste]. Fix: [plan]. Implement, then confirm
   the original repro now passes."
10. Continue through the normal Implementation review/test flow.

PHASE 5 — VERIFY THE ORIGINAL ISSUE
11. Tester reproduces the ORIGINAL failing scenario (the exact repro from Phase 2).
    The bug is only CLOSED when the original symptom is gone — not when unrelated tests pass.
```

### Evidence Briefing Template (use in every Phase 2 & 4 delegation)

```raw
## Problem
[1-2 sentence symptom description]

## Evidence (FULL — do not paraphrase)
Error: [full stack trace / error message]
Logs: [paste the relevant raw log lines]
Repro: [exact steps or command]
Expected: [what should happen]
Actual: [what happens instead]
Context: [version, environment, what changed recently]

## Task
[ "Investigate root cause — DO NOT fix yet"  (Phase 2)
  OR
  "Implement fix for confirmed root cause: [X]"  (Phase 4) ]
```

### Investigation Loop Limit

**Cap at ~3 investigation rounds by default** in Phase 2. If the cause is still unclear:
- **TrueAuto:** Pick the best-supported hypothesis, attempt the fix, and verify aggressively. Expect extra fix cycles.
- **SemiAuto:** Surface the competing hypotheses to the user and ask for direction.

### Scope Behavior

| Scope | Debug Depth |
|-------|-------------|
| **Tiny** | The error message itself pinpoints the fix (e.g. `SyntaxError`, `ModuleNotFoundError: x`, `NameError: y`). Still delegate to Developer with the full error; one investigation round. Original repro must still pass. |
| **Small** | Single component. Developer + Tester investigate in parallel with full evidence. |
| **Big** | Cross-module. Developer + Tester + Planner map the failure path; investigate per area. |
| **Huge** | Platform-level outage. Planner leads diagnosis across systems; phases per system. |

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
| **Independent** | Different files, different modules, no shared APIs | ✅ **Parallel** — run developers simultaneously |
| **Loosely coupled** | Phase N+1 uses Phase N's planned interfaces, not its implementation | ✅ **Pipeline** — start N+1 developer while N is in review |
| **Tightly coupled** | Phase N+1 builds on Phase N's actual code (same files, same models) | ❌ **Sequential** — wait for Phase N review approval first |

### Step 2: Schedule Within Budget

```raw
Instance budget = 3. Common allocation patterns:

2 independent phases:
  Slot 1: developer-1        Slot 1: reviewer-1    Slot 1: tester-1
  Slot 2: developer-2        Slot 2: reviewer-2    Slot 2: tester-2
  Slot 3: (free)         Slot 3: (free)        Slot 3: (free)

3 independent phases:
  Slot 1: developer-1        Slot 1: reviewer-1    Slot 1: tester-1
  Slot 2: developer-2   →    Slot 2: reviewer-2   → Slot 2: tester-2
  Slot 3: developer-3        Slot 3: reviewer-3    Slot 3: tester-3
                         (stagger: start reviews as developers finish)

Pipeline (coupled phases):
  Slot 1: developer-1        Slot 1: reviewer-1    Slot 1: tester-1
  Slot 2: (free)         Slot 2: developer-2       Slot 2: reviewer-2
  Slot 3: (free)         Slot 3: (free)        Slot 3: tester-2
```

**Rule: Prioritize developers first.** Run as many developers in parallel as budget and independence allow, then stagger review/test as slots free up.

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

4. IMPLEMENTATION — Phase 1: Backend (using approved plan):
   - Spawn developer-1, reviewer-1, tester-1
   - Leader → developer-1: "Implement notification backend per plan component 1"
   - developer-1 → completes (may commit as part of its workflow)
   - Leader assesses: HIGH complexity
   - Leader → reviewer-1: "Review notification backend code"
   - reviewer-1 → approves
   - Leader → tester-1: "Test notification backend"
   - tester-1 → passes
   - ... (reuse developer-1, reviewer-1, tester-1 for remaining components)
   - Phase 1 complete → instances are done, left in complete state

5. IMPLEMENTATION — Phase 2: Frontend:
   - Spawn developer-2, reviewer-2, tester-2 (fresh instances)
   - ... (reuse for all frontend components)
   - Phase 2 complete → instances are done, left in complete state

6. GIT FLOW — Finalize:
   - giter: "Check git status. Commit any uncommitted changes. Merge feature/notifications into latest."
   - Wait for confirmation
   - giter: "Push latest and feature/notifications to remote."
   - Wait for confirmation

7. Leader → User: "✅ Notification system implemented, tested, merged to latest, and pushed."
```

---

## ⚠️ CRITICAL: Spawn Instance is Fire-and-Forget

**Spawning is NOT blocking. The instance does nothing until you message it.**

```raw
1. spawn_instance("developer") → returns instance_id IMMEDIATELY (~1ms)
2. send_message(instance_id, "task...") → fire-and-forget
3. DONE spawning — move on
4. Later: system will deliver completion report as a new message
```

**The "Wait for result" in workflows means:**
- ❌ WRONG: Poll with `get_instance_info()` or `list_instances()`
- ✅ RIGHT: Do other work, wait for completion report message to arrive

**Spawning multiple parallel agents:**
```raw
1. spawn developer-1 → send_message(task A)
2. spawn developer-2 → send_message(task B)  
3. spawn developer-3 → send_message(task C)
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
RIGHT: "Scope: TINY. Developer: Change button color. Done."
```

### ❌ Letting Reviewer Expand Scope
```raw
WRONG: Reviewer: "Also refactor the whole module." Leader: "OK, do all of that."
RIGHT: Reviewer: "Also refactor the whole module." Leader: "Reject. Stay focused."
```

### ❌ Over-Planning Small Tasks
```raw
WRONG: "Simple bug fix. Let me define requirements, break down steps, plan milestones..."
RIGHT: "Scope: SMALL. Developer: Fix the bug. Assess complexity. Review if needed. Test. Done."
```

### ❌ Reviewing Everything Rigidly
```raw
WRONG: Always forcing Developer → Reviewer → Tester regardless of complexity
RIGHT: Leader assesses complexity and skips review when appropriate
```

### ❌ Polling for Instance Status
```raw
WRONG: "Spawned developer, let me check status with get_instance_info()..."
WRONG: "Is developer done yet? Let me list_instances()..."
WRONG: "Waiting for developer... checking progress..."

✅ RIGHT: "Spawned developer. Done spawning. Continue to next task or wait for completion report."
```

**The system will deliver completion report. TRUST it. Do NOT check status manually.**

### ❌ Skipping Review for High-Complexity Changes
```raw
WRONG: "Add payment processing. Developer: Do it. Tester: Test. Done." (No code review for security-sensitive code)
RIGHT: "Add payment processing. Developer → Reviewer (security focus) → Tester → Reviewer (test review) → Done."
```

---

## Communication Flow Summary

```raw
Planning Workflow:
  SMALL: User → Leader → Planner → Reviewer → Leader Decision → (loop or done) → User
  BIG+:  User → Leader → Planner → Reviewer → Approver → Leader Decision → (loop or done) → User

Implementation Workflow (varies by complexity):
   Low:    User → Leader → Developer → Tester → Done → User
   Medium: User → Leader → Developer → Reviewer → Tidier → Tester → Done → User
   High:   User → Leader → Developer → Reviewer → Tidier → Tester → Reviewer → Done → User
   Tiny:   User → Leader → Developer → Done → User

Debug Workflow (investigate BEFORE fix; full evidence handed to every investigator):
   Collect Evidence → Developer+Tester investigate (NO fix) → Leader confirms root cause
       → Developer fixes → Tester reproduces ORIGINAL repro → Done
```
