# Workflow

**I plan, opencode works.**

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
For each session in plan order:
```
spawn_session("opencode", project_id)
send_message(session_id, "Review [target] for [concerns]. Report: file:line, issue, severity, fix.")
```

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
