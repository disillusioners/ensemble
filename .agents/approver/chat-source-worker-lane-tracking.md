# Tracking: chat-source-worker-lane

Plan: Chat-Source Worker Lane — dedicated 2-worker lane for chat-source instances, isolated from the default worker pool
Slug: chat-source-worker-lane
Artifact: .agents/shared/planning/chat-source-worker-lane/ (plan-overview.md, decisions.md, phase1/2/3-plan.md; 1,014 lines)

## Iteration 001 — REJECTED (2026-09-18)
Workers: approve-worker-overview (ff13829d, plan-approval, overview+decisions) → APPROVED, 0 blocking, 6 notes.
         approve-worker-phases (2514688e, plan-approval, phase1/2/3) → REJECTED, 2 blocking, 7 notes.
Both reports well-formed, independently code-verified (~60 file:line claims; 19-site wake census confirmed exact).

Blocking issues to resolve for iteration 002:
1. Unsafe P1→P2 intermediate state — phase1-plan.md §Coupling line 28 ("can ship first") + Tasks 6-7 (lines 19-20). Phase 1 folds default-lane chat exclusion into the sole claim caller (task_processor.py:1279, no lane arg) while the chat pool only exists in Phase 2 (manager.py:6410). Phase-1-only activation (merge → rebuild+restart convention) makes every telegram:/slack:/discord: row (minted at registry.py:857 today) permanently unclaimable, zero runtime signal. Fix (pick one, write into plan): (a) atomic P1+P2 merge/activation coupling; (b) make default-lane exclusion conditional on chat-pool presence; (c) land predicate in P1, flip strictness in P2.
2. Phase 2 Task 5 ordering contradiction — phase2-plan.md Task 5 (line 17) + decisions.md D10.4 snippet (lines 369-387). Prescribed order runs stop(); pool=None BEFORE the alive-check loop; existing code already None's the default pool (manager.py:6713-6715) → loop iterates (None, None) → A5.1 acceptance ("WARNING iff worker alive after stop") is dead code for BOTH pools. Fix: snapshot [p for p in (default, chat) if p] before the stop/None blocks; iterate snapshot after both stops.

Deduped non-blocking notes: path typo daemon/worker_pool.py → daemon/services/worker_pool.py (D10.4/D10.5/Risk #7); D10.3 ctor example omits task_processor positional; SC#6 conditional on Phase 3 Task #4a + stale "processed serially" fragment in overview SC6 row (contradicts corrected F10 text + phase3 Task 4); D10.4 private-attribute reach into pool._workers (accessor follow-up); Phase 3 test-gate stale paths (test_job_recovery_service.py under tests/job_queue/ not tests/integration/; test_joblock_sweep_lifecycle.py actual name; waiting-children suite actual path); tests/unit/routers/test_sources.py must be CREATED (exit #10 bookkeeping: "4 new files" vs 3 enumerated); D1 raw text() SQL — render EXISTS as literal portable SQL; D6 stale cite models :338 → daemon/repositories/job_queue/models.py:213; Phase 2 exit #1 num_workers inconsistency (WORKER_POOL_SIZE line 63 vs 1 line 92); D10.3 boot-order narrative vs actual code order (:6683 → :6697 → :6708); AUTOSTART cite drift (:224-225 vs :230/:247).

Status: awaiting revised plan → iteration 002.

## Iteration 002 — REJECTED (2026-09-19)
Workers: approve-worker-overview (1f7dd48b, plan-approval, overview+decisions) → APPROVED, 0 blocking, 4 notes.
         approve-worker-phases (fc676e5e, plan-approval, phase1/2/3) → REJECTED, 1 blocking, 6 notes.
Both reports well-formed; ~40 fresh file:line seam claims verified at pinned HEAD c8855e8a; all 19 D5 wake sites re-confirmed exact.

Prior blocking issues RESOLVED: #1 (P1-only strand) closed via B1 conditional fail-open (chat-lane-active flag, 3-state semantics — traced phase1 §Coupling + phase2 Task #2/#5 + decisions D2); #2 (Task 5 ordering) closed via B2 snapshot-before-stop.

Blocking issue to resolve for iteration 003:
1. phase1-plan.md Exit Criterion #10 (line 75) change-scope fence omits test/packs/origin_contract_e2e_probe_test.py — Task #4 (line 16) explicitly modifies it (3 gate_422 e2e cases + stale-ref fix at :33) and the "Pin extensions" list (line 102) includes it → Phase 1 exit criteria unsatisfiable as written (implementer must skip the D10.1 Pin-4 e2e matrix or violate the phase-exit fence). Fix: add the file to the #10 allowed-file list (one line).

Deduped non-blocking notes: (overview worker) plan-overview line 86 stale "Phase 8" ref; plan-overview line 38 stale models:338 not re-aligned to decisions D6/N8 (:213); phase1 §Coupling "covered by Task #5" should read Tasks #6-7; B1 test-location ambiguity (§B1 inline marker vs phase3 Task #1 — coder's choice permitted). (phases worker) teardown snapshot iteration order (list built default-first vs chat-first stop mandate — iterate reversed or snapshot chat-first); _chat_lane_active transport unspecified across 2 TaskRepository instances (manager.py:715/:6444) — pin per-claim read via shared callable; phase3 Task #7 log-parsing from in-process harness — use in-process capture (precedent tests/unit/job_queue/test_joblock_sweep_lifecycle.py); phase1 Task #5 validator envelope naming (JobValidationError vs sources.py ErrorResponse/ErrorCodes) + SourceType enum .value; manager._pools should init [] in __init__ (boot-window AttributeError guard); minor naming drift (EligiblePendingSweepService actual class; shutdown_worker_pool at manager.py:6713).

Status: awaiting revised plan → iteration 003 (FINAL before escalation).

## Iteration 003 — APPROVED (2026-09-19)
Workers: approve-worker-core (1b3a62d8, plan-approval, overview+decisions) → APPROVED, 0 blocking, 3 notes.
         approve-worker-phases (9995b439, plan-approval, phase1/2/3) → APPROVED, 0 blocking, 9 notes.
Both reports well-formed and evidence-rich: worker A re-verified 19/19 D5 wake sites exact at feature/chat-source-worker-lane @ c8855e8a and independently re-derived the D8 deadlock analysis; worker B verified 11+ file:line citations and confirmed all referenced test files exist on disk.

Prior blocking issue RESOLVED (verified fresh, not from history): iteration-002 Exit #10 fence omission closed — Task #4's test/packs/origin_contract_e2e_probe_test.py changes now plan-covered (worker A verified e2e cases at :281-284 + stale-ref fix at :33; worker B verified exit criteria name every task-required gate artifact — no unsatisfiable fences).

Deduped non-blocking notes (carry into implementation): (core) decisions.md §D6 stale admission cite :3484-3538 → instance_messaging.py:~2486-2498 (substance verified, pointer wrong); plan-overview Open Question #1 dynamic boot-line join vs SC#8 literal string — pin one; manager.py line drift (:6409/:6711). (phases) phase1 Task #5 "or equivalent" wording vs Exit #6 new file (N6 resolves — tighten); SC#6 conditional on Task #4a fixture gate (escalation defined); D8 semaphore cap may become P3 follow-up if 30s no-deadlock window fails — do NOT silently widen; Phase 1 ships alone only under B1 fail-open; phase2 Task #3 code-shape choice — requirement unambiguous, pin shape at impl; SC#13 subtractive vs D9 multiplicative framing (D9 authoritative); pre-ship git grep census for literal telegram:/slack:/discord: posters as Task #4 pre-req (D10.1); phase3 Task #2 slack/telegram fixture prefix mismatch — pick one; /readyz stats deferred P3 per D12.

Status: APPROVED — plan closed at iteration 003 (escalation cap not needed).
