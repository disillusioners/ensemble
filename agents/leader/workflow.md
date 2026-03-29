# Workflow

## 🎯 SCOPE-FIRST APPROACH
[... existing scope table ...]

## 🔄 PHASE ISOLATION RULE (CRITICAL)

**For multi-phase requests: EACH PHASE = BRAND NEW AGENT SESSIONS**

```
Phase 1 Complete ──→ Phase 2 Start ──→ Phase 3 Start
      ↓                   ↓                   ↓
  terminate/             spawn               spawn
  stop using           Coder_2             Coder_3
  Coder_1             Reviewer_2           Reviewer_3
  Reviewer_1          Tester_2             Tester_3
  Tester_1
```

**NEVER reuse**: session_ids, agent sessions, or context from previous phases.
**ALWAYS spawn**: fresh Coder, Reviewer, Tester for each new phase.

---

## 📋 CONTEXT HANDOFF PROTOCOL (CRITICAL)

**New phase agents need context. Provide it via FILES, not walls of text.**

### Before Spawning New Phase Agents

```
1. WRITE phase plan to shared working directory:
   write_file(".agents/shared/working/{feature_name}/phase2-plan.md", """
   # Phase 2: [Goal]
   
   ## Objective
   [What this phase delivers]
   
   ## Context
   [What Phase 1 completed, key decisions]
   
   ## Requirements
   [Specific requirements for this phase]
   
   ## Key Files
   - path/to/file1.py — [purpose]
   - path/to/file2.py — [purpose]
   
   ## Constraints
   [Critical constraints]
   """)

2. SPAWN with concise message:
   send_message(session_id, """
   Phase 2: [Goal]
   
   Plan: .agents/shared/working/{feature_name}/phase2-plan.md
   Key files: src/auth/, config/db.yaml
   
   Constraints: [if any]
   """)
```

### Context Handoff Template

```
Phase N: [One-line goal]

Plan: .agents/shared/working/{feature_name}/phaseN-plan.md
Key files: src/auth/, config/db.yaml

Constraints: [1-2 critical items or "None"]
```

### File Location Conventions

```
Project Root/
├── .agents/
│   ├── shared/
│   │   └── working/
│   │       └── {feature_name}/
│   │           ├── phase1-plan.md      ← Phase 1 plan
│   │           ├── phase2-plan.md      ← Phase 2 plan
│   │           ├── decisions.md        ← Architecture decisions
│   │           └── notes.md            ← Working notes
│   └── leader/
│       └── LESSONS/                    ← Coordination patterns
└── [project files]
```

### What Goes In Plan File

| Section | Content | Length |
|---------|---------|--------|
| **Objective** | What this phase delivers | 1-2 sentences |
| **Context** | What previous phase completed | 2-3 bullet points |
| **Requirements** | Specific requirements | Bullet list |
| **Key Files** | Important files with purpose | List with 1-line descriptions |
| **Constraints** | Critical constraints | Bullet list |
| **Decisions** | Key decisions made | Only if relevant |

**Keep plan files CONCISE. Agent can explore code for details.**

---

## 📊 PLANNER INTEGRATION

**For complex requests, call Planner BEFORE orchestrating execution.**

### When to Call Planner

| Scope | Action |
|-------|--------|
| **HUGE** | REQUIRED — Spawn planner for multi-phase roadmap |
| **LARGE** | RECOMMENDED — Spawn planner for detailed plan |
| **MEDIUM** | OPTIONAL — Leader creates inline plan, or call planner |
| **SMALL** | NOT NEEDED — Execute directly with coder |

### How to Call Planner

```
1. SPAWN planner session:
   spawn_session("planner", project_id)

2. SEND planning request:
   send_message(planner_session_id, """
   Create execution plan for: [feature/task name]
   
   Context:
   - [brief description]
   
   Working directory: [project path]
   
   Request scope: [small/medium/large/huge]

   PHASE GRANULARITY — FOLLOW STRICTLY:
   - Create ONE .md FILE PER PHASE (phase1-plan.md, phase2-plan.md, etc.)
   - Each phase = 1 coder session's worth of work
   - Right size: MODULE-LEVEL grouping (e.g., "auth module", "user profile CRUD", "payment integration")
   - Too small: splitting to component/function level (e.g., "login form only", "validateJWT function")
   - Too big: grouping unrelated features into one phase
   - If 5+ phases needed, consider if some can be merged at module level
   """)

3. RECEIVE plan from planner (structured markdown, multiple phase files)

4. EXECUTE using plan as guide:
   - Spawn coder/reviewer/tester per phase
   - Track progress via planner if LARGE/HUGE
```

### Phase Granularity Guidelines (Strict)

**When instructing Planner, enforce this sizing rule:**

| Granularity | Example | Verdict |
|---|---|---|
| ✅ Module level | `auth module` (login, register, logout, middleware together) | **RIGHT SIZE** |
| ✅ Feature slice | `user profile CRUD` (model, API, UI together) | **RIGHT SIZE** |
| ✅ Integration layer | `payment integration` (gateway, webhook, reconciliation) | **RIGHT SIZE** |
| ❌ Component level | `login form component only` | **TOO SMALL** |
| ❌ Single function | `validateJWT function only` | **WAY TOO SMALL** |
| ❌ Fragmented module | Splitting auth into: `login only`, `register only`, `logout only` | **TOO FRAGMENTED** |

**Rule: Each phase should contain ~3-10 tasks at the module/feature level. If a phase has only 1-2 tasks or covers less than one module, merge it with adjacent phases.**

### Planner Output Format

Planner returns:
```
📋 **Plan Created**: [feature name]

**Scope**: [assessment]
**Phases**: [number]
**Est. Time**: [estimate]

Plan overview: .agents/shared/working/{feature_name}/plan-overview.md
Phase files:
  - .agents/shared/working/{feature_name}/phase1-plan.md
  - .agents/shared/working/{feature_name}/phase2-plan.md
  - .agents/shared/working/{feature_name}/phaseN-plan.md

[1-2 sentence summary]
```

### Multi-File Plan Structure (REQUIRED for LARGE/HUGE)

**Planner MUST output multiple files — one per phase:**

```
.agents/shared/working/{feature_name}/
├── plan-overview.md          ← Summary: objectives, phase list, dependencies, risks
├── phase1-plan.md            ← Phase 1: self-contained plan for 1 coder session
├── phase2-plan.md            ← Phase 2: self-contained plan for 1 coder session
├── phaseN-plan.md            ← Phase N: ...
├── decisions.md              ← Architecture decisions (shared across phases)
└── notes.md                  ← Working notes
```

**plan-overview.md contains:** objectives, scope, phase index with dependencies, risks, success criteria.
**phaseN-plan.md contains:** objective, context from prior phases, task breakdown, key files, constraints.

**Leader uses plan-overview.md for orchestration, hands individual phaseN-plan.md to each coder session.**

### Tracking with Planner (LARGE/HUGE only)

```
- Planner maintains plan file with task status
- Planner reports periodic updates to Leader
- Leader delegates execution to coder/reviewer/tester
- Leader monitors overall progress
```

---

## 🔄 PLAN REVIEW LOOP (CRITICAL)

**Before executing any plan, have Reviewer validate it. Loop until approved.**

### The Loop

```
Planner creates plan → Reviewer reviews plan → Pass feedback to Planner → (repeat until approved) → Execute
```

### Step-by-Step

```
1. SPAWN planner session:
   spawn_session("planner", project_id)

2. REQUEST plan:
   send_message(planner_session_id, """
   Create execution plan for: [feature/task name]
   
   Context:
   - [brief description]
   
   Working directory: [project path]
   """)

3. RECEIVE plan from planner

4. SPAWN reviewer session:
   spawn_session("reviewer", project_id)

5. REQUEST plan review:
   send_message(reviewer_session_id, """
   Review plan: [plan file path or inline content]
   
   Review type: Plan
   Focus areas: Completeness, Feasibility, Clarity, Risks
   
   Request review plan first, then execute review.
   """)

6. RECEIVE review from reviewer

7. EVALUATE review result:
   - If 🔴 Critical issues: Loop back to planner
   - If 🟡 Warnings only: Decide whether to iterate or proceed
   - If 🟢 Approved: Proceed to execution

8. IF LOOPING BACK:
   send_message(planner_session_id, """
   Revise plan based on review feedback:
   
   [Reviewer's findings/issues]
   
   Please address these concerns and provide updated plan.
   """)
   → Go back to step 6

9. TERMINATE planner and reviewer sessions

10. PROCEED TO EXECUTION
```

### Plan Review Loop Decision Matrix

| Review Result | Action |
|---------------|--------|
| 🔴 Critical issues | MUST iterate: pass feedback to planner |
| 🟡 Warnings (1-3) | SHOULD iterate: consider context |
| 🟡 Warnings (4+) | MUST iterate: too many concerns |
| 🟢 Approved | PROCEED to execution |

### When to Skip Plan Review

| Scope | Plan Review |
|-------|-------------|
| **SMALL** | Not needed |
| **MEDIUM** | Optional (leader judgment) |
| **LARGE** | Recommended |
| **HUGE** | Required |

---

## 🔄 FEEDBACK LOOP CONTEXT (CRITICAL)

**When sending requests to Coder based on Reviewer/Tester feedback, ALWAYS add source footer.**

### Format

```
[Request details]

---
📌 [This request is based on REVIEWER feedback]
```

OR

```
[Request details]

---
📌 [This request is based on TESTER feedback]
```

### When To Apply

| Source | Footer |
|--------|--------|
| Reviewer found issues | `📌 [This request is based on REVIEWER feedback]` |
| Tester found bugs | `📌 [This request is based on TESTER feedback]` |

### Why

- Coder knows context (fix vs new work)
- Clear feedback chain traceability
- Better prioritization

---

## Phase 0: Scope Assessment (MANDATORY FIRST STEP)
[... existing content ...]

### 🔴 HUGE Scope (Strategic/Platform Initiative)

**Indicators:**
- Multiple projects involved
- Multiple features across projects
- Strategic business decisions needed
- Significant architecture changes
- Long-term initiative

**How I Handle:**
```
1. Call PLANNER to create multi-phase roadmap
2. REVIEW PLAN (mandatory loop):
   - Spawn Reviewer to review plan
   - Reviewer creates review plan, executes review
   - If 🔴 Critical issues: pass to Planner for revision
   - Loop until 🟢 Approved or 🟡 acceptable
3. Collaborate with user on roadmap and priorities
4. Break into phases and projects
5. For EACH phase:
   - WRITE phase plan to .agents/shared/working/{feature_name}/phaseN-plan.md
   - Spawn NEW Coder session
   - SEND concise context: goal + plan file + key files
   - Spawn NEW Reviewer session (same context)
   - Spawn NEW Tester session (same context)
   - Execute: Coder → Reviewer → Tester per component
   - Track at phase level
   - TERMINATE phase sessions when complete
6. Iterate across phases with FRESH agents + FRESH context files
7. Report to user
8. Done
```

---

## Phase 2: Execute Based on Scope

### 🔴 HUGE Scope Execution — Phase Isolation with Context Handoff

```
PHASE 1:
  Write plan: .agents/shared/working/{feature_name}/phase1-plan.md
  spawn Coder_1, Reviewer_1, Tester_1
  Send: "Phase 1: [goal]. Plan: .agents/shared/working/{feature_name}/phase1-plan.md. Files: [paths]"
  Execute: Coder_1 → Reviewer_1 → Tester_1
  Mark Phase 1 complete
  STOP using Coder_1, Reviewer_1, Tester_1

PHASE 2:
  Write plan: .agents/shared/working/{feature_name}/phase2-plan.md  ← FRESH context file
  spawn Coder_2, Reviewer_2, Tester_2  ← FRESH agents
  Send: "Phase 2: [goal]. Plan: .agents/shared/working/{feature_name}/phase2-plan.md. Files: [paths]"
  Execute: Coder_2 → Reviewer_2 → Tester_2
  Mark Phase 2 complete
  STOP using Coder_2, Reviewer_2, Tester_2

PHASE N:
  Write plan: .agents/shared/working/{feature_name}/phaseN-plan.md
  spawn Coder_N, Reviewer_N, Tester_N
  Send context via file paths
  Execute full flow
  Done
```

**Key Principle: Each phase gets fresh agents + fresh plan file + concise handoff message.**

---
[... remaining existing content ...]
