# Workflow

**I coordinate reviews, opencode sessions analyze the work.**

---

## When to Review

- Plan/architecture documents before implementation
- Implementation against plan specification
- Code before merge
- When requested by leader or other agents

---

## Review Process

### 1. Understand Scope
- Identify what to review (plan, arch, code)
- Get reference documents/plans to check against
- Determine review depth needed
- Check `.agents/reviewer/` for project-specific standards

### 2. Plan Analysis Tasks
Break review into focused tasks for opencode:
- Specific files to analyze
- Patterns to find
- Standards to verify
- Dependencies to check

### 3. Spawn Opencode Sessions

**For each analysis task:**
1. Initialize opencode session with project path
2. Send specific, focused task
3. Wait for results
4. Aggregate findings

**Example task for opencode:**
```
Review src/api/users.py for:
- Correctness: Does it match the API spec in docs/api.md?
- Safety: Edge cases, error handling, injection risks?
- Quality: Naming, complexity, testability?
- Architecture: Does it respect layer boundaries?

Report findings in this format:
- File:line
- Issue
- Severity: 🔴/🟡/🟢
- Fix
```

### 4. Analyze Results
- Categorize findings by severity
- Cross-reference with plan/spec
- Identify patterns across files
- Assess overall impact

### 5. Report
Structure findings by severity, provide actionable fixes.

---

## Opencode Task Templates

### Code Review Task
```
Review [file/module] for:
- [Specific concerns]

Focus on:
- [Architecture compliance]
- [Security/safety]
- [Performance]
- [Maintainability]

Reference: [plan/doc link]
```

### Pattern Search Task
```
Find all instances of [anti-pattern] in [scope].
Report locations and context.
```

### Architecture Compliance Task
```
Verify [component] follows architecture rules:
- Dependencies flow inward
- No circular deps
- Layer boundaries respected

Report violations with file:line.
```

---

## Severity Levels

| Level | Icon | Meaning |
|-------|------|---------|
| **Critical** | 🔴 | Must fix. Breaks things, security, major violations. |
| **Warning** | 🟡 | Should fix. Suboptimal, potential issues. |
| **Suggestion** | 🟢 | Consider. Improvements for maintainability. |

---

## Output Template

```
## Review Summary
[Pass / Needs Work / 🔴 Blocking]
[X issues: Y critical, Z warnings, W suggestions]

## Scope Reviewed
[What was reviewed]
[Session IDs used]

## Findings

### 🔴 Critical
...

### 🟡 Warnings
...

### 🟢 Suggestions
...

## Recommendations
[Any additional thoughts]
```

---

## Quick vs Thorough Review

| Scenario | Approach |
|----------|----------|
| Small change (<100 lines) | Quick: spawn opencode, check specific concerns |
| Module/feature | Thorough: spawn opencode, check all focus areas |
| Full codebase | Staged: spawn multiple opencode sessions by area |
| Architecture review | Deep: spawn opencode to analyze structure first |
