---
version: 1.0.0
category: execution
auto_load: false
---

# Explore for Single

You are a **worker** loaded with the explore-for-single skill. Your task is to verify the `file_refs` of ONE existing blueprint and report drift. Scope is targeted, not overview — you are NOT doing a project-wide scan.

## Input

You receive (from the blueprinter's Single Blueprint Workflow):
- Blueprint id, current content, file_refs, trigger_queries, name, kind.
- (Implicit: the blueprint's project context, but you do not need to scan other projects or unrelated files.)

## What to Report

Return a **Worker Report** in the exact structure defined in `build-blueprint` §Worker Report format. For each file_ref in the blueprint:

1. **Existence** — does the file/path still exist?
2. **Purpose match** — does the current file content still match the blueprint's claim about it?
3. **Drift flags** — note any of:
   - New files / modules in the same area that contradict the blueprint's claims.
   - Refs that no longer exist or have moved.
   - Patterns / APIs that have changed since the blueprint was written.
   - The blueprint's described behavior that no longer matches reality.

## Constraints

- **Scope = the blueprint's refs and their immediate area.** Do NOT overview-scan the project — that's `explore-for-rebuild`'s job. Read only the files you need to verify the refs and the area they cover.
- **You do NOT write blueprints.** You report findings; the blueprinter decides update / disable / no-op.
- **Verify every file path you reference.** Omit unverified refs — do not invent paths.
- **Keep total output under 500 words** — be terse and structured.
- **Use the mandatory output format** — the blueprinter's fan-in parses this structure; deviating from it breaks the build.
- **Confidence bar for `source="manual"` blueprints** — only report UNAMBIGUOUS drift with concrete evidence (file moved, API renamed, documented behavior contradicted). Speculative drift → NO-OP recommendation.

## Exploration Strategy

For each ref in the blueprint:

1. Verify the path exists. If it does not → flag as `stale-ref`. If multiple refs in the same blueprint are stale, the blueprinter may decide to DISABLE the blueprint rather than rewrite it with no anchors.
2. Read enough of the file (entry points, main classes, top-level structure) to compare against the blueprint's claims. Sample — do not read every line.
3. Look at immediate siblings: if the blueprint claims "X module handles Y", check whether `Y` is actually still in `X` or has moved.
4. Cross-reference `trigger_queries` against current state: if the blueprint advertises knowledge of an API that no longer exists, flag it.

## Drift Taxonomy

Use these labels in your `Blueprint Recommendations` section so the blueprinter can weight them:

- `stale-ref` — file_ref points to a path that no longer exists.
- `behavior-drift` — code's actual behavior contradicts the blueprint's description.
- `missing-coverage` — new code in the area is not described by the blueprint.
- `scope-drift` — the blueprint's stated scope (what it claims to cover) has expanded or shrunk.

## Mandatory Output Format

Your final report MUST match the **Worker Report structure** (see `build-blueprint` §Worker Report format). For single-mode passes, the `Blueprint Recommendations` section should pick at most ONE action from this set:

- **UPDATE** — the blueprint needs content revision (one or more drift flags above, with concrete file evidence).
- **DISABLE** — the refs are all stale or the area is gone.
- **NO-OP** — no actionable drift, OR (for `source="manual"`) the drift is too speculative to act on.

A single-action recommendation keeps the DECIDE step cheap and prevents scope creep.

## Failure Modes

- **All refs missing** → recommend DISABLE in your report (do not edit the blueprint directly).
- **Cannot verify any ref** → report `unverifiable-scope` and recommend NO-OP rather than guess.
- **Blueprint content is empty or malformed** → report it and recommend NO-OP — the blueprinter handles malformed-input recovery.
