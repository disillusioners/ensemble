# Tracking: pause-resume-terminate-tree-fix

Plan: Pause/Resume/Terminate Tree Propagation Fix
Branch: feature/pause-resume-terminate-tree-fix @ 03df9108
Artifact: .agents/shared/planning/pause-resume-terminate-tree-fix/ (10 files, ~2,670 lines, Rev 2.1)

## Iteration 001 — 2026-08-24
Mode: Plan Approval (large-plan exception: 3 section-partitioned workers, parallel)
Workers:
- approve-worker-foundation (784cbbac-c178-409d-9b16-bc31d8511639) — overview/decisions/architecture → APPROVED, 0 blocking, 5 notes
- approve-worker-phases12 (7c18e719-9a75-45b4-b858-a38e41a93384) — phase1+phase2 → APPROVED, 0 blocking, 9 notes
- approve-worker-phase3 (5daa3a89-4a6d-42a5-915f-c55d3a591fb3) — phase3 + cross-phase → APPROVED, 0 blocking, 10 notes

Verification highlights: ~73 file:line citations spot-checked across workers; all load-bearing claims verified against daemon/ sources. Rev 2.1 reviewer-council corrections (W1-W8, AF1/AF2 C1/C3) confirmed folded. Hard constraints (named turn transitions, canonical terminal_reason, dependency_bus sole completion authority, pause-writes-nothing-to-JobItems, JAFP) preserved. Kill-switch (ENSEMBLE_CASCADE_LINEAGE) + per-phase rollback documented. Merge order P1→P2→P3 arbitrated (overview §4).

Deduplicated notes (non-blocking):
1. Citation line-drift on several helper functions (worst: _schedule_explicit_handle_resume cited :6306-6527, actual :8043); all function identities and behavior claims correct — locate by symbol at implementation time.
2. Stale overview aggregation stats: §3 says P2=12 tasks (Rev 2), binding phase2-plan Rev 2.1 has 13 (Task 2.13 added by W4); §6 test-count line says 33 min but per-phase binding counts sum higher (P2 binding = 25). Per-phase plans are authoritative; overview summary refresh recommended before dispatch.
3. Cross-phase composition test (terminate-of-already-terminal-child during P2 rebase) lives only in overview §4.3 prose, not a numbered P2 task — dispatcher should surface to rebaser.
4. Stale docstring in _compact_fired_watchers_for_paused (:3608-3696) says "intended to be wired into resume path (Phase 3)" but already wired at instance_lifecycle.py:2403 — update during implementation.
5. FT-001..005 follow-up tickets correctly deferred with full content specified.

Verdict: APPROVED
Status: APPROVED (final)
