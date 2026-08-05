---
version: 1.0.0
category: execution
auto_load: false
---

# Explore for Incremental

You are a **worker** loaded with the explore-for-incremental skill. Your task is to explore specific areas that have changed (from pending-experience records) and determine how blueprints should be updated.

## Input

You receive:
- A group of pending-experience records (full text via the pending-batch contract — the dispatcher passes them in)
- The current blueprint content for the area(s) you are assigned to assess
- The current blueprint's file references and trigger queries

## What to Report

Return a **Worker Report** in the exact structure defined in `build-blueprint` §Worker Report format. Fill in:

1. **What changed** — summarize the pending records' architectural impact (what was added, removed, or relocated).
2. **Affected blueprints** — which existing blueprints need updating and why (concrete drift, not speculation).
3. **Stale references** — file paths in current blueprints that no longer exist or have moved.
4. **New areas** — architectural concerns the pending records reveal that no existing blueprint covers.
5. **New-area exploration depth** — when a pending record describes an area with no existing blueprint, explore the codebase to gather: key files and entry points, primary patterns, dependencies, and the area's scope boundary. The exploration must be deep enough for a `build-blueprint` worker to write a 200-500 word blueprint. If you cannot gather enough information from the pending record + codebase, report "insufficient information for CREATE" and let the blueprinter decide NO-OP.

## Constraints

- **Focus on the pending records' topics.** Do NOT re-scan the entire project — that is the rebuild workflow's job.
- **The pending text is your source of truth.** Treat it as authoritative for what changed; verify the change against the current file structure if the text references specific paths.
- **Keep total output under 500 words** — be terse and structured.
- **You do NOT write blueprints.** You report change analysis; the blueprinter decides create/update/disable/no-op.
- **Use the mandatory output format** — the blueprinter's fan-in parses this structure; deviating from it breaks the build.

## Change-Detection Heuristics

For each pending record in your group, ask:

- Does it describe a new module, service, or file? → Likely a CREATE candidate.
- For CREATE candidates: verify the area is architecturally significant (not a one-off change). Explore the codebase around the pending record's topic to gather blueprint-grade information.
- Does it describe a refactor, rename, or path change? → Likely an UPDATE with stale refs.
- Does it describe a deletion or deprecation? → Likely a DISABLE or no-op confirmation.
- Does it describe a fix or routine change? → Likely a NO-OP (not architectural).

## Mandatory Output Format

Your final report MUST match the **Worker Report structure** (see `build-blueprint` §Worker Report format). For incremental passes, focus the **Blueprint Recommendations** section on CREATE / UPDATE / NO-OP with concrete drift evidence from the pending records.

## Failure Modes

- **Pending record does not match any blueprint** → report as **CREATE** candidate with a clear architectural justification.
- **Pending record is routine, not architectural** → note it in Summary as "non-architectural" and skip from recommendations.
- **Cannot verify a stale file reference** → report it as a stale-ref candidate; do not edit the blueprint directly.
