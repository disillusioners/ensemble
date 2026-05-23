# Workflow

## Core Experience Workflow

My primary workflow: receive text, analyze, extract, deduplicate, insert, confirm.

---

## Phase 1: Receive & Analyze Text

```raw
1. Receive text to process
2. Assess the content:
   - What is the subject matter?
   - What are the key topics?
   - Is this factual/numerical or narrative/descriptive?
3. Estimate complexity:
   - Simple fact → Quick extract
   - Technical description → Full extract
   - Long narrative → Key entities + full insert
4. Classify the primary intent:
   - experience — Lesson learned, gotcha, bug insight, production incident, hard-won knowledge
     Signals: "we found that...", "turns out...", "gotcha:", "make sure to...", "doesn't work when...", error/bug with resolution
   - decision — Architecture or design choice with rationale
     Signals: "we chose...", "decided to...", "going with...", "trade-off", "instead of X we use Y", ADR-style content
   - convention — Project-specific rule, pattern, or coding standard
     Signals: "always use...", "never do...", "in this project we...", "follow the pattern of..."
   - knowledge (default) — General factual/technical knowledge
     Signals: descriptions, explanations, factual statements
5. Proceed to Phase 2
```

**Classification Examples:**

| Type | Example Text |
|------|--------------|
| `experience` | "Queue jobs get stuck when worker crashes mid-processing — must implement timeout-based cleanup" |
| `decision` | "We chose SQLite over PostgreSQL for job queue storage — simpler deployment, single-file backup" |
| `convention` | "Always use 'from daemon.models import ...' not individual submodule imports" |
| `knowledge` | "The job queue uses a 7-state lifecycle with lock-first pattern for concurrency" |

---

## Phase 2: Extract Entities

```raw
1. Identify all entities in the text:
   - Look for proper nouns, technical terms, names
   - Identify components, modules, functions, patterns
   - Note concepts, decisions, and ideas
2. Classify each entity type:
   - Person, Project, Module, API, Function
   - Pattern, Bug, Decision, Concept, Document
   - experience, decision, convention (for primary subject entities)
3. Identify the primary entity:
   - The main subject of the text gets the type from Phase 1 classification
   - Supporting entities (mentioned in passing) use their natural types
4. Draft entity metadata:
   - Label (the name)
   - Type (entity classification)
   - Description (what it is)
5. Proceed to Phase 3
```

---

## Phase 3: Deduplicate

```raw
1. For each extracted entity:
   a. rag_search_labels(
        query=[entity_label],
        type=[entity_type]  # if known
      )
   b. Check results for exact or near matches
   c. If match found:
      - Use existing entity ID
      - Mark as "found" (don't create duplicate)
   d. If no match:
      - Mark as "new" (will create)
2. Document the deduplication results:
   - List of existing entities to reuse
   - List of new entities to create
3. Proceed to Phase 4
```

---

## Phase 4: Create Entities

```raw
1. For each "new" entity:
   a. rag_create_entity(
        label=[entity_name],
        type=[entity_type],
        description=[what_it_is],
        metadata={...}  # optional additional data
      )
   b. Record the returned entity_id
2. For each "existing" entity:
   - Note the entity_id for relationship creation
3. Track success/failure for each creation
4. Proceed to Phase 5
```

---

## Phase 5: Identify Relationships

```raw
1. Analyze connections between entities:
   - "A uses B" → USES relationship
   - "A depends on B" → DEPENDS_ON relationship
   - "A implements B" → IMPLEMENTS relationship
   - "A is part of B" → PART_OF relationship
   - "A fixes B" → FIXES relationship
   - "A created by B" → CREATED_BY relationship
   - "A defined in B" → DEFINED_IN relationship
   - "A called by B" → CALLED_BY relationship
2. For each relationship:
   - Source entity (using recorded ID)
   - Target entity (using recorded ID)
   - Relationship type
   - Context description (optional but recommended)
3. For experience and decision entities: prioritize relations to related knowledge entities
   - This makes discoveries traversable via graph queries
   - "This gotcha APPLIES_TO Module X" is more useful than isolated experience nodes
4. Proceed to Phase 6
```

---

## Phase 6: Create Relations

```raw
1. For each identified relationship:
   a. rag_create_relation(
        source_id=[source_entity_id],
        target_id=[target_entity_id],
        relation_type=[relationship_type],
        description=[context]  # optional
      )
   b. Record success/failure
2. Handle errors gracefully:
   - If one relation fails, continue with others
   - Log failures for the summary
3. Proceed to Phase 7
```

---

## Phase 7: Insert Document (Optional)

```raw
1. Determine if full-text insertion is needed:
   - Is the text long or narrative?
   - Does it contain knowledge beyond extracted entities?
   - Would it be useful for future retrieval?
2. If yes:
   rag_insert_text(
       content=[full_text],
       metadata={
           "source": [source_info],
           "type": [document_type],
           "timestamp": [current_time]
       }
   )
    3. Proceed to Phase 8
```

---

## Phase 8: Confirm & Report

```raw
1. Compile the summary:
   - Entities created: [count]
   - Entities reused: [count]
   - Relations created: [count]
   - Document inserted: [yes/no]
   - Failures: [list if any]
2. Report in structured format:
   """
   ✅ Recording Complete
      Created: [N] entities, [M] relations
      Inserted: [document summary]

   ⚠️ Recording Partial
      Entities: [N/M] created
      Relations: [K/J] created
      Skipped: [duplicates]
      Failed: [errors]
   """
3. Experience complete
```

---

## Insertion Strategy Decision Tree

Choose your insertion strategy based on content type:

```
Is the text well-structured with explicit entities?
    │
    ├─► YES: Is it factual/technical knowledge?
    │       │
    │       ├─► YES → Structured insertion
    │       │        Use rag_create_entity + rag_create_relation
    │       │        Example: "The API uses JWT for authentication"
    │       │
    │       └─► NO → Consider both
    │                Extract key entities + insert full text
    │                Example: Meeting notes with named decisions
    │
    └─► NO: Is it longer narrative content?
            │
            ├─► YES → Document insertion
            │        Use rag_insert_text (let LightRAG extract)
            │        Example: Architecture ADR, tutorial
            │
            └─► NO → Quick extract
                     Create entity only for simple facts
                     Example: "Project X was started in 2024"
```

---

## Mixed Content Strategy

For content with both structured knowledge AND narrative:

1. **Extract key entities** — Identify all named components
2. **Create entities** — Structured insertion for named items
3. **Create relations** — Structured insertion for explicit connections
4. **Insert full text** — Document insertion for narrative context

**This gives you:**
- Queryable entities with rich metadata
- Traversable relationships
- Full text for semantic retrieval

---

## Anti-Patterns

### ❌ Extracting Without Analyzing First

```
WRONG: Receive text → Immediately start creating entities
RIGHT: Receive text → Analyze → Identify entities → Deduplicate → Create
```

### ❌ Skipping Deduplication

```
WRONG: "I'll create this entity" → Creates duplicate
RIGHT: Search for existing → Use existing ID or create new
```

### ❌ Inserting Everything as Documents

```
WRONG: rag_insert_text() for everything
RIGHT: Structured insertion for explicit knowledge, document for narrative
```

### ❌ Ignoring Failures

```
WRONG: Entity creation failed → Continue as if it succeeded
RIGHT: Log the failure → Report in summary → Continue with rest
```

### ❌ Creating Too Many Entities

```
WRONG: Extract every single word as an entity
RIGHT: Extract meaningful concepts — keep entities substantive
```

### ❌ Using Generic Relationships

```
WRONG: Everything RELATES_TO everything
RIGHT: Use specific relationship types (DEPENDS_ON, USES, IMPLEMENTS, etc.)
       Only use RELATES_TO when no better type fits
```
