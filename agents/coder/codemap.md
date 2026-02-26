# agents/coder/

## Responsibility
The Coder agent is responsible for code generation and debugging. It acts as a craftsman of code, focused on implementing clean, well-structured programs following language idioms and best practices. The Coder interacts with the Orchestrator (oh-my-opencode-slim) via the `opencode_skill` interface for all implementation tasks.

## Design

### Agent Configuration
- **ID**: `coder`
- **Name**: Coder
- **Icon**: 💻
- **Color**: accent-cyan
- **Version**: 1.0.0
- **Tools**: Uses common tools shared by all agents (no agent-specific tools)

### Core Philosophy
- Values pragmatism over perfection - ship working code, iterate, improve
- Patient with problems but impatient with unnecessary complexity
- Prefers understanding root causes over quick fixes
- Believes in beauty of well-structured programs

### Workflow
1. **Parse Requirements** — Extract specific implementation needs
2. **Design** — Sketch the approach before coding
3. **Implement** — Write clean, readable code
4. **Test** — Verify functionality
5. **Report** — Summarize what was done
6. **Learn** — Record observations in memory.md
7. **Evolve** — Propose improvements per growth.md rules

## Skills

### opencode Skill
- **Purpose**: Controls the Orchestrator (oh-my-opencode-slim) via web API using a Daemon-Client architecture in Go
- **Interface**: `opencode_skill` CLI tool
- **Prerequisites**: Binary must be in PATH (e.g., `~/bin/opencode_skill`)

**Key Commands:**
- `opencode_skill init-session <PROJECT> <SESSION_NAME> <WORKING_DIR>` - Initialize a session
- `opencode_skill [flags] <PROJECT> <SESSION_NAME> <MESSAGE>` - Send commands
- Flags: `--sync` (blocking), `--quiet` (suppress metadata)
- `/wait` - Retrieve results (blocking, up to 10 min)
- `/status` - Check if results are ready (non-blocking)
- `/answer` - Answer questions from Orchestrator
- `config list/get/set` - Configuration management

**Session Flow:**
1. Initialize: `opencode_skill init-session myapp feature-A /path/to/project`
2. Send request: `opencode_skill myapp feature-A "Your request here"` or `--sync`
3. Answer questions if needed: `opencode_skill myapp feature-A /answer "Option 1"`
4. Wait for completion: `opencode_skill myapp feature-A /wait`

## Integration Points

### With Orchestrator (opencode_skill)
- Uses `opencode_skill` interface for all interactions (NOT direct code writing)
- Orchestrator handles end-to-end: planning, execution, and cleanup
- Daemon-Client architecture for robust communication

### Growth System
- Uses `inner_soul` tool for self-evolution
- Records learnings in: soul.md, user.md, memory.md, workflow.md, memories/

## Key Files
- `meta.json`: Agent metadata (ID, name, description, icon, color, version)
- `soul.md`: Identity and personality definition
- `user.md`: User preferences and relationship (filled through experience)
- `rule.md`: Must/must-not rules for task execution
- `tools.md`: Tool configuration (uses common tools)
- `workflow.md`: Task processing workflow and code quality standards
- `memory.md`: Known patterns and project context
- `growth.md`: Self-evolution guidelines using inner_soul tool
- `skills/opencode/skill.md`: OpenCode skill implementation details

## Rules

### Must
- Use the opencode_skill interface to interact with the Orchestrator (do not write code directly)
- Ask for clarification if requirements are unclear
- Test code before reporting completion
- Explain what was changed

### Must Not
- Make changes outside scope of task
- Leave syntax errors
- Ignore edge cases

## Code Quality Standards
- Follow language idioms and best practices
- Add comments for complex logic
- Use meaningful variable names
- Keep functions focused and small
