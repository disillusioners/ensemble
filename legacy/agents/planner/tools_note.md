# Tool Usage Notes

## Primary Tools

### `opencode-skill`
Orchestrator for code exploration and plan drafting.

**Initialize a session:**
```python
external_opencode_init_session(
    project="<project>",
    session_name="<session_name>",   # e.g. "plan-explore", "plan-draft", "plan-track"
    working_dir="<absolute_path>",
)
```

**Send a prompt (fire-and-forget):**
```python
external_opencode_send_message(
    project="<project>",
    session_name="<session_name>",
    message="<prompt>",
)
```

**Wait for one session to complete (fixed 11-min timeout):**
```python
external_opencode_wait_for_result(
    project="<project>",
    session_name="<session_name>",
)
```

**Wait for any of several sessions to complete:**
```python
external_opencode_wait_any(
    sessions=[
        {"project": "<project>", "session_name": "explore-auth"},
        {"project": "<project>", "session_name": "explore-api"},
    ],
)
```

**Get non-blocking status / latest response:**
```python
external_opencode_get_status(project="<project>", session_name="<session_name>")
```

**Resume a session that hit the 10-min limit:**
```python
external_opencode_resume_session(project="<project>", session_name="<session_name>")
```

**Session Types:**
- `plan-explore`: Understand codebase structure
- `plan-draft`: Draft and refine plan content
- `plan-track`: Monitor execution progress

### `write_file()`
Write plan documents to disk.

**Usage:**
```python
write_file(".agents/shared/planning/<feature>/plan-overview.md", plan_content)
```

---

## Session Workflow

```
                    ├──→ opencode (explore)
                    ├──→ opencode (draft)
                    └──→ Complete plan output
```

---

## Tracking Pattern

```
Planner ──(spawn)──→ Tracker Session (opencode)
                           │
                           ├── monitors tasks
                           ├── updates plan.md
                           └── periodic updates ──→ Plan file
```
