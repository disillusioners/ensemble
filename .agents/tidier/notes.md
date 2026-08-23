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

## 2026-08-23 — P2.2 self-restart/upgrade tools (feature/self-restart-p2p2-ari-tools, d4c41d68..0949dd51), Iteration 001
- Dispatch: 3 parallel workers (readable-code @85d54c44, static-hygiene @9eabd488, robustness @1568b35f). All reported fully; skill_feedback soft-failed on all 3 ("No usage record found" — load_skill-injected skills untracked).
- Verdict: FIX-NOW(list) — 0 High / 14 Medium / 22 Low after dedup. No merge-blocker; FIX-NOW set is entirely trivial (docstrings, dead code, twin-helper deletion, comment fixes, ~4-line except-path guards). LEDGER set: structural refactors of security-approved code (actor-tool dedup, 330-line system_upgrade split, module split, type hardening).
- Key merged findings: local twin helpers `_parse_iso_utc`/`_lock_run_id` duplicate imported journal originals (upgrade_tools.py:213/:454); stale "granted to no one" wiring comment at instance.py:2089-2095 (comment asserts false security property after ari allow landed); partial-arm in_flight wedge (upgrade_tools.py:1491-1527); false "Never raises" docstring on reconcile_pending_op (upgrade_journal.py:660).
- Exclusions honored: gate security/correctness not re-litigated (APPROVED-WITH-NITS); P2.3 ledger items MINOR-A/B, NITs C-G, F2, N1/N5/N6/OBS untouched. Frozen-binary drift contract re-verified green by hygiene worker.
- Positive anchors worth citing as house style: T2 registration checklist comment + greppable test (upgrade_tools.py:107-140); refusal token taxonomy + `_refusal_reason` regex test helper; torn-journal byte-preservation tests; `except Exception` discipline (CancelledError gotcha NOT reproduced).
