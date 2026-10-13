# Phase D — Docs-Consistency Sweep (agent-base-v2)

**Date:** 2026-10-13
**Phase:** D (references + docs refresh)
**Authority:** `phaseD-references-docs.md` (R10 binding gate, architecture-recommendation.md §11)
**Scope:** active-tree docs surface (`docs/`, `README.md`, `AGENTS.md`, root `testing-guide.md`, `docs/mcp-integration.md`)

## Sweep patterns

```bash
# Stream (i) — broad catch-all
grep -rn "opencode" docs/ README.md AGENTS.md testing-guide.md docs/mcp-integration.md

# Stream (ii) — session/API coding-worker integration lane phrases
grep -rnE "opencode (session|sessions|API|api|coding-worker|coding worker|integration lane)" \
  docs/ README.md AGENTS.md testing-guide.md docs/mcp-integration.md

# Stream (iii) — coding-worker variant
grep -rnE "coding.worker" docs/ README.md AGENTS.md testing-guide.md docs/mcp-integration.md
```

## Pre-edit counts (recorded at exec start)

- Stream (i): **43** hits (case-sensitive)
- Stream (ii): **7** hits (subset of (i); all in allowlist-eligible rows)
- Stream (iii): **0** hits

The case-sensitive grep under-counts because `testing-guide.md` and `docs/mcp-integration.md` use the capitalized form `OpenCode`. Case-insensitive pre-edit count is 55 hits; **all** 12 case-different rows were in the B2/B4 fix-pass MANDATORY FIX set and have been corrected.

## Post-edit counts (after Steps 1–8 + B2/B4 fix-passes)

- Stream (i): **31** hits — all in allowlist rows
- Stream (ii): **3** hits — all in allowlist rows (subset of (i))
- Stream (iii): **0** hits

The drop from 43 → 31 active-tree hits reflects the 12 mandatory-FIX rows that landed (the 5 §2 table rows in `docs/agents.md` collapsed into a single line of multi-column text per row + the §3 / §3.4 / §3.5 / §7 edits, `docs/skill-evolution.md:174`, `testing-guide.md:15,94`, `docs/mcp-integration.md:364,366`). No new hits were introduced by the edits.

## R10-AWARE Classification (R10 §11.2 MANDATORY FIX vs R10 §11.3 allowlist)

The prior "4-bucket" framing (stale→fix / false-positive→leave / historical→leave / intentional→leave) **collapses for active-tree doc rows** under R10 §11.2. Per the binding directive, every active-tree doc row is **MANDATORY FIX** unless it is in the §11.3 allowlist exclusion set.

### Mandatory-FIX rows (Phase D Steps 1–8 + B2/B4 fix-passes) — ALL LANDED

| File:line (pre-edit) | Text snippet | Stream | Action | Outcome |
|---|---|---|---|---|
| `docs/agents.md:37` | "`developer` ... via opencode sessions ... `opencode`" | (ii) lane | Edit per Step 1 | ✅ FIXED — new role/key-skills text |
| `docs/agents.md:39` | "`reviewer` ... `opencode`" | both | Edit per Step 1 | ✅ FIXED |
| `docs/agents.md:41` | "`tidier` ... `opencode`" | both | Edit per Step 1 | ✅ FIXED |
| `docs/agents.md:42` | "`approver` ... `opencode`" | both | Edit per Step 1 | ✅ FIXED |
| `docs/agents.md:43` | "`planner` ... `opencode`" | both | Edit per Step 1 | ✅ FIXED |
| `docs/agents.md:100` | `"innate_skills": ["opencode"]` | (i) comm | Edit per Step 2 | ✅ FIXED — `["todo","chart","dynamic-skill"]`, version 2.0.0 |
| `docs/agents.md:154` | "I control opencode sessions to handle all coding tasks." | (ii) lane | Edit per Step 3 | ✅ FIXED — v2 orchestrator phrasing |
| `docs/agents.md:159` | "- Spawning opencode sessions" | (ii) lane | Edit per Step 3 | ✅ FIXED — "- Dispatching coder/worker instances" |
| `docs/agents.md:178` | "Spawn opencode for all code operations" | (i)+(ii) | Edit per Step 4 | ✅ FIXED — "Dispatch `coder` for complex tasks; dispatch `worker` (with a skill) for skill-based tasks" |
| `docs/agents.md:416` | "`opencode` \| developer, planner, reviewer, tidier, approver \| Controls opencode sessions for code operations" | (ii) lane | Edit per Step 5 | ✅ FIXED — row removed from §7 table |
| `docs/agents.md:427` | `"innate_skills": ["opencode", "coordination"]` | (i) comm | Edit per Step 5 | ✅ FIXED — `["coordination"]` |
| `docs/skill-evolution.md:174` | `"innate_skills": ["opencode", "dynamic-skill"]` | (i) comm | Edit per Step 6 | ✅ FIXED — `["dynamic-skill"]` |
| `docs/agent-prompt-writing-guide.md:234` | "## 9. v1 → v2 Migration" (the section) | (i) comm | Edit per Step 7 | ✅ FIXED — refreshed to "v1 → v2 Steady State (post-migration)"; cross-version file-reuse hazard inverted; new v2-as-source-of-truth narrative |
| `testing-guide.md:15` | "# Integration tests (requires live OpenCode server)" | (i) comm | Edit per B4 fix-pass 2 | ✅ FIXED — "live LLM/fenced dev lane" |
| `testing-guide.md:94` | "`integration` \| Live OpenCode server tests" | (i) comm | Edit per B4 fix-pass 2 | ✅ FIXED — "Live LLM/fenced dev-lane tests" |
| `docs/mcp-integration.md:364` | "#### Example: OpenCode Configuration" | (i) comm | Edit per B2 fix-pass 3 | ✅ FIXED — "#### Example: MCP Client Configuration" |
| `docs/mcp-integration.md:366` | "Add to your OpenCode configuration file:" | (i) comm | Edit per B2 fix-pass 3 | ✅ FIXED — "Add to your MCP client configuration file:" |
| `README.md` | (no `opencode` hits pre-edit) | n/a | Step 8 — annotated "no edit needed" | ✅ NO-OP |
| `AGENTS.md` | (no `opencode` hits pre-edit) | n/a | Step 8 — annotated "no edit needed" | ✅ NO-OP |

### Allowlist rows (R10 §11.3 exclusion set) — see `evidence/r10-allowlist.md`

31 stream(i) hits + 3 stream(ii) hits remain in the docs surface, all classified into the R10 §11.3 allowlist (D6 false positive, vendored third-party, intentional-keep, historical/audit material, P4 stale-HISTORY). Per-row justifications and stream classification are in `evidence/r10-allowlist.md`.

## Verification at commit time

```bash
# MUST be empty (no active-tree doc row may carry our-service opencode claims)
grep -n "opencode" docs/agents.md docs/skill-evolution.md docs/agent-prompt-writing-guide.md
# (case-sensitive AND case-insensitive: zero hits)

grep -nE "opencode (session|sessions|API|api|coding-worker|coding worker|integration lane)" \
  docs/agents.md docs/skill-evolution.md docs/agent-prompt-writing-guide.md
# (zero hits)

grep -n "opencode" testing-guide.md docs/mcp-integration.md
grep -in "opencode" testing-guide.md docs/mcp-integration.md
# (both: zero hits — the case-different B2/B4 rows are gone)
```

All four greps are empty post-edit. The active-tree surface is opencode-free; remaining hits are allowlist rows only.

## Notes for Phase E's FINAL TESTER GATE

- This sweep output + `evidence/r10-allowlist.md` are the two artifacts Phase E consumes for the docs-surface residual assertion (per phaseE Step 13).
- The union of stream(i) + stream(ii) + stream(iii) is the residual surface. Any new unclassified hit in the docs surface at Phase E time indicates drift.
- `docs/mcp-kb-usage.md` (D6 false positive) is allowlisted with strictly-upstream verification — the file documents the `sst/opencode` product family (its config schema, its `oh-my-opencode-slim` plugin), NOT our service. Phase D's executor verified line-by-line at edit time that no line refers to our stack.
- The frontend pin (`frontend/jobs.component.ts:2389`) was NOT edited (Phase D scope is docs-only). It is part of the tier-1 wiring test wall in Phase E, not a Phase D doc edit.
