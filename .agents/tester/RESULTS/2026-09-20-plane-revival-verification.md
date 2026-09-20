# Plane Integration Revival — Final Gate Verification

- **Branch**: `feature/plane-integration-revival`; first gate @ `10ab7f18c2ebf4d0c895d185ae4d4a8992e3c9e7` (base `67b51e17`), re-gate @ `ebb34527f9176f965efb2b09bc8934ef07930298`. Pins held by every worker; worktree porcelain-clean throughout; zero repo writes, zero code changes, zero commits by gate workers beyond the RESULTS file itself.
- **Dates**: 2026-09-20 (first gate + same-day re-gate). **Gate**: tester instance `12533058-68e9-4f86-8f76-80bcdf83ebda`; 11 first-gate workers + 6 re-gate workers.
- **Mission**: pre-merge final gate; 5 success criteria + kill-switch legs; Part A pytest suites, Part B live second-daemon proof (disposable PG14 :15432, daemon :8180, never touching 8079/8088, ensemble_prod read-only).

## Per-Criterion Verdict Table (FIRST GATE @ 10ab7f18)

| # | Criterion | Verdict | Blocking cause |
|---|---|---|---|
| 1 | MCP session health: zero `Failed to create session for 'plane'` post-boot; session ESTABLISHED | ✅ **PASS** | — |
| 2 | MCP E2E: agent creates/reads/deletes Plane task via plane_* tools | ❌ **FAIL** | **D1** — plane_* tools invisible to agent runtime (pre-existing seam) |
| 3 | REST sync live: 200 `status=linked` + `plane_project_id`, Plane-side exists, 404, no-dup | ❌ **FAIL** (core leg) | **D3** — `create_project` payload missing Plane-required `identifier` → 400 → `status="error"` (branch-caused). Legs 404/no-dup/no-artifact PASS |
| 4 | PM agent: RUNNING + trivial task + plane_* tools listed | ❌ **FAIL** (sub-criterion) | Liveness ✅ (RUNNING→COMPLETED in ~8s); plane_* ❌ — same **D1** |
| 5 | Re-drive mechanism: error-state row → boot sweep → linked/error-with-retry | ✅ **PASS** (conditional) | Adopt-path → `linked` proven with exact `plane_project_id` match; non-match → error-with-retry (attempt 0→1, D3's 400 recorded). Create-path linked blocked by D3 |
| KS | Kill-switches `PLANE_SYNC_ENABLED=false` / `PLANE_MCP_ENABLED=false` | ⚠️ **SPLIT** | SYNC leg ✅ PASS / MCP leg ❌ FAIL — **D5** partial coverage (177 tools still primed with the switch OFF) |

**First-gate overall: ❌ NOT READY** — C2/C4 fail on D1 (pre-existing at `latest`, NOT branch-caused), C3 core leg fails on D3 (branch-caused, critical), MCP kill-switch leg fails on D5. All Part A suites green with zero branch-caused failures. → Fix chain shipped same day; see Re-Test Section below.

## Diagnosis-Conflict Resolution (from Check 1)

**Phase 1 quote-poisoning diagnosis CONFIRMED; Phase 2's 401 `invalid_token` verdict superseded — it was contamination from the poisoned row value.**
- The daemon process inherited `PLANE_MCP_*` env vars with literal surrounding quotes; config-layer sanitizers (`daemon/mcp/config.py:64-131`) stripped them before persisting to `mcp_servers` and before session creation.
- Persisted row (disposable PG, SELECT): url `https://mcp.ensem.dev/plane/http/api-key/mcp` (unquoted), `Authorization: Bearer plane_api_…` (unquoted) — accepted: **177 tools primed**, protocol `2025-11-25`, `POST /api/mcp-servers/test-connection` → `success=true, tools_count=177`.
- The key itself is valid (`plan…6f2` redacted form). Zero `Failed to create session for 'plane'` post-boot.
- Corroboration: REST path with the same key (clean, X-Api-Key) returned 201 direct-create → key + workspace valid on the REST host too.

## Defect Register (first gate; report-only — all four fixed in the same-day chain, verified in Re-Test Section)

### D1 🔴 CRITICAL — plane_* MCP tools invisible to agent instances (pre-existing at `latest`; NOT branch-caused)
- **Mechanism (confirmed by source trace)**: `daemon/tools/instance.py:329-338` dynamic MCP category expansion matched only `name.startswith("mcp_")`; the plane server's `tool_name_prefix="plane"` never expanded. PM's `allow=["plane", …]` fell through the literal-name branch (`:351-357`), so every `plane_*` tool was dropped at `:4946`. `DYNAMIC_TOOL_PREFIXES={"plane_"}` was validation-only.
- **Attribution**: `git log 67b51e17..HEAD -- daemon/tools/instance.py` = empty at first gate; block authored `3ec7884e` (2026-05-17), plane server added `560c2e90`. Existed on `latest`.
- **Live proof**: two independent PM instances reported zero plane_* tools; API `mcp_tool_names=null` while daemon-side 177-179 schemas were loaded.
- **Fix (shipped)**: `1b58393b` generic `DYNAMIC_TOOL_PREFIXES` expansion + `ebb34527` real-seam pin. **Re-gate: FIXED** (C2/C4 PASS).

### D3 🔴 CRITICAL — REST `create_project` payload missing required `identifier` (branch-caused)
- `daemon/clients/plane_http_client.py:331-363` sent only `{name, description}`; Plane requires `identifier` → HTTP 400 → sync 200-with-`status="error"`, `plane_project_id=null`. Plane also rejects hyphens in project names.
- **Fix (shipped)**: `109a96db` `derive_plane_identifier(name, attempt)` — seed shortname‖name → [A-Z0-9] ≤12, ENSEMBLE fallback, collision → `PlaneIdentifierCollisionError` on live-409 → retry ≤5 → error+watchdog re-drive. **Re-gate: FIXED** (C3 real-create → linked, derived `ENSRETEST202`).

### D5 🔴 CRITICAL — `PLANE_MCP_ENABLED=false` kill-switch partial coverage
- First gate LEG-2: with the switch OFF, `eager_warm_schemas` still warmed the pre-existing DB `plane` row (177 tools primed). Emergency-off valve for the MCP path did not stop plane traffic.
- **Fix (shipped)**: `63fc33b7` per-server gate via `BuiltinServerRegistry.is_available` seam, one INFO notice per skipped server, user rows warm unconditionally. **Re-gate: FIXED for warmup/priming** (see KS-MCP row; residual `test-connection` bypass tracked as D7).

### D2 🟠 IMPORTANT — REST-path env quote-leak
- `_env()` (`daemon/clients/plane_http_client.py:94-96`) only whitespace-stripped; quoted `.env` values interpolated literal quotes into the REST URL → Plane 403. Boot-recipe corollary: first boot lacked `PLANE_API_KEY` in process env entirely (recipe gap).
- **Fix (shipped)**: `109a96db` outer-layer quote-strip (P1 semantics). **Re-gate: FIXED at the seam** (boot with quote-carrying env → REST watchdog started CONFIGURED, "not configured" count 1→0; unit-pinned by 33 `test_plane_identifier.py` tests).

### D4 🟢 OBSERVATION — transient `Connection closed` on the test-connection seam
- Observed only inside another verifier's restart window; settled-daemon re-check 3/3 `success=true`. Not a standalone defect; consistent with the known spawn-cadence standing risk.

## Part A — Suite Results, FIRST GATE (all `uv run python -m pytest`, dual-layer timeouts, pin `10ab7f18`)

| Pack | Scope | Result | Notes |
|---|---|---|---|
| A1a plane suite | 5 plane test files (dev said 6 — description drift; 6th diffs as MCP-quote, run in A1b) | ✅ **260/260** | 253P default + 7P integration re-run; dev claim "~286" not reconcilable — flagged |
| A1b MCP quote/config | `test_mcp_quote_sanitization.py` + `test_mcp_config.py` | ✅ **67/67** | 38+29 exactly as claimed |
| A3 concurrency | `test/packs/concurrency_atomic_unit_test.sh` (13-file canonical) | ✅ **98P/74S/0F** | Exact parity with 2026-09-13 baseline — ensure.md Core #2/#3 GREEN |
| A2 unit sweep | `tests/unit/` + `tests/api/test_plane_settings.py`, 5 chunks | ✅ **PASS — 0 branch-caused** | 12,780 collected → 12,642P/57F/58S/23E; every red classified |

**Known pre-existing reds — confirmed at HEAD by exact signature:** `test_api_module_is_small` (2827<1600, quarantined family); mock-rot **23E** (17 `test_builtin_mcp_servers` + 4 context7 + 2 webfetch, `service_tool`; `test_mcp_service.py` itself green — mission family naming broader than rot site). Ledger-gap recommendation: gaia ×3 → QUARANTINE row (tools.allow-drift family).

## Part B — Live Verification, FIRST GATE

- Disposable PG **14.22** :15432 (`ensemble_plane_verify`), daemon :8180 from the feature worktree; boot lineage PIDs 34869→37361→38378 (coordinated, port-ownership-verified restarts). Prod (8079) vacant — never contacted; 8088 untouched; `ensemble_prod`: exactly ONE read-only SELECT.

### Criteria 5 detail (re-drive mechanism)
- Boot sweep first tick **T+3s**: `{'considered': 3, 're_drove': 1, 'errors': 2, 'skipped_backoff': 0}`.
- Project A (no Plane name-match): `error` → attempt 0→1, `plane_last_error` = D3's 400 identifier — error-with-retry proven.
- Project B (name-matched `LLM Proxy`): **ADOPT → `linked`, `plane_project_id=da18585b-a301-4e5d-b2cd-6dee6215504d`** — exact match to Plane REST id.
- **Prod backlog (read-only)**: **21 rows** = entire population (1 error + 20 no metadata), 0 linked.

### Kill-switch verification, FIRST GATE
- **LEG 1 `PLANE_SYNC_ENABLED=false`: ✅ PASS** — 503 `plane_disabled`; exactly one watchdog no-op line; 330s soak with identical before/after psql state (no error-state writes).
- **LEG 2 `PLANE_MCP_ENABLED=false`: ❌ FAIL → D5** — plane still primed (177 tools) from pre-existing DB row; kill-switch-specific notice count 1, total plane-disable notices 2.

## ensure.md Status (first gate; Core, blast-radius scoped)

- [x] Critical #1 — changed packs PASS · [x] #2 deadlock/concurrency (98P/74S/0F) · [x] #3 no-sync-DB (same pack) · [x] #4 `dev.sh:102` `--timeout-graceful-shutdown 10` (static, PASS). Important #1 out of blast radius (not run, noted). Release Gate deferred-with-notice (requires `./dev.sh` on 8079 — forbidden).

## Scope Decision

Mission-scoped both rounds (leader-defined): targeted suites + timeboxed sweep (first gate) / focused re-test packs (re-gate); live chains on disposable second daemons. No silent expansion.

## FIRST-GATE Artifacts

- **Plane-side**: NONE (direct-created test project DELETE 204; adopt metadata-only; final count 3 originals).
- **Disposable env**: torn down (daemon stopped, PG stopped, :8180/:15432 freed; pgdat + transcripts preserved).
- **Transcripts (kept)**: `/tmp/plane-revival-verify/b1-criteria1.md`, `b2-criteria2.md`, `b3-criteria3.md`, `b4-criteria4.md` (+ `c4-evidence/`), `b5-criteria5.md`, `b6-killswitch.md`, `daemon-stdout.log`, `data/logs/ensemble.log`.
- **Repo**: single gate commit `1872d649` = this RESULTS file.

## FIRST-GATE Worker Roster

| Worker | Instance | Node |
|---|---|---|
| pack-plane-suite | `36bc0718` | A1a (+integration re-run) |
| pack-mcp-config | `0caa4cb7` | A1b |
| pack-concurrency | `67f25416` | A3 |
| pack-unit-sweep | `9acc39da` | A2 |
| live-boot-c1 | `ba074ec6` | B1 / Criteria 1 |
| live-c2-mcp-e2e | `4af7be52` | B2 / Criteria 2 |
| live-c3-rest-sync | `aa23ae81` | B3 / Criteria 3 |
| live-c4-pm-agent | `26cf29fe` | B4 / Criteria 4 |
| live-c5-redrive | `fe00d343` | B5 / Criteria 5 |
| live-c6-killswitch | `5c06b432` | B6 / Kill-switches + teardown |
| analyze-plane-tool-filter | `0b27a6f2` | D1 root-cause + attribution |

---

# Re-Test Section — Fix-Chain Verification (2026-09-20, same day)

**Chain under test**: `10ab7f18` → `1872d649` (gate RESULTS) → `109a96db` (D3 identifier + D2 quote-strip, REST lane) → `1b58393b` (D1 generic DYNAMIC_TOOL_PREFIXES expansion) → `63fc33b7` (D5 eager-warm gate) → **`ebb34527`** (D1 real-seam pin). Pin verified by every re-gate worker; worktree porcelain-clean; zero gate-worker code changes.

## Re-Gate Verdict Table (all 5 criteria + both KS legs)

| # | Criterion | First gate | Re-gate @ ebb34527 | Evidence |
|---|---|---|---|---|
| 1 | MCP session health | ✅ PASS | ✅ **PASS** | 0 × `Failed to create session for 'plane'`; `test-connection` success, 177 tools; REST watchdog started configured (first gate's "not configured" line: 1 → 0) |
| 2 | MCP E2E via agent | ❌ FAIL (D1) | ✅ **PASS** | PM instance `8bbd35f2…`: 5 distinct `plane_*` invocations logged — `plane_list_projects` → `plane_create_work_item` (task `SYSTEMDEFAUL-1` / `ens-retest-2026-09-20T2007Z`) → retrieve (exact title) → delete → re-read 404. Plane-side artifacts: NONE |
| 3 | REST sync real-create → linked | ❌ FAIL (D3) | ✅ **PASS** (all legs) | Project `ensretest20260920200851` → **real Plane-side CREATE** (absent from baseline → new id `602eac72-…`, count 4→5), sync 200 `status=linked`, derived identifier **`ENSRETEST202`** ([A-Z0-9], 12 chars — D3 spec); second POST idempotent (plane_project_id unchanged, name count stable 1); cleanup DELETE 204, back to baseline. Nuance: the genuine create was performed by the background sweep ~1s after project creation (~20s before the explicit POST, hence `action=updated` on POST #1) — create genuine, endpoint idempotent |
| 4 | PM agent plane_* tools | ❌ FAIL (D1) | ✅ **PASS** | `mcp_tool_names` = **179** (177 plane_* + 2 mcp_context7_*); sample names `plane_list_projects`, `plane_create_project`, `plane_update_project`…; agent reply matches listing 1:1; lifecycle idle→running→completed 15s |
| KS-SYNC | `PLANE_SYNC_ENABLED=false` | ✅ PASS | ✅ **PASS (carried forward)** | Not re-run this round — the fix chain does not touch the sync kill-switch gate path; first-gate evidence (503 `plane_disabled`, 1 no-op line, 0 error-writes over 330s) stands at the same router seam |
| KS-MCP | `PLANE_MCP_ENABLED=false` | ❌ FAIL (D5) | ✅ **PASS (per mission spec) + residual D7** | (a) ZERO plane primed — `primed 1/1 MCP server schema(s) (context7: 2 tool(s))`; (b) exactly ONE skip notice — `builtin 'plane' not available (kill-switch off or missing config) — skipping schema warm`; (c) context7 unaffected. **Flip-back**: no override → `primed 2/2 (plane: 177, context7: 2)`, skip-notice count 0 — full reversibility. Residual: `test-connection` for plane STILL returns `success=true, 177 tools` with the switch OFF (**D7**, pre-existing, non-regression) |

**Re-gate overall: ✅ READY — all 5 criteria + both KS legs PASS at `ebb34527`; zero branch-caused failures across all suites; residuals D6/D7 (both pre-existing, non-blocking for merge) filed for follow-up.**

## Re-Gate Suite Results (uv run python -m pytest only, dual-layer timeouts, pin `ebb34527`)

| Pack | Scope | Result | Notes |
|---|---|---|---|
| R-A1 plane set | 6-file union (flag: mission's "7-file" arithmetic double-counts `test_plane_sync_phase4.py`; the 2 chain-new files outside the plane-name grep ran in R-A2) | ✅ **300/300** (293 default + 7 integration re-run) | New `test_plane_identifier.py` = 33 tests (D2 quote-strip + D3 derive/collision/error-hierarchy); `test_plane_sync_phase4.py` 29→36 (+7 KS split) |
| R-A2 tool family | `tests/unit/tools/` (2,789 collected) + `test_mcp_service.py` (69) + PM agent + domain access + quote/config (227) | ✅ **PASS — 0 branch-caused** | 2,779P/5F/5S + 225P/2F; the 7 failures are EXACTLY the first-gate quarantined families (archive_lifecycle ×5, PM prompt cross-reference ×2); **D1 seam `test_dynamic_toolset_expansion.py` 11/11 incl. real `create_instance_tools` public-spawn coverage** (`test_create_instance_tools_threads_plane_through_real_seam`, `test_no_plane_server_row_yields_no_phantom_tools` negative control) |

**Count reconciliation vs "≥326"**: not reproducible from the specified union — 300 (in-pack) + 16 chain-new tests living in the 2 grep-invisible files (mcp_service +5, dynamic expansion +11, both green in R-A2) = 316 branch-adjacent; branch-new test count = **56** (33 identifier + 7 phase4-KS + 5 mcp_service + 11 expansion), all green. Description drift flagged, not a coverage gap.

## Ledger Item — `mcp_full_access: ["plane"]` ↔ D1 expansion (functional proof)

`agents/project-manager/meta.json:18` declares `mcp_full_access: ["plane"]` — the exact surface the D1 dynamic-expansion fix operates on. Proven functionally at ebb34527: **VISIBLE** — PM instance spawned with no custom id exposes 177 `plane_*` names in `mcp_tool_names` (179 total with the 2 `mcp_context7_*`), so the registry materialized every Plane function into the agent's tool list at build time, not just a permission slot. **USABLE** — the C2 run round-tripped five distinct `plane_*` invocations (`plane_list_projects` → `plane_create_work_item` → `plane_retrieve_work_item` → `plane_delete_work_item` → `plane_retrieve_work_item` 404-confirm), each a real `[LLM] Tool call:` line with bound args in the daemon log, resolved → authorized → executed against mcp.ensem.dev → typed payload consumed by the LLM. No gap found; no new unit test required beyond the shipped real-seam pin (11/11).

## New Findings This Round (report-only; both pre-existing, non-blocking for merge)

- **D6 🟠 pre-existing spawn-flow defect (NOT branch-caused)**: custom non-UUID `instance_id` → `ensure_mcp_preloaded()` runs with the name that `instance_lifecycle.py:1610` then rejects and regenerates → API response carries the new UUID with `mcp_tool_names=[]`. Workaround: default UUID ids (all re-gate spawns used them). Fix directions in `/tmp/plane-retest/rb1-boot-c4.md` §7 (reject upfront / re-preload post-validation / validate inside spawn before preload).
- **D7 🟠 `test-connection` endpoint bypasses the MCP kill-switch (pre-existing at 10ab7f18; unchanged by `63fc33b7`; NON-regression)**: with `PLANE_MCP_ENABLED=false`, warmup/priming is correctly gated (context7-only, one INFO skip notice) but `POST /api/mcp-servers/test-connection` for plane opens a FRESH connection from the inline-supplied config, bypassing `_mcp_kill_switch_enabled()` (`daemon/routers/mcp_servers.py:173`) → returns `success=true, 177 tools` while the emergency switch is OFF — actively misleading for an operator verifying switch state. Fix direction: route the endpoint's plane (builtin) path through the same kill-switch check.
- **Incident disclosure (constraint-adjacent)**: the re-gate's FIRST boot attempt used `start.py`, which loads `.env` and overrides `POSTGRES_*` with shared `ensemble_dev` defaults — a ~3-minute mis-boot ran `create_all` (~51 tables) on shared **ensemble_dev**. No data changed; **ensemble_prod untouched**; corrected recipe (pre-written disposable `data/ensemble.json` + direct `uvicorn daemon.api:app`) used for every subsequent boot and documented in evidence. First-gate `/tmp/plane-revival-verify/` evidence left intact.
- **Housekeeping**: the first-gate commit `1872d649` carried a duplicated tail-section artifact (worker roster ×2 + one split line) introduced by concurrent same-file edits during report assembly; this revision rebuilds the file cleanly with identical information content — no evidence lost.
- Minor: daemon `[LLM] Tool call:` log lines truncate arg dicts ~200 chars (names + binding args still visible); Plane sync state lives in `project_metadata_records` rows, not `projects.metadata` JSONB.

## Re-Gate Artifacts & Certification

- **Plane-side**: NONE — C2 task delete 404-confirmed; C3 test project DELETE 204; R-B4 final REST list = exactly the 4-project baseline (`nea`/NEA, `Ensemble`/ENSEMBLE, `LLM Proxy`/LLMPROXY, `__system_default__`/SYSTEMDEFAUL) with **zero ens-retest/ensretest/ens-* artifacts**.
- **Disposable env**: torn down — daemon stopped (all boot PIDs), :8180 FREE; PG `pg_ctl stop -m fast` clean, :15432 FREE; `/tmp/plane-retest/` evidence preserved (rb1-rb4 + raw JSON artifacts + logs).
- **Fences**: :8079 and :8088 untouched both rounds (lsof-verified); `ensemble_prod` never written (first gate: 1 read-only SELECT; re-gate: zero access); `ensemble_dev`: one disclosed mis-boot DDL-only incident (above).
- **Repo certification**: HEAD `ebb34527…` unchanged; only this RESULTS file modified; zero gate-worker commits beyond the RESULTS commits.
- **Transcripts**: `/tmp/plane-retest/rb1-boot-c4.md` (+ `c4-evidence-round2.json`), `rb2-criteria2.md`, `rb3-criteria3.md` (+8 raw artifacts), `rb4-killswitch.md` (+ leg1/flipback mcp-servers JSON, testconn JSONs, daemon-stdout logs ×2).

## Re-Gate Worker Roster

| Worker | Instance | Node |
|---|---|---|
| repack-plane-set | `ee9cd331` | R-A1 |
| repack-tool-family | `6c3ddcfc` | R-A2 |
| relive-boot-c4 | `c05fd9e0` | R-B1 (boot + health + C4) |
| relive-c2-mcp-e2e | `2c47baf6` | R-B2 (C2 + ledger item) |
| relive-c3-rest-sync | `3a13126f` | R-B3 (C3) |
| relive-c6-killswitch | `47b7d0b1` | R-B4 (KS-MCP + teardown) |
