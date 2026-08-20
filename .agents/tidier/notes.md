# Tidier Review Notes — agents-ensemble

## 2026-08-20 — pause-report-recovery (6bb99d5f..HEAD), Iteration 001
- Verdict: Needs Work — 1 High (vacuous `assert True` test, test_explicit_handle_resume_report_guard.py:483), 15 Medium, 7 Low. 0 findings on the 8 adjudicated do-not-flag items.
- Pattern: review-fix rounds leave `assert True` placeholders even when devs report band-aids removed — always grep `assert True` on branches with 3+ fix rounds.
- Positive anchors: case-lockstep contract docs (models.py:28-67), C4 grep-audit docstring, per-lane numbered step comments, never-raises discipline in recovery service — cite these as house style for future sweeps.
- Deferred-to-Reviewer items logged in final report (DEFERRED-row state after no-row reconcile; count_pending_for_parent semantics; instances JOIN index; SQLite discriminator robustness; FM-1 safe-default flip = behavior change).
