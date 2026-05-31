# Phase 2: Explorer Prompt — Concise Section Addition

## Objective
Add a mandatory `## Concise` section to the Explorer agent's response format. This section provides 1-3 sentences of summary that serves dual purpose: (1) content for medium-confidence context injection, and (2) first sentence becomes the summary in the file index.

## Coupling
- **Depends on**: Phase 1 (format contract — the system layer expects `## Concise` heading to parse)
- **Coupling type**: loose — Phase 1's parsing logic doesn't import or call Phase 2's output; it just expects the heading name
- **Shared files with other phases**: None (agent markdown files are separate from Python code)
- **Shared APIs/interfaces**: The `## Concise` heading name is the interface contract
- **Why this coupling**: Both sides agree on the heading name `## Concise`. If Phase 2 changes the name, Phase 1's parser must also change.

## Context
Current Explorer response format (from workflow.md Step 6):
```
## Confidence: {HIGH|MEDIUM|LOW}
## Need Update KB: {true|false}

## Answer
[Main response]

## Related Experience
[...]

## Sources
[...]
```

New format adds `## Concise` between the metadata headings and `## Answer`:
```
## Confidence: {HIGH|MEDIUM|LOW}
## Need Update KB: {true|false}

## Concise
[1-3 sentences. First sentence = standalone summary usable in file index.]

## Answer
[Full detailed response as today]

## Related Experience
[...]

## Sources
[...]
```

## Tasks

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 1 | Add `## Concise` to response format in workflow.md | Update Step 6 format template to include Concise section between metadata and Answer. Add guidance: "1-3 sentences. First sentence must be a standalone summary suitable for a file index." | `agents/explorer/workflow.md` |
| 2 | Add Concise writing rule to rule.md | Add rule: "Every response MUST include `## Concise` section with 1-3 sentence summary. First sentence must be standalone." Add to the existing "Every response MUST include" rule block. | `agents/explorer/rule.md` |
| 3 | Add concise trait to soul.md | Add to personality traits: "Concise Summarizer — I always provide a 1-3 sentence summary before my full answer, ensuring my findings are quickly scannable." | `agents/explorer/soul.md` |
| 4 | Update response example in workflow.md | Replace the existing example response to include the Concise section. | `agents/explorer/workflow.md` |
| 5 | Update Step 2 context-check guidance | In workflow.md Step 2, note that pre-loaded context will be provided by the system when available. The agent should check the "Pre-loaded Context" section in the message before deciding to read files. | `agents/explorer/workflow.md` |

## Detailed Changes

### File: `agents/explorer/workflow.md`

**Change 1: Step 6 format template**

Replace the current Step 6 format block with:
```markdown
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
```

**Change 2: Update response body rules**

Update the "Response body rules" section:
```markdown
### Response body rules — MUST follow:
- `## Confidence:` and `## Need Update KB:` headings MUST appear first, before any body content
- `## Concise` section MUST appear immediately after the metadata headings, before `## Answer`
- Concise must be 1-3 sentences; first sentence must be standalone (usable as a file summary without context)
- Your response must contain ONLY factual findings about the codebase
- ... (rest unchanged)
```

**Change 3: Update example response**

Replace the existing complete response example:
```markdown
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
```

**Change 4: Update Step 2 — auto-injection as primary, manual check as fallback**

Replace Step 2 with:
```markdown
## Step 2: Review pre-loaded context (if provided)

> **Note**: The system automatically matches and injects relevant context files into your message as a "Pre-loaded Context" section. This handles the common case — you should NOT manually scan the context directory unless the injection is clearly insufficient.

1. **Check message for "## Pre-loaded Context" section** — If present, review the auto-matched content first
2. **Only if pre-loaded context is insufficient** (missing files you expect, or partial coverage of a specific sub-topic):
   - If ENSEMBLE_SHARED_CONTEXT_DIR is set, use `list_directory` to find additional .md files
   - Read files whose filenames (slugs) are relevant to gaps in the pre-loaded context
3. Evaluate relevance:
   - Pre-loaded + RAG fully answers the query → Return answer. Skip manual file reading.
   - Pre-loaded partially covers → Proceed to RAG for gaps (Step 3)
   - No pre-loaded context → Proceed to RAG as normal (Step 3)

**Speed guideline:** Manual file reading should be rare (1 in 20 queries). The auto-injection + RAG covers most cases.
```

### File: `agents/explorer/rule.md`

**Change 1: Update the first "Must" rule**

Replace:
```
- **Every response MUST include both `## Confidence:` and `## Need Update KB:` headings — no exceptions, no omissions**
```

With:
```
- **Every response MUST include `## Confidence:`, `## Need Update KB:`, and `## Concise` sections — no exceptions, no omissions**
```

**Change 2: Add Concise writing rule**

Add after the existing format rule:
```
- **`## Concise` must be 1-3 sentences** — First sentence must be a standalone summary that makes sense without the full answer (used in file indexes). Second/third sentences add key details.
```

### File: `agents/explorer/soul.md`

**Change 1: Add trait to `## My Nature` section** (NOT "What Makes Me Effective" — that section doesn't exist; the actual section is `## My Nature` starting at line 18)

Add bullet to the `## My Nature` bullet list:
```
- **Concise Summarizer** — I always provide a 1-3 sentence summary before my full answer, making my findings quickly scannable by both humans and automated systems
```

## Key Files
- `agents/explorer/workflow.md` — Response format template, Step 2 guidance, example
- `agents/explorer/rule.md` — Mandatory heading rules
- `agents/explorer/soul.md` — Personality traits

## Constraints
- Changes are prompt-only — no Python code changes in this phase
- The `## Concise` heading name is the contract with Phase 1's parser
- First sentence constraint is critical — it feeds the file index
- Concise section should NOT contain markdown formatting (no headers, no code blocks)

## Deliverables
- [ ] `## Concise` section added to workflow.md response template
- [ ] Response body rules updated in workflow.md
- [ ] Example response updated with Concise section
- [ ] Step 2 revised: auto-injection as primary, manual check as optional fallback (speed guideline: manual reading should be 1 in 20 queries)
- [ ] Mandatory heading rule updated in rule.md
- [ ] Concise writing rule added to rule.md
- [ ] Concise Summarizer trait added to soul.md `## My Nature` section (not "What Makes Me Effective")
