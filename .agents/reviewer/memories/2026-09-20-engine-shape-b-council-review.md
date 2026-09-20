# 2026-09-20 — engine shape-(b) council review (b0a4f21d..9766563a, feature/watch-notify-engine)

Deep-Review council (2 models, `code-review`, Round 0 + one targeted re-query). **VERDICT: REJECT — 1 critical (merge-blocking), 4 medium, 4 minor.** Site-4 PASS; site-7 exemption SOUND (census record unpinned).

## The critical (fix = one line + one regression test)
- **Site-1 `UnboundLocalError` on the race-lost path** — `daemon/services/task_processor.py:1079` reads `finalized_mirror` at outer indent, but the variable binds ONLY inside the if-block opened at `:1046` (`= None` at `:1051`). Trigger: Task already terminal when `on_success` runs → `complete_task` returns None → block skipped → NameError. Designed-for race class (concurrent finalizers). Consequence: pipeline swallows at `message_processing_pipeline.py:618-626` as "non-fatal" but aborts the rest of `on_success` — including the W6 usage-limit anchor clear at `:1099-1101` whose contract says "clearing must never break a finalize". Introduced by f03f66c4 (pre-image has no such variable). Test gap: both site-1 tests seed RUNNING tasks (`test_event_driven_completion.py:225,:270`) so the None path is uncovered. Fix: hoist `finalized_mirror = None` to outer scope (~:1040) + pre-terminal-Task regression test asserting W6 clear still runs.

## Site-7 exemption — verified SOUND (leader ratification holds)
(a) repo purity: no JobQueueService reach in task/repository.py (imports :3-40; ctor :173 takes engine + on_pending_task only; sole mention a comment :2842; no seam). (b) event-time callers `task_processor.py:990`/`:1081` cover the same work_id — fires `notify_work_watchers` directly, CAS-equivalent via facade delegation `job_queue_service.py:377`; boundaries census-hooked (`job_queue_service.py:3967`, `job_retry_engine.py:467,:1175`) → a site-7 hook is a guaranteed CAS-noop. (c) CASE write gated on terminal snapshot (`task/repository.py:1229-1231`) following already-terminal handling → cannot orphan. Flip cost = pure cost, no coverage gain. Caveat: exemption rests on the argument, not a pin (see M1) + contract wording at architecture-recommendation.md:259 should say "CAS-equivalent direct helper" not "canonical notify_watchers".

## Census walker blind spots (engine-hardening backlog)
- Walker discovers 24 sites = 17 hooked + 7 exempt; fixture holds 26 entries — 2 VACUOUS: the `reap_legacy_mirror_zombies` def-shape entry (M1 fires on ast.Call only, def sites invisible) and the site-7 `bulk_sql:completed` entry (S3 requires `terminal_reason='<literal>'` regex; the CASE binds `:terminal_reason` as param at `task/repository.py:1305-1306`).
- S3 walker blind to bind-param terminal writers generally — future writers of that shape escape the census.
- Fixed 6-name census method surface (`test_terminal_write_census.py:32-39`) — novel method names outside finalize-family/bulk-SQL shapes escape.
- Anti-rot ±5-line check is substring-based (`:171-178` — `"notify_watchers" in line`), satisfiable by a comment; discovery is genuine AST.

## Other notables
- Site-4 PASS element-for-element: pre-SELECT `repository.py:3857-3883` (WHERE byte-identical to UPDATE clause `:3935-3938`), UPDATE untouched, re-SELECT filters `admission_state='done'` (`:3886-3900`), notify loops over `fired_queued_ids` only (`job_queue_service.py:1291-1305`), no RETURNING; race test asserts stale id NOT notified AND watcher row survives.
- Dead-letter gate `work_notifier.py:369` `not _is_terminal(mission_live)` — strict superset (also unblocks `settled`); hold preservation intact.
- 4 adjacent-suite failures differential-proven PRE-EXISTING at b0a4f21d (temp worktree): 3× watch_events JSON-list equality + 1× observer mock (`test_job_feedback_observer.py:327`).
- Site-5 token hardcoded `'cancelled'` (`job_queue_service.py:1362`) — currently equivalent, drifts silently.
- Site-4 natural-completion mis-token (queued→done between UPDATE and re-SELECT fires 'cancelled') — exactly-once preserved via CAS; inherent to mandated shape.


## Fix-round closure (2026-09-20, b77e52a1 on intact 9766563a)
Delta re-check — **APPROVE-DELTA, all 7 items PASS** (single code-review worker; both verification modes on the critical):
- **Critical RESOLVED, revert-proven**: `finalized_mirror = None` hoisted to `task_processor.py:1047` (precedes the `:1053` guard and `:1085` read on every path). Temp-worktree revert-proof: hoist reverted in /tmp worktree → regression test RED with the exact predicted `UnboundLocalError` at the read site → restored → GREEN. Main tree untouched. Regression test `test_pre_terminal_task_completes_on_success_fully` seeds a genuinely pre-COMPLETED Task (`status != 'running'` → `complete_task` returns None) and asserts real outcomes: no exception, W6 anchor cleared in DB, zero notify deliveries.
- Census now walker-symmetric: 24 fixture entries = 24 discovered (17 hooked / 7 exempt), zero vacuous; site-3/site-7 carried as walker-invisible NOTES, site-7's note carries the argument+CAS-equivalence-not-a-pin caveat and a do-not-loosen-S3 warning.
- M3: `orphan_terminal_reason` local at `job_queue_service.py:1343` feeds both repo arg (`:1347`) and notify token (`:1364-1366`); no hardcoded `'cancelled'` in any notify call (AST-scanned); sole caller confirmed.
- M2 wording verified in untracked working-tree doc (`:259` — "notify_work_watchers DIRECTLY (CAS-equivalent via the facade delegation…)").
- Suites: new 23 green (12+3+8; n1_pin grew 7→8) in 1.91s; full tests/job_queue = 1741 passed / 7 failed / 38 skipped (54.85s); all 7 failures differential-proven pre-existing (incl. ancestor 94fc6da1 — even stronger than branch-base proof): 2 watcher-repo concurrent + 1 observer mock + 2 in_progress_guard + 1 jober_watch + 1 phase2_feedback_verify. The leader's "2 surviving" framing was conservative; the load-bearing property (zero NEW failures) holds.
- **Bonus**: 3 `test_f1_killswitch_tz_matrix.py` parity tests (TestRecoveryServiceBothStateParity) flipped FAIL→PASS at HEAD — side-effect of the notify hooks; recommend a one-line PR note so it isn't a surprise.
- **Branch `feature/watch-notify-engine` @ b77e52a1 is CLEAR for the remaining pipeline (tidier → tester).**
