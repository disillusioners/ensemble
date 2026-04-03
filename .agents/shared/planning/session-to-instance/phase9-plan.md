# Phase 9: Agent Definitions & Documentation

## Objective
Update all markdown agent definition files and documentation that reference the "session" concept. These are text files (not code), so the changes are straightforward text replacements. **CRITICAL**: Must distinguish between `opencode_skill` session concept (KEEP) and agent instance concept (RENAME).

## Context
- **Phase 5 completed**: Tool names and categories renamed (spawn_instance, etc.)
- Agent markdown files contain references to session tools and concepts that must be updated
- This phase can run in parallel with Phase 8 (Frontend)

## Tasks

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 1 | **Update agents/leader/rule.md** | Rename: `spawn_session`→`spawn_instance`, `terminate_session`→`terminate_instance`, `list_sessions`→`list_instances`, `get_session_info`→`get_instance_info`, `session_id`→`instance_id`, `SessionManager`→`InstanceManager`, any "session" concept descriptions → "instance". **Preserve** any `opencode_skill` session references. | `agents/leader/rule.md` |
| 2 | **Update agents/leader/workflow.md** | Same rename patterns. Update any workflow descriptions referencing "session". | `agents/leader/workflow.md` |
| 3 | **Update agents/coder/soul.md** | Rename session tool references and concept descriptions. | `agents/coder/soul.md` |
| 4 | **Update agents/coder/rule.md** | Rename session tool references and concept descriptions. | `agents/coder/rule.md` |
| 5 | **Update agents/tester/soul.md** | Rename session tool references. | `agents/tester/soul.md` |
| 6 | **Update agents/tester/rule.md** | Rename session tool references. | `agents/tester/rule.md` |
| 7 | **Update agents/reviewer/soul.md** | Rename session tool references. | `agents/reviewer/soul.md` |
| 8 | **Update agents/planner/workflow.md** | Rename session concept references, especially the `send_message()` communication patterns that reference session IDs. Update `spawn_session`→`spawn_instance`. | `agents/planner/workflow.md` |
| 9 | **Update agents/giter/** | Check all files in agents/giter/ for session references. | `agents/giter/` |
| 10 | **Update agents/_mother/** | Check all files in agents/_mother/ for session references. | `agents/_mother/` |
| 11 | **Update agents/_baby_template/** | Check all template files for session references. | `agents/_baby_template/` |
| 12 | **Update AGENTS.md** | Root documentation file. Rename session concept descriptions, tool references, API examples. | `AGENTS.md` |
| 13 | **Update design docs** | Rename `docs/design/scheduler-session-mode.md` → `scheduler-instance-mode.md` and update content. Check for other doc files with session references. | `docs/design/` |
| 14 | **Update README.md** | Check and update any session references in root README. | `README.md` |

## Key Files
- `agents/leader/rule.md`, `agents/leader/workflow.md`
- `agents/coder/soul.md`, `agents/coder/rule.md`
- `agents/tester/soul.md`, `agents/tester/rule.md`
- `agents/reviewer/soul.md`
- `agents/planner/workflow.md`
- `agents/giter/` (all files)
- `agents/_mother/` (all files)
- `agents/_baby_template/` (all files)
- `AGENTS.md`
- `docs/design/scheduler-session-mode.md` → `scheduler-instance-mode.md`
- `README.md`

## 🚨 CRITICAL: DO NOT RENAME vs RENAME Guide

The coder MUST carefully distinguish between two different "session" concepts in these markdown files. Some files (especially leader, planner, coder) reference BOTH the agent session/instance concept AND the `opencode_skill` session concept.

### ✅ RENAME These (agent instance concept)
These refer to the agent execution instance — a running agent spawned from an agent definition:

```
RENAME: "spawn_session" tool → "spawn_instance"
RENAME: "terminate_session" tool → "terminate_instance"  
RENAME: "list_sessions" tool → "list_instances"
RENAME: "get_session_info" tool → "get_instance_info"
RENAME: "session_id" variable/parameter → "instance_id"
RENAME: "SessionManager" class → "InstanceManager"
RENAME: "agent session" concept → "agent instance"
RENAME: "session hierarchy" → "instance hierarchy"
RENAME: "spawn a session" → "spawn an instance"
RENAME: "session status" → "instance status"
RENAME: "SchedulerSessionMode" → "SchedulerInstanceMode"
RENAME: "session mode" → "instance mode"
RENAME: "spawn_session" in tool usage examples → "spawn_instance"
RENAME: "session_id" in code examples → "instance_id"
RENAME: "/sessions" API routes in examples → "/instances"
RENAME: "current_session_id" → "current_instance_id"
RENAME: "project_get_by_session" → "project_get_by_instance"
RENAME: "creator_session_id" → "creator_instance_id"
```

### 🚫 KEEP These (opencode_skill session concept)
These refer to the opencode_skill orchestrator's session management — a DIFFERENT concept:

```
KEEP: "opencode_skill init-session" (opencode command)
KEEP: "opencode session" (referring to opencode's sessions)
KEEP: "reuse session" (when used in opencode context)
KEEP: "opencode_skill <project> <session_name> ..." command syntax
KEEP: Any text describing how to use opencode_skill CLI
KEEP: "session" in phrases like "session naming convention" when it refers to opencode sessions
KEEP: "plan-explore" / "plan-draft" / "plan-track" session names (these are opencode sessions)
```

### 🔍 How to Tell the Difference
| Context | Concept | Action |
|---------|---------|--------|
| Tool name like `spawn_session` | Agent instance | RENAME |
| API route like `/sessions` | Agent instance | RENAME |
| Variable `session_id` in Python code | Agent instance | RENAME |
| Class `SessionManager` | Agent instance | RENAME |
| `opencode_skill init-session` | OpenCode session | KEEP |
| `opencode_skill <project> <session> <message>` | OpenCode session | KEEP |
| "Initialize a session" referring to opencode | OpenCode session | KEEP |
| Describing opencode session naming conventions | OpenCode session | KEEP |

## Rename Patterns

### Tool References in Markdown
| Old | New |
|-----|-----|
| `spawn_session` | `spawn_instance` |
| `terminate_session` | `terminate_instance` |
| `list_sessions` | `list_instances` |
| `get_session_info` | `get_instance_info` |
| `session_id` | `instance_id` |
| `SessionManager` | `InstanceManager` |

### Concept References
| Old | New |
|-----|-----|
| "agent session" | "agent instance" |
| "session hierarchy" | "instance hierarchy" |
| "spawn a session" | "spawn an instance" |
| "session concept" | "instance concept" |
| "session status" | "instance status" |
| `SchedulerSessionMode` | `SchedulerInstanceMode` |
| "session mode" | "instance mode" |

## Constraints
- These are documentation/markdown files — no compilation needed
- Be careful with `send_message` — the function name stays, but the concept of "sending to a session" becomes "sending to an instance"
- The `_baby_template` is used as a template for new agents — must be accurate
- Don't change agent personality or behavior, only the naming
- File `docs/design/scheduler-session-mode.md` should be `git mv` renamed
- **READ EACH OCCURRENCE IN CONTEXT** before deciding to rename — don't blind-replace

## Verification
```bash
# 1. Find remaining agent-instance session references in agents/
grep -rn "session_id\|spawn_session\|terminate_session\|list_sessions\|get_session_info\|SessionManager\|creator_session_id\|project_get_by_session" agents/ docs/ AGENTS.md README.md

# 2. Verify key tool names updated
grep -rn "spawn_instance\|terminate_instance\|list_instances\|get_instance_info\|instance_id" agents/

# 3. Check doc file renamed
ls docs/design/scheduler-instance-mode.md

# 4. Verify opencode_skill references are PRESERVED
grep -rn "opencode_skill" agents/ docs/ AGENTS.md README.md
# These should still exist and be unchanged
```

## Deliverables
- [ ] All agent markdown files updated with new terminology
- [ ] `AGENTS.md` updated
- [ ] `README.md` updated
- [ ] Design doc file renamed: `scheduler-session-mode.md` → `scheduler-instance-mode.md`
- [ ] Grep shows 0 old session tool names in agents/ and docs/
- [ ] `opencode_skill` session references are PRESERVED unchanged
