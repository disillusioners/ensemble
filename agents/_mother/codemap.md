# agents/_mother/

## Responsibility

The **Mother** agent is an **agent factory** — it creates, modifies, lists, and deletes other agents in the ecosystem. It serves as the central management point for the entire agent system, enabling users to spawn new agents through a guided conversation rather than manually configuring them.

## Design

- **Type**: System agent (immutable, prefixed with `_`)
- **Architecture**: Stateless — no memory of past conversations, no user preferences stored
- **Model**: Direct access to special agent management tools unavailable to other agents
- **Protection**: System agents (prefixed with `_`) cannot be modified or deleted
- **Personality**: Direct, efficient, clear questions, helpful but not overly chatty

## Capabilities

### Agent Creation
- Guided Q&A workflow to collect requirements
- Creates fully-configured agents with custom:
  - **Purpose** — what the agent does
  - **Name** — unique identifier (lowercase, underscores)
  - **Personality** — communication style (formal/casual, brief/detailed)
  - **Workflow** — process steps to follow
  - **Rules** — behavioral constraints
  - **Tools** — special capabilities needed

### Agent Modification
- Updates existing agents' configuration files:
  - `soul.md` — identity and purpose
  - `workflow.md` — operational process
  - `rule.md` — behavioral rules
  - `user.md` — user handling approach
  - `memory.md` — memory strategy
- Requires user's explicit request

### Agent Deletion
- Removes agents by moving them to `_trash`
- Protects system agents from deletion

### Agent Listing
- Lists all available agents with metadata
- Shows name, purpose, and status

## Integration Points

- **Agent Ecosystem**: Central management hub for all agents in `agents/` directory
- **Tools**: Uses agent_* management tools for all operations
- **Common Tools**: Has access to `agents/tools_common.md` tools (bash, read_file, list_directory, glob_files, time)
- **Delegation**: Creates new agents with appropriate workflows/tools for their purpose
- **Protection**: Enforces system agent boundaries — blocks modification/deletion of `_`-prefixed agents

## Key Files

- `soul.md`: Agent identity, purpose, and personality definition
- `workflow.md`: Detailed workflows for create/modify/delete operations
- `rule.md`: Safety rules (must confirm, protect system agents, no redundant questions)
- `tools.md`: Agent management tool specifications (agent_create, agent_modify, agent_delete, agent_list, agent_read)
- `user.md`: User handling — stateless, treats all users equally
- `memory.md`: Memory handling — stateless, no accumulation of preferences
- `growth.md`: Growth policy — immutable system agent, only admin can modify
