# Maintenance Console — FINAL PRE-MERGE VERIFICATION (BE+FE complete)

Date: 2026-09-27 (05:00–06:00 UTC window)
Branch: `feature/maintenance-console` @ **`c939aaa0`** (task brief said `d66af240`; actual HEAD is 2 commits past it — `d66af240` (FE e2e spec) + `c939aaa0` (A-8 verbatim surface) are BOTH included; superset verified. Base for delta: `666c089d`. 17 post-phase-1 commits, pure linear chain, verified by forensics.)
Commissioner: FINAL merge-blocking pass (FE functional + browser e2e + consolidated whole-tree regression + BE PG suites + smoke + spot-probes).
Source mods: **ZERO**. Test adds: **ZERO** (no genuine coverage gaps found — all 3 spot-probe behaviors already covered by meaningful, mangle-matrix-verified pins). Commits by this commission: **NONE**.

## Overall Verdict — ✅ READY TO MERGE (testing complete)

| # | Deliverable | Verdict |
|---|---|---|
| 1 | Independent Playwright re-run (deferred item 1) | ✅ **PASS 14/14, 0 skipped, 0 flaky, PW_EXIT=0, 41.4s** (attempt 1 invalid — invocation protocol, see §1) |
| 2 | Full FE suite: Jest full + tsc + build/budget (deferred item 2) | ✅ Jest 3469 tests = 3388P baseline-class + 77 maintenance NEW-all-green + exactly the 4 QUARANTINE.md pre-existing; tsc exit 0; build exit 0; **initial 5.89 MB < 6 MB cap — claim CONFIRMED** |
| 3 | BE PG suites @ final HEAD (deferred item 4) | ✅ guard family **122/122**; PG API suite **39P+1S** (collected 40, designed skip verified) — dev claim CONFIRMED exactly; real-saver A/B **7P/2F ≡ base, same node ids** — CONFIRMED pre-existing |
| 4 | BE smoke 12/12 (deferred item 5) | ✅ **12/12 PASS, exit 0**, `-u POSTGRES_URL` form re-proven, zero `ensemble_prod` contact, port 8199 + DB cleanup verified |
| 5 | **Authoritative whole-tree regression @ final HEAD (deferred item 3 — THE critical deliverable)** | ✅ **ZERO branch-caused new failures** — 203/204 phase-1 ledger nodes reproduced identically; 1 ledger node = documented load-flake (P/P/P solo today); census node = ENVIRONMENTAL (live code-server socket, phase-1-proven class, re-proven F/F/F with exact signature); 56 mock-harness ERROR nodes = **enumeration artifact, proven BASE-IDENTICAL 56≡56** (md5-identical files at base/HEAD, never branch-touched); all branch-changed/added test files GREEN |
| 6 | FE functional spot-probes (mock-level) | ✅ 77/77 scoped specs PASS + all 3 behaviors COVERED with non-tautological pins (stale-dry-run no-dialog+snackbar / 409-adoption silent resume / interrupted-card affordance) |
| — | ensure.md (scoped) | ✅ Core critical: changed-packs-green PASS; dev.sh static PASS; concurrency mapped (§8); Release Gate NOT warranted (§8) |

---

## 1. Independent Playwright re-run (14-case gate) — PASS

Worker `61e9dfec`, 2 attempts (re-dispatch budget 1, used with cause).

**Attempt 1 → INVALID (not a product verdict), 3 root causes adjudicated:**
- Invocation omitted the config-documented file filter (`playwright.maintenance.config.ts:20-21` prescribes positional `maintenance-checkpoint-cleanup` filter) → 141 tests ran instead of 14 (123 out-of-gate specs hardcode dev ports 4199/8079 → ERR_CONNECTION_REFUSED noise).
- Spec `beforeAll` guard (`spec:35`) requires `ENSEMBLE_DB_DSN` in the WORKER env (webServer env does NOT propagate to test workers; spec module-load scrubs `POSTGRES_*` at `:65`) → dispatcher-side scrub tripped the refusal guard. Any prior 14/14 attestation must have exported the DSN token.
- Genuine infra defect discovered: **deterministic teardown leak** — Playwright SIGTERMs webServer#1 without the boot script's cleanup path running; `postgres -D /tmp/pg_e2e_maint_<pid>` survives on :15432 (reproduced 2/2 attempts; 8 dead `/tmp/pg_e2e_maint_*` clusters accumulated from earlier runs corroborate). Worker cleaned its own leftovers both times (port-verified).

**Attempt 2 (corrected protocol) → PASS:**
- Env: `ENSEMBLE_DB_DSN=postgresql://ensemble@127.0.0.1:15432/ensemble_e2e_maint_disposable` (satisfies `:35` guard + `:41-43` prod-refusal; disposable-stack port, non-prod name).
- Command: `cd frontend && timeout 300 npx playwright test -c playwright.maintenance.config.ts maintenance-checkpoint-cleanup --project maintenance --reporter=line` → **`14 passed (41.4s)`, PW_EXIT=0**, wall 42.0s.
- Config-fact transcript proof: boot log `Initializing PostgreSQL cluster at /tmp/pg_e2e_maint_88610 on port 15432` → `ensemble_e2e_maint_88610` → daemon pid 88653 → canary `Canary response: state='ready'` → `daemon is READY on :8099`; ng serve `Local: http://localhost:4299/`; `reuseExistingServer:false` fresh-boot proven by NEW pid-suffixed cluster (88610 ≠ attempt-1's 85654) with all ports pre-free; per-test 90s / actionTimeout 20s never approached.
- 14/14 all passed, **zero skips** (the `:230 test.skip(!ready)` gate never fired — stack healthy). Per-case list (titles at spec lines 234–556): availability-driven gear-menu show/hide ×3, dual-flavor render ×2, skipped-render badge map, dry-run counts/fresh_until/duration, Cancel-no-POST, confirm-POST-polls-terminal, stale-dry-run-recheck-no-POST, 409-adoption-silent-resume, interrupted-card, cross-origin 403, kill-switch 503 banner.
- Invocation detail: `--project` is variadic — positional filter must precede it (one instant CLI misfire, no boot, no tests, corrected).

## 2. Full FE suite — PASS

| Pack | Command | Exit | Result |
|---|---|---|---|
| Jest FULL | `CI=true timeout 300 npm test -- --watch=false` (frontend/) | 1 (expected: 4 quarantined F) | 102 suites (100P/2F); **3469 tests: 3465P + 4F + 0S**; 9.6s. The 4F = EXACTLY the QUARANTINE.md 2026-09-26 set (jobs-filter-state enum-drift ×1, jobs-grouping timeAgo ×3) — same files/lines/names/shapes. Maintenance specs (4 files, 77 its) ALL PASS. Count arithmetic exact: 3392 baseline + 77 maintenance. |
| Static+build pack | `EXPECTED_BRANCH=feature/maintenance-console timeout 300 bash test/packs/fe_static_typecheck_build_test.sh` | 0 | Stage-1 `tsc --noEmit` exit 0; Stage-2 `ng build` exit 0. **Initial bundle 5.89 MB raw / 1.27 MB transfer < 6 MB `maximumError` cap — 6MB claim CONFIRMED** (+0.01 MB vs prior baseline = noise). SCSS/budget warnings: same 7 pre-existing classes (1MB warning-tier + 6 component files + lighten() deprecation ×2 emissions); **NEW: 2 NG8113 cleanliness warnings** (maintenance-side): `maintenance.component.ts:30` orphan `CheckpointCleanupComponent` import; `vscode-editor-cache.component.ts:57` unused imports — compiler-dead-code class, NOT budget, non-blocking (follow-up F2). |
| Scoped spot-probe specs | `CI=true timeout 300 npx jest <4 maintenance spec files> --watch=false` | 0 | 77/77 PASS in 1.2s. Coverage adjudication: (a) TTL-expired dry-run → component L653 asserts `mockDialog.openCalls` length 0 + snack `/stale/i`; service `isDryRunStale()` past/future/null trio — **COVERED**; (b) 409 adoption → service `adoptRunIdFromError()` 4 cases + component 409-throw test asserting `pollRunCalls[0].runId` adopted + `snackBar.openCalls===0`; pins-spec 3-pin block incl. negative no-toast-in-block regex — **COVERED**; (c) interrupted card → `canRerunInterrupted` predicate boundary + template `.ck-interrupted-card` pin (wiring verified at component.ts:327 / html:237-238) — **COVERED**. Pins load PRODUCTION source via readFileSync (not self-reading) — mangle-matrix-safe. |

## 3. BE suites @ final HEAD — PASS

| Pack | Result | Claim adjudication |
|---|---|---|
| Guard family (5 files, SQLite) | **122/122 PASS in 4.4s** (destructive_override 11 + cleanup_service 65 + run_lock_and_capture 15 + wiring_pin 7 + anti_join 24) | Dev claim "guard 65" = **cleanup_service file alone** (its collected count) — real referent, sloppy label; authoritative family count 122. Phase-1 (4 files) was 69/69; family grew +53 with 0 failures. |
| PG API suite (`tests/integration/test_maintenance_checkpoint_cleanup_api.py`, disposable PG :15532, `PG_TEST_*`, `--override-ini="addopts=" -m "integration and postgres"`) | **39 passed + 1 skipped (collected 40) in 9.6s, exit 0**; skip = `test_sqlite_fallback_insert_where_not_exists` in-test PG-conditional (designed, AM-5); teardown clean (cluster removed, port free) | Dev claim "39P+1S" **CONFIRMED exactly** (static arithmetic 31 defs − 2 parametrized + 11 rows = 40). "Contract pack 33" = stale phase-1-era count of the same suite (33+1 @3fb798d1 → 37+1 post-G1 → 40 now). |
| Real-saver A/B (`checkpoint_prune_real_saver.py`, PG :15534 HEAD / :15535 base-worktree) | HEAD **7P/2F** ≡ BASE **7P/2F**, identical node ids (`test_real_saver_write_retention_prune_blob_prune_resume`, `test_real_saver_dry_run_report_line_shape`) — the QUARANTINE.md caplog pair | "2 base-identical pre-existing" **CONFIRMED** — re-proven at final HEAD; 2-char test-side fix still deferred (out of scope). Scratch worktrees removed; main checkout porcelain unchanged. |
| Smoke (`tests/manual/maintenance_console_smoke.sh`, port 8199, local PG 5432 disposable `ensemble_mc_smoke_*`) | **12/12 PASS, exit 0, ~3 min**: availability/status 200, evil-origin 403, availability origin-exempt, localhost 200, dry-run 200 (run_id minted), confirm gate 400, runs 404, runs-by-id 200, boot-sweep heal (phantom→interrupted, 0 in_flight on re-boot), kill-switch 4×503, availability `kill_switched`. Cleanup: port free, 0 leftover DBs, `ensemble_prod` grep = 0 | Dev claim "12/12 on -u POSTGRES_URL form" **CONFIRMED** (fix `691fb557` scrub re-proven live). |

## 4. Whole-tree regression @ c939aaa0 — THE DELTA VERDICT: **ZERO branch-caused new failures**

9 shards (phase-1 architecture; per-shard `timeout 300`, env-scrubbed, default addopts, `uv run python -m pytest` only; every shard silent-wedge-checked: exit-1 + populated summary = genuine inventory, never trusted empty):

| Shard | Collected | F | E | S | Wall | vs phase-1 | Candidate-NEW (normalized) |
|---|---:|---:|---:|---:|---|---|---|
| S1 unit/{tools,sources,checkpoint_adapter,config,graph,models,job_state,job_queue,persistence,repositories,rag} | 3536 | 7 | 0 | 5 | 73s | identical 7-node set | 0 |
| S2 unit/{services,routers} | 2478 (+26) | 13 | 0 | 0 | 71s | identical 13-node set; maintenance family 104/104 green | 0 |
| S5c {integration,api,opencode,performance} − 34 attestation files (`--ignore-glob`, exclusion proven: collect Δ = −229 family nodes, 0 family failures) | 1540 | 14 | 0 | 1 | 246s | identical 14-node set; `test_vscode_proxy.py` 70/70 green | 0 |
| S6 {job_queue,message_queue_redesign,repositories,services,tools} | 3686 | 29 (−1) | 0 | 65 | 133s | 29/30 baseline nodes (the −1 = phase-1 load-flake did not refire) | 0 |
| U1 unit top-level 1–111/331 | 2393 | 25 | 21 | 2 | 43s | byte-identical 25F+21E signature (MCP fixture baseline) | 0 |
| U2 unit top-level 112–221/331 | 2890 | 17 | 0 | 18 | 206s | identical 17-node set | 0 |
| U3 unit top-level 222–331/331 | 2219 | 5 | 2 | 33 | 43s | identical 7-node set | 0 |
| T1 tests top-level 1–78/155 | 1817 | 14 | 56 | 48 | 46s | 14 baseline F; **56 ERROR = mock-harness artifact** (see below) | 0 non-mock |
| T2 tests top-level 79–155/155 + {manager,migration,property,static} | 2453 | 56 | 0 | 79 | 83s | all 56 in ledger | 0 |
| Attestation family (own pack, per-file `--override-ini='timeout=120'`, 34 files) | 227P+2S | 0 | 0 | 2 | 666s serial | 34/34 CLEAN (+32 tests vs phase-1); 0 wedges | 0 |

**Union & ledger reconciliation** (delta-agg worker): union = 259 nodes = 203 non-mock + 56 mock-artifact.
- `comm` vs phase-1 204-node ledger: **reproduced 203 / head-only non-mock 0 / ledger-only 1**.
- The 1 ledger-only node: `test_skill_evolution_service.py::…force_resolve` — phase-1-documented LOAD-FLAKE (failed once under 12-way load; P/P/P 3× solo at both sides in phase-1; **P/P/P again today**). Watch-list class, not a regression signal.
- Census node `test_lcancheck_family_separation.py::…test_s3_whole_tree_census` failed in-shard → **F/F/F 3× solo today, each with the exact socket signature** (`grep: ./data_dev/vscode-user-data/code-server-: Operation not supported on socket`; live socket proven post-suite) → **ENVIRONMENTAL** (phase-1 proved P/P/P in fresh base worktree; file unchanged since). Follow-up (phase-1 #1, still open): add `data_dev` to `_CENSUS_EXCLUDES`.

**The 56 mock_* ERRORs — fully dispositioned, branch-not-cause:**
- Forensics (worker `38e956b4`): `tests/mock_{ensure_system_queues,project_delete,terminate_job_cleanup,test_job_queue_api}.py` are tracked, **md5-IDENTICAL at base `666c089d` and HEAD `c939aaa0`**, added by pre-base commits (`1e7e9223`, `acc1ff4e`, `4ed0d49e`); zero post-phase-1 commits touched ANY top-level `tests/*.py`; `git status tests/` clean.
- The "142 → 155 file drift" was an **enumeration-definition mismatch**: phase-1 T-shards enumerated `test_*.py` (142, pytest `python_files` contract); this sweep's T-shards enumerated all `*.py` (155 = 142 test_* + 7 mock_* + 3 manual_* + conftest + resume_mock_test + `__init__`). Explicit file args bypass pytest's filename filter → the 13 non-test harness files were force-collected and errored (3 classes: fixture `name` ×2, fixture `session` ×6, `JobLockManager.__init__() missing 'lock_repo'` ×48).
- Empirical base-parity (worker `cd01225e`, scope-matched both sides in scratch worktrees): **56 ≡ 56, node-set md5-identical, same 3 root-cause classes** → pre-existing harness-vs-contract dust, never-runnable as plain pytest file args from inception. Excluded from the delta with proof.
- Coverage integrity: all 142 `test_*.py` files ran (the 13 extra files are additive noise, no gap); T1/T2 boundary overlapped at `test_main_entry.py` (line 78 in both halves — cosmetic, green both sides).

**Branch-surface positive proofs:** `tests/test_maintenance.py` (branch-modified +24/−5) direct run = **77/77 PASS**; the 4 branch-added test files green (122/122 family + 39P+1S PG); `test_vscode_proxy.py` 70/70; FE 77 maintenance its + full-jest green.

**Delta verdict: ZERO new failures attributable to the maintenance-console branch at `c939aaa0` vs base `666c089d`.** Expected "204 pre-existing base-identical" = 203 reproduced (census = environmental-class among them) + 1 documented flake; +56 base-identical harness artifacts outside the ledger by construction. Dev's triage claim independently re-confirmed at final HEAD.

## 5. Skips & exclusions (explicit)

1. 34 attestation files excluded from S5c (wedge-class architecture, LESSONS 2026-09-27) — compensated by own 34-file pack, all clean.
2. `tests/postgres` (322 PG-gated) + `tests/e2e` (real-LLM) + `test/packs` — outside the default partition by addopts/convention; PG surface covered by the 3 dedicated PG packs above.
3. 1 designed PG-suite skip (case-64 SQLite-fallback conditional) — by-design loud skip.
4. 2 quarantined real-saver nodes (QUARANTINE.md, base-attributed) — reconfirmed base-identical.
5. 4 quarantined FE Jest nodes — reproduced exactly, A/B-proven pre-existing at parent (2026-09-26).
6. concurrency_atomic_unit_test pack NOT run — out of blast radius (branch does not touch cascade/observer/asyncio-helper machinery); concurrency-relevant surface (maintenance run-lock) covered by 15 lock/capture tests + TestContention/TestDualArmContention classes. Mapped in §8.
7. Release Gate (E2E LLM workflows) NOT run — additive cross-cutting feature, not an architecture refactor of core loops; per ensure.md blast-radius header. Whole-tree default partition + PG packs + browser e2e ARE the merge gate here.

## 6. ensure.md validation (scoped)

- **Critical 1 — no regressions in changed packs**: ✅ every change-set pack PASS (§1–§4).
- **Critical 2/3 — deadlock/concurrency integrity + no sync DB on loop**: ✅ by scoped mapping — `test_maintenance_run_lock_and_capture.py` 15/15 + contention classes in PG suite (real concurrent 2-engine one-winner case 64); full `concurrency_atomic_unit_test.sh` exists but guards subsystems untouched by this branch (skip documented, §5.6).
- **Critical 4 — dev.sh `--timeout-graceful-shutdown 10`**: ✅ PASS (dev.sh:102, live uvicorn line).
- **Important (await-correctness of 3 named helpers)**: out of blast radius — helpers untouched by branch diff.
- **Nice-to-have (dead code)**: 2 NEW NG8113 dead-import warnings found on the branch's FE files (F2) — flagged.
- No contradictions between ensure.md methods and pack rules this cycle (all validations ran as packs). No Improvement Notices.

## 7. Gaps found & dispositions

- **F1 🟠 (test-infra, pre-existing, deterministic): e2e boot-script teardown leak** — Playwright SIGTERM bypasses cleanup; postgres survives :15432; 8 dead `/tmp/pg_e2e_maint_*` clusters + repo `data_e2e_maintenance/` residue accumulated. Fix = boot-script trap/action_stop hardening (test-infra only). Not merge-blocking (post-run damage, cleaned by runner each time).
- **F2 🟢: 2 NG8113 cleanliness warnings** — `maintenance.component.ts:30` orphan import; `vscode-editor-cache.component.ts:57` unused imports.
- **F3 🟢 (methodology, this commission): T-shard enumeration must filter `test_*.py`** (python_files contract) — lesson recorded (LESSONS/2026-09-27-t-shard-enumeration-contract.md). Cost this cycle: forensics + base-parity legs to disposition 56 artifact nodes.
- **F4 🟢: "guard 65" claim arithmetic** — referent = single file's collected count; family authoritative = 122. Evidence discipline note to dev: state file sets when quoting counts.
- **F5 🟢 (carried): real-saver 2-char caplog logger fix** — cheapest test-debt win in tree, still deferred.
- **F6 🟢 (carried, phase-1 #1): `data_dev` census exclusion** — live socket broke the census test environmentally again today (3rd occurrence class).

## 8. Documentation & hygiene

- Worktree: ZERO writes by this commission outside `.agents/tester/` docs; foreign dirt (16 porcelain entries: approver/tidier/reviewer WIP + phase-1 artifacts + `data_e2e_maintenance/`) disclosed and untouched. HEAD anchored `c939aaa0` throughout (drift-guarded by 3 workers).
- All disposable PGs (15532/15534/15535 + e2e 15432 instances) torn down; all scratch worktrees removed (`git worktree list` clean); local shared PG 5432 untouched except the smoke script's own disposable DB; port 8088 never contacted.
- Worker instances (23): 8340a4f9 verify · 61e9dfec pw ×2 · 0f290f4b jest · c096ef17 smoke · d60cdbb2 unit · 4d461cad pgapi · 8dbd78dc realsaver · 6fbad2cb attest · bd1007cb febuild · 187c5c3b fespot · shards c0924cb2/f625b431/c19f0737/97121cda/9525bdcb/55c5cea6/438f2885/2d5722d3/ab22f6bd · 38e956b4 forensics · cd01225e mock-base · 45efa171 delta-agg · 4317359a ensure-statics.
- Logs: /tmp/mc-*-final*.log, /tmp/mc-wt-*.log (+ node lists), /tmp/mc-mock-{base,head}.log.

### Overall Status
- Playwright e2e: ✅ PASS (independent) · FE full suite: ✅ PASS · BE suites: ✅ PASS · Smoke: ✅ 12/12 · Whole-tree delta: ✅ ZERO new · ensure.md: ✅ (scoped)
- **Testing Complete: ✅ READY — no merge-blocking findings. Follow-ups F1–F6 non-blocking.**
