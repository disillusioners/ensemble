# Constitution Phase 0 + Fix A — FULL Regression Gate — `feature/job-task-constitution-p0a` @ `b07a91f7`

Date: 2026-09-01/02 (UTC gate window) · Base: `940e88b7` (latest) · Range: `940e88b7..b07a91f7`
Dispatched: 15 worker instances (5 wave-1, 8 full-suite partitions, 1 base-attribution, 1 flake-confirmation). Repo READ-ONLY throughout (zero commits by gate; worktree left on `feature/job-task-constitution-p0a` @ `b07a91f7`; base scratch worktrees created + removed cleanly).

## FINAL VERDICT: ✅ **PASS (merge-ready)** — AMENDED 2026-09-02 @ `bdfa57d1` (was ❌ FAIL @ `b07a91f7`: 2 deterministic blockers, both resolved test-code-only; see §10)

---

## 1. Acceptance sets (5/5 EXACT — independent re-runs)

| Set | Expected | Actual | Result |
|---|---|---|---|
| `bash test/packs/constitution_drift_test.sh` (plain) | 24 passed + branch-guard SKIP notice, exit 0 | 24 passed in 5.48s, exit 0, notice verbatim: `RESULT: SKIP (set EXPECTED_BRANCH to enforce branch guard)` | ✅ |
| `tests/unit/job_state/test_constitution_drift.py` | 10 | 10 passed (4.96s) | ✅ |
| `tests/unit/services/test_linkage_contract_fail_closed.py` | 14 | 14 passed (0.70s) | ✅ |
| `tests/unit/test_manager_enqueue_message_work_id_required.py` | 4 | 4 passed (0.76s) | ✅ |
| `tests/integration/test_job_driven_enqueue_work_id_facade.py` | 3 | 3 passed (1.24s), 0 env-skips | ✅ |

## 2. Pack branch-guard matrix

| Env | Exit | Counts | Wall | Proof |
|---|---|---|---|---|
| unset (plain) | 0 | 24P | 5.5s | SKIP notice + proceeds |
| `EXPECTED_BRANCH=feature/job-task-constitution-p0a` | 0 | 24P | 6.03s | `RESULT: BRANCH-CHECK` + full run |
| `EXPECTED_BRANCH=wrong` | **1** | pytest never ran | **0.029s** | `RESULT: BRANCH-DRIFT (expected wrong, got feature/job-task-constitution-p0a)`; guard `exit 1` at script line 29, before pytest invocation at line 36; zero pytest output |

🟢 Non-blocking pack defects (flag to author, not gating): (a) naive `EXIT_CODE=$?` (no `||` form) at line 40 under `set -euo pipefail` → `RESULT: FAIL/TIMEOUT` markers unreachable; raw pytest exit code still propagates, so gate integrity holds; (b) pack gates only the 2 unit files — facade/integration/manager acceptance sets are NOT inside the shell gate; (c) `-v` + `-q` cancel out; `--override-ini="addopts="` strips repo timeout defaults inside the pack (its own 110s timer remains).

## 3. FULL-suite baseline at HEAD (NEW baseline established — prior "373 adjacent" unrecoverable, as expected)

Scope: ALL pytest-collectable tests under `tests/` EXCEPT `tests/e2e` (live daemon + real LLM = Release-Gate territory), `tests/postgres/` (270, `-m postgres` — need live PG; out of "unit + integration"), shell tests, Playwright JS, fixtures/mocks. Frontend Jest excluded (zero FE files in range). Integration marker re-enabled via `--override-ini="addopts="` + `-m "not postgres"`.

| Partition | Scope | Collected | Passed | Failed | Errors | Skipped | Runtime |
|---|---|---:|---:|---:|---:|---:|---:|
| P1 | unit/{services,routers,rag,graph,job_state,repositories,…} | 1,509 | 1,501 | 8 | 0 | 0 | 42.9s |
| P2 | unit/tools + unit/test_[a-k]* | 2,539 | 2,492 | 24 | 21 | 2 | 57.3s |
| P3 | unit/test_[lm]* | 1,472 | 1,468 | 3 | 0 | 1 | 176.5s |
| P4 | unit/test_[n-z]* | 2,128 | 2,017 | 59 | 2 | 50 | 38.8s |
| P5 | {job_queue,services,message_queue_redesign,migration} | 2,746 | 2,674 | 6 | 0 | 66 | 69.6s |
| P6 | tests/test_[a-j]* + {tools,api,manager,lint,performance,property,static} | 1,769 | 1,675 | 46 | 0 | 48 | 62.3s |
| P7 | tests/test_[k-z]* + {opencode,repositories} | 3,479 | 3,336 | 43 | 0 | 83 | 119.3s |
| P8 | tests/integration (addopts-override) | 416 | 349 | 36 | 16 | 1 | 45.8s |
| **TOTAL** | | **16,058** | **15,512** | **225** | **39** | **251** | ~10.5 min summed |

(+26 postgres-marked deselected within P7/P8; +5 xfailed P7.) Partition logs: `/tmp/full-p1.log` … `/tmp/full-p8.log`.

## 4. Per-failure attribution (the main event) — every one of 264 F+E classified

Method: scratch worktree @ `940e88b7` (own `uv sync` venv; isolation proven via `daemon.__file__` under worktree), batch runs of the exact failing node IDs, then context-matched partition runs + 3× solo determinism budgets for all pass-at-base cases.

| Verdict | Count | Detail |
|---|---:|---|
| **PRE-EXISTING at base** | **261** | fail at base (batch) or fail at base in partition context |
| **🔴 CAUSED (deterministic)** | **2** | pass at base (all contexts), fail at HEAD (solo 3/3 + batch + partition) |
| **🟠 Borderline (pre-existing-broken, order-dependent pass)** | **1** | see 4c |
| NEW-on-branch failure | 0 | — |
| Unexplained | 0 | full reconciliation |

### 4a. 🔴 Blocker 1 — `tests/job_queue/test_f1_mint_processor_tripfire.py::test_processor_crash_recovery_respawn_warns_on_linkage_violation`
- Repro: `.venv/bin/pytest "<node id>" -q` at HEAD → FAIL 3/3 (0.11–0.13s); at base solo → PASS.
- Signature: `LinkageContractError: … Task.work_id != JobItem.job_id` then `TypeError: MagicMock can't be used in 'await' expression`.
- Cause: Fix A escalates the crash-recovery re-spawn site (`job_processor.py:984` enforce=True) from WARN to raise; this f1-incident tripwire test pins the OLD WARN semantics with a Mock mismatch.
- Suggested remediation (dev's call): update test to the new contract (expect raise / supply real `work_id=job_id` in the mock) — NOT a production revert.

### 4b. 🔴 Blocker 2 — `tests/job_queue/test_job_processor_admission_starvation.py::TestJobProcessorAdmissionStarvation::test_admits_job_for_system_default_when_over_100_other_projects_exist`
- Repro: solo at HEAD → FAIL 3/3 (0.14s); at base solo → PASS.
- Signature: `admission_state='done' (expected 'active')` + daemon log `LINKAGE CONTRACT VIOLATION … work_id <MagicMock name='mock.enqueue_message().job_id.__getitem__()'>` at `job_processor.py:1339`; processor recovers from the raise by finalizing the JobItem, bypassing queued→active.
- Cause: same enforce=True escalation vs mocked enqueue result. Same remediation shape (real work_id in mock, or expect-finalize assertion).

### 4c. 🟠 Borderline — `tests/integration/test_agent_bootstrap.py::test_agent_bootstrap_and_hello`
- HEAD: FAIL in ALL contexts (solo 3/3, batch, partition). Base: FAIL solo 3/3, PASS in batch/partition (mock-state carryover from companion `test_agent_bootstrap_with_instance_manager`).
- Test file byte-identical HEAD↔base. The test's mock infra is broken at BOTH commits; its base "pass" was order-dependent leakage. Branch perturbs the leakage → now fails everywhere.
- Not a clean branch regression; recommend fixing the test's mock infra (assert `'messages' in <MagicMock compile().invoke()>` is the visible rot). Flagged, not counted as blocker.

### 4d. Pre-existing census (all base-evidenced this gate)
- Watchover family **47** (decision 28 + watcher_context_builder 9 + integration 4 + phase5 3 + edge_cases 3) — quarantine exact-match, all fail at base.
- SQLite-migration cascade **38** (progressive_dispatch 18, spawn_limit 9, memory_integration 10, migration_api 1) — all fail at base.
- `job_queue_proxy_phase1` ×8, drift cluster (archive 5, hide_kb 5, coder_developer_migration 5, devops 3, job_processor_status_guard 4, builtin_mcp ERROR 17, context7 ERROR 4, webfetch ERROR 2, injection_api 26, manager skill-init 10, agents_api 2, llm_allowed_models_precedence 2, models_split/models, llm_load_balance, innate_skills, enqueue_shared, wanderer 2, phase4, phase5, validate_agent_id, vision, paused_auto_resume 5, terminal_reason_mirror, property/turn_state_machine, static/chokepoint_callers 2, terminal_orphan, queue_routing, compaction_guard, manager_pause_cascade …) — every unit-side failure that is not Blocker 1/2 fails identically at base.
- Integration pre-existing: 33 batch-attributed + **18 order-sensitive** (multi_turn_resume ×3, vscode_routing ×8 incl. parametrize, vscode_security ×8, workspace_sse ×1) — **all 18 reproduce at base in the context-matched partition run** → pre-existing on `940e88b7`, NOT branch-caused.
- Named pre-existing candidates from task context — verified: `test_router_forwards_queue_id_to_enqueue_message_job` ✗ FAIL (pre-existing at base), `test_manager_pause_instance_cascade_delegates…` ✗ FAIL (pre-existing), `test_instance_messaging_compaction_guard…` ✗ FAIL (pre-existing), MagicMock-unawaitable class confirmed. `test_instance_messaging_queue_routing` family — only the named node fails.
- Flakes observed: `test_complete_flow_b_pipeline` (fail P8-run-1 → pass P8-run-2; fails at base), `test_ab_resolution_threshold_met` (skill-evolution StaleDataError family, quarantine sibling), `test_dequeue_concurrent_only_one_worker_wins` (already quarantined).

## 5. Deferred verification surfaces

- **Frozen-binary scanner contract**: all THREE discover functions (`discover_admission_state_writer_paths` :509, `discover_jobitem_creator_paths` :527, `discover_work_id_mint_paths` :550) covered by `RuntimeError`-on-zero-source tests (match "no daemon/ source files readable") via `_SOURCE_ROOT`→empty-dir monkeypatch. Fallback test asymmetric (writers only — `get_all_jobitem_creators/mint_sites` fallback untested). **Honest gap**: frozen-mode is simulated by attribute redirect after import; real PyInstaller/`_MEIPASS` behavior (import-time root computation, physically-absent sources) is UNTESTABLE here and remains a known gap, flagged per protocol.
- **Runtime raise-path**: SAFE. Exactly 2 raise sites (`messaging_types.py:143` mismatch; `instance_messaging.py:696` omission); exactly 4 job-driven enforced sites (Observer `:3887`; JobProcessor main `:1299`, crash-recovery `:984`, orphan-resume `:1073`) — all supply `work_id=<job_id>`. Every internal family (HTTP message API, sources, agent-to-agent, cascade-resume, child reports→dependency-bus, compaction [zero enqueue calls], error reports, report-integrity, WC watchdog, FollowUp, invoke_and_wait) uses default `work_id_required=False` → self-mint branch → raise unreachable. `enqueue_message_job` structurally safe (mint `:2241`/bind `:2291`). Caveat learned the hard way (§4a/4b): tests that MOCK the dispatch result now hit the raise at enforced sites — the 2 blockers are exactly this class.
- **M6 message change**: ✅ omission wording NEW (verbatim: `…job-driven dispatch arrived with work_id=None; auto-mint refused (fail-closed) — pass work_id=job_id.`; `omission=True` 0→1 occurrences base→HEAD); pre-existing mismatch WARN template byte-identical base↔HEAD (diff hunk = pure addition; `logger.warning` block is context).
- **Pack behavior**: matrix in §2.

## 6. Mock-quality audit (TrueAuto rule)

**PASS.** Integration file (3) = real dispatch (real InstanceManager → InstanceMessagingService → `_ensure_work_id_fail_closed` → file-backed SQLite) with DB-read-back (`tasks[0].work_id == job_id`). Manager file (4) = mock-only by declared scope but asserts forwarded kwargs CONTENT (defeats AsyncMock-accepts-anything within scope). Linkage file = 10/14 real-invocation + 4/14 source-grep tripwires (🚩 blind to bind-time regression — acceptable only because the behavioral layers carry it; the 4 production dispatch sites remain behaviorally untested — residual concern for follow-up). Regression catch matrix: (a) facade-drops-kwarg → 6 tests; (b) silent-accept → 3; (c) raise-removed → 3; two independent layers.

## 7. Gaps / known limitations

1. `tests/postgres/` (270) excluded — needs live PG provisioning (out of "unit + integration"; house pg packs cover selectively).
2. `tests/e2e/` excluded — Release Gate territory (live daemon + LLM, run individually per ensure.md).
3. Real frozen-binary (PyInstaller) behavior untested (see §5).
4. Frontend Jest excluded (zero FE files in range `940e88b7..b07a91f7`).
5. Order-sensitive pre-existing flakes (§4d) will keep re-redding full-suite integration runs until quarantined/fixed — QUARANTINE.md updated this gate.

## 8. Documentation updated

- PACKS.md: `constitution_drift_test` registered (was on-disk-but-unregistered) with gate result + matrix + set-e caveat.
- QUARANTINE.md: +2 rows (integration order-sensitive family; skill-evolution threshold_met extension).
- LESSONS/2026-09-02-enforce-true-vs-mock-workid-and-context-attribution.md.
- This file.

## 9. Verdict

- Acceptance 5/5 · matrix ✅ · raise-path SAFE · M6 ✅ · mock-audit PASS · frozen-mode covered-at-simulation-level (gap flagged)
- Full suite: 15,512 passed; **0 unexplained failures**; 261 pre-existing (base-evidenced); 1 borderline order-dependent pre-existing-broken test
- **🔴 2 deterministic branch-caused regressions (both test-vs-new-contract conflicts from Fix A's WARN→raise escalation at enforced job-driven sites vs Mock work_id):**
  1. `test_f1_mint_processor_tripfire.py::test_processor_crash_recovery_respawn_warns_on_linkage_violation`
  2. `test_job_processor_admission_starvation.py::…::test_admits_job_for_system_default_when_over_100_other_projects_exist`
- **FINAL: ❌ FAIL — NOT merge-ready** until the 2 blockers are resolved (expected shape: update the 2 stale tests to the enforced contract — or justify); borderline item 4c recommended but non-blocking.

> **AMENDED — see §10: both blockers resolved at `bdfa57d1` (test-code-only, numstat-verified); verdict flipped to ✅ PASS.**

## 10. Flip-verification @ `bdfa57d1` — AMENDED VERDICT: ✅ PASS (merge-ready)

Commit `bdfa57d1` (`test(job_queue): update 2 stale tests to Fix A fail-closed contract`, parent `b07a91f7`). Verification: 6-step light re-gate (worker `flip-verify-bdfa57d1`, test-pack-execution), rev-parse-bracketed.

| Step | Check | Result |
|---|---|---|
| 1 | Isolation (trust-but-verify) | ✅ `git show --numstat bdfa57d1` = `git diff --numstat b07a91f7..bdfa57d1` = exactly 2 files (`test_f1_mint_processor_tripfire.py` +228/−28, `test_job_processor_admission_starvation.py` +26/−1), zero production/daemon changes |
| 2 | Both files combined run | ✅ 8/8 passed in 0.21s (matches dev claim) |
| 3 | Solo determinism 3×3 (raises-omission / starvation / new WARN-mode) | ✅ 9/9 PASS, ~0.1s each, deterministic |
| 4 | `tests/job_queue/` partition re-run | ✅ `1609 passed, 39 skipped, 0 failed in 30.77s` — matches dev claim exactly (39 skips = repo-default integration/postgres deselection) |
| 5 | Acceptance sets @ `bdfa57d1` | ✅ 5/5: pack 24P exit 0 + SKIP notice; drift 10; linkage 14; manager 4; facade 3 |
| 6 | WARN-mode spot-check (code read) | ✅ (a) drives `_assert_linkage_contract` directly with `enforce=False` on the self-mint/default branch — genuinely non-job-driven, NOT an enforced site in disguise; (b) asserts WARN via `caplog` WARNING-level `"LINKAGE CONTRACT"` + `"LegacyInternalCaller"` record, no `pytest.raises`; (c) renamed raises-test asserts verbatim M6 fragment `"job-driven dispatch arrived with work_id=None"` (in both the log record AND `complete_job(error=…)`), `complete_job` awaited exactly once with `DemandState.FAILED` (fail-closed finalization), and site binding (`work_id_required is True` + `work_id == proc_job.job_id` in `enqueue_message` kwargs) |

**Baseline continuity note:** the full-suite baseline from §3 (16,058 collected / 15,512P / 225F / 39E / 251S @ `b07a91f7`) STANDS for `bdfa57d1` — only the 2 test files changed since (numstat-verified in step 1); no production surface moved, so re-running the full suite would reproduce §3 modulo the two now-fixed tests.

**Carried known-gaps / follow-ups (non-blocking):**
1. 🟡 Frozen-mode coverage is simulation-only (`_SOURCE_ROOT` monkeypatch); real PyInstaller/`_MEIPASS` frozen-binary behavior untested in this environment. Fallback test asymmetric (writers only; `get_all_jobitem_creators`/`get_all_mint_sites` fallback untested).
2. 🟡 4 source-grep tripwire tests in `test_linkage_contract_fail_closed.py` are text-level only (blind to bind-time regressions; behavioral load carried by facade/integration files); the 4 production dispatch sites (Observer + JobProcessor ×3) remain behaviorally untested end-to-end.
3. 🟠 `tests/integration/test_agent_bootstrap.py::test_agent_bootstrap_and_hello` — pre-existing broken mock infra (solo-fail at base AND HEAD; base pass was order-dependent carryover). QUARANTINE.md row added; fix recommended.
4. 🟢 Pack hygiene (author follow-ups): `RESULT: FAIL/TIMEOUT` markers dead under `set -e` (exit code still propagates); shell gate covers only the 2 unit files; `-v`+`-q` cancel out.
5. 🟢 21-test integration order-sensitive family (httpx/conftest pollution) + `test_complete_flow_b_pipeline` flake — pre-existing at base, quarantined; will keep re-redding full-suite integration runs until the polluter is fixed (pair-bisection follow-up from 2026-08-28 row).

**AMENDED FINAL: ✅ PASS — merge-ready @ `bdfa57d1`.**

