# Service Tool — FINAL VERIFICATION GATE (2026-09-16)

- **Branch**: `feature/service-tool` @ `13964b15` · **Base**: `ebd57cfc` (ancestor verified, exit 0)
- **Scope**: 40 files, +14,792 / −47 (service-tool surfaces: `daemon/services/service_tool_manager.py`, `daemon/services/service_reconciliation.py`, `daemon/repositories/service_tool/`, `daemon/tools/service_tools.py` + `service_spawner.py`, migration `20260915_212810_create_service_tracking.sql`, ~3,400 lines of tests). ZERO frontend files.
- **Tester**: tester (dispatch-only; 21 worker instances across 4 waves). Workers: precheck-infra, pack-killsite, pack-pgsmoke, sqlite-boot-sanity, sweep-p01..p12, sweep-integration, sweep-postgres, mg1-unit-block, mg1-int-block, adjudication-1/2/3, live-daemon-c1, live-daemon-c2-e2e.
- **VERDICT: ✅ SHIP (GO for merge) — 0 branch-caused reds; original goal verified end-to-end on a live daemon.**

## Scope Decision
Full suite run — **warranted**: this is a release/merge gate (leader-mandated 3.MG.5 + original-goal close-out). No scope reduction applied; e2e real-LLM files excluded from the automated `-m integration` sweep **by design** (Release-Gate prerequisites: live daemon + queue cleanup + one-by-one real-LLM runs; prior-gate precedent) — the feature's own live behavior is covered by Section C instead.

---

## A. Full test suite (3.MG.5)

**Method**: 12 disjoint partitions covering the ENTIRE default-addopts selection (precheck-verified: sum = 20,662 collected, `uniq -d` empty, per-partition collect rc=0) + full `-m integration` sweep (`--ignore=tests/e2e --ignore=tests/packs` by design) + serial `-m postgres` shard on disposable PG. All runs: `uv run python -m pytest` from repo root, `timeout 300` wrapper, no `-x`, quarantine-aware adjudication. **Landmine handled**: `tests/packs/g7_unique_index_smoke_test.py` sys.exits at import → all scopes exclude `tests/packs` (pre-existing).

### A.1 Unit partitions (default addopts)

| Partition | Counts | Runtime | Reds → attribution |
|---|---|---|---|
| P01 unit/tools a,c,e-f,i-k,m | 959P/5F/4S | 31s | TestAccessMemoryArchive ×5 → QUARANTINE rows 48–52 (ledger) |
| P02 unit/tools p,r-w | 1726P/1S/0F | 14s | CLEAN |
| P03 unit/[a-k] | 2057P/27F/2S/21E | 40s | 1 expected (coder) + all else ledger-mapped (see A.4) |
| P04 unit/[l-n] | 1837P/5F/1S | 185s | 4 mapped (rows 10/15/33/42) + 1 ctx-flake (mcp_tool_timeout, solo-green both sides) |
| P05 unit/[p-w] | 2387P/13F/50S/2E | 42s | 13 mapped (rows 10/31/33/40); 2E webfetch → row 30 + signature shift (A.5-d) |
| P06 unit/services | 1807P/8F/0S | 54s | b1_wc ×1 (row 12) + proxy_phase1 ×7 (rows 10/32 EXACT set) |
| P07 unit subdirs | 837P/0F | 28s | CLEAN |
| P08 top a-j + 5 clean e2e | 1376P/35F/48S | 38s | 34 mapped (rows 31/42/33/35/43/45/46 + 2026-09-15 CN-gate row); 1 base-identical (answer_dismiss, A/B) |
| P09 top l-m,o-r + api/manager/static/lint/migration/property/performance/integration | 1883P/71F/60S/42E (split-fallback: 2×99-file halves after 300s single-run timeout; union preserved) | 456s | See A.4 — incl. 44–60 SSL-cert env-artifact httpx errors (cleared on cert-unset; A/B-3), SQLite-trap families, 1 base-identical attestation-migration guard red |
| P10 top s-t,w + services | 1960P/14F/34S | 61s | ALL 14 mapped (rows 15/36/31/39/41/42). **5 expected expander base-reds NOT reproduced — GREEN at HEAD** |
| P11 job_queue | 1723P/7F/38S | 58s | 7 = ledger row 17 EXACT node-for-node |
| P12 opencode/mq_redesign/repositories/tools | 1615P/13S/4desel (run-2) | 83s | run-1 1F = ledger row 20 flake (pass on rerun + solo) → partition CLEAN |

**Totals**: ≈20,167P / 185F / 65E / 251S (+1 flake on P12 run-1; xfail/parametrize accounting ±6 vs 20,662 collected).

### A.2 Integration sweep (`-m integration`, e2e+packs excluded by design)
205P / 27F / 6S / 14E in 48s. Migration-cascade 16 nodes = documented 20260714 SQLite trap (compaction ×2 + message_queue ×3 = ledger row 25 EXACT; agent_bootstrap/completion_report/daemon_startup/inner_soul/instance_title/migration_e2e = same trap class). Remainder A/B-adjudicated (A.4). **All 5 branch-owned integration files GREEN** (kill_site 11P+1S incl. summary; cap 4P; flag_off 9P; reconciliation_e2e green; maintenancer marker-deselected here — covered green via P09 + solo run).

### A.3 Postgres shard (serial, disposable PG14:15433, both env families)
Command A (tests/postgres): 267P/5F/34S/2xf (308). Command B (remainder): 28P/1F/1S (30). Aggregate 295P/6F/35S (338 collected vs 337 est). **5 = the 2026-09-15 quarantine row EXACT set** (list_queues ×2, 06f500af, report_deferred C4, report_delivery Lane2 — none healed). **1 NEW-candidate → base-A/B'd: `test_defer_gate_post_settle_window::TestIdlePredicatePgSqliteParity` DatatypeMismatch boolean-vs-integer — IDENTICAL at base ebd57cfc → PRE-EXISTING** (adjudication-2, disposable PG 15434).

### A.4 Red adjudication — the gate's core question
Leader expectation: "the 6 KNOWN base-reds (5 expander-consumer pins + 1 coder prompt-composition) and NOTHING new." Observed reality per repo convention (quarantine families are sweep-visible; cf. 2026-09-15 job-timestamps gate: 128 reds ALL KNOWN):

- **Expected-6 reconciliation**: coder `test_load_coder_prompts` REPRODUCED (P03, pre-existing). **The 5 `TestExpandAllowForInnateSkills` expander pins are GREEN at HEAD** (P10; glob-match verified; class absent from output) — positive deviation; the dev's pre/post red set is context-dependent (full-run order vs partition).
- **Every other red family mapped to the QUARANTINE ledger** (rows 10/12/15/17/20/25/26/30/31/32/33/34/35/36/38/39/41/42/43/45/46/48–52/54 + the 2026-09-15 critical-notes gate row for `test_api.py` AsyncMock-rot ×2).
- **Base-tree A/B at ebd57cfc (detached worktrees, node-for-node F/E set compare)**:
  - Adjudication-2: 22 files, 62 red nodes + PG parity node → **ALL IDENTICAL at base; 0 branch-caused**. Includes: answer_dismiss ×1, webfetch ×2 (same nodes red at base with `'blueprint'` vs HEAD `'service_tool'` attr — signature drift only), mcp_tool_timeout (green solo both sides → ctx-flake).
  - Adjudication-3: migration guard + cert re-runs + 15 files → **0 branch-caused**: attestation-migration guard red IDENTICAL at base (offender `20260915_120000_critical_notes_lifecycle.sql` — critical-notes merge 68182287, NOT service-tool; branch's own `service_tracking` migration is guard-clean: no boolean columns, no int-literal defaults; branch touches exactly 1 migration); memory ×10 stable (+2 partition ctx-flakes), phase4_metrics ×6, skill_service ×3, progressive ×18, cold_resume ×2, skill family ×2 — all identical-sets at base; persistence/pipeline_unified/language_check ×124/loop_breaker/TestSendMessage GREEN solo (partition ctx-flakes); `report_delivery_double_delivery_pg` 14E = **env-only** (14/14 PASS with disposable PG at HEAD AND base).
  - SSL-cert env-artifact class (P09's 44–60 httpx TypeErrors): CONFIRMED row 37/62 — `unset SSL_CERT_FILE SSL_CERT_DIR` cleared everything except the 1 predicted quarantined node (`vscode_security TestC1PathTraversal`, row 38).
- **Adjudication-1 (P01+P03 families) — COMPLETE (revived report; initially completed 03:16 with undelivered report)**: 10 files HEAD-solo vs base-solo → **ALL PRE-EXISTING, 0 branch-caused**. archive ×5 identical (rows 48–52); api_router ×1 identical (api.py 2499>1600 at base → 2619 at HEAD — pre-existing cap breach; branch adds +120 service wiring to an already-red assert); coder_developer ×5, devops ×3, find_near ×13 (manager.py find_near region byte-identical, 0 diff hits; same ValueError both sides), job_processor ×4 — all identical sets; builtin_mcp 17E + context7 4E — identical node sets with the mock-ripple signature shift (base `slash_commands`/`blueprint` → HEAD `service_tool`), same class as webfetch; coder ×1 identical (the reproduced expected red). maintenancer_spawn_resolves_tools: **13P clean at BOTH sides** (file created at base c24e399d; branch-modified 1cb570d0 adds `service` to PRIVILEGED_TOOL_CATEGORIES — no regression; A14 third pin green with base comparison). Full 40-file diff list confirms **zero overlap between the branch diff and any red-family test file**.

**A-section verdict: PASS-WITH-KNOWN-REDS — 0 NEW (branch-caused) reds across 21,258 selected tests.**

## B. Packs

- **B1 — `test/packs/service_tool_kill_site_invariant.sh` (A7 CI grep-gate): PASS 25/25, exit 0, 1s.** Verbatim: `Token scan: 25 hit(s) across daemon/; 25 inside the allowlist. PASS: zero kill-primitive sites outside the allowlist.` Allowlist = 8 entries (the trio `bash.py`/`proc_tools.py`/`vscode_server_manager.py` + `upgrade_journal.py` precedent + the 4 service-tool surfaces).
- **B2 — `test/packs/service_tool_pg_smoke_integration_test.sh` (PG smoke, LIVE on real PG — never-executed residual, now closed): PASS 4/4 legs, exit 0, 2s.**
  - Leg A DDL idempotency ×3: byte-identical index set `['idx_service_tracking_name_active', 'idx_service_tracking_pid', 'service_tracking_pkey']` across applies.
  - Leg B partial-index predicate: `CREATE UNIQUE INDEX idx_service_tracking_name_active ON public.service_tracking USING btree (name) WHERE (status = ANY (ARRAY['starting','running']))` — exact.
  - Leg C start_time round-trip: `spawn()=1789551393 re-read=1789551393` ×2 pids PASS (+ non-blocking NOTE: macOS `lstart` seconds-precision → same-second processes share a token; the load-bearing round-trip assertion holds; platform-limited).
  - Leg D sweep reaps dead row: `{'alive': 0, 'reaped': 1, 'errors': 0, 'starting_reaped': 0}`; row → `exited`.
  - Hygiene: disposable DB `ensemble_test_servicetool_3a8` created+dropped on the local PG (URL hard-guards; **`ensemble_prod` byte-stable 31,227,415,899 bytes**); no leftover processes. *Note: pack used a uniquely-named disposable DATABASE on the existing local PG rather than a disposable server — functionally equivalent, prod provably untouched.*
- **Registration gap (fixed this gate)**: NEITHER pack was registered in `.agents/tester/PACKS.md` — both registered via this gate's banner (this file's convention is a gate ledger).

## C. Live daemon verification (dev env ONLY — ./dev.sh :8079; DB `ensemble_dev`; prod 9797 + self-system 8088 untouched throughout; every boot/kill PID-verified)

### C.1 Boot probe — PASS
`[ServiceTool] service_tool_enabled=True (env ENSEMBLE_SERVICE_TOOL_ENABLED), max_concurrent=10, reconcile_interval=90s` + `ServiceReconciliationService started: interval=90s` (re-confirmed on 2nd/3rd/4th boots across C-workers). /livez + /readyz 200. **Env-name truth**: `ENSEMBLE_SERVICE_TOOL_RECONCILE_INTERVAL` (NO `_SECONDS`; the `_seconds` suffix is the yaml field `ServicesConfig.service_tool_reconcile_interval_seconds` only) — matches the corrected task note; plan §3.B.7 A8 wording is the stale one.

### C.2 Default-deny (security direction) — PASS
Three surfaces: (1) `agents/wanderer/meta.json` tools.allow (15 categories) has no `service`; `/api/agents` (35 entries) zero service mentions; (2) **live filter line** `Filtered tools for wanderer: 237 → 88 (removed: …)` with ALL 5 `service_*` tools in the removed set; (3) instance-detail API exposes only `mcp_tool_names` (structural: no `/tools` route exists anywhere — precheck-verified; the DEBUG filter line is the authoritative tool-resolution observable).

### C.3 Explicit-allow — PASS
Scratch `agents/zz-svcgate/` (tools.allow `["service","todo"]`, registry restart to load): spawn → `Filtered tools for zz-svcgate: 237 → 16` — arithmetic closure: **exactly 5 `service_*` + all 11 `todo_*`**; non-privileged control category resolves normally alongside the privileged grant.

### C.4 ORIGINAL GOAL end-to-end — PASS (every sub-check)
- **service_start** (real LLM via llm.ensem.dev, 3-turn convergence — see observation (a)): pid=15381 `bash -c 'echo service-e2e-alive; sleep 600'`, PGID=15381. Triple evidence: OS `pgrep`; DB row `e2e-svc|running|15381|<instance-id>`; log `[ServiceTool] service_started name=e2e-svc pid=15381 status=running`.
- **TERMINATE the starting instance** → `{"terminated":true}`; **OS process STILL ALIVE** (PPID→multiprocessing tracker, unchanged); **row unchanged** (running/15381); **no `[ServiceTool] service_stopped` emitted — daemon did not touch the group**.
- **RESTART the daemon** → boot probe + `ServiceReconciliationService started: interval=90s` + **`[ServiceTool] reconcile_boot_sweep alive=1 reaped=0 errors=0`** (boot sweep runs, live service detected, nothing reaped); **row STILL RUNNING, SAME pid, `updated_at` byte-stable, EXACTLY ONE running row** (count=2 = 1 running + 1 historical `exited` attempt-1 row — by-design, see (b)); OS process alive (PPID→1 after restart, expected).
- **service_status** → `{"name":"e2e-svc","pid":15381,"status":"running"}`; **service_list** → running row + historical exited row (correct); **service_logs** → contains `service-e2e-alive`; **service_stop** → `{"status":"exited"}` + `[ServiceTool] service_stopped name=e2e-svc pid=15381 force=False grace_exited=True`; DB row `exited`; **process gone; PGID 15381 empty — no orphan children (killpg semantics verified)**.
- Cleanup: all instances terminated, 8079 free, scratch agent removed, tree byte-identical, `pgrep -f 'sleep 600'` empty.

### C.5 Flag-OFF spot check — PASS-WITH-NOTE (mechanism documented)
`ENSEMBLE_SERVICE_TOOL_ENABLED=0` boot: probe line `service_tool_enabled=False` (still emitted — by design, states the disabled state); **`ServiceReconciliationService DISABLED (service_tool_enabled=False)` — 0 start lines**; /livez + /readyz 200 (daemon otherwise normal). **Privilege-strip mechanism**: for an explicit-allow agent the 5 tools REMAIN in the resolved list (filter is allow-driven); enforcement is **at call time** — verified live: `service_start` under flag-OFF returns `{"status":"disabled","reason":"service_tool_enabled=False"}` with **no row written and no OS process spawned** (`daemon/services/service_tool_manager.py:252`). The default universe never resolves the tools at all (C.2). End-state (no service can be started) achieved; the leader's literal "tools absent in REMOVED set" holds only for the default-deny universe, not explicit-allow agents. The flag-off integration pin (`test_service_tool_flag_off_byte_identical.py`, 9P) is green — the pinned contract is satisfied; this note records the actual mechanism for the close-out.

## D. Sanity

- **FE impact: NONE confirmed** — `grep -ri "service_tool|service_start|service_stop|service_status|service_list|service_logs" frontend/src frontend/package.json` → **0 hits**.
- **Fresh-SQLite scratch boot: PASS-WITH-KNOWN-TRAP.** Boot on scratch `ENSEMBLE_DATA_DIR` (port 8180, uvicorn-direct) dies in migration `20260714_000001` (PG-only `DROP CONSTRAINT IF EXISTS` → sqlite OperationalError) — **pre-existing trap** (landed 843e2c34 2026-07-14, ancestor, branch never touched it; LESSONS 2026-09-04). **Both service-tool items PASS before the crash**: boot probe emitted at config-load (`service_tool_enabled=True …`), and `service_tracking` created via `SQLModel.metadata.create_all` (manager.py:510 runs before run_pending_migrations :515; 47 tables incl. service_tracking). /livez//readyz unreachable is the trap's consequence, not the feature's. Residue byte-clean.

## 3.MG.1 runbook (executed as written + deviations documented)

- UNIT block: 9/10 files GREEN (148P/0F/0S: registration 15, repository 29, tools 15, reconciliation 27, loader-cold-boot 3, frozen-name 7, upgrade-registration 21, attestation-registration 24, schema-pin 7). **Item 4 path drift**: runbook says `tests/unit/test_service_spawner.py` — never existed; actual `tests/unit/tools/test_service_spawner.py` → **14P/1S GREEN** (adjudication-2 deliverable-0). Plan §3.MG.1 runbook status marker `[done 3.A.4 — d617bd5b]` refers to the real path.
- INTEGRATION block: kill-site matrix **13 sites enumerated: 11 PASSED + K12 SKIP (darwin, as documented) + summary PASSED**; cap-enforcement 4P; flag-off 9P. **Command 1 (`test_maintenancer_spawn_resolves_tools.py -m integration`) was a NO-OP** — the file carries ZERO integration markers (grep-verified) so `-m integration` deselects its 13 tests; covered GREEN via P09 (default addopts, tests/integration dir) + solo run. Runbook marker drift, not a code defect. Command 5 (`test_service_reconciliation_real_pg.py`) N/A — absent per its own conditional; `test_service_reconciliation_e2e.py` exists instead and is green in the integration sweep.
- A14 third pin (maintenancer frozenset): exercised green via P09 + solo (above).
- MG.1 acceptance: **all service-tool-owned tests 100% green** (unit 162P+1S across 10 real files; integration 24P+1S).

## Observations (leader item 4, precise)

- **(a) `cwd` field-required wrinkle** — Plan `phase3-plan.md` task 3.A.3 case (g) specifies `service_start` schema: "name, command (list[str], min_length=1), **cwd (Optional[str])**". LIVE behavior (C.4): an LLM tool-call OMITTING `cwd` was rejected with **`Field required`** (langchain args-schema validation); a call passing `cwd="null"` (string) passed schema but failed spawn (`spawn_failed`, exited row). The tool only succeeded with an explicit valid `cwd` path (3rd turn). → **Spec-vs-implementation drift**: the shipped args schema treats `cwd` as required. Follow-up: either make `cwd` Optional in `daemon/tools/service_tools.py` args_schema (+ amend the schema pin in `tests/unit/tools/test_service_tools.py` case (g)) or amend the plan/docs to document required-cwd. Severity 🟠 (agent-UX: every caller must supply cwd; not a correctness defect).
- **(b) Historical `exited` row** — by design: partial UNIQUE index `idx_service_tracking_name_active WHERE status IN ('starting','running')` allows name reuse after EXITED (D5); `service_list` surfaces historical rows. One line, closed.
- (c) C.5 mechanism (call-time runtime-disable vs registry-strip) — see C.5.
- (d) **webfetch mock-ripple signature shift**: `test_webfetch_builtin.py` ×2 setup errors — SAME 2 nodes red at base (row 30 'blueprint' AttributeError) and at HEAD, but HEAD's missing attribute is **`service_tool`** (the branch's new config surface; stale spec'd fixtures). Node-set identical → not a new red; **test-debt follow-up: update the fixtures to mock `config.service_tool`** (mirrors the row-30 blueprint fix pattern). Ledger marker recorded.
- (e) **Pre-existing PG-reject DDL surfaced by the static guard** (NOT service-tool): `20260915_120000_critical_notes_lifecycle.sql` carries `BOOLEAN NOT NULL DEFAULT 0` — PG rejects on fresh deploy, SQLite silently accepts. Merged via critical-notes 68182287. **Route to critical-notes owner; fix or allowlist with justification.**
- (f) **PG env-family trap**: `tests/postgres/conftest.py` reads `PG_TEST_HOST/PORT/DB/USER/PASSWORD` (NOT `ENSEMBLE_TEST_PG_URL`, which only `tests/unit/tools/test_ens_db_*` consumes) — both families required; a first shard attempt probed the system PG (no mutation — create_all failed pre-yield on permissions). LESSONS-worthy.
- (g) Expander ×5 expected-reds not reproduced (green at HEAD) — dev A/B full-run red set is context-dependent; bookkeeping note only.

## Quarantine ledger updates (this gate)
Consolidated row added (base-evidenced @ ebd57cfc by adjudication-2/3): answer_dismiss ×1, defer_gate PG parity ×1, attestation-migration guard ×1 (offender = critical-notes DDL), memory stable ×10 (re-confirm), phase4_metrics ×6, skill_service ×3 (re-confirm), progressive ×18 (re-confirm), cold_resume ×2, skill family ×2, mcp_tool_timeout ctx-flake ×1, webfetch signature-shift marker, report_delivery_double_delivery_pg env-class (14/14 pass w/ PG), httpx cert-class re-confirm (cleared on unset). All prior rows still accurate; none healed except expander ×5 (green in partition context — no ledger change needed since they were never ledger rows).

## Follow-ups (non-blocking, ranked)
1. 🟠 `cwd` Optionality fix (observation (a)) — small, test-coupled.
2. 🟠 Critical-notes DDL `BOOLEAN NOT NULL DEFAULT 0` (observation (e)) — pre-existing, PG-fresh-deploy blocker for THAT feature, not this one.
3. 🟢 webfetch fixtures mock `service_tool` (observation (d)).
4. 🟢 Runbook fixes in plan docs: spawner path, maintenancer marker note, real_pg conditional, A8 `_SECONDS` wording (env truth: `ENSEMBLE_SERVICE_TOOL_RECONCILE_INTERVAL`).
5. 🟢 LESSONS: PG env-family trap (f); SSL-cert unset required for integration sweeps (re-confirm of row 37).
6. 🟢 Adjudication-1 addendum when its revived report lands (confirmatory; expected no change).

## Overall Status
- **A (full suite): ✅ PASS-WITH-KNOWN-REDS — 0 NEW reds** (expected-6 → 1 reproduced + 5 green; every other red ledger-known or base-A/B-identical at ebd57cfc)
- **B (packs): ✅ PASS** (A7 25/25; PG smoke 4/4 legs)
- **C (live daemon): ✅ PASS** (C.1–C.4; C.5 PASS-WITH-NOTE mechanism)
- **D (sanity): ✅ PASS** (FE 0 hits; SQLite boot = known pre-existing trap + service-tool items green)
- **VERDICT: SHIP (GO for merge)**

---

# ADDENDUM — OVERRIDE GATE: default-enablement (D4 reversed) — 2026-09-16 (later same day)

- **Branch**: `feature/service-meta-grant` @ `a29778c6` (base `282c20b3`) — USER OVERRIDE: service tools default-enabled for all agents. **Proportionate gate** (targeted families + live boot only; full suite NOT rerun by design — dev pre-ran pins/registration 62/62, flipped core 69/69, 217 family, 9 flag-off, 251 service suites, 23/1-skip service integration, harness exit 0 ×2).
- **VERDICT: ✅ GO for merge.**

## 1. Harness — PASS
`timeout 120 uv run python tools/dev/verify_service_default_open.py` → **exit 0**, ~30s. Table: **24 agents gain +5 service tools, 13 unchanged**; final line `OK — all agents match the meta-grant IFF invariant.` (e.g. watcher `default`-shape 0→5; charter/coder/wanderer/worker non-empty 0→5; leader/blueprinter/explorer/governor… unchanged.)

## 2. Targeted families — ALL GREEN (217P/0F/0E)
`tests/test_tool_filter.py` 55P (expander family fully green under default-open) · pins: `test_upgrade_registration` 21P + `test_attestation_registration` 24P + `test_maintenancer_spawn_resolves_tools` 13P · `test_service_tool_flag_off_byte_identical` 9P (`-m integration`) · `test_service_tool_config` 71P · `test_service_registration` 17P · `test_frozen_tool_name_discovery` 7P.

## 3. Known-red registry-consumer family — IDENTICAL (no new sensitivity)
`test_builtin_mcp_servers` 17E + `test_context7_builtin` 4E + `test_webfetch_builtin` 2E = **23 errors, node-for-node + signature IDENTICAL to this gate's base-A/B** (`AttributeError: Mock object has no attribute 'service_tool'` on all) — the documented mock-ripple family reproduces unchanged; default-open introduces no new red nodes.

## 4. meta.json regression diff — ALLOW-APPENDS-ONLY ✓
`git diff 282c20b3..a29778c6 -- 'agents/*/meta.json'` = **22 files, uniform `"service"` append to tools.allow**; deny arrays byte-identical (v2 agents); no other field changes; no adds/deletes/renames. Count reconciliation 24-vs-22: `_baby_template` + `watcher` receive service via the default-open expansion path itself (empty/other allow shapes) — consistent with the harness IFF invariant; no per-file append needed.

## 5. Live boot (dev.sh :8079, `ensemble_dev`; prod 9797 untouched) — PASS
- **Default boot**: probe `[ServiceTool] service_tool_enabled=True (env …), max_concurrent=10, reconcile_interval=90s`; /livez + /readyz 200.
- **REVERSAL PROOF**: `watcher` (`tools.allow: []` — default universe) resolves the FULL 237-tool universe incl. all 5 `service_*` (no filter-removal line — nothing removed); `charter` (allow incl. `service`) filter line `237 → 20 (removed: …217…)` with **all 5 service tools KEPT, none in removed set**; arithmetic 20+217=237 ✓. Inverse of the default-DENY branch (wanderer `237 → 88`, all 5 removed — Section C.2 above).
- **Flag-OFF boot** (`ENSEMBLE_SERVICE_TOOL_ENABLED=0`): probe `service_tool_enabled=False`; `ServiceReconciliationService DISABLED (service_tool_enabled=False)` — **no sweep lines**; /livez 200; **call-time disable marker** verified via real-LLM `service_status` call → tool_call output verbatim `{"status": "disabled", "reason": "service_tool_enabled=False", "name": "flagoff-probe"}`.
- Cleanup: 4 instances terminated, daemon stopped, 8079 free, env unset, `git status` clean.

## Override-gate status
- 1 Harness ✅ · 2 Targeted families ✅ (217P/0F) · 3 Known-family identical ✅ · 4 meta.json allow-appends-only ✅ · 5 Live reversal + flag-OFF ✅
- **VERDICT: GO for merge.** (Follow-ups inherited unchanged from the main gate: cwd-Optionality 🟠, critical-notes boolean DDL 🟠, mock-family fixture updates 🟢.)
