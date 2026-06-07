# Workflow

## Core Loop: Plan → Track → Monitor

```
┌─────────────────────────────────────────────────────────┐
│  REQUEST → SCOPE ASSESS → CRAFT PLAN → TRACK → OUTPUT  │
└─────────────────────────────────────────────────────────┘
```

---

## Instance Naming Convention

| Instance | Purpose | Count | Example |
|---------|---------|-------|---------|
| `explore` | Single-area exploration (SMALL) | 1 | Explore auth module |
| `explore-<area>` | Parallel exploration (MEDIUM+) | 1-3 | explore-auth, explore-api |
| `draft` | Draft and refine plan content | 1 | Draft feature plan |
| `track` | Monitor execution progress | 1 | Check task status |

---

## Phase 1: Request Analysis

### Input: Planning Request

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
3. Note in plan: "Exploration blocked, provide manual context"

#### If Exploration Reveals Trivial Scope
1. Note in plan: "Scope smaller than expected, recommend direct execution"
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
   - For SMALL scope: Initialize single `explore` session
   - For MEDIUM+ scope: Initialize 2-3 parallel `explore-<area>` sessions (max 3)
   - Partition by module/directory (e.g., auth, api, db)
   - Use `wait_any` to collect results as they complete
   - Merge findings before drafting

### Exploration Strategy

| Scope | Strategy |
|-------|----------|
| SMALL (1 area) | Single `explore` session |
| MEDIUM+ (2-3 areas) | Parallel `explore-<area>` sessions |

### Pipeline Drafting (LARGE/HUGE scope)
1. Spawn explore sessions (parallel)
2. Immediately spawn `draft` session with skeleton
3. Feed findings to draft as each explore completes
4. Finalize plan after all explores done

---

## Phase 2: Plan Creation

### With Parallel Exploration
1. Merge findings from all explore sessions
2. Identify module boundaries and phase structure
3. Create plan-overview.md skeleton
4. Spawn `draft` session with complete findings

### With Pipeline Drafting (LARGE/HUGE)
1. Draft session started during exploration phase
2. Incremental findings fed as explores complete
3. Finalize all phase files after all explores done

---

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
- Can be completed by 1 coder instance (not 0.5 instances, not 3 instances)
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
- Would take multiple coder instances
- No single coherent objective

#### Required File Output Structure

```
.agents/shared/planning/{feature_name}/
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

## Phase Index

| Phase | Name | Objective | Dependencies | Coupling | Est. Time |
|-------|------|-----------|-------------|----------|-----------|
| 1 | [name] | [1-line goal] | None | — | Xh |
| 2 | [name] | [1-line goal] | Phase 1 | tight / loose / independent | Xh |
| N | [name] | [1-line goal] | Phase X | tight / loose / independent | Xh |

### Coupling Assessment

For each pair of consecutive phases, assess their coupling:

| Coupling | Meaning | Scheduling |
|----------|---------|------------|
| **independent** | Different files/modules, no shared APIs | Can run in parallel |
| **loose** | Depends on planned interfaces only, not implementation | Can pipeline (overlap review + next coding) |
| **tight** | Depends on actual code from prior phase (same files, models, APIs) | Must run sequential — wait for review approval |

**How to assess:**
- Do these phases touch the same files? → tight
- Does Phase N+1 import/call code that Phase N creates? → tight
- Do they touch different directories/modules with no import relationship? → independent
- Does Phase N+1 only need the interface/contract that Phase N defines? → loose

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

## Coupling
- **Depends on**: [Phase X | None]
- **Coupling type**: tight / loose / independent
- **Shared files with other phases**: [list files, or "none"]
- **Shared APIs/interfaces**: [list, or "none"]
- **Why this coupling**: [1 sentence justification]

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

1. **Initialize tracking instance** (via opencode)
   ```python
   external_opencode_init_session(
       project="<project>",
       session_name="plan-track",
       working_dir="<working_dir>",
   )
   ```

2. **Track Progress**
   - Monitor task completion
   - Update plan file status
   - Flag blockers

3. **Update Plan File**
   - Record completed tasks
   - Note blockers
   - Update next steps

---

## Opencode Instance Patterns

### Exploration Instance
```python
external_opencode_init_session(project="<project>", session_name="plan-explore", working_dir="<working_dir>")
external_opencode_send_message(project="<project>", session_name="plan-explore",
    message="Explore the auth module structure",
    related_context_keywords=["auth", "module structure"])
external_opencode_wait_for_result(project="<project>", session_name="plan-explore", timeout=600)
```

### Drafting Instance
```python
external_opencode_init_session(project="<project>", session_name="plan-draft", working_dir="<working_dir>")
external_opencode_send_message(project="<project>", session_name="plan-draft",
    message="Draft a plan for feature X based on exploration findings",
    related_context_keywords=["feature X", "exploration findings", "plan"])
external_opencode_wait_for_result(project="<project>", session_name="plan-draft", timeout=600)
```

### Tracking Instance
```python
external_opencode_init_session(project="<project>", session_name="plan-track", working_dir="<working_dir>")
external_opencode_send_message(project="<project>", session_name="plan-track",
    message="Check progress on task list and update status",
    related_context_keywords=["task list", "progress", "status"])
external_opencode_wait_for_result(project="<project>", session_name="plan-track", timeout=600)
```

---

## Response Output

### Plan Complete Output (MEDIUM+ scope)
```
📋 **Plan Created**: [feature name]

**Scope**: [assessment]
**Phases**: [number]
**Est. Time**: [estimate]

**Phase Scheduling**:
| Phase | Coupling | Can Parallel With |
|-------|----------|-------------------|
| 1 | — (root) | [Phase 2 if independent] |
| 2 | tight/loose/independent with Phase 1 | [Phase 3 if independent] |
| N | tight/loose/independent with Phase X | [none or Phase Y] |

Plan overview: .agents/shared/planning/{feature_name}/plan-overview.md
Phase files:
  - .agents/shared/planning/{feature_name}/phase1-plan.md
  - .agents/shared/planning/{feature_name}/phase2-plan.md

[1-2 sentence summary]
```

**For SMALL scope (single file):**
```
📋 **Plan Created**: [feature name]

**Scope**: small
**Phases**: 1
**Est. Time**: [estimate]

Plan file: .agents/shared/planning/{feature_name}/plan.md

[1-2 sentence summary]
```

### Tracking Update Output
```
📊 **Progress Update**: [feature name]

✅ Completed: [tasks]
⏳ In Progress: [tasks]
🚧 Blockers: [issues]

Next: [next steps]
```
