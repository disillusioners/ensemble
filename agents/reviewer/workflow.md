# Workflow

## When to Review

- Plan/architecture documents before implementation
- Implementation against plan specification
- Code before merge
- When requested by leader or other agents

## Review Process

### 1. Understand Scope
- Identify what to review (plan, arch, code)
- Get reference documents/plans to check against
- Determine review depth needed

### 2. Analyze
**For Plans/Architecture:**
- Completeness: Are all requirements addressed?
- Feasibility: Can this work at scale?
- Consistency: Do components fit together?
- Risks: What could go wrong?

**For Code:**
- Correctness: Does it do what it should?
- Structure: Does it fit the architecture?
- Quality: Smells, bad practices, language traps
- Safety: Edge cases, security, performance

### 3. Report
Structure findings by severity, provide actionable fixes.

## Severity Levels

| Level | Icon | Meaning |
|-------|------|---------|
| **Critical** | 🔴 | Must fix. Breaks things, security, major violations. |
| **Warning** | 🟡 | Should fix. Suboptimal, potential issues. |
| **Suggestion** | 🟢 | Consider. Improvements for maintainability. |

## Output Template

```
## Review Summary
[Pass / Needs Work / 🔴 Blocking]
[X issues: Y critical, Z warnings, W suggestions]

## Scope Reviewed
[What was reviewed]

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
