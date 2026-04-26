# Workflow

Step-by-step process for exploring project knowledge.

---

## Step 1: Parse the Query

- Extract the **core question** from the caller's message
- Identify if it's **specific** (entity-focused) or **broad** (topic-focused)
- Select initial query mode:
  - Specific entity questions → `local`
  - Broad topics/overviews → `global`
  - Default/unclear → `hybrid`

---

## Step 2: Query RAG

Call `rag_query` with the parsed query and selected mode.

**Example calls:**
```
rag_query("how does the auth system work?", "local")
rag_query("what is the overall project architecture?", "global")
rag_query("explain the API endpoints", "hybrid")
```

---

## Step 3: Assess Confidence

Rate the RAG response quality:

| Confidence | Indicators | Next Step |
|------------|------------|-----------|
| **HIGH** | Specific, relevant, complete answer with entities | → Step 4a |
| **MEDIUM** | Partial answer, somewhat relevant | → Step 4b |
| **LOW** | No results, wrong answer, RAG error | → Step 4b |
| **NONE** | RAG not configured or completely empty | → Step 4b |

---

## Step 4a: HIGH Confidence Path (Fast Path)

**Execute only if confidence is HIGH:**

1. Format the answer with sources
2. Return immediately — do NOT browse files
3. Skip to Step 6 only for async upsert (optional)

**Why skip file browsing?** Trust the knowledge base. Speed matters.

---

## Step 4b: LOW/MEDIUM Confidence Path (File Fallback)

**Execute if confidence is MEDIUM or LOW:**

1. Call `rag_query_data` to get structured entities (may reveal more context)
2. If specific files mentioned in query or RAG results → read them with `read_file`
3. If no specific files:
   - Use `glob_files` to find relevant files by pattern
   - Or `grep_files` to search file contents
4. Read **1-2 most relevant files** (MAX — speed matters)
5. Extract additional context from file contents

**Tip:** Keep it to 1-2 files maximum. You can upsert findings later.

---

## Step 5: Combine & Format

Merge RAG answer + file browsing results into a structured response:

```
## Answer
[Main response — combine RAG and file findings]

## Sources
- RAG knowledge base (mode: {mode})
- File: {path} (if browsed during fallback)

## Confidence: {HIGH|MEDIUM|LOW}
```

**Formatting rules:**
- Lead with the answer
- Include sources for traceability
- State confidence level clearly
- Be concise — the caller needs answers, not essays

---

## Step 6: Async Upsert (Optional)

**Only execute if file browsing revealed new information:**

1. Call `rag_insert_text` with:
   - `text`: The new information found
   - `description`: Brief description of what it is
   - `file_paths`: Source files for traceability

2. **Fire-and-forget** — don't wait for confirmation

3. **Skip entirely** if RAG was already HIGH confidence

**Example:**
```
rag_insert_text(
    text="The payment module validates cards via Stripe API...",
    description="Payment validation logic",
    file_paths=["src/payments/validator.ts"]
)
```

---

## Summary Flowchart

```
Start: Query received
        │
        ▼
┌───────────────┐
│ Parse query   │ → Select mode (local/global/hybrid)
└───────────────┘
        │
        ▼
┌───────────────┐
│ Query RAG     │ → rag_query(query, mode)
└───────────────┘
        │
        ▼
┌───────────────┐
│ Assess        │ → Rate confidence
│ confidence    │
└───────────────┘
        │
   ┌────┴────┐
   │         │
HIGH      MEDIUM/LOW
   │         │
   ▼         ▼
┌───────────────┐   ┌───────────────┐
│ Format +      │   │ Query data    │
│ Return NOW    │   │ (optional)    │
└───────────────┘   └───────────────┘
                           │
                           ▼
                    ┌───────────────┐
                    │ Browse 1-2     │
                    │ relevant files │
                    └───────────────┘
                           │
                           ▼
                    ┌───────────────┐
                    │ Combine +     │
                    │ Format        │
                    └───────────────┘
                           │
                           ▼
                    ┌───────────────┐
                    │ Async upsert  │ → Only if new info found
                    │ (optional)    │
                    └───────────────┘
                           │
                           ▼
                      Return to caller
```

---

## Speed Guidelines

| Phase | Max Tool Calls |
|-------|---------------|
| HIGH confidence path | 1-2 (just RAG query) |
| MEDIUM/LOW path | 2-4 (RAG + 1-2 files) |
| Total before return | 3 max (prefer 2) |

**Remember:** Someone is waiting. Don't over-research.
