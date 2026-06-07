# OpenCode_Skill

This skill controls **Orchestrator** (oh-my-opencode-slim) via **native Python tools** that call the OpenCode HTTP API directly (no external binary required). The Orchestrator handles everything end-to-end - planning, execution, and cleanup.

## Prerequisites

1.  **OpenCode running**: The OpenCode service must be running at `http://127.0.0.1:4095`. Defaults (URL, Basic Auth `opencode:opencode`, default model `litellm/coding`, default agent `orchestrator`) are hardcoded in `daemon/opencode/constants.py`.
2.  **Session Initialization**: You **MUST** initialize a session with a target working directory before sending commands. The session remembers this directory, so you do not need to be in the project root when running subsequent commands.

## Context Before Delegation

> **Before sending any task to an external system, gather and share relevant context first.**

External agents (opencode sessions) start with zero knowledge of your session. They depend entirely on what you tell them. Before delegating:

1. **Gather context** — Use your available tools to understand the task (explore knowledge, read files, review prior results)
2. **Share context in your prompt** — Include relevant findings, constraints, and background in the message you send
3. **Check shared context directory** — If `shared_context_dir` is available (from your system prompt), reference it so the external system can read accumulated context

**Why:** External agents perform significantly better with context. A 30-second context gathering step before delegation saves minutes of back-and-forth later.

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

**Syntax:**
```python
external_opencode_init_session(
    project="<PROJECT>",
    session_name="<SESSION_NAME>",
    working_dir="<WORKING_DIR>"
)
```
- `<PROJECT>`: Project identifier (e.g., `myapp`, `website`, `api`).
- `<SESSION_NAME>`: Task or feature name (e.g., `task-1`, `bugfix`).
- `<WORKING_DIR>`: Absolute path to the project root directory where the agent should work.

**Example:**
```python
external_opencode_init_session(
    project="myapp",
    session_name="feature-login",
    working_dir="/Users/me/projects/my-app"
)
```

**Re-initializing a Session:**
If you call `external_opencode_init_session` with the same PROJECT and SESSION_NAME, the old session will be automatically aborted and a new one created. No confirmation is required.

### 2. Send a Message

**Syntax:**
```python
external_opencode_send_message(
    project="<PROJECT>",
    session_name="<SESSION_NAME>",
    message="<MESSAGE>"
)
```
- `<PROJECT>`: The project identifier used when initializing the session.
- `<SESSION_NAME>`: The session name used when initializing the session.
- `<MESSAGE>`: Text to send, or a command prompt (e.g., `start-work`, `continue`, `retry`, `abort`).

### Non-Blocking Message Submission

All `external_opencode_send_message` calls return **immediately** with a confirmation (fire-and-forget). The Orchestrator continues processing in the background. Use `external_opencode_wait_for_result` or `external_opencode_get_status` to retrieve results when ready.

### Retrieving Results

**Single Session:**
`external_opencode_wait_for_result` retrieves results from the session:
- **Blocking**: Waits up to 10 minutes for completion (polls every 30s)
- **Non-blocking alternative**: Use `external_opencode_get_status` to check if results are ready

```python
external_opencode_wait_for_result(
    project="<PROJECT>",
    session_name="<SESSION_NAME>",
    timeout=600
)
```

**Multiple Sessions:**
For parallel sessions, use `external_opencode_wait_any` to retrieve results from the first completed session:

```python
external_opencode_wait_any(
    sessions=[
        {"project": "myapp", "session_name": "task-1"},
        {"project": "myapp", "session_name": "task-2"},
        # ...
    ],
    timeout=600
)
```

### Available Tools (Basic Flow)

```python
# Send a message or prompt
external_opencode_send_message(project="myapp", session_name="feature-A", message="Your request here")

# Check status (non-blocking)
external_opencode_get_status(project="myapp", session_name="feature-A")

# Wait for result (blocking, up to 10 min)
external_opencode_wait_for_result(project="myapp", session_name="feature-A", timeout=600)

# Wait for any session to complete (for parallel work)
external_opencode_wait_any(sessions=[
    {"project": "myapp", "session_name": "task-1"},
    {"project": "myapp", "session_name": "task-2"},
    {"project": "myapp", "session_name": "task-3"},
])

# Resume a timed-out session
external_opencode_resume_session(project="myapp", session_name="feature-A")

# Abort and reset a session
external_opencode_abort_session(project="myapp", session_name="feature-A")
```

### Interactive Questions

If the Orchestrator asks a question, `external_opencode_get_status` returns it with a `request_id`:
```text
status: WAITING_FOR_INPUT
request_id: req_abc123
question: Which linter should I use?
options: [...]
```

**CRITICAL INSTRUCTION**: When a question is received:
1.  **Suggest** the best answer to the user based on context.
2.  **Ask** the user for confirmation.
3.  **DO NOT** automatically answer unless the user explicitly tells you to "auto-answer" or "decide for me".

**To Answer:**
```python
# Answer with text or option label
external_opencode_answer_question(
    project="<PROJECT>",
    session_name="<SESSION_NAME>",
    request_id="req_abc123",
    answers=["ESLint"]
)

# If multiple questions are asked, pass multiple answers
external_opencode_answer_question(
    project="<PROJECT>",
    session_name="<SESSION_NAME>",
    request_id="req_abc123",
    answers=["ESLint", "Jest"]
)
```

## Unified Workflow

1.  **Initialize**: `external_opencode_init_session(project="myapp", session_name="feature-A", working_dir="/path/to/project")`
2.  **Send request**: `external_opencode_send_message(project="myapp", session_name="feature-A", message="Your request here")`
3.  **Answer questions if needed**: `external_opencode_answer_question(project="myapp", session_name="feature-A", request_id="...", answers=["Option 1"])`
4.  **Wait for completion**: `external_opencode_wait_for_result(project="myapp", session_name="feature-A", timeout=600)`

The Orchestrator handles planning, execution, and cleanup automatically.

## Parallel Sessions Workflow (Async)

Run up to **3 sessions in parallel** for independent tasks.

> **⚠️ IMPORTANT: Only use for tasks with NO dependencies.** Parallel sessions must not rely on each other's output or modify the same files.

**Basic pattern:**
```python
# Initialize sessions in parallel
external_opencode_init_session(project="myapp", session_name="task-1", working_dir="/path")
external_opencode_init_session(project="myapp", session_name="task-2", working_dir="/path")
external_opencode_init_session(project="myapp", session_name="task-3", working_dir="/path")

# Send requests (async / fire-and-forget)
external_opencode_send_message(project="myapp", session_name="task-1", message="Task 1")
external_opencode_send_message(project="myapp", session_name="task-2", message="Task 2")
external_opencode_send_message(project="myapp", session_name="task-3", message="Task 3")

# Wait for any session to complete
external_opencode_wait_any(sessions=[
    {"project": "myapp", "session_name": "task-1"},
    {"project": "myapp", "session_name": "task-2"},
    {"project": "myapp", "session_name": "task-3"},
])

# When one session is complete (ex: task-1), you can start a new one (ex: task-4)
external_opencode_init_session(project="myapp", session_name="task-4", working_dir="/path")
external_opencode_send_message(project="myapp", session_name="task-4", message="Task 4")

# Use wait_any again to get the next completed session
external_opencode_wait_any(sessions=[
    {"project": "myapp", "session_name": "task-2"},
    {"project": "myapp", "session_name": "task-3"},
    {"project": "myapp", "session_name": "task-4"},
])
```

## Error Handling

**"The operation timed out" (timeout errors type)**: Call `external_opencode_resume_session()` one or two times. If it times out repeatedly, this means the task is too large and should be split into smaller tasks.

```python
# Resume a timed-out session
external_opencode_resume_session(project="<PROJECT>", session_name="<SESSION_NAME>")
```

**Session is busy error**: Wait with `external_opencode_wait_for_result()` or abort with `external_opencode_abort_session()`.