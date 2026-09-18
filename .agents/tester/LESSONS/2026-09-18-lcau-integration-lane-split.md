# LESSON: Integration-lane splitting must account for the real-judge-LLM surface (2026-09-18, LCA user-intent gate)

## Context
LCA user-intent merge gate (a6442bff..47b56df8). The attestation integration family (33 files) was split fast(31)/slow(2) based on discovery timing that identified only 2 heavyweight files. The 31-file "fast" pack FAILED on first run — exit 1 at 32s with no FAILED summary: pyproject's `timeout=30, timeout_method=thread` killed the FIRST test mid-LLM-stream (`llm_stream_watchdog.py:246`, SSE bytes from the live judge endpoint).

## Root cause
11 of the 31 integration files make REAL judge-LLM calls (no `_invoke_judge_llm` stub):
- 6 files exceed the 30s per-test cap outright: idle_orphan_incident 113s, revive_after_escalation 79s, incident_acceptance_lca 64s, bound_escalation 51s, ledger_reset 45s, mode_tri_state 41s.
- 5 files sit at 16–33s total (pass, but with zero latency headroom).

Per-file runtime, not file COUNT, is the splitting signal for this family. The repo's own precedent (`lca2_matrix_10..15`: 1–3 files/pack, `--override-ini="timeout=300"`) already encoded this — discovery's single-file timings flagged only the 2 extreme files, missing the wider real-LLM surface.

## Fix applied (test-lane only, no production changes)
Re-split into 5 packs balanced by MEASURED per-file seconds: intf-f (20 stub-fast files, default timeout), intf-m (5 mid files, per-test 120), intf-s1/s2/s3 (heavyweight trios/solos, per-test 240). All 171 selected tests green, every pack ≤165s inner against 280s inner / 300s outer caps.

## Key diagnostics that prevented a false FAIL verdict
1. Standalone re-run of the first offender with `-o "timeout=120"` → 1 passed in 51s (test is healthy; the cap was wrong).
2. Reproducer at base `a6442bff` → identical timeout shape (base-identical, NOT branch-caused).
3. Kill-switch check: `ENSEMBLE_LEADER_ATTESTATION_LLM_JUDGE_ENABLED=0` is NOT an escape hatch — gate tests assert `judge_invoked=True`, disabling produces REAL reds (verified: 1 failed / 168 passed).

## Rule going forward
Before packing an attestation integration family: grep for stubbed-judge seams per file (`_invoke_judge_llm` / mock_llm imports), and time ANY unstubbed file standalone before assigning it to a "fast" pack. Split by measured seconds with ≥40% headroom under the 280s inner cap. Per-test override values: 120 (mid), 240 (heavyweight) — cap-raise is never the answer; the split is.
