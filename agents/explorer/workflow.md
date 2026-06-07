# Workflow

Step-by-step process for exploring project knowledge.

---

## Step 1: Parse the Query

- Extract the **core question** from the caller's message
- Identify if it's **specific** (entity-focused) or **broad** (topic-focused)
- Select initial query mode:

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

## Step 2: Review pre-loaded context (if provided)

> **Note**: The system automatically matches and injects relevant context files into your message as a "Pre-loaded Context" section. This handles the common case — you should NOT manually scan the context directory unless the injection is clearly insufficient.

1. **Check message for "## Pre-loaded Context" section** — If present, review the auto-matched content first
2. **Only if pre-loaded context is insufficient** (missing files you expect, or partial coverage of a specific sub-topic):
   - Call `list_context(CONTEXT_KEY)` to enumerate additional `.md` files in the shared context directory
   - Use `read_context(CONTEXT_KEY, filename)` to read files whose slugs are relevant to gaps in the pre-loaded context
3. Evaluate relevance:
   - Pre-loaded + RAG fully answers the query → Return answer. Skip manual file reading.
   - Pre-loaded partially covers → Proceed to RAG for gaps (Step 3)
   - No pre-loaded context → Proceed to RAG as normal (Step 3)

**Speed guideline:** Manual file reading should be rare (1 in 20 queries). The auto-injection + RAG covers most cases.

---

## Step 3: Query RAG

Call `rag_query_data` with the parsed query and selected mode. This returns raw entity-relation data for YOU to synthesize — no extra LLM call.

**Entity structure** (use in synthesis):
- `name`: Entity identifier — use to reference it
- `type`: Category (e.g., Person, Service, Function, Class)
- `description`: The actual information — your primary content source

```
- **AuthService** (Service): Handles JWT token generation and validation
- **JobQueue stuck bug** (experience): Queue jobs get stuck when worker crashes mid-processing
```

**Relation structure** (use to trace connections):
- `source` / `target`: Connected entity names
- `type`: Relationship type (e.g., USES, IMPLEMENTS, DEPENDS_ON)
- `description`: Context about the relationship

```
- AuthService -[USES]-> Database: AuthService stores sessions in Database
```

**Example calls:**
```
rag_query_data("how does the auth system work?", "local")
rag_query_data("what is the overall project architecture?", "global")
rag_query_data("explain the API endpoints", "hybrid")
```

---

## Step 3b: Extract Experiences

After receiving RAG results, scan for experience-type entities:

- **Types to look for:** `experience`, `decision`, `convention`
- **How to identify:** Check the `type` field of each returned entity
- **Separate these** from regular knowledge entities for the "Related Experience" section

**Intelligent follow-up (optional):**
If the query topic would benefit from experiences and you found few or none, you MAY do 1-2 targeted queries:
```
rag_query_data("experience " + original_query, "local")
rag_get_graph(label=key_entity_name, max_depth=2)  # to find connected experiences
```
If this query returns similar results to the original (no new experience-type entities), skip further follow-up queries.

**DO follow up:** debugging queries, deployment questions, configuration issues, architecture decisions, error troubleshooting.
**DON'T follow up:** simple lookups ("what is X?"), factual queries ("list the endpoints"), high-confidence results.

This is judgment-based. Don't do this for every query — only when experiences would add value.

---

## Step 4: Assess Confidence

Rate the RAG response quality based on signal strength:

| Signal | Confidence | Interpretation | Action |
|--------|------------|-----------------|--------|
| Multiple relevant entities with good descriptions, relations connecting them | **HIGH** | Rich material — synthesize and return | → Step 5a |
| Some entities found but sparse or partial descriptions | **MEDIUM** | Good start, may need file fallback for details | → Step 5b |
| Few entities, no relations, weak/missing descriptions | **LOW** | RAG doesn't have good coverage — browse files | → Step 5b |
| RAG returns "no results" or clearly wrong answer | **LOW** | Browse files more broadly, report what you found | → Step 5b |
| RAG error or not configured | **NONE** | Go straight to file browsing | → Step 5b |

**Confidence Decision Tree:**
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

## Step 5a: HIGH Confidence Path (Fast Path)

**Execute only if confidence is HIGH:**

1. Format the answer with sources
2. Return immediately — do NOT browse files
3. Skip to Step 6 for formatting

**Why skip file browsing?** Trust the knowledge base. Speed matters.

---

## Step 5b: LOW/MEDIUM Confidence Path (File Fallback)

**Execute if confidence is MEDIUM or LOW:**

1. Call `rag_query_data` to get structured entities (may reveal more context)
2. If specific files mentioned in query or RAG results → read them with `read_file`
3. If no specific files:
   - Use `glob_files` to find relevant files by pattern
   - Or `grep_files` to search file contents
4. Read **1-2 most relevant files** (MAX — speed matters)
5. Extract additional context from file contents

---

## Step 6: Combine & Format

**BOTH `## Confidence:` and `## Need Update KB:` headings are MANDATORY in EVERY response. The `## Concise` section is also MANDATORY. Never omit any of these three.**

Merge RAG answer + file browsing results into a structured response:

```
## Confidence: {HIGH|MEDIUM|LOW}
## Need Update KB: {true|false}

## Concise
[1-3 sentences summarizing the key findings. First sentence MUST be a standalone summary usable in a file index — it should make sense without reading the full answer.]

## Answer
[Main response — combine RAG and file findings]

## Related Experience
[Experience-type entities: gotchas, decisions, conventions]
[If none: "No related experiences found for this topic."]

## Sources
- RAG knowledge base (mode: {mode})
- File: {path} (if browsed during fallback)
```

**Experience formatting (for "Related Experience" section):**

| Entity Type | Emoji | Content |
|-------------|-------|---------|
| `experience` | ⚠️ | Gotchas, warnings from past work |
| `decision` | 📋 | Design decisions, architectural choices |
| `convention` | 📏 | Coding standards, practices |

Format each entry concisely: entity name + key point from description.

**Complete response example:**

```markdown
## Confidence: MEDIUM
## Need Update KB: false

## Concise
The authentication module uses JWT tokens with RS256 signing, located at `src/auth/`. The main entry point is `AuthService.login()` which validates credentials against the user table.

## Answer
The authentication module is located at `src/auth/`. It uses JWT tokens with RS256 signing.
The main entry point is `AuthService.login()` which validates credentials against the user table.

## Related Experience
⚠️ **AuthService token expiry**: Tokens expire after 1 hour — handle refresh gracefully
📋 **JWT signing decision**: Chose RS256 over HS256 for service-to-service auth
📏 **Auth convention**: Always validate tokens on every protected endpoint

## Sources
- RAG knowledge base (mode: hybrid)
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
- `## Concise` section MUST appear immediately after the metadata headings, before `## Answer`
- Concise must be 1-3 sentences; first sentence must be standalone (usable as a file summary without context)
- Your response must contain ONLY factual findings about the codebase
- Never mention RAG knowledge base status (empty, full, stale, etc.)
- Never suggest workflows, actions, or next steps to the caller (e.g., "should be upserted", "consider running experience()", "run exploration again")
- Never mention the exploration process itself
- **Always include** the `## Related Experience` section. If no experiences were found, include: "No related experiences found for this topic."

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
┌───────────────────────────────────────┐
│ Check Pre-loaded Context              │ → Auto-injected by system (primary)
│ (auto-injected by system)             │
└───────────────────────────────────────┘
        │
   ┌────┴────┐
   │         │
   ▼         ▼ (if insufficient)
┌───────────────┐    ┌───────────────────────────────┐
│ Sufficient?   │NO  │ Use list_directory to find    │
└───────────────┘    │ additional .md files (rare)   │
   │                 └───────────────────────────────┘
  YES
   │
        │
        ▼
┌───────────────┐
│ Query RAG     │ → rag_query_data(query, mode)
└───────────────┘
        │
        ▼
┌───────────────┐
│ Extract       │ → Scan for experience/decision/
│ Experiences   │   convention entities
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

**Shared speed budget:** Follow-up experience queries (Step 3b) and file browsing (Step 5b) share the same budget — they don't stack. If you used follow-up queries, reduce file browsing calls accordingly.

**Remember:** Someone is waiting. Don't over-research.
