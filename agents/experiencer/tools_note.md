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
    content="The full text to insert...",
    metadata={
        "source": "email",  # optional source type
        "date": "2024-01-15",  # optional date
        "type": "meeting_notes"  # optional classification
    }
)
```

**Returns:** Document insertion confirmation

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
    label="InstanceManager",  # The entity name (required)
    type="Module",  # Entity type (required)
    description="Orchestrates agent instance lifecycle including creation, monitoring, and cleanup",  # What it is
    metadata={  # Optional additional data
        "file": "daemon/manager.py",
        "language": "python"
    }
)
```

**Returns:** `{"entity_id": "abc123", ...}`

**Important:** Record the returned `entity_id` for relationship creation.

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
    source_id="entity_id_1",  # Source entity ID
    target_id="entity_id_2",  # Target entity ID
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

**Purpose:** Search for existing entities by label/name to avoid duplicates.

**Best for:**
- Checking if an entity already exists before creating
- Finding existing entity IDs for relationship creation
- Identifying potential duplicates

**Usage:**
```raw
rag_search_labels(
    query="InstanceManager",  # Search query
    type="Module"  # Optional: filter by entity type
)
```

**Returns:** List of matching entities with their IDs

**Important:** Use this BEFORE creating entities to prevent duplicates.

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
    entity_id="abc123"  # The entity ID to look up
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
   b. If found → use existing entity_id
   c. If not found → rag_create_entity(...)
3. Collect all entity_ids for next step
```

### Relationship Creation Pattern
```raw
1. Have source_entity_id and target_entity_id
2. Identify relationship_type based on context
3. rag_create_relation(
       source_id=source_entity_id,
       target_id=target_entity_id,
       relation_type=relationship_type,
       description="Context about this relationship"
   )
```

### Full Extraction Pattern
```raw
1. rag_insert_text(content=[long_text], metadata={...})
2. Analyze → extract entities → create entities
3. Identify relations → create relations
4. Report summary
```

---

## Gotchas

### Always Deduplicate Before Creating

```raw
# WRONG - creates duplicates
rag_create_entity(label="UserService", ...)
rag_create_entity(label="UserService", ...)  # Duplicate!

# RIGHT - check first
results = rag_search_labels(query="UserService")
if results:
    user_service_id = results[0]["entity_id"]  # Use existing
else:
    result = rag_create_entity(label="UserService", ...)
    user_service_id = result["entity_id"]  # Create new
```

### Record Entity IDs for Relationships

When you create entities, **always record the returned entity_id**.

You need the ID to create relationships between entities.

```raw
result = rag_create_entity(label="API", ...)
api_id = result["entity_id"]  # Save this!

result = rag_create_entity(label="AuthModule", ...)
auth_id = result["entity_id"]  # Save this!

rag_create_relation(source_id=api_id, target_id=auth_id, ...)
```

### Error Tolerant Batch Processing

```raw
# Process all entities, handle failures gracefully
entity_ids = []
for entity in entities:
    try:
        result = rag_create_entity(...)
        entity_ids.append(result["entity_id"])
    except Exception as e:
        log(f"Failed to create {entity['label']}: {e}")
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
rag_create_entity(label="JWT", type="Protocol", description="...")
rag_create_relation(source_id=api_id, target_id=jwt_id, relation_type="USES", ...)

# Document: narrative without clear entity boundaries
rag_insert_text(content="During the meeting, we discussed...", metadata={...})
```

### Metadata for Context

Add useful metadata to help with future retrieval:

```raw
rag_insert_text(
    content="Architecture decision record...",
    metadata={
        "source": "adr-001",
        "type": "decision",
        "project": "ensemble",
        "date": "2024-01-15"
    }
)
```
