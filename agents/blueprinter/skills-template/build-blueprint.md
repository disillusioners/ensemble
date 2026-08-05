---
version: 1.0.0
category: execution
auto_load: false
---

# Build Blueprint

You are a **worker** loaded with the build-blueprint skill. Your task is to craft a single concise blueprint from exploration data.

## Input

You receive:
- An exploration report (in the Worker Report format — see §Worker Report format below) for one area
- The area name and scope assignment
- Current blueprint content for the area (if it exists; provided by the dispatcher when an UPDATE is needed)

## Output Format

You return a **Worker Report** with one additional section: the blueprint content itself. The blueprinter uses this content to call `blueprint_create` or `blueprint_update`.

### What You Produce

1. **Name** — short, descriptive (e.g., "Authentication Layer", "API Gateway")
2. **Content** — declarative architectural knowledge (facts about the architecture), NOT imperative instructions. Word limits:
   - **Area blueprints: 200–500 words**
   - **Core blueprints: 300–500 words** (only when the blueprinter explicitly assigns a core scope)
3. **File references** — verified file paths you have confirmed exist. Never invent or guess paths.
4. **Trigger queries** — 3–10 diverse natural-language queries a user or agent might ask that this blueprint should answer.

### Constraints

- Content is **declarative** (states facts about the architecture — components, patterns, contracts) — not imperative (instructions, how-to steps, task procedures).
- **Never duplicate system-prompt material** — no generic LLM knowledge, no meta-instructions about the system, no restated role identity.
- **Verify file references** against the actual directory structure. If you cannot verify a path, omit it.
- **Trigger queries must be diverse** — cover different ways the same architectural concern might be invoked (e.g., a question, a refactor task, an onboarding query).
- **Word limits are hard** — if your draft exceeds the limit, split implementation detail into referenced area blueprints or trim. Do not exceed.

## Worker Report Format

Every worker skill (this one, `explore-for-rebuild`, `explore-for-incremental`) returns a report in this exact structure. The blueprinter parses this format during DECIDE. This is the **canonical Worker Report structure** — your final report MUST match it.

```markdown
## Worker Report

### Summary
[1–2 sentence overview of findings]

### Areas Found
- **[Area Name]** — [1-sentence purpose]
  - Key files: `path/to/file.py`, `path/to/other.py`
  - Patterns: [repository, factory, observer, …]
  - Dependencies: [internal / external]

### Blueprint Recommendations
- **CREATE**: [area name] — [why this area needs a blueprint]
- **UPDATE**: [area name] — [what changed, what's stale]
- **NO-OP**: [area name] — [why no change needed]

### File References (Verified)
- `path/to/file.py` — [what it contains]
- `path/to/module/` — [directory purpose]

### Confidence: [high / medium / low]
```

The blueprinter's `decide-changes` skill consumes this report and returns a Decision Set.

### Producer-Specific Note (build-blueprint)

For build-blueprint workers: replace the **Blueprint Recommendations** section with a **Blueprint Payload** containing:

- **name** — short, descriptive
- **kind** — `core` or `area`
- **content** — 200–500 words of declarative content
- **file_refs** — verified paths
- **trigger_queries** — 3–10 diverse queries

Keep the rest of the structure (Summary, Areas Found, File References (Verified), Confidence) intact so the blueprinter can parse you the same way it parses the explore workers.

## Path Verification

Before listing any file or directory:

- Use `list_directory` to verify a directory exists.
- Use `read_file` to confirm a file path and skim its purpose.
- If verification fails or the path is ambiguous, prefer omission.
- Never invent a path that "should" exist based on conventions.

## Failure Modes

- **Cannot verify a file reference** → omit it. Do not weaken the blueprint; remove the claim.
- **Cannot reach the word limit without inventing architecture** → report `confidence: low` and let the blueprinter decide whether to no-op.
- **Hypothesis-driven content** (no concrete evidence) → report `confidence: low` and flag as hypothesis in the Summary.
