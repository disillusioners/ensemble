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
