# Re-Review: `approver[v2]` Agent (post improve commit b33d3832)

**Date:** 2026-07-31
**Commit:** b33d3832 "improve(v2-agents): dedup canonical refs, cardinal rules, fan-in escape valves"
**Status:** Re-review only — no changes applied

Commit touched 7 approver files (`rule.md` −138 lines, `workflow.md` −332 lines collapsed, `approval-strategy.md` +43 canonical home, `meta.json`/`soul.md`, two skill version bumps). Net effect: planning layer single-sourced in `approval-strategy.md`, cardinal rules split, escape valve added.

## Verification of prior flags

| Prior flag | Status | Evidence (file:line) |
|---|---|---|
| 1. Massive cross-file duplication | **Partial** | `approval-strategy.md:9` declares itself single canonical home; `workflow.md` 350→146 lines delegating via `:10,28,65,73,84`. Residual: dispatch snippet still duplicated verbatim — `tools_note.md:19-34` ≈ `approval-strategy.md:128-145`; Common Approval Traps still in both `plan-approval.md:110-118` and `decision-approval.md:109-117` (3→2 copies). |
| 2. rule.md 35 rules, no cardinal split | **Resolved** | `rule.md:3-14` 5 Cardinal ("never violate"); `:19-50` Guidelines; `:54-62` "Never" restatement. |
| 3. Fuzzy tool-permission (no deny-list) | **Partial** | `meta.json:16` now `deny: ["apply_patch","edit_file","write_file","db"]`. Residual: `bash` still allow-listed; `git commit/push/merge` via bash not denied — workers prohibit only in skill prose (`plan-approval.md:16-21`), so a skill-less worker can still mutate via git. |
| 4. skill-set.yaml version drift (3 drifts) | **Resolved** | All aligned: `skill-set.yaml:4,9,14` = `1.1.0 / 1.2.0 / 1.2.0` ↔ frontmatter `approval-strategy.md:2`, `plan-approval.md:2`, `decision-approval.md:2`. |
| 5. APPROVED-status contradiction | **Resolved** | Single canonical rule at `approval-strategy.md:217`; `workflow.md:70` references it; no competing rule. |
| 6. No v1→v2 migration story | **Still open** | `meta.json:7 version: 2.0.0`; no changelog/deprecation note referencing `agents/approver/` (v1). |
| 7. Cross-agent skill-bank no fallback | **Partial** | `rule.md:24` (re-dispatch once, then escalate), `workflow.md:98` (skill-less output → low-confidence). Residual: still *post-hoc* (detects after the worker acted); no pre-dispatch confirmation that `load_skill` resolved, no fallback where approver reads the artifact read-only itself. |
| 8. Calibration anchors weak (line-count proxies) | **Still open** | `approval-strategy.md:46-49` still `<10 / <50 / >500 lines`; `workflow.md:119-121` same. No worked examples / risk-based anchors added. |
| 9. Tone directive implicit | **Resolved** | `soul.md:70-79` "Tone & Voice" — verdict leads, per-severity framing (APPROVED one-line + observations; REJECTED with Expected/Found/required-change), "no approved-with-suggestions", ESCALATED-is-not-a-verdict. |
| 10. Iteration number leaks bias (read pre-dispatch) | **Partial** (deliberate) | Addressed as a deliberate decision: `rule.md:7`, `workflow.md:47`, `approval-strategy.md:226` — "iteration counter is NOT inherited bias; approver may read its own retry state; passing it/rejection history into worker prompts is forbidden." Reasoned and consistent, but the original bias hole (knowing "003" implies two prior rejections) is *justified, not closed*. |
| 11. Aggregation judgment band undefined | **Resolved** | `approval-strategy.md:196-199` + `rule.md:11` Cardinal #4: MAY downgrade Blocking→Note (stated reason) / MAY merge conflicting; MAY NOT upgrade Note→Blocking or introduce new blocking; suspected miss surfaces as "Approver note — recommend re-review." |
| 12. Verdict 3 forms | **Resolved** | `rule.md:7,21` and `approval-strategy.md:224,231`: ESCALATED is `active.md` state, not verdict string; on 3rd rejection return plain `REJECTED` + Note "Max iterations reached (3) — escalated." Compound string dropped. |
| 13. Heartbeat/timeout primitive missing | **Partial** | `workflow.md:90-100` Fan-In Escape Valve: confirm stuck → one re-dispatch → REJECTED+escalation Note, max 1. Residual: no *runtime-owned* heartbeat; escape valve triggers only on external signal (worker reports `error`/`crashed` or caller signals gone). A *silently-hung* single worker (default case, no `todo_graph` node per `approval-strategy.md:183`) has no detectable failure state; step 1 conflates "slow" with "stuck." |
| 14. Skill-load confirmation hook missing | **Partial** | Replaced with post-hoc detection (`rule.md:24`, `workflow.md:98`) rather than a pre-dispatch `get_instance_info(skill_loaded=...)` hook. The "explicitly NOT counted as polling" confirmation primitive requested was **not** added. |
| 15. image/mcp/proc/time/shared_context allow-listed, no guidance | **Still open** | `meta.json:15` still allow-lists all five; `tools_note.md` documents only `instance`, `filesystem/bash`, `knowledge`, team members, innate skills — zero guidance for the five previously flagged categories. |

## New issues introduced

1. **Single-worker escape-valve references a non-existent `todo_graph` node.** `workflow.md:95` "For single-worker, this IS the todo node implicitly" — but `rule.md:22` and `approval-strategy.md:183` both state single-worker approvals *skip the graph entirely*. The "implicitly" hand-wave is the only thing keeping it from contradicting. Minor but fragile.

2. **Conflicting-findings rule brushes Cardinal #4.** `workflow.md:133`: if two section-workers give irreconcilable conflicting findings on a shared dependency, "surface both under Blocking." Cardinal #4 (`rule.md:11`, `approval-strategy.md:199`) forbids *introducing* a new blocking issue workers did not raise. Promoting one worker's Note to Blocking to "surface both" is plausibly a Cardinal #4 violation. Needs an explicit carve-out ("conflict-merge is a merge, not an upgrade").

3. **Residual dispatch-snippet duplication contradicts commit thesis.** `tools_note.md:19-34` reproduces the `spawn_instance`/`send_message` snippet near-verbatim against new canonical `approval-strategy.md:128-145`. Commit message claims "one edit, one propagation" — `tools_note.md` still hosts a parallel copy that will drift.

## What improved most
**Canonical-home collapse + cardinal split.** `workflow.md` shrank 350→146 lines and delegates every duplicated block to `approval-strategy.md` with explicit pointers. Pairing with the 5-rule cardinal split and the explicit aggregation judgment band resolved flags 2, 5, 9, 11, 12 in one coherent stroke — APPROVED-status contradiction and 3-form verdict ambiguity gone because each rule now lives in exactly one place.

## What remains weakest
**Operational reachability of the escape valve** (flags 13, 14, 7). Every "stuck worker" path still depends on an *external* signal — worker must report `error`/`crashed` or caller must signal gone (`workflow.md:95`). For the default single-worker case with a worker that simply stops responding, no runtime-owned heartbeat, no pre-dispatch skill-load confirmation, no self-detect of staleness — dead-end path is defined but not *reachable*. Silent hang effectively unhandled.

## Top fixes
1. **Add a runtime-owned skill-load confirmation + staleness signal.** A one-shot `get_instance_info(skill_loaded=<name>)` immediately post-dispatch (explicitly *not* counted as polling — satisfies Cardinal #3) and an instance-state/staleness signal the escape valve can key off, so silently-hung single-worker becomes detectable without external nudge. Closes flags 7, 13, 14 in one mechanism.
2. **Swap calibration anchors from line-count to risk-based** with 2–3 worked examples (e.g. "auth/schema change = large regardless of length; additive single-component change = small"), and add a one-line v1→v2 deprecation note. Closes flags 6 and 8.
