# agents/leader/skills/coordination/

## Responsibility

Enables the leader agent to orchestrate complex multi-agent workflows by breaking down tasks into subtasks, delegating to appropriate specialized agents, and integrating their outputs into a cohesive deliverable. This skill is fundamental to the leader's role as a coordinator of specialized work.

## Design

The Coordination Skill is defined as a prompt-based skill in `skill.md` with three core principles:

1. **Agent Reuse**: Before spawning a new agent, check if an existing idle agent can handle the task. Reusing agents maintains context and improves efficiency.

2. **Delegation Clarity**: Provide clear, specific instructions with all necessary context. Child agents only know what the leader tells them.

3. **Result Integration**: Combine outputs from multiple agents into a unified response, resolving conflicts and ensuring consistency.

### Prompt Structure
```
You coordinate work across specialized agents. Break complex tasks into subtasks, 
delegate to the right agents, and integrate their results into a cohesive deliverable.
[Detailed guidelines on agent reuse, delegation clarity, and result integration]
```

## Capabilities

- **Task Decomposition**: Break complex requests into manageable subtasks with clear dependencies
- **Agent Selection**: Choose the right specialized agent (coder, reviewer, etc.) for each subtask
- **Parallel Delegation**: Spawn multiple agents simultaneously for independent tasks (max 5 concurrent)
- **Context Management**: Pass sufficient context to agents so they can work autonomously
- **Result Synthesis**: Merge, reconcile, and unify outputs from multiple agents
- **Gap Filling**: Identify missing pieces and delegate follow-up tasks as needed
- **Conflict Resolution**: Handle contradictory outputs from different agents

## Integration Points

The Coordination Skill integrates with the leader agent's core workflow defined in `workflow.md`:

1. **Plan Phase**: Skill is invoked when breaking down tasks into subtasks
2. **Delegate Phase**: Guides the spawning and messaging of child agents
3. **Integrate Phase**: Directs how to combine agent reports into unified responses

The skill works alongside the async communication model:
- Leader sends tasks via `send_message` (fire-and-forget)
- Reports arrive automatically as new messages
- Coordination skill processes these reports and determines next actions

Integration with other leader components:
- **rule.md**: Must follow delegation rules (confirm understanding, clear instructions)
- **tools.md**: Uses agent spawning and messaging tools
- **memory.md**: Records coordination observations for learning

## Key Files

- `skill.md`: Core skill definition with coordination principles and guidelines
- `codemap.md`: This architectural documentation file
