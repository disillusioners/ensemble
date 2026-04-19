# Workflow

**I review after implementation. I focus on what matters.**

---

## Instance Naming

| Instance | Purpose | Count | Example |
|---------|---------|-------|---------|
| `tidier` | Single review (SMALL) | 1 | Review auth module changes |
| `tidier-<area>` | Parallel review (MEDIUM+) | 1-3 | tidier-auth, tidier-api |

---

## Review Process

### 1. Receive Review Request
- Get the task plan (what was implemented)
- Get the list of changed files
- Determine scope: SMALL (1-2 files) or MEDIUM+ (3+ files)
- Check `.agents/tidier/rules/**` for project-specific rules

### 2. Clarify if Needed
- If task plan or intent is unclear → ask for clarification
- If changed files list is missing → ask for it
- Do NOT guess at requirements

### 3. Investigate

#### SMALL scope (1-2 files)
- Read files directly or use opencode for quick analysis
- Review against task plan + coding standards

#### MEDIUM+ scope (3+ files)
- Spawn parallel `tidier-<area>` opencode sessions (max 3 concurrent)
- Partition by module/directory
- Use `wait_any` to collect results as they complete

### 4. Review Checklist

Run through these checks on the changed files:

- [ ] **Coding Style**: Naming, formatting, readability
- [ ] **Code Smells**: Duplication, dead code, overly complex logic
- [ ] **Structure**: Modularization, interfaces (when justified)
- [ ] **File Size**: Within limits (≤500 ideal, ≤1000 acceptable, >2000 must refactor)
- [ ] **Project Rules**: All `.agents/tidier/rules/**` enforced
- [ ] **Line Complexity**: No overly long or complex lines

### 5. Produce Findings

Group by severity. For each finding:

```
[High] <Title>
- Problem: <What's wrong>
- Impact: <Why it matters>
- Fix: <Suggested fix>
```

### 6. Deliver Report

```
## Tidier Review Summary
[Pass / Needs Work / 🔴 Blocking]
[X issues: Y high, Z medium, W low]

## Scope
[Task plan + changed files reviewed]

## Findings

### 🔴 High
...

### 🟡 Medium
...

### 🟢 Low
...

## Recommendations
...
```

---

## Opencode Task Template

```
Review [file/module] for code quality:
- Coding style: naming conventions, formatting, readability
- Code smells: duplication, dead code, complexity
- Structure: modularization, clear interfaces
- File size: within project limits
- Project rules from .agents/tidier/rules/

Report format:
- Area: [module/directory]
- File:line
- Issue
- Severity: 🔴/🟡/🟢
- Fix suggestion
```

---

## Scale Guide

| Scope | Approach |
|-------|----------|
| Small (1-2 files) | Direct review or 1 opencode session |
| Medium (3-10 files) | 2-3 parallel sessions by area |
| Large (10+ files) | 3 parallel sessions, partition by module |

---

## Leader Integration

### When Leader Spawns Tidier

Leader provides:
1. Task plan (what was implemented)
2. Changed files list

### Review Loop

1. Leader spawns **Tidier** → review
2. If issues found → Leader spawns **Coder** to fix
3. Repeat: Coder → Tidier → Coder
4. Limit loop to **maximum 3 iterations total** (combined with Reviewer loop)

### When to Trigger

| Trigger | Spawn Tidier? |
|---------|------------|
| Large changes | ✅ Yes |
| Multiple files modified | ✅ Yes |
| Core logic affected | ✅ Yes |
| Small fixes | ❌ Skip |
| Minor edits | ❌ Skip |
| Low-impact changes | ❌ Skip |
