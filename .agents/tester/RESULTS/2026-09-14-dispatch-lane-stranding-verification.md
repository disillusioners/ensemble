# Test Report: dispatch-lane stranding fix (commit 62e72b78)

Date: 2026-09-14
Branch: `feature/fix-question-resume-stuck` @ `62e72b78` (base `d521bf17`, v0.12.10)
Worktree: `/Users/nguyenminhkha/All/Code/opensource-projects/agents-ensemble-wt-question-resume-stuck`
Worker instances: W1 `dfeddb53` (infra) · W2 `7e79b630` (analysis) · W3 `bf96852b` (scenario) · W4 `640803cb` (seam pack) · W5a `8eb3fc6c` · W5b `c05b8782` · W5c `0b1cfdbb` · W5d `677ccdee` (broad census) · W6 `c99a58d6` (constitution) · W7 `520e2509` (A/B) · W8 `d0e1685f` (concurrency)
Mode: verification-only — zero commits, zero source modifications, worktree left clean at `62e72b78` (confirmed post-run).

## VERDICT: ✅ SHIP — incident closed, no regressions, dev claims CONFIRMED (one strengthened, one off-by-one)

### Summary
- **Incident acceptance bar: MET.** Scenario repro green; dispatch materializes durably; stranding class closed by two independent, tested mechanisms (Defect A guard + Defect B skip).
- **Fix's own tests:** 24/24 unit + 2/2 integration PASS at branch (W4, W3). **21 of 26 NEW tests FAIL at base** (dev claimed 16 — conservative undercount); **5 NEW pins PASS at base** (dev claimed 4 — the 5th is the integration live-graph pin). Exact-failure proof holds, strengthened.
- **Broad unit tree:** 11,055 passed / 57 failed / 23 errors / 57 skipped across 4 slices; **zero failures in any fix-commit file**; all measured failures map to documented pre-existing families.
- **A/B base comparison (temp worktree at d521bf17):** all 3 sampled pre-existing failure files **BYTE-IDENTICAL** base↔branch (18 node ids, same error classes, same lines) — including the highest-priority `daemon/routers/messages.py:249` mock-await cluster (7 tests): **pre-existing mock-rot, NOT branch-induced**.
- **ensure.md Core: 4/4 critical gates PASS** (scoped packs, concurrency pack, sync-DB-thread gate via pack, dev.sh static check). Release Gate: not warranted (scoped bugfix verification; no release).
- **Constitution drift pack: PASS** (24/24, branch guard matched). Flag-policy census: **zero new env flags/knobs** — owner always-on policy honored.
- Quick fixes applied: none (verification-only). Quarantined tests skipped by packs: archive_lifecycle family already quarantined (raw census runs still enumerate them; expected).

### Scope Decision
Leader-mandated verification of ONE commit (13 files: 7 daemon, 6 test) in a dedicated worktree. Scoped packs = the commit's own tests, the incident integration test, the unit tree census (explicitly required for the 84-claim), constitution + concurrency packs (ensure.md gates in blast radius: pause-cascade + messaging seams), and a base A/B. NOT run: tests/postgres (PG-only), tests/e2e (Release Gate — needs ./dev.sh daemon, not warranted), tests/integration beyond the incident file (only the fix's own integration file is in the change set). No PACKS.md changes (existing pack scripts used as-is; ad-hoc pytest scopes documented here).

---

### 1. Scenario repro — acceptance bar (W3)
`tests/integration/test_spawn_pause_resume_dispatch_durable.py` — `--override-ini="addopts="` (integration marker otherwise deselected): **2 passed in 4.82s**.
- Real InstanceManager over file-backed SQLite (tmp_path + NullPool + WAL + busy_timeout=10000 — project convention); real `pause_instance_cascade` / `resume_instance_cascade` / real agent-tool `send_message` closure; `claim_pending_task` against real TaskRepository. Mock surface justified and documented (`_check_team_membership`, engine injection, `_worker_pool = None` for deterministic claim-gate inspection).
- FIXED-behavior assertions all present and green: durable task rows (PENDING) for both children ✅, durable message_queue rows ✅, `_pending_injections` EMPTY (no stranding) ✅, durable task CLAIMABLE → graph-run start ✅.
- Fidelity divergences — adjudicated, not defects:
  - Children seeded IDLE, not RUNNING-but-graphless: **fix-shaped.** Defect B prevents the limbo from ever forming (ghosts skipped by pause-cascade → no bare PAUSED→RUNNING flip → no graphless-running state to strand against). The literal pre-fix sequence is behavior the fix removes. The RUNNING-without-live-graph dispatch state IS covered at unit level for all 3 lanes (see §2), and the opposite pole (live graph → in-memory injection preserved) is pinned in integration test 2 (L342).
  - `routed_via=enqueue_graphless_guard` marker not pinned in integration file — pinned at unit level instead (`test_graphless_downgrade_logs_observability_line`, test_instance_tools.py L5601).
  - No leader-tree-final-state (completion vs WAITING_CHILDREN-wedge) assertion — see Residual R1.

### 2. Seam coverage map (W2 analysis + W4 execution)
All 3 consumer seams + defect B + negatives covered; behavior map:

| Behavior | Coverage | Run |
|---|---|---|
| (a) Seam 1 agent-tool send → durable enqueue + marker | 4 tests (`TestRunningGraphlessDurableFallback` ×4, incl. observability-log pin) | PASS |
| (b) Seam 2 HTTP POST /messages → 200-enqueued | `TestHttpRouteGraphlessDurable::test_graphless_running_returns_200_enqueued` | PASS |
| (c) Seam 3 job_inject → enqueued + busy-guard | `TestJobInjectGraphlessDurable` ×2 | PASS |
| (d) Negative: live graph → in-memory injection preserved | 4 unit + 1 integration (`test_live_running_target_still_receives_injection`) | PASS |
| (e) Defect B: pause-cascade skips ghosts | 2 unit + 1 integration | PASS |
| (f) Negative: dispatched children still paused (no over-skip) | same tests assert `paused_ids` includes dispatched control | PASS |
| (g) Probe-failure → degrade-to-legacy pause-all | 2 unit | PASS |
| (h) Old-behavior pins | 4 unit pins + 1 integration pin | PASS |
| (i) Full-chain unit coverage | integration-only (by design); cross-lane equivalence not unit-pinned | see R2 |

Implementation sites (corrected): seam 1 `daemon/tools/instance.py:3052-3067`; seam 2 `daemon/routers/messages.py:442`; seam 3 `daemon/tools/job_queue.py:2316-2342`; shared verifier `daemon/manager.py:3694-3726` (`has_live_graph_task`); Defect B `daemon/services/instance_lifecycle.py:3134-3144` (probe+degrade), `:3190-3197` (skip); repo probe `daemon/repositories/instance/repository.py:658-712` (`filter_never_dispatched_ids`). Marker `routed_via=enqueue_graphless_guard` emitted only at seam 1 (seams 2/3 use their own wire shapes — 200 MessageResponse.queued / `{"status":"enqueued"}`).

**Fixture audit (item 4):** `tests/helpers/send_message_fixtures.py` sets `manager.has_live_graph_task = MagicMock(return_value=True)` as the shared default (+6 lines). Verdict: **PARTIAL mask by design — does NOT mask the fix.** Default-True pins all 12 consumer files' pre-existing tests to the live-graph/injection lane (regression protection); the fix's graphless tests explicitly override to False. A/B proved zero fixture-induced old-test breakage at base (all 5 modified-file failures confined to the 2 new classes). Residual hazard documented: flipping/removing the default-True would silently swap pre-existing suites to the durable branch — mitigated by the guard comment + `test_running_with_live_graph_still_injects`.

**Test-count reconciliation:** 26 NEW tests verifiable (10+8+6 in modified files + 2 integration). Dev's "27" = off-by-one (`test_stop_instance_subtree.py` +27 lines adds ZERO tests — fixture helper only). W4 collected 225 across the 4 files; all green.

### 3. Broad unit-tree census (W5a–d) — branch side
| Slice | Scope | Result | Runtime |
|---|---|---|---|
| W5a | tests/unit/services (86 files) | 8F / 1671P / 0E | 39.32s |
| W5b | 9 non-root subdirs (67 files) | 5F / 3326P / 0E | 54.32s |
| W5c | tests/unit root half-1 (138 files) | 30F / 2963P / 21E | 147.32s |
| W5d | tests/unit root half-2 (138 files) | 14F / 3095P / 2E | 100.42s |
| **Total** | tests/unit (429 files) | **57F / 11,055P / 23E / 57S** | ~6 min wall (parallel slices) |

- **Zero failures in fix-commit files** (`test_graphless_running_durable_dispatch.py` 10/10 ✅, `test_pause_never_dispatched_ghosts.py` 8/8 ✅, `test_instance_tools.py` 0 failures, `test_stop_instance_subtree.py` 0 failures).
- Failure families (all pre-existing, documented in critical notes / charter-reuse residuals): find_near ×13 (stale mocks, documented), builtin_mcp ×17 + context7 ×4 + webfetch ×2 setup errors (Mock AttributeErrors `slash_commands`/`blueprint`), job_queue_proxy_phase1 ×7, archive_lifecycle ×5 (QUARANTINE.md family), paused_auto_resume ×5 + vision ×1 (awaited-dispatch mock rot at messages.py:249), coder/devops agent drift ×7, job_processor_status_guard ×4, llm_allowed_models ×2 (coding2), wanderer ×2, b1_wc_durable_send ×1 (**env hazard**: hardcoded sibling-worktree path `agents-ensemble-wt-wc-wake-resilience` — fails on any machine lacking it), release-tag pin ×1 (v0.12.4 vs v0.12.10), api_router_extraction ×1, models_split ×1, phase4_manager_decomposition ×1, project_manager_agent ×2, terminal_reason_mirror ×1, validate_agent_id ×1.

### 4. Base-vs-branch A/B (W7 — temp detached worktree at d521bf17, removed after)
**Leg 1 — exact-failure proof (6 test files copied, test-code only, never daemon/):**
- **21 NEW tests FAIL at base** (dev claimed 16): 8 graphless (incl. 5× `has_live_graph_task` AttributeError, 202-vs-200, injected-vs-enqueued), 7 ghosts (incl. 4× `filter_never_dispatched_ids` AttributeError, ghosts-not-skipped, probe-log-missing), 5 in test_instance_tools.py new classes (guard-not-consulted, marker-log-missing, ordering), 1 integration (ghosts survive cascade).
- **5 NEW pins PASS at base** (dev claimed 4): the 4 unit live-graph/probe-fallback pins + integration `test_live_running_target_still_receives_injection`.
- **Fixture-induced old-test breakage: ABSENT** — all modified-file failures confined to the 2 new classes.
- Verdict: dev claim conservative; evidence STRONGER (21 distinct behavior-distinguishing tests, not 16).

**Leg 2 — pre-existing samples (base's own file versions):**
| File | Branch fails | Base fails | Verdict |
|---|---|---|---|
| test_job_queue_proxy_phase1.py | 7 | 7 (same node ids, same AssertionError shapes) | **BYTE-IDENTICAL** |
| test_archive_lifecycle.py | 5 | 5 (same node ids, 'Access denied' variants) | **BYTE-IDENTICAL** |
| test_paused_auto_resume_fallback.py + test_vision.py (messages.py:249 cluster) | 6 | 6 (same node ids, same TypeError at :249) | **BYTE-IDENTICAL — pre-existing mock-rot; NOT branch-induced** |

**84-claim reconciliation:** branch-side census measured 57F+23E=80 non-passing vs dev's 84. No new failure appeared in fix files; sampled families byte-identical. Delta attributed to: env variance (the wc-wake-resilience sibling-worktree hazard test — depends on machine state), known run-to-run flakies (conftest-documented TestWatchoverEvaluatorEvaluate / rag-test_config — adjudicate by failure-SET diff, not counts), and counting method (errors vs failures). Dev's 84 at base is plausible under their env; nothing in the delta implicates this branch.

### 5. ensure.md Validation Results (Core, blast-radius scoped)
- **Critical 4/4 PASS**: no regressions in changed packs ✅ (W4/W3 green); concurrency_atomic_unit_test ✅ (W8: 98P/0F/74 intentional skips, 7.14s); sync-DB-on-event-loop gate ✅ (thread-identity tests inside W8 pack); dev.sh `--timeout-graceful-shutdown 10` ✅ (grep, dev.sh:102).
- Important: deadlock scenario covered by W8 pack ✅. Await-conversion grep not re-run (no async-signature changes in diff — commit is additive guards; noted rather than validated).
- Nice-to-have (dead code): N/A — commit purely additive (+1553/−5).
- Release Gate: NOT RUN — not warranted (scoped bugfix verification in worktree; E2E requires live daemon). No contradictions with ensure.md methods found (all validations ran as packs/static checks).

### 6. Packs & gates
- `test/packs/constitution_drift_test.sh` with `EXPECTED_BRANCH=feature/fix-question-resume-stuck`: **RESULT: PASS**, 24/24 in 5.17s, zero drift, branch guard matched.
- `test/packs/concurrency_atomic_unit_test.sh`: **PASS** (98/0/74, 7.14s).
- Ad-hoc scoped pytest packs (documented above): all dual-layer (outer `timeout 300`, inner per-test 30s from pyproject ini; constitution pack 150s outer / 110s internal).

### 7. Residuals / risks
- **R1 (🟢 nice-to-have):** No leader-tree final-state pin (completion vs re-wedge) anywhere — integration test ends at claim-gate drain. Suggested follow-up: extend integration test to drive the pool (or stub-drain) and assert leader reaches terminal COMPLETED, not WAITING_CHILDREN wedge.
- **R2 (🟢):** Cross-lane equivalence (HTTP vs agent-tool vs job_inject guard firing identically) unit-tested per-lane only; full-chain is integration-level and exercises only the agent-tool lane end-to-end.
- **R3 (🟢):** `routed_via=enqueue_graphless_guard` marker pinned at unit level only (log-assertion); seams 2/3 have no equivalent marker — intentional (wire shapes differ), documented here for future forensics.
- **R4 (🟠 pre-existing, NOT this branch):** messages.py:249 awaited-dispatch mock-rot (6 tests), job_queue_proxy_phase1 ×7, and the other census families remain red at base AND branch — backlog, unaffected by this fix.
- **R5 (🟢 pre-existing env hazard):** test_b1_wc_durable_send hardcodes sibling-worktree path; fails on machines lacking it. Candidate for a path-resolution fix in the test (separate work).
- **R6 (🟢):** Dev off-by-one ("27 new tests" vs 26 verifiable) — cosmetic, matches councilor verdict.
- Fixture-default hazard (default-True `has_live_graph_task`) documented in §2 — guard comment + pin test mitigate; future editors warned.

### 8. Worktree state on completion
`git status --porcelain` empty; HEAD `62e72b78`; base temp worktree created and removed cleanly (`git worktree list` verified); no commits made anywhere (per mission constraints).

### Documentation Updated
- [x] RESULTS/2026-09-14-dispatch-lane-stranding-verification.md — this report
- [ ] PACKS.md — no changes (no new pack scripts; existing packs used)
- [ ] QUARANTINE.md — no changes (no new flaky candidates this session; archive family already quarantined)
- [ ] MOCK_TESTS.md — N/A (no mock services)

### Overall Status
- Scenario repro (acceptance bar): ✅ PASS
- Seam/defect-B/pin coverage: ✅ complete (a–h covered; i integration-only by design)
- Broad unit census: ✅ no fix-related failures; pre-existing families byte-identical on samples
- A/B exact-failure proof: ✅ STRONGER than claimed (21/5 vs 16/4)
- ensure.md Core: ✅ 4/4 · Constitution drift: ✅ · Flag policy: ✅
- **Testing Complete: ✅ READY — fix verified; branch cleared for merge workflow**
