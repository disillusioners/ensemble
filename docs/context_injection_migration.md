# Per-Turn Context Messages

## Overview

Ensemble supplies runtime context to agents as discrete `HumanMessage` objects. Each message uses a tagged envelope:

```text
[SYSTEM CONTEXT: <title>]

<context content>
```

These messages are separate from both the agent's system prompt and the user's message body. This keeps runtime data structurally distinct from persona instructions and user input.

Context-message delivery is unconditional. Agents do not configure or opt into it through `meta.json`.

## Orchestrator

The central orchestrator is `assemble_context_messages` in:

```text
daemon/services/context_messages.py
```

It is invoked as part of turn processing and assembles the applicable context blocks for the current agent and project. Every emitted block is a `HumanMessage` marked in `additional_kwargs` as injected context, including a `context_kind` value that identifies the block type.

Conceptually, the LLM input is arranged as:

```text
SystemMessage(agent persona and instructions)
HumanMessage([SYSTEM CONTEXT: ...])
HumanMessage([SYSTEM CONTEXT: ...])
HumanMessage(user request)
```

Only blocks with available content are emitted, so an agent without an attached project or without a particular context source does not receive an empty placeholder.

## Context Sources

### Project context

Project information is rendered into a `[SYSTEM CONTEXT: Related Project]` message. The project block contains the available project metadata needed by the agent, such as identity, directory information, description, status, tags, relationships, and other project fields supplied by the project repository.

### Shared-context metadata

Tree-root shared-context key/value metadata is included in the related-project context when available. Tree-root resolution lets instances in the same hierarchy receive the appropriate shared metadata without embedding it into the user request.

Shared Markdown content selected for a request may be represented as its own `[SYSTEM CONTEXT: Shared Context]` message. That selection pipeline is separate from the unconditional context-message delivery mechanism described here.

### Critical notes

Critical project notes are fetched from the project repository and rendered as a dedicated subsection of the related-project message. They retain their priority and category cues so operational constraints and risks are easy to distinguish from ordinary project metadata.

### Project history

Recent project history is fetched from the project repository and rendered as a subsection of the related-project message. The orchestrator uses the current repository data when assembling the context rather than placing history inside the agent persona or user text.

### Auto-load skills

Skills configured for automatic loading are assembled by `_build_auto_load_block` in `daemon/services/context_messages.py`. When applicable, the result is emitted as a separate `[SYSTEM CONTEXT: Auto-Load Skills]` HumanMessage so foundational skill guidance remains distinct from project data and the user request.

## Message Shape

Context messages follow a common structure:

```python
HumanMessage(
    content="[SYSTEM CONTEXT: <title>]\n\n<content>",
    additional_kwargs={
        "injected_message": True,
        "context_kind": "<kind>",
    },
)
```

Consumers should use the metadata rather than parsing ordinary user text to identify injected context. In particular:

- `injected_message: true` identifies an orchestrator-supplied message.
- `context_kind` identifies the source category.
- The `[SYSTEM CONTEXT: ...]` title gives the model and human-facing clients a readable label.

## Turn Processing

For each turn, the runtime calls `assemble_context_messages` with the current instance, project, agent metadata, repositories, and request data. The orchestrator then:

1. Resolves the instance's project and tree-root context.
2. Loads available project metadata, critical notes, and recent history.
3. Loads shared-context metadata and any applicable shared content.
4. Builds the auto-load skill block through `_build_auto_load_block`.
5. Returns discrete context `HumanMessage` objects for insertion into the model input flow.

The runtime controls persistence and reuse details for individual context categories, but the delivery contract remains the same: context reaches the model as tagged HumanMessages, never as text baked into the system prompt or concatenated into the user's message body.

## Operational Guidance

- Do not add a context-delivery field to agent `meta.json`; there is no per-agent toggle.
- Add or update project information through the project repositories and APIs rather than editing agent prompts.
- Store hierarchy-wide runtime values in shared-context metadata when that scope is appropriate.
- Use critical notes for important project constraints and recent history for project events.
- Configure foundational skills through the auto-load skill mechanism; `_build_auto_load_block` formats them for delivery.
- Clients that display message streams may identify injected context through `additional_kwargs.injected_message` and `context_kind`.

## Key Symbols

| Symbol | Location | Purpose |
|---|---|---|
| `assemble_context_messages` | `daemon/services/context_messages.py` | Coordinates construction of context messages during turn processing |
| `_build_auto_load_block` | `daemon/services/context_messages.py` | Builds the auto-load skill context block |
| Context message factory and formatters | `daemon/services/context_messages.py` | Produce consistently tagged HumanMessages for project, shared, and skill content |

## See Also

- `daemon/services/context_messages.py` — context assembly and formatting
- `docs/agents.md` — agent definitions and metadata
