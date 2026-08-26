# PR3 — C1 Read Flip: get_instance_messages aget-only (External Gate)

Date: 2026-08-26 · Branch: feature/langgraph-checkpoint-perf · Range: c42a8bf5..80c84219
Mode: 🔴 Deep-Review (council, 2 councilors: agentic + coding, skill=code-review)
Verdict: **APPROVED** — 0 🔴 / 1 🟡 / 5 🟢. Internal PASS 10/10 corroborated; council confirmed 8/8.

## What landed (reference for future reviews)
- THE FLIP (dbfbf812): get_instance_messages reads ONLY state["channel_values"]["messages"] via aget
  (persistence.py:340-342); serialization loop iterates in channel order (:429-446). message_metadata consumed
  exactly twice — msg_timestamps build (:410-412) + per-message .get(msg_id) for created_at ONLY (:437).
  NO sort anywhere; identity fields never touched by metadata. Over-record tolerance holds: ghosts test-pinned
  (test_over_record_rows_never_join seeds ghost rows, asserts absent, len==4).
- alist is DEAD on the live path: repo-wide only migrations/checkpoint_migrator.py:36/:211 (offline, by design).
  Runtime proof = armed/poisoned assert_not_called mocks across 3 suites. daemon/persistence.py:34 log_saver_op
  orphaned (dead export, harmless).
- Degradation contract: repo/to_thread exception → except Exception → logger.warning → {} → state.ts fallback;
  response shape unchanged. Flip adds exactly ONE new except (Exception, not BaseException, not pass) —
  CancelledError propagates. Bug-class clean.
- Fixture freeze: 6 variants; JSON touched only by pre-flip 5d928d51 (git-proven); loud-regeneration fence
  (observed_alist_count >= 1 on-disk) makes post-flip regeneration fail loudly.
- PR2 wiring gap CLOSED: dropping slot kwargs from EITHER build_instance_graph site (instance_lifecycle.py
  ~:1323/:3309) now fails test_spawn_and_restore_paths_both_wired — kwarg-presence per call site.
- Empty-path: state None / empty channel → early return [] with alist_count=0; synthetic insert(0),
  synthetic-system-{iid}, 11-key set; disable_auto_load_tracking untouched; only new read-path DB touch is a
  pure SELECT (get_for_thread, repository.py:117-137) — no writes on poll.

## Findings that matter (follow-ups)
1. 🟡 persistence.py:380-397 getattr-guard short-circuit is SILENT when message_metadata_repo is None — no
   log/metric; all created_at degrade to state.ts. Unreachable in today's prod (repo built unconditionally,
   manager.py:578-581) but masks misconfiguration if that ever changes. Fix = one logger.warning + caplog assert.
2. 🟢 commit-message arithmetic: "+42 new tests" is actually +22 new (42 = 22 new + 20 touched). Manifest 344→366 correct.
3. 🟢 message_tap.py:87 residual stale phrase "joins message_metadata to the checkpoint walk" — no walk anymore.

## Patterns learned
- assert-not-called with ARMED (would-succeed-if-invoked) mocks is a genuine absence proof for dead-path claims —
  prefer over grep-only when reviewing deletions of shared helpers.
- Pre-flip machine-captured fixture extension + on-disk loud-regeneration fence = strong schema-freeze pattern;
  the fence converts silent fixture rot into test failure.
- Commit-message test-count phrasing ("N new") vs manifest delta divergence is a recurring claim-accuracy trap —
  cross-check --collect-only deltas, not prose.
