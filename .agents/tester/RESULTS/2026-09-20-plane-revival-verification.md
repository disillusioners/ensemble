# Plane Integration Revival — Final Gate Verification

- **Branch**: `feature/plane-integration-revival` @ `10ab7f18c2ebf4d0c895d185ae4d4a8992e3c9e7` (base `67b51e17`), pinned and held by every worker; worktree porcelain-clean throughout; zero repo writes, zero commits by gate workers.
- **Date**: 2026-09-20 (UTC). **Gate**: tester instance `12533058-68e9-4f86-8f76-80bcdf83ebda`, 10 worker dispatches (4 pack + 5 live + 1 root-cause analysis), all evidence-dense reports adjudicated.
- **Mission**: pre-merge final gate; 5 success criteria + kill-switch legs; Part A pytest suites, Part B live second-daemon proof (disposable PG14 :15432, daemon :8180, never touching 8079/8088, ensemble_prod read-only).

## Per-Criterion Verdict Table

| # | Criterion | Verdict | Blocking cause |
|---|---|---|---|
| 1 | MCP session health: zero `Failed to create session for 'plane'` post-boot; session ESTABLISHED | ✅ **PASS** | — |
| 2 | MCP E2E: agent creates/reads/deletes Plane task via plane_* tools | ❌ **FAIL** | **D1** — plane_* tools invisible to agent runtime (pre-existing seam) |
| 3 | REST sync live: 200 `status=linked` + `plane_project_id`, Plane-side exists, 404, no-dup | ❌ **FAIL** (core leg) | **D3** — `create_project` payload missing Plane-required `identifier` → 400 → `status="error"` (branch-caused). Legs 404/no-dup/no-artifact PASS |
| 4 | PM agent: RUNNING + trivial task + plane_* tools listed | ❌ **FAIL** (sub-criterion) | Liveness ✅ (RUNNING→COMPLETED in ~8s); plane_* ❌ — same **D1** |
| 5 | Re-drive mechanism: error-state row → boot sweep → linked/error-with-retry | ✅ **PASS** (conditional) | Adopt-path → `linked` proven with exact `plane_project_id` match; non-match → error-with-retry (attempt 0→1, D3's 400 recorded). Create-path linked blocked by D3 |
| KS | Kill-switches `PLANE_SYNC_ENABLED=false` / `PLANE_MCP_ENABLED=false` | ⚠️ **SPLIT** — SYNC leg ✅ PASS / MCP leg ❌ FAIL | **D5** — MCP kill-switch partial coverage: `eager_warm_schemas` ignores it and warms the pre-existing DB `plane` row (177 tools primed with the switch OFF) |

**Overall: ❌ NOT READY for merge as-is** — Criteria 2/4 fail on D1 (pre-existing at `latest`, NOT branch-caused), Criteria 3 core leg fails on D3 (branch-caused, critical), MCP kill-switch leg fails on D5 (activation-safety valve partial). All Part A suites green with zero branch-caused failures. Merge adjudication belongs to the leader; defect register + activation forecast below.

## Diagnosis-Conflict Resolution (from Check 1)

**Phase 1 quote-poisoning diagnosis CONFIRMED; Phase 2's 401 `invalid_token` verdict superseded — it was contamination from the poisoned row value.**
- The daemon process inherited `PLANE_MCP_*` env vars with literal surrounding quotes; config-layer sanitizers (`daemon/mcp/config.py:64-131` — `_strip_wrapping_quotes`, `_sanitize_quoted_url`, `_sanitize_quoted_headers`) stripped them before persisting to `mcp_servers` and before session creation.
- Persisted row (disposable PG, SELECT): url `https://mcp.ensem.dev/plane/http/api-key/mcp` (unquoted), `Authorization: Bearer plane_api_…` (unquoted) — accepted: **177 tools primed**, protocol `2025-11-25`, `POST /api/mcp-servers/test-connection` → `success=true, tools_count=177`.
- The key itself is valid (`plan…6f2` redacted form). Zero `Failed to create session for 'plane'` post-boot (boot baseline + time-bracketed greps).
- Corroboration: REST path with the same key (clean, X-Api-Key) returned 201 direct-create → key + workspace valid on the REST host too.

## Defect Register (report-only; no fixes applied per mission constraints)

### D1 🔴 CRITICAL — plane_* MCP tools invisible to agent instances (pre-existing at `latest`; NOT branch-caused)
- **Mechanism (confirmed by source trace)**: `daemon/tools/instance.py:329-338` dynamic MCP category expansion matches only `name.startswith("mcp_")`; the plane server's `tool_name_prefix="plane"` (`daemon/mcp/builtin_servers/plane.py:159`) never expands. PM's `allow=["plane", …]` falls through the literal-name branch (`:351-357` → `allowed_tools.add("plane")`), so every `plane_*` tool is dropped at `:4946` (`tool_name in allowed_tools`). `tool_categories["plane"]` is `[]` because `daemon/tools/plane_tools.py` is a 16-line stub (tools are runtime-created by `create_lazy_mcp_tools`). `DYNAMIC_TOOL_PREFIXES={"plane_"}` (`daemon/tools/_tool_registry.py:116-118`) is validation-only and never feeds the filter.
- **Attribution**: `git log 67b51e17..HEAD -- daemon/tools/instance.py` = empty; block authored `3ec7884e` (2026-05-17, MCP Phase 2), plane server added later in `560c2e90`. Exists on `latest`; every daemon since `560c2e90` has a silently tool-less PM agent.
- **Live proof**: two independent PM instances (`d002dfe4…`, `4e37e925…`) report zero plane_* tools; API `mcp_tool_names=null`; daemon-side 177-179 schemas loaded ("Lazy-loaded 179 MCP tool schemas from 2 server(s)").
- **Escape hatches**: NONE viable config-only (enumerating all 177 tool names in `tools.allow` works but is brittle; empty-allow, `"mcp"` allow, `PLANE_MCP_ENABLED=false` all fail).
- **Coverage gap**: `TestResolveToolFilterPlaneVsMcp` pre-populates `tool_categories` (diverges from production registry `{"plane": []}`) — passes while production fails. Planning TODO at `.agents/shared/planning/project-manager-agent/plane-mcp-architecture.md:329` was never implemented.
- **Smallest fix direction**: generalize the `:329-338` expansion to consume `DYNAMIC_TOOL_PREFIXES` (single loop; future-proof for any `tool_name_prefix` server); pair with a real-meta.json `_apply_tool_filter` test. Second follow-up: empty-allow branch should also consume the prefixes.

### D3 🔴 CRITICAL — REST `create_project` payload missing required `identifier` (branch-caused)
- `daemon/clients/plane_http_client.py:331-363` sends only `{name, description}`; Plane requires `identifier` → HTTP 400 `{"identifier":["This field is required."]}` → sync endpoint returns HTTP 200 with `status="error"`, `plane_project_id=null`. Plane also rejects hyphens in project names (separate 400 on the `ens-sync-test-<ts>` name class).
- **Blast radius**: the create path can NEVER reach `linked`. At activation, ~3/21 prod rows name-match existing Plane projects (adopt→linked); ~18/21 stay error-with-retry until D3 is fixed. No destructive Plane writes (adopt-before-create discipline held — verified live).
- Legs that DID pass: 404 bogus id (structured error envelope), no-dup second POST (attempt count incremented, no duplicate create; Plane-side count unchanged), cleanup (direct REST DELETE 204; final Plane count = 3 originals).

### D2 🟠 IMPORTANT — REST-path env quote-leak (same class P1 fixed for MCP; unsanitized on REST)
- `daemon/clients/plane_http_client.py:94-96` `_env()` only whitespace-strips; quoted `.env` values (e.g. `PLANE_MCP_WORKSPACE_SLUG="nea"`) interpolate literal quotes into the REST URL → Plane 403. Boot-recipe corollary (Defect A in B3's report): first boot lacked `PLANE_API_KEY` in the process env entirely (recipe gap, not code) → watchdog "not configured".

### D4 🟢 OBSERVATION — transient `Connection closed` on the test-connection seam
- Observed only inside another verifier's restart window; settled-daemon re-check: **3/3 `success=true tools_count=177`**. Not filed as a standalone defect; consistent with the known spawn-cadence standing risk (connection_manager.py:88 class).

## Part A — Suite Results (all `uv run python -m pytest` from repo root, dual-layer timeouts, pin `10ab7f18`)

| Pack | Scope | Result | Notes |
|---|---|---|---|
| A1a plane suite | 5 plane test files (**dev said 6 — description drift**; the 6th diffs as `test_mcp_quote_sanitization.py`, MCP-quote scoped, run in A1b): `tests/integration/test_plane_sync_endpoint.py`, `tests/unit/test_plane_mcp.py`, `test_plane_sync.py`, `test_plane_sync_phase3.py`, `test_plane_sync_phase4.py` | ✅ **260/260** | 253P default addopts + 7P integration-marker re-run (`--override-ini="addopts=" -m integration`), 0F. Dev claim "~286" not reconcilable (260 actual collected) — flagged, not a test gap |
| A1b MCP quote/config | `tests/unit/test_mcp_quote_sanitization.py` + `tests/unit/test_mcp_config.py` | ✅ **67/67** | 38+29 exactly as claimed |
| A3 concurrency | `test/packs/concurrency_atomic_unit_test.sh` (13-file canonical) | ✅ **98P/74S/0F** | Exact parity with 2026-09-13 baseline — ensure.md Core #2 + #3 GREEN |
| A2 unit sweep | `tests/unit/` + `tests/api/test_plane_settings.py`, 5 chunks | ✅ **PASS — 0 branch-caused failures** | 12,780 collected → 12,642P/57F/58S/23E in ~6.6 min; every red classified |

**Known pre-existing reds — confirmed at HEAD by exact signature (no base checkout needed; mission's "verify, don't fix" honored):**
- `test_api_module_is_small` (`tests/unit/test_api_router_extraction.py:778`): `daemon/api.py has 2827 lines … assert 2827 < 1600` — matches expectation (2827@HEAD vs 2748@base), quarantined family lineage 2005→…→2827.
- Mock-rot errors: **23E** = 17E `test_builtin_mcp_servers.py` + 4E `test_context7_builtin.py` + 2E `test_webfetch_builtin.py` (`AttributeError: Mock object has no attribute 'service_tool'` at `daemon/manager.py:686`; attr drifted slash_commands→blueprint→service_tool). In the expected ~17-21 band. **Note**: `test_mcp_service.py` itself is GREEN — mission's family naming was broader than the actual rot site.
- Ledger-gap recommendation: `test_gaia_agent.py` ×3 (`Left contains one more item: 'service'` vs `agents/gaia/meta.json`) — pre-existing by construction (introducer `3e46576d`, branch touches neither file); recommend adding a QUARANTINE.md row (tools.allow-drift family).

## Part B — Live Verification Environment

- Disposable PG **14.22** on :15432 (`initdb -A trust`, db `ensemble_plane_verify`), daemon booted from the feature worktree on **:8180** (PORT env override per `dev.sh:88`/`start.py:69`), `ENSEMBLE_DATA_DIR`/`DAEMON_LOG_DIR` under `/tmp/plane-revival-verify/`. Boot lineage: PID 34869 → 37361 → 38378 (coordinated restarts only, each port-ownership-verified, zero non-terminal instances first). Prod (8079) was not running on this machine — constraint held vacuously; nothing on 8079/8088 was ever contacted. `ensemble_prod`: exactly ONE read-only SELECT (stuck-row count). Key material redacted `plan…6f2` throughout.

### Criteria 5 detail (re-drive mechanism — the activation ride for prod's stuck rows)
- Boot sweep first tick **T+3s** post-readyz: `{'considered': 3, 're_drove': 1, 'errors': 2, 'skipped_backoff': 0}` — fresh seed consumed budget, eligible.
- Project A `ens-retry-err-20260920T185848Z` (no Plane name-match): `error` → attempt 0→1, `plane_last_error="Plane client error 400: identifier required"` (D3 recorded) — error-with-retry behavior proven.
- Project B seeded with existing Plane project name `LLM Proxy`: **ADOPT → `plane_sync_state='linked'`, `plane_project_id=da18585b-a301-4e5d-b2cd-6dee6215504d`** — exact match to the Plane REST id. Terminal linked reached without the broken create path.
- Incidental: pre-existing stray `system_default` error row also re-driven (attempt 2→3) — sweep picks up legacy rows too.
- **Prod backlog (read-only)**: **21 rows** = entire project population (1 `error` + 20 with no plane metadata), 0 linked. Predicate: `(no plane_project_id row) AND (plane_sync_state ∈ {error, syncing} OR no plane_sync_state row)`.
- **Activation forecast**: ~3 rows adopt→linked immediately ('nea', 'Ensemble', 'LLM Proxy' name class); ~18 blocked by D3 until fixed; sync kill-switch (once B6-verified) is the safety valve.

## Kill-Switch Verification

**LEG 1 — `PLANE_SYNC_ENABLED=false`: ✅ PASS (fully effective)**
- `POST /api/plane/sync/<seed-A>` → **HTTP 503**, body `{"detail":{"error":"plane_disabled","project_id":"dfcd5686-…","message":"Plane integration disabled by kill-switch (PLANE_SYNC_ENABLED=false)…"}}`.
- Exactly ONE watchdog no-op line: `PlaneSyncWatchdogService: Plane sync disabled by kill-switch (PLANE_SYNC_ENABLED=false) — watchdog no-op`; zero watchdog tick lines post-boot.
- No error-state writes: after a 330s soak, psql after-state IDENTICAL to before-state for all three plane-sync rows (attempt counts + last_error unchanged); no new error/syncing rows.

**LEG 2 — `PLANE_MCP_ENABLED=false`: ❌ FAIL → D5**
- Expected "no plane session created": VIOLATED — plane still primed (177 tools) because `eager_warm_schemas` warms the pre-existing DB row regardless of the switch (mechanism in defect register).
- Lazy notice: kill-switch-specific count = 1 ✓, but total plane-disable notices = 2 (generic bootstrap `Builtin 'plane' skipped — missing environment configuration` adds one).

**Teardown + certification (B6)**
- Plane-side final: 3 original projects (`nea`, `Ensemble`, `LLM Proxy`), zero `ens-*` artifacts.
- Daemon stopped (final PID 39454), :8180 freed; PG stopped, :15432 freed; pgdat preserved under `/tmp/plane-revival-verify/pgdata` (stopped).
- Repo: HEAD still `10ab7f18…`, porcelain shows only this RESULTS file as untracked; zero gate-worker commits; `find -newermt` cross-check confirmed no repo files modified during the live window.

## ensure.md Status (Core, blast-radius scoped)

- [x] Critical #1 — no regressions in changed packs: all scoped packs PASS (A1a/A1b/A3 + branch-touched files green in sweep)
- [x] Critical #2 — deadlock/concurrency integrity: `concurrency_atomic_unit_test` PASS (98P/74S/0F, exact parity)
- [x] Critical #3 — no sync DB calls on event loop: same pack, thread-identity tests green
- [ ] Critical #4 — `dev.sh` `--timeout-graceful-shutdown 10` static grep: executed by the commit worker (below)
- Important #1 (await-callers grep): out of blast radius (branch does not touch those functions) — not run, noted
- Release Gate: out of scope for this feature gate (requires `./dev.sh` on 8079 — forbidden here); deferred-with-notice, consistent with prior gates

## Scope Decision

Mission-scoped gate (leader-defined): Part A targeted suites + timeboxed sweep (no full-suite census — not warranted; change set is plane-scoped, 23 files, and the sweep covered all of `tests/unit/` + plane settings); Part B live chain on the disposable second daemon. No scope reduction below mission spec; no expansion beyond it.

## Artifacts Left / Cleaned

- **Plane-side**: NONE — direct-created test project deleted (REST DELETE 204); adopt was metadata-only; final count = 3 originals. B6 re-certified: 3 originals (`nea`, `Ensemble`, `LLM Proxy`), zero `ens-*` artifacts.
- **Disposable env**: PG stopped, daemon stopped, ports freed by B6 teardown (this section finalized with B6).
- **Transcripts (kept)**: `/tmp/plane-revival-verify/b1-criteria1.md`, `b2-criteria2.md`, `b3-criteria3.md`, `b4-criteria4.md` (+ `c4-evidence/`), `b5-criteria5.md`, `b6-killswitch.md`, `daemon-stdout.log`, `data/logs/ensemble.log`.
- **Repo**: clean; single gate commit = this RESULTS file only.

## Worker Roster

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
ginals. (B6 re-certifies.)
- **Disposable env**: torn down — daemon stopped (final PID 39454), PG stopped, :8180/:15432 freed; pgdat + transcripts preserved under `/tmp/plane-revival-verify/` (stopped state).
- **Transcripts (kept)**: `/tmp/plane-revival-verify/b1-criteria1.md`, `b2-criteria2.md`, `b3-criteria3.md`, `b4-criteria4.md` (+ `c4-evidence/`), `b5-criteria5.md`, `b6-killswitch.md`, `daemon-stdout.log`, `data/logs/ensemble.log`.
- **Repo**: clean; single gate commit = this RESULTS file only.

## Worker Roster

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
