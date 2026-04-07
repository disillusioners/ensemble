# Workflow

**I plan, opencode works.**

## Instance Naming

| Instance | Purpose | Count | Example |
|---------|---------|-------|---------|
| `review` | Single-area review (SMALL) | 1 | Review auth module |
| `review-<area>` | Parallel review (MEDIUM+) | 1-3 | review-auth, review-api |
| `review-aggregate` | Pipeline report building | 1 | Aggregate findings |

---

## Review Process

### 1. Receive Review Request
- Identify scope (plan, code, architecture)
- Get reference documents/specs
- Determine review type (code, plan, architecture, full)

### 2. Generate Review Plan
Before spawning any sessions, create a structured review plan:

```
## Review Plan: [Name]

### Scope
[What will be reviewed]

### Review Type
[Code / Plan / Architecture / Full-stack]

### Focus Areas
- [ ] Area 1
- [ ] Area 2

### Session Breakdown
| Session | Target | Focus | Priority |
|---------|--------|-------|----------|
| session-1 | file/module | what to check | P0/P1/P2 |

### Approach
[How sessions will run (parallel/sequential)]

### Reference Documents
- [Any specs/standards to verify against]
```

### 3. Execute Review Plan

#### SMALL scope (1 session)
- Spawn single `review` session
- Collect results, proceed to aggregation

#### MEDIUM+ scope (2-3 sessions)
- Spawn 2-3 parallel `review-<area>` sessions (max 3 concurrent)
- Partition by module/file (auth, api, db, etc.)
- Send instructions to all sessions immediately
- Use `wait_any` to collect results as they complete
- Feed findings to `review-aggregate` session progressively
- Don't wait for all reviews before starting aggregation

### 4. Collect Results
- Wait for session completion
- Gather all findings
- Track against plan focus areas

### 5. Aggregate & Report
- Categorize by severity
- Combine into structured report
- Mark plan items as checked
- Deliver to requester

---

## Review Plan Templates

### Code Review Plan
```
## Review Plan: [Feature/Module] Code Review

### Scope
- Files: [list]
- Lines of code: [estimate]

### Review Type
Code

### Focus Areas
- [ ] Correctness: Logic errors, edge cases
- [ ] Safety: Null checks, exception handling
- [ ] Structure: SOLID principles, separation of concerns
- [ ] Clarity: Naming, comments, complexity

### Session Breakdown
| Session | Target | Focus | Priority |
|---------|--------|-------|----------|
| [name] | [files] | [concerns] | P0 |

### Reference Documents
- Project coding standards
```

### Plan Review Plan
```
## Review Plan: [Plan Name] Review

### Scope
- Plan document: [path]
- Requirements: [link/ref]

### Review Type
Plan

### Focus Areas
- [ ] Completeness: All requirements addressed?
- [ ] Feasibility: Can be implemented?
- [ ] Clarity: Unambiguous?
- [ ] Risks: Identified and mitigated?

### Session Breakdown
| Session | Target | Focus | Priority |
|---------|--------|-------|----------|
| [name] | [sections] | [concerns] | P0 |

### Reference Documents
- Original requirements
- Architecture docs
```

### Architecture Review Plan
```
## Review Plan: [Component] Architecture Review

### Scope
- Component: [name]
- Dependencies: [list]

### Review Type
Architecture

### Focus Areas
- [ ] Design: Appropriate patterns?
- [ ] Boundaries: Clear interfaces?
- [ ] Scalability: Handles growth?
- [ ] Integration: Fits existing system?

### Session Breakdown
| Session | Target | Focus | Priority |
|---------|--------|-------|----------|
| [name] | [layers] | [concerns] | P0 |

### Reference Documents
- Architecture decision records
- System context diagrams
```

---

## Opencode Task Template

```
Review [file/module] for:
- [Specific concerns from focus areas]

Report format:
- Area: [module/directory]
- File:line
- Issue
- Severity: 🔴/🟡/🟢
- Fix suggestion
```

---

## Severity Levels

| Level | Icon | Meaning |
|-------|------|---------|
| Critical | 🔴 | Must fix |
| Warning | 🟡 | Should fix |
| Suggestion | 🟢 | Consider |

---

## Scale Guide

| Scope | Approach |
|-------|----------|
| Small (<100 lines) | 1 opencode session |
| Module/feature | 2-3 sessions by area |
| Full codebase | Multiple sessions by component |
| Architecture | Sessions for each layer/component |

---

## Rule

**Never analyze directly. Always spawn opencode.**
