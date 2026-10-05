# Verification Gate — QUESTION-WATCH-FANOUT (HIGH-priority live-prod bug fix)

| Field | Value |
|---|---|
| Date | 2026-10-05 (17:4x–18:5x UTC) |
| Worktree | `/home/nea/ensemble-src-wt-question-watch-fanout` |
| Branch / HEAD | `feature/question-watch-fanout` @ `c7862368` (verified) |
| Delta | `52ab3b6e..c7862368` (10 commits, 11 files, +2064/−115) |
| Mode | READ-ONLY gate: zero repo code changes, no commits, no push, no deploy/promote, no prod(:9797)/demo(:7979)/self(:8088) contact |
| Workers | 18 (1 prep + 14 wave + 3 baseline-attribution legs) |
| Artifacts | `/tmp/qwf/` (suite_run, rdr_run, c2_run, wrarm_run, trwake_run, family_run, mpins_run, sweep_q1..q4, adj_run_1..3, baseline_run, baseline2_run, baseline3_run, concurrency_run, orig_scenario.log, COVERAGE_MAP.md, recon-2026-10-05.md) |

## VERDICT: 🟢 SHIP

Zero new-at-delta test regressions across ~4,050 test executions. All acceptance semantics of the fix are proven by real-side-effect tests plus a full-chain in-process harness run.

## Checklist results (commission format)

| # | Item | Result | Evidence |
|---|---|---|---|
| 1 | New regression suite `tests/job_queue/test_question_watch_fanout.py` full run | **PASS — 19/19, 4.47s** | Incident-geometry test real: `_seed_incident` builds R1-settled + mission_terminal-only row + R2-spontaneous-no-row → watcher receives `question requested ❓` w/ `[JOB_EVENT]` envelope naming R1 (test `test_question_on_wake_receipt_reaches_mission_watcher` :243–291). Non-claiming pinned twice (:286–291, :861–877). Dedupe (:302–330). Events-exemption via `["completed"]` transport-only row (:332–350). Reconcile-mint sub-shape b (:910–938). Step-4b escalation-after-DELETE: REAL side effects — actual `DELETE FROM task` on real engine + `task_repo.get_by_instance == []` + escalation envelope still delivered (:547–639), NOT vacuous AsyncMock (mock carries real DB side effect). 36be8aef guards inside suite: `TestMissionTerminalUntouched` ×3 (:830–901). |
| 2 | Scoped packs: rdr / c2 / watcher_rearm / terminal_report_wake | **PASS — 4/4 packs** | `report_delivery_recovery_regression` 44/44 (5.96s); `c2_question_deferred_pause` 55/55 (8s); `watcher_rearm_integration` 4/4 (10s); `terminal_report_wake_unit` 4/4 (2.36s). pg_smoke + prefix_worktree variants deliberately excluded (PG-creating / parent-discrimination harness, out of gate scope). |
| 3 | 36be8aef family green; mission_terminal untouched | **PASS** | `test_mission_terminal_commission_pins.py` 6/6 + `test_mission_live_guard.py` 24/24 = **30/30, 8.87s** (NOTE: these are standalone FILES, not part of mission_pins pack). `mission_pins_final_test.sh`: **exact parity 3F/34P, 11.13s** — same 3 names as documented baseline (n8 `test_mirror_work_id_renders_settled_via_primary_event_path`, `test_mission_terminal_fires_when_both_terminal`, `test_mission_terminal_watcher_held_when_mission_not_terminal`). |
| 4 | Broader regression sweep + baseline attribution | **PASS — 0 new-at-delta** | job_queue dir: 4 quarters, 2024 tests, 15F (q1 442P/3F/139.61s; q2 506P/3F/19S/42.41s; q3 453P/4F/53.99s; q4 570P/5F/19S/114.18s). Adjacent tests/unit+services (61 module-matched files): 1733 tests, 1691P/28F/14S/173.28s. **All 43 unique reds attributed: 40 machine-proven PRE-EXISTING at pristine 52ab3b6e (detached temp worktrees; leg A 11/11, leg B 1/1, leg C 28/28 with `comm -3` diff = 0 lines), 3 in the documented pre-existing set (2× in_progress_guard fake_sync + 1× n8, the latter also mpins-parity-proven).** Concurrency pack 99P/0F/74S (baseline 98P/0F/74S; +1 = coverage-direction, 74.28s). |
| 5 | Original-scenario closure (full chain once) | **PASS — 2/2, 2.7s** (in-process mock lane, justified) | Harness `/tmp/qwf/orig_scenario_test.py`: real repos on in-memory SQLite, real `ask_questions` tool body, real `notify_work_watchers` C1 hold, real `HeartbeatEmitStuckProcessor` ×2, real Step-4b DELETE; fake clock. Verbatim emissions delivered to mission watcher (all name R1, the mission_terminal-only row on the settled receipt): `question requested ❓`, `stuck awaiting answer ⏳` ×3 (0s/1800s/3600s), `question escalation ⚠`. Non-claiming verified across all 5 deliveries. Mock-only justification: commission prefers mock lane; live boot guarded out (no ports); repo's own regression idiom is real-repos-on-SQLite + facade mocks. |
| 6 | Cap sanity (newest-kept DESC + WARN lists dropped ids) | **PASS (with 🟢 note)** | `test_…multi_receipt_select_is_capped` + receipt-scan test: `ids[0] == newest_work_id` — REAL ordering assertion (:701–724). Cap-equality pin via runtime `inspect.signature` vs service-canonical constants (:758–785) — REAL. Cap-hit WARN: fires once w/ `total_rows=`/`limit=`/`dropped=` tokens (:787–826) — real observability assertions, but does NOT pin the concrete dropped watcher IDs inside the WARN body (🟢 tighten later). |
| 7 | No web-frontend scope | **CONFIRMED** | Delta is daemon-backend only (8 daemon modules + 1 new test file + design.md). No browser automation run; none needed. |

## ensure.md (Core, blast-radius scoped — execution-lane intersection: task_processor.py + task/repository.py in delta)

- **Critical #1** (no regressions in changed packs): **PASS** — all scoped packs green.
- **Critical #2/#3** (deadlock/concurrency; no sync DB on loop): **PASS** — `concurrency_atomic_unit_test` 99P/0F/74S.
- **Critical #4** (dev.sh `--timeout-graceful-shutdown 10`): **PASS** — grep-verified at dev.sh:142.
- Important (await-correctness informational grep): no suspicious un-awaited sites (6 hits, all docstrings/comments).
- Release Gate: **not triggered** — scoped bug-fix, not architecture/release (job_queue + module-matched adjacent sweep IS the blast radius; full non-integration suite not warranted).

## Baseline attribution detail (pristine 52ab3b6e, detached temp worktrees, all removed cleanly)

- **Leg A (11 job_queue reds)**: 11/11 PRE-EXISTING incl. both flagged "delta candidates" — `test_tool_registration` (24≠22 tools) and `test_add_watch_creates_record` / `test_watcher_repository_concurrent` ×2 (`settled` token drift) fail identically at base; `test_ensure_dev_sh_still_works` = hardcoded macOS path (env-specific, fails at base); TestSite1InlineMirrorFinalize ×3 (MagicMock-JSON, matches project-known RED-since-v0.13.10 family); TERMINAL_WRITE_CENSUS ×2; wedge_resolver event-loop RuntimeError.
- **Leg B**: `test_job_answer_tool::test_create_job_tools_returns_job_answer` (job_resume-vs-job_answer ordering) — PRE-EXISTING, identical at base.
- **Leg C (28 adjacent reds, 9 files)**: 28/28 PRE-EXISTING, machine-checked (`comm -3` on sorted failure-ID lists = 0 diff lines). Includes the adjacent worker's "candidate regression" `test_mission_terminal_watcher_held_when_mission_not_terminal` — pre-existing (also in documented mission_pins 3F set).

## Findings

- 🟢 F1: Cap-hit WARN test asserts tokens but not concrete dropped watcher IDs — tighten when convenient (suite :787–826).
- 🟠 F2: PACKS.md integrity drift (pre-gate): `report_delivery_recovery_regression` / `watcher_rearm_integration` / `terminal_report_wake` narrative-only registrations (no rows); count drift (rdr row says 27→runs 44 incl. stale header comment; c2 40→55; mission_pins 35→37); `report_delivery_recovery_...sh` mode 644 (run via `bash`). Doc-debt only — scripts exist and pass.
- 🟠 F3: Test-debt inventory for a future cleanup commission (all pre-existing, ~43 reds): Py3.14 fake_sync/mock-arity family; `settled`-token watch_events pins (3 tests); tool-surface pins (count 24≠22 + job_answer ordering); TestSite1InlineMirrorFinalize MagicMock-JSON ×3; TERMINAL_WRITE_CENSUS drift ×2; misc fixture drift (coder_migration ×5, status_guard ×4, process_message_metrics ×10, api_router ×2, phase4 ×1, f16 ×1, dev.sh macOS path ×1).
- 🟢 F4: e2e coverage-map caveat — the child-report mint *transaction* (child_reports.py, NOT touched by delta) is covered by shape-seeding + reconcile-mint test, not the full transaction; acceptable since delta semantics operate on receipts post-mint.
- 🟢 F5: Escalation emission shows `Agent: unknown` post-Step-4b (instance row gone) — documented degrade-and-still-deliver; delivery itself is the pinned contract.

## Coverage totals

≈4,050 test executions at delta (19 suite + 137 scoped packs + 37 mpins + 2,024 job_queue quarters + 1,733 adjacent + 99 concurrency + 2 harness) — known overlaps: mpins' 5 pin files ⊂ sweeps; concurrency's 13 files may overlap adjacent's 61-file list. Unique reds: 43 → 100% attributed, 0 new-at-delta. Quarantine: no new flaky tests (all reds deterministic across delta and base).

## Documentation updated

- [x] RESULTS/2026-10-05-question-watch-fanout-verification-gate.md (this file)
- [x] PACKS.md — completed-commission section + ad-hoc pack rows
- [x] LESSONS/2026-10-05-baseline-attribution-temp-worktree.md
- [ ] rules/ensure.md — untouched (user-owned)

## Code changes

None — verification-only gate. Docs above written to `.agents/tester/` only (uncommitted; branch owner may fold into docs commit). Worktree HEAD remained `c7862368`; only pre-existing dirty file `.agents/tidier/notes.md` (not ours).
