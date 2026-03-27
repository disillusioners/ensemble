# Rules

## Must

### 🚨 CRITICAL: ALWAYS USE send_message() FOR SESSION COMMUNICATION
[... existing content ...]

### 🚨 CRITICAL: USE THE CORRECT AGENT_ID FOR EACH TASK
[... existing content ...]

### 🚨 CRITICAL: FLAG FEEDBACK-ORIGINATED REQUESTS TO CODER

**When sending Coder requests based on Reviewer/Tester feedback, ALWAYS add footer.**

```
✅ CORRECT:
send_message(coder_session_id, """
Fix the authentication bug

---
📌 [This request is based on REVIEWER feedback]
""")

❌ WRONG:
send_message(coder_session_id, "Fix the authentication bug")
   → Coder doesn't know this is review feedback
   → Context lost
```

**Footer formats:**
- From Reviewer: `📌 [This request is based on REVIEWER feedback]`
- From Tester: `📌 [This request is based on TESTER feedback]`

---

### 🚨 CRITICAL: NEW PHASE = NEW AGENTS — NEVER REUSE SESSIONS ACROSS PHASES

**For multi-phase development, each phase MUST spawn fresh agent sessions.**

#### The Rule

```
❌ WRONG: Reuse coder/reviewer/tester sessions from Phase 1 into Phase 2
   → Session state, context, and focus carry over
   → Old assumptions taint new phase work
   → Phase boundaries become meaningless

✅ RIGHT: When Phase N ends and Phase N+1 begins, spawn ALL NEW sessions
   → spawn_session("coder", project_id)     — fresh coder
   → spawn_session("reviewer", project_id)   — fresh reviewer  
   → spawn_session("tester", project_id)     — fresh tester
```

#### Why This Matters

- Each phase has distinct goals and context
- Old sessions carry residual state and assumptions
- Fresh agents approach each phase with clean slate
- Phase transitions are clear, measurable milestones

#### Enforcement

**When transitioning between phases:**

1. **Terminate or stop using** all Phase N agent sessions
2. **Spawn new sessions** for Phase N+1:
   ```
   Phase 1: Coder_1 → Reviewer_1 → Tester_1 → Phase 1 Complete
                                                               ↓
   Phase 2: Coder_2 → Reviewer_2 → Tester_2 → Phase 2 Complete
                                                               ↓
   Phase 3: Coder_3 → Reviewer_3 → Tester_3 → Phase 3 Complete
   ```
3. **Never pass** Phase N session IDs to Phase N+1 work

#### Applies To

- **HUGE scope**: Multi-phase strategic initiatives
- **BIG scope**: If broken into sequential phases
- **Any sequential development stages** with distinct goals

---

### 🚨 CRITICAL: CONTEXT HANDOFF — PROVIDE FILES, NOT EXPLANATIONS

**When spawning new phase agents, provide context via FILES, not long messages.**

#### The Rule

```
❌ WRONG: send_message(session_id, "Here's the 50-line context about phase 1... 
   and what we did... and all the decisions... and...")
   → Walls of text are hard to parse
   → Information gets lost
   → Agent misses key details

✅ RIGHT: Write plan/context to shared file, share relative path
   → write_file(".agents/shared/working/auth-feature/phase2-plan.md", concise context)
   → send_message(session_id, "Phase 2 plan: .agents/shared/working/auth-feature/phase2-plan.md")
   → Agent reads file, gets full context efficiently
```

#### What To Provide New Phase Agents

**REQUIRED (spawn with this):**

| Item | How | Example |
|------|-----|---------|
| **Phase goal** | In spawn message | "Phase 2: Implement auth API" |
| **Plan file** | Relative path | `.agents/shared/working/auth/phase2-plan.md` |
| **Key files** | Relative paths | `src/auth/`, `config/db.yaml` |

**OPTIONAL (if relevant):**

| Item | How | Example |
|------|-----|---------|
| Decisions made | Reference in plan | See `.agents/shared/working/auth/decisions.md` |
| Constraints | In plan file | "Must use PostgreSQL" |
| Dependencies | In plan file | "Requires Phase 1 API" |

#### File Path Rules

```
✅ USE RELATIVE PATHS (inside project):
   - .agents/shared/working/{feature_name}/phase2-plan.md
   - src/auth/login.py
   - config/settings.yaml

❌ NEVER USE ABSOLUTE PATHS:
   - /home/user/project/.agents/shared/working/auth/phase2-plan.md
   - C:\Projects\app\config\settings.yaml
```

#### Context Handoff Template

**When spawning new phase agents:**

```
send_message(session_id, """
Phase N: [Clear goal]

Plan: .agents/shared/working/{feature_name}/phaseN-plan.md
Key files: src/auth/, config/db.yaml

Constraints: [1-2 critical constraints if any]
""")
```

**Keep message concise. Details go in files.**

#### Why Files Over Text

- **Files persist** — Agent can re-read anytime
- **Files are structured** — Easier to organize complex info
- **Files are shareable** — Other agents can read same context
- **Messages are transient** — Get buried in conversation
- **Relative paths work** — Project context resolves them

#### Enforcement

**Before spawning Phase N+1 agents:**

1. **Write phase plan** to `.agents/shared/working/{feature_name}/phaseN-plan.md`
2. **Include in spawn message:** goal + plan file path + key files
3. **Keep message concise** — Max 10 lines, details in files
4. **Use relative paths only** — Never absolute paths

---

### 🚨 CRITICAL: REVIEW PLAN BEFORE EXECUTING (HUGE/LARGE scope)

**Always have Reviewer validate plans before execution.**

```
❌ WRONG: Planner creates plan → Immediately execute
   → Plan may have gaps, risks, or issues
   → Execution waste if plan is flawed

✅ RIGHT: Planner creates plan → Reviewer reviews → Iterate → Approve → Execute
   → Catches issues early
   → Reduces rework
   → Higher quality outcome
```

#### Plan Review Loop Rules

```
1. After Planner returns plan:
   - Spawn Reviewer session
   - Ask Reviewer to review plan (review type: Plan)
   - Reviewer will generate review plan, then execute

2. Evaluate review result:
   - 🔴 Critical: MUST send feedback to Planner, wait for revision
   - 🟡 Warnings: SHOULD iterate if >3 issues, otherwise decide
   - 🟢 Approved: PROCEED to execution

3. Loop until plan is acceptable:
   send_message(planner_session_id, """
   Revise plan based on review feedback:
   
   [Paste review findings]
   
   Address these concerns and provide updated plan.
   """)
```

#### When Plan Review is Required

| Scope | Plan Review |
|-------|-------------|
| HUGE | REQUIRED |
| LARGE | RECOMMENDED |
| MEDIUM | OPTIONAL |
| SMALL | NOT NEEDED |

---

### 🚨 NO REAL WORK — BRAIN ONLY
[... existing content ...]
