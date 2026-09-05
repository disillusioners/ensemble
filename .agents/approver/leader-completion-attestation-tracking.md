# Tracking: Leader Completion Attestation

Plan dir: .agents/shared/planning/leader-completion-attestation/
Branch: feature/leader-completion-attestation

## Iteration 001 — 2026-09-05 — VERDICT: REJECTED

Workers (skill: plan-approval, cold context):
- approve-worker-coherence (3d4add9e-ba9b-4d0a-9a97-c6c1989e3e74) — REJECTED
- approve-worker-phases-1-3 (371b3d0d-eb9f-4e46-95c4-b340e8c3f947) — REJECTED
- approve-worker-phases-4-6 (6d969f21-c8f4-4708-aba7-002e5c38a615) — APPROVED

### Blocking issues (aggregated, deduplicated)

1. Canonical gate-decision enum cited inconsistently across primary artifacts.
   Canonical = 5 values (allowed | denied | terminal_after_bound | dry_log |
   allowed_legitimate_pending_wakeup, single source of truth phase4-plan task 4.5).
   Found: plan-overview.md:32 (3-value), requirements.md:169 NFR-14 (3-value,
   different casing allow/deny), architecture-recommendation.md:71 (4-value),
   plan-overview.md:97 (4-value). Phase files use the canonical 5-value form.
   Fix: replace all four truncated citations with a pointer to the canonical enum.

2. Ledger column name divergence: requirements.md + architecture-recommendation.md D5
   use `denied_count`; plan-overview.md:60/:100/:234 + phase3-plan.md (tasks 3.2/3.3)
   use `attestation_denied_count`. Fix: pick one canonical column name, sweep all files.

3. R2-input facade method names — three variants for the same deliverable
   (Phase 2 task 2.3 / CR-1): requirements.md:62 + plan-overview.md:62
   (`get_pending_children|get_queued_wakeups`); phase2-plan.md tasks 2.3/2.5/2.6
   (`count_pending_children` + `get_queued_or_expected_wakeups`); phase3-plan.md:14/:22
   (`manager.get_subtree_state`). Fix: single canonical method spec.

4. Decision status contradiction: decisions.md (OPEN section) + plan-overview.md:249-256
   list D3, D4, D7, D9, D10 as OPEN; architecture-recommendation.md:22/:23/:26/:28
   lists all five as RESOLVED with specific values (D3 leader-only; D4 N=3 Pattern C;
   D7 no-arg idempotent NOT privileged; D9 moot; D10 ANY-in-last-N post-compaction).
   decisions.md status legend says OPEN means OPEN, full stop. Fix: reconcile status.

5. Phase 3 exit criterion (phase3-plan.md:126 + summary :16) labels AC-6.4 with a
   4-trigger reset description that actually spans AC-6.4 (fresh instance denied_count=0,
   requirements.md:340-344) + AC-6.5 (reset on allow, requirements.md:346-350) +
   terminal_after_bound reset; AC-6.5 absent from checklist by ID. Exit gate not
   verifiable against requirements. Fix: name AC-6.5 explicitly and split the
   description per AC.

### Notable non-blocking notes (selection)
- phase6 task 6.1: `priority=1` rationale wrong — 1 is default user priority
  (instance_messaging.py:2178); preemption needs priority=0.
- Nudge injection should fire ONLY on Decision.denied, not terminal_after_bound;
  test 5.6 catches it but impl spec (phase4 tasks 4.3/4.4) should state the guard.
- task 5.17 scripted-chat-model seam blocks tests 5.5/5.14/5.15/5.18 — order it early.
- O1 boot-assert call site in manager not specified; O8 unit-assert ownership unassigned.
- Loop-breaker pop-site count: plan says 3 (manager.py:3734/:3798/:8548 — verified);
  worker B found 3 more in instance_lifecycle.py:2043/:2664/:2903 (metadata only;
  task dropped in C1c).
- Possible Phase 5 task 5.7 reset-trigger drift (only revive-from-COMPLETED listed
  vs Phase 3's 4 triggers) — flagged by worker B as unverified; reconcile.
- plan-overview.md:197 MVP nudge AC summary misses AC-4.3/AC-4.4.
- decisions.md:629 stale path daemon/services/compaction.py (actual daemon/compaction.py).

### Verified positively
Architecture sound (D1=B, R1/R2, D2 tri-state, C2 both-branches exit test, fail-open
seams, rollback per component, coverage honesty). All FR/NFR/E2E traceable. Worker C
verified ~30 load-bearing file:line claims against the code — all matched.
