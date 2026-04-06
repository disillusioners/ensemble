# OpenCode_Skill

This skill controls **Orchestrator** (oh-my-opencode-slim) via the web API using a robust **Daemon-Client architecture** implemented in Go. The Orchestrator handles everything end-to-end - planning, execution, and cleanup.

## Prerequisites

1.  **Binary**: Use `opencode_skill` for all interactions. Ensure it is in your PATH (e.g., `~/bin/opencode_skill`).
2.  **Session Initialization**: You **MUST** initialize a session with a target working directory before sending commands. The session remembers this directory, so you do not need to be in the project root when running subsequent commands.

## Usage

### 1. Initialize a Session
**Syntax:**
```bash
opencode_skill init-session <PROJECT> <SESSION_NAME> <WORKING_DIR>
```
- `<PROJECT>`: Project identifier (e.g., `myapp`, `website`, `api`).
- `<SESSION_NAME>`: Task or feature name (e.g., `task-1`, `bugfix`).
- `<WORKING_DIR>`: Absolute path to the project root directory where the agent should work.

**Example:**
```bash
opencode_skill init-session myapp feature-login /Users/me/projects/my-app
```

**Re-initializing a Session:**
If you run `init-session` with the same PROJECT and SESSION_NAME, the old session will be automatically aborted and a new one created. No confirmation is required.

### 2. Send Commands
**Syntax:**
```bash
opencode_skill [flags] <PROJECT> <SESSION_NAME> <MESSAGE>
```

> **Note:** Flags must come **before** positional arguments.

- `<PROJECT>`: The project identifier used when initializing the session.
- `<SESSION_NAME>`: The session name used when initializing the session.
- `<MESSAGE>`: Text to send, or a command starting with `/`.
- `[flags]`:
    - `--sync`: Send prompt AND wait for result in a single command (blocking).
    - `--quiet`: Suppress informational messages (keeps errors visible).

### Long Message Input via @file

For long or complex prompts, use `@file` syntax to read from a file:

**Path format:** `/tmp/opencode_skill/prompt_files/{project_name}/{session_name}_prompt.txt`

**Example:**
```bash
# Create input file with the task
cat > /tmp/opencode_skill/prompt_files/myapp/feature-login_prompt.txt << 'EOF'
Implement user login with email and password.
Include session management and logout functionality.
EOF

# Send using @file syntax
opencode_skill --sync myapp feature-login @/tmp/opencode_skill/prompt_files/myapp/feature-login_prompt.txt
```

### Sync Mode (`--sync`)
The `--sync` flag combines sending a prompt and waiting for results into a single command:

```bash
# Instead of two commands:
opencode_skill myapp feature-A "Fix the bug"
opencode_skill myapp feature-A /wait

# Use one command (flags first!):
opencode_skill --sync myapp feature-A "Fix the bug"
```

### Quiet Mode (`--quiet`)
The `--quiet` flag suppresses verbose metadata. Only the response content is returned:

```bash
# Normal output includes metadata
opencode_skill myapp feature-A /wait

# Quiet mode returns only the response
opencode_skill --quiet myapp feature-A /wait

# Combine sync + quiet for clean, one-shot responses
opencode_skill --sync --quiet myapp feature-A "What is 2+2?"
```

### Non-Blocking Message Submission
All message submissions return **immediately** with a confirmation:

```text
[SUBMITTED] Run: opencode_skill <PROJECT> <SESSION_NAME> /wait
```

The daemon continues processing in the background. Use `/wait` to retrieve results when ready.

### Retrieving Results

**Single Session (`/wait`):**
The `/wait` command retrieves results from the daemon:
- **Blocking**: Waits up to 10 minutes for completion
- **Non-blocking alternative**: Use `/status` to check if results are ready

```bash
opencode_skill <PROJECT> <SESSION_NAME> /wait
```

**Multiple Sessions (`wait_any`):**
For parallel sessions, use `wait_any` to retrieve results from the first completed session:

```bash
# Wait for first session to complete among multiple parallel sessions
opencode_skill wait_any <PROJECT> <SESSION1> [<SESSION2> ...]
```
</sector>

### Available Commands

**Configuration Management:**

> **CRITICAL INSTRUCTION**: Do not change the configuration unless the user **explicitly** asks you to.

```bash
# Display all configurable properties and their current values
opencode_skill config list

# Get the currently configured default model
opencode_skill config get model

# Set a new default model (must be in provider/model format)
opencode_skill config set model provider/new-model-name
```

**Basic Flow:**
```bash
# Send a message or prompt
opencode_skill myapp feature-A "Your request here"

# Check status (non-blocking)
opencode_skill myapp feature-A /status

# Wait for result (blocking, up to 10 min)
opencode_skill myapp feature-A /wait

# Wait for any session to complete (for parallel work)
opencode_skill wait_any myapp task-1 task-2 task-3

# Resume a timed-out session
opencode_skill myapp feature-A /resume

# Sync mode - send and wait in one command (flags first!)
opencode_skill --sync myapp feature-A "Your request here"

# Sync + quiet - clean response only
opencode_skill --sync --quiet myapp feature-A "Your request here"
```

### Interactive Questions
If the Orchestrator asks a question, it will prompt you:
```text
[?] Request ID: ...
    Which linter should I use?
    Options available.
```

**CRITICAL INSTRUCTION**: When a question is received:
1.  **Suggest** the best answer to the user based on context.
2.  **Ask** the user for confirmation.
3.  **DO NOT** automatically execute the `/answer` command unless the user explicitly tells you to "auto-answer" or "decide for me".

**To Answer:**
```bash
# Answer with text or option label
opencode_skill <PROJECT> <SESSION_NAME> /answer "ESLint"

# If multiple questions are asked:
opencode_skill <PROJECT> <SESSION_NAME> /answer "ESLint" "Jest"
```

## Unified Workflow

1.  **Initialize**: `opencode_skill init-session myapp feature-A /path/to/project`
2.  **Send request**: `opencode_skill myapp feature-A "Your request here"` or use `--sync` to send and wait
3.  **Answer questions if needed**: `opencode_skill myapp feature-A /answer "Option 1"`
4.  **Wait for completion**: `opencode_skill myapp feature-A /wait`

The Orchestrator handles planning, execution, and cleanup automatically.

## Parallel Sessions Workflow (Async)

Run up to **3 sessions in parallel** for independent tasks.

> **⚠️ IMPORTANT: Only use for tasks with NO dependencies.** Parallel sessions must not rely on each other's output or modify the same files.

**Basic pattern:**
```bash
# Initialize sessions in parallel
opencode_skill init-session myapp task-1 /path & opencode_skill init-session myapp task-2 /path & opencode_skill init-session myapp task-3 /path & wait

# Send requests (async)
opencode_skill myapp task-1 "Task 1" & opencode_skill myapp task-2 "Task 2" & opencode_skill myapp task-3 "Task 3" & wait

# Wait for any session to complete
opencode_skill wait_any myapp task-1 task-2 task-3

# When one session is complete (ex: task-1), you can start new one (ex: task-4)
opencode_skill myapp task-4 "Task 4"

# Use wait_any again to get the next completed session
opencode_skill wait_any myapp task-2 task-3 task-4
```

## Error Handling

**"The operation timed out"**: Run `/resume` once. If it times out again, split into smaller tasks.

```bash
# Resume a timed-out session
opencode_skill <PROJECT> <SESSION_NAME> /resume
```