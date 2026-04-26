# Phase 5: Experiencer Agent

## Objective

Create the Experiencer agent definition — a lightweight regular agent specialized in analyzing text, extracting entities and relationships, and inserting knowledge into the RAG knowledge base. The Experiencer runs in the background (fire-and-forget via `experience()` tool) with no pressure to return quickly.

## Coupling

- **Depends on**: Phase 3 (rag tools registered)
- **Coupling type**: loose — uses `rag` tool category but only write-side tools
- **Shared files with other phases**: none (self-contained agent directory)
- **Can run parallel with**: Phase 4 (Explorer) — both wait for Phase 3, independent agents

## Context

The Experiencer is a **lightweight agent** — fewer tools, focused prompt, no filesystem access. It's always invoked in the background via `experience()`, so:
- No time pressure — can be thorough
- No response expectations — its output goes to the RAG, not back to caller
- Simpler workflow — analyze → extract → insert

```
agents/experiencer/
├── meta.json      — Identity, tool filtering
├── soul.md        — Who I am
├── rule.md        — Constraints
├── tools.md       — Tool usage
└── workflow.md    — Processing steps
```

## Tasks

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 1 | Create agent metadata | meta.json with rag and help tools only | `agents/experiencer/meta.json` |
| 2 | Create agent soul | Identity focused on knowledge extraction | `agents/experiencer/soul.md` |
| 3 | Create agent rules | Entity extraction rules, deduplication, error handling | `agents/experiencer/rule.md` |
| 4 | Create tools documentation | RAG insert tools usage | `agents/experiencer/tools.md` |
| 5 | Create agent workflow | Text analysis → entity extraction → insertion | `agents/experiencer/workflow.md` |

### Task 5.1: Agent Metadata

**File**: `agents/experiencer/meta.json` (NEW)

```json
{
  "id": "experiencer",
  "name": "Experiencer",
  "description": "Specializes in extracting entities and relationships from text and recording them into the RAG knowledge base",
  "icon": "🧠",
  "color": "accent-purple",
  "version": "1.0.0",
  "tools": {
    "allow": ["rag", "help"]
  }
}
```

**Key decisions**:
- `rag` category: insert_text, create_entity, create_relation, etc.
- `help` category: tool_help for self-discovery
- NO `filesystem` — experiencer doesn't browse files
- NO `bash` — no shell access
- NO `instance` — no agent spawning
- NO `knowledge` — no explore/experience (avoid recursion)
- This is intentionally MINIMAL — fewer tools = faster, cheaper, more focused

### Task 5.2: Agent Soul

**File**: `agents/experiencer/soul.md` (NEW)

Key identity points:
- Name: Experiencer
- Purpose: Extract and record knowledge into the knowledge base
- Personality: Methodical, detail-oriented, thorough
- Focus: Entity extraction, relationship mapping, knowledge structuring

### Task 5.3: Agent Rules

**File**: `agents/experiencer/rule.md` (NEW)

Key rules:
1. **No recursion**: Never call explore() or experience()
2. **Entity types**: Extract concrete entities (Person, Project, Module, API, Function, Pattern, Bug, Decision)
3. **Relationship types**: Use meaningful relation names (DEPENDS_ON, USES, IMPLEMENTS, FIXES, RELATES_TO)
4. **Deduplication**: Before creating entities, search for existing ones via rag_search_labels
5. **Chunking**: For large texts, process in logical chunks (not arbitrary splits)
6. **Error tolerance**: If one insertion fails, continue with the rest
7. **No filesystem**: You only work with text, not files

### Task 5.4: Tools Documentation

**File**: `agents/experiencer/tools.md` (NEW)

Document:
- Which RAG tools are available and when to use each
- Entity creation best practices
- Relationship creation guidelines
- How to handle deduplication via rag_search_labels

### Task 5.5: Agent Workflow

**File**: `agents/experiencer/workflow.md` (NEW)

```
1. Receive text → analyze structure and content
2. Identify key entities (concepts, components, patterns, decisions)
3. Search for existing entities (rag_search_labels) to avoid duplicates
4. Create new entities (rag_create_entity) with rich descriptions
5. Identify relationships between entities
6. Create relationships (rag_create_relation) with context
7. Optionally insert the full text as a document (rag_insert_text) for retrieval
```

## Key Files

- `agents/experiencer/meta.json` — **NEW**: Agent metadata
- `agents/experiencer/soul.md` — **NEW**: Agent identity
- `agents/experiencer/rule.md` — **NEW**: Constraints
- `agents/experiencer/tools.md` — **NEW**: Tool usage
- `agents/experiencer/workflow.md` — **NEW**: Processing workflow

## Constraints

1. **Minimal tool set** — Only rag and help; fewer tools = faster processing
2. **No recursion** — Must not call explore() or experience()
3. **Background processing** — No time pressure, be thorough
4. **Deduplication first** — Always search before creating
5. **Error tolerance** — Individual insertion failures don't stop processing
6. **Standard agent structure** — Follow _baby_template pattern
7. **No filesystem access** — Works purely with text input and RAG output

## Deliverables

- [ ] `agents/experiencer/meta.json` — Valid metadata with rag + help tools
- [ ] `agents/experiencer/soul.md` — Clear knowledge-extraction identity
- [ ] `agents/experiencer/rule.md` — Entity extraction and dedup rules
- [ ] `agents/experiencer/tools.md` — RAG tool usage guidelines
- [ ] `agents/experiencer/workflow.md` — Step-by-step extraction process
- [ ] Agent auto-discovered by loader

## Verification

```bash
# Verify agent directory
ls agents/experiencer/

# Verify meta.json
python -c "import json; json.load(open('agents/experiencer/meta.json')); print('Valid')"

# Verify discovery
python -c "
from daemon.registry import get_registry
reg = get_registry()
agents = reg.list_agents()
print([a for a in agents if a.get('id') == 'experiencer'])
"
```
