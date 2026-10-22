# Phase D1 — mcps-gateway-interop FINAL MERGE GATE

- **Date:** 2026-10-22 (18:27Z → 20:0xZ)
- **Verdict: PASS-WITH-NOTES** — mergeable to `latest`
- **Branch under test:** `feature/mcps-gateway-interop` @ `96bd8f695` + 3 authorized test-only commits (`0b26f515d`, `6e2f0d5c8`, `ac1d00f06` — all this gate's quick fixes; branch is 20 commits, base `9632badd4` confirmed ancestor; local/unpushed — push is the developer's call)
- **Gate role:** Tester / Test Leader — all execution delegated to worker instances; this file is the aggregation artifact.
- **Deferred FULL-suite regression mandate: DISCHARGED.**

---

## 1. Verdict summary

| Gate criterion | Result |
|---|---|
| 1. Full-suite regression, ZERO new failures vs base | ✅ **ZERO NEW** — every red classified; base-leg A/B executed on all 13 unmatched nodes (11 token-identical, 2 mechanism-proven pre-existing); 0 branch-caused |
| 2. Interop gates re-run (P0 + P1/P2 + P3) | ✅ **PASS 38/38** (70.2 s; real Go client × real gateway over TLS, fence DB) |
| 3. FE web automation (TrueAuto UI validation) | ✅ **PASS** — A 14/14, B 11/11, C 16/16, teardown clean |
| 4. Mock-vs-real honesty | ✅ decoder MATCH (D3a load-bearing); 8 mock-strength gaps + 2 stale docs flagged (no shipped defect) |
| 5. Mission-level acceptance | ✅ 9/11 PROVEN with executed-run evidence; 2 PROVEN-with-disclosed-scope-limit (criteria 2, 9) |

**Notes (non-blocking):** pre-existing base-carried production/test debt newly surfaced by the first-ever `-m integration` gate leg (§12); 3 mock-strength gaps worth fixing post-merge (§9); 2 stale evidence/plan docs (§9); FE degraded-path reachability nuances (§8 findings 1–2).

---

## 2. Scope decision

Full suite **was warranted** (mandated merge gate; cross-stack diff: BE gateway repo/routers/schemas/services + FE page + conformance kit + docs — 47 files). Executed as bounded packs (≤5 min, dual-layer timeout), **mirroring the Phase C gate partition** (`.agents/tester/RESULTS/2026-10-22-mcps-gateway-fe-phaseC-merge-gate.md`) plus the two NEW legs this branch creates (`tests/conformance` kit; `tests/integration -m integration` — never gate-run before, now demanded explicitly).

**Excluded by design (documented, not silent):** `tests/packs/*` (g7 collection blocker — **proven BASE-IDENTICAL** at `9632badd4` via `git show` diff; root-level `pytest tests` aborts rc=3 `INTERNALERROR SystemExit: 0` at `tests/packs/g7_unique_index_smoke_test.py:121` — pre-existing, zero branch reach); `tests/e2e/*` (LLM-calling release-gate items, unchanged by diff, Phase C precedent); `tests/mock_*.py` ×7 top-level (NEVER-RUNNABLE by inception design); `postgres`/`slow`-marked (default addopts deselect). Zero `mock_*.py` under `tests/unit`.

**Addopts drift handled (discovery W3):** `pyproject.toml` now excludes `not conformance` and the whole `tests/integration/mcps_gateway` family is integration-marked (collects 0 under default addopts) — both got dedicated legs so nothing the branch ships is invisible to the gate.

---

## 3. Full-suite regression — census reconciliation

Census (`--collect-only`, fence env `POSTGRES_HOST=127.0.0.1 POSTGRES_DB=ensemble_dev_test_only ENSEMBLE_SELF_ENV=dev DAEMON_PORT=8079`, `-u DATABASE_URL`) vs executed:

| Leg | Census | Executed | Δ | Note |
|---|---|---|---|---|
| unit U1 (routers+services) | 3,747 | 3,747 | 0 | exact |
| unit B1–B5 (rest, 5 buckets) | 14,455 | 2,953+32solo / 2,842 / 2,841 / 2,854 / 2,933 = 14,455 | 0 | exact; B1 solo = session-aborter file |
| **tests/unit total** | **18,202** | **18,202** | **0** | ✅ |
| integration default (attest-excl) | 878 | 879 | +1 | xdist worker re-collection artifact (class known from Phase C); deselection provably active |
| attestation (5 shards) | 229 | 20+25+93+40+51 = 229 | 0 | exact |
| job_queue | 2,233 | 2,233 | 0 | exact |
| top-level halves (mock_* excluded) | ~5,725 | 2,576 + 3,154 = 5,730 | +5 | branch/collection drift vs Phase C baseline; mock_* (56) excluded per design |
| serial no_xdist leg | 2 | 2 | 0 | exact |
| **Default-gate-semantics total** | **≈27,267** | **≈27,271** | — | Phase C ran 27,238; delta = branch test additions |
| **integration `-m integration` (NEW leg)** | 407 (excl. mcps_gateway) | 169 + 242 = 411 | +4 | xdist re-collection class; mcps_gateway family (38) ran separately |
| conformance kit (NEW leg) | 41 | 41 | 0 | self-host mode |
| FE jest full | 125 suites | 4,166 tests | +9 vs Phase C | branch spec additions |

**Totals executed this gate: ≈31,800 backend nodes + 4,166 FE tests.**

## 4. Per-pack results

| # | Pack | Counts (P/F/E/S) | Wall | RESULT |
|---|---|---|---|---|
| MG | `tests/integration/mcps_gateway -m integration` (interop family) | **38/0/0/0** | 70.2 s | ✅ PASS |
| U1 | unit routers+services | 3,739/8/0/0 | 69 s | ✅ PASS (8 ledger) |
| B1 | unit bucket 1 | 2,896/4/4/49 (+32-node solo leg: 28P, 1 aborter node INTERNALERROR — ledger) | 67 s + 3 s | ✅ PASS (classified) |
| B2 | unit bucket 2 | 2,778/33/17/14 | 111 s | ✅ PASS (classified; **docs pins 55/55 green**) |
| B3 | unit bucket 3 | 2,801/11/0/29 (post-fix) | 41 s | ✅ PASS (classified; 5 quick-fixed) |
| B4 | unit bucket 4 | 2,843/11/0/0 | 19 s | ✅ PASS (classified) |
| B5 | unit bucket 5 | 2,917/6/0/10 (post-fix) | 39 s | ✅ PASS (classified) |
| I1 | integration default (attest-excl) | 853/25/0/1 | 74 s | ✅ PASS (classified) |
| A1–A5 | attestation shards | 20/25/93/40/51 = **227P + 2S(by-design)** | 27+34+52+20+10 s | ✅ PASS |
| I2a | integration `-m integration` half A | 146/21/0/2 | 35 s | ✅ PASS (classified; never-gated leg) |
| I2b | integration `-m integration` half B | 214/9/0(18E)/1 | 35 s | ✅ PASS (classified; never-gated leg) |
| PG-RR | PG_TEST_* re-run (2 files, leg-private scratch DB) | **27/0/0/0** | 6.6 s | ✅ PASS (converts I2b's 17 env-errors to green) |
| J1 | job_queue | 2,181/14/0/38 (post-fix) | 80 s | ✅ PASS (classified) |
| T1 | top-level half 1 | 2,411/68/0/97 | 70 s | ✅ PASS (classified) |
| T2 | top-level half 2 | 3,049/54/5/41(+5XF) | 97 s | ✅ PASS (classified) |
| S1 | serial no_xdist | 2/0/0/0 | 1.4 s | ✅ PASS |
| K1 | conformance kit (`CONFORMANCE_RUN_TIMEOUT=280` under `timeout 300`) | 41/0/0/0 | 12.4 s | ✅ PASS |
| F1 | FE jest full | 4,162/4/0/0 | 20 s | ✅ PASS (4 = quarantined jobs-model ledger) |
| FE-WEB | live browser vs real daemon (§8) | 41/41 scenario assertions | ~35 min session | ✅ PASS |
| BASE-AB | base-leg triage @ `9632badd4` worktree | 13 nodes serial | — | ✅ 0 branch-caused |

## 5. Failure classification — every red, ZERO new

**Class keys:** Q = quarantined/ledger · B = base-attributed (executed base leg or byte-identity) · E = environmental · X = flaky (retry-budgeted) · ENV = env-contract (proven green under proper env).

Ledger-matched families (counts as observed): U1 8 (proxy_phase1 ×7 row 67, scheduling once-anchor ×1 row 36) · B1 8 (terminal_reason mirror-set, audit_sink kwarg-pin, api.py size+route ×2, context7 ×4E) · B2 50 (builtin_mcp 17E, configure_builtin 15, governor vision 10, coder_developer 5, llm_failover 1, caller_model 1, bearer_p5 env 1) · B3 11 (planner subtree ×2, knowledge caller-model ×2, snapshot gate drift ×1, maintenancer memory.md ×3, project_manager prompts ×2, release-tag pin ×1 — all production-side) · B4 11 (prompt md-token/fence ×5, repository_claim_lane ×6) · B5 5 (inner_soul instance-identity ×3, snapshot 37→38 ×1, sync_runner dup-pin ×1) · I1 25 (armed_absence/context_freshness/m2_missions/mission_final/explorer B×5, complete_cancel ×4, memory.md ×6, lcan_legacy, lcau_incident, pause_race_w7, vscode_security, spawn_intelligence_tier E×5, lcancheck-S3 solo-readjudicated=host socket artifact) · J1 14 (consolidated 14-node family) · T1 61 + T2 59 of the 122 top-level reds match ledger/B-families (progressive_dispatch 18 SQLite-migration, spawn_team vision 21, memory_integration 16, governor 16, spawn_limit 9, memory_system 7, test_api 6, instance_metadata 5, charter 5, job_queue_tools 4, instance_title 2, phase8_docs/prompt_version_resolution/resource_monitor/scheduler_adapter 4, postgres-grant teardown errors 5E→scratch-DB leak cleaned).

**Unmatched reds → base-leg A/B (executed, serial, `/tmp/d1-base-9632` worktree @ `9632badd4`, worktree removed after):**

| Node | HEAD signature | Base verdict |
|---|---|---|
| test_agents_api ×2 (list_agents_success / empty_directory) | reads real `agents/` dir, 39==1 | **BASE-IDENTICAL** (same 39) |
| test_models::test_error_codes_values | 27==18 enum pin | **BASE-IDENTICAL** (token) |
| test_enqueue_shared::test_triggers_title_on_idle_to_running | title bridge fires ×2 (deterministic 3/3) — product-suspect | **BASE-IDENTICAL (×3/3)** → pre-existing production debt |
| test_skill_evolution_config::test_defaults | repo-root `.env` EMBEDDING_BASE_URL leak | **BASE-IDENTICAL** (env-contamination class) |
| test_skill_evolution_config::…defaults_via_config | ab_sample_size 20 vs pin 10 (D15) | **BASE-IDENTICAL**; `git log -S` proves `e8d3fa211` predates base |
| test_agent_bootstrap_with_instance_manager | MigrationError SQLite `near "CONSTRAINT"` (20260714) | **BASE-IDENTICAL** (token) |
| test_compaction_e2e::test_compaction_and_graph_continuation | langgraph `Checkpoint` ImportError | **BASE-IDENTICAL** (token) |
| test_cold_resume_ttl::test_resume_db_sync_transitions… | 'pending'=='cancelled' | **BASE-IDENTICAL** (token) |
| test_maintenance_checkpoint_cleanup_api::test_overlap_two_manuals_409 | 202==409 | **DIFFERS-on-signature** — base flaky (2 hangs + TypeError on `vacuum_full` kwarg) on code the branch never touches (empty diff-stat both sides); not branch-code-attributable |
| test_message_queue_e2e::test_sse_events_count | sqlite3 `near CONSTRAINT` | **DIFFERS-on-signature** — base hit langgraph ImportError first, but sibling N7 in the same base session hit the IDENTICAL migration error → mechanism proven pre-existing |
| test_skill_injection_persistence::test_feedback_fails_without_persistence | `record_feedback` on-miss now creates usage record | **BASE-IDENTICAL** (token) |
| test_plugin_registry::test_default_plugins_root_is_repo_plugins_dir | checkout-name regex `^ensemble-src…` | **BASE-IDENTICAL mechanism** (fails under any non-ensemble-src dir name, base included) — environmental |
| test_dependency_bus::test_per_parent_lock_serializes_db_insert | session.refresh InvalidRequestError | flaky solo (1P/2F over 3 serial runs) → **QUARANTINED** (retry budget spent, no base leg needed) |

**Bottom line: 0 branch-caused failures across ≈31,800 backend + 4,166 FE nodes.** All branch-touched suites green on first run: interop family 38/38, `test_mcps_gateway_read` 32, `test_mcps_gateway_audit_repository` 12, `test_mcps_gateway_audit_projection` 10, `test_mcps_gateway_docs` 55/55, `test_mcps_gateway_config_and_tools`/`tool_filter`/`transport` 77/77, FE mcps-gateway specs 3 suites / 62 its.

## 6. Quick fixes applied (3 commits, test-code only, local/unpushed)

| Commit | Pack | What |
|---|---|---|
| `0b26f515d` | J1 | `test_round2_council_fixes.py:938` `get_event_loop().run_until_complete` → `asyncio.run` (loop-independence for the stale-task wedge hook node; ledger-recommended isolation; node green solo + in-pack after) |
| `6e2f0d5c8` | B3 | gaia `tools.allow` += `"service"` ×3; devops `skill_injection` pin → False (+node renamed `_disabled`); devops forbidden-set += `"chart"` — RED→GREEN alignment with shipped agent config (backing prod commits `3e46576dc`/`e56070de6`/`f4d3be5db`, all ancestors of base); count-neutral |
| `ac1d00f06` | B5 | `test_promote_staleness` frozen-NOW time-bomb anchored to real clock; `test_sentinels` walk-exclusion += gitignored `.inspiration-projects` |

Side-effect hygiene: `install-audit.jsonl` kms self-emit restored to HEAD sha by every touching worker (verified); no `git add -A` anywhere; scratch DBs dropped (see §14).

## 7. The deliverable's own proof (gate item 2)

- **Interop family re-run: PASS 38/38** in 70.23 s (one pytest process, fence env, `--override-ini='addopts=' -m integration`, outer `timeout 300`). Per-file: fake_client_selftest 15, protocol 19, **P0 gate 1 (12.84 s)**, **real_pair P1/P2/P3 3 (7.98/13.56/9.31 s)**. Slowest node 13.56 s ≪ per-test cap. 28 warnings = third-party websockets deprecations.
- **Harness side effect (by design):** `p0_gate.py` appended a 91-line 17th attempt block (PASS @ 18:43:11Z) to the tracked `.agents/shared/planning/mcps-gateway-interop/p0-gate-evidence.md`. Committed as evidence (see §15). Note: the file's header still says "9 green" while the log now holds 12 PASS blocks (stale header, benign direction).
- **Conformance kit: PASS 41/41** (self-host mode, 12.42 s, kit-internal 280 s bound under outer 300 s). Pointed/external-center mode NOT run (needs a second real center — disclosed scope limit, docs §6).
- Teardown verified: no orphan go/client/harness processes, no 80xx/41xx listeners, `mcps_client` repo unmodified, 9797 untouched.

## 8. FE web automation (gate item 3) — live daemon + live FE + Playwright

Setup: real daemon on **8079** (uvicorn-direct, `POSTGRES_DB=ensemble_dev_test_only`, `MCPS_GATEWAY_ENABLED=1`, `MCPS_GATEWAY_TOKENS` runtime-minted via `daemon.auth.cli issue-token`, `/availability state=ready` in 8 s) + real FE `npm start` on **4199** (proxy /api→8079) + Playwright Chromium. 7 audit rows seeded through `McpGatewayAuditRepository.record` with the real `hash_args` path, `machine_id=feverify-*`. The page has no token input — credentials ride the ambient OAuth lane; bearer injected at browser-context level (same credential as the curl parity legs).

| Scenario | Result | Key evidence |
|---|---|---|
| A healthy | **14/14** | 3 headings (machines/sessions/audit), tables laid out, audit 14 rows incl. all 7 `feverify-*`, zero pageerrors, browser body ≡ curl body on all three endpoints, `/api/mcps-gateway/ws` → 400 (route registered). Screenshot `scenario-a-healthy.png` |
| B degraded | **11/11** | Daemon-side: **HTTP 200 `{"degraded":true,"error":"audit_unavailable",...}` — NOT 503**; browser: degraded banner visible, **previous 14 rows still rendered (W8)**, machines/sessions lanes unaffected; recovery → `200 degraded=false count=14`. Artifacts under `/tmp/d1-fe-web/artifacts/` |
| C 403/401 honesty | **16/16** | no-token → 401 flat `AUTH_REQUIRED` labelled AUTH_REQUIRED (NOT SCOPE_INSUFFICIENT); wrong-scope → 403 nested `detail.error=SCOPE_INSUFFICIENT` labelled SCOPE_INSUFFICIENT **matching the body code**; garbage → 401. Raw bodies + screenshots correlated |
| D teardown | **PASS** | 8079/4199 free, 8088 never bound, **9797 still owned by original prod PID 11311**, no owned orphans, seeded rows + minted bearers deleted from fence DB |

Scenario B deviation (documented): a dead DB is a **boot refusal** (finding F1 below), so the REAL outage used the doc's own hung-DB shape — `ACCESS EXCLUSIVE` lock on `mcp_gateway_audit` (fence DB only) + `MCPS_GATEWAY_READ_TIMEOUT_MS=1500`, with auth ON and a live bearer (stronger than the briefed fallback).

**Findings (report-only, no code changed):**
1. **[daemon] Cold DB outage = boot failure, not a degraded read.** `api.py` lifespan hard-depends on the DB; the §8.5 degraded contract is only reachable from a mid-flight read failure (hung/slow DB). Repro: `POSTGRES_PORT=5433 uvicorn daemon.api:app` → startup exits.
2. **[daemon] Degraded read unreachable through the credential lane on a dead DB** — auth middleware collapses resolution failures to 401 before the route runs (combine with F1: degraded observable only with hung DB + valid bearer, or auth off).
3. **[FE] W8 holds across a degraded *refresh*, not a hard *reload*** — the client-side tail is per-page-load state; the shipped doc wording ("KEEPS the last page on screen") is session-scoped, so read as consistent-with-spec; flagged for explicit wording.
4. [harness] ng serve binds `[::1]:4199` only (127.0.0.1 probe never answers); context-level bearer leaks into cross-origin font requests (CORS console noise).

## 9. Mock-vs-real honesty (gate item 4)

**Decoder: MATCH.** D3a rawCode-first confirmed load-bearing (`service.ts:436` before `:456`; the CSRF 403 spec at `:378-395` fails under the old code). Phase-C vacuity removed; both real shapes (401 flat / 403 nested) exercised; degraded envelope mocked exactly (7-key) ×2; W8 pinned at service AND component spec layers. Rows field-exact 8/10/9; no phantom fields; no `id`/`detail` leak.

**Would-stay-green-if-wire-changed (8, mock-strength follow-ups):** (1) 8 healthy mocks omit envelope `degraded`+`error` keys (B3b-decided, server-side-guarded — if BE stopped emitting `degraded`, prod never banners, suite stays green); (2) `count`=page-length never load-bearing FE-side (`??` fallback + no `count≠len` mock; pager Next-button would silently die); (3) 401 message assertion vacuous (coincides with hardcoded fallback); (4) 503 message ditto; (5) `status===401` disjunct unpinned; (6) 401↔403 nesting swap undetectable by design (never cite the FE suite as nesting proof — server-side pins are the guard); (7) degraded `error` vocabulary unconsumed FE-side; (8) bare-array switch → FE shows not_json banner, suite green (server-side key-set pins are the only guard).

**Stale docs (2):** `plan.md:423/:566` W8 wording ("rows replaced by empty list + banner") contradicts the landed rows-survive behavior (superseded by `p4-evidence.md:96-100` + `plan.md:232`); `p3-evidence.md:218-220` pins wrapper key-sets as 3/3/5 while the P4 emitter sends 5/5/7 (the FE mocks aligned to the stale pin). Cross-language BE↔FE-model parity remains hand-maintained (known gap).

## 10. Mission-level acceptance (gate item 5)

| # | Criterion | Verdict | Artifact |
|---|---|---|---|
| 1 | Real×real SPEC v0.1 flow (TLS, stub child) | **PROVEN** | `p0-gate-evidence.md` (executed logs) + today's re-run: P0 gate green in the 38/38 |
| 2 | Typed errors, 15-code vocabulary | **PROVEN (scoped)** | ~7–8/15 codes wire-proven with the real client (P2 ledger #7/8/12/14/16/26); full 15 traversal only in the kit (`tests/conformance/test_errors.py` ALL_ERROR_CODES pin). Phase-disclosed at `COVERAGE.md:62`. Headline should read "vocabulary-complete in kit; subset real-client-proven" |
| 3 | MACHINE_BUSY exclusivity §8.3 | **PROVEN** | P2 #14 ×2 + `test_real_pair.py:1455-1480` (exactly ONE session.begin frame; refusal before the machine) |
| 4 | Heartbeat death-detect | **PROVEN** | P2 §5 seven parts, kill band [1500,3000] ms hit 4×; `test_real_pair.py:1567+` |
| 5 | Audit rows in REAL DB, args hashed §8.4 | **PROVEN** | P0 §9 (`engine-is-postgres`, fence-DB canary, args-hash exact) + P3 #17/#18 (shasum second implementation); `test_real_pair.py:407,479,1755-1803,2219-2423` |
| 6 | Read-endpoint parity | **PROVEN** | P3 #23 (18 entries, live daemon, 401-flat/403-nested controls, count==page-length) |
| 7 | Degraded signal + W8 | **PROVEN** | Unit pins (`test_mcps_gateway_read.py:468-533`, repo-raises `:336`) + FE specs + **today's live browser proof §8-B** |
| 8a/8b/8c | Riders AD7 / AD8 / AD5 | **PROVEN** | `service.ts:422-464` + load-bearing specs; `repository.py:102-104` + tie-order/paging tests; repository-raises docstring + degraded-body tests |
| 9 | Conformance kit reusable | **PROVEN (scoped)** | `scripts/conformance/` + 6 SPEC §10 areas verified against read-only `mcps_client/SPEC.md:862-882`; self-host 41 green today; pointed-mode needs a second real center (disclosed) |
| 10 | Docs updated | **PROVEN** | `docs/mcps-gateway.md` §1.7/§2.2/§5/§6/§8.4/§8.5/§10/§11 + 55-node pin suite green today |

Item-11 open list (from p4/p5, carried verbatim into §12): inverted DETAIL string `p0_gate.py:635`; `read_client_audit_log` skipped-count surfacing; TestClient-portal executor finding; doubled `app = create_app()` in `api.py` (F-D2); TLS-terminator owner+date (user decision, §11); kit pointed-mode observability boundary (§6.2); HEARTBEAT_TIMEOUT `id`-less vs SPEC §6.1 tension; §8.4 args-hash algorithm unnamed in SPEC + two audit heads not joinable; cross-center proof deferred.

## 11. ensure.md validation (scoped to blast radius; release-gate warranted and run as packs)

- **Critical 1** (no regressions in changed packs): **PASS** — every branch-touched pack green (§5 bottom line).
- **Critical 2/3** (concurrency/deadlock integrity + no sync DB on loop): **PASS** — covered inside the exact unit partition (concurrency families green in U1/B*; zero concurrency-family reds anywhere); plus explicit `concurrency_atomic_unit_test` re-run → see addendum.
- **Critical 4** (`dev.sh` `--timeout-graceful-shutdown 10`): static check → see addendum.
- **Important** (await-correctness grep; parent→child→complete no-block): covered by green unit/integration partition (no new reds in those families).
- No `pytest -x` used anywhere; every validation ran as a pack under `timeout 300`; quarantine honored (4 FE jobs-model + ledger rows did not fail any requirement).

## 12. New defects / follow-ups surfaced (all PRE-EXISTING at base — none block this merge)

**Production debt (base-carried, base-leg-proven):**
1. Title-generation bridge double-fires on idle→running (`test_enqueue_shared.py:498`, deterministic ×3 at base AND HEAD).
2. SQLite-incompatible migration `20260714_000001` (`ADD CONSTRAINT` — PG-only) breaks every SQLite-booting test (progressive_dispatch 18 + message_queue_e2e + agent_bootstrap families).
3. `skill_metrics_service.record_feedback` creates usage record on-miss (contract drift vs 2 tests); A/B loser resolved-but-not-deactivated.
4. Cold-resume PAUSED→CANCELLED transition drift (`'pending'=='cancelled'` ×2).
5. Maintenance checkpoint-cleanup contention semantics (202-vs-409; flaky at base with `vacuum_full` kwarg TypeError).
6. `daemon/api.py` doubled module-level `app = create_app()` (F-D2, carried).
7. api.py size pin (4839 > 1600) + release-tag pin `v0.12.4` vs pyproject `0.27.1` (the latter pinned by 6 KB doc anchors — re-verify those, don't just bump).
8. Degraded-path reachability nuances (§8 findings 1–2) — doc-worthy, possibly contract-worthy.
9. FE W8 refresh-vs-reload wording (§8 finding 3) + stale `plan.md` W8 / `p3-evidence.md:218-220` key-set pin.

**Test debt:** the 8 mock-strength gaps (§9); `tests/conftest.py` langgraph-mock eviction contract (`Checkpoint` split-brain, order-dependent at collection scale); `PG_TEST_*` env contract unset in gate legs (leg-private scratch-DB pattern proven — document it); repo-root `.env` leaks `EMBEDDING_BASE_URL` into tests; plaintext embedding key sitting in repo-root `.env` (pre-existing, matches this repo's leaked-key history — flag to owner); `env -u POSTGRES_*` does NOT glob (per-field fence vars are the reliable form — all fence-held runs overrode `POSTGRES_DB` explicitly and the fence canaries passed).

**Tooling traps recorded:** pytest 9 `--ignore` does NOT exclude explicitly-passed files (B1 nearly ran the session-aborter inside the bucket); xdist re-collection deltas (+1/+4/+5 nodes) are a known artifact class — reconcile via collect-only census, not run summaries.

## 13. Quarantine status

New Active rows (this gate): `test_dependency_bus::…serializes_db_insert` (flaky 1P/2F solo, DB-state ordering); `plugin_registry::test_default_plugins_root_is_repo_plugins_dir` (environmental host-layout, fails on any non-`ensemble-src` checkout name — sibling of the 2026-09-06 family). Resolved (this gate): gaia tools.allow ×3 + devops meta ×2 via `6e2f0d5c8`; round2 wedge-hook node via `0b26f515d`; promote_staleness time-bomb + sentinels vocabulary via `ac1d00f06`. Consolidated D1-gate addendum row added (never-gated integration-marked leg families, base-proven). Active count net ≈ unchanged; several long-standing ledger gaps closed rather than new debt added.

## 14. Hygiene / teardown (global end-state)

- Ports: 8079 free · 4199 free · 8088 never bound · **9797 owned by original prod PID 11311 throughout** (never touched). Base worktree `/tmp/d1-base-9632` removed and confirmed gone. No owned orphan processes (uvicorn/node/playwright/chromium); the only `@playwright/mcp` chrome server predates the gate by a day.
- DB: fence DB `ensemble_dev_test_only` used exclusively for writes; seeded `feverify-*` rows + minted bearers deleted; leg-private scratch DBs (`ensemble_d1_pgrr_test`, secrets_v1 self-managed) dropped with lifecycle proof; `ensemble_promptver_test_*` teardown-leak DBs → see addendum. Remote prod PG never contacted.
- `mcps_client` repo: unmodified (verified `git status`; only pre-existing untracked artifacts from before the gate).
- `install-audit.jsonl`: restored to HEAD sha after kms self-emit (multiple workers, sha-verified).
- Worktree end-state: only known foreign `.agents/` dirt (approver/active.md, tidier/notes.md, untracked planning/research + RESULTS files) — untouched, never staged; branch tip `ac1d00f06`, ahead of origin by 3 (the 3 gate quick-fix commits; base commits pushed by developer).

## 15. Code changes summary (this gate)

- `0b26f515d` — test(job-queue): stale-task wedge hook loop-independence (1 file, 1 line)
- `6e2f0d5c8` — test: align gaia/devops meta expectations with shipped agent config (2 files, +20/−16)
- `ac1d00f06` — test: defuse plugin-sentinel/staleness env time-bombs (2 files, +9/−1)
- This report + QUARANTINE.md + PACKS.md + LESSONS + the P0 evidence append — committed by the tester lane (explicit paths only).

---

### Overall Status
- Full-suite regression: ✅ **ZERO new failures vs base** (executed A/B proof, not construction-only)
- Interop gates: ✅ PASS 38/38 · Conformance: ✅ PASS 41/41 · FE unit: ✅ PASS (quarantine-aware)
- FE web automation: ✅ PASS (all scenarios + clean teardown)
- Mock-vs-real: ✅ no shipped defect (8 strength gaps + 2 stale docs flagged)
- Mission acceptance: ✅ 9 PROVEN + 2 PROVEN-scoped
- **Verdict: PASS-WITH-NOTES — READY TO MERGE** (notes = §12 follow-ups; none block)

*Generated by the tester agent (Test Leader) — all execution performed by worker instances; evidence logs under /tmp (b1/b2/b4 runs, pack_toplevel_half1, ens-integration-half-b, fe-jest-full, d1-fe-web/, mg-style base-leg table above). Snapshot captured post-write.*


---

## 16. Addendum — final hygiene/ensure sweep (executed post-report)

- **ensure.md Critical 4 (dev.sh flag): PASS** — `--timeout-graceful-shutdown 10` present at `dev.sh:155` (uvicorn invocation; comment at :152). Cite the flag, not line numbers (they drift).
- **ensure.md Critical 2/3 (concurrency pack): PASS (quarantine-aware)** — `concurrency_atomic_unit_test` re-run via its registered script under `timeout 300`: `5F / 94P / 74S` in 13.6 s. All 5 reds = `test_instance_metadata_atomic.py` DetachedInstanceError ×5 — the exact family ledger-carried at `QUARANTINE.md:134` with EXECUTED base-leg A/B at `639b7129b` (Phase C gate), re-observed ledger-matched in this gate's T1 shard; branch diff empty across the exercised seams (3 independent attribution proofs). ⚠️ The PACKS.md baseline "99P/0F" is stale against current multi-dev drift (99 non-skipped holds; only the P/F split moved) — baseline re-pin owed.
- **Global end-state: CLEAN** — 8079 free · 4199 free · 8088 never bound · **9797 owned by prod PID 11311 throughout** · no owned orphans (only the day-old pre-existing `@playwright/mcp` server).
- **Scratch-DB residue: 71 `ensemble_promptver_test_*` databases** (accumulated teardown-leak from failing postgres-grant teardowns across sessions) — **dropped one-by-one, 0 failures, 0 remaining**; the 4 protected DBs (`ensemble_dev`, `ensemble_dev_test_only`, `ensemble_prod`, `ensemble_test`) verified intact.
- **install-audit.jsonl:** 346 lines before AND after (self-emit did not fire this sweep).
- Git end-state: HEAD `ac1d00f06`; the mid-run `.agents/tester/*` writes the sweep observed were this gate's own Test-Leader doc writes (RESULTS/QUARANTINE/PACKS/LESSONS), not a foreign session.
