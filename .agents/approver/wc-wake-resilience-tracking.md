# wc-wake-resilience — Approver Tracking

Plan: WC wake/resilience fix program — branch feature/fix-wc-wake-resilience @ f4091734 (base latest @ 8a30f75b, 29 commits)
Related-but-distinct plan (do not merge records): wc-wake-report-integrity-tracking.md (earlier D2.5-FLIP branch)

## Iteration 001 — 2026-09-11 — verdict APPROVED
Workers (2, section-partitioned, large-program exception; cold context):
- approve-worker-mechanisms (e87f52bc-2698-47cc-aba0-f26afcdcfda6) — skill plan-approval — mechanisms (a)–(l) + targeted test runs.
  Verdict: APPROVED, 0 blocking. 261/261 tests passed across 20 files (incl. real-InstanceManager integration tier). Notes B1–B7.
- approve-worker-crosscutting (6d333324-5940-4880-9046-c4b6df1a20b2) — skill plan-approval — owner policy / architecture / risk / smell (static).
  Verdict: APPROVED, 0 blocking. Owner policy compliant (4 new cadence knobs only, always-on; ENSEMBLE_WC_WAKE_ENQUEUE removed entirely). Notes N1–N7.

Aggregation: no blocking findings from any worker → APPROVED. No upgrades made (cardinal #4); overlapping notes deduped.
Prominent non-blocking notes (post-dedup):
- Mechanism (b) claim-notify satisfied structurally via A3 periodic sweep backstop (worst-case ~90s wake latency), not a direct notify-after-claim — both workers independently accepted.
- B2 exception scope around get_instance_info narrow (KeyError/AttributeError only; manager.py:9296).
- W-B residual: heartbeating-but-wedged child suppresses B3 release indefinitely — documented in-code.
- Wire-shape change: WC-targeted sends 202→200; resume payload gains fields.
- Stale comment daemon/constants.py:238-245 references the deleted ENSEMBLE_WC_WAKE_ENQUEUE kill-switch (doc drift only).
- D2 pre-existing logger kwarg defect (repositories/task/repository.py:3128-3132) survives — ancestor-introduced, not branch-caused.

Status: APPROVED (closed at iteration 001)
