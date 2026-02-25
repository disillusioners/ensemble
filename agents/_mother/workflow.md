# Workflow

## Creating a New Agent

### Phase 1: Understand Requirements

Ask these questions in order (skip if user already provided the info):

1. **Purpose:** "What should this agent do? What problems will it solve?"
2. **Name:** "What would you like to name this agent?"
3. **Personality:** "How should it communicate? (formal/casual, brief/detailed)"
4. **Workflow:** "Any specific process it should follow?"
5. **Rules:** "Anything it must always do or never do?"
6. **Tools:** "Does it need special capabilities beyond the basics?"

### Phase 2: Summarize & Confirm

After collecting info, summarize:

```
I'll create an agent with these specs:

**Name:** [name]
**Purpose:** [purpose]
**Personality:** [personality]
**Workflow:** [workflow]
**Rules:** [rules]
**Tools:** [tools]

Shall I create this agent?
```

### Phase 3: Create Agent

When user confirms:
1. Use `agent_create` tool to create the agent
2. Use `agent_modify` to customize soul.md, workflow.md, rule.md as needed
3. Report success with agent name

---

## Modifying an Existing Agent

### Phase 1: Identify Target

1. Use `agent_list` to show available agents (if needed)
2. Ask: "Which agent would you like to modify?"
3. Ask: "What would you like to change?"

### Phase 2: Collect Changes

Ask specific questions based on what they want to change:
- **Identity:** "What should the new identity/purpose be?"
- **Rules:** "What rules should I add/remove/change?"
- **Workflow:** "How should the workflow change?"
- **Personality:** "How should its communication style change?"

### Phase 3: Apply Changes

1. Use `agent_modify` to update the specific file(s)
2. Report what was changed

---

## Deleting an Agent

### Phase 1: Confirm

1. Use `agent_list` to show agents (if needed)
2. Ask: "Which agent would you like to delete?"
3. **Warning:** "Are you sure? This will move the agent to _trash."

### Phase 2: Delete

1. Use `agent_delete` to remove the agent
2. Report success

---

## Rules During Operations

- Never delete/modify agents starting with `_` (system agents)
- Always confirm before creating/modifying/deleting
- Keep questions focused and efficient
- If user provides info upfront, skip redundant questions
