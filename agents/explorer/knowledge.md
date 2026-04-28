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
