# Phase C — mcps-gateway FE management: MERGE-GATE VERIFICATION

- **Date:** 2026-10-22
- **Verdict: GREEN** (mergeable to `latest`)
- **Branch/HEAD under test:** `feature/mcps-gateway-fe` @ `ada2eda95` (base `639b7129b`; 26-file diff — caller stated ~25)
- **Worktree:** `/Users/nguyenminhkha/All/Code/opensource-projects/agents-ensemble-wt-mcps-gateway-fe`
- **Gate role:** Tester / Test Leader — all execution delegated to worker instances; this file is the aggregation artifact.

---

## 1. Verdict summary

| Gate criterion | Result |
|---|---|
| Phase C-touched suites green (endpoint 27, audit-repo 9, scope-manifest 7, class-manifest 5, docs 44, T6.2 8) | ✅ **100/100** |
| FE touched specs green (70 cases across 4 specs) | ✅ **70/70** (FE full suite 4153/4157, 4 = quarantined jobs-model) |
| `fe_static_typecheck_build_test.sh` | ✅ **PASS** (exit 0; branch check + build; see pack defect D1) |
| Full-suite failures = only classified pre-existing/environmental | ✅ **YES** — every red classified; **base-leg A/B executed** (39/40 base-identical, 1 xdist artifact) |
| Zero diff-overlap regressions | ✅ **ZERO** (2 diff-overlapping reds proven pre-existing: `api.py` was 4818 lines at base vs 1600 pin) |
| Worktree ends clean (only known foreign files) | ✅ after cleanup (see §9) |

**Nothing NEW. No merge blockers.** Caveats (§10) are follow-ups, not gates.

---

## 2. Scope decision

Full suite **was warranted** (release/merge gate, cross-stack diff: BE routers/services/repo + FE page + docs + tests). Executed as bounded packs (≤5 min, dual-layer timeout) covering **27,238 default-gate-semantics tests**:

- `tests/unit` 18,173 + `tests/integration` default-collected 1,107 (incl. attestation 229) + `tests/job_queue` 2,233 + top-level `tests/*.py` 5,725 = **27,238** — coverage reconciled exactly across shards.
- Extra adjacent: `tests/integration/mcps_gateway` `-m integration` 34 (Phase-B family) + serial `no_xdist` leg (2 nodes).
- **Excluded by design (documented, not silent):** `tests/mock_*.py` 56 nodes (blueprint: NEVER-RUNNABLE from inception, zero diff overlap); `postgres`-marked 363 (default addopts deselect; never run against the shared dev PG); `slow`-marked; `tests/packs/*` + `tests/e2e/*` (F5 collection blockers: import-time `sys.exit(0)` and live-daemon `requests.get` — pre-existing, provenance pre-dates base).

---

## 3. Per-pack results

| # | Pack | Command shape | Counts | Duration | RESULT |
|---|---|---|---|---|---|
| 1 | Touched BE suites | `timeout 300 uv run python -m pytest <6 files> -v` | **100/100 passed**, 0F 0S (27/9/7/44/8/5 exact) | 4.55 s | ✅ PASS |
| 2 | Static pack | `EXPECTED_BRANCH=feature/mcps-gateway-fe timeout 300 bash test/packs/fe_static_typecheck_build_test.sh` | branch ✓, tsc (vacuous — D1), `ng build` ✓ 13.9 s, page chunk emitted | 15 s | ✅ PASS |
| 3 | FE jest full | `timeout 300 npx jest --ci --silent` (125 specs) | 125 suites (123P/2F); **4,157 tests: 4,153P / 4F / 0S** | 14.1 s | ✅ PASS-WITH-PREEXISTING |
| 4 | BE unit shard 1 (routers+services) | `timeout 300 … tests/unit/routers tests/unit/services -n 4` | 3,742: **3,734P / 8F / 0E / 0S** | 77.1 s | ✅ PASS-WITH-CLASSIFIED |
| 5 | BE unit shard 2 (unit-rest) | 5 bounded buckets (`timeout 300`, `-n 5`) | 14,431: **14,237P / 70F / 21E / 102S** + 1 session-abort node | 642 s total (max bucket 204 s) | ✅ PASS-WITH-CLASSIFIED |
| 6 | BE integration (corrected) | `timeout 300 … tests/integration --ignore-glob='*test_attestation*' -n 4 -v` | 878: **853P / 25F / 0E / 1S** | 155.8 s | ✅ PASS-WITH-CLASSIFIED |
| 6b | mcps-gateway integration family | `tests/integration/mcps_gateway --override-ini='addopts=' -m integration` | **34/34 passed** | 34.3 s | ✅ PASS |
| 7 | BE attestation (5 shards) | `--ignore-glob`-free `tests/integration/test_attestation*`, `-n 4 --override-ini='timeout=120'` | 229 covered: **227P / 2S (by design, branch-gated) / 0F** | 269.9 s wall | ✅ PASS |
| 8 | BE job_queue | `timeout 300 … tests/job_queue -n 4` | 2,233: **2,180P / 15F / 0E / 38S** | 152.2 s | ✅ PASS-WITH-CLASSIFIED |
| 9 | BE top-level `tests/*.py` (195 files) | 2 bounded halves, `-n 4` | 5,725: **5,456P / 126F / 1E / 138S / 5XF** | 203 s | ✅ PASS-WITH-CLASSIFIED |
| 10 | Serial `no_xdist` leg | `-m "no_xdist and not integration and not postgres and not slow"`, serial | 2 selected: **2P** / 1 benign skip | 53.3 s | ✅ PASS |
| 11 | **Base-leg A/B** (40 nodes) | detached worktrees @ `639b7129b` + HEAD control @ `ada2eda95`, serial, identical env/flags | **39/40 BASE-IDENTICAL; 0 regressions; 1 DIFFERS = xdist artifact** | 7 batches, all ≤23 s | ✅ ATTRIBUTION PROVEN |
| 12 | Live-DOM smoke | built SPA + mock BE on :10080/:10081, Chromium via Playwright | 3 sections rendered + visible; gateway-off banner parsed off wire; ports freed | bounded | ✅ PASS |

**Reconciliation:** every shard's collected count equals its `--collect-only` census; attestation shards sum 22+66+51+57+33 = 229; unit-rest 14,237+70+21+102+1 = 14,431; top-level halves 2,618+3,107 = 5,725.

---

## 4. Failure classification — every red

**Class keys:** Q = quarantined (ledger row) · B = base-attributed (base-leg A/B executed) · E = environmental · D = diff-overlap-but-pre-existing · X = flaky under xdist (3× serial pass).

| Shard | Red | Class | Evidence |
|---|---|---|---|
| FE jest | `jobs-grouping.model.spec.ts` ×3 (timeAgo time-bomb), `jobs-filter-state.model.spec.ts` ×1 (JOB_STATUS_VALUES 9-vs-8 pin) | Q | Ledger rows 2026-10-08/10; byte-identical vs `2026-10-10-snapshots-v2-merge-gate.md` A/B (files not in diff) |
| unit 1 | `test_job_queue_proxy_phase1` ×7 | Q | QUARANTINE.md row "job_queue_proxy derived-status map gap (7 nodes)"; 3× identical |
| unit 1 | `test_scheduling_service::test_user_setting_rung_covers_once_anchor_site` | Q | Ledger "scheduling once-anchor TIME-BOMB ×1" (hardcoded past `when=2026-10-05`) |
| unit 2 | 87 nodes across builtin_mcp×17E+context7×4E, configure_builtin×15, governor vision×10, … | Q | node-count-exact family matches vs QUARANTINE.md Active rows |
| unit 2 | `test_api_router_extraction` ×2 (`/api/schedules` method; `api.py` size 4839>1600) | **D** | `api.py` = **4,818 lines at base** (Phase-C +21 lines, 2 hunks: import + include_router); both ledger-carried; BASE-IDENTICAL (base leg #38/#39) |
| unit 2 | `plugin_subsystem/test_plugin_registry::test_default_plugins_root_is_repo_plugins_dir` | E | checkout-name regex (`ensemble-src(-wt-…)`) fails in both main checkout and feature worktree |
| unit 2 | `test_llm_failover_v2_resilience::…degraded_select_when_facade_exhausts` | **B** | stale mock (`agent_id` kwarg, commit 660e73453 — ancestor of base); BASE-IDENTICAL (base leg #37) |
| unit 2 | `tools/test_upgrade_executor_systemd_service::…swallowed_exception…` | **B** | SESSION-ABORTER (`Path.exists` global patch → INTERNALERROR rc=3); file unchanged since 248e1e46e; BASE-IDENTICAL (base leg #40) |
| job_queue | 14-node consolidated family (job_answer order-pin, in_progress_guard×2, terminal_write_census×2, event_driven_completion×3, jober_watch×2, n8×1, watcher_concurrent×2, round2×1) | Q | exact 14-node match, ledger line 38 |
| job_queue | `test_instance_lifecycle_events::test_terminate_instance_publishes_lifecycle_event` | **B** | `audit_sink` kwarg-pin (5260bfa37 predates base); 3 files blob-identical base↔HEAD; BASE-IDENTICAL (base leg #36) |
| job_queue | `test_defer_self_witness_watchdog` gw1 worker crash | X | 3×P serial (16.8 s each) |
| integration | 14 nodes (maintenancer memory.md ×6, complete_cancel ×4, lcan_legacy, lcau_incident, pause_race_w7, vscode_security) | Q | row-matched, verbatim signatures |
| integration | `test_spawn_intelligence_tier` ×5 | E | `VisionModelNotAllowedError: 'vision' missing from config.llm.allowed_models` — host env gate; deterministic |
| integration | `test_armed_absence_alist`, `test_explorer_shared_context_paths`, `test_context_freshness`, `test_m2_missions_runtime_contract`, `test_mission_final_vocab_runtime` | **B** | 3×F serial each; BASE-IDENTICAL (base leg #30–34) |
| integration | `test_lcancheck_family_separation::TestS3WholeTreeCensus` | X (contention) | TIMEOUT@30s under `-n 4`; **PASSES 15–19 s solo at base AND at HEAD** (A/B control) |
| integration | `test_vscode_lifecycle::test_auto_restart_exhaustion_marks_crashed` | X | 3×P solo |
| top-level | 91 nodes across test_api×6, progressive_dispatch×18, spawn_limit×9, memory_integration×16, governor×16, spawn_team vision×17, … | Q | signature+count matches; ledger rows cited per family |
| top-level | 29 nodes across spawn_team_members×4, instance_metadata_atomic×5, instance_title×2, job_queue_tools×4, memory_system×7, charter_render×3, phase8_docs, prompt_version_resolution, resource_monitor, scheduler_adapter | **B** | 3×F serial each; **BASE-IDENTICAL (base leg #1–29)** |
| top-level | `test_main_entry` ×3 | E | `POSTGRES_DB` fence (`ensemble_dev_test_only` missing); **27/27 PASS under `env -u POSTGRES_*`** |
| top-level | `test_charter_render_capture` BSD-date ×2 | E | GNU-only `touch -d`, macOS `/private/var` |
| top-level | drill_smoke; prompt_version_content_mutations[reject-postgres] | X | PASS serially |
| attestation | — | — | 0 failures |

**Base-leg A/B (executed evidence):** detached worktree @ `639b7129b` + HEAD control @ `ada2eda95`, serial, identical `-m` marker expression and fence env (`ENSEMBLE_SELF_ENV=dev POSTGRES_DB=ensemble_dev_test_only DAEMON_PORT=8079`). Per-node status diff **39/39 SAME**; reason diffs identical except `api.py` line count (4818→4839) and one random `context_key` UUID. Full table: `/tmp/mg_base_ab_verdicts.md`.

---

## 5. Mock-vs-real audit (TrueAuto careful-mock rule)

**Mock drift: NONE.** Field-by-field parity verified FE `mcps-gateway.model.ts` ↔ `daemon/routers/mcps_gateway_schemas.py` (all 6 model pairs, no phantom fields; widened unions match BE `str` and the real presence vocabulary). BE endpoint harness is a **real** `McpGatewayService` (real stores, pinned clock); repo tests pin the real `_audit_entry` projection; `set(row) == set(model_fields)` closes the real↔wire link.

Edge cases verified real: pagination bounds (limit 0/−1/201→422 via `Query(ge/le)`, offset past end→200+empty+echoed offset, page walk), gateway-off 200+empty+`gateway_enabled:false` (machines/sessions — see F4 for audit lane), 503 unwired with nested `detail.error`, 403 SCOPE_INSUFFICIENT nested shape asserted BE + both shapes tolerated FE, no-write negatives (all requests GET-asserted + `httpMock.verify()`, button set exactly `['refresh-button','audit-prev','audit-next']`, source-grep for write verbs), 3-sections render proof (`h2` headings exactly `['Machines','Sessions','Audit']` + tables + row assertions).

**Important findings (test-strength, NOT behavioral defects — follow-ups):**
1. FE error-shape tests are **vacuous** — `decodeError` short-circuits on status 403/503, so nested-vs-flat parsing is never load-bearing (`service.ts:340,348-352`; specs `:311-364`).
2. No automated BE-schema ↔ FE-model parity link (cross-language); plus `tsconfig.app.json` excludes specs from the build lane.
3. Gateway-off contract asserted only on a hand-wired app; the real lifespan wiring (`api.py:2401`, `manager.py:1453-1456`) is never exercised.
4. The **audit lane's** gateway-off shape unasserted both sides.

Nice-to-have: 6 more (comment mis-attribution, `last_activity_at` unasserted, fake-sink `id` omission, call-shape-specific write grep, hardcoded heartbeat constant, 4/5 decode arms untested).

---

## 6. Smoke result

**PASS — live DOM, no daemon boot, no DB.** Built SPA (`frontend/dist`) + stdlib mock BE served on `127.0.0.1:10080/:10081`; Chromium (Playwright 1.60.0) loaded `/mcps-gateway`: `h2Count:3`, headings `["Machines","Sessions","Audit"]` (ids `machines/sessions/audit-heading`), all visible with laid-out tables (7/9/8 columns + empty-state rows), **zero pageerrors**. The gateway-off banner rendered with the exact documented copy — proving `gateway_enabled:false` was parsed off the wire (a heading alone cannot prove that). Screenshot `/tmp/mcps-gateway-smoke/mcps-gateway-page.png`. Ports 10080/10081 verified freed; only PID 64955 started/killed.

---

## 7. Pack defects & gate-hygiene findings (fix candidates)

| # | Finding | Impact | Suggested fix |
|---|---|---|---|
| D1 | `fe_static_typecheck_build_test.sh` Stage-1 `npx tsc --noEmit` is **vacuous** — solution-style `tsconfig.json` (`files: []`) ⇒ 0 files checked, exit 0 unconditionally | A broken branch passes the typecheck phase; real coverage came only from `ng build` AOT | `npx tsc -p tsconfig.app.json --noEmit` |
| D2 | `--ignore-glob='test_attestation*'` is a **no-op** in pytest 9 (needs leading `*`) — caused the integration shard's 3 wall-kills (scope 1107 vs 878) | Shard timeouts; mis-scoped attestation leakage | Use `--ignore-glob='*test_attestation*'`; add scope-census pre-check |
| D3 | `test_upgrade_executor_systemd_service` patches `pathlib.Path.exists` globally ⇒ **INTERNALERROR rc=3 kills whole pytest sessions**, silently dropping unrun items | Any sweep including it under-reports | Isolate/deselect in packs (quarantined); fix the patch target |
| D4 | `tests/mock_*.py` are pytest-**collected** (56 nodes) despite the NEVER-RUNNABLE disposition; not in QUARANTINE.md | Silent-scope risk | Add ledger row |
| D5 | TS1128 at `app.component.spec.ts:2127` is invisible to jest (`isolatedModules` ⇒ ts-jest transpile-only) and to the static pack (D1) — **no current lane surfaces it** | Parse defects in specs can ship | D1 fix + optional TS compiler-API check |
| D6 | KMS/configure_builtin tests append `kms_issue` rows to the **tracked** `.agents/shared/planning/designer-agent/install-audit.jsonl` on every `tests/unit/services` run | Dirty tracked file at every gate | Redirect audit sink to `tmp_path` in fixtures |
| D7 | Fence KV says `POSTGRES_DB=ensemble_dev`; repo's own strict fence requires `ensemble_dev_test_only`/`ensemble_test_only` (11 files `pytest.exit` otherwise) | Shard aborts (rc=3) unless env reconciled | Correct the fence KV; or amend fence files |

---

## 8. Quarantine status

- Active quarantined count after this gate: **prior ledger + 10 consolidated addendum rows** (see `.agents/tester/QUARANTINE.md` "2026-10-22 Phase-C merge gate addendum").
- Newly ledgered (were reddening gates without rows): 29 top-level + 5 integration deterministic reds, job_queue `audit_sink`, unit `llm_failover` stale mock, `upgrade_executor` session-aborter, 5 env-gated vision nodes, 3 `test_main_entry` env nodes, 2 BSD-date nodes, 2 xdist-flakes, lcancheck S3 (xdist-correlated).
- Quarantined tests do not fail pack results; a rising quarantine count remains a quality signal (this gate closed several ledger gaps rather than adding new debt).

## 9. Worktree end-state

- HEAD `ada2eda95` + this docs commit (RESULTS + QUARANTINE.md only — explicit paths).
- `git status --porcelain`: only the two known foreign modifications — ` M .agents/tidier/notes.md` (tidier artifact pinned to older HEAD `e5ebf8502`; must NOT ride a merge via `git add -A`) and ` M .agents/shared/planning/designer-agent/install-audit.jsonl` (test self-emit; restored to HEAD content at gate end).
- No stray listeners: 10080/10081/8099/15432 free; 8079/8088 free; 9797 (prod ensemble) untouched.

## 10. Caveats / follow-ups for the merge

1. **`api.py` size pin**: `test_api_module_is_small` fails at base and HEAD (4818→4839 lines). The `api.py census prose` commit recognized this; a re-baseline (or router extraction) is owed — pre-existing, not Phase-C's to block on.
2. **`.agents/tidier/notes.md`** is tracked+modified (caller expected untracked). Excluded from the gate commit; the tidier lane should land or drop it — a blind `git add -A` would fold it into the merge.
3. **Mock-audit findings 1–4** (test-strength) — cheap to fix pre- or post-merge at leader's discretion; none affect shipped behavior.
4. **Pack defects D1–D7** — recommended as immediate follow-ups; D1+D2+D3+D5 directly weaken future gates.
5. **TS1128** (`app.component.spec.ts:2127`, base `7b6095e47`) remains unsurfaced by any lane (D5); harmless to this gate, latent for future spec edits.
6. **Multi-dev drift**: 29 top-level + 5 integration deterministic reds are base-carried debt from other developers' commits — now ledgered, not fixed here (out of scope for this gate).

---

*Generated by the tester agent (Test Leader) — all execution performed by worker instances; evidence logs under `/tmp/mg_*`. Snapshot ref: base-leg verdicts `/tmp/mg_base_ab_verdicts.md`.*
