# Phase 3 Pause-Report-Recovery: Boot-Splice Regression + Re-Run (2026-08-20)

Branch `feature/pause-report-recovery`. SHAs: base `6bb99d5f`, old HEAD `73bfe0ed`(+test commits → `95edd680`), repaired HEAD `b9b2929a`.

## 1. The boot-splice regression (record; FIXED in b9b2929a)

- Commit `1d5144f4` spliced 4 methods (`_has_non_terminal_injection_for`, `_is_parent_terminal`, `_get_event_loop`, `_session_scope`) into the middle of `InstanceManager.__init__` using the `self._write_guard = WritePauseGuard()` line as anchor. `__init__` truncated to 33 lines; ~858 lines of init (engine, repos, 7 services, `_completion_registry`) orphaned as after-yield statements of the `@contextlib.contextmanager` `_session_scope` — executed only when a CM block EXITS, and nothing in the startup path enters it.
- Impact: `initialize()` crashed at `manager.py:2049` (`AttributeError '_completion_registry'`); daemon lifespan dead; ALL request handling/cancellation/shutdown paths dereferenced None on first use.
- Detection story — 3 independent confirmations: (a) suite-worker failure attribution (base worktree: phase4_manager_decomposition PASSED on base), (b) e2e worker's 3 failed daemon relaunches, (c) dedicated falsify-first verifier (parent commit boots clean on scratch PG).
- **Why 3 review cycles + green unit suite missed it**: phase-3 unit tests build the manager as `MagicMock` + monkeypatch `_session_scope`, so the truncated `__init__` is structurally invisible; module imports fine (no syntax error); the commit message described router/repo work, giving reviewers no cue. The only detector was `test_phase4_manager_decomposition::test_manager_has_all_seven_service_attributes` — a source-text assertion on `__init__`.
- Fix `b9b2929a` verified: init byte-identical to `1d5144f4^` reference, live PG boot `INITIALIZE OK`, TestBootSmokeRegression 3/3 (empirically fails on broken parent).

**Lesson: any test asserting manager structure should be paired with a real-construction smoke test (`InstanceManager(config=...)` + `initialize()` on PG). Mock-holder patterns hide init-order regressions entirely.**

## 2. Re-run deltas (95edd680 → b9b2929a): repair is clean

- Unit chunks A–C / D–F / G–M: zero delta (7F / 2F / 9F all pre-existing, identical sets). A–C surfaced 4 TestContext7Bootstrap setup ERRORs — attributed pre-existing-at-base via worktree; the repair *unmasked* them by restoring the `config.blueprint` init block (base-truth restored; fixture needs `spec=BlueprintConfig`).
- N–Z: phase4 detector now PASSES (73/73). Exactly the 15 pre-existing failures remain; `test_report_deferred_marker_pipeline::test_concurrent_duplicate_marker_absorbed` is FLAKY (~18% single-node) → QUARANTINE.md. Repair-introduced genuine failures: **zero**.

## 3. Environment lessons

- venv lacks `pytest-timeout` AND `pip` (uv-managed): ini `timeout=30` is INERT — always wrap with shell `timeout`. Install via `uv pip install pytest-timeout` when possible.
- venv lacks `hypothesis` → `tests/property/test_turn_state_machine.py` cannot collect (env limit, not a defect).
- PG suite is serial-only (xdist guard) and marker-gated: `.venv/bin/pytest tests/postgres/ --override-ini="addopts=" -m postgres`.
- Dev daemon `--reload` on a broken branch dies mid-reload while holding port 8079 → looks like a hung daemon (e2e import-time probes ReadTimeout). Health-probe + verified-PID kill/restart is the recovery; never assume the daemon is healthy because the process exists.

## 4. Other findings recorded (not fixed, per brief)

- Lane-2 no-row backstop malformed SQL on PG (`daemon/repositories/report_injection/repository.py:712`) — 1-line fix documented.
- BUG-A legacy-SQLite migration column-swap collision (NOT NULL forever + silent APPLIED).
- BUG-1/BUG-2 `claim_for_task_delivery(None)` contract violations.
- 4 known pre-existing job_processor status-guard failures (legacy message-orphan path removed by ancestor 4a872c35).
