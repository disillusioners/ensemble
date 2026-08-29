# Independent Verification Gate — #3 W1 Serialization Full Fix (`feature/injection-marker-serialization`)

- **Branch**: `feature/injection-marker-serialization` @ `51f5dc54` (range `22d03844..51f5dc54`, SINGLE commit — verified: parent of 51f5dc54 IS 22d03844; 9 files +968/−96 exactly as claimed: 4 daemon/ + 4 test files + 1 planning doc)
- **Date**: 2026-08-28
- **Dispatches**: 24 workers (2 recon + 1 statics + 8 committed packs + 7 full-tree sweeps + 1 new-tests bundle + 3 probes + 1 commit-verify + 1 base-evidence), 0 direct executions.
- **Rider commits made during gate (test-only, verified clean, disclosed)**: `1884d95c` (job_create stale assert → P2.3 B3.5 anti-forgery contract, +4/−4, 1 file) and `83a1a8b7` (_RealLangGraph teardown module-identity restore, +17/−5, 1 file). Branch tip at gate close: `83a1a8b7`.

## VERDICT: ✅ PASS — injection-marker-serialization gate CLOSED; CLEARED FOR MERGE to `latest`
**Zero new regressions attributable to this branch** across ~15k collected tests (8 committed packs + 7 sweep partitions, with intentional overlap). All 5 claimed behaviors verified statically AND behaviorally; leader's 7 verification scopes all closed with evidence.

---

## 1. Scope mapping (leader's 7 items)

| # | Scope item | Result | Evidence |
|---|---|---|---|
| 1 | Full regression vs baseline | ✅ 0 NEW failures | §4 packs (all baseline-exact), §5 sweeps (5/7 baseline-exact; 2 with composition shifts fully attributed via §6 base-evidence) |
| 2 | Round-trip depth (non-vacuous, real read path) | ✅ REAL | R2 audit: file-backed AsyncSqliteSaver, asserts on real `get_instance_messages` output, 4 marker profiles + absence-through-real-path; 3/3 PASS in 3 independent runs; independent probe re-drove the same real path 16/16 |
| 3 | D12 both sides (descendant excl. incl. superset; caller-own kept) | ✅ PINNED | unit 9/9 (desc-dropped, caller-kept, witness-kept, mid-text-kept); integration both sides; **superset (internal_agent:* FIFO frames + [SYSTEM NOTE: frames) was UNPINNED in shipped tests — my probe B/C pinned it**: dropped in descendant view, kept caller-side, through real serialize→filter→tool chain |
| 4 | Byte-shape back-compat (literal) | ✅ LITERAL PASS | worktree byte-compare HEAD vs base: 8/8 marker-less profiles byte-identical; 5/5 marker profiles additive-only (no base key changed); key-set equality (9 keys) identical |
| 5 | Report-drain source (conditional; absent→byte-identical; RETURNING additive) | ✅ | statics (graph.py:3101-3116 vs base :3078-3081) + behavioral: real SQLite repo — HEAD drained dict == BASE + `child_instance_id` only (verbatim dicts); real `create_agent_node` — with child_iid → exact `{'injected_message': True, 'source': 'internal_report:child-77'}`; without → byte-identical `{'injected_message': True}`; None-report defensive guard PASS |
| 6 | Behavioral probe (GET /messages keys; subtree no leakage) | ✅ 31/31 | probe A: real read path, keys visible/absent per profile incl. plain-message no-spurious-keys; probes B+C: descendant views exclude ALL injected frames (incl. superset), caller-own retained, unmarked-prefix witness KEPT |
| 7 | Mock fidelity on new tests | ✅ CLEAN | no StaticPool anywhere in new fixtures (file-backed throughout), 16/16 patch targets verified at HEAD, no assert-on-mock, hand-built D12 dicts verified key-faithful; 2 D12 drop-tests carry a marker+prefix fixture confound (documented; discrimination closed by witness test + my probe) |

## 2. Statics (P0) — every claim confirmed

- **(a)** serialize_message: `injected_message` guarded `is not None`, `source` guarded truthy — strictly additive (utils.py diff; `context_kind` block pre-existing/unchanged).
- **(b)** D12: `_SUBTREE_CONTEXT_INJECTION_PREFIX` (base instance.py:926) + prefix-match block (base :1025-1031) REMOVED; new criterion `m.get("injected_message") is True` (HEAD :1034) inside `is_descendant` branch (:1022); caller exempted (:986-993). Superset mechanics: FIFO injections stamped `injected_message=True` at drain (graph.py:2894-2897) — no prefix → leaked at base, dropped at HEAD; `[SYSTEM NOTE:` frames (graph.py:217 frame + :3102 stamp) — prefix ≠ `[SYSTEM CONTEXT:` → leaked at base, dropped at HEAD.
- **(c)** Report-drain stamp conditional on `report.get("child_instance_id")`; absent case kwargs byte-identical `{"injected_message": True}`; zero log-line changes.
- **(d)** Repository: RETURNING gains `child_instance_id` as 4th column; WHERE/VALUES byte-identical context lines; drained dict +1 key. ADDITIVE.
- **(e)** GET /messages passthrough: `routers/instances.py` NOT in diff — the additive keys arrive via serialize_message in the persistence chain (route :1489 raw-returns; chain scanned, no key strip). Precise framing of the claim: unchanged passthrough, not a router modification.
- serialize_message callers: 8 sites, ALL read/SSE output paths (persistence ×2, graph SSE ×2, instance_messaging SSE ×4) — **read-side-only CONFIRMED** (council claim independently reproduced).
- Out-of-scope scan: zero added production lines outside the stated change shape.
- ensure.md Core statics: dev.sh:102 `--timeout-graceful-shutdown 10` ✓; 8/8 async call sites of the 3 converted functions awaited ✓.

## 3. New-test quality audit (vacuity + mock fidelity)

- 19 new tests verified (3 integration + 8 serialize + 3 persistence + 2 new D12 + 2 renamed D12 + 2 same-shape). File-backed savers; zero unittest.mock in integration file; runs in default suite (no integration marker) ✓.
- Discrimination: reversion-A (serialize stops surfacing) caught by 11 tests; reversion-B (filter reverts to prefix) caught by exactly 1 (`test_unmarked_system_context_prefix_descendant_kept_after_W1` — the deliberate witness).
- **Gaps found (all closed by probes, none blocking)**: D12 superset unpinned (closed: probe B/C); exact key-set back-compat unpinned (closed: worktree byte-compare); write-side (report-drain stamp + RETURNING) had ZERO diff test coverage (closed: reportdrain probe both parts).

## 4. Committed packs — all baseline-exact

| Pack | Baseline @22d03844 | This gate | Δ |
|---|---|---|---|
| tools_suite | 991c/986P/0F/5-des | 993c/988P/0F/5-des (24.9s) | +2 = new D12 tests, 0F |
| api_unit | 213P/8S | 213P/8S (13.5s) | exact |
| concurrency_atomic (ensure.md Critical) | 98P/74S | 98P/74S (7.3s) | exact |
| instance_messaging_regression | 28/28 | 28/28 (0.8s) | exact |
| instance_messaging_queue_routing | 16/16 | 16/16 (1.5s) | exact (PACKS row was stale at 8/8) |
| job_queue_tools | 80c/76run/75P/1F | 76P/0F/4-des after fix 1884d95c | known job_create 1F fixed (test-only) |
| registry_validation | 140/140 | 140/140 (6.5s) | exact |
| child_reports | 15/15 | 15/15 (1.1s) | exact |

W1 acceptance (4-file single-process bundle): **215/215 PASS** after fix 83a1a8b7 (see §7).

## 5. Full-tree sweeps ×7 — 0 new regressions

- unit-ah 15F+4E = baseline exact (coder_agent 6, hide_kb 5, devops 3, context7 4E, api_router 1)
- unit-ir 44F = exact (buffer_response_header 36 [15/6/11/4], job_processor_status_guard 4, singles 4: models_split/phase4/phase5/question_deferred); loop_repairer PASSED in-partition (council flake did not recur)
- unit-sz 50F+2E = exact (watchover 47, validate_agent_id 1, wanderer 2, webfetch 2E); **new file test_serialize_message.py 44/44 PASS**
- top-ah 3F = exact (agents_api ×2, enqueue_shared ×1)
- top-ir 91F = count-equal, composition shifted: 17 buffer + 67 MigrationError + 1 job_create (pre-fix) + **6 unmapped → ALL base-evidenced PRE-EXISTING (§6)**; 19 previously-known failures recovered (proxy_phase1 ×8, job_continue ×4, 8 stale singles — job_continue now 3rd green run: un-quarantine eligible)
- top-sz 12F = exact; **spawn_team_members 44/44 HOLDS** (4th recurrence pattern did NOT materialize)
- subdirs 47F+108E+1 collection-error → 27 known-family + **26 unmapped → 20 PRE-EXISTING-IDENTICAL at base + 1 flaky + 5 env-artifacts (§6)**; W1 acceptance 12/12 PASS in-sweep; D12 class is 9 tests (4 new, not 2 — recon undercount)

## 6. Base-evidence worktree A/B (@22d03844) — the 26 unmapped, resolved

**20 PRE-EXISTING-IDENTICAL** (identical signatures both sides): injection_cleanup ×1 + injection_slot ×3 (`_ManagerStub._deferred_watchover_terminate` AttributeError — the known tool-pairing follow-up family), innate_skills ×1, llm_load_balance_meta ×1, complete_cancel_route ×4, nuclear_cleanup_bucket5 ×6, chokepoint_callers ×2, turn_state_machine ×1 (base has an EXTRA failure mode HEAD no longer hits — branch reduced failure surface), vscode_security ×1. **1 FLAKY**: atomic_dequeue (passes 4/4 isolated both commits). **5 ENV-ARTIFACT**: opencode/test_client passes 48/48 in isolation at both commits — its sweep failures were shared-process pollution (§7). **New regressions: 0.** QUARANTINE.md family row added so future gates classify these on sight.

## 7. Gate findings & fixes (test-only riders on the branch)

1. **`_RealLangGraph` shared-process pollution (REAL, caught by the single-process 4-file bundle)** — the branch's new integration file evicted conftest langgraph mocks but its `__exit__` DELETED `daemon.persistence/graph/manager/compaction` from sys.modules → later files' patches bound to fresh module identities → 2 failures in test_persistence.py combined runs (standalone 23/23 pass; isolated pair reproduces; 3-file control clean — full triage matrix in worker report). **Fix 83a1a8b7**: snapshot in `__enter__`, restore identities in `__exit__` (+17/−5, tests/integration/test_persistence_w1_markers.py only). Post-fix: 4-file bundle 215/215; **discrimination re-run: the 108 httpx setup errors + 5 body failures from the pre-fix sweep → 0 errors at HEAD** (7-file shared-process run, httpx signature ABSENT; only residual = 2 pre-existing `search=` kwarg fixture-drift failures in test_instance_ui_prefs_api.py — same class as the hide_kb family, in files the branch never touched).
2. **job_create stale assertion** (known family since P2.3): `source == 'manual'` inverted by anti-forgery derivation (job_queue.py:518-549 unconditional `agent:<caller>`). **Fix 1884d95c**: rename + invert + docstring (+4/−4). Post-fix 76P/0F, re-verified post-commit.
3. Both commits independently verified: single test file each, ≤20 net lines, zero daemon/ changes, topology 51f5dc54 → 1884d95c → 83a1a8b7 intact, author tester@ensemble.local, working tree otherwise untouched.

## 8. ensure.md status

- **Core Critical 4/4**: scoped packs all PASS; concurrency_atomic PASS; sync-DB-on-loop covered (pack PASS); dev.sh flag grep PASS.
- **Core Important 2/2**: async-await 8/8 sites awaited; deadlock scenario covered by concurrency pack.
- **Release Gate**: full non-integration suite effectively executed via packs + sweeps (every failure attributed; 0 new). E2E daemon/LLM items ruled NOT TRIGGERED: read-side serialization change; no job/task/queue semantics touched; session precedent (prior 4 gates in this feature set). No contradictions found in ensure.md (all requirements pack-mapped cleanly).

## 9. Follow-ups (non-blocking, routed)

1. [Owner: dev/giter] D12 coverage hardening (~10 lines): one descendant-drop test with `injected_message=True` + NON-prefixed content (kills the marker+prefix fixture confound), + exact key-set equality assert for plain messages. My probes pin both behaviors today; tests should too.
2. [Owner: dev] `test_instance_ui_prefs_api.py` `_ManagerStandin` lacks `search=` kwarg (2 failures, pre-existing — surfaced from under the pollution).
3. [Quarantine hygiene] job_continue ×4: 3rd consecutive green run recorded — eligible for un-quarantine (remove 4 deselects from job_queue_tools_unit_test.sh per protocol).
4. [Env] httpx/opencode shared-process sensitivity is now twice-observed (pre-fix pollution collateral) — the restore-identities rule is captured in LESSONS + a worker skill.

## 10. Worker instances

139453d7 (diff) · 0300c0af (test audit) · 436c0b9f (statics) · b06fb873 tools · 3c400b56 api · 7a5f2dfe conc · 920b3b62 msg-reg · b71e186a msg-route · 87f6feaf jqt (+fix) · 27d44fc5 registry · e7c67f8f child-reports · 2c31a371/f5b056b4/dbc78569/1b095a15/b175956a/5ef6aab1/dab6255b sweeps ×7 · e86cb2c0 newtests (+fix 83a1a8b7) · 9916f9d4 backcompat · f3eec674 behavioral · 85cc5a24 reportdrain · c747452e commit-verify · dce25e81 base-evidence · 88f36f2a post-fix recheck.
