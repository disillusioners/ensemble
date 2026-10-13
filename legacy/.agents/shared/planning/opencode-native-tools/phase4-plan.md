# Phase 4: Skill Prompt Rewrite

## Objective
Rewrite `agents/_prompt_system/innate-skills/opencode/skill.md` to document the 8 native Python tools instead of the Go CLI binary. Backup the current version first.

## Coupling
- **Depends on**: Phase 2 (tool signatures and behavior)
- **Coupling type**: loose
- **Shared files with other phases**: `agents/_prompt_system/innate-skills/opencode/skill.md`
- **Why this coupling**: Documentation only — only needs tool function signatures and outputs.

## Context
- Current `skill.md`: 231 lines, documents Go binary workflow
- New prompt: documents 8 native Python tools (Phase 2)
- **W20**: Remove the "Instance Naming Convention" section (it lists `explore`, `draft`, `track` which are planner-instance concepts, not opencode concepts — they were copy-pasted wrong)

## Tasks

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 1 | Backup current skill.md | Copy to `skill.md.bak` | `agents/_prompt_system/innate-skills/opencode/skill.md.bak` (NEW) |
| 2 | Write new skill.md | Complete rewrite with 8 tools | `agents/_prompt_system/innate-skills/opencode/skill.md` (MODIFY) |
| 3 | Verify no Go binary references | grep for `opencode_skill` binary, port 44111, `@file`, `--sync`/`--quiet` | Manual check |
| 4 | Verify all 8 tools documented | Each tool has a usage example | Manual check |
| 5 | Test in agent context | Start daemon, verify category appears in `tool_help()` | Manual smoke test |

## New `skill.md` Structure

```markdown
# OpenCode_Skill

Controls **Orchestrator** (oh-my-opencode-slim) via **native Python tools**.
The tools call the OpenCode HTTP API directly (no external binary required).

## Prerequisites

1. **OpenCode running**: The OpenCode service must be running at `http://127.0.0.1:4095`
2. **Configuration**: Settings at `{data_dir}/opencode_skill.json`
   - Defaults work out-of-box: `opencode`/`opencode` auth, `http://127.0.0.1:4095`

## Context Before Delegation

> **Before sending any task to OpenCode, gather and share relevant context first.**

[Keep this section verbatim — same pattern as knowledge tools]

## Tool Inventory

| Tool | Blocking? | Description |
|------|-----------|-------------|
| `external_opencode_init_session` | No | Create/replace a named session |
| `external_opencode_send_message` | No | Send prompt (fire-and-forget) |
| `external_opencode_get_status` | No | Check state + response + questions |
| `external_opencode_wait_for_result` | Yes | Block until session completes (30s poll, max 10min) |
| `external_opencode_wait_any` | Yes | Block until ANY session completes |
| `external_opencode_answer_question` | No | Answer interactive questions |
| `external_opencode_resume_session` | No | Resume a timed-out session |
| `external_opencode_abort_session` | No | Abort and reset to IDLE |

## Usage

### 1. Initialize a Session

```python
external_opencode_init_session(
    project="myapp",
    session_name="feature-login",
    working_dir="/Users/me/projects/my-app"
)
```

### 2. Send a Message

```python
external_opencode_send_message(
    project="myapp",
    session_name="feature-login",
    message="Explore the auth module structure"
)
```

### 3. Check Status (non-blocking)

```python
external_opencode_get_status(
    project="myapp",
    session_name="feature-login"
)
```

### 4. Wait for Result (blocking)

```python
external_opencode_wait_for_result(
    project="myapp",
    session_name="feature-login",
    timeout=600
)
```

### 5. Answer Questions

```python
external_opencode_answer_question(
    project="myapp",
    session_name="feature-login",
    request_id="req_abc123",
    answers=["ESLint"]
)
```

### 6. Resume After Timeout

```python
external_opencode_resume_session(
    project="myapp",
    session_name="feature-login"
)
```

### 7. Abort Session

```python
external_opencode_abort_session(
    project="myapp",
    session_name="feature-login"
)
```

## Unified Workflow

```
1. external_opencode_init_session(project, session_name, working_dir)
2. external_opencode_send_message(project, session_name, message)
3. external_opencode_wait_for_result(project, session_name)  # or get_status
4. If WAITING_FOR_INPUT → external_opencode_answer_question(...)
5. If timeout → external_opencode_resume_session(...)
6. external_opencode_get_status() to check progress
```

## Parallel Sessions Workflow

Run up to **3 sessions in parallel** for independent tasks.

```python
# Initialize 3 sessions
external_opencode_init_session("myapp", "task-1", "/path")
external_opencode_init_session("myapp", "task-2", "/path")
external_opencode_init_session("myapp", "task-3", "/path")

# Send messages to all 3
external_opencode_send_message("myapp", "task-1", "Task 1 message")
external_opencode_send_message("myapp", "task-2", "Task 2 message")
external_opencode_send_message("myapp", "task-3", "Task 3 message")

# Wait for any to complete
external_opencode_wait_any(
    sessions=[
        {"project": "myapp", "session_name": "task-1"},
        {"project": "myapp", "session_name": "task-2"},
        {"project": "myapp", "session_name": "task-3"},
    ],
    timeout=600
)
```

## Special Prompts (Bypass BUSY Check)

These can be sent even when the session is BUSY:
- `start-work` — also locks agent to `atlas` (used for delegation)
- `continue` — routes through RESUME (hardcoded prompt)
- `retry` — routes through RESUME
- `abort` — bypasses BUSY (use `external_opencode_abort_session` for the real action)

## Error Handling

**Session did not complete within timeout**: Call `external_opencode_resume_session()`.

**Session is busy error**: Wait with `external_opencode_wait_for_result()` or abort with `external_opencode_abort_session()`.

**Interactive questions**: Call `external_opencode_get_status()` to see questions, then `external_opencode_answer_question()` to respond.

## Configuration

`{data_dir}/opencode_skill.json`:
```json
{
    "default_model": "litellm/coding",
    "api_user": "opencode",
    "api_key": "opencode",
    "opencode_url": "http://127.0.0.1:4095"
}
```

The `data_dir` is `./data/` in production and `./data_dev/` in development.
```

## Key Changes from Current Prompt

| Element | Before (Go Binary) | After (Native Tools) |
|---------|--------------------|----------------------|
| Prerequisites | Go binary in PATH | OpenCode service at :4095 |
| Session init | `opencode_skill init-session` | `external_opencode_init_session()` |
| Send message | `opencode_skill project session "message"` | `external_opencode_send_message()` |
| Wait | `opencode_skill project session /wait` | `external_opencode_wait_for_result()` |
| Status | `opencode_skill project session /status` | `external_opencode_get_status()` |
| Answer | `opencode_skill project session /answer "A"` | `external_opencode_answer_question()` |
| Wait any | `opencode_skill wait_any project s1 s2` | `external_opencode_wait_any(sessions=[...])` |
| Resume | `opencode_skill project session /resume` | `external_opencode_resume_session()` |
| Abort | `opencode_skill project session abort` | `external_opencode_abort_session()` |
| Sync mode | `--sync` flag | `wait_for_result` is inherently blocking |
| File prompt | `@file.txt` syntax | Pass text directly to `send_message` |
| Daemon | Port 44111 TCP | No daemon (runs in-process) |
| Config | `~/.opencode_skill/config.json` | `{data_dir}/opencode_skill.json` |

## Constraints
- Do NOT reference the Go binary anywhere
- Do NOT mention TCP daemon, port 44111, or CLI flags
- Do NOT mention `@file` prompt syntax
- Keep "Context Before Delegation" section verbatim
- **W20**: Remove the "Instance Naming Convention" section (wrong content — was copy-pasted from a different tool)
- All examples use actual tool function call syntax
- Configuration section must reference the new JSON file path

## Deliverables
- [ ] `skill.md.bak` created (backup of current prompt)
- [ ] New `skill.md` written with all 8 tools documented
- [ ] No references to Go binary, TCP daemon, CLI flags, or `@file` syntax
- [ ] All examples use correct tool function call syntax
- [ ] Configuration section updated
- [ ] Parallel sessions workflow documented
- [ ] Error handling section preserved
- [ ] "Instance Naming Convention" section REMOVED (W20)
