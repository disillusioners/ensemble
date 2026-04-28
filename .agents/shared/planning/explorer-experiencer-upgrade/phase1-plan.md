# Phase 1: Clean Explorer Agent Definition

## Objective
Remove all references to KB updates, rag_insert_text, experience(), experiencer, and persistence from the Explorer agent definition files. Add `## Should Update KB: true/false` to the output format.

## Coupling
- **Depends on**: None
- **Coupling type**: independent
- **Shared files with other phases**: None (agent markdown files are separate from Python code)
- **Shared APIs/interfaces**: Phase 2 reads the output format defined here (the `## Should Update KB` header)

## Context
Explorer agent currently does two things it shouldn't:
1. Directly calls `rag_insert_text` to upsert knowledge (Step 6 in workflow)
2. Mentions experience(), experiencer, KB updates in its agent files

The Explorer should be a pure retrieval agent — it finds information, assesses confidence, and signals whether the KB should be updated. The actual update is handled by the tool layer + job queue.

## Tasks

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 1 | Clean `soul.md` | Remove "Upsert Knowledge" from "What I Do" list (item 4). Remove "Async knowledge upserting" from "Strengths". Change "Cannot spawn agents — No recursion via explore() or experience()" to just "Cannot spawn agents — No recursion via explore()". | `agents/explorer/soul.md` |
| 2 | Clean `rule.md` | Remove the Must rule: "Upsert new findings to RAG asynchronously when stale or missing data detected during file browsing (fire-and-forget with rag_insert_text)". Add Must Not rule: "Never use rag_insert_text — knowledge upserts are handled externally". Remove Immutable rule: "Async upsert is fire-and-forget — don't wait for confirmation when updating RAG". | `agents/explorer/rule.md` |
| 3 | Clean `workflow.md` | Remove Step 6 entirely (Async Upsert). Add `## Should Update KB: {true\|false}` to the Step 5 output format template. Update the summary flowchart to remove the async upsert box. Remove the tip about upserting findings later. | `agents/explorer/workflow.md` |
| 4 | Clean `knowledge.md` | Remove the entire "Async Upsert Strategy" section. Add `## Should Update KB: {true\|false}` to the response template with guidance: "Set to true if file browsing found information not in RAG. Set to false if RAG had HIGH confidence and no new info found." | `agents/explorer/knowledge.md` |
| 5 | Clean `tools_note.md` | Remove the rag_insert_text documentation section. Add rag_insert_text to the "CRITICAL: NEVER USE" table with reason "Experiencer handles knowledge upserts, not Explorer". Remove the "Async upsert" bullet from Key Principles. | `agents/explorer/tools_note.md` |

## Key Files
- `agents/explorer/soul.md` — Explorer identity and capabilities description
- `agents/explorer/rule.md` — Must/Must Not rules for Explorer behavior
- `agents/explorer/workflow.md` — Step-by-step exploration workflow
- `agents/explorer/knowledge.md` — Domain knowledge and reference templates
- `agents/explorer/tools_note.md` — Tool usage reference

## Detailed Change Specification

### soul.md Changes

**Remove from "What I Do" section:**
```markdown
4. **Upsert Knowledge** — Async insert new findings back to RAG for future use
```

**Remove from "Strengths" section:**
```markdown
- Async knowledge upserting
```

**Change in "Limitations" section:**
- FROM: `**Cannot spawn agents** — No recursion via explore() or experience()`
- TO: `**Cannot spawn agents** — No recursion via explore()`

### rule.md Changes

**Remove from "Must" section:**
```markdown
- **Upsert new findings to RAG asynchronously** when stale or missing data detected during file browsing (fire-and-forget with rag_insert_text)
```

**Add to "Must Not" section:**
```markdown
- **Never use rag_insert_text** — Experiencer handles knowledge upserts, not Explorer
```

**Remove from "Immutable" section:**
```markdown
- **Async upsert is fire-and-forget** — don't wait for confirmation when updating RAG
```

### workflow.md Changes

**Modify Step 4a (HIGH Confidence Path):**
- Change: `3. Skip to Step 5 for formatting, Step 6 only for async upsert`
- To: `3. Skip to Step 5 for formatting`

**Modify Step 4b (LOW/MEDIUM Confidence Path):**
- Remove: `**Tip:** Keep it to 1-2 files maximum. You can upsert findings later.`

**Modify Step 5 output template — add after `## Confidence` line:**
```markdown

## Should Update KB: {true|false}
```

**Remove entire Step 6 section** (from "## Step 6: Async Upsert (Optional)" to the next "---")

**Update Summary Flowchart:**
- Remove the "Async upsert (optional)" box and its connections
- Flow should be: Combine + Format → Return to caller

### knowledge.md Changes

**Remove entire "Async Upsert Strategy" section** (from "## Async Upsert Strategy" to the next "---")

**Add to the response template** after `## Confidence: {HIGH|MEDIUM|LOW}`:
```markdown

## Should Update KB: {true|false}
    - Set to **true** if file browsing found information not in RAG
    - Set to **false** if RAG had HIGH confidence and no new info found
```

### tools_note.md Changes

**Remove the rag_insert_text documentation section:**
```markdown
### rag_insert_text(text, description, file_paths)

**Async upsert tool.** Insert new text into the knowledge base.
...entire section...
```

**Add to "CRITICAL: NEVER USE" table:**
```markdown
| `rag_insert_text` | FORBIDDEN — Experiencer handles knowledge upserts, not Explorer |
```

**Remove from Key Principles:**
```markdown
4. **Async upsert** — Don't wait for rag_insert_text confirmation
```

## Constraints
- Experiencer agent files (`agents/experiencer/`) must NOT be changed
- Explorer's `meta.json` should NOT be changed (tools allow list stays the same — `rag_insert_text` is a rag tool and Explorer still has rag access for querying)
- The `## Should Update KB` format must be consistent across workflow.md and knowledge.md templates

## Deliverables
- [ ] Explorer agent files cleaned of all KB update references
- [ ] Output format includes `## Should Update KB: true/false`
- [ ] No references to: experience(), experiencer, rag_insert_text, "Upsert", "KB update" in Explorer files
