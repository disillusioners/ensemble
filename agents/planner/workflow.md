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

### Output: Plan Document (markdown file)

```markdown
# Plan: [Feature/Task Name]

## Objective
[1-2 sentence description]

## Scope Assessment
[small/medium/large/huge with justification]

## Context
- Project: [name]
- Working Directory: [path]
- Requested by: Leader

## Task Breakdown

### Phase 1: [Name]
| # | Task | Agent | Est. Time | Risk |
|---|------|-------|-----------|------|
| 1 | [task] | coder | Xh | low/med/high |
| 2 | [task] | reviewer | Xh | low/med/high |

### Phase 2: [Name] (if applicable)
...

## Dependencies
- [dependency 1]
- [dependency 2]

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

Plan file: [path to .md]

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
