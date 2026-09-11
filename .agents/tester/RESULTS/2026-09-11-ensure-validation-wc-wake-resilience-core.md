# ensure.md Validation — wc-wake-resilience Core Gate (2026-09-11)

- **Worktree**: agents-ensemble-wt-wc-wake-resilience @ `a7d7b760` (branch `feature/fix-wc-wake-resilience`; descendant of `a880b033`; benign test-only drift from `55a76bb5` — expected per mission)
- **Scope**: mission-limited to the 4 Core-Critical requirements (R1–R4). Release Gate E2E items OUT OF SCOPE — contradiction notice below, NOT attempted.
- **Constraints honored**: no daemon/dev.sh boot; no processes killed; no merges/pushes; `.agents/tester/rules/ensure.md` untouched (read-only); tests only via pack script / `uv run python -m pytest`.

## Verdict summary

| Req | Requirement (Critical) | Verdict | Key evidence |
|-----|------------------------|---------|--------------|
| R1 | Deadlock / concurrency integrity — pack `concurrency_atomic_unit_test` PASS | ✅ PASS | `98 passed, 74 skipped, 44 warnings in 6.71s` / `RESULT: PASS` (= canonical baseline 98P/0F/74S) |
| R2 | `dev.sh` includes `--timeout-graceful-shutdown 10` | ✅ PASS | `dev.sh:102` verbatim match (static grep only) |
| R3 | No sync DB calls on event loop — thread-identity tests in pack | ✅ PASS | 12 thread-identity nodes in pack scope; `10 passed, 2 skipped` targeted verdict; 2 skips documented legacy-API skipifs w/ equivalent-coverage pointer |
| R4 | No regressions in changed packs | ⏸ NOT RUN (per mission) | Leader adjudicates from full-suite chunk data; zero runs executed for R4 |

---

## R1 — Deadlock / concurrency integrity (pack PASS) — ✅ PASS

Command: `timeout 300 bash test/packs/concurrency_atomic_unit_test.sh` (pack internal Layer-2 timeout 280s).

Raw final pytest line + verdict:

```
98 passed, 74 skipped, 44 warnings in 6.71s
RESULT: PASS
```

Pack exit 0; outer `timeout 300` exit 0. **Exact match to canonical baseline 98P/0F/74S.**

Scope integrity: pack header reported `Running 13 test file(s)` — all 13 candidate files exist and ran (no missing-file skips). Quarantine-awareness: none of the 13 pack files appear in `.agents/tester/QUARANTINE.md` (quarantine rows live in `tests/job_queue/`, `tests/unit/`, `tests/integration/` partitions); the 74 skips are in-file markers, including the 2 documented skips detailed under R3.

## R2 — dev.sh graceful-shutdown flag — ✅ PASS

Command: `grep -n "timeout-graceful-shutdown" dev.sh`. Raw output (verbatim):

```
99:# --timeout-graceful-shutdown 10 ensures uvicorn forces exit after 10s even
102:$PYTHON -m uvicorn daemon.api:app --host "$HOST" --port "$PORT" --reload --log-level "$LOG_LEVEL" --no-access-log --timeout-graceful-shutdown 10
```

Flag present at `dev.sh:102` (comment at `:99`). Static file check only — daemon NOT booted (mission constraint).

## R3 — No sync DB calls on the asyncio event loop — ✅ PASS

Adjudicated per mission method: R1 pack PASS + `--collect-only -k "thread"` probe of the pack scope, plus one targeted verdict run to positively confirm ran-vs-skipped.

Probe (`--collect-only -q -k "thread"` over the 13 pack files): **18 thread-named nodes collected (154 deselected)**. The 12 dedicated thread-identity nodes (`*_runs_off_loop_thread` / `*_via_to_thread` asyncio.to_thread pairs):

- `tests/test_deadlock_fix.py` — TestPrepareEnqueuedMessageOffloaded ×2, TestNotifyWatchersOffloaded ×2, TestFinalizeInstanceDbSyncOffloaded ×2, TestProcessChildCompletionDbSyncOffloaded ×2, TestSendErrorReportDbSyncOffloaded ×2 (covering prepare_enqueued_message, get_watchers/notify, finalize_instance_db_sync, process_child_completion db_sync, send_error_report db_sync)
- `tests/test_finalize_job_h15.py` — TestH15ThreadOffload ×2 (`test_finalize_job_db_sync_runs_off_loop_thread`, `test_finalize_job_db_sync_via_to_thread`)

Targeted verdict run: `uv run python -m pytest tests/test_deadlock_fix.py tests/test_finalize_job_h15.py -k "thread" -q --override-ini="addopts=" --tb=short -rs` →

```
10 passed, 2 skipped, 9 deselected in 0.43s
```

Skip reasons (`-rs`, verbatim) — both at `tests/test_finalize_job_h15.py:777` and `:811`:

```
SKIPPED [1] tests/test_finalize_job_h15.py:777: Legacy CorrelationManager API removed in D13 migration (89333a47). Tests drive JobFeedbackObserver.handle_correlation_complete which no longer exists. Equivalent post-migration coverage: tests/job_queue/test_job_feedback_observer.py.
SKIPPED [1] tests/test_finalize_job_h15.py:811: Legacy CorrelationManager API removed in D13 migration (89333a47). ...
```

Attribution: `89333a47` (2026-06-27, "refactor: D13 — eliminate MESSAGE JobItem creation") **is an ancestor of HEAD** — pre-existing, branch-caused = 0; baseline-consistent (98P/74S unchanged vs canonical). Both skips carry an explicit equivalent-coverage pointer (`tests/job_queue/test_job_feedback_observer.py`).

Verdict: PASS — thread-identity coverage is present and active (10/12 executed & green; 2 principled, documented, pre-existing skips with equivalent-coverage pointer).

## R4 — No regressions in changed packs — ⏸ NOT RUN (per mission instruction)

Zero pack/test invocations executed for R4. Leader adjudicates from the full-suite chunk data. Disclosure for full transparency: the only pytest invocations in this validation session were (a) the R1 pack, (b) the R3 collect-only probe, (c) two 12-node `-k thread` scoped verdict runs (0.43s each, pack-file subset) in service of R3 evidence. Nothing else ran.

---

## ensure.md Improvement Notices (contradictions)

⚠️ **Contradiction — Release Gate E2E vs mission no-boot constraint**

- **Requirement text**: ensure.md:37 "Daemon running: `./dev.sh` (health at `localhost:8079`)" + Critical release-gate items :46–53 (happy-path / pause-resume / terminate-revive / 3-level cascade E2E).
- **Rule contradicted**: mission safety constraint for this validation — "never boot the daemon/dev.sh". The Release Gate prerequisites structurally require a live daemon; the two cannot both hold.
- **How validated instead**: not validated — declared out of scope by the mission; NO E2E attempt was made. This notice records the gap so coverage is not silently assumed.
- **Suggested rewrite** (per ensure.md's own Release Gate preamble, :34: "prefer converting each to a mock-test pack (daemon mocked) so it runs under the 5-min cap without `./dev.sh`"): convert each of the 4 E2E items to daemon-mocked mock-test packs (mock InstanceManager/graph task + stub LLM), e.g. `test/packs/e2e_workflows_mock_test.sh` per scenario, Validation: `timeout 300 bash test/packs/<scenario>_mock_test.sh`; retain the live-daemon variants under a separate operator-run section outside the automated gate.
- **Secondary note**: the E2E validation lines (:47/:49/:51/:53) offer a raw `.venv/bin/pytest` fallback command — if used without the pack shell that conflicts with the file's own "run as packs" rule (:6); the mock-pack rewrite resolves this too.

ensure.md itself was NOT modified (user-owned).
