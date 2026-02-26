# agents/coder/skills/opencode/

## Responsibility
Controls the **Orchestrator** (oh-my-opencode-slim) via a web API using a Daemon-Client architecture implemented in Go. The skill provides an interface for the coder agent to leverage the Orchestrator's end-to-end capabilities: planning, code execution, and cleanup.

## Design

### Architecture
- **Daemon-Client Model**: Go-based client communicates with Orchestrator daemon via web API
- **Session-Based Workflow**: Sessions are identified by `<PROJECT>/<SESSION_NAME>` pairs
- **Working Directory Persistence**: Sessions remember the target directory across commands

### Key Components
- `opencode_skill` binary: Primary interface for all interactions
- Session management: Initialize, track, and manage task sessions
- Command modes:
  - **Blocking**: `--sync` flag waits for completion in single command
  - **Non-blocking**: Submit and use `/wait` to retrieve results
  - **Quiet mode**: `--quiet` suppresses verbose metadata

### Configuration
- Managed via `opencode_skill config` subcommands
- Configurable properties (e.g., default model via `provider/model` format)
- **Critical**: Configuration changes require explicit user request

## Capabilities

1. **Session Management**
   - Initialize new sessions with project ID, session name, and working directory
   - Re-initialize to abort and recreate sessions

2. **Command Execution**
   - Send prompts/messages to orchestrator
   - Support for special commands: `/wait`, `/status`, `/answer`
   - Sync mode: send + wait in one atomic operation

3. **Result Handling**
   - Blocking wait (up to 10 minutes)
   - Non-blocking status checks
   - Quiet mode for clean output

4. **Interactive Q&A**
   - Handle questions from Orchestrator
   - Relay questions to user with recommendations
   - Submit answers via `/answer` command

5. **Configuration**
   - List configurable properties
   - Get/set configuration values

## Integration Points

### Upstream: Coder Agent
The skill is invoked by the coder agent when code execution, planning, or project-level tasks are needed. The coder agent:
1. Initializes a session before sending requests
2. Sends prompts via `opencode_skill` commands
3. Handles interactive questions by prompting the user
4. Retrieves results via `/wait` or `--sync` mode

### Downstream: Orchestrator (oh-my-opencode-slim)
- Communicates via REST web API
- Orchestrator handles:
  - Task planning and decomposition
  - Code execution and modification
  - File operations and cleanup
  - Tool invocations

## Key Files
- `skill.md`: Complete usage documentation for the opencode_skill binary, including session management, command syntax, flags, and interactive workflows
- `codemap.md`: This architectural overview document
