# Midflight-QA-Channel Acceptance Gate — FINAL REPORT

**Date:** 2026-09-21 · **Branch:** `feature/midflight-qa-channel` @ `1222d0d7c54434382718b8ee8533fa2119f7b678` · **Base:** `246b7325` (v0.13.9, direct ancestor, linear) · **Worktree:** `/home/nea/ensemble-worktrees/qa-channel` (editable-install verified CLEAN, py 3.13.15)
**Verdict: ✅ PASS_WITH_EXCEPTIONS** — all 8 acceptance criteria (a–h) PASS; every exception is pre-existing with A/B or artifact proof; **zero new failures vs base**.

## Per-criterion verdicts

### (a) Question surfaces as event to watcher — **PASS**
- Dynamic: `TestQuestionSurfacesToWatcher::test_question_surfaces_to_watcher` + `test_fan_out_reaches_every_live_work_id` green (42/42 suite, 6.21s). Asserts `[JOB_EVENT] Job … question requested ❓`, pack payload in body (`"Approach A or B?"`), `source=internal_agent:job_event:{work_id}:question_requested`, durable event row, watch-row survives non-terminal.
- Static (PROVEN): `question_tools.py:388-468` → `emit_question_requested` (`midflight_qa.py:302-363`) → `EventBus.create_event(kind=QUESTION_REQUESTED, data=payload)` + per-work_id `notify_work_watchers(status="question_requested", result_summary=pack_to_dict(pack))` → watcher enqueue via `work_notifier.py:461-494` (non-terminal branch `:444-452` never claims/deletes the watch row). Payload (`midflight_qa.py:173-230`) carries BOTH the question payload (`questions[]` full text/options) AND work_id correlation (`job_id` array = MAJOR-1 fan-out over `enumerate_live_work_ids`, `:81-155`). SSE: `GET /api/messages/{id}/events` (`routers/messages.py:907`) via `LiveEventHub.stream_question_pack` (`live_event_hub.py:384-426`), envelope `{instance_id, event_type:"question_pack", message:pack}`.

### (b) Answer resumes the asker with the answer in-context — **PASS** (gap found → closed with new real-chain test)
- Existing unit pack `tests/unit/test_answer_gate_resume_chain.py`: **10/10 PASS** (1.59s).
- Adjudication (design §9.1(b) delegation): **Q1/Q2/Q3 all GAP** — every prior test mock-isolates the resume chain (`resume_processing_job`/`resume_instance_cascade` = AsyncMock; answer-content assertions were mock-kwarg captures; `/api/jobs/{work_id}/answer` end-to-end never driven).
- **NEW test authored (per commission): `tests/job_queue/test_answer_resume_real_chain.py`** — LEFT UNCOMMITTED as working-tree addition (commission default; state: reported, not committed). Real machinery: real `find_suspended_turn_for_answer`, real CAS `set_answers`, real EventBus row, real `_resume_cascade_db_sync` (instance PAUSED→RUNNING + `ResumeTurn` PAUSED→PENDING + handle cleared), real `resume_processing_job` entry. Only the outermost graph/LLM boundary is mocked — its captured `message` arg proves answer content reaches the resumed turn (F7 echo header + `"Approach A"` answer + question echo + 2nd Q/A pair + handle work_id + `silent=False`).
- Result: **2/2 PASS** (1.25s); combined with existing suite **12/12**. Exactly-once re-proven (2nd answer → `already_delivered`, zero 2nd graph call). Bonus: T3 terminal → HTTP 410 `ANSWER_TARGET_TERMINAL` (leader decision 1). **No product defects exposed.**

### (c) Report event non-blocking — **PASS**
- Dynamic: `TestReportNonBlocking::test_report_non_blocking` green — `set_question_pause_requested` mock canary asserts `flag_state == {}`; watcher gets `mid-flight report ⟳` + summary; event row persisted; watch row survives.
- Static (PROVEN): call site is the agent-direct tool `mid_flight_report` (`tools/midflight_report.py:62-120`; NOT HeartbeatEmitStuckProcessor — that owns the separate STUCK wedge emission). Zero pause-mechanism interaction: no `set_question_pause_requested`/`pause_instance_cascade`/`suspension_reason` write anywhere in the report path; sole "pause" token is the informational payload field `"paused": False` (`midflight_qa.py:391`). All three lanes (EventBus/SSE/work_notifier) individually fail-open (`except Exception` §8.6); bounded in-memory push writes only.

### (d) No polling introduced — **PASS**
- Dynamic: `TestNoPollingIntroduced` **6/6** green (incl. `inspect.getsource` on `HeartbeatEmitStuckProcessor` class body; `EVENT_STREAM_POLL_INTERVAL` consumer pin; claim-SQL no-loop pin).
- Static (independent audit): **ZERO_POLLING_INTRODUCED: YES** — 3,029 added daemon lines swept (13-pattern base + case-insensitive + blocking-wait primitives + broadened-loop passes); 40 raw hits all adjudicated (15 `#`-comments, 25 docstring/SQL-comments, 1 BENIGN bounded ancestor walk `hops < 32`, no sleep/wait); final-state crosscheck: 6 residual hits all blame-proven pre-existing (merge-base-ancestor commits, no hunk overlap); `EVENT_STREAM_POLL_INTERVAL` untouched. Wedge chain = future-dated one-shot Task rows through the existing atomic claim — not a timer/loop.

### (e) Completed-event Result bodies still work — **PASS**
- `job_completion_acceptance_test` pack: **30/30 PASS** (4.26s; dual-layer timeout confirmed). Count reconciled: 29 (v0.13.9 gate) + 1 (`TestRootCompletionGateBusPendingHold`, added `e3d60849`) = 30 on this lineage. All 4 intent points green (non-empty Result body; result_summary at completion; root+cascade/dead-letter/wedge gating; failed-terminal Error body via real publisher).
- Note: pack script header still documents the `child_reports.py:2007` blocker as OPEN — stale doc drift; `e3d60849` fixed it (test green).

### (f) No new failures vs base — **PASS**
- **job_queue full A/B** (byte-identical invocations, scrubbed env, `timeout 300`): base `246b7325` = 11F/1776P/38S/3DS (246.5s, temp worktree `/tmp/mfq-gate/wt-base-246b7325`, import-verified in-worktree); feature `1222d0d7` = 11F/1820P/38S/3DS (243.2s). **Failure sets node-for-node IDENTICAL (11/11).** Delta +44P = 42 midflight + 2 real-chain tests, all green. 8 of 11 are QUARANTINED-FAMILY; 3 not-listed (terminal_write_census ×2, wedge_resolver event-loop RuntimeError, dev_sh hardcoded-path env-defect — all base-failing).
- **Adjacent units** (51 files / 1061 tests, 44.1s): **1027P / 34F — all QUARANTINED-FAMILY** (injection_api MagicMock-await ×25 — `daemon/routers/messages.py` empty branch diff; devops meta-drift ×3 + coder_agent ×1 + coder_migration ×5 — meta.json diffs are `tools.allow`-only). Zero non-quarantined failures → base leg skipped per contract. All 328 watchover tests GREEN (47-node watchover quarantine family appears healed — re-adjudication candidate).
- **e2e adjudication:** `test_answer_dismiss_flow.py::test_full_answer_lifecycle_pause_answer_resume_complete` FAILS at feature with signature **byte-identical to base** (line 599, `job_item_admission == 'done'` got `'active'`, AssertionError; test file byte-identical across legs; both ~1–2s; in-memory SQLite, no live daemon) → **PRE-EXISTING-CONFIRMED**; developer attribution corroborated. Consistent with known Task↔JobItem mirror debt.
- Caveat (not a new failure): `test_terminal_write_census` red at BOTH revisions; feature-leg signature additionally names `manager.py:5047 _on_stale_task_permanent_failure` as unlisted — possible branch-extended drift inside an already-red census; census refresh = test-debt follow-up.

### (g) Boot gate — **PASS (adjudicated)**
- **ensure.md citation-verified** (53-line pack-mapped format; the "4-line boot probe" memory is STALE): Core #1 scoped packs (green above); **#2/#3 concurrency**: `concurrency_atomic_unit_test` **98P/74S/0F** (63.5s, canonical-exact); **#4 static**: `dev.sh:102` `--timeout-graceful-shutdown 10` PRESENT.
- Scrubbed SQLite boot: port 8079 verified free pre/post; app startup failed at **migration phase only** — `MigrationError 20260714_000001: (sqlite3.OperationalError) near "EXISTS"` on `ALTER TABLE job_queues DROP CONSTRAINT IF EXISTS …` (PG-only DDL). Pre-existing proof: migration file blob-identical at base, `git diff … daemon/migrations/` EMPTY, **no diff-touched module in the traceback** (manager.py diff hunks at +2485/+3385 only), prior v0.13.9 Phase-D gate same adjudication. No kills needed; no stray process.
- `boot_probes_unit_test`: **75/75 PASS** (10.0s). Import sweep: **22/22** diff-touched daemon modules import clean.

### (h) Wedge guard — **PASS** (static + dynamic)
- Dynamic: `TestWedgeGuardOneShot` **5/5** (claimable-while-paused; ordinary-task-blocked; rearm+escalate with exactly-one PENDING successor then zero after escalation; no-op on consumed handle; drift-reconciler excludes heartbeat rows) + `test_mint_cap_skips_when_pending_row_exists` (cap pins) + carve-out shape pin.
- Static (PROVEN): one-shot mint = single future-dated `Task` insert (`create_one_shot_heartbeat`); atomic claim = scalar-subquery + outer-UPDATE row-lock with type-scoped disjunct wrapping the pause gate; exactly-one successor via durable no-op predicate (`find_suspended_turn_for_answer → None` after `ResumeTurn` handle-clear, or terminal status → self-cancel, no mint) + re-arm gated below escalation index. Escalation at `STUCK_HEARTBEAT_ESCALATION_INDEX = 3`, intervals `1800s` → total window **3600s = 60 min** (t=0/1800/3600; escalation fires AT #3, no successor). PENDING cap = 1 per asker (`has_pending_of_type_for_instance`, pending-status-scoped so re-arm never self-blocks); breach → skip-mint (return None, INFO log). Fail-open cap-check noted as deliberate (narrow residual risk).

## Exceptions (all pre-existing, with proof)
1. **11 job_queue failures** — node-for-node base-identical (detached-worktree A/B @246b7325, byte-identical invocation). 8 QUARANTINED-FAMILY + 3 not-listed pre-existing (census drift ×2, event-loop RuntimeError ×1, env-defect hardcoded path ×1 → counted within the 11).
2. **e2e `test_full_answer_lifecycle_pause_answer_resume_complete`** — PRE-EXISTING-CONFIRMED (byte-identical signature at base; file byte-identical).
3. **SQLite boot blocker `20260714_000001`** — pre-existing (base-verbatim migration, empty migrations diff, prior-gate corroboration); boot-from-fresh-SQLite impossible repo-wide until dual-driver rewrite (separate commission).
4. **34 adjacent failures** — all QUARANTINED-FAMILY (quarantine-aware exemption; empty branch diff on every failing file).
5. **ensure.md Release-Gate E2E items** (live-daemon LLM tests) — not runnable: boot prerequisite blocked by exception 3; out of this commission's scoped gates (a–h).

## Safety record
Zero live-system contact: ports 9797/7979/8388/54329 untouched; `ensemble_prod` ambient POSTGRES_* (present in worker shells — hazard re-confirmed) scrubbed on every invocation via the 7-var `env -u` set; only scrubbed SQLite boots in worktree; no kills; `/home/nea/ensemble-src` untouched.

## Artifacts & state
- **New test (UNCOMMITTED, per commission default):** `tests/job_queue/test_answer_resume_real_chain.py` (+ registered as ad-hoc pack `answer_resume_real_chain_unit_test` in PACKS.md).
- Temp A/B worktrees left in place (reusable): `/tmp/mfq-gate/wt-base-246b7325`, `/tmp/mfq-gate/wt-base-e2e-246b7325`; logs under `/tmp/mfq-gate/`, `/tmp/feature_leg_jq.out`.
- Feature worktree HEAD unchanged `1222d0d7`; git status delta = the new untracked test only (+ pre-existing scratch).

## Workers (11 dispatches, 0 re-dispatches needed)
recon-mfq-01 · pack-midflight-01 · trace-mfq-01 · greppoll-mfq-01 · pack-answer-gate-01 · pack-acceptance-01 · pack-concurrency-01 · pack-jqbase-01 · pack-jqfeat-01 · pack-boot-01 · pack-adjacent-01 · pack-e2eadj-01 (12 named; 11 work nodes).

## Follow-ups (non-gating)
- Census refresh (`test_terminal_write_census` red since base; possible new site `manager.py:5047`).
- Watchover 47-node quarantine family looks healed → 3× clean re-run candidate for un-quarantine.
- messages.py await-mock family anchor drifted `:258→:325` — quarantine rows should match by signature.
- Acceptance-pack header doc drift (stale blocker note).
- e2e admission-mirror `'active'≠'done'` — known Task↔JobItem reconciliation debt (separate commission).
