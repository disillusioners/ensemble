# Rules

## Must

### 🚨 CRITICAL: ALWAYS USE send_message() FOR SESSION COMMUNICATION
[... existing content ...]

### 🚨 CRITICAL: USE THE CORRECT AGENT_ID FOR EACH TASK
[... existing content ...]

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

### 🚨 NO REAL WORK — BRAIN ONLY
[... existing content ...]
