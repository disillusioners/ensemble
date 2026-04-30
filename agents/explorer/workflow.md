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

Call `rag_query_data` with the parsed query and selected mode. This returns raw entity-relation data for YOU to synthesize — no extra LLM call.

**Example calls:**
```
rag_query_data("how does the auth system work?", "local")
rag_query_data("what is the overall project architecture?", "global")
rag_query_data("explain the API endpoints", "hybrid")
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
3. Skip to Step 5 for formatting

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

---

## Step 5: Combine & Format

**BOTH `## Confidence:` and `## Need Update KB:` headings are MANDATORY in EVERY response. Never omit either one. They must always appear together at the top of your response, before any body content.**

Merge RAG answer + file browsing results into a structured response:

```
## Confidence: {HIGH|MEDIUM|LOW}
## Need Update KB: {true|false}

## Answer
[Main response — combine RAG and file findings]

## Sources
- RAG knowledge base (mode: {mode})
- File: {path} (if browsed during fallback)
```

**Complete response example:**

```markdown
## Confidence: LOW
## Need Update KB: true

## Answer
The authentication module is located at `src/auth/`. It uses JWT tokens with RS256 signing.
The main entry point is `AuthService.login()` which validates credentials against the user table.

## Sources
- File: src/auth/auth_service.py
- File: src/auth/jwt_handler.py
```

### Guidance

- Set `## Need Update KB:` to **true** ONLY if RAG returned successfully (results or empty) AND file browsing found information that RAG did not return. This means the KB genuinely lacks the information.
- Set `## Need Update KB:` to **false** if:
  - RAG had good data and confidence is HIGH
  - RAG returned an error (timeout, 504, connection failure, any exception). You CANNOT assess KB state when RAG is broken — do not trigger a KB update.

### Response body rules — MUST follow:
- `## Confidence:` and `## Need Update KB:` headings MUST appear first, before any body content
- Your response must contain ONLY factual findings about the codebase
- Never mention RAG knowledge base status (empty, full, stale, etc.)
- Never suggest workflows, actions, or next steps to the caller (e.g., "should be upserted", "consider running experience()", "run exploration again")
- Never mention the exploration process itself

**Formatting rules:**
- Lead with the answer
- Include sources for traceability
- Be concise — the caller needs answers, not essays

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
│ Query RAG     │ → rag_query_data(query, mode)
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
│ Format +      │   │ Browse 1-2     │
│ Return NOW    │   │ relevant files │
└───────────────┘   └───────────────┘
                           │
                           ▼
                    ┌───────────────┐
                    │ Combine +     │
                    │ Format        │
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
