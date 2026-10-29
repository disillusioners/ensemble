VERDICT: APPROVED

# DB Pool Idle-in-Transaction Fix Lane (db-pool-idle-txn) — Independent Plan Approval

- Date: 2026-10-28T15:55Z
- Approver iteration: 001 (FINAL)
- Plan under evaluation: `.agents/shared/planning/db-pool-idle-txn/` — plan-overview.md + final-implementation-spec.md (binding contract) + decisions.md, phase0–3-plan.md, test-strategy.md, FOLD-MAP.md, BASELINE.md, expected-collected.md
- Worktree: `/home/nea/ensemble-src-wt-dbpool-idletxn`, branch `fix/db-pool-idle-txn`, HEAD 5e59c8e2b (base 19fc11c8b = staged v0.27.11 tree)
- Mode: Plan Approval — single fresh-eyes worker, `plan-approval` skill, cold-context dispatch
- Independence fence honored: council-report.md, delta-recheck.md, delta-recheck-2.md, architecture-recommendation.md, architecture-delta.md were NOT read by the approver or the worker (referenced by filename only in the plan's Delta1..Delta8 closure block)

## Verdict

**APPROVED — 0 blocking issues.** 1/1 worker APPROVED.

The plan-of-record delivers the commission's fix list (F1–F7 as evidence-revised) plus what its own evidence demands — the F8 borrow-then-await fix as the Phase-1 primary, the F4-A admission pre-check, per-conninfo attribution — with no requirement silently dropped (narrowings are explicit and disclosed). ~25 file:line anchors spot-checked against the worktree code, all matching (minor drift acknowledged as C4). Safety couplings C1–C4 are honored by the plan text itself; the "narrow and disclose" pattern is applied honestly (disabled engine-level backstop with a stated enablement condition and boot-time log; demoted F4; routed items). The non-contribution boundary (event-loop sync-DB driver routed to its own commission) is stated truthfully in four separate sections. Release posture is complete and correct: stage-only, no promote/restart/.env, provisional v0.27.12 with stage-time re-derivation, merge-latest-if-moved.

## Blocking Issues

(none)

## Non-blocking Observations

1. `expected-collected.md` counts are TBD (task R-1 routed to the tester lane). The plan explicitly does not invent numbers and does not mark the gate satisfied — the counts must be measured before the C3 gate can be declared satisfied. Carry as a completion precondition at gate time, not a plan defect.
2. `plan-overview.md` §16 carries a duplicated "DR-36" bullet (lines 498/500 — byte-identical). Cosmetic; no semantic impact. (Independently re-verified by the approver.)
3. `MANAGER_ENGINE_IDLE_IN_TXN_TIMEOUT_MS` ships DEFINED-BUT-NOT-APPLIED (disabled) — the engine-level idle-in-txn backstop is deliberately inactive until a trustworthy measurement exists (DR-29/DR-7; enabling at a 30 s floor would coincide exactly with the legitimate pool_timeout wait and risk killing a legitimate long waiter). Narrow-and-disclose applied correctly — including the boot log line "MANAGER_ENGINE_IDLE_IN_TXN_TIMEOUT_MS DISABLED — no Phase-0 measurement" and the OQ-3 conditional carve-out (mandatory `SET LOCAL statement_timeout = 0` inside manager.py:8760 if ever enabled). Consequence: F2 does not bound idle-in-txn at the engine level until enabled; the disclosure is honest. (Independently re-verified by the approver at final-implementation-spec.md F2.1/F2.7 and the knob table.)
4. The plan references the forbidden review-history files by filename in its Delta1..Delta8 closure block; references are by name only and the plan content is self-contained for evaluation.
5. Worker-disclosed unverified areas (disclosed, not blocking): the 46-pin inventory (final-implementation-spec.md §5) was asserted to match test-strategy.md §3 row-for-row but was not independently row-verified; `audit-method.md` and `wi-78967dba-verdict.md` were cited by filename only. Recommend pin-count consistency be confirmed when pins land — the C3 collected-count gate would surface any missing pin at run time.

## Worker Evidence Basis

- ~25 file:line anchors spot-checked against actual worktree code (all matched; minor drift acknowledged as C4)
- Gate ladder verified: canonical 17-entry ladder (C3c) + legacy-to-canonical mapping (C3b); lane-wide false-green invariant (every gate asserts collected count > 0)
- Premise ledger §3.1 (P-1..P-11), routed-items §12 (9 items with owning commissions), safety-precedence §13 (3 narrow-and-disclose cases), release posture §15 all checked
- Common-traps checklist completed (halo, negative cases, implicit assumptions, complexity hiding, dependency blindness)
- Approver report-sanity spot-check: duplicate DR-36 confirmed at plan-overview.md:498/:500; disabled-backstop disclosure confirmed at final-implementation-spec.md (F2.1/F2.7/knob table). Worker verdict stands on accurate evidence.

## Skills Used

plan-approval

## Session IDs

- approve-worker-plan (worker): f2dff7d1-3a60-4465-aa8f-40e1250427d2

---
*Tracking: .agents/approver/db-pool-idle-txn-tracking.md (this file)*
