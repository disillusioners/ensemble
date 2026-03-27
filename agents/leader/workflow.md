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
1. Collaborate with user on roadmap and priorities
2. Break into phases and projects
3. Make strategic architecture decisions
4. Define milestones and success criteria
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
