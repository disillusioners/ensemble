# Phase 2 Report-Integrity Review (Deep-Review council) — 2026-08-30

Branch: `feature/wc-wake-report-integrity`, range `7a484afb..26fe4d9f` — **6 commits, not 5** (`fe64ea06` phase-1 gate pack is inside the range; covered).
Council: governor `36bbff54-c9c4-4ceb-b8bc-62c2f130bf98`, 2 councilors (agentic + coding, canonical-model dedup caps at 2), both `code-review`.

## Verdict: ✅ APPROVED — 0 🔴 / 2 🟡 / 8 🟢

## Gate question answer
B-guard flag-OFF is **truly OFF** for the (b) enforcement path + WC-wake routing: first-statement short-circuit (`report_integrity_guard.py:769-771`), no auto-flip writer, flag-OFF behavioral tests pass (real `_process_child_completion_db_sync`, zero enqueue calls), off-bytecompat 4/4 vs base `1f8f8ed4`.
**Scope precision:** Wave-1 instruments ((c) marker content mutation, NR-3 counter lines) are **always-on by design** (plan §4.1 "no config flag — additive"). Their rollback seam is `SANITY_FLAG_VERSION`, NOT the B-guard flag — doc gap → Suggestion 3.

## Pre-D2.5-FLIP gate (must land before operator flip)
- **W1** (c)-marker false positive: `child_reports.py:1606-1711` filter drops `content=""` assistant msgs → pure tool-call AIMessage shape invisible → false `low_evidence=True` on legitimate minimal-tool reports → marker text lies, NR-3 inflated, 12 (d) parents spuriously re-verify. Fix: scan unfiltered assistant msgs for `any(tool_calls)`, keep width half; add fixture; D2.18 amendment.
- **W2** Latent unguarded stamp paths: `job_feedback_observer.py:2799-2869` + `error_reporting.py:318` lack the (b) attach (dead today; NOT in accepted-deviation list). Fix: attach stage-ii log or assert unguarded-by-design.

## Suite baselines (independently cross-validated, identical)
Phase-2 set **299P/17S/0F** (upward drift from ~238, benign); job_queue **1569P exact**; prompt **49P exact**; child_reports+watchdog 94P; guard+registry 45P; fail-open+observer+reconciler+repair 106+61P; NR-1 repro 5P (red→green); archive_lifecycle 5F = pre-existing quarantine.

## Cross-validated positives
Predicate durable-row only (zero pending_watchers/cache reads), B.S.7 ordering pinned by probe tests, exactly 4 stamp sites + 2 child-self-sites skipped; `system:report-integrity-guard` unforgeable (reserved prefix `constants.py:442`, HTTP 422 at `routers/jobs_crud.py:299`, not in USER_ORIGIN_SOURCES); kill-switch restart-required dual-read; no-double-fire control-tested.

## Residuals
No live flag-ON e2e against a real WC-parked tree (strongest pre-flip proof still pending); `to_thread` cancellation under wedged DB untested (fail-OPEN absorbs).
