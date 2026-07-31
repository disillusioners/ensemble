# Re-Review: `planner[v2]` Agent (post improve commit b33d3832)

**Date:** 2026-07-31
**Commit:** b33d3832
**Status:** Re-review only — no changes applied

## Verification of prior flags

| Prior flag | Status | Evidence (file:line) |
|---|---|---|
| 1. Heavy duplication (5 restatements w/ TINY drift, dispatch table, code blocks workflow↔tools_note, Planning Plan template dup, END TURN rant 5×) | **Partial** | Scope tiers + Skill Selection Guide now canonical-pointed (`workflow.md:62`, `:191`, `:207` → `planning-strategy.md`); `planning-strategy.md:9` declares itself canonical. **Still duplicated:** Planning Plan template in BOTH `soul.md:168-188` and `planning-strategy.md:129-149`. Dispatch code blocks still copy-pasted between `workflow.md:114-162` and `tools_note.md:11-54`. `workflow.md` restates tiers twice (`:195-203` Skill-Selection wave **and** `:211-217` Scale Guide) plus canonical `planning-strategy.md:23-29`. END TURN still in 5 places. |
| 2. rule.md 30 rules + literal dup (#14==#30), no cardinal split | **Resolved** | `rule.md:3-13` = 5 Cardinal Rules; flat numbering → 28 themed Guidelines. `#14`/`#30` duplicate collapsed to single §13 (`rule.md:31`). |
| 3. Version mismatches skill-set.yaml ↔ frontmatter | **Resolved** | `planning-strategy.md:2`=1.1.0 ↔ `skill-set.yaml:4`; all four execution skills =1.2.0 ↔ `skill-set.yaml:9,14,19,24`. |
| 4. Auto-load contradiction | **Resolved** | `tools_note.md:147` rewritten: states auto-loads via `skill-set.yaml auto_load: true`, explicitly "**not** in `meta.json` `innate_skills`", names the distinction, adds seeding-gap fallback. `meta.json:8` intentionally consistent. |
| 5. shared_context allow-listed but workflow never uses it | **Partial** | `workflow.md:187` defines the use case ("reserved for handing the running research buffer to planning workers when findings large enough"). Still **no concrete `shared_context_*` call in any dispatch example** — prose intent only, code path not wired. |
| 6. proc and mcp allow-listed with weak justification | **Still open** | `tools_note.md:74` ("Reserved for long-running helpers (rare for a planner)"), `:81` unchanged "we might need it someday" wording. |
| 7. Hardcoded project_id/name in soul.md | **Resolved** | Removed; `soul.md:145-147` says "project-scoped" generically. |
| 8. No v1→v2 migration story | **Still open** | No `migration.md`, no delta doc anywhere. `meta.json:7 version: 2.0.0` with zero delta doc. |
| 9. Tone directive missing | **Resolved** | `soul.md:49-58` "Tone & Voice" (caller / workers / explorers / Complete / Partial / progressive). |
| 10. Worker skill_feedback contract mismatch (dispatcher says first, worker skills never mention it) | **Resolved** | All 4 execution skills now carry the contract: `plan-creation.md:52`, `requirements-analysis.md:53`, `roadmap-strategy.md:51`, `technical-analysis.md:56` — exact "TOOL CALL ONLY before FINAL message" wording, mirrored in `workflow.md:119-159` and `rule.md:77`. |
| 11. No fan-in escape valve when worker never reports | **Resolved** | `workflow.md:68-79` Fan-In Escape Valve ladder (confirm stuck → 1 re-dispatch → partial+Gaps → max re-dispatch=1) + `rule.md:13` Cardinal #5. |
| 12. Scope calibration fuzzy (MEDIUM vs LARGE boundary) | **Partial** | TINY added (`planning-strategy.md:25`) fixing the old drift; per-tier signals given. Boundary MEDIUM ("one module/feature, light research") vs LARGE ("multi-phase, multi-module, 2+ plan sections") still qualitative — "2+ plan sections" is only hard line; "light research needed" undefined. |
| 13. Pipeline "enough research" heuristic undefined | **Resolved** | `workflow.md:181`: "once **≥1 explorer has reported** AND its findings cover the **primary module of the first plan phase**." |
| 14. rule.md:6 cites research findings but planner only aggregates | **Resolved** | `rule.md:19` now parenthetical: "when ONLY aggregating, workers are the ones who cite file:line; I pass through their citations." |
| 15. Worker reuse vs one-skill-per-worker attribution ambiguous | **Still open** | `tools_note.md:135` still says "a worker can be re-dispatched with a new `load_skill`." `rule.md:7` builds 1:1 mapping on "one skill per worker." Re-loading a *different* skill on the *same* worker instance breaks worker-instance↔skill 1:1 — is attribution keyed to instance or to skill-load event? Never reconciled. |
| 16. Aggregator write boundary unclear (can planner write plan-overview.md?) | **Partial → NEW CONTRADICTION** | `rule.md:56-58` §25 resolves it: planner MAY write `plan-overview.md` synthesis; specialist files belong to matching workers. **But** this now *contradicts* `soul.md:7` ("I never write plans or code myself"), `soul.md:41` ("**NEVER write plans directly**"), `soul.md:94` table ("Writes plans? | No (delegates to worker)"). §25 exception not propagated to soul.md. |
| 17. END TURN batching vs per-dispatch ambiguous for LARGE | **Resolved** | `workflow.md:219`: "for LARGE scope I may spawn 2–3 workers in one wave and then END TURN once (after the batch)… Per-dispatch END TURN… is NOT required for parallel fan-out within a single wave." |

**Tally:** Resolved = 9 · Partial = 4 · Still open = 3 · (one new contradiction under #16)

## New issues introduced

1. **§25 write-exception vs soul.md absolute prohibition (regression).** New `rule.md:25,58` permits planner to write `plan-overview.md`, but `soul.md` was not updated: `soul.md:7` ("I never write plans or code myself; I orchestrate"), `soul.md:41` ("**NEVER write plans directly.**"), `soul.md:94` table ("Writes plans? | No (delegates to worker)") still assert unconditional ban. An agent reading soul.md first will reject the synthesis step that rule §25 authorizes.

2. **`soul.md` skill-pointer drift.** `soul.md:82` directs reader to "see `workflow.md` Skill Selection Guide," but `workflow.md:62` and `:191` now declare the canonical guide lives in `planning-strategy.md`. Pointer is one hop too short — should point directly to `planning-strategy.md → Skill Selection Guide`.

3. **Intra-`workflow.md` tier re-duplication.** Dedup collapsed files across each other but `workflow.md` now contains two near-parallel tier restatements — "Skill Selection Decisions" (`workflow.md:195-203`) and "Scale Guide" (`workflow.md:211-217`) — plus canonical in `planning-strategy.md:23-29`. The two workflow tables overlap heavily and will drift; only one is needed.

## What improved most
**Cardinal Rules split + skill_feedback contract reconciliation** (flags #2, #10, #14): `rule.md` now has a real cardinal tier, duplicate rule gone, worker-side `skill_feedback`-then-final-message contract byte-identical across all four execution skills and dispatcher prompts — eliminating the attribution-corruption gap. **Fan-In Escape Valve** (#11) and **"enough research" signal** (#13) close the two most dangerous runtime dead-ends in the prior design.

## What remains weakest
The **soul.md vs rule.md §25 contradiction** (new issue #1) is the sharpest remaining defect — pits the agent's identity block against its own aggregation rule, and §25 is *only* stated in `rule.md`. Close second: incomplete dedup (Planning Plan template still duplicated `soul.md:168` ↔ `planning-strategy.md:129`; `workflow.md` carries two self-overlapping tier tables); "one canonical home" goal at `planning-strategy.md:9` is only half-realized.

## Top remaining fixes
1. **Reconcile soul.md with rule §25.** Soften `soul.md:7`, `:41`, and `:94` table cell to "I never write plans *from my own analysis* — I synthesize worker output into `plan-overview.md` (per `rule.md` §25)" so identity block matches aggregator write boundary.
2. **Finish the dedup the commit started:** make `planning-strategy.md:129-149` the sole Planning Plan template (soul.md links to it instead of restating), and collapse `workflow.md:195-203` + `:211-217` into the single canonical tier table in `planning-strategy.md:23-29` (keep only the dispatch-wave column in workflow.md).
