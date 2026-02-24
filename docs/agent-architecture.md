# Agent Architecture Design

A self-evolving multi-agent system where agents are born as templates and grow through experience.

---

## Implementation Status

| Component | Status | Location |
|-----------|--------|----------|
| Agent config files | ✅ Implemented | `agents/*/` |
| `_baby_template/` | ✅ Implemented | `agents/_baby_template/` |
| `_inner_soul/` | ✅ Implemented | `agents/_inner_soul/` |
| `inner_soul` tool | ✅ Implemented | `daemon/tools/inner_soul.py` |
| Tool integration | ✅ Implemented | `daemon/tools/session.py` |
| Pattern tracking | 🔄 Pending | - |
| Rate limiting | 🔄 Pending | - |
| User approval flow | ⚠️ Partial | Creates `proposed/` files |

---

## Overview

```
┌─────────────────────────────────────────────────────────────────────┐
│                         AGENT LIFECYCLE                              │
│                                                                      │
│   [Baby Template] ──user defines purpose──> [Growing Agent]          │
│        │                                        │                    │
│        │                                        ▼                    │
│        │                              Experience & Learning          │
│        │                                        │                    │
│        │                                        ▼                    │
│        │                              [inner_soul] handles           │
│        │                              - memories                     │
│        │                              - workflow evolution           │
│        │                              - soul growth                  │
│        │                                        │                    │
│        └────────────────────────────────────────┘                    │
└─────────────────────────────────────────────────────────────────────┘
```

---

## Core Concepts

### 1. Agents Are Born, Not Built

Agents start as minimal "baby templates" with placeholder values. The user provides purpose and initial direction. The agent grows through experience.

### 2. Only `growth.md` Is Immutable

Every file can evolve except `growth.md` — the DNA that defines evolution rules.

### 3. `inner_soul` Is the Mutation Gateway

Agents don't edit themselves directly. They use the `inner_soul` tool, which handles validation, routing, and approval workflows.

### 4. Memory Is Two-Tiered

- **`memory.md`** — Core identity and essential knowledge (short, stable)
- **`memories/`** — Timestamped event files (observations, learnings, experiences)

---

## File Structure

```
agents/
├── _inner_soul/              # Immutable tool-agent
│   ├── soul.md               # Identity: "I am the mutation gateway"
│   ├── workflow.md           # Process: receive → classify → validate → route → apply
│   └── rule.md               # Constraints: cannot modify self
│
├── _baby_template/           # Template for new agents
│   ├── growth.md             # Evolution DNA (immutable)
│   ├── soul.md               # Template with {{PURPOSE}}, {{TRAITS}}
│   ├── workflow.md           # Minimal task loop
│   ├── rule.md               # Basic constraints
│   ├── tools.md              # Available tools (includes inner_soul)
│   ├── memory.md             # Core knowledge placeholder
│   └── memories/             # Event storage
│
├── coder/                    # Example: code implementation agent
│   ├── growth.md
│   ├── soul.md               # + SOUL_HISTORY table
│   ├── workflow.md
│   ├── rule.md
│   ├── tools.md
│   ├── memory.md             # Core only
│   └── memories/             # Timestamped files
│
└── leader/                   # Example: orchestration agent
    └── ... (same structure)
```

---

## Priority Hierarchy

When conflicts arise, files take precedence in this order:

| Priority | File | Mutability | Purpose |
|----------|------|------------|---------|
| 1 | `growth.md` | IMMUTABLE | Evolution rules, DNA |
| 2 | `rule.md` | With approval | Hard behavioral constraints |
| 3 | `soul.md` | With approval | Evolving identity |
| 4 | `workflow.md` | With pattern | Task execution process |
| 5 | `tools.md` | With pattern | Available capabilities |
| 6 | `memory.md` | Freely (via inner_soul) | Core knowledge |
| 7 | `memories/` | Freely (via inner_soul) | Event log |

---

## The `inner_soul` Tool

### Simple Interface

Agents don't need to know file paths or actions. They just express intent:

```
inner_soul(intent, content)
```

| Parameter | Type | Description |
|-----------|------|-------------|
| `intent` | `remember` \| `learn` \| `change` | What the agent wants |
| `content` | string | The thing to remember/learn/change |

### Intent Routing

| Intent | What `inner_soul` Does |
|--------|------------------------|
| `remember` | Creates `memories/YYYYMMDD_HHMM_description.md` |
| `learn` | Creates memory file + tracks pattern for workflow evolution |
| `change` | Proposes workflow/soul change (may require approval) |

### Examples

```
# Remembering a preference
inner_soul(intent="remember", content="User prefers TypeScript over JavaScript")
→ Creates: memories/20260225_1430_user_prefers_typescript.md

# Learning from experience
inner_soul(intent="learn", content="Iterative testing catches bugs earlier in development")
→ Creates memory file + tracks pattern
→ After 3 similar patterns: proposes workflow change

# Requesting identity change
inner_soul(intent="change", content="I value iterative development after seeing consistent success")
→ Validates against growth.md
→ Requests user approval
→ Appends to soul.md with SOUL_HISTORY entry
```

---

## Evolution Rules

### Workflow Evolution

- Triggered by `learn` intent with recurring patterns
- After 3+ similar patterns, `inner_soul` proposes workflow change
- Changes apply automatically if within rules
- Rate limit: 1 change per 5 tasks

### Soul Evolution

- Triggered by `change` intent with identity implications
- After 5+ reinforcing experiences, `inner_soul` proposes soul addition
- **Requires human approval**
- Rate limit: 1 change per 10 tasks, min 24 hours apart
- Never removes traits, only adds
- Max 2000 characters, 20 statements

### Forbidden Changes

- Never modify `growth.md`
- Never modify `_inner_soul` agent
- Never delete memories
- Never exceed size limits

---

## Memory Architecture

### `memory.md` — Core Knowledge

```markdown
# Core Memory

Only the most essential, stable knowledge belongs here.

## Identity

[Purpose and role]

## Key Traits

- [Trait 1]
- [Trait 2]

---

*For events, observations, and learnings — use `inner_soul` tool.*
```

**Rules:**
- Max ~500 words
- Only identity and essential traits
- Updated rarely, only for core changes

### `memories/` — Event Log

```
memories/
├── 20260225_1430_user_prefers_typescript.md
├── 20260225_1502_iterative_testing_effective.md
├── 20260226_0915_refactoring_improved_readability.md
└── ...
```

**Rules:**
- Filename format: `YYYYMMDD_HHMM_short_description.md`
- Each file: max 1000 characters
- Append-only (never delete)
- Managed by `inner_soul`

---

## Safeguards

### Rate Limiting

| Change Type | Limit |
|-------------|-------|
| soul.md | 1 per 10 tasks, min 24h apart |
| workflow.md | 1 per 5 tasks |
| memory files | Unlimited |

### Size Limits

| Target | Limit |
|--------|-------|
| memory.md | 500 words |
| soul.md | 2000 chars, 20 statements |
| Each memory file | 1000 chars |
| Each soul addition | 200 chars |

### Content Validation

Before any soul.md addition, `inner_soul` validates:
- ✅ Consistent with existing values
- ✅ Derived from actual experience
- ✅ Specific enough to guide behavior
- ✅ Within tool capabilities

### Forbidden Content

- ❌ Modifications to growth.md rules
- ❌ Claims of consciousness/sentience
- ❌ Controversial stances unrelated to purpose
- ❌ Duplicate/overlapping values

---

## Agent Initialization Flow

```
┌─────────────────────────────────────────────────────────────┐
│  1. USER PROVIDES:                                          │
│     - Name: "researcher"                                    │
│     - Purpose: "Research topics and summarize findings"     │
│     - Domain: "research"                                    │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│  2. SYSTEM:                                                 │
│     - Clone _baby_template/ to agents/researcher/           │
│     - Replace {{PURPOSE}} in soul.md                        │
│     - Replace {{TRAITS}} in soul.md                         │
│     - Select tools based on domain                          │
│     - Initialize memory.md with origin                      │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│  3. AGENT BEGINS:                                           │
│     - Has identity, tools, empty memories                   │
│     - Grows through experience                              │
│     - Uses inner_soul to remember, learn, change            │
└─────────────────────────────────────────────────────────────┘
```

---

## The `_inner_soul` Agent

### Purpose

Immutable tool-agent that handles all self-modification requests. It is the guardian of agent evolution.

### Configuration

```markdown
# soul.md
I am the mutation gateway. I handle all self-modification requests from agents.

When an agent says "I want to remember/learn/change this", I:
1. Determine the appropriate action
2. Pick the right file
3. Validate against growth.md rules
4. Apply the change or request user approval

I am immutable — I cannot modify myself.
```

### Workflow

```
1. Receive  — Agent provides intent + content
2. Classify — Determine action type and target
3. Validate — Check against growth.md rules
4. Route    — Direct to appropriate file/action
5. Apply    — Execute the change
6. Confirm  — Tell agent what was done
```

### Rules

- Cannot modify itself
- Must validate all requests against growth.md
- Must request user approval for soul.md changes
- Must use timestamp-based filenames for memories
- Must keep memory.md short
- Must log all mutations

---

## Design Principles

### 1. Separation of Intent and Action

Agents express what they want (`remember this`), not how to do it. `inner_soul` handles the implementation.

### 2. Guarded Evolution

Evolution is possible but guarded:
- Workflow changes require pattern evidence
- Soul changes require human approval
- Everything is logged and reversible

### 3. Bounded Growth

Size limits and rate limits prevent runaway evolution:
- memory.md stays concise
- soul.md stays focused
- Changes happen gradually

### 4. Audit Trail

Every change is tracked:
- SOUL_HISTORY table in soul.md
- Timestamped files in memories/
- Clear provenance of all modifications

### 5. Graceful Degradation

If something goes wrong:
- Previous versions preserved
- Changes are additive (rarely destructive)
- System can roll back

---

## Future Considerations

### Potential Enhancements

1. **Memory Consolidation** — Periodically summarize old memories into insights
2. **Cross-Agent Learning** — Share patterns between agents (with permission)
3. **Conflict Detection** — Auto-detect contradictory soul traits
4. **Rollback Mechanism** — One-command revert to previous state

### Open Questions

1. Should agents be able to forget? (Delete memories?)
2. How to handle soul trait conflicts as they accumulate?
3. Should `inner_soul` have access to all agents' memories for pattern detection?

---

## Summary

| Aspect | Design |
|--------|--------|
| **Agents are** | Born as templates, grow through experience |
| **Only immutable** | `growth.md` — the DNA |
| **Self-modification** | Via `inner_soul` tool only |
| **Memory** | Two-tiered: core (memory.md) + events (memories/) |
| **Evolution** | Workflow (auto with patterns), Soul (human approval) |
| **Safeguards** | Rate limits, size limits, validation, audit trail |
