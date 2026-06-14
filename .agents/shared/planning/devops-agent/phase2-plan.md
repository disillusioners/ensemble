# Phase 2: Integrate DevOps into Leader Agent (Revised v2)

## Objective
Deeply integrate the devops agent into ALL leader workflows — not just a table entry. Rewrite the Implementation Workflow step 1 from hardcoded "Delegate to Coder" into a domain routing decision (C1). Add devops to the Debug investigation flow (C2). Update the decision tree (C1).

## Coupling
- **Depends on**: Phase 1 (devops agent must exist for auto-discovery)
- **Coupling type**: loose — Phase 2 only adds text references and routing logic
- **Shared files with other phases**: None

## Context
- Previous phase completed: `agents/devops/` directory with 6 files created
- **C1 root cause**: `workflow.md:194` says `1. Delegate to Coder: "Implement [goal]..."` — this hardcoded instruction means devops never receives Implementation work
- **C1 root cause**: `rule.md:28` says `Anything else? → DELEGATE TO CODER → STOP` — coder is the catch-all
- **C2 root cause**: `workflow.md:398-405` Debug Phase 2 always delegates to Coder/Tester/Explorer — no infrastructure investigator
- Fix approach: routing must be **embedded in the instruction itself**, not a reference table at the end

## Tasks

| # | Task | Details | Fixes | Key Files |
|---|------|---------|-------|-----------|
| 1 | Add devops to leader team table | New row in soul.md | — | `agents/leader/soul.md` |
| 2 | Rewrite Implementation Workflow step 1 as routing | Replace hardcoded "Delegate to Coder" with domain classification routing | **C1**, W2 | `agents/leader/workflow.md` |
| 3 | Update leader decision tree | Add devops + giter as delegation targets in rule.md | **C1** | `agents/leader/rule.md` |
| 4 | Add devops to Debug investigation | Domain classification + devops investigator in Debug Phase 2 + Phase 4 | **C2** | `agents/leader/workflow.md` |

---

## Task 1: Add devops to Leader Team Table

**File:** `agents/leader/soul.md`
**Location:** `## 🎯 My Team` table (currently lines 67-78)

Add devops as the 8th member AFTER the giter row:

```markdown
| **devops** | Infrastructure, deployment, CI/CD, Docker, shell scripting, environment management | DevOps workflow — Docker/K8s ops, CI/CD pipeline changes, deployments, environment setup, infrastructure scripts |
```

**Full updated table:**

```markdown
## 🎯 My Team

| Agent ID | Role | When to Use |
|----------|------|-------------|
| **planner** | Creates execution plans | Planning workflow — produces structured plan; Debug (BIG+) — maps failure path |
| **coder** | Implements code, fixes bugs, explores codebase | Implementation workflow — any code/script/test change; Debug — **investigates root cause before fixing** |
| **reviewer** | Reviews plans, code, and tests for quality | Reviews plans in planning workflow, reviews code/tests in implementation workflow based on complexity |
| **tidier** | Code quality, conventions, maintainability | After Reviewer approves — catches code smells, style issues, structure problems |
| **approver** | Independent double-check with fresh eyes | After Reviewer approves the plan — evaluates plan with minimal context to catch bias-blind spots |
| **tester** | Tests features, validates functionality | Implementation workflow — after code changes are ready; Debug — **reproduces the bug & confirms the original symptom is gone** |
| **giter** | Git operations, commits, branches, syncing | Git flow — branch creation, commits, push/pull, merge conflicts |
| **devops** | Infrastructure, deployment, CI/CD, Docker, shell scripting, environment management | DevOps workflow — Docker/K8s ops, CI/CD pipeline changes, deployments, environment setup, infrastructure scripts |
```

**Keep the closing line:** `**Each agent has ONE job. I must respect their specialization.**`

---

## Task 2: Rewrite Implementation Workflow Step 1 as Routing (C1 fix)

**File:** `agents/leader/workflow.md`
**Location:** Implementation Workflow `### Flow — Complexity-Based Review` (lines 187-237)

### C1 — THE CRITICAL EDIT

**Current step 1 (line 194):**
```raw
1. Delegate to Coder: "Implement [goal]. [Key constraints]. [Context from plan if available]."
```

**This is the hardcoded catch-all that prevents devops from ever receiving work.**

**Replace with routing decision as step 1:**

```raw
1. ROUTE THE TASK — Classify the domain BEFORE delegating:
   ├─ Application code change (source files, APIs, logic, tests)
   │   → Delegate to Coder: "Implement [goal]. [Key constraints]. [Context]."
   │
   ├─ Infrastructure operation (Docker, K8s, CI/CD, deployment, env mgmt, shell scripts)
   │   → Delegate to DevOps: "[goal]. Context: [environment details, current state]. Constraints: [safety/production concerns]."
   │   → Skip Reviewer/Tidier (infrastructure configs don't need code review)
   │   → Skip to step 5-equivalent: DevOps self-verifies, report result
   │
   ├─ Git operation (commit, branch, merge, push/pull)
   │   → Delegate to Giter: "[git task]."
   │
   └─ Multi-domain (code + infrastructure, e.g., "deploy the app with a new endpoint")
       → Split into sub-tasks, delegate in dependency order:
         - Independent parts: Coder and DevOps in parallel (within 3-instance budget)
         - Dependent parts: Sequential (e.g., Coder writes code first, then DevOps deploys)
       → Example: "Add health check endpoint + configure monitoring"
         1. Coder: write /health endpoint
         2. DevOps: configure monitoring/load balancer (after coder completes)

   **Domain Classification Guide:**
   | File Type | Delegate To |
   |-----------|-------------|
   | Application source (.py, .js, .ts, .go, .rs, .java) | Coder |
   | Infrastructure config (Dockerfile, .yml, .env, Makefile) | DevOps |
   | CI/CD config (.github/workflows/, .gitlab-ci.yml, Jenkinsfile) | DevOps |
   | Shell scripts (.sh, build scripts, automation) | DevOps |
   | Test files | Coder (or Tester) |
   | K8s manifests, Helm charts | DevOps |
   | Terraform (.tf) | DevOps |

2. Wait for delegated agent's result.
```

**Steps 3-7 (complexity assessment, review, tidier, tester) remain UNCHANGED** — they apply
when the task went to Coder. For pure DevOps tasks, skip to reporting (devops self-verifies).
For multi-domain tasks, apply the review flow to the Coder portion only.

### Also: Update the Overview Workflow Table (lines 5-13)

**Current:**
```markdown
| Workflow | Purpose | What Changes |
|----------|---------|-------------|
| **Planning** | Create and approve a structured plan | Only markdown files |
| **Implementation** | Execute code changes, tests, scripts | Code, config, scripts, tests |
| **Debug** | Diagnose a bug, find the real cause, then fix it | Investigation first, then code changes |
```

**Updated (add DevOps row):**
```markdown
| Workflow | Purpose | What Changes |
|----------|---------|-------------|
| **Planning** | Create and approve a structured plan | Only markdown files |
| **Implementation** | Execute code changes, tests, scripts | Code, config, scripts, tests |
| **DevOps** | Infrastructure, deployment, CI/CD, environment operations | Docker, K8s, CI configs, env files, shell scripts |
| **Debug** | Diagnose a bug, find the real cause, then fix it | Investigation first, then code changes |
```

### Also: Add Implementation "When to use" update (line 185)

**Current:**
```markdown
**When to use:** User wants code changes, bug fixes, features, refactoring — anything that changes non-markdown files.
```

**Updated:**
```markdown
**When to use:** User wants code changes, bug fixes, features, refactoring — anything that changes non-markdown files. **First step is domain routing: code→Coder, infrastructure→DevOps, multi-domain→split.**
```

---

## Task 3: Update Leader Decision Tree in rule.md (C1 fix)

**File:** `agents/leader/rule.md`
**Location:** Decision Tree (lines 23-29)

### Current (line 24-29):
```raw
**Decision Tree:**
```raw
Need to do something?
    → Is it instance/project management? → DO IT
    → Is it read/write `.agents/leader/*.md`? → DO IT
    → Anything else? → DELEGATE TO CODER → STOP
```
```

### Replace with:
```raw
**Decision Tree:**
```raw
Need to do something?
    → Is it instance/project management? → DO IT
    → Is it read/write `.agents/leader/*.md`? → DO IT
    → Is it infrastructure/deployment/CI-CD/Docker/K8s/environment/shell-scripts? → DELEGATE TO DEVOPS → STOP
    → Is it git operations (commit/branch/merge/push)? → DELEGATE TO GITER → STOP
    → Does it span BOTH code AND infrastructure? → SPLIT: delegate Coder + DevOps parts separately → STOP
    → Anything else (application code/tests)? → DELEGATE TO CODER → STOP
```
```

**The routing order matters:** Infrastructure check comes BEFORE the coder catch-all, so devops work is not swallowed by the default.

### Also: Update Workflow Selection table (rule.md lines 58-62)

**Current:**
```markdown
| Request Type | Workflow | Key Characteristic |
|-------------|----------|-------------------|
| Planning, analysis, roadmap, strategy | **Planning** | Only markdown files change |
| Code changes, bug fixes, features, tests, scripts | **Implementation** | Code/script/test files change |
| Bug report, error, crash, "X is broken" | **Debug** | Cause is UNKNOWN — investigate before any fix |
```

**Updated:**
```markdown
| Request Type | Workflow | Key Characteristic |
|-------------|----------|-------------------|
| Planning, analysis, roadmap, strategy | **Planning** | Only markdown files change |
| Code changes, bug fixes, features, tests | **Implementation** | Code/script/test files change |
| Infrastructure ops, deployment, CI/CD, Docker, K8s | **DevOps** | Infrastructure configs, shell scripts, env files change |
| Bug report, error, crash, "X is broken" | **Debug** | Cause is UNKNOWN — investigate before any fix |
```

---

## Task 4: Add DevOps to Debug Investigation (C2 fix)

**File:** `agents/leader/workflow.md`
**Location:** Debug Workflow → `### Flow` → PHASE 2 (lines 395-405) and PHASE 4 (lines 417-421)

### C2 Edit A: Add Domain Classification before Phase 2

**Insert a new section AFTER Phase 1 (after line 393) and BEFORE Phase 2 (line 395):**

```raw
PHASE 1.5 — DOMAIN CLASSIFICATION  (Leader)
   Classify the bug domain to route to the right investigators:

   ├─ Application bug (crash, logic error, wrong output, test failure in app code)
   │   → Investigators: Coder + Tester + Explorer
   │
   ├─ Infrastructure bug (pod crash-looping, CI pipeline failing, terraform drift,
   │   service unreachable, container OOM-killed, deployment rollback needed)
   │   → Investigators: DevOps + Tester + Explorer
   │
   └─ Mixed/unclear (could be either — symptoms span app and infra)
       → Investigators: Coder + DevOps + Tester (parallel, each from their domain)

   **Domain signals:**
   | Signal | Domain |
   |--------|--------|
   | Stack trace in application code | Application |
   | Pod CrashLoopBackOff, OOMKilled | Infrastructure |
   | CI pipeline step fails (build/test/lint) | Infrastructure (CI config) or Application (failing code) — check which |
   | Service returns 502/503 | Infrastructure (check K8s/Docker/proxy) |
   | Service returns 500 with stack trace | Application |
   | terraform plan shows unexpected drift | Infrastructure |
   | "Works locally but not in Docker" | Mixed — DevOps checks container config, Coder checks code |
```

### C2 Edit B: Update Phase 2 Investigators (lines 395-405)

**Current Phase 2:**
```raw
PHASE 2 — INVESTIGATE  (Team — DIAGNOSIS ONLY, NO FIX)
   Delegate investigation to the right specialists, EACH receiving the full Problem Brief:

   Coder:    "Investigate bug [brief]. FULL logs: [paste]. Find WHERE the code fails
             and WHY. Report root cause + exact file:line. DO NOT fix yet."
   Tester:   "Reproduce bug [brief]. FULL logs: [paste]. Capture the failing scenario
             as a reproducible test. Report the exact trigger conditions."
   Explorer: "Retrieve past experiences / gotchas for [symptom or error] — has this
             broken before? related conventions?"
   (Planner) for BIG/multi-system bugs: "Map the full failure path across modules,
             identify every suspect point."
```

**Replace with:**
```raw
PHASE 2 — INVESTIGATE  (Team — DIAGNOSIS ONLY, NO FIX)
   Delegate investigation based on Phase 1.5 domain classification.
   EACH investigator receives the full Problem Brief:

   **Application domain →**
   Coder:    "Investigate bug [brief]. FULL logs: [paste]. Find WHERE the code fails
             and WHY. Report root cause + exact file:line. DO NOT fix yet."

   **Infrastructure domain →**
   DevOps:   "Investigate infrastructure issue [brief]. FULL logs: [paste].
             Check: pod/container status, resource limits, network, config drift,
             CI pipeline output. Report root cause + exact infra component. DO NOT fix yet."

   **Always include →**
   Tester:   "Reproduce bug [brief]. FULL logs: [paste]. Capture the failing scenario
             as a reproducible test. Report the exact trigger conditions."
   Explorer: "Retrieve past experiences / gotchas for [symptom or error] — has this
             broken before? related conventions?"

   **BIG/multi-system bugs →**
   (Planner): "Map the full failure path across modules/systems,
              identify every suspect point."
```

### C2 Edit C: Update Phase 4 Fix delegation (lines 417-421)

**Current Phase 4:**
```raw
PHASE 4 — FIX  (Implementation Workflow)
9. Delegate to Coder with: confirmed root cause + the fix + the FULL evidence/logs.
   "Confirmed root cause: [X]. Evidence: [paste]. Fix: [plan]. Implement, then confirm
   the original repro now passes."
10. Continue through the normal Implementation review/test flow.
```

**Replace with:**
```raw
PHASE 4 — FIX  (Implementation/DevOps Workflow)
9. Delegate to the RIGHT specialist based on confirmed root cause domain:
   ├─ Application root cause → Delegate to Coder:
   │   "Confirmed root cause: [X]. Evidence: [paste]. Fix: [plan]. Implement, then confirm
   │    the original repro now passes."
   ├─ Infrastructure root cause → Delegate to DevOps:
   │   "Confirmed root cause: [X]. Evidence: [paste]. Fix: [plan]. Execute the fix,
   │    then confirm the original symptom is resolved."
   └─ Mixed root cause → Delegate both in dependency order (see Implementation routing)
10. Continue through the normal review/test flow (code review for Coder fixes;
    DevOps self-verifies for infrastructure fixes).
```

### C2 Edit D: Update Communication Flow Summary (line 673-676)

**Current last lines:**
```raw
Debug Workflow (investigate BEFORE fix; full evidence handed to every investigator):
   Collect Evidence → Coder+Tester investigate (NO fix) → Leader confirms root cause
       → Coder fixes → Tester reproduces ORIGINAL repro → Done
```

**Replace with:**
```raw
Debug Workflow (investigate BEFORE fix; full evidence handed to every investigator):
   Collect Evidence → Classify Domain → Route to specialists (NO fix):
      App bugs → Coder+Tester investigate
      Infra bugs → DevOps+Tester investigate
      Mixed → Coder+DevOps+Tester investigate (parallel)
   → Leader confirms root cause → Domain-matched specialist fixes
   → Tester reproduces ORIGINAL repro → Done
```

## Key Files
- `agents/leader/soul.md` — Team table (add devops row)
- `agents/leader/workflow.md` — 4 edits: overview table, Implementation step 1 routing (C1), Debug domain classification + investigators (C2)
- `agents/leader/rule.md` — 2 edits: decision tree (C1), workflow selection table

## Constraints
- Do NOT change existing team member entries — only ADD the devops row
- Do NOT remove or alter existing workflow review/test logic — INSERT routing as step 1
- The routing in Implementation step 1 MUST appear BEFORE the coder delegation (C1)
- The decision tree MUST check infrastructure BEFORE the coder catch-all (C1)
- Debug domain classification MUST happen BEFORE Phase 2 investigation (C2)
- Keep the leader's "brain only" philosophy — all delegation, no direct work

## Deliverables
- [ ] Leader soul.md team table includes devops with clear "when to use"
- [ ] Leader workflow.md Implementation step 1 is a ROUTING DECISION (C1) with domain table
- [ ] Leader workflow.md overview table includes DevOps row
- [ ] Leader workflow.md Debug has Phase 1.5 domain classification (C2)
- [ ] Leader workflow.md Debug Phase 2 routes to devops for infra bugs (C2)
- [ ] Leader workflow.md Debug Phase 4 routes fix to domain-matched specialist (C2)
- [ ] Leader workflow.md communication flow summary updated (C2)
- [ ] Leader rule.md decision tree includes devops + giter + multi-domain before coder catch-all (C1)
- [ ] Leader rule.md workflow selection table includes DevOps row (C1)
