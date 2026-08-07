# Watchover Feature — Approval Tracking

## Iteration 001 (2026-08-05)
**Verdict: REJECTED**

### Blocking Issues (9)

**Cluster A — Deny-whole-batch propagation failures (AD-9 / LD-1)**
1. `requirements.md` AC-EC.9 (L452-456) never updated to deny-whole-batch — still describes per-tool-call execution + per-call counter increment; contradicts overview AD-9 (L27) and propagation-log 🔴-4 "✅" (L273). [w-overview #1]
2. `technical-analysis.md` §G (L614) + TD #11 (L687) still describe Option A as "mixed-batch finalization" with AIMessage replacement + finalize node — contradicts LD-1 (`architecture-recommendation.md` L435) which eliminated `watchover_finalize_denials`. [w-architecture #2]

**Cluster B — Bifurcated failure handling contradiction (LD-2)**
3. `technical-analysis.md` §B3 (L309-321) + pseudo-code (L254) still specify fail-closed (Deny+count) on infra errors (timeout, provider exceptions) — contradicts LD-2 / CR-2 (`architecture-recommendation.md`). Would recreate the self-DoS cascade CR-2 was created to prevent. [w-architecture #1]

**Cluster C — Auth assumption factually wrong (FR-27)**
4. `requirements.md` Assumption #19 (L507) claims "existing session ownership check (project already has this pattern)" — factually false per overview OQ-2 (L246) / TD-9 (L226) / NFR-9 / AC-27.1: no such primitive exists. Safety risk on a terminate-capable toggle. [w-overview #2]

**Cluster D — Stale open questions (documentation currency)**
5. `technical-analysis.md` Open Questions 3/4/5 (L710-712) marked "Confirm" but are answered elsewhere (AD-7 child inheritance; phase1 T1.2 sensitive reads; phase1 T1.1 model fallback). [w-architecture #3]

**Cluster E — Phase plans missing committed TD implementations (safety-critical)**
6. TD-8: No phase writes `instance_metadata["watchover_pending_termination"]`; all phases use RAM-only `_deferred_watchover_terminate`. Crash between graph END and post-graph callback loses the marker → watched instance survives. Affects 3-strike termination core. [w-phases #1]
7. TD-2: No phase adds `manager.wait_for_instance_quiescent(instance_id, timeout)`; phase3 T3.5 calls only `pause_instance_cascade` (insufficient per overview). Activation atomicity (FR-28 / NFR-15) not delivered. [w-phases #2]
8. TD-6: No phase implements raw-tail fallback for empty/short history; phase3 T3.4 calls `compact()` with no fallback → empty `watchover_context` on fresh instances → arbitrary verdicts. [w-phases #3]
9. TD-12: No phase modifies Instance API schema (`schemas.py`/`instances.py`) or frontend Instance model; phase4 T4.6 stays localStorage-only — the exact problem TD-12 targets. [w-phases #4]

### Status
- active.md → Iteration: 002, Status: IN_PROGRESS
