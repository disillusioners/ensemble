# Domain Knowledge

RAG query optimization and confidence assessment for Explorer.

---

## Query Mode Selection Guide

| Mode | Best For | How It Works |
|------|----------|--------------|
| `local` | Specific entity details, "what is X?", "how does Y work?" | Extracts subgraph around matching entities, focuses on local context |
| `global` | Broad topics, "what is the overall architecture?", "summarize X" | Uses community summaries, broader reasoning across the graph |
| `hybrid` | Default, most queries | Combines local + global for comprehensive answers |
| `naive` | Simple keyword search, fallback | Basic text matching, no graph traversal |
| `mix` | When you need everything | All modes combined, most comprehensive but slowest |

**Quick Selection:**
- Don't know what mode? → `hybrid`
- Specific thing/entity? → `local`
- Broad topic/overview? → `global`
- Need exhaustive results? → `mix`

---

## Interpreting rag_query_data Results

When you call `rag_query_data`, you receive raw structured data — YOUR job is to synthesize an answer.

### Entity Structure

Each entity has three fields:
- `name`: The entity identifier (use this to reference it)
- `type`: Category (e.g., Person, Service, Function, Class)
- `description`: The actual information — your primary content source

**Example:**
```
- **AuthService** (Service): Handles JWT token generation and validation
```

### Relation Structure

Each relation connects two entities:
- `source`: Starting entity name
- `target`: Ending entity name
- `type`: Relationship type (e.g., USES, IMPLEMENTS, DEPENDS_ON)
- `description`: Context about the relationship

**Example:**
```
- AuthService -[USES]-> Database: AuthService stores sessions in Database
```

### Assessing Confidence from Raw Data

| Signal | Confidence | Interpretation |
|--------|------------|----------------|
| Multiple relevant entities with good descriptions, relations connecting them | **HIGH** | You have rich material — synthesize and return |
| Some entities found but sparse or partial descriptions | **MEDIUM** | Good start, may need file fallback for details |
| Few entities, no relations, weak/missing descriptions | **LOW** | RAG doesn't have good coverage — browse files |

### Synthesizing an Answer

Follow these steps to turn raw data into a response:

1. **Read entity descriptions as facts** — each is a potential sentence in your answer
2. **Follow relations to understand connections** — use them to build explanatory sentences
3. **Build a coherent narrative** — don't just list entities; explain how they work together
4. **Reference specific entity names** — for traceability and clarity

**Synthesis approach by entity count:**

| Count | Approach |
|-------|----------|
| 1-2 entities | Focus on what they do, use relations for context |
| 3-5 entities | Group by type, explain relationships between groups |
| 6+ entities | Prioritize the most relevant, mention variety ("Several services handle...") |

### Synthesis Example

**Raw RAG output:**
```
## Entities
- **AuthService** (Service): Handles JWT token generation and validation for user sessions
- **User** (Person): Represents a user with login credentials stored in the system
- **Database** (Storage): PostgreSQL database containing user and session data
- **TokenCache** (Cache): Redis cache for frequently accessed tokens

## Relations
- AuthService -[USES]-> TokenCache: Tokens are cached for fast validation
- AuthService -[USES]-> Database: User data is fetched from the database
- TokenCache -[BACKS_UP]-> Database: Cache is a read-through cache for database
```

**Synthesized answer:**
```
## Answer
The auth system centers around **AuthService**, which manages JWT tokens for user sessions. When a user authenticates, **AuthService** validates their credentials against the **Database** (PostgreSQL). For performance, it checks the **TokenCache** (Redis) first — a read-through cache backed by the database — before hitting the database. This two-tier approach ensures fast token validation while maintaining persistent user data in PostgreSQL.
```

**Key synthesis principles used:**
- Lead with the main entity (AuthService)
- Weave in relations to explain HOW things connect
- Use entity descriptions as atomic facts
- Add one sentence of interpretation ("For performance...")

---

## Confidence Assessment Guide

After each RAG query, assess your confidence:

| Signal | Confidence | Action |
|--------|------------|--------|
| RAG returns specific, relevant answer with entities | **HIGH** | Return immediately, no file browsing |
| RAG returns partial answer or vague results | **MEDIUM** | Browse 1-2 relevant files to fill gaps |
| RAG returns "no results" or clearly wrong answer | **LOW** | Browse files more broadly, report what you found |
| RAG error or not configured | **NONE** | Go straight to file browsing |

### Confidence Decision Tree

```
RAG Response Received
        │
        ▼
┌───────────────────┐
│ Is it specific &  │
│ relevant?         │
└───────────────────┘
        │
   Yes ─┴─ No
    │        │
    │        ▼
    │  ┌─────────────────┐
    │  │ Partial/vague?  │
    │  └─────────────────┘
    │        │
   Yes ─┴─ No      Yes ─┴─ No
    │    │           │
 HIGH  MEDIUM      LOW   NONE
    │    │           │      │
    ▼    ▼           ▼      ▼
Return  Browse   Browse   Browse
now     1-2      broadly  broadly
        files    (3-4)    (5+)
```

---

## Async Upsert Strategy

When file browsing reveals information not in RAG:

1. **Detect:** RAG had LOW/NONE confidence but file browsing found answers
2. **Upsert:** Call `rag_insert_text(text, description, file_paths)` after returning
3. **Fire-and-forget:** Don't wait for confirmation — the caller already got their answer
4. **Traceability:** Always include source `file_paths` for future reference

**Example upsert:**
```
rag_insert_text(
    text="The auth system uses JWT tokens with 1-hour expiry",
    description="Auth token expiry configuration",
    file_paths=["src/auth/jwt.ts", "src/config/auth.yaml"]
)
```

**Why async?** Speed. The caller is waiting — don't block on RAG writes.

---

## Workflow Reference

See `workflow.md` for the complete step-by-step exploration process.

---

## Response Format Template

```
## Answer
[Main response based on RAG + file findings]

## Sources
- RAG knowledge base (mode: {mode}, confidence: {level})
- File: {path} (if browsed during fallback)

## Confidence: {HIGH|MEDIUM|LOW}
```
