# Tracking: Worktree-Aware Agent Coordination (slug: worktree-aware-prompts)

> NOTE: Tracking relocated to the recovered worktree (agents-ensemble-wt-approver,
> branch feature/worktree-aware-prompts @ 4a64690e) because the main checkout was
> hijacked mid-review by an external agent (stash + branch switch, 2026-09-06) and
> is off-limits. Earlier cycle state (active.md iteration 001 setup) was written in
> the main checkout before the hijack intensified.

## Iteration 001 — 2026-09-06 — VERDICT: REJECTED

Dispatch: 3 section-parallel workers, each load_skill=plan-approval (large multi-section plan, 8 files / 1796 lines):
- Worker A approve-worker-overview (85dc8249): plan-overview.md + decisions.md → REJECTED (3 blocking)
- Worker B approve-worker-phases (e66651f1): phase1-4-plan.md → APPROVED (0 blocking, 5 notes)
- Worker C approve-worker-analysis (1e83a6e7): technical-analysis.md + architecture-recommendation.md → APPROVED (0 blocking, 8 notes)

Aggregation: any-worker-blocking → REJECTED. All 3 blockers from Worker A stand (artifact-level contract defects in plan-overview/decisions; no downgrade grounds; B and C did not contradict them — different partitions).

Blocking issues (iteration 001):
1. plan-overview.md:103 — Success Criterion 3 globs two nonexistent paths (agents/*/prompts/*, plans/) → vacuous false-healthy gate; criteria 4/5 use correct convention. Fix: reword to real paths (agents/{giter,leader,developer,tester,tidier}/*.md + .agents/shared/planning/worktree-aware-prompts/) or defer to phase3's executable snippet.
2. plan-overview.md:30 vs decisions.md:249 (D5 cell) — contradictory giter tools_note catalogs (5th entry: NO-OP note vs "prune-fallback-when-remove-fails"); the prune-fallback scenario is technically impossible per both files' own no-op claims. Fix: reconcile on overview's catalog; drop or re-justify prune-fallback for dir-missing deregistration (would also fix #3).
3. plan-overview.md:32+:85 vs :111 — Phase 4 skip rule (dir-missing entries logged "already-resolved", skipped) makes Success Criterion 13 ("zero expected entries remain registered") unsatisfiable in that documented edge case. Fix: one clause — deregister dir-missing entries before logging, or exempt "already-resolved" from the threshold.

Environment incident (recorded, not adjudicated): main checkout branch-switched to feature/fix-terminal-report-wake @ 77ce4ae8 mid-review; artifact auto-stashed (main-repo stash@{0} "WIP: worktree-aware-prompts scratch"); caller recovered all 8 files into worktree agents-ensemble-wt-approver @ 4a64690e (byte-identical, line counts 166/362/411/144/160/312/60/181). Worker B terminated during disruption → revived with corrected path (escape-valve re-dispatch consumed). Worker C read via stash@{0}^3 / branch ref; verified drift touched none of the reviewed files. todo_graph store reset during disruption; fan-in satisfied by direct report evidence.

Next: iteration 002 (worker prompts stay cold — no rejection history passes to workers).

## Amendment to Iteration 001 — late worker report (2026-09-06)

Provenance: dispatcher mis-addressed the phase-path coordination update to Worker C (1e83a6e7, analysis partition, already done); the revive executed the phase1-4 verification from the recovered worktree and returned a full plan-approval report. Report is evidence-backed (anchors re-verified, bytes re-measured) — admitted as a legitimate late worker input.

Conflict on phases partition: Worker B APPROVED (literals byte-exact, anchors resolve, sequencing sound) vs Worker C-late REJECTED (3 blocking). Not a factual contradiction: B did not test gate satisfiability; C did. Per dedup rules the more specific (satisfiability) analysis is kept; B's positive findings on literals/anchors/sequencing stand unchallenged on those axes.

Additional blocking issues (worker-raised, phase2/3):
4. phase3-plan.md:275-276/:282-283/:163-164 vs phase1-plan.md:68-76 — snippet #8 requires `10 min.*heartbeat`/`15 min.*TTL`; snippet #9 requires lowercase `reconcil`+`pre-check` in giter/workflow.md; but the sanctioned 572B literal has "heartbeat" BEFORE "10 min", no "TTL" anywhere, "Reconcile:" capitalized, and no "pre-check"/"-b"/"never launch dev.sh" (those live in the tools_note literal, which #9 doesn't grep). Exact byte cap + no-duplication rule → positive checks unsatisfiable as scripted.
5. phase3-plan.md:147,149,263 vs phase2-plan.md:79,122,127,150 (+P2-E :45) — pointer-map rows/snippet expect "Worktree Mode" pointers in leader/workflow.md and developer/rule.md; phase2 explicitly forbids both (leader's pointer in tools_note.md, developer's in workflow.md; 95B rule.md literal at exact cap). P2-E "developer allowed two pointers" is stale-draft residue. Two gate rows can never resolve.
6. phase3-plan.md:19 (Task 3 rule a/b) vs :66/:88 — duplication sweep condemns the sanctioned tester/tidier `.env` safety cautions ("Never launch dev.sh inside a worktree") because they sit outside giter/workflow.md and outside a "(see) Worktree Mode" sentence; mechanical run instructs deleting safety lines. Fix: exempt sanctioned one-line `.env` cautions.

Verdict UNCHANGED: REJECTED (iteration 001). Blocking total now 6 (3 overview/decisions + 3 phase2/3). All fixes are text edits; no re-architecture. Byte arithmetic discrepancy noted: B's "sums match caps exactly (1650)" is summary-prose imprecision — C's measurement total 1628 ≤ 1650 (giter 850 + leader 314 + developer 220 + tester 122 + tidier 122) is the precise figure; decisions.md stated leader sub-caps (175+145=320) exceed measured literals (172+142=314) — consistent with A's "dead optional" note.

## Iteration 002 — 2026-09-06 — VERDICT: APPROVED (FINAL)

Dispatch: 3 FRESH section-parallel workers (new instances, cold prompts, no iteration-001 history leaked; worktree paths only), each load_skill=plan-approval:
- approve2-worker-overview (f6bb9590): plan-overview.md + decisions.md → APPROVED (0 blocking, 6 notes)
- approve2-worker-phases (6386e3ec): phase1-4-plan.md → APPROVED (0 blocking, 5 notes) — every verification gate simulated against the frozen literals and PASSES; all byte counts re-measured exactly (1628B total ≤ 1650 cap); all anchors re-verified at 4a64690e
- approve2-worker-analysis (ca406338): technical-analysis.md + architecture-recommendation.md → APPROVED (0 blocking, 3 notes) — all file:line claims verified against codebase at 4a64690e

Aggregation: zero blocking from all workers → APPROVED. All six iteration-001 blockers confirmed fixed by independent fresh verification (phase-partition worker explicitly simulated the previously-unsatisfiable gate classes and they now pass).

Consolidated non-blocking notes for the implementer:
1. phase3-plan.md:145-151 — one stale presentation-table row (leader/workflow.md listed as pointer site; leader's pointer lives in tools_note.md). Operative gate (snippet #6) correctly omits it; remove row or mark n/a.
2. phase3 byte-budget snippet: diff wc -c includes +1 byte/line `+` prefix overhead — plan documents the rule; implementer must subtract line count before comparing to sub-caps.
3. Deferred-record items (decisions.md:327-329): unpinned "fresh" clock (default 15-min census TTL), _error-payload vs exception wording, Phase-4 subset-vs-body-filter tension — all explicitly recorded as not-blocking with defaults.
4. Stale environment snapshot: technical-analysis.md:30 says "Five" registrations; live state = 4 (ens-autopromote-micro gone). C1 runtime re-enumeration absorbs it; Phase 4 will log "already-resolved".
5. Implementation-phase runtime check (from analysis worker's unverified item): confirm wt_path actually renders in the spawned editor's [SYSTEM CONTEXT: Task Context] block on first real dispatch — one-line acceptance check.
6. Cosmetic: developer rule.md new bullet is plain text vs siblings' bold (byte-cap driven, per C3-iii keep verbatim); phase2 task-1 acceptance wording could be tightened (leader pointer lives in task-2 literal).
7. Artifact remains UNTRACKED in the worktree — commit to feature/worktree-aware-prompts before implementation dispatch (planning artifacts don't survive branch recreation). A duplicate copy remains in main-repo stash@{0}.
