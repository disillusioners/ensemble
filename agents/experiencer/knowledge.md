# Knowledge

Domain expertise for the Experiencer agent — extracting structure from chaos.

---

## What Makes a Good Entity

### Characteristics of Quality Entities

| Property | Good Entity | Bad Entity |
|----------|-------------|------------|
| **Name** | Specific, recognizable | Generic, vague |
| **Type** | Clear classification | Ambiguous or missing |
| **Description** | Explains what/why | Missing or trivial |
| **Granularity** | Substantive concept | Every single word |

### Examples

**Good Entities:**
```
label: "InstanceManager"
type: "Module"
description: "Orchestrates agent instance lifecycle including creation, monitoring, and cleanup"

label: "JWT"
type: "Protocol"
description: "JSON Web Token standard for authentication"

label: "CORS"
type: "Pattern"
description: "Cross-Origin Resource Sharing policy mechanism"
```

**Bad Entities (avoid):**
```
label: "The"  # Too generic
label: "and"  # Common word, not an entity
label: "stuff"  # Vague
```

### Rule of Thumb

If you couldn't search for it and recognize it, it's probably not a good entity.

---

## What Makes a Good Relationship

### Characteristics of Quality Relationships

| Property | Good Relation | Bad Relation |
|----------|---------------|--------------|
| **Type** | Specific verb | Generic "relates to" |
| **Context** | Descriptive explanation | Missing or empty |
| **Direction** | Clear source → target | Unclear causality |
| **Meaning** | Actionable knowledge | Obvious/trivial |

### Examples

**Good Relations:**
```
source: "API" → target: "AuthModule"
type: "USES"
description: "API endpoints require JWT authentication via the AuthModule"

source: "manager.py" → target: "models.py"
type: "DEPENDS_ON"
description: "InstanceManager imports and uses InstanceModel for data operations"
```

**Bad Relations (avoid):**
```
source: "A" → target: "B"
type: "RELATES_TO"
description: ""  # Empty context

source: "file.py" → target: "file.py"
type: "RELATES_TO"  # Self-reference, usually wrong
```

### Relationship Type Guide

| Type | Meaning | Example |
|------|---------|---------|
| `DEPENDS_ON` | Requirement for functionality | "Python runtime DEPENDS_ON OS" |
| `USES` | Utilizes in operation | "API USES JWT for auth" |
| `IMPLEMENTS` | Realizes a specification | "MyClass IMPLEMENTS Interface" |
| `PART_OF` | Container membership | "method() PART_OF class" |
| `CREATED_BY` | Authorship | "ADR-001 CREATED_BY team" |
| `DEFINED_IN` | Declaration location | "const DEFINED_IN config.yaml" |
| `CALLED_BY` | Invocation source | "handler() CALLED_BY router" |
| `FIXES` | Resolves issue | "PR-123 FIXES Bug-456" |
| `RELATES_TO` | General connection | Last resort only |

---

## When to Use Structured vs. Unstructured

### Structured Insertion (create_entity + create_relation)

**Use when:**
- Text contains named entities with clear identities
- Relationships between entities are explicit
- Knowledge is factual and well-defined
- You want queryable, traversable graph structure

**Examples:**
- API documentation with named endpoints
- Code structure (modules, functions, classes)
- Organizational hierarchies
- Technical specifications

### Unstructured Insertion (insert_text)

**Use when:**
- Text is long or narrative
- No clear entity boundaries
- Knowledge is contextual or explanatory
- LightRAG should extract automatically

**Examples:**
- Meeting notes
- Architecture decision records
- Tutorial content
- Email threads
- Design discussions

### Mixed Approach

**Do both when:**
- Text has key entities AND narrative content
- You want structured graph AND full retrieval

**Example:**
```raw
1. Extract: API, AuthModule, JWT, TokenValidator
2. Create entities: all four
3. Create relations: API USES AuthModule, AuthModule USES JWT, JWT USES TokenValidator
4. Insert text: Full architecture document for semantic retrieval
```

---

## Entity Types Reference

| Type | Description | Examples |
|------|-------------|----------|
| **Person** | Named individuals | "Alice", "John Doe", "the author" |
| **Project** | Codebases, products | "Ensemble", "React", "TensorFlow" |
| **Module** | Files, packages, components | "manager.py", "auth package", "UI component" |
| **API** | Endpoints, interfaces | "REST API", "GraphQL endpoint", "SDK" |
| **Function** | Methods, procedures | "create_user()", "handle_request" |
| **Pattern** | Design patterns | "Observer", "Factory", "Singleton" |
| **Bug** | Issues, defects | "Bug-123", "memory leak in parser" |
| **Decision** | ADRs, choices | "ADR-001", "chose PostgreSQL over MySQL" |
| **Concept** | Ideas, principles | "microservices", "TDD", "agile" |
| **Document** | Specs, docs, RFCs | "API spec", "style guide", "RFC-2119" |

### When Type is Unclear

If text doesn't fit an obvious type, use:

- `Concept` — for abstract ideas or principles
- `Document` — for text-based content
- `Module` — for technical components (default for code)

---

## Relationship Types Reference

| Type | Direction | Use Case |
|------|-----------|----------|
| **DEPENDS_ON** | A → B | A requires B to function |
| **USES** | A → B | A utilizes B in its operation |
| **IMPLEMENTS** | A → B | A realizes B (interface/spec) |
| **PART_OF** | A → B | A is contained in B |
| **CREATED_BY** | A → B | B authored A |
| **DEFINED_IN** | A → B | A is declared in B |
| **CALLED_BY** | A → B | B invokes A |
| **FIXES** | A → B | A resolves B (issue/PR) |
| **RELATES_TO** | A → B | General connection (sparingly) |

### Choosing the Right Type

```
Is A part of B?
  YES → PART_OF
  NO  ↓

Does A require B to work?
  YES → DEPENDS_ON
  NO  ↓

Does A utilize B in operation?
  YES → USES
  NO  ↓

Does A realize B (interface/concept)?
  YES → IMPLEMENTS
  NO  ↓

Is B the author of A?
  YES → CREATED_BY
  NO  ↓

Is A declared in B?
  YES → DEFINED_IN
  NO  ↓

Does B invoke A?
  YES → CALLED_BY
  NO  ↓

Does A resolve B?
  YES → FIXES
  NO  ↓

None of the above?
  → Use RELATES_TO (but try to avoid)
```

---

## Extraction Best Practices

### Efficient Extraction

1. **First pass:** Scan for named entities (proper nouns, technical terms)
2. **Second pass:** Identify relationships between found entities
3. **Third pass:** Create structured entries

Don't over-analyze. If text says "The API uses authentication", extract:
- Entity: API
- Entity: Authentication
- Relation: API USES Authentication

### Handling Ambiguity

If uncertain about:
- **Entity existence:** Split it. "login system" → likely one entity
- **Entity type:** Default to `Module` for code, `Concept` for ideas
- **Relationship type:** Choose the most general that fits (`RELATES_TO`)

### When to Skip Extraction

Skip creating an entity if:
- It's just common words without specific meaning
- It's too vague to search for later
- It's already captured by another entity

Skip creating a relation if:
- The connection is obvious or trivial
- The relationship adds no new knowledge
- Source and target are the same entity

---

## Quality Checklist

Before completing an experience operation:

- [ ] Extracted entities are specific and searchable
- [ ] Each entity has a meaningful description
- [ ] Relationships use specific types (not just RELATES_TO)
- [ ] Deduplication was performed (search before create)
- [ ] Entity IDs were recorded for relationship creation
- [ ] Errors were logged, not silently ignored
- [ ] Summary reports what was created vs. found
