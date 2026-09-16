# Phase 2: Lifecycle Exemption + Boot Reconciliation

Date: 2026-09-15
Author: planner[v2] via plan-creation worker
Parent plan: `.agents/shared/planning/service-tool/plan-overview.md`
Decisions anchor: `decisions.md` (D1, D6 in particular); OQ#2 disposition recorded here
Phase 1 dependency: this phase consumes Phase 1's `ServiceManager`, `ServiceRepo`, `service_spawner`, and `service_tracking` table.

---

## Objective

Prove by construction AND by test that a service started via `service_start` survives every daemon-internal lifecycle trigger that would normally kill a `bash`-spawned or `proc_run`-spawned process, AND that after a daemon restart the reconcile sweep correctly reconciles service state against the OS process table (marking EXITED on liveness/start-time mismatch, leaving RUNNING when the PID is alive and the start-time matches). The deliverable is a single integration test file `tests/integration/test_service_tool_kill_site_exemption.py` that triggers each of the 13 kill sites and asserts the service's PID is still alive + the row state is unchanged, plus a complete `ServiceReconciliationService` implementation that runs as a 90s periodic in lifespan.

The single sentence that marks this phase complete: **for every kill site K1–K13 in `research-lifecycle-killsites.md:28-43`, a service started before the trigger remains alive after the trigger fires; for a daemon restart, the reconcile sweep correctly marks services with dead or recycled PIDs as `EXITED` while leaving live services in `RUNNING` with no extra daemon-side handle.**

---

## Background

Phase 1 ships the buildable surface (tools, repo, spawner, registration, privilege, kill-switch, boot probe). What Phase 1 does NOT prove is that the exemption claim holds in practice — only that the code path exists. Phase 2 closes that gap.

The kill-site inventory at `research-lifecycle-killsites.md:28-43` enumerates 13 sites (K1–K13). All are registry-scoped (none walk `/proc`, none use `killpg(0)`, no ppid traversal). The exemption proof is therefore a 3-step argument:
1. Spawn unregistered (model on `upgrade_journal.spawn_executor`).
2. `setsid` via `start_new_session=True` at spawn (no in-group kill window).
3. Reparent to launchd/init on daemon death (no orphan-reap path exists; documented in `bash.py:74-79` and `proc_tools.py:1636-1643`).

Phase 2's job is to verify all three steps in code (not by argument) and to wire the periodic reconcile sweep so the row state stays eventually consistent with the OS process table.

---

## Shared Context (what every implementer MUST know)

- **D1 (decisions.md L26-60):** spawn via `subprocess.Popen(start_new_session=True, ...)`. The 3-step exemption argument is binding.
- **D6 (decisions.md L417-556):** reconcile sweep = PID liveness + start-time match → mark EXITED on mismatch. NO re-spawn. Mount as `ServiceReconciliationService` periodic service in lifespan, mirroring `EligiblePendingSweepService`.
- **13 kill sites** (`research-lifecycle-killsites.md:28-43`): K1–K3 (bash timeout/cancel), K4 (bash cleanup_instance), K5 (bash cleanup_all), K6–K8 (proc_run lifetime), K9–K11 (code-server), K12 (Windows), K13 (git-diff/doc-commit direct children).
- **Phase 1 contracts consumed:**
  - `ServiceManager.stop(name, force=False)` returns the 4-tuple `(running, exited, not_found, pid_recycled)` (Phase 1.B.6).
  - `ServiceManager.start(...)` returns `{name, pid, status, log_path}` (Phase 1.B.5).
  - `service_tracking` table has columns `pid`, `start_time`, `status`, `exit_code`, `name`, `command`, `cwd`, `started_by_instance_id`, `started_by_agent_id`, `log_path`, `created_at`, `updated_at`.
  - `ServiceReconciliationService` skeleton exists (Phase 1.C.12) with `start()`, `stop()`, `sweep_once()`.
- **Lifespan integration points** (`daemon/api.py`):
  - Boot: insert after OrphanWatcherSweep at `:656-696`, before vscode at `:1400-1406` (Phase 1.C.14).
  - Shutdown: getattr-guarded stop at `:1367-1397` (Phase 1.C.15).
- **Instance lifecycle entry points** (relevant to exemption test triggers):
  - `terminate_instance` at `daemon/services/instance_lifecycle.py:2113`.
  - `pause_instance_cascade` at `daemon/services/instance_lifecycle.py:3030`.
  - `cancel_graph_task` at `daemon/manager.py:8968-9060`.
  - `manager.shutdown` at `daemon/manager.py:10894-10981` (process-relevant at `:10922-10952`).
- **Concurrency limit:** Phase 2 is single-worker (sequenced integration tests). Each of the 13 exemption tests must observe a known pre-state and assert a known post-state; splitting into 2-3 parallel workers risks cross-test contamination (instance 1's service status interfering with instance 2's reconcile).
- **Repo & Dev Environment Conventions blueprint item (d):** tests run exclusively via `uv run python -m pytest` from worktree root.
- **Plan-doc access (caller-pinned):** the plan directory exists ONLY on `feature/service-tool`. Reference from absolute path.

---

## Touched Files (with verified anchors at `f6ca8791`; re-locate by symbol before editing)

| File | Symbol / anchor | Reason |
|------|----------------|--------|
| `daemon/services/service_reconciliation.py` (Phase 1.C.12 skeleton) | full file | full implementation of `sweep_once` + counters + log lines |
| `daemon/services/service_tool_manager.py` (Phase 1.B.2) | `list_active`, `mark_exited`, `repo.list_active`, `repo.mark_exited` | helpers consumed by reconcile |
| `tests/integration/test_service_tool_kill_site_exemption.py` (NEW) | next to `tests/integration/test_message_metadata_deletion_paths_prune.py` | 13-site exemption matrix |
| `tests/integration/test_service_reconciliation_real_pg.py` (NEW, optional) | next to `tests/integration/checkpoint_prune_real_saver.py` | PG-backed reconcile integration test |
| `tests/unit/services/test_service_reconciliation.py` (Phase 1.C.12 unit skeleton) | full file | expand unit tests for sweep behavior |
| `daemon/api.py` | boot insertion at `:600-696` (Phase 1.C.14); shutdown at `:1367-1397` (Phase 1.C.15) | no new edits beyond Phase 1.C; just verifying wiring |

**Do NOT modify:** `daemon/services/instance_lifecycle.py` (do NOT add service cleanup to the terminate cascade — that would violate the exemption invariant); `daemon/manager.py:10894-10981` shutdown body (do NOT add `cleanup_all(services)` — same invariant); `daemon/tools/bash.py`, `daemon/tools/proc_tools.py`, `daemon/services/vscode_server_manager.py` (do NOT add service-aware logic to existing kill paths).

---

## Tasks

### Phase 2.A — Complete `ServiceReconciliationService` (single worker)

| # | Task | Depends On | Acceptance |
|---|------|------------|------------|
| 2.A.1 | Read `decisions.md` lines 432-540 (D6 — amended per A3 + A5 + A6 + A8 + A9) end-to-end. Confirm sweep pseudocode, sweep counters (incl. `starting_reaped`), log line format, mount point, A3 eternal-`starting` reaper branch, A5/A6 mount + boot-pass, A8 config naming + stop semantics, A9 narrow-collaborator (`repo` not `manager`). | Phase 1 complete | Implementer can write the 50-line `sweep_once` body from memory against the pseudocode. |
| 2.A.2 | Implement `ServiceReconciliationService.sweep_once()` (full) per `decisions.md:512-540` amended pseudocode. Steps: (a) `await asyncio.to_thread(self._repo.list_active)` returns rows where `status IN ('starting','running')` (A9 — direct `ServiceRepo` inject, NOT `self._manager.repo`); (b) for each row: (b1) **A3 — eternal-`starting` reaper:** if `pid is None` and `_row_age_seconds(row) >= self._starting_grace_seconds` (default 30s) → `mark_exited(...)` + WARNING log `[ServiceTool] reconcile_reaped name=… reason=spawn_failed_or_interrupted age=…s grace=…s` + increment `starting_reaped` counter; (b2) else if `pid is None` → still inside grace, leave alone (spawn may be in progress); (b3) call `service_spawner.get_process_start_time(row.pid)` via `asyncio.to_thread`; (b4) on `None` → `mark_exited(...)` (A13 atomic guard) + INFO log `… reason=dead`; (b5) on start-time mismatch → `mark_exited(...)` + WARNING log `… reason=pid_recycled`; (b6) else increment `alive` counter. Errors caught into `errors` counter + `logger.exception`. After loop: if `reaped > 0` OR `starting_reaped > 0`, emit `[ServiceTool] reconcile_swept alive=N reaped=M starting_reaped=S errors=K` INFO. Return counters dict. | Tasks 2.A.1, Phase 1.C.12 | `sweep_once` round-trip unit test: 4 rows (live, dead, recycled, starting-within-grace) → returns `{"alive": 1, "reaped": 2, "errors": 0, "starting_reaped": 0}`; + 1 row `starting-past-grace` → returns `{"alive": 1, "reaped": 2, "errors": 0, "starting_reaped": 1}` with WARNING log line. |
| 2.A.3 | Implement the `_run_loop` per `decisions.md:500-510`: while `not self._stop_event.is_set()`: try `await self.sweep_once()`; on `Exception` log + increment errors; `await asyncio.wait_for(self._stop_event.wait(), timeout=self._interval_seconds)`; on `TimeoutError` continue. **A8 stop semantics:** `stop(timeout=5.0)` uses template `set()` → `cancel()` → `await` with `CancelledError` caught + swallowed; `asyncio.TimeoutError` triggers `task.cancel()`. Drop the dead `max_concurrent` param (A8). | Task 2.A.2 | Loop unit test: 3 ticks → 3 sweep_once calls; `stop()` exits cleanly within timeout; `CancelledError` swallowed. |
| 2.A.4 | Implement `mark_exited` row transitions with **`updated_at` repo-side bump** (A12 — no `datetime.utcnow` factory at the column, no `onupdate=text("now()")` non-portable kwarg). Use `_now_iso()` factory or repo-side bump in the same SQL UPDATE. **A13 atomic guard:** every status-mutating UPDATE is guarded `WHERE id=? AND status IN ('starting','running')`; `mark_exited` returns the row count (1 = transitioned, 0 = race-lost). Pattern: `daemon/repositories/task/repository.py:91-104` (status-write discipline — direct `UPDATE` outside named transition is forbidden by C7); atomic-claim pattern precedent `report_injection/models.py`. | Task 2.A.2 | Direct-write guard NOT triggered; sweep transitions go through the repo method; atomic-guard verified (concurrent sweep + service_stop calls: only one wins the row transition; the loser sees row count=0 and treats as idempotent). |
| 2.A.5 | Add `service_status` and `service_list` inline liveness reconciliation (Phase 1.B.7 stub): when called, sweep the row's PID + start-time and update the row if mismatched. This makes `service_status` a primary reconciliation path, not just a read. **A13:** the inline reconciliation uses the same atomic-guard pattern — race-safe with the periodic sweep. | Phase 1.B.7 | `service_status` returns fresh state within the same call; row updated atomically; race test: concurrent `service_status` + periodic sweep — both observe consistent final state. |
| 2.A.6 | Add a `_resolve_service_tool_enabled=False` early-return path to `ServiceReconciliationService.sweep_once`: if the kill-switch is OFF, log DEBUG `service_reconciliation_disabled` and return zero counters. Mirror the `feature/monitoring-followups` precedent (defensive, never assume ON). | Task 2.A.2 | Unit test: with kill-switch OFF, sweep returns `{"alive": 0, "reaped": 0, "errors": 0, "starting_reaped": 0, "disabled": True}`; no DB queries. |

### Phase 2.B — 13-site exemption test matrix (single worker, sequenced)

| # | Task | Depends On | Acceptance |
|---|------|------------|------------|
| 2.B.1 | Read `research-lifecycle-killsites.md` lines 28-205 end-to-end (kill-site inventory + per-path narratives + global scan table). | Task 2.A.2 | Implementer can map each K1–K13 to the test trigger it requires. |
| 2.B.2 | Build a `ServiceExemptionFixture` test fixture (top-level `tests/integration/test_service_tool_kill_site_exemption.py`). Fixture creates a real (non-mock) `InstanceManager` against a file-backed SQLite DB (per `test_chart_tools_reuse_integration.py:80-109`); spawns a service via `service_start(name, command=[sys.executable, "-c", "import time; time.sleep(300)"], cwd=tmp_path)`; yields the manager + the service row + the spawned PID. | Task 2.A.2 | Fixture importable; service row inserted with PID alive; cleanup at teardown kills the child via `service_stop`. |
| 2.B.3 | Test K1+K2: bash timeout → 5s → SIGKILL. Trigger: call `bash` tool with `timeout=1` and a 60s sleep; assert the bash group is killed, but the service's PID is still alive AND the service row is unchanged. Use a 30s test timeout (bash timeout is 1s + K2 escalation; reconcile doesn't fire inline — just confirm via `os.kill(service.pid, 0)` liveness check). | Task 2.B.2 | Service PID alive post-test; row `status="running"`; row `pid` unchanged. |
| 2.B.4 | Test K3: `asyncio.CancelledError` inside bash. Trigger: spawn the bash tool with a 60s sleep, then `graph_task.cancel()` (mirroring pause/terminate); assert service PID alive + row unchanged. | Task 2.B.3 | Same as 2.B.3 acceptance. |
| 2.B.5 | Test K4: bash `cleanup_instance`. Trigger: spawn bash tool with a registered group; call `get_bash_process_registry().cleanup_instance(test_instance_id)`; assert service PID alive + row unchanged (service was NEVER registered in the bash registry). | Task 2.B.4 | Same. |
| 2.B.6 | Test K5: bash `cleanup_all`. Trigger: spawn multiple bash tools across instances; call `get_bash_process_registry().cleanup_all()`; assert service PID alive + row unchanged. | Task 2.B.5 | Same. |
| 2.B.7 | Test K6: `proc_stop` SIGTERM/SIGKILL. Trigger: spawn `proc_run` with a 300s sleep; call `manager.stop_process(test_instance_id, process_id, force=False)`; assert the proc_run child is killed AND the service PID is alive + row unchanged. | Task 2.B.6 | proc_run child gone; service alive; row unchanged. |
| 2.B.8 | Test K7: race guard in `proc_tools.start_process` (C2 re-check after spawn). This is a spawn-time guard, not a kill site per se; trigger by calling `start_process` twice with the same `process_id`; assert second call fails AND service PID alive + row unchanged. | Task 2.B.7 | Same. |
| 2.B.9 | Test K8: `proc_run.cleanup_instance` + `cleanup_all`. Trigger: spawn `proc_run`; call `get_background_process_manager().cleanup_instance(test_instance_id)`; assert service PID alive + row unchanged. Repeat for `cleanup_all()`. | Task 2.B.8 | Same. |
| 2.B.10 | Test K9+K10: code-server stop. Trigger: NOT applicable to service — code-server pid/pgid is tracked separately (`VSCodeServerManager`); trigger `vscode_manager.stop()` and assert service PID alive + row unchanged. The proof is that the code-server kill helper targets only `state.pid/pgid`, not the service PID. | Task 2.B.9 | Same. |
| 2.B.11 | Test K11: code-server `_kill_orphan` (half-spawn failure). Trigger: NOT applicable — code-server spawn is separate; trigger by simulating a code-server start() failure; assert service PID alive + row unchanged. | Task 2.B.10 | Same. |
| 2.B.12 | Test K12: Windows-only path. SKIP with `@pytest.mark.skipif(platform.system() != "Windows", reason="windows-only")`. On Windows: trigger `bash._kill_process` windows branch; assert service PID alive + row unchanged. (darwin-only daemon target; this is a defensive pin.) | Task 2.B.11 | Test skipped on darwin; on Windows runner, same acceptance as 2.B.3. |
| 2.B.13 | Test K13: git-diff / doc-commit `subprocess.run(..., timeout=...)` direct child kill. Trigger: not directly applicable — these are short-lived sync git operations; trigger by calling `git_diff_service` with a 1s timeout against a slow filesystem; assert the git-diff child is killed AND the service PID is alive + row unchanged. | Task 2.B.12 | Service alive; row unchanged. |
| 2.B.14 | Aggregate: parametric test `test_kill_site_exemption[kill_site_id]` running all 13 cases; report `[K1, K2, ..., K13]` with pass/fail per site. Output goes to `.agents/tester/RESULTS/2026-09-15-service-tool-verification.md` template (Phase 3 task). | All 2.B.* tasks | Parametric run reports 13/13 pass on darwin; K12 marked SKIP. |
| 2.B.15 | **F9 — Full-cascade exemption case (≥1):** spawn a service on a test instance; then call `manager.terminate_instance(test_instance_id)` (the full terminate cascade). Assert (a) the service PID is still alive after the cascade completes, (b) the service row is unchanged in `service_tracking`, (c) `service_status(name)` returns `status="running"` with the original `pid` + `start_time`. The cascade traverses `instance_lifecycle.py:2113-2354` (terminate path) which invokes K4 (bash `cleanup_instance`), K6/K8 (proc cleanup), graph-task cancel; NONE of those should reach a service that was never registered in any of the three registries. This is the "full lifetime cascade" case — distinct from the per-site K1-K13 triggers in that it fires ALL relevant cascade sites at once. | Task 2.B.13 | Service PID alive post-cascade; row unchanged; `service_status` returns `running`. |
| 2.B.16 | **F9 — Pause-cascade exemption case (optional):** spawn a service on a test instance; then call `manager.pause_instance_cascade(test_instance_id)`. Assert service PID still alive + row unchanged. The pause cascade does NOT sweep service rows (Phase 1 design invariant — pause preserves state for resume); the test pins this invariant. Mark `@pytest.mark.optional` so the test is not required to pass for `SHIP` verdict — the pause case is a design statement, not a kill-site per se. | Task 2.B.15 | Service PID alive post-pause; row unchanged. |
| 2.B.17 | **F9 — GC-sweep exemption case (optional):** spawn a service on a test instance; trigger `_cleanup_cached_instances` (the GC sweep that releases in-memory graphs for terminal/PAUSED instances — `manager.py:4499-4585`, cadence 10min, TTL `INSTANCE_CACHE_TTL_HOURS`=24h). Assert service PID still alive + row unchanged. The GC sweep releases RAM dicts only — no OS kill. Mark `@pytest.mark.optional`. | Task 2.B.16 | Service PID alive post-GC; row unchanged. |

### Phase 2.C — Reconcile sweep end-to-end (single worker)

| # | Task | Depends On | Acceptance |
|---|------|------------|------------|
| 2.C.1 | Test: kill-sweep reaping. Spawn a service with a 60s sleep; externally kill the PID via `os.kill(pid, SIGKILL)`; advance time by 90s (or call `sweep_once()` directly); assert row `status="exited"`, `exit_code=None`; INFO log line `[ServiceTool] reconcile_reaped name=… pid=… reason=dead` present. | Task 2.A.2 | Row transitioned; log line emitted. |
| 2.C.2 | Test: PID-reuse sweep. Spawn service A; externally kill PID A; spawn service B (gets same PID via kernel recycle); advance time + sweep; assert A's row `status="exited"` with `reason=pid_recycled` log; B's row still `status="running"`. | Task 2.C.1 | A EXITED, B RUNNING; WARNING log emitted for A. |
| 2.C.3 | Test: periodic loop. Start the service; observe 3 ticks of the loop (mock the wait to fire `TimeoutError` after 1ms each); assert 3 `sweep_once` calls; assert `stop()` exits within 5s. | Task 2.A.3 | 3 calls observed; clean shutdown. |
| 2.C.4 | Test: graceful daemon shutdown + restart reconciliation. (a) Spawn a service via the daemon; (b) trigger `manager.shutdown()` gracefully (the test harness simulates this); (c) re-create `InstanceManager` against the same DB; (d) call `ServiceReconciliationService.sweep_once()` (simulating the boot-time tick); (e) assert the service row is either `running` (if PID survived) or `exited` (if PID was reaped by the OS); (f) the boot INFO log line `[ServiceTool] reconcile_swept alive=… reaped=… errors=…` is emitted. | Tasks 2.C.1, 2.C.2 | Restart survival: live PID row stays RUNNING; dead PID row EXITED; log line present. |
| 2.C.5 | Test: cap counter survives restart. Spawn 5 services; restart daemon; reconcile sweep observes 5 RUNNING rows; cap=10 still has headroom for 5 more starts. | Task 2.C.4 | 5 rows RUNNING post-restart; cap counter recomputed from `list_active()`. |
| 2.C.6 | Test: OQ#2 multi-daemon guard (v1 documented limitation). Spawn a service with daemon_instance_id=D1; have a second test InstanceManager (simulating D2) call `service_stop(name)` — assert the service IS stopped (v1 does not guard against this; OQ#2 deferred). Document the v1 limitation in the test docstring so future contributors understand the gap. | Tasks 2.C.1, 2.C.2 | Test passes; docstring surfaces the v1 limitation. |

### Phase 2 merge gate

| # | Task | Depends On | Acceptance |
|---|------|------------|------------|
| 2.MG.1 | Run the full Phase 2 test surface: `uv run python -m pytest tests/integration/test_service_tool_kill_site_exemption.py tests/unit/services/test_service_reconciliation.py`. | All Phase 2 tasks | All tests pass; the parametric 13-site matrix reports 13/13 (K12 SKIP on darwin); **A3** eternal-`starting` reaper test passes (starting row past grace is reaped with WARNING log); **A9** sweep injects `ServiceRepo` directly (no `manager.repo` reach-through); SC-1 (kill-site exemption provable site-by-site) and SC-2 (restart survival) are demonstrably met. |
| 2.MG.2 | Boot daemon in test worktree; spawn a real service via the API (or via a fixture that exercises the manager); trigger `manager.shutdown()`; restart daemon; call `service_status(name)` via the API; assert `status="running"` (or `"exited"` if the OS reaped the PID); emit the boot INFO log line. | Task 2.MG.1 | End-to-end restart-survival verified; SC-2 met. |
| 2.MG.3 | Verify no cross-file schema drift between Phase 1's 3-site registration and Phase 2's reconcile reads. | Task 2.MG.1 | `git diff --stat` matches the Touched Files table; reconcile reads use the same column names Phase 1 wrote. |
| 2.MG.4 | Confirm `instance_lifecycle.py:2330-2354` (terminate cascade) and `manager.py:10922-10952` (shutdown `cleanup_all`) are UNTOUCHED — Phase 2 must NOT add service-aware cleanup to these paths. | Task 2.MG.1 | `git diff daemon/services/instance_lifecycle.py daemon/manager.py` shows zero edits beyond Phase 1's `_ensure_postgres_columns` block. |

---

## Coupling (Phase 2 internal)

- **Tight:** Phase 2.A (sweep impl) ↔ Phase 2.B (exemption tests) — tests call `sweep_once` directly to simulate the boot-time tick.
- **Tight:** Phase 2.A ↔ Phase 2.C (reconcile tests) — `mark_exited` transitions must be atomic and idempotent for the parametric run to pass deterministically.
- **Independent of:** other phases.

## Risks (Phase 2-specific; cross-phase risks in `plan-overview.md`)

| # | Risk | Impact | Likelihood | Mitigation |
|---|------|--------|------------|------------|
| 1.P2 | One of the 13 kill sites inadvertently gains a new call that includes `service_*` rows in its scope (e.g., a future contributor adds `cleanup_instance(services)` to the terminate cascade) | High | Low | (a) Phase 2.B's matrix is the regression net — when run against a future PR, it surfaces any kill site that incorrectly targets services; (b) Phase 3 documents the registry-scoped invariant in `docs/architecture/instance-lifecycle.md` so future contributors see the constraint. |
| 2.P2 | Parametric test for K12 (Windows) is unreachable on darwin — leaves a class of bug undetected | Low | Low | Defensive pin: mark K12 `@pytest.mark.skipif(platform.system() != "Windows", reason="windows-only")`; document in RESULTS file that the test is darwin-skipped per daemon target matrix. |
| 3.P2 | `service_status` inline reconciliation races with `ServiceReconciliationService` sweep — both update the same row simultaneously | Medium | Low | (a) Repo writes are atomic via `engine.begin()` (Phase 1.A.4); (b) **A13 atomic-guard** `WHERE id=? AND status IN ('starting','running')` on every status-mutating UPDATE makes sweep↔stop↔status-update races idempotent (loser sees row count=0); (c) `mark_exited` is idempotent (transitioning `exited → exited` is a no-op). |
| 4.P2 | Test fixture for the parametric run takes >30s because of bash sleep + escalation timing; CI timeout | Medium | Medium | Use `sys.executable -c "import time; time.sleep(N)"` patterns with N=1 for the bash/proc tests (kill happens fast); reserve longer sleeps for the explicit `service_start` targets (those are the rows under test). Total matrix wall-time budget: <60s. |
| 5.P2 | OQ#2 v1 limitation (no `daemon_instance_id`) causes test flakiness in the multi-daemon guard test | Low | Low | Test 2.C.6 deliberately documents the v1 limitation; the test PASSES (proving the v1 contract) — flakiness would mean the implementation does NOT match the v1 contract, which is the bug we want to surface. |

## Rollback / Kill-switch

- **Phase 2 has no new kill-switch beyond Phase 1's `ENSEMBLE_SERVICE_TOOL_ENABLED`.** Setting it to 0 disables: (a) the `ServiceReconciliationService` lifespan start; (b) `sweep_once` is a no-op (`disabled=True`); (c) inline reconciliation in `service_status` is also no-op (defensive guard).
- **Phase 2 does NOT introduce any new kill sites.** If the parametric matrix reveals a site that DOES reach services, that's a CRITICAL bug — revert immediately and route to architect + governor.
- **Hot-unwind mid-flight:** flip env to 0 + restart; existing rows stay; reconcile stops; inline `service_status` returns stale data but does no harm (a no-op sweep call is idempotent).

---

## Exit Criterion

Phase 2 is complete when ALL of the following are true:

1. `uv run python -m pytest tests/integration/test_service_tool_kill_site_exemption.py` reports 13/13 (K12 SKIP on darwin); SC-1 demonstrably met.
2. `uv run python -m pytest tests/unit/services/test_service_reconciliation.py` passes the expanded sweep tests (mark_exited, periodic loop, kill-sweep, PID-reuse, restart survival, cap counter survives).
3. `uv run python -m pytest tests/integration/test_service_reconciliation_real_pg.py` (if the optional PG-backed test is added) passes against a disposable PG.
4. `instance_lifecycle.py` and `manager.py` shutdown body are UNTOUCHED beyond Phase 1's `_ensure_postgres_columns` block.
5. Boot INFO log line `[ServiceTool] reconcile_swept alive=… reaped=… errors=…` is emitted on the test daemon's first reconcile sweep.

Phase 3 (tests + docs) can then begin (≤2 parallel workers).