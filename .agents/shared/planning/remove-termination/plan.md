# Plan: Remove Mandatory Instance Termination from Leader Prompt

## Objective
Remove all mandatory `terminate_instance` instructions from the leader agent prompt files. Completed instances naturally sit in "complete" state — termination is unnecessary overhead. Keep `terminate_instance` as an optional emergency tool only.

## Scope Assessment
**SMALL** — Only markdown prompt files change. No code changes. 4 files affected, all in `agents/leader/`.

## Affected Files

| File | Changes |
|------|---------|
| `agents/leader/workflow.md` | 9 termination references to remove/rewrite |
| `agents/leader/tools_note.md` | 1 section to rewrite |
| `agents/leader/rule.md` | 2 termination references to soften |

---

## Tasks

| # | Task | File | Details |
|---|------|------|---------|
| 1 | **Remove termination from Git Flow** | `workflow.md` line 59 | **Before:** `Terminate giter instance` (step 3 of Git Flow). **After:** Remove the line. Giter completes and stays in "complete" state. |
| 2 | **Remove termination from Planning Workflow** | `workflow.md` line 126 | **Before:** `6. Terminate Planner, Reviewer, and Approver instances`. **After:** Remove step 6 entirely. Renumber step 7 (Report approved plan to user) to step 6. |
| 3 | **Rewrite Instance Lifecycle section** | `workflow.md` lines 309–332 | **Before:** Pseudocode shows `Phase N complete → Terminate all instances` after each phase. Line 332 says "terminate when done." **After:** Replace with "leave completed" pattern. Remove termination lines from pseudocode. Change line 332 to say instances are left in complete state. See detailed rewrite below. |
| 4 | **Remove termination from Sequential Example** | `workflow.md` lines 418, 430, 435, 442 | **Before:** Four `Terminate ...` lines in the example walkthrough. **After:** Remove all four terminate lines. Instances complete naturally. |
| 5 | **Rewrite terminate_instance section in tools_note** | `tools_note.md` lines 20–21 | **Before:** `### terminate_instance` with instruction "ONLY terminate after receiving completion report AND certain no more work needed." **After:** Rewrite to position as emergency-only tool. See detailed rewrite below. |
| 6 | **Soften termination in rule.md** | `rule.md` line 12, line 79 | **Before (line 12):** `- **Manage instances** — Spawn, message, terminate agent instances`. **After:** `- **Manage instances** — Spawn, message agent instances`. **Before (line 79):** `— spawn once, reuse for all git operations, terminate when done`. **After:** `— spawn once, reuse for all git operations`. |

---

## Detailed Rewrites

### Task 3: Instance Lifecycle Section Rewrite

**Current** (lines 309–332):
```markdown
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
```

**Proposed:**
```markdown
### Instance Lifecycle — Reuse by Phase

**Instances are reused within a phase and refreshed across phases.** Completed instances remain in "complete" state — no need to terminate them. They can be reused if needed via `send_message()`.

```raw
PHASE 1:
  Spawn: coder-1, reviewer-1, tester-1
  Component A: coder-1 → reviewer-1 → tester-1
  Component B: coder-1 → reviewer-1 → tester-1  (same instances, shared context)
  Component C: coder-1 → reviewer-1 → tester-1  (same instances, shared context)
  Phase 1 complete → instances are done, left in complete state

PHASE 2:
  Spawn: coder-2, reviewer-2, tester-2  (fresh instances, new context)
  Component D: coder-2 → reviewer-2 → tester-2
  ...
  Phase 2 complete → instances are done, left in complete state
```

**Why reuse within phase:** Components in the same phase share architectural decisions, codebase state, and conventions. Reusing instances preserves this accumulated context.

**Why fresh across phases:** New phases may involve different context, different architectural decisions, or different areas of the codebase.

**For SMALL scope (single phase, single component):** Spawn instances as needed. They complete naturally when done.
```

### Task 5: tools_note.md terminate_instance Section Rewrite

**Current** (lines 20–21):
```markdown
### `terminate_instance`
**ONLY terminate after receiving completion report AND certain no more work needed.**
```

**Proposed:**
```markdown
### `terminate_instance` — EMERGENCY ONLY
**Do NOT routinely terminate instances.** Completed instances sit harmlessly in "complete" state and consume no resources. Only use `terminate_instance` if an instance is misbehaving (e.g., runaway, stuck, producing garbage output). Normal workflow completion does NOT require termination.
```

---

## What Does NOT Change

- The `terminate_instance` tool itself remains available (Python code in `daemon/tools/instance.py` unchanged)
- The tool is still listed in the leader's available tools — just repositioned as emergency-only
- Other agent prompts (coder, reviewer, tester, etc.) are unaffected
- No code changes anywhere

## Success Criteria
- [ ] No mandatory `terminate_instance` instructions remain in any leader prompt file
- [ ] `terminate_instance` is mentioned only as an optional/emergency tool
- [ ] All workflow flows, pseudocode examples, and lifecycle descriptions reflect "leave completed" pattern
- [ ] The sequential workflow example has no termination steps
- [ ] `grep -i "terminate" agents/leader/*.md` shows only the emergency-only mention in tools_note.md and the soft reference in rule.md

## Tracking
- Created: 2025-06-15
- Status: draft
