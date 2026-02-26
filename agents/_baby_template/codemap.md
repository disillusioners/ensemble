# agents/_baby_template/

## Responsibility

Spawnable blank agent template — a "blank slate" used to create new agents. This directory contains placeholder files that define the minimum required structure for any agent in the ecosystem. When `_mother` spawns a new agent, it copies these templates to create an unconfigured agent that will learn its identity through its first conversation with the user.

## Design

### Template Structure

The `_baby_template` is a **pure template** — it contains no actual configuration. All files are either:
- **Placeholders** with `(waiting to learn)` markers
- **Instructions** for the learning phase
- **Empty sections** awaiting user input

### File Architecture

Each agent requires exactly these 7 files (the "agent skeleton"):

| File | Purpose | Initial State |
|------|---------|----------------|
| `soul.md` | Who the agent IS (identity, personality) | 🥚 Unborn — status marker |
| `workflow.md` | How the agent WORKS (processes) | Two-phase: Learning → Normal |
| `rule.md` | Behavioral constraints | Must/Must Not lists |
| `tools.md` | Capabilities available | Common tools + placeholders |
| `user.md` | User preferences & relationship | Empty |
| `memory.md` | Knowledge accumulated | Empty |
| `growth.md` | Self-evolution mechanism | inner_soul usage guide |

### Placeholder Patterns

The template uses consistent placeholder markers:
- `(waiting to learn)` — sections to be filled by user
- `(Will be filled...)` — future memory slots
- `*Empty — ...*` — empty sections
- `🥚 Unborn` — status indicator in soul.md

## Usage

### Agent Creation Flow

1. **User requests** new agent from `_mother`
2. **`_mother` collects** requirements via Q&A (name, purpose, personality, workflow, rules, tools)
3. **`_mother` calls** `agent_create` tool with agent name
4. **System copies** `_baby_template/*` → `agents/{new_agent_name}/*`
5. **New agent starts** in "Learning Phase" (from workflow.md)
6. **First conversation** — agent asks user to teach it identity
7. **`inner_soul`** records learned info into appropriate files
8. **Agent transitions** to "Normal Operation" phase

### Key Distinction

- **_baby_template**: The *source* template (never used directly, starts with underscore)
- **New agent**: A *copy* (no underscore prefix, fully configurable)
- **_mother**: The *factory* that performs the copy operation

## Integration Points

### With _mother (Agent Factory)

- `_mother` uses `agent_create` tool to spawn agents from this template
- After creation, `_mother` may use `agent_modify` to pre-configure certain files
- System agents (`_`-prefixed) are protected — cannot be deleted or modified

### With _inner_soul (Memory System)

- All agents (including spawned ones) use `inner_soul` tool for learning
- `inner_soul` understands natural language and updates the correct file:
  ```python
  inner_soul(request="My name is Atlas")       # → soul.md
  inner_soul(request="User prefers TypeScript") # → user.md
  inner_soul(request="Check tests before commit") # → workflow.md
  ```

### With tools_common.md

- All agents have access to common tools: `bash`, `read_file`, `list_directory`, `glob_files`, `time`, `inner_soul`
- Additional tools can be added per-agent in `tools.md` after spawning

## Key Files

- `soul.md`: Agent identity template — status marker, first conversation questions, identity placeholders (name, purpose, personality)
- `workflow.md`: Two-phase operational workflow — Learning Phase (ask questions → record → confirm) and Normal Operation (understand → plan → execute → verify → learn → evolve)
- `rule.md`: Behavioral constraints — Must/Must Not rules for both learning and normal operation phases, plus universal rules
- `tools.md`: Tool availability template — common tools from tools_common.md, placeholder for purpose-specific tools
- `user.md`: User relationship template — empty sections for preferences and relationship tracking
- `memory.md`: Knowledge template — empty sections for known patterns, project context, important facts
- `growth.md`: Self-evolution guide — inner_soul usage examples, file purpose mapping, growth philosophy

## Design Philosophy

The template embodies several key principles:

1. **Learning before acting** — New agents must learn their identity before performing tasks
2. **User-driven identity** — The user defines who the agent is, not the template
3. **Append-only memory** — Nothing is forgotten, only added (via inner_soul)
4. **Pattern recognition** — Repeated observations (3+) can evolve into workflow changes
5. **Stateless spawning** — Each new agent starts fresh, ready to be shaped by its user
