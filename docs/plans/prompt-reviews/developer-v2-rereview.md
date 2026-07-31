# Re-Review: `developer[v2]` Agent (post improve commit b33d3832)

**Date:** 2026-07-31
**Commit:** b33d3832 "improve(v2-agents): dedup canonical refs, cardinal rules, fan-in escape valves"
**Status:** Re-review only — no changes applied

## Verification of prior flags

| Prior flag | Status | Evidence (file:line) |
|---|---|---|
| 1. Heavy table duplication across 5 files | **Partial** | `dev-strategy.md:9` declares itself single canonical home; `soul.md:63`, `workflow.md:7`, `tools_note.md:3` point to it. Residual: `soul.md:57-61` (Dispatch Tiers), `soul.md:125-145` (Dev Plan template ↔ `dev-strategy.md:194-213`), `workflow.md:146-151` (Scale Guide), `workflow.md:28-30` (skill_feedback contract quoted verbatim despite claiming "stated once"). |
| 2. rule.md 27 rules, no cardinal split | **Resolved** | `rule.md:3-13` exactly 5 Cardinal Rules; `rule.md:17-53` demotes rest to Guidelines. Count 27 → 18. |
| 3. Fuzzy tool-permission boundaries | **Resolved** | Explicit allow-list in `rule.md:36-40`; two-column allow/deny table in `tools_note.md:21-24`; concrete commands `tools_note.md:27-32`. |
| 4. END TURN contract consistency | **Resolved** (minor drift) | Consistent across `rule.md:9`, `workflow.md:34-38`, `dev-strategy.md:136,149`. Minor: `workflow.md:26-30` says "stated **once**, in `dev-strategy.md`" then quotes it full-length itself. |
| 5. Skill-ownership boundary muddied | **Resolved** | `dev-strategy.md:13`, `rule.md:53`, `tools_note.md:67`. |
| 6. No v1→v2 migration story | **Still open** | No migration/memory note. Reviewer got v2-local `memory.md`; developer did not. |
| 7. Verification loop no escape valve | **Resolved** | `rule.md:47` (§16) 3-iteration cap → `Partial`; `workflow.md:66-77` Fan-In Escape Valve (confirm-stuck → 1 re-dispatch → partial+gaps, max 1 re-dispatch). Mirrored `soul.md:74`, `workflow.md:170`. |
| 8. Cross-agent skill bank dependencies no fallback | **Resolved** (see new issue #1) | `rule.md:54` (§18) `code-review` missing → spawn `reviewer`; execution skills missing → worker-no-skill + degradation flag. `workflow.md:159` Skill-Seed Gotcha. **But** fallback assumes `reviewer` is spawnable — see new issues. |
| 9. Calibration anchors missing | **Resolved** | `dev-strategy.md:33-38` scope matrix (SMALL <1h … HUGE >4h); hard numbers: 3-concurrent cap `rule.md:29`, 3-iter cap `rule.md:47`, max 1 re-dispatch `workflow.md:75`, chart trigger ≥2 instances/≥2 modules `tools_note.md:76`. |
| 10. Tone directive missing | **Resolved** | `soul.md:32-39` "Tone & Voice" (caller / dispatched / Complete / Partial·Blocked / code-fix dispatch). |
| 11. Mixed-tier Dev Plan template vs rule.md §9 | **Resolved** | Template Tier line now "Mixed (multi-feature → fan-out, one tier per instance)" in both `dev-strategy.md:200` and `soul.md:132`; `rule.md:23` (§10) defines Mixed properly. |

## New issues introduced

1. **Fallback path references an agent not in `team_members`.** `rule.md:54` and `workflow.md:172` instruct spawning `reviewer` when `code-review` skill load fails. But `meta.json:17` declares `team_members: ["coder","worker"]` — `reviewer` absent. If `spawn_instance(agent="reviewer")` is gated by `team_members`, the fallback cannot fire and silently degrades. **Evidence:** `meta.json:17` vs `rule.md:54`, `workflow.md:172`.

2. **Self-contradicting "stated once" claim.** `workflow.md:30` asserts the `skill_feedback` contract is "stated **once**, in `dev-strategy.md`. I do not maintain parallel copies." Yet `workflow.md:26-28` reproduces it verbatim. **Evidence:** `workflow.md:26-30` vs `dev-strategy.md:129-132`.

3. **Dev Plan template still fully duplicated.** `soul.md:123-145` restates the whole template while noting "template also in `dev-strategy.md`"; `dev-strategy.md:194-213` holds canonical copy. **Evidence:** `soul.md:125-145` ↔ `dev-strategy.md:194-213`.

## What improved most
Cardinal/Guideline split (`rule.md:3-13`) + canonical `dev-strategy.md` eliminated the worst dilution: rules are now enforceable (5 never-violate) and Scope/Tier/Skill/Verification tables have a single stated home. Escape valves (#7) and fallback rule (#8) close the two most dangerous runtime failure modes.

## What remains weakest
The canonical-home goal is only half met: high-churn artifacts (Dev Plan template, skill_feedback contract) are *described* as deduped but still physically duplicated. The new `reviewer`-fallback (#1) is unspunnable given current `team_members` — the strongest new rule has a broken exit.

## Top fixes
1. **Add `reviewer` to `meta.json:17` `team_members`** (or document an alternate escalation path) so §18 can actually execute.
2. **Make Dev Plan template and skill_feedback contract single-sourced in `dev-strategy.md`** — replace copies at `soul.md:125-145` and the verbatim quote at `workflow.md:26-28` with one-line pointers. Make `workflow.md:30`'s "stated once" claim true or drop it.

Open from prior review: #6 (v1→v2 migration story) untouched. Consider adding a `memory.md` mirroring reviewer[v2]'s treatment.
