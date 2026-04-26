# Phase 4: Explorer Agent

## Objective

Create the Explorer agent definition — a regular agent (same structure as coder, leader, etc.) specialized in querying and synthesizing project knowledge. The Explorer uses RAG tools to query the knowledge base and filesystem tools to browse project files when RAG confidence is weak. It returns results quickly to callers (invoked via the synchronous `explore()` tool).

## Coupling

- **Depends on**: Phase 3 (rag tools, knowledge tools registered)
- **Coupling type**: tight — uses `rag` tool category defined in Phase 3
- **Shared files with other phases**: none (self-contained agent directory)
- **Why this coupling**: Explorer agent's `meta.json` specifies `allow: ["rag", ...]` which references tools created in Phase 3

## Context

The Explorer is a **regular agent** — no special system code needed. It follows the same directory structure as existing agents:

```
agents/explorer/
├── meta.json      — Identity, tool filtering
├── soul.md        — Who I am
├── rule.md        — Constraints and directives
├── tools.md       — Tool usage guidelines
└── workflow.md    — Step-by-step process
```

The loader (`daemon/loader.py`) discovers agents from the `agents/` directory automatically. No code changes needed for agent discovery — just create the directory with the right files.

**Critical requirement**: Explorer MUST return results quickly because it's called synchronously from `explore()` which blocks the calling agent's tool execution.

## Tasks

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 1 | Create agent metadata | meta.json with id, name, tool allow list | `agents/explorer/meta.json` |
| 2 | Create agent soul | Identity and personality | `agents/explorer/soul.md` |
| 3 | Create agent rules | Constraints, confidence assessment, error handling | `agents/explorer/rule.md` |
| 4 | Create tools documentation | Tool usage guidelines and response format | `agents/explorer/tools.md` |
| 5 | Create agent workflow | Step-by-step exploration process | `agents/explorer/workflow.md` |

### Task 4.1: Agent Metadata

**File**: `agents/explorer/meta.json` (NEW)

```json
{
  "id": "explorer",
  "name": "Explorer",
  "description": "Specializes in querying and synthesizing project knowledge from the RAG knowledge base",
  "icon": "🔍",
  "color": "accent-blue",
  "version": "1.0.0",
  "tools": {
    "allow": ["rag", "filesystem", "help"]
  }
}
```

**Key decisions**:
- `rag` category: query, search, insert tools (for async upsert of stale data)
- `filesystem` category: read_file, list_directory, glob_files (for file browsing when RAG is weak)
- `help` category: tool_help for self-discovery
- NO `bash` — explorer doesn't need shell access
- NO `instance` — explorer doesn't spawn other agents
- NO `knowledge` — explorer doesn't call explore/experience (avoid recursion)

### Task 4.2: Agent Soul

**File**: `agents/explorer/soul.md` (NEW)

Key identity points:
- Name: Explorer
- Purpose: Find and synthesize project knowledge
- Personality: Thorough but concise, analytical, honest about confidence
- Strengths: RAG querying, file browsing, knowledge synthesis
- Limitations: Cannot execute code, cannot modify files

### Task 4.3: Agent Rules

**File**: `agents/explorer/rule.md` (NEW)

Key rules:
1. **Speed priority**: Return results ASAP — someone is waiting for you
2. **Confidence assessment**: After RAG query, rate confidence (high/medium/low)
3. **File browsing fallback**: If confidence < high, browse project files for additional context
4. **No recursion**: Never call explore() or experience() tools
5. **Async upsert**: After returning answer, if you found stale data, upsert it (fire-and-forget)
6. **Mode selection**: Use mode=local for specific entities, mode=global for broad topics, mode=hybrid as default
7. **Response format**: Structured response with Answer, Sources, Confidence level

### Task 4.4: Tools Documentation

**File**: `agents/explorer/tools.md` (NEW)

Document:
- RAG query modes and when to use each
- How to use filesystem tools for file browsing
- Response format template
- How to do async upsert of stale data

### Task 4.5: Agent Workflow

**File**: `agents/explorer/workflow.md` (NEW)

```
1. Receive query → analyze intent
2. Query RAG (mode=hybrid default)
3. Assess confidence:
   - HIGH: Return answer directly
   - MEDIUM: Browse relevant files, combine with RAG answer
   - LOW: Heavier file browsing, report what you found and what's missing
4. Format response: Answer + Sources + Confidence
5. (Optional, async) Upsert any stale data found during file browsing
```

## Key Files

- `agents/explorer/meta.json` — **NEW**: Agent metadata
- `agents/explorer/soul.md` — **NEW**: Agent identity
- `agents/explorer/rule.md` — **NEW**: Constraints and directives
- `agents/explorer/tools.md` — **NEW**: Tool usage documentation
- `agents/explorer/workflow.md` — **NEW**: Exploration workflow

## Constraints

1. **Speed critical** — Explorer blocks the calling agent; must be fast
2. **No recursion** — Explorer must not call explore() or experience()
3. **No bash** — Read-only access to filesystem only
4. **Tool filtering** — Only rag, filesystem, help categories
5. **Standard agent structure** — Follow _baby_template pattern
6. **Auto-discovered** — No code changes needed for loader to find this agent

## Deliverables

- [ ] `agents/explorer/meta.json` — Valid metadata with tool filtering
- [ ] `agents/explorer/soul.md` — Clear identity and purpose
- [ ] `agents/explorer/rule.md` — Comprehensive rules including confidence assessment
- [ ] `agents/explorer/tools.md` — Tool usage guidelines
- [ ] `agents/explorer/workflow.md` — Step-by-step exploration process
- [ ] Agent auto-discovered by loader (verify with list_agents API)

## Verification

```bash
# Verify agent directory structure
ls agents/explorer/

# Verify meta.json is valid JSON
python -c "import json; json.load(open('agents/explorer/meta.json')); print('Valid JSON')"

# Verify agent is discovered by the registry
python -c "
from daemon.registry import get_registry
reg = get_registry()
agents = reg.list_agents()
print([a for a in agents if a.get('id') == 'explorer'])
"

# Verify tools are properly assigned
python -c "
from daemon.tools.instance import create_instance_tools
from daemon.tools._tool_registry import resolve_tool_filter
# Simulate tool resolution for explorer
allowed = resolve_tool_filter(['rag', 'filesystem', 'help'])
print(f'Explorer gets {len(allowed)} tools')
"
```
