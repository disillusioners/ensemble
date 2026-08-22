# Tidier Review Notes — agents-ensemble

## 2026-08-20 — pause-report-recovery (6bb99d5f..HEAD), Iteration 001
- Verdict: Needs Work — 1 High (vacuous `assert True` test, test_explicit_handle_resume_report_guard.py:483), 15 Medium, 7 Low. 0 findings on the 8 adjudicated do-not-flag items.
- Pattern: review-fix rounds leave `assert True` placeholders even when devs report band-aids removed — always grep `assert True` on branches with 3+ fix rounds.
- Positive anchors: case-lockstep contract docs (models.py:28-67), C4 grep-audit docstring, per-lane numbered step comments, never-raises discipline in recovery service — cite these as house style for future sweeps.
- Deferred-to-Reviewer items logged in final report (DEFERRED-row state after no-row reconcile; count_pending_for_parent semantics; instances JOIN index; SQLite discriminator robustness; FM-1 safe-default flip = behavior change).

## 2026-08-15 — LLM provider HA auto-fallback (feature/llm-provider-fallback, de3d3582..ef35ff4a)

- Iteration: 001/003. Dispatch: 2 parallel workers (tidier-readable-code @6e01c09e, tidier-static-hygiene @2c1656e4). Both reported fully.
- Verdict: Needs Work — 3 High, 9 Medium, 12 Low, 10 nits. No merge-blocker.
- Key findings:
  - H: `build_instance_llms` grew to 198 lines / 4 responsibilities (graph.py:3076-3273) — extract `_wire_retry_and_failover`.
  - H (bumped from worker Medium): sticky-on-success docs contradict adjudicated W1 semantic at 5 sites (config.py:91,100; config.yaml:14; .env.example:27; llm_error_classifier.py:328). Canonical wording: llm_error_classifier.py:156-159.
  - H: graph.py (5313) / manager.py (7388) >3000 refactor flags — pre-existing, diff-amplified, not branch blockers.
  - M: 6-line "NOT consumed here" comment block copy-pasted 7-8× across secondary sites — single-source it.
  - M: 5× ~40-line wiring-test scaffold (test_llm_failover.py:718-966) — factor into fixture.
- Positive: all "NOT consumed here" markers verified truthful; `[LLM-HA]` prefix + field placement consistent across config surfaces; test file organization strong (finding-named classes).
- Deferred to Reviewer: W5 dead-swap `_on_backup=True` while URL unchanged; `base_url_backup` key flows into SkillEmbeddingService config dicts (manager.py:841,1008).
- Exclusions honored: F811 warnings (13, accepted r1), primary-only v1 scope, 2 doc nits, pytest-timeout dep.
