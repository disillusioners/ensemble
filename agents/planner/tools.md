# Tools

## Primary Tools

### opencode_skill
Orchestrator for code exploration and plan drafting.

**Usage:**
```bash
# Initialize session
opencode_skill init-session <project> <session_name> <working_dir>

# Send command (async)
opencode_skill <project> <session_name> "<message>"

# Sync mode (send + wait)
opencode_skill --sync <project> <session_name> "<message>"

# Wait for result
opencode_skill <project> <session_name> /wait
```

**Session Types:**
- `plan-explore`: Understand codebase structure
- `plan-draft`: Draft and refine plan content
- `plan-track`: Monitor execution progress

### send_message()
Inter-agent communication. Use to report to Leader.

### write_file()
Write plan documents to disk.

**Usage:**
```python
write_file(".agents/shared/working/<feature>/plan.md", plan_content)
```

---

## Session Communication Pattern

```
Leader ──(request)──→ Planner
                        │
                        ├──→ opencode (explore)
                        ├──→ opencode (draft)
                        │
                        └──→ (response) ──→ Leader
```

---

## Tracking Pattern

```
Planner ──(spawn)──→ Tracker Session (opencode)
                           │
                           ├── monitors tasks
                           ├── updates plan.md
                           │
                           └── periodic updates ──→ Planner ──→ Leader
```
