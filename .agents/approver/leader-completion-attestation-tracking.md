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

## Iteration 002 — 2026-09-05T13:49Z — VERDICT: REJECTED

Workers (skill: plan-approval, cold context):
- approve-worker-consistency (d7d310e0-385c-4e2d-891f-02285b87ecf1) — REJECTED (2 blocking)
- approve-worker-codeground (d4036105-5968-4dca-b26a-9721c1ea6210) — APPROVED (0 blocking, 5 notes; anchors verified at HEAD @ 46c682dc)

### Blocking issues (aggregated)

1. decisions.md internal status contradiction (carried over, morphed from 001 #4).
   Per-decision Status lines mark D3 (:221), D4 (:259), D7 (:384), D9 (:478),
   D10 (:511) RESOLVED (CLOSED-by-leader 2026-09-05, citing architecture-
   recommendation.md), but the Dependency Graph (:564-579) + "Decision-order
   reality" line (:581) still list all five as OPEN. 001's fix updated the
   per-decision lines but left the dependency-graph tail stale.
   Fix: reconcile the Dependency Graph + decision-order section to the RESOLVED
   statuses (or explicitly scope why they remain OPEN).

2. plan-overview.md:195 Success Criteria row #3 retains pre-R1 wording.
   Row says the deny path => "recovery enqueued"; post-R1/C5 MVP deny path is the
   in-graph HumanMessage nudge with NO manager.enqueue_message (AC-3.1,
   requirements.md:250-254, correctly worded there). The summary row contradicts
   the AC it claims to verify.
   Fix: reword row #3 to the in-graph-nudge semantics.

### Verified fixed since 001
- Canonical 5-value gate-decision enum consistent across all 12 files (001 #1).
- Ledger column naming consistent: attestation_denied_count /
  completion_gate_escalated (001 #2).
- Facade contracts consistent: count_pending_children /
  get_queued_or_expected_wakeups / increment_attestation_denied_count /
  reset_attestation_denied_count / set_completion_gate_escalated /
  get_attestation_denied_count (001 #3).
- Phase3 exit criterion AC-6.4/6.5 split no longer flagged (001 #5).
- Worker B verified load-bearing code anchors at HEAD @ 46c682dc; architecture
  sound (D1=B mirrors language_check/create_should_continue precedent; R2
  grounded in count_pending_for_target_sync; tri-state dry-default; rollback
  5-step explicit; fail-open seams spec'd).

### Notable non-blocking notes (selection, merged A+B)
- Six AC numbers defined in requirements.md never cited in any phase Exit
  Criterion checklist (AC-13.1, AC-13.2, AC-10.3, AC-10.4, AC-E2E-7, AC-E2E-8),
  though exercised implicitly (tests 5.8/5.10; E2E-8 relocated pointer at
  phase5-plan.md:316-326).
- phase4-plan.md:37 labels AC-7.5 as restart-read; AC-7.6 is restart-read
  (requirements.md:390-394); phase4 bottom checklist (:168) conflates them and
  misses AC-7.7/AC-7.8.
- plan-overview Success Criteria table maps only ~14 of the AC set; AC-3.3/3.4,
  AC-4.1-4.4, AC-6.5/6.6, AC-7.7-7.9, AC-10.3/10.4, AC-13.x, AC-E2E-1b/6/7/8
  unmapped in the table.
- Line-citation drift (substance correct, anchors wrong):
  _tool_registry.py:454-493 should cite :106 (register_tool_category);
  graph.py:6379-6383 should cite :6459-6484 (conditional-edges wiring);
  job_feedback_observer.py:1698 "early-return" is a mislabel; Step-2 bare
  terminal write is :3753 (range :3740-3752 covers the logger).
- Phase5 task 5.3 Command(goto=...) return shape deviates from the language_check
  precedent (plain dict + routing fn, graph.py:2666-2685); Command not imported
  in graph.py — align with precedent or justify the deviation.
- C5 interpretation fork pending user veto (architecture-recommendation.md §8) —
  user-decision risk, not a planning defect.
- OS-2 no-leader-turn cascade class deferred to phase6 (acknowledged boundary).
- Compaction precondition (task 1.7 / D10(b)) uncertain; default config in safe
  zone (WINDOW=3 == min_recent_window=3); D10(b1) fallback specified.
- FakeMessagesListChatModel availability asserted by plan, not independently
  verified by workers.


---

## Iteration 003 — 2026-09-05T14:41Z — VERDICT: REJECTED (ESCALATED — max iterations reached)

Worker (skill: plan-approval, cold context): approve-worker-plan (dab80296-2bb4-4abf-8fb9-3cb9eb8e933d) — REJECTED (4 blocking; ~30 code citations spot-checked, nearly all exact).

### Blocking issues (aggregated; all four upheld, none downgraded)

1. Counter-reset semantics contradiction (D5 closed ruling vs its own carriers).
   decisions.md D5 LEADER RULING (:323): attestation_denied_count resets on exactly four
   triggers — (1) attested allow, (2) terminal_after_bound, (3) revive-from-COMPLETED via
   NEW top-level message, (4) instance creation; in-graph deny-nudges NEVER reset it.
   Found carrying the OPPOSITE semantics (reset on EVERY allow, incl. un-attested R2):
   requirements.md FR-6 (:60); AC-E2E-1b step 3 (:507); phase5 5.2(h) reset_denied_count=True;
   5.13(i); 5.7 (claims the R2-allow path exercises trigger (1) — it is not attested);
   phase3 3.5 first clause (mislabeled allowed_legitimate_pending_wakeup as allow-with-attest
   → reset), contradicting its own second clause and phase3 3.3/exit criterion. Material:
   deny(1) → delegation-allow(reset 0) → deny(1) never reaches bound 3 — the loop protection
   the ruling exists for is defeated; phase3 3.3 vs phase5 5.2(h) ship different behavior.
   [Continues the reset-semantics thread from 001 #5 + 001 note re 5.7 drift; now
   contradicts the CLOSED D5 ruling itself.]

2. Escalation-flag lifecycle undefined.
   phase5 5.14 asserts completion_gate_escalated=False at start of the post-escalation
   mission; phase6 6.4(a) asserts False after allow — but NO file defines a reset trigger
   or method. phase3 3.3(c) ledger methods = increment / reset-counter / set-escalated /
   get-counter ("persists for postmortem"); FR-6 + D5 both say "persistent". Tests assert
   behavior the specified ledger cannot produce.

3. AC-E2E-6 orphaned — promotion mechanism loses its data source.
   requirements.md :553 assigns the recorded-corpus replay driver
   (tests/support/recorded_corpus_replay.py) + fixtures (tests/fixtures/recorded_leader_missions/)
   to "Phase 5 task 5.16", but 5.16 is the structured-logging-schema test owning neither;
   no phase5 task owns them; Phase-5 exit criteria list AC-E2E-1..5,7,8 — AC-E2E-6 absent.
   The ship-dry → adjudicated-enforce flip has no corpus in the MVP.

4. Resolver failure-posture contradiction + false precedent (kill-switch surface).
   requirements.md AC-7.9 (:411): resolver raises ResolverError (fail-CLOSED), citing the
   WC-wake resolver's "fail-closed posture for typo'd keys". phase4 4.1(d): chosen Pattern C
   FAILS OPEN on env typo. Code check: WC-wake resolver actually fails OPEN
   (_resolve_wc_wake_enqueue_enabled — one-shot WARN + default, no raise). Two normative
   statements prescribe opposite operator-typo behavior on the gate's config surface and
   the precedent claim is factually false. FR-8 "raises ResolverError if any non-tri-state
   key is set" is ambiguous (legacy key present vs invalid canonical value).

### Notable non-blocking notes (selection)
- Overview SC #5 pass-threshold cites the Phase-6 durable path (NO JobItem row) — unevaluable
  at MVP and contradicts MVP AC-4.3; re-point to AC-4.1–4.4, move the durable row to Phase 6.
- Task-count drift: overview 7/8/6/6/9 = 36 vs actual (Phase 2 = 5 active + 5 archived
  headers; Phase 5 has no 5.17 — gap explained in §6.7 O7 but the number is skipped).
- phase1/phase3 exit criteria pre-checked [x] (some items depend on Phase 4/5 artifacts)
  while overview marks all phases pending.
- Citation misses: daemon/services/llm_helpers.py and daemon/daemon.py do not exist
  (build_instance_llms = daemon/graph.py:4382); min_recent_window defined at
  daemon/config.py:729, not compaction.py; plan-overview:38 OS-1 stale
  (USER_ORIGIN_SOURCES landed, 5ef35262a) — phase6 already flags moot.
- Gaps-table residue: G3/G4/G6 retain "Architect to confirm" vs traceability RESOLVED;
  D7 Impacted-Components PRIVILEGED sub-question still "open"; G12 (operator-termination
  bypass) honestly OPEN — low risk (gate is pre-END in-graph).
- O4 "CHOSEN" upsert INSERT … ON CONFLICT (instance_id, denial_epoch) does not fit the
  chosen instance-row-column storage; no denial_epoch column in migration 3.2.
- Naming slips: phase3 risk 6 uses admission_state on the instance row (job-queue concept;
  instance rows carry status); FR-10 gate_decision vs canonical decision.
- Rot cluster: phase4 4.4 duplicated stale test-notes row (kill-switch ON/OFF terms);
  phase2 risk 3 calls closed D10(b) "unresolved"; boot-log prefix drift
  (leader_completion_gate: vs attestation_resolver:); phase6 §6.8 ≥14d vs ≤2wk inequality
  flip (intent discernible); phase5 :328 truncated table row ending "it |".
- Rollback reverts resolver to hardcoded mode="enforce" — hotter than the ship default
  (dry); needs an explicit justification line.

### Verified positively
C2 both-branches composition real (graph.py:2718-2721 + conditional edges ~6459-6486);
fail-open carve-out precisely matches the W4 narrow-tuple precedent; R1/C5 fork +
dual-delivery locks consistent across phases 2/5/6; otherwise exceptional citation
discipline (job_feedback_observer :1698/:1572-1577/:3083; _loop_breaker_state exactly 3
sites; child_reports pins; tools/instance.py:4475-4477; origin-stamping; priority
semantics).

### Relation to prior iterations
001's five blockings and 002's two blockings were NOT re-flagged by the 003 worker
(canonical enum, ledger column names, facade contracts, decision statuses /
dependency graph, SC row #3). All four 003 blockings are new or deeper manifestations:
#1 continues the reset-semantics thread; #2/#3/#4 first surfaced at this depth. Recurring
root cause: leader rulings and AC renumbering land mid-cycle without a verbatim sweep of
every declared carrier.

### Disposition
3rd rejection — max iterations (3) reached. active.md Status set to ESCALATED. Verdict
returned to caller: REJECTED with escalation note. Leader to present full tracking
history to user.
