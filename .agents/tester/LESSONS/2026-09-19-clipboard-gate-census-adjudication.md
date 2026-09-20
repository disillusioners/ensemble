# LESSON: clipboard-gate census adjudication — kwarg-pin rot, partition-context flakes, saturation quarantine (2026-09-19)

Final merge gate for `feature/clipboard-image-chat` ran a 14-partition backend census
(~21.2k executed). Three adjudication patterns worth reusing:

## 1. Branch-caused kwarg-pin rot (facade-forwarding class) — 1 node, FIXED

`tests/services/test_instance_messaging_queue_routing.py::…::test_manager_wrapper_forwards_queue_id`
failed because production `InstanceManager.enqueue_message_job` grew keyword-only
`image_refs: list[str] | None = None` (Phase 2) and the test's `assert_awaited_once_with` pins a
strict kwarg set. Exactly the documented bug class ("InstanceManager = manual-forwarding facade —
new kwargs need facade-forwarding check"). Deceptive error: pytest-mock reports
"expected await not found" for a KWARG mismatch on an AsyncMock — do not chase mock-asyncness
when the mock is already AsyncMock; diff the kwarg sets.
Fix: +6 lines adding `image_refs=None` to the expectation (commit 792d9826, path-scoped, test-only).
Grep-sweep rule: after any facade kwarg addition, `grep -rn "enqueue_message_job" tests/` and
spot-run every strict `assert_*_with` site whose call path propagates the new kwarg.

## 2. "Branch-caused" verdicts need SAME-CONTEXT base legs

Base A/B initially tagged `tests/integration/test_skill_cross_phase_flow_b.py` ×2 as
branch-caused (base-solo PASS vs census-in-pack FAIL). Quick-fix reproduction showed the file
13/13 GREEN solo at HEAD — the census failures were **partition-context flakes** (962-test xdist
run with a 26-error SSL-class pollution in the same process). Lesson: an A/B verdict comparing
base-solo vs branch-IN-PACK is unsound; context (solo vs partition, xdist, pollution) must match
or be explicitly adjudicated. Both nodes quarantined as partition-context (row 2026-09-19,
consolidated brace-idiom), monitored with escalation trigger "solo failure ⇒ regression".
Also: perf-matrix ×5 census reds collapse to 0 serially — xdist-flake, not regression.

## 3. Monitoring conditions fire — quarantine immediately when pre-registered

The saturation A2.2 node (chat-worker claim under saturation) had a pre-registered LESSONS
trigger: "quarantine the moment it fails INSIDE the registered pack". It fired during the census
(heavy 14-pack parallel load). Retry budget 3× solo under low load: 1F/2P → flake confirmed →
QUARANTINE row + pack `--deselect` + pack re-run green (58P/0F/1 deselected). Pre-registered
monitoring conditions are cheap insurance — honor them mechanically, they need no re-litigation.

## Misc operational notes

- `regression_integration_test.sh` now breaches a 300s wrapper (1019 collected, 314s actual;
  script timers 350/360) — maintenance split due (registered in gate report).
- Disposable-env boots MUST explicitly override every `POSTGRES_*` per-field var — inherited
  prod shell env can split-brain a disposable boot (caught in the failfast worker's shell).
- FE `ng serve` on macOS defaults to `[::1]` bind — pass `--host 127.0.0.1` for IPv4 curl/proxy
  assertions.
- Fresh-SQLite trap again bit 19+ census nodes in-partition (documented elsewhere); disposable
  PG14 remains the only clean boot path.
