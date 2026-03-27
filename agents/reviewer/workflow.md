# Workflow

**I plan, opencode works.**

---

## Review Process

### 1. Receive Review Request
- Identify scope (plan, code, architecture)
- Get reference documents/specs

### 2. Plan Tasks
Break into focused opencode tasks:
- Files/modules to analyze
- Patterns to find
- Standards to verify

### 3. Spawn Opencode Sessions
For each task:
```
spawn_session("opencode", project_id)
send_message(session_id, "Review [target] for [concerns]. Report: file:line, issue, severity, fix.")
```

### 4. Collect Results
- Wait for session completion
- Gather all findings
- Terminate sessions

### 5. Aggregate & Report
- Categorize by severity
- Combine into structured report
- Deliver to requester

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
