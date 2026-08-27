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

## 2026-08-25 — pause/resume/terminate tree-fix integrated pass (feature/pause-resume-terminate-tree-fix, 03df9108..5d8566db), Iteration 001/003
- Dispatch: 3 workers SEQUENTIAL CHAIN (not parallel) — readable-code @3116f94b → static-hygiene @ba0d8ce1 → robustness @2c32d5f5. Chain chosen over parallelism deliberately: cosmetic-application pass + repo's documented silent edit_file corruption history ⇒ concurrent multi-writer editing of shared large files (instance_lifecycle.py et al.) unacceptable. Worked clean; reuse this shape for apply-passes.
- Mandated fold F-1 (E4) done: phase3-errata.md 38→41 across ALL 7 references (title/intro/table/total/sum/2 narrative + summary cell), not just the 3 named lines — cross-consistency sweep caught 4 extra sites a literal-lines-only edit would have missed. Ground-truth grep = 13 tests. Lesson: count folds need a whole-file consistency grep for the old number, never just the cited lines.
- Verdict: Pass. 0 High. Applied (5 files, commit d8389477, on 5d8566db): E4 fold; dependency_bus.py:1157 fire_for_terminated_target docstring ("logged only" → 3 consumption sites); report_injection/repository.py:141-146 unused _FAILED_STATE removed (repo-wide grep, zero refs); child_reports.py:2987 stale line ref; job_recovery_service.py:1207-1210 tally comment (a/b/c/d/e → a/b/d/e, pattern c log-only). Scoped suites: compact 13P / child_outcome 5P / atomic-transition 10P (lives at tests/unit/repositories/, not tests/job_queue/).
- LEDGER (structural, semantics-frozen so deferred): db_dead_parent predicate duplicated 4 sites (child_reports.py:2780, manager.py:6840, 7297, 7420) — extract _is_dead_parent(row); _cancel_bus_watchers_for ~190 lines/4 nesting levels (instance_lifecycle.py:50-241); dead-letter stamp pattern split literal vs ReportInjectionState.FAILED.value across manager.py:6988/7294/7565; 5 fail-open except blocks swallow tracebacks (no exc_info) — dependency_bus.py:1192,1279 + instance_lifecycle.py:170,204,230; TERMINAL_STATUSES try/except rebind fallback (instance_lifecycle.py:127-138); _reconcile(connection=) shadows Connection class; >3000 files (manager 9605, instance_lifecycle 4626, child_reports 3732) diff-amplified pre-existing, not blockers.
- Deferred to Reviewer: (1) range-introduced red test — models.py:113 FAILED="failed" (lowercase, partial-index predicate intent) violates pre-existing test_state_values_have_no_lowercase_aliases (stash-verified pre/post edit, not tidier-caused); both fixes semantic. (2) Outcome dataclass passed to fire_for_terminated_target but only .status consumed — API-shape tightening. (3) pre-existing stale line refs out-of-diff: child_reports.py:2908, 2951, 3067, 3244 — next sweep.
- Exclusions honored: ticketed backlog untouched (FIRED-scan caching, stale-marker revive, P3-D2 deleted_at, reserved COALESCE, property-invariant owner, council infra); .agents/approver/* dirt never staged; no `git add -A`; no semantic changes.
- Positive anchors: py3.13 CancelledError contract documented end-to-end (instance_lifecycle.py:4033↔4351); all new-in-range functions fully return-typed; all new imports verified used; frozen-dataclass dataclass_replace discipline (dependency_bus.py:1261); test files carry plan-referencing top docstrings.


## 2026-08-26 — agent-instance-tools Phase 1 (feature/agent-instance-tools, 6ca9541c..ec8116ff), Iteration 001/003
- Dispatch: 2 parallel workers (readable-code @e525d358, static-hygiene @7156f563). Both reported fully; zero finding overlap.
- Verdict: Needs Work (minor) — 0 High / 4 Medium / 11 Low. No merge-blockers.
- Key findings: M fixture triplication (~240 lines: _patch_heavy_helpers/_make_manager/_get_send_message_tool ×3 test files → conftest); M stale "Phase 2 / Task 3" label contradicting new Phase-1 comment (routers/messages.py:38); M instance.py 2719 lines no top-level size comment (+560 this diff); M NEW test file 1505 lines without size note (adjudicated Low→Medium: threshold rule is categorical for new files; worker report was internally inconsistent table-vs-summary).
- Caller questions answered: CR-2 region (~55 lines @ instance.py:1935-1990) FINE AS-IS (mostly WHY-comments, ~6 lines code); D14 PASS for new code (grandfathered job_inject reach-in at job_queue.py:1784 → project-wide follow-up); JAFP PASS; _full_doc_ parity PASS (TestDocstringParity locks verbatim parity); constants leaf VERIFIED (zero imports); import hygiene PASS.
- Pattern: phase-label comment collisions — when a later phase hoists work an earlier phase's comment claimed, the old phase tag survives as a contradictory header (messages.py:38). Sweep for stale phase tags on any cross-phase touch.
- Pattern: guard-test fixture triplication — new test files adjacent to an existing pair copy the manager-patch scaffold verbatim; check sibling test dirs before scaffolding.
- Brittle-test pair: inspect.getsource literal-string assertion (test_instance_tools.py:1018-1043) and grep -c "JobItem" == 3 (1168-1181) both assert on source TEXT not behavior — breaks on harmless reformat. Prefer behavioral/AST anchors.
- Positive anchors: TestAuditBaseline pattern (test-layer re-verification of single-FIFO-writer invariant, test_instance_tools.py:223-277); constants.py fork-history docstring (175-237); helper-purity preserved by keeping load_skill/context override OUT of _route_send_message.
- LEDGER (structural, deferred): send_message ~450-line soft split (instance.py:1749-2200) into send_message_routing.py; route-vocabulary Literal/Enum hardening + 3 `routed_via == "injection"` sites; InstanceManager TYPE_CHECKING lift ×5 sites; job_queue.py:1564/1721 parallel terminal tuples → TERMINAL_INSTANCE_STATUSES; report_injection/repository.py:150-153 cross-layer set.
- Exclusions honored: entry var, hardcoded repo paths, W4 tautology (concurrent), S-3 ticket, test_job_queue_tools rot, plan-deferred items — 0 findings on all.
