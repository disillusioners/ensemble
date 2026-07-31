# Re-Review: `tidier[v2]` Agent (post improve commit b33d3832)

**Date:** 2026-07-31
**Commit:** b33d3832
**Status:** Re-review only — no changes applied

## Verification of prior flags

| # | Prior flag | Status | Evidence (file:line) |
|---|---|---|---|
| 1 | Heavy duplication (Dispatch Shape Matrix 3×, file-size thresholds ~5×, Severity Guidelines byte-for-byte dup, boundary table 4× prose) | **Partial** | Severity Guidelines table fully deduped — `workflow.md:251-253` points at `tidier-strategy.md` (table at 146-156), no copy. But Dispatch Shape Matrix still restated 4×: canonical `tidier-strategy.md:91-96` plus `workflow.md:160-165`, `workflow.md:270-277`, `rule.md:47`. File-size thresholds: `rule.md:35` *declares* `tidier-strategy.md` canonical but `tidier-strategy.md` contains no threshold numbers — actuals live in `soul.md:68`, `rule.md:35`, `tidier-static-hygiene.md:51,75-77,104-106,116-118,227` (5 places). Boundary material still in `soul.md:79-101`, `rule.md:39-44`, `tidier-strategy.md:20-31`, `workflow.md:107-124` (4 places). |
| 2 | rule.md 31 rules + 7-bullet "Never" list, no cardinal split | **Resolved** | `rule.md:12` cardinal split — exactly 5 Cardinal Rules + Guidelines (6–29) + "Never" list each mapped back to its cardinal (`rule.md:73-79`). `rule.md:8` notes old flat 1–31 numbering is gone. |
| 3 | Tool permission boundary fuzzy: meta.json allows 13 tools, tools_note documents 4; bash prose overlay; image/mcp/context/shared_context/proc/self/help/time undocumented | **Partial** | `bash` + `filesystem` boundary now crisp via allow-list table (`rule.md:54-59`). But `meta.json:15` allows 12 tools; `tools_note.md` only documents `instance`, `filesystem`/`bash`, `knowledge`, innate skills, team members — `proc`, `time`, `self`, `help`, `image`, `mcp`, `context`, `shared_context` (8 tools) remain undocumented. |
| 4 | skill-set.yaml vs frontmatter version drift (all 4 out of sync despite rule.md forbidding it) | **Resolved** | `skill-set.yaml:7,13,19,24` (1.1.0 / 1.2.0 ×3) matches every `.md` frontmatter: `tidier-strategy.md:2`=1.1.0, `tidier-readable-code.md:2`=1.2.0, `tidier-static-hygiene.md:2`=1.2.0, `tidier-robustness.md:2`=1.2.0. |
| 5 | No v1→v2 migration story (scattered "verbatim from v1" notes) | **Still open** | Unchanged. Notes still scattered: `soul.md:126` "(matches v1 verbatim)", `soul.md:60-63` "six v1 craftsmanship categories", `tidier-strategy.md:180,200`. No consolidated migration/naming paragraph. |
| 6 | Cross-agent skill-bank dependency no fallback path | **Still open** | `meta.json:8 skill_injection: true`; `dynamic-skill` (`skill_search`/`skill_view`/`skill_feedback`) depends on external skill bank with no degradation path documented in `tools_note.md:122-124`. Commit added worker escape valves, not skill-bank fallback. |
| 7 | Fuzzy Reviewer/Tidier overlap on input validation (rule.md:52 defers to Reviewer; robustness:153 owns it) | **Resolved** | Ownership line drawn twice, consistently: `rule.md:43` (Guideline #16) keeps defensive/craftsmanship validation, defers security/trust-boundary validation to Reviewer; `tidier-robustness.md:151-153` matching boundary note + checklist rewrite (`:155`). Calibration table row relabeled (`:268`). |
| 8 | tidier-robustness.md:271 lists cast()/`# type:ignore` as Medium (cross-skill calibration drift) | **Resolved** | Removed from robustness calibration table; `tidier-robustness.md:275` adds explicit note: "`cast()` / `# type: ignore` … is a **Type Cleanliness** item owned by `tidier-static-hygiene`, not this skill — do not file it here." |
| 9 | active.md ESCALATED check references undefined external state/file | **Resolved** | `workflow.md:309` explains source: "If the spawn message **or** `.agents/shared/active.md` (an external Leader/Approver tracking contract) shows `Status: ESCALATED` …" Previously undefined state now bound to explicit external contract. |
| 10 | Tone directive: only "Direct, concise, practical" personality, no voice section | **Resolved** | New `soul.md:47-56` "Tone & Voice" with per-severity framing (🔴 firm-but-not-alarmist, 🟡 direct, 🟢 "Consider:" invitation) plus citation/deferred-findings voice rules. |
| 11 | No escape valve for stuck fan-in (worker-never-reports branch missing) | **Resolved** | New `workflow.md:314-326` "Fan-In Escape Valve": confirm-stuck → ONE re-dispatch → partial-aggregate with `### Gaps` + `unverified` markers → max re-dispatch = 1 → empty-report distinct handling. Cardinalized at `rule.md:20` (#4). |
| 12 | Re-dispatch vs respawn worker ambiguity (workflow.md:333 vs tools_note.md:107) | **Resolved** | Escape valve step 2 (`workflow.md:319`) explicit for failure case: "spawn ONE replacement worker with the same `load_skill`." `tools_note.md:107-108` reuse guidance clearly scoped to follow-up reviews, not failure recovery — the two no longer collide. |

## New issues introduced by b33d3832

1. **Broken `rule.md §N` cross-references — renumbering orphaned three pointers.** `rule.md:8` states old 1–31 numbering is "gone," yet several files still cite `§N` using old positional numbers, now pointing at unrelated rules:
   - `tidier-robustness.md:153` & `:268` cite "`rule.md` §14" for the input-validation security boundary — but §14 is now "Defer architecture to Reviewer." Correct is **§16** (Defer security).
   - `workflow.md:318` cites "(rule.md §9)" for "never poll/sleep" — but §9 is now "Skill must match category." Correct is **Cardinal #3**.
   - `workflow.md:326` cites "rule.md §10" for gap-surfacing obligation — but §10 is "File-size thresholds." Correct is **Cardinal #4** / Guideline #19.
   These actively mislead a worker following the pointer.

2. **Dangling "canonical" pointer for file-size thresholds.** `rule.md:35` declares "(canonical in `tidier-strategy.md`)" but `tidier-strategy.md` only *mentions* them in passing (`:44-45` "remain the default") and never states `≤500 / 500–1000 / 1000–3000 / >3000`. Numbers actually live in `tidier-static-hygiene.md:51,75,104,116` (the reviewer skill). Declared single-source is empty; duplication flagged in #1 is only partially consolidated.

3. **"Re-dispatch" remains a sliver of overloaded terminology.** Escape valve step 2 says "spawn ONE replacement"; step 5 ("Empty report … re-dispatch once") is ambiguous about whether that means fresh `spawn_instance` or `send_message` to the same worker. Minor, but reintroduces a sliver of #12 ambiguity *within* the escape-valve section itself.

## What improved most
The cardinal/guideline split (`rule.md`) plus the Fan-In Escape Valve (`workflow.md:314-326`) are the high-leverage wins — they flatten rule dilution, give the dispatcher an explicit stuck-worker ladder instead of silent aggregation, and are reinforced at `rule.md:20` Cardinal #4. Version sync (#4), input-validation ownership (#7), and the `cast()` calibration leak (#8) were closed cleanly and are testable.

## What remains weakest
**Cross-reference hygiene.** The commit renumbered `rule.md` but left three `§N` pointers stale (`tidier-robustness.md:153,268`; `workflow.md:318,326`), and the "canonical home" claim for file-size thresholds points at a file that doesn't hold them. This is exactly the kind of drift the dedup effort was meant to eliminate, and it's the most likely thing a worker will actually trip over mid-run.

## Top remaining fixes
1. **Fix the three stale `rule.md §N` references** in `tidier-robustness.md:153,268` (→ §16) and `workflow.md:318` (→ Cardinal #3) / `workflow.md:326` (→ Cardinal #4 or Guideline #19). Prefer `Cardinal #N` / `Guideline #N` labels over positional `§N` so future renumbering can't silently break them again.
2. **Make file-size thresholds actually canonical in `tidier-strategy.md`** (add the `≤500 / 500–1000 / 1000–3000 / >3000` row there), then collapse restatements in `soul.md:68`, `rule.md:35`, and `tidier-static-hygiene.md` to reference it — completing the dedup that #1 only half-finished.
