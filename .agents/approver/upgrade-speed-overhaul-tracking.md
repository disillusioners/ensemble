# Tracking: upgrade-speed-overhaul

## Iteration 001 — REJECTED (2026-10-12)

**Plan:** Upgrade-System Overhaul — Fast-by-Default Promote Ceremonies
**Artifact dir:** /home/nea/ensemble-src-wt-upg-overhaul/.agents/shared/planning/upgrade-speed-overhaul/
**Mode:** Plan Approval (3 section-parallel workers, all `plan-approval`)
**Aggregate verdict:** REJECTED — 6 blocking issues across 3 plan files.

### Worker reports

| Worker | Section | Verdict | Blocking | Notes |
|---|---|---|---|---|
| approve-worker-phases-a (08e6816d) | plan-overview.md + phase1 + phase2 | APPROVED | 0 | 8 |
| approve-worker-phases-b (4c6647c0) | phase3 + phase4 + risk-register | REJECTED | 4 | 16 |
| approve-worker-support (597d8c24) | decisions + architecture-recommendation + test-strategy | REJECTED | 2 | 4 |

### Blocking issues (deduplicated, in the order reported by each worker)

1. **`phase4-plan.md` §5 AC2 row** — "worst-case ceiling ≤ 600 s" is unsatisfiable against the plan's own 870/1400 arithmetic and the resolved Q6a framing. The "(Q6a resolution)" citation attaches the resolution's name to the very claim the resolution rescinded. (Worker B, B#1)
2. **`phase3-plan.md` §5 G2a + `test-strategy.md:45`** — N×15 s minimums stated as "N=2→≥45 s, N=4→≥105 s" are not derivable from a 15 s cadence (correct: N=2→≥30 s, N=4→≥60 s). The 105 s is the soak-squeeze constant copy-pasted into the N×15 cells; the predecessor 30 s-cadence numbers (60/120) were correct for the old model. T-G2a cannot pass as bounded. (Worker B, B#2)
3. **`phase3-plan.md` D-P3.7 vs T3.5** — boot-window grace direction reversal: D-P3.7 says grace ENDS on 3 consecutive non-transient greens; T3.5 outcome asserts grace EXTENDS on green-seen. The T3.5 outcome column asserts the wrong behavior; under D-P3.7 the test cannot be made to pass except vacuously. (Worker B, B#3)
4. **`risk-register.md` §2 item 1 (Conservative-mode escape, env-only, immediate)** — body says set `ENSEMBLE_PROMOTE_SOAK_S=300` to revert to current behavior; per D-P3.2 / decisions.md:251 the actual conservative-mode flag is `ENSEMBLE_SOAK_CONSERVATIVE=1`. An operator following §2.1 verbatim will not get the AC3-pinned reversion. Title and §4 of the same file are consistent with D-P3.2; only §2.1 step 1 is wrong. (Worker B, B#4)
5. **`test-strategy.md` T-AC2b (line 26)** — per-component breakdown (preflight 35 + stop 1 + flip 5 + handback 1 + livez 225 + readyz 75 + version 180 + soak 120 + commit 60) sums to 702 s, not the claimed 870 s; per-component values do not match decisions.md (d) C-10a (which uses 45/225/30/90/180/120/30/120/30). The "C-10a reconciled arithmetic" headline is not reconciled at the per-component level. (Worker C, C#1)
6. **`test-strategy.md` T-AC2b** — cross-references a "885 s value in decisions.md (d)" that does not exist (closest numbers: 870 s non-stop worst, 900 s cap, 1400 s all-budgets-max). The reconciliation arithmetic itself is also wrong: 870 + 30 slack = 900, not 885. (Worker C, C#2)

### Notes (consolidated; non-blocking, retain across iterations)

**From Worker A (plan-overview + phase1 + phase2):**
- N1: T1.1 line-anchor drift — text says "Capture `$OLD_CUR` at `promote.sh:227` and `:436`" but `:227` is the `CUR=...` capture, not `$OLD_CUR`. The implementation edit (T1.1) is correct; tighten wording.
- N2: T1.4 wording imprecision — "caller-supplied `$OLD_CUR`" misnames the actual rollback.sh parameter; intent is to require explicit version (env/arg) rather than relying on `journal.previous` fallback. T1.8 cell 2 exercises promote.sh auto-rollback (T1.1 path), so this imprecision does not affect AC5.
- N3: T-AC5a cell 3 — add explicit test pin for the old-format `in_flight` halt-for-human branch (no `displaced_version` → no `previous` write, halt, txn NOT silently committed).
- N4: T2.0/T2.0a ordering — confirmed as preconditions of T2.1 in §4 Tasks table; keep hard.
- N5: `owner_heartbeat_at` field hygiene — add one-line comment at `daemon/tools/upgrade_journal.py:741` ("currently unwritten; reserved for python reconciler").
- N6: T1.5 — add explicit ordering pin: `previous` write lands between `current` update (`:1027`) and `in_flight` clear (`:1029`); add an ordering assertion test.
- N7: field-type discipline — `displaced_version` (version string) vs `last_heartbeat` (epoch int); add a one-line type pin in each decision.
- N8: Q4 closure cross-reference consistent.

**From Worker B (phase3 + phase4 + risk-register):**
- phase3 §2 entry condition "656 s" is a stale pre-C-10a carry-over (reconciled all-budgets-max is 1400 s); drop or reconcile.
- phase3 AC2c "19 × ~280 s/event ≈ 28–46 min" arithmetic reconciles at ~89 min, not 28–46 — likely unit/typo error (~280 ms/event). Prose motivation, not a gate.
- phase3 AC2b prose component list (42+225+75+180+120) sums to 642 s, not 870 — missing budget rows not enumerated in prose.
- phase3 D-P3.1 `ADAPTIVE_SOAK_FLOOR_S=60` has no consumer in the four termination rules; either delete or pin a semantic.
- phase3 D-P3.6 "+1 s OR +5 s" precedence ambiguous; tighten to "(readyz-green still true AND +1 s elapsed) OR (+5 s elapsed)".
- phase3 AC2b per-cell ≤600 s still asserted for `soak ∈ {60, 120}` though 60 s cell worst >600 s; mark "typical ≤600 s" and reference identity table for worst-case.
- phase4 §2 entry condition "task-strategy" → typo for "test-strategy".
- phase4 D-P4.2/T4.2 "ENVSIM_FULL=1 adds 6–10 min" relationship to <10 min default could be stated more clearly (default ⊂ full).
- phase4 T4.5 (5) "sandbox drill shows real systemd handback < 90 s" — neither test-strategy nor plan-overview carries a 90 s handback number; `lib.sh:95` HANDBACK_START_BUDGET_S=60 default. Cite or correct the threshold.
- Line-anchor drifts: `promote.sh:156` for 900 s cap is at `:157`; `upgrade_tools.py:2187` system_restart at `:2181`; `:2477` system_upgrade at `:2473`. Not safety-relevant.
- risk-register R-10 mitigation retains "**(was the implicit ≤540 s / now R-10 ≤540s RELAXED …)**" — operative pins clear, but the parenthetical should be removed for readability.
- risk-register §2 item 1 "(+ explicit budget knobs as desired)" undershoots AC3a; tighten to "(+ all other budget knobs explicit per AC3a)".
- phase3 T3.9–T3.11 KEEP-GREEN pin — carve out which G2a cells (characterization/conservative) run pre-change vs. gated until the feature lands.
- D-P4.7 C-12 reconcile `feature/checkpoint-conn-resilience` on `c8a3fbf6` — unverifiable from this worktree alone; T4.8 is the right gate.

**From Worker C (decisions + architecture + test-strategy):**
- decisions.md line 96 (a) field-ownership matrix vs line 150 (b) — line 96 uses present tense for a post-P2 intent ("reads `in_flight.last_heartbeat`, NOT a top-level `last_heartbeat`") but the current code (line 1590) reads `op.owner_heartbeat_at or op.armed_at` (matches line 150). Implementer should treat line 150 as ground truth and line 175 (P2 hardening) as the spec.
- decisions.md P1/P2/P3 labeling overloaded — same letters label tier in one paragraph and PR-order in another. Suggest T1/T2/T3 or PR-1/2/3.
- test-strategy T-AC2c inconsistent metric framing — "E6 non-journal budgets 536 s" vs "typical observed non-journal ≈ 221 s"; 536+300 ≈ 836 s ≈ 14 min, not 8–13 min as stated.
- architecture-recommendation.md line 84 stale cross-reference — "decisions.md currently self-contradicts" was true at authoring; decisions.md line 89 now reads unambiguously.

### Positive observations (worker-A summary, retained for cycle-002 reviewer context)
- Quantitative AC rigor (AC1–AC5 with median-of-5/max-of-5/control cells).
- Tier normalization correction surfaced in the plan rather than hidden.
- Phase-budget arithmetic single-source-of-truth at C-10a.
- Test coverage depth — T-NEG-* per load-bearing claim.
- Blast-radius discipline — launcher.sh gains NO python3 dep, repeated 3× and pinned.
- Risk-to-test mapping with explicit Detection citations per top-5 risk.
- C-9 fixture correction (real splice path, not fake heartbeat) prevents vacuous-green.
- Q6b (FAST maiden) consistent across phase4 / plan-overview / risk-register.

### Iteration outcome
- 001 verdict: **REJECTED**
- Next: cycle 002 should target the 6 blocking issues (4 in phase3/phase4/risk-register, 2 in test-strategy). Workers A and C notes also worth addressing in the same pass to harden the plan.
- Status: IN_PROGRESS (next cycle dispatched by caller; this approver does not auto-re-dispatch).

## Iteration 002 — READY FOR RE-REVIEW (2026-10-12)

**Plan:** Upgrade-System Overhaul — Fast-by-Default Promote Ceremonies
**Artifact dir:** /home/nea/ensemble-src-wt-upg-overhaul/.agents/shared/planning/upgrade-speed-overhaul/
**Mode:** Plan Approval responder (iter 001 REJECT → iter 002 fix)
**Fix worker:** plan-worker-upg-repair (d0999267), doc-only in-place edits + single commit.

### Blocking issues fixed (the 6 from iter 001)

1. **`phase4-plan.md` §5 AC2 row** — replaced unsatisfiable "worst-case ceiling ≤ 600 s (Q6a resolution)" with the Q6a final framing: typical ≤ 600 s, non-stop worst 870 s + 30 s slack inside the 900 s cap, all-budgets-max 1400 s halt. Dropped the "(Q6a resolution)" citation that attached the resolution name to the claim it rescinded.
2. **`phase3-plan.md` §5 G2a + `test-strategy.md:45`** — replaced the wrong N×15 s math (45 s / 105 s — copy-paste of D-P3.5's 105 s soak-squeeze) with the C-1-corrected floor/cap statement: **floor 60 s (ADAPTIVE_SOAK_FLOOR_S, ≥ 3 consecutive greens at 15 s), cap 120 s, cap-reach with < 3 greens → exit 1 → auto-rollback**. Wired ADAPTIVE_SOAK_FLOOR_S=60 into a termination rule (it was defined but unconsumed in the four rules) — early-exit-success now requires `consecutive_green >= 3 AND now >= ADAPTIVE_SOAK_FLOOR_S` so 3 greens at 15 s cadence (≈ 45 s) cannot satisfy before the 60 s floor.
3. **`phase3-plan.md` D-P3.7 vs T3.5** — rewrote T3.5 outcome to mirror D-P3.7 verbatim: grace ENDS on the earliest of (a) elapsed OR (b) 3 consecutive non-transient greens on livez (cap 60 s); the prior T3.5 asserted the opposite ("grace auto-extends on green-seen"). Also tightened D-P3.6 "+1 s OR +5 s" precedence: **"(readyz-green still true AND +1 s elapsed) OR (+5 s elapsed)"** with rationale (the +1 s arm is the cheap "still-green" check; the +5 s arm is the hard wall-clock floor for the delete phase).
4. **`risk-register.md` §2 item 1** — changed the conservative-mode escape knob from `ENSEMBLE_PROMOTE_SOAK_S=300` (yields adaptive w/ cap 300, NOT current envelope) to the sanctioned D-P3.2 / `decisions.md:251` knob **`ENSEMBLE_SOAK_CONSERVATIVE=1`** (bit-identical 300 s conservative opt-in). Removed the "(+ explicit budget knobs as desired)" undershoot — CONSERVATIVE=1 is the single sanctioned reversion knob.
5. **`test-strategy.md` T-AC2b** — replaced the per-component breakdown that summed to 702 s with the **C-10a 9-row breakdown**: preflight 45 / stop 225 / flip 30 / handback 90 / livez 180 / readyz 120 / version 30 / soak 120 / commit 30 = **870 s**. Also fixed `phase3-plan.md` AC2b prose component list (was 42+225+75+180+120=642, missing rows) to enumerate all 9 C-10a rows summing to 870.
6. **`test-strategy.md` T-AC2b** — deleted the non-existent 885 s reference (the "RECONCILED with 870+30 slack" arithmetic IS 900, not 885). Fixed the 536+300 ≈ 8-13 min mismatch: **536+300=836 ≈ 14 min**, not 8-13 (tightened). Reconciled the 19 × ~280 s/event ≈ 28-46 min arithmetic: today 19 events × 2 reads × 139 s ≈ **88-89 min journal-dominated** (the 28-46 was a unit/typo); post-splice 19 × < 1 s = ≤ 19 s total journal cost. Reconciled E6 "8-13 min at 300 s soak" vs 536+300=836 ≈ 14 min tension — the 8-13 design target is the typical observed non-journal ≈ 221 s + 300 s = 521 s ≈ 8.7 min, NOT the all-budgets-max 536+300.

### Load-bearing non-blockings absorbed in the same pass
- **N1** T1.1 line-anchor wording (`promote.sh:227` is CUR capture, not OLD_CUR; tightened prose; impl edit unchanged).
- **N2** T1.4 rollback.sh explicit-version param naming (prose tightened; intent preserved).
- **N3** T-AC5a cell 3: explicit pin for old-format `in_flight` (no `displaced_version`) → halt-for-human branch.
- **N6** T1.5: ordering pin for new `previous` write between `current` update (`:1027`) and `in_flight` clear (`:1029`).
- **N7** type-discipline pins: `displaced_version` (version string) vs `last_heartbeat` (epoch int) — one-line in each decision.
- `phase3-plan.md` §2 entry "656 s" carry-over (stale pre-C-10a) dropped (all-budgets-max = 1400 s).
- `phase4-plan.md` §2 "task-strategy" → "test-strategy" typo fixed.
- `phase4-plan.md` T4.5 (5) handback anchor to `lib.sh:95` `HANDBACK_START_BUDGET_S=60` (not a free-floating 90 s).
- `phase3-plan.md` T3.9–T3.11 KEEP-GREEN: carved out which G2a cells (characterization/conservative) run pre-change.
- `risk-register.md` R-10 "≤ 540 s" parenthetical removed (editing residue).
- Line-anchor drifts in doc text: `promote.sh:156`→`:157`; `upgrade_tools.py:2187`→`:2181`; `:2477`→`:2473` (doc citations only; code untouched).
- `decisions.md` line 96 present-tense disambiguation (line 150 = current sweep behavior; line 175 = P2 spec).
- `architecture-recommendation.md` line 84 stale cross-ref to `decisions.md:89` dropped.

### Notes explicitly deferred (out of "doc-only / no code changes" scope)
- **N5**: one-line `owner_heartbeat_at` field-type discipline comment at `daemon/tools/upgrade_journal.py:741` — code-touch; handed to developer lane.

### Verification (reviewer-prescribed — run before reporting)
- grep `< 600` and `<= 600` → zero outside historical markers + typical-path scope qualifiers.
- grep `885`, `702`, `656`, `642` → zero non-marker hits.
- grep `ENSEMBLE_PROMOTE_SOAK_S=300` in `risk-register.md` §2 → zero.
- grep `N=[0-9].*≥[0-9]+ s` in `phase3-plan.md` G2a + `test-strategy.md:45` → only corrected floor/cap (60/45/30/60) values.
- grep `auto-extends on green-seen` → zero.
- 9-row C-10a breakdown (45/225/30/90/180/120/30/120/30 = 870) consistent across `decisions.md`, `test-strategy.md` T-AC2b, `phase3-plan.md` AC2b, `plan-overview.md`, `risk-register.md` R-10.

### Commit
- Message: `docs(planning): close approver iter-001 — 6 blockings + load-bearing notes`
- Files: 8 planning docs (in-place edits) + this tracking file (iter-002 entry).
- `.agents/approver/active.md`: reverted (approver-side incidental edit, not in commission scope; not committed).
- Doc-only; no code changes; working tree clean post-commit.

### Status
READY FOR APPROVER CYCLE 002 re-review.
