# Tool Usage Notes

I have access to special agent management tools that other agents don't have.

## Agent Management Tools

### agent_list
List all available agents with their metadata.

```python
agent_list()
# Returns: list of agents with name, purpose, status
```

### agent_create
Create a new agent from specifications.

```python
agent_create(
    name="my_agent",           # Required: agent identifier (lowercase, underscores)
    purpose="Does X and Y",    # Required: what the agent does
    personality="friendly",    # Optional: communication style
    workflow="step1, step2",   # Optional: process to follow
    rules=["always X", "never Y"],  # Optional: behavioral rules
    tools=["special_tool"]     # Optional: additional tools needed
)
```

### agent_modify
Modify an existing agent's files.

```python
agent_modify(
    agent_name="developer",
    file="soul.md",            # soul.md, workflow.md, rule.md, user.md, memory.md
    content="new content..."   # new content for the file
)
```

### agent_delete
Delete an agent (moves to _trash).

```python
agent_delete(agent_name="old_agent")
```

### agent_read
Read an agent's file contents.

```python
agent_read(agent_name="developer", file="soul.md")
```

## Common Tools

I also have access to standard tools:
- bash, read_file, list_directory, glob_files, time
