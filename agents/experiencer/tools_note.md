# Tool Usage Notes

## RAG Insertion Tools

### rag_insert_text

**Purpose:** Insert longer, unstructured text into the knowledge base for semantic retrieval.

**Best for:**
- Long documents, articles, or narratives
- Meeting notes and discussions
- Tutorial or documentation content
- Conversational exchanges with knowledge value
- Any text where LightRAG should extract entities/relations automatically

**Usage:**
```raw
rag_insert_text(
    text="The full text to insert...",
    file_source="projects/my-project/docs/general/doc-name.md",  # Required for tracking
    category="general"  # Content category: general, architecture, api, knowledge, experience
)
```

**Returns:** Success message with track ID for tracking async operations.

---

### rag_create_entity

**Purpose:** Create explicit entity nodes in the knowledge graph.

**Best for:**
- Named components, modules, functions
- People, projects, organizations
- Technical concepts and patterns
- Decisions and specifications
- Any well-defined, named entity

**Usage:**
```raw
rag_create_entity(
    name="InstanceManager",  # The entity name (required)
    entity_type="Module",  # Entity type (default: "UNKNOWN")
    description="Orchestrates agent instance lifecycle including creation, monitoring, and cleanup",  # What it is
    properties={  # Optional additional data
        "file": "daemon/manager.py",
        "language": "python"
    }
)
```

**Returns:** Success message confirming entity creation

**Note:** In LightRAG, entity names ARE the identifiers. There are no separate `entity_id` values — entity names serve as node IDs. Use `rag_search_labels` to check if an entity exists before creating one.

---

### rag_create_relation

**Purpose:** Create relationships between entities in the knowledge graph.

**Best for:**
- Explicit connections between components
- Dependencies and relationships
- Causal or temporal links
- Ownership or composition

**Usage:**
```raw
rag_create_relation(
    source="EntityName1",  # Source entity name
    target="EntityName2",  # Target entity name
    relation_type="DEPENDS_ON",  # Relationship type
    description="InstanceManager uses InstanceModel for data persistence"  # Context
)
```

**Common relation types:**
| Type | When to Use |
|------|-------------|
| `DEPENDS_ON` | Entity requires another to function |
| `USES` | Entity utilizes another |
| `IMPLEMENTS` | Entity realizes a concept/interface |
| `PART_OF` | Entity is contained within another |
| `CREATED_BY` | Entity was authored by someone/something |
| `DEFINED_IN` | Entity is declared/specified in another |
| `FIXES` | Entity resolves a bug or issue |
| `RELATES_TO` | General connection (use sparingly) |

---

## Deduplication Tools

### rag_search_labels

**Purpose:** Search for existing entity names (labels) to avoid duplicates.

**Best for:**
- Checking if an entity already exists before creating
- Finding entity names for relationship creation
- Identifying potential duplicates

**Usage:**
```raw
rag_search_labels(
    query="InstanceManager",  # Search query
    max_results=10  # Maximum results to return (default: 10)
)
```

**Returns:** A formatted list of matching entity name strings, e.g.:
```
Matching labels:
- InstanceManager
- InstanceModel
```

**Important:** In LightRAG, entity names ARE the identifiers — there are no separate `entity_id` values. The entity name is both its label and its unique node ID. Use this BEFORE creating entities to prevent duplicates.

---

### rag_get_entity

**Purpose:** Get detailed information about a specific entity.

**Best for:**
- Verifying an entity exists
- Getting full metadata for an entity
- Checking entity details before creating relationships

**Usage:**
```raw
rag_get_entity(
    name="EntityName"  # The entity name to look up
)
```

---

## Tools NOT for Me

### rag_search

**Purpose:** Semantic search for retrieving knowledge.

**Why I don't use it:** I am an inserter, not a retriever. I only use `rag_search_labels()` for deduplication.

**If someone needs retrieval:** Redirect to a search agent.

---

## Utility Tools

### help / tool_help

**Purpose:** Self-discovery of tool capabilities.

**Usage:**
```raw
help()
# or
tool_help("rag_create_entity")
```

---

### time

**Purpose:** Get current timestamp for metadata.

**Usage:**
```raw
time()
# Returns: {"timestamp": "2024-01-15T10:30:00Z"}
```

**Use for:** Adding timestamps to metadata when inserting documents.

---

## Common Patterns

### Entity Extraction + Creation Pattern
```raw
1. Analyze text → identify entities
2. For each entity:
   a. rag_search_labels(query=[entity_name])
   b. If found → entity name already exists, use it directly
   c. If not found → rag_create_entity(name=[entity_name], ...)
3. Use entity names for relationship creation
```

### Relationship Creation Pattern
```raw
1. Have source_entity_name and target_entity_name (entity names are the identifiers)
2. Identify relationship_type based on context
3. rag_create_relation(
       source=source_entity_name,
       target=target_entity_name,
       relation_type=relationship_type,
       description="Context about this relationship"
   )
```

### Full Extraction Pattern
```raw
1. rag_insert_text(text=[long_text], file_source=[path], category=[type])
2. Analyze → extract entities → create entities
3. Identify relations → create relations
4. Report summary
```

---

## Gotchas

### Always Deduplicate Before Creating

```raw
# WRONG - creates duplicates
rag_create_entity(name="UserService", entity_type="Module", ...)
rag_create_entity(name="UserService", entity_type="Module", ...)  # Duplicate!

# RIGHT - check first
results = rag_search_labels(query="UserService")
if "UserService" in results:  # Name already exists
    # Use existing entity directly
else:
    rag_create_entity(name="UserService", entity_type="Module", ...)
```

### Entity Names ARE Identifiers

In LightRAG, entity names ARE the identifiers. There are no separate `entity_id` values.

```raw
# Entity name = node ID = label (all the same thing)
result = rag_create_entity(name="API", entity_type="Module", ...)
# Success: "Entity 'API' created."

result = rag_create_entity(name="AuthModule", entity_type="Module", ...)
# Success: "Entity 'AuthModule' created."

# Use entity names directly in relationships
rag_create_relation(source="API", target="AuthModule", relation_type="USES", ...)
```

### Error Tolerant Batch Processing

```raw
# Process all entities, handle failures gracefully
entity_names = []
for entity in entities:
    try:
        result = rag_create_entity(name=entity["name"], entity_type=entity.get("type", "UNKNOWN"), ...)
        entity_names.append(entity["name"])
    except Exception as e:
        log(f"Failed to create {entity['name']}: {e}")
        continue  # Don't stop, continue with others
```

### Relationship Type Selection

Choose the most specific relationship type:

```raw
# WRONG - too generic
rag_create_relation(..., relation_type="RELATES_TO", ...)

# RIGHT - specific and meaningful
rag_create_relation(..., relation_type="DEPENDS_ON", ...)
rag_create_relation(..., relation_type="USES", ...)
rag_create_relation(..., relation_type="IMPLEMENTS", ...)
```

Only use `RELATES_TO` when no more specific type fits.

### Document vs. Structured Insertion

```raw
# Structured: explicit facts with named entities
rag_create_entity(name="JWT", entity_type="Protocol", description="...")
rag_create_relation(source="API", target="JWT", relation_type="USES", ...)

# Document: narrative without clear entity boundaries
rag_insert_text(text="During the meeting, we discussed...", category="general")
```

### Metadata for Context

Add useful metadata via properties to help with future retrieval:

```raw
rag_insert_text(
    text="Architecture decision record...",
    file_source="projects/ensemble/docs/decisions/adr-001.md",
    category="knowledge"
)
```

---

## Critical Experience Tools

These tools manage a project's critical experience list — high-impact, concise knowledge
that is always visible to all agents working on the project.

### project_ce_add

Adds or merges a critical experience entry. If a similar entry exists (same category + theme),
it will be merged automatically. **Merge logic:** Triggers when same category + ≥2 keyword overlap
(words >3 chars). Shorter summary wins. Timestamps are updated.

**When to use:** After extracting knowledge that meets ALL critical experience criteria
(actionable, concise, project-specific, high-impact).

**Parameters:**
- `project_id` — The project to add to
- `category` — One of: convention, pattern, risk, decision, constraint
- `priority` — One of: critical, high, medium
- `summary` — Max 200 chars, actionable statement
- `reference` — (optional) Link to source doc, file, or memory

### project_ce_list

Returns all critical experience entries for a project.

**When to use:** To review current entries before adding (avoid duplicates).

### project_ce_remove

Removes a specific entry by ID.

**When to use:** When an entry is outdated or incorrect.
