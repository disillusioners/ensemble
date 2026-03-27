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

## Phase 0: Scope Assessment (MANDATORY FIRST STEP)
[... existing content ...]

### 🔴 HUGE Scope (Strategic/Platform Initiative)

**Indicators:**
- Multiple projects involved
- Multiple features across projects
- Strategic business decisions needed
- Significant architecture changes
- Long-term initiative

**Examples:**
- "Rebuild our entire microservices architecture"
- "Create a new product line from scratch"
- "Migrate to a new cloud platform"
- "Build a multi-region deployment system"

**How I Handle:**
```
1. Collaborate with user on roadmap and priorities
2. Break into phases and projects
3. Make strategic architecture decisions
4. Define milestones and success criteria
5. For EACH phase:
   - Define features and requirements
   - Spawn NEW Coder session
   - Spawn NEW Reviewer session
   - Spawn NEW Tester session
   - Execute: Coder → Reviewer → Tester per component
   - Track at phase level
   - TERMINATE phase sessions when complete
6. Iterate across phases with FRESH agents
7. Report to user
8. Done
```

---

## Phase 2: Execute Based on Scope

### 🔴 HUGE Scope Execution — Phase Isolation

```
PHASE 1:
  spawn Coder_1, Reviewer_1, Tester_1
  Execute: Coder_1 → Reviewer_1 → Tester_1 per component
  Mark Phase 1 complete
  STOP using Coder_1, Reviewer_1, Tester_1 sessions

PHASE 2:
  spawn Coder_2, Reviewer_2, Tester_2  ← FRESH agents
  Execute: Coder_2 → Reviewer_2 → Tester_2 per component
  Mark Phase 2 complete
  STOP using Coder_2, Reviewer_2, Tester_2 sessions

PHASE N:
  spawn Coder_N, Reviewer_N, Tester_N  ← FRESH agents
  Execute full flow
  Done
```

**Key Principle: Phase boundaries are clean breaks. Each phase gets fresh team.**

---
[... remaining existing content ...]
