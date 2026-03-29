# Workflow

## Core Loop: Plan → Track → Monitor

```
┌─────────────────────────────────────────────────────────┐
│  REQUEST → SCOPE ASSESS → CRAFT PLAN → TRACK → REPORT  │
└─────────────────────────────────────────────────────────┘
```

---

## Session Naming Convention

Use simple, consistent session names:

| Session Name | Purpose | Example |
|--------------|---------|---------|
| `explore` | Understand codebase structure | Explore auth module |
| `draft` | Draft and refine plan content | Draft feature plan |
| `track` | Monitor execution progress | Check task status |

**Note**: Project is set during session initialization, no need to include in session name.

---

## Phase 1: Request Analysis

### Input: Raw Request from Leader

```
[Planning request with context]
```

### When Exploration is Required
- Request involves unfamiliar codebase areas
- Multiple files/modules may be affected
- Architecture patterns need verification
- LARGE/HUGE scope requests
- Technical constraints unclear

### When to Skip Exploration
- Single file, well-understood change
- Request includes clear technical context
- SMALL scope with obvious implementation
- Bug fix with clear root cause

### How Much Exploration is Enough?
- Understand the affected areas: 80% done
- Verify key patterns: 90% done
- Full codebase mastery: NOT required
- If unsure, document what you don't know in plan

---

### Error Handling

#### If Opencode Exploration Fails
1. Try simpler exploration query
2. Fall back to direct file reads for SMALL scope
3. Report to Leader: "Exploration blocked, need manual context"

#### If Exploration Reveals Trivial Scope
1. Report to Leader: "Scope smaller than expected, recommend direct execution"
2. Provide minimal plan (objective + single task)

#### If Opencode Times Out
1. Check partial results
2. Summarize what's known
3. Flag unknowns in plan with "TBD" markers

---

### Steps:

1. **Clarify** (if ambiguous)
   - Identify gaps in requirements
   - Ask targeted questions

2. **Assess Scope**
   | Scope | Indicators | Planning Depth |
   |-------|------------|----------------|
   | SMALL | Single file, 1-2 hours | 1-2 paragraphs |
   | MEDIUM | Few files, half-day | Standard plan |
   | LARGE | Multiple features, 1-2 days | Detailed phases |
   | HUGE | Multi-project, week+ | Multi-phase roadmap |

3. **Explore Codebase** (via opencode)
   - Initialize opencode session: `opencode_skill init-session <project> plan-explore <working_dir>`
   - Explore relevant code areas
   - Identify existing patterns and constraints

---

## Phase 2: Plan Creation

### 🚨 Phase Granularity & Multi-File Output (CRITICAL)

**Every plan for MEDIUM+ scope MUST be split into multiple phase files — one file per phase.**

#### Sizing Rule: Module-Level, Not Component-Level

| Granularity | Example | Verdict |
|---|---|---|
| ✅ Module level | `auth module` (login, register, logout, middleware together) | **RIGHT SIZE** |
| ✅ Feature slice | `user profile CRUD` (model, API, UI together) | **RIGHT SIZE** |
| ✅ Integration layer | `payment integration` (gateway, webhook, reconciliation) | **RIGHT SIZE** |
| ❌ Component level | `login form component only` | **TOO SMALL — merge up** |
| ❌ Single function | `validateJWT function only` | **WAY TOO SMALL — merge up** |
| ❌ Fragmented module | Splitting auth into: `login only`, `register only`, `logout only` | **TOO FRAGMENTED — keep together** |

#### How to Size a Phase

**A well-sized phase:**
- Covers ONE logical module or feature area
- Contains 3-10 tasks
- Can be completed by 1 coder session (not 0.5 sessions, not 3 sessions)
- Is self-contained enough to review and test as a unit
- Groups related components that belong together

**Too small signals (merge with adjacent):**
- Only 1-2 tasks
- Single component/function
- No meaningful review/testing boundary
- Would take <30 min of coder work

**Too big signals (split further):**
- 15+ tasks
- Spans multiple unrelated modules
- Would take multiple coder sessions
- No single coherent objective

#### Required File Output Structure

```
.agents/shared/working/{feature_name}/
├── plan-overview.md          ← Summary: objectives, phase list, dependencies, risks
├── phase1-plan.md            ← Phase 1: self-contained plan
├── phase2-plan.md            ← Phase 2: self-contained plan
├── phaseN-plan.md            ← Phase N: ...
├── decisions.md              ← Architecture decisions (if any)
└── notes.md                  ← Working notes (if any)
```

**For SMALL scope:** Single `plan.md` file is fine.
**For MEDIUM scope:** Minimum 1 `plan-overview.md` + individual phase files if 2+ phases.
**For LARGE/HUGE scope:** Multi-file output is MANDATORY.

### Output: Plan Files (markdown)

#### plan-overview.md Template

```markdown
# Plan Overview: [Feature/Task Name]

## Objective
[1-2 sentence description]

## Scope Assessment
[small/medium/large/huge with justification]

## Context
- Project: [name]
- Working Directory: [path]
- Requested by: Leader

## Phase Index

| Phase | Name | Objective | Dependencies | Est. Time |
|-------|------|-----------|-------------|-----------|
| 1 | [name] | [1-line goal] | None | Xh |
| 2 | [name] | [1-line goal] | Phase 1 | Xh |
| N | [name] | [1-line goal] | Phase N-1 | Xh |

## Risks & Mitigations
| Risk | Impact | Mitigation |
|------|--------|------------|
| [risk] | high/med/low | [mitigation] |

## Success Criteria
- [ ] [criterion 1]
- [ ] [criterion 2]

## Tracking
- Created: [date]
- Last Updated: [date]
- Status: [draft/active/complete]
```

#### phaseN-plan.md Template (per phase)

```markdown
# Phase N: [Phase Name]

## Objective
[What this phase delivers — 1-2 sentences]

## Context
- Previous phase completed: [what Phase N-1 delivered]
- Key decisions: [relevant architectural decisions]

## Tasks
| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 1 | [task] | [brief detail] | path/to/file |
| 2 | [task] | [brief detail] | path/to/file |

## Key Files
- path/to/file1 — [purpose]
- path/to/file2 — [purpose]

## Constraints
- [constraint 1]
- [constraint 2]

## Deliverables
- [ ] [deliverable 1]
- [ ] [deliverable 2]
```

### Single-File Plan Template (SMALL scope only)

```markdown
# Plan: [Feature/Task Name]

## Objective
[1-2 sentence description]

## Scope Assessment
[small with justification]

## Tasks
| # | Task | Key Files |
|---|------|-----------|
| 1 | [task] | path/to/file |

## Success Criteria
- [ ] [criterion 1]

## Tracking
- Created: [date]
- Status: [draft/active/complete]
```

---

## Phase 3: Tracking & Monitoring

### During Execution

1. **Initialize tracking session** (via opencode)
   ```bash
   opencode_skill init-session <project> plan-track <working_dir>
   ```

2. **Track Progress**
   - Monitor task completion
   - Update plan file status
   - Flag blockers

3. **Report to Leader**
   - Send periodic updates via send_message()
   - Include: completed tasks, blockers, next steps

---

## Opencode Session Patterns

### Exploration Session
```bash
opencode_skill init-session <project> plan-explore <working_dir>
opencode_skill --sync <project> plan-explore "Explore the auth module structure"
opencode_skill <project> plan-explore /wait
```

### Drafting Session
```bash
opencode_skill init-session <project> plan-draft <working_dir>
opencode_skill --sync <project> plan-draft "Draft a plan for feature X based on exploration findings"
opencode_skill <project> plan-draft /wait
```

### Tracking Session
```bash
opencode_skill init-session <project> plan-track <working_dir>
opencode_skill --sync <project> plan-track "Check progress on task list and update status"
opencode_skill <project> plan-track /wait
```

---

## Integration with Leader

### When Called
Leader sends: planning request + context + working directory

### Response Format
```
📋 **Plan Created**: [feature name]

**Scope**: [assessment]
**Phases**: [number]
**Est. Time**: [estimate]

Plan overview: .agents/shared/working/{feature_name}/plan-overview.md
Phase files:
  - .agents/shared/working/{feature_name}/phase1-plan.md
  - .agents/shared/working/{feature_name}/phase2-plan.md

[1-2 sentence summary]
```

**For SMALL scope (single file):**
```
📋 **Plan Created**: [feature name]

**Scope**: small
**Phases**: 1
**Est. Time**: [estimate]

Plan file: .agents/shared/working/{feature_name}/plan.md

[1-2 sentence summary]
```

### Tracking Updates
```
📊 **Progress Update**: [feature name]

✅ Completed: [tasks]
⏳ In Progress: [tasks]
🚧 Blockers: [issues]

Next: [next steps]
```
