# W5 Claim-Order Pin Amendment (ee66f0eb two-tier wake lane)

Date: 2026-09-06 | Gate: terminal-report-wake verification | Commit: `9b0dab41` (worker 93652330, quick-fix skill) | Status: **PENDING LEADER RATIFICATION**

## Root cause
`tests/unit/services/test_w5_claim_order_wc_wake.py::TestW5TwoTurnClaimOrder::test_user_msg_first_created_claimed_first_report_second_turn` pinned the PRE-fix contract: claim order between a user-msg PROCESS_MESSAGE and a later PROCESS_REPORT is "symmetric, purely created_at-driven … not type-biased". Commit ee66f0eb deliberately makes claim order type-biased (`ORDER BY CASE WHEN task_type='process_report' THEN 0 ELSE 1 END, created_at ASC`) — that priority IS the 7807e521 fix (14m49s report starvation under strict FIFO). Deterministic head-on contract collision; not flaky, not a delivery regression (exactly-once 4/4, barrier-race single-winner, pause-gate dominance all green).

## Fix applied (12+/7−, one file, <20-line budget)
- Flipped the two assertions: first claim = PROCESS_REPORT (across tiers), second = user-msg task; `complete_task` retargeted to the report row so the second turn is genuinely exercised (non-vacuous).
- Docstring + comment cite the superseding contract and canonical tests (`tests/integration/test_report_wake_priority_claim.py`).
- Mirror variant (report created first) and all other 16 W5 + 39 D1/pairing tests untouched.
- Pack re-run: 57/57 PASS (was 56P/1F).

## Why amendment (not quarantine)
Branch-caused deterministic failure cannot be "pre-existing"; quarantine is for flakes/base-evidenced failures. Leaving red blocks merge; the new contract is independently pinned by 13 new tests + PG verifier + pre-fix worktree proof.

## Lesson
When a fix intentionally changes an ordering contract, pre-existing "symmetry" pins in adjacent suites (here: WC-wake W5) collide deterministically. Gates should grep neighboring suites for ordering-invariant language ("symmetric", "not type-biased", "purely created_at") BEFORE running, and pre-stage the adjudication: intended-new-behavior amendments are leader-ratification material, never silent.
