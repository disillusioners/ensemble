# Phase 6: Frontend, Docs, Scripts & `.agents/` (Rev. 2)

> **Revision 2**: Fixes W2 (docs massively undercounted — 25 files, not 5), W3 (frontend 9 files, not 13), W5 (`.agents/` 73 files — explicit scoping).

## Objective
Update the Angular frontend (9 files), project documentation (4 root + 25 `docs/` files), utility scripts (3 files), and active `.agents/` files to reference "developer" instead of "coder".

## Coupling
- **Depends on**: Phase 1 (agent renamed)
- **Coupling type**: loose
- **Shared files with other phases**: None
- **Why this coupling**: Can run in parallel with Phases 2–5.

---

## Part A: Frontend (9 files — W3 Corrected)

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 1 | Update runtime color maps | Add `'developer': '#10a7f7'` to agent color maps (keep `'coder'` entry for backward compat with cached data) | `chat-interface.component.ts`, `message-input.component.ts` |
| 2 | Update default agent_id | Change `input('coder')` to `input('developer')` | `message-input.component.ts` |
| 3 | Update test helper | Change `agent_id: 'coder'` to `agent_id: 'developer'` | `testing/job-test-helpers.ts` |
| 4 | Update all spec files | Batch replace `'coder'` → `'developer'` in test data and assertions | 6 `.spec.ts` files |

### Runtime Files (3 files — CRITICAL)

**frontend/src/app/components/chat-interface/chat-interface.component.ts**
```typescript
// Line 31
'coder': '#10a7f7',
// Change to (additive — keep old for cached data):
'developer': '#10a7f7',
'coder': '#10a7f7',  // backward compat for cached responses
```

**frontend/src/app/components/message-input/message-input.component.ts**
```typescript
// Line 30
readonly agentColor = input('coder');
// Change to:
readonly agentColor = input('developer');

// Line 71 — same additive pattern as chat-interface
'developer': '#10a7f7',
'coder': '#10a7f7',  // backward compat
```

**frontend/src/app/testing/job-test-helpers.ts**
```typescript
// Line 28
agent_id: 'coder',
// Change to:
agent_id: 'developer',
```

### Spec Files (6 files)
```
frontend/src/app/models/job.model.spec.ts                       (6 refs)
frontend/src/app/components/message-input/message-input.component.spec.ts  (2 refs)
frontend/src/app/pages/jobs/jobs.component.spec.ts              (16 refs)
frontend/src/app/services/job.service.spec.ts                    (refs)
frontend/src/app/services/notification.service.spec.ts          (1 ref)
frontend/src/app/services/sse.service.spec.ts                    (refs)
```

---

## Part B: Documentation (W2 — Corrected to 29 files)

The plan originally listed only 4 root docs. There are **25 additional files** under `docs/` that reference "coder". **All must be updated.**

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 5 | Update root docs | Update README.md, ROADMAP.md, PLAN.md, base-plan.md (4 files) | `*.md` |
| 6 | **Update `docs/` — high priority** | User-facing and architectural docs | 7 files (see below) |
| 7 | **Update `docs/` — standard priority** | Feature, plan, and architecture docs | 7 files (see below) |
| 8 | **Update `docs/bugs/`** | Bug reports and reviews (informational) | 9 files (see below) |
| 9 | **Update `docs/migration/`** | Migration guide | 1 file |

### High-Priority Docs (user-facing — update first)

| File | Why Important |
|------|---------------|
| `docs/api-reference.md` | API documentation — users reference this |
| `docs/agents.md` | Agent list/descriptions |
| `docs/agent-architecture.md` | Architecture overview |
| `docs/usage.md` | Usage guide |
| `docs/architecture.md` | System architecture |
| `docs/job-queue.md` | Job queue docs |
| `docs/features/job-queue.md` | Feature docs |

### Standard-Priority Docs

| File | Content |
|------|---------|
| `docs/plans/decouple-execution-plan.md` | Plan doc |
| `docs/plans/kb-auto-load-experience.md` | Plan doc |
| `docs/plans/unified-dispatcher.md` | Plan doc |
| `docs/features/IMPLEMENTATION.md` | Implementation notes |
| `docs/pluggable-sources-architecture.md` | Architecture doc |
| `docs/architecture/message-queue-problems.md` | Architecture analysis |
| `docs/architecture/job-task-pause-resume.md` | Architecture analysis |

### Bug Report Docs (9 files — informational)

```
docs/bugs/child-completion-report-lost-under-concurrent-task-processing.md
docs/bugs/child-completion-report-lost-under-concurrent-task-processing.review.md
docs/bugs/child-completion-report-lost-under-concurrent-task-processing.codereview.r2.md
docs/bugs/child-completion-report-lost-cross-dispatcher-jobqueue-vs-workerpool.md
docs/bugs/root-instance-premature-completion-on-pending-message.md
docs/bugs/job-completed-when-parent-agent-not-done.log
docs/bugs/parent-instance-premature-completion-on-fast-child.md
docs/bugs/external-opencode-wait-latency.md
docs/bugs/bash-tool-hangs-on-backgrounded-subprocess.md
docs/bugs/unresolved/symmetric-cross-system-race-messagejobhandler-ignores-running-tasks.md
```

> **Note**: Bug reports are historical records. Updating "coder" → "developer" in them is optional but recommended for consistency. Prioritize the high-priority docs first.

### Root Docs (4 files — unchanged from Rev. 1)

#### README.md
| Line | Current | New |
|------|---------|-----|
| 120 | `└── coder/` | `└── developer/` |
| 139 | `\| \`coder\` \| Writes and modifies code \|` | `\| \`developer\` \| Writes and modifies code \|` |

#### ROADMAP.md
| Line | Current | New |
|------|---------|-----|
| 36 | `│ (coder) │` | `│ (developer) │` |
| 55 | `agents/coder/` | `agents/developer/` |
| 186 | `agent_dir: str, # e.g., "agents/coder"` | `agent_dir: str, # e.g., "agents/developer"` |
| 294 | `├── coder/` | `├── developer/` |

#### PLAN.md
| Line | Current | New |
|------|---------|-----|
| 216 | `Leader spawns coder session` | `Leader spawns developer session` |
| 217 | `Leader sends task to coder` | `Leader sends task to developer` |
| 218 | `Coder responds` | `Developer responds` |

#### base-plan.md
| Line | Current | New |
|------|---------|-----|
| 27 | `├── coder/` | `├── developer/` |
| 95 | `"agent": "agents/coder"` | `"agent": "agents/developer"` |

---

## Part C: Scripts (3 files — unchanged)

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 10 | Update migrate_agent_id.py | Update docstring examples (2 refs) | `scripts/migrate_agent_id.py` |
| 11 | Update migrate_memory_to_rag.py | Update usage example (1 ref) | `scripts/migrate_memory_to_rag.py` |
| 12 | Update e2e_pause_resume_test.py | Update test prompts and assertions (6 refs) | `scripts/e2e_pause_resume_test.py` |

---

## Part D: `.agents/` Directory (W5 — 73 files, explicit scoping)

73 files in `.agents/` reference "coder". These split into **active** and **historical**:

### Active Files — MUST UPDATE (6 files)

These files are loaded at runtime by agents:

| File | Purpose | Action |
|------|---------|--------|
| `.agents/coder/memories/2026-04-29-explore-sequential-rule.md` | Developer agent memory | Rename dir `.agents/coder/` → `.agents/developer/`, update internal refs |
| `.agents/coder/memories/` (2nd file) | Developer agent memory | Same as above |
| `.agents/tester/rules/ensure.md` | Tester agent rule referencing coder | Update "coder" → "developer" |
| `.agents/tester/LESSONS/e2e-workflow-tests-created.md` | Tester lesson referencing coder | Update "coder" → "developer" |
| `.agents/approver/remove-mandatory-instance-termination-tracking.md` | Approver note | Update "coder" → "developer" |

### Historical Files — INTENTIONALLY LEFT UNCHANGED (67 files)

These are historical RESULTS, LESSONS, and planning docs. They are **append-only records** that describe what happened at a point in time. Changing "coder" → "developer" in them would misrepresent history.

| Category | Count | Examples |
|----------|-------|---------|
| `.agents/tester/RESULTS/` | 30 files | Test results from past runs |
| `.agents/tester/LESSONS/` | 6 files | Lessons learned (excluding the 1 active one above) |
| `.agents/shared/planning/` | 28 files | Historical feature plans (devops-agent, rag-knowledge-toolset, etc.) |

> **Documentation note**: Add a line to `.agents/shared/context.md` documenting the rename:
> ```
> ## Agent Rename: coder → developer (2026-06-25)
> The "coder" agent was renamed to "developer". Historical docs in
> .agents/ and docs/bugs/ may still reference "coder" — these are
> intentional historical records.
> ```

---

## Constraints
- Frontend color map changes should be additive (keep `'coder'` entry for backward compat)
- Documentation changes are cosmetic but important for user-facing accuracy
- **Do NOT** modify historical RESULTS/LESSONS files in `.agents/` — they are append-only records
- Active `.agents/` files (rules, memories) MUST be updated
- Script changes only affect docstrings/examples, not logic

## Deliverables
- [ ] Frontend runtime code uses `'developer'` in color maps and default agent_id (3 files)
- [ ] All frontend spec files use `'developer'` (6 files)
- [ ] `grep -rn "coder" frontend/src/ --include="*.ts" --include="*.tsx"` returns 0 matches (excluding backward-compat color map entries)
- [ ] Frontend tests pass: `cd frontend && npm test`
- [ ] Root docs updated (4 files): README.md, ROADMAP.md, PLAN.md, base-plan.md
- [ ] High-priority `docs/` files updated (7 files): api-reference, agents, agent-architecture, usage, architecture, job-queue, features/job-queue
- [ ] Standard-priority `docs/` files updated (7 files)
- [ ] Bug report docs updated (9 files — optional but recommended)
- [ ] Scripts docstrings/examples updated (3 files)
- [ ] Active `.agents/` files updated (6 files)
- [ ] `.agents/shared/context.md` updated with rename note
- [ ] Historical `.agents/` files (67 files) intentionally left unchanged — documented
