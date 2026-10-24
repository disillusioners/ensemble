# TEST GATE — cu builtin MCP server removal (`feature/remove-cu-builtin`)

- **Branch:** `feature/remove-cu-builtin` @ `c5b8d42c1` (full `c5b8d42c1b830bf83ff76b8ec1dccbadc263f4bd`), 2 commits over base
- **Base:** `d4ecdbd3da15149effefd5fe812f2f30208140db` (= origin/latest at worktree creation; verified ancestor via merge-base)
- **Worktree:** `/Users/nguyenminhkha/All/Code/opensource-projects/agents-ensemble-wt-remove-cu-builtin` (giter-managed; KV claim `wt.claim.remove-cu-builtin`)
- **Gate date:** 2026-10-24 (dispatched ~06:35Z, packs ran 07:19–07:25Z)
- **Gate executor:** Tester agent (test leader) + 15 dispatched worker instances (recon, 10 pack workers w/ `test-pack-execution`, 3 analysis workers, 1 A/B worker)
- **Code freeze:** held throughout — zero code/test/doc modifications by the gate; the only file written by the tester is this evidence file. No daemon ops, no DB access by the gate, no push, main checkout untouched.
- **FINAL VERDICT: ✅ SHIP — zero functional blockers; zero branch-caused test failures (47/47 red nodes A/B-proven pre-existing at base); 2 important doc-truth defects (D-1, D-2) listed as strongly-recommended pre-merge fixes (§5, both ≤15-line prose edits); full adjudication in §9.**

---

## 0. Mission

Prove the removal of the daemon-side `computer-use` (cu) builtin MCP server is **complete, correct, and breaks NOTHING** for the surviving surfaces (three registered builtins webfetch/context7/plane + every generic mechanism). Close-out shape: full affected-family run on the branch, A/B base-vs-branch proof for every failure, invariant verification (a–f), mock-quality review of the ported gate tests, doc-truth verification of the 5 changed docs + 3 tester docs, FE-zero confirmation.

## 0.1 Change under test (recon-verified diff inventory)

29 files, `+1100/−2809`. Production: `daemon/mcp/builtin_servers/computer_use.py` (296 lines, DELETED), `daemon/tools/cu_tools.py` (1 line, DELETED), `daemon/mcp/builtin_servers/__init__.py` (cu unregistered), `daemon/tools/_tool_registry.py` (`cu_` prefix, `cu` category, `cu` CATEGORY_MODULES row removed), `daemon/routers/mcp_servers.py` (D7 guard `_block_test_connection_when_builtin_unavailable` + single call site removed, `−131`), `daemon/services/mcp_service.py` + `daemon/mcp/tool_adapter.py` (comment/docstring-only truth fixes), `agents/ari/meta.json` + `agents/leader/meta.json` (`cu` dropped from tools.allow, `mcp_rpc` retained). Tests: `tests/unit/test_computer_use_mcp.py` (1726 lines, DELETED), `tests/unit/test_mcp_builtin_availability_gate.py` (862 lines, NEW — generic half ported + S1 skip-branch test), 9 privileged-set pin files (16→15), `tests/integration/mcps_gateway/tls_harness.py` (disable tuple `("CONTEXT7","COMPUTER_USE","WEBFETCH","PLANE")` → `("CONTEXT7","WEBFETCH","PLANE")`). Docs: 4 files (stub, gateway count fix, remote-hands status note, idle-reap fix) + CHANGELOG `### Removed` entry + 3 tester-doc edits (MOCK_TESTS/PACKS RETIRED notes, QUARANTINE row cell update).

**Frontend: ZERO files in the diff** (`git diff --name-only d4ecdbd3d..HEAD -- frontend/ | wc -l` → `0`). **No web automation test is applicable** — the cu builtin had no FE surface; the FE/UI surface is the remote-hands gateway path, untouched by this branch.

## 0.2 Method / environment

- Runner: `timeout <cap> ./scripts/run_tests_scrubbed.sh <args>` — the repo's sanctioned runner (unsets `POSTGRES_*`, `DATABASE_URL_POSTGRES`, bare libpq vars `PGHOST/PGPORT/PGDATABASE/PGUSER/PGPASSWORD/PGPASSFILE/PGSSLMODE/PGSERVICE/PGSERVICEFILE`, fail-closed exit-78 leak check, then `exec .venv/bin/pytest` from repo root).
- Dual-layer timeout: outer `timeout 240/300` (command-level) + pyproject per-test `timeout = 30` / `timeout_method = "thread"` (inner, never disabled).
- Default addopts `-m 'not integration and not postgres and not slow and not conformance'`; integration-marked files run with `--override-ini="addopts=" -m integration` (the retired cu pack's own pattern).
- Worktree-local `.venv` (Python 3.13.3, pytest-timeout present). HEAD re-verified `c5b8d42c1` before and after every pack.

### Scope decision (blast radius)

Full affected-family coverage per the gate mandate (10 packs, 55 distinct test files, ~1576 executed tests). Two documented scoping calls:
1. **Broad `_tool_category` consumer set (49 files) not blanket-run** — the direct constant/mechanism pins (all 24 `PRIVILEGED_TOOL_CATEGORIES`-referencing files, all 3 `DYNAMIC_TOOL_PREFIXES` files, all 8 `daemon.mcp.tool_adapter` importers, all 6 routers/mcp_servers test files, the stamp symbol's only direct test file) ARE covered across packs 1–7; the remaining `_tool_category` consumers are indirect surface already represented by the pin equivalence (the mechanism minus `cu`).
2. **`tests/unit/probe_designer_od_lane_binding.py` excluded** — grep-matched the preload family but is not pytest-collectible (`--collect-only` → `no tests collected`); it is a probe script, not a test file.

---

## 1. FULL AFFECTED FAMILIES — branch-leg results (10 packs, all complete)

| # | Pack (worker) | Files | Verbatim summary | Verdict |
|---|---|---|---|---|
| 1 | `cu_pins_unit_test` (f4fb247b) | 9 edited pin files | A: `170 passed in 14.60s` · B (override+integration): `9 passed in 1.42s` | ✅ **PASS 179/179** — 16→15 pin consistent in all 9 files |
| 2 | `cu_gate_new_unit_test` (c613a62d) | NEW `test_mcp_builtin_availability_gate.py` | `13 passed in 0.33s` | ✅ **PASS 13/13** |
| 3 | `cu_builtin_core_unit_test` (4793911c) | builtin+context7+webfetch+infra | `139 passed, 21 errors in 5.21s` | ⚠️ FAIL-by-quarantine — **exactly the 21 documented errors** (§2) |
| 4 | `cu_builtin_mcp_service_unit_test` (aedd59d9) | mcp_service+warmup_pool+quote_sanitize | `180 passed in 63.10s` | ✅ **PASS 180/180** |
| 5 | `cu_builtin_plane_unit_test` (c43565b2) | plane_domain_access+plane_mcp+project_manager | `2 failed, 157 passed in 1.46s` | ⚠️ FAIL-by-quarantine — exactly the 2 ledgered project_manager prompt-debt rows |
| 6 | `cu_preload_unit_test` (86803689) | 5 unit + lifecycle (+probe excluded) | A: `65 passed in 6.08s` · B2 (override+integration): `13 passed in 0.59s` | ✅ **PASS 78/78** |
| 7 | `cu_routers_unit_test` (6485dfc0) | crud+test_connection+configure_builtin+kms×2 | `15 failed, 209 passed in 6.15s` | ⚠️ FAIL-by-quarantine — exactly the ledgered `configure_builtin ×15` family; `/test-connection` 71/71 green, CRUD 81/81 green |
| 8 | `cu_registry_adapter_unit_test` (bfc02a77) | 14 registry/adapter files + maintenancer_e2e | A: `422 passed in 17.60s` · B1: `6 failed, 13 passed in 0.69s` | ⚠️ FAIL-by-quarantine — A fully green 422/422; B1 = exactly the ledgered memory.md ×6 family |
| 9 | `cu_gateway_integration_test` (d99c160e) | `tests/integration/mcps_gateway/` (B: override+integration) | `38 passed in 76.71s` | ✅ **PASS 38/38** — tls_harness 3-survivor tuple green; no port leaks; protected ports untouched |
| 10 | `cu_docs_meta_unit_test` (e73a076c) | 3 doc-pin + 4 meta files | `3 failed, 201 passed in 6.28s` | ⚠️ FAIL — 55-test docs suite ALL GREEN (developer's "55 passed" confirmed); 1 phase8-changelog node + 2 agents_api nodes → §2 adjudication |

**Branch-leg totals: 1529 passed / 26 failed / 21 errors / 0 timeouts** across 10 packs. Every pack finished far under its cap (slowest: gateway 76.71s vs 300s).

### Pack 3 detail — the 21 errors (all one signature)

`AttributeError: Mock object has no attribute 'service_tool'` — fixture `instance_manager_with_repo` (`test_builtin_mcp_servers.py:368` / `test_context7_builtin.py:340`) → `InstanceManager(mock_config)` → `daemon/manager.py:997` (`config.services.service_tool.max_concurrent`). Setup-phase only; bodies never execute. Class mix: TestBootstrap ×5, TestBootstrapDisableEnable ×6, TestBootstrapSkipsUnavailable ×2, TestOrphanedBuiltinCleanup ×4 (all in `test_builtin_mcp_servers.py`, 17 total) + TestContext7Bootstrap ×4 (`test_context7_builtin.py`) = **21 — matches the QUARANTINE ledger row (2026-09-04/10-02/10-16) class-mix exactly**. Full 21 node-ids recorded by worker 4793911c and passed to the A/B leg. `test_webfetch_builtin.py` + `test_infra_bootstrap.py`: 47 passed / 0 errors — the older "blueprint" family did NOT appear.

### Pack 7 detail — the 15 failures

All in `tests/unit/test_configure_builtin_idempotency.py` (17 collected: 15F/2P). Root cause (read-only corroborated): `registry.get_by_name("opendesign")` → None → configure-builtin 404 (`Built-in server template 'opendesign' not found`) → cascading `NoneType.id/instance_metadata/config` AttributeErrors + 2 audit-file-missing nodes. `opendesign` was never a registered builtin at base either (base registry = webfetch, context7, plane, computer_use). The branch touched neither the template registry nor this test file. Stale `test_mcp_servers.db` absent → the location-attributed crud family did not appear (CRUD 81/81 green).

### Pack 10 detail — the 3 failures

1. `tests/test_phase8_docs_gate.py::test_changelog_has_unreleased_section_naming_commission` — `CHANGELOG [Unreleased] section must name the resource-cost-control commission`; the branch's `[Unreleased] — 2026-10-24` section (cu-removal entry) does not contain the string. **Key adjudication node → §2.**
2. `tests/test_agents_api.py::test_list_agents_success` — `assert 39 == 1` (fixture expected 1 agent; enumerated the full 39-agent roster).
3. `tests/test_agents_api.py::test_list_agents_empty_directory` — expected `[]`, got the 39-agent roster (first extra `agents/approver`).
The other 5 files in the pack: `test_mcps_gateway_docs.py` **55/55** (the developer's "docs test with 55 passed" — CONFIRMED), `test_docs_cleanup_truth_split.py` 4/4, `test_registry.py` 102/102 (the `validate_tool_configs` seam is clean on the edited ari/leader metas), `test_frozen_tool_name_discovery.py` 7/7, `test_ari_worker_integration.py` 13/13.

### Additional family verifications (recon + invariant legs)

- **Removed-helper check:** `grep -rn "_block_test_connection_when_builtin_unavailable" tests/ daemon/ agents/ scripts/ frontend/` → **ZERO hits anywhere**. The deleted D7 guard had tests only in the deleted file; no stale reference remains.
- **Import sanity:** `import daemon.api, daemon.manager, daemon.routers, daemon.routers.mcp_servers` → `import-chain OK` (EXIT=0; the duplicated config/banner block in output is the documented benign double `create_app()`).

---

## 2. A/B BASE-VS-BRANCH PROOF — **47/47 nodes PRE-EXISTING; BRANCH-CAUSED: none; base-only failures: none** (worker 9538184b, scratch worktree /tmp/ens-base-cu-ab @ d4ecdbd3d, removed after)

**Setup evidence:** `git worktree add --detach /tmp/ens-base-cu-ab d4ecdbd3d` → 12880 files, HEAD `d4ecdbd3da15149effefd5fe812f2f30208140db`, porcelain EMPTY; `uv sync --frozen` exit 0; **IMPORT-PROOF** `import daemon.manager` → `/private/tmp/ens-base-cu-ab/daemon/manager.py` (inside base tree — no leaking import root). All invocations via the base worktree's own `./scripts/run_tests_scrubbed.sh`, `timeout 240`, same invocation shape as the branch leg. No `.env` needed (no env-shaped failures).

| Group | Files | Base verbatim summary | Branch ledger | Verdict |
|---|---|---|---|---|
| 1 | test_builtin_mcp_servers.py + test_context7_builtin.py | `92 passed, 21 errors in 4.91s` | 21 errors | **21/21 PRE-EXISTING** — node-id-for-node-id identical, same signature `AttributeError: Mock object has no attribute 'service_tool'` @ `daemon/manager.py:997` (same line both refs). **21-claim: branch 21 == base 21, set-difference ∅.** Base 92P matches the QUARANTINE 2026-10-16 reference (92P/0F+21E). |
| 2 | test_configure_builtin_idempotency.py | `15 failed, 2 passed in 2.82s` | 15 failures | **15/15 PRE-EXISTING** — base failing set = exactly the branch's 15 (the documented ×15↔16 batch-ordering swap did NOT materialize; no swap member at either ref). Same family signature (opendesign template 404 → NoneType cascades + 2 audit-file-missing nodes). |
| 3 | test_maintenancer_end_to_end.py | `6 failed, 13 passed in 0.64s` | 6 failures | **6/6 PRE-EXISTING** — identical `agents/maintenancer/memory.md missing` @ :147 (base path `/private/tmp/ens-base-cu-ab/...` verbatim; file deleted by archive sweep 3c307e8d4, ancestor of base). |
| 4 | test_project_manager_agent.py | `2 failed, 63 passed in 0.61s` | 2 failures | **2/2 PRE-EXISTING** — byte-identical signatures (`soul.md must cross-reference rule.md` / `workflow.md must reference soul.md for output templates`); standing QUARANTINE row 2026-10-07. |
| 5 | test_phase8_docs_gate.py | `1 failed, 8 passed in 0.15s` | 1 failure | **1/1 PRE-EXISTING — KEY ADJUDICATION RESOLVED.** Base fails with the IDENTICAL missing-naming signature: `AssertionError: CHANGELOG [Unreleased] section must name the resource-cost-control commission` / `assert 'resource-cost-control' in '## [Unreleased] — 2026-09-22\n\n### Changed…'` @ `tests/test_phase8_docs_gate.py:100`. Forensics: the test slices the FIRST `## [Unreleased]` heading → next `## [` heading; base has THREE [Unreleased] sections (stale 2026-09-22 SELF_ENV entry at :642 is first; the `resource-cost-control` string exists at base only inside released `## [0.26.x]` sections the test never reads). At branch the sliced section is the new cu-removal entry at top — failure MODE identical on both refs ("first [Unreleased] section does not name resource-cost-control"). The branch did NOT break a passing test; this is pre-existing test rot (the gate inspects only the first of N Unreleased sections). Provenance nuance recorded; does not flip the verdict. |
| 6 | test_agents_api.py | `2 failed, 12 passed in 1.41s` | 2 failures | **2/2 PRE-EXISTING** — identical fixture-isolation rot (39-agent roster enumerated: `assert 39 == 1` / `Left contains 39 more items, first extra agents/approver`, base paths verbatim). The ari/leader meta content edits are irrelevant to these nodes. |

**Cross-check:** base leg 21E + 15F + 6F + 2F + 1F + 2F = 47 nodes = branch leg 26F + 21E = 47. **Set-difference ∅ in BOTH directions** (no base-only failures either — nothing that passed on branch fails at base).

**Cleanup proof (executed in mandated order):** feature HEAD re-verified `c5b8d42c1` → `git worktree remove --force /tmp/ens-base-cu-ab` OK + prune no-op → `git worktree list`: main + 6 pre-existing linked worktrees, no /tmp residue → feature `git status --porcelain`: the 2 known dirty files + the untracked evidence file (this gate's deliverable; the A/B leg itself wrote nothing into the feature worktree).

---

## 3. INVARIANT VERIFICATION (a–f) + import sanity — **7/7 PASS** (worker ff4c4f8c)

**(a) PRIVILEGED_TOOL_CATEGORIES — PASS.** Verbatim probe output:
```
['board', 'board-link', 'ens-db', 'ens-env', 'instance_ops', 'instance_read', 'mcp_rpc', 'secrets', 'self-alarm', 'settings', 'system-log', 'system_upgrade', 'view-views', 'view-write', 'wedge-capture']
len: 15
trio: True
cu_absent: True
mcp_rpc_present: True
set_equal_expected: True
missing: []
extra: []
```
Exactly 15 members, set-equal to the required 15; security trio `{system_upgrade, system-log, ens-db}` present exactly as-is; `cu` ABSENT; `mcp_rpc` PRESENT. Source-parse cross-check: base had 16 incl. `cu`, prefixes `['plane_','cu_']` → HEAD 15, `['plane_']`.

**(b) DYNAMIC_TOOL_PREFIXES — PASS.** `['plane_']` / `D == frozenset({"plane_"}) → True` / type frozenset.

**(c) Builtin registry — PASS.** Enumerated via real `get_registry()`/`_registry`: `registered_names: ['webfetch', 'context7', 'plane']`, count 3, `computer_use_present: False`, set-equal expected, no missing/extra. **Actual name strings: `webfetch`, `context7`, `plane`.** (Non-gate note: `webfetch` showed `is_builtin_disabled: True` in the probe shell — env artifact `MCP_DISABLE_BUILT_IN_WEBFETCH`, not branch state.)

**(d) CATEGORY_MODULES + deleted modules — PASS.** `cu_in_keys: False`, `cu_tools_refs: []`, 54 keys (full sorted list in worker report; no `cu`). `import daemon.tools.cu_tools` → `ModuleNotFoundError` (GOOD); `import daemon.mcp.builtin_servers.computer_use` → `ModuleNotFoundError` (GOOD); both files absent from disk.

**(e) Agent metas — PASS.** `agents/ari/meta.json` valid JSON, tools.allow = 32 entries, `cu_absent: True`, `mcp_rpc_present: True`. `agents/leader/meta.json` valid JSON, tools.allow = 24 entries, `cu_absent: True`, `mcp_rpc_present: True`. Full allow lists quoted verbatim in worker report. Repo-wide sweep: **no agent anywhere grants `cu`** (`grep -rn '"cu"' agents/*/meta.json` → empty).

**(f) Residual cu-reference census — PASS (0 UNSANCTIONED).** Total 124 hits repo-wide (excl. node_modules/.git/.venv/dist/__pycache__/.pytest_cache):
- **16 hits in `daemon/ agents/ tests/ scripts/`** — ALL comment/docstring/markdown prose, zero live code. Includes the 4 sanctioned history comments (`daemon/tools/_tool_registry.py:543`, `daemon/sources/registry.py:800`, `daemon/services/mcp_service.py:754`, `agents/gaia/workflow.md:32` — each verified in context) + 12 test-side prose hits (docstrings/comments/COVERAGE.md rows — no imports, no runtime string keys).
- **Decisive live-code audit:** `grep -rn -E "['\"]cu['\"]|['\"]cu_|\"cu_" daemon/ --include="*.py" | grep -v -E ":[0-9]+: *#"` → **0 non-comment cu string literals in daemon/**.
- **frontend/: 0 hits** (spec-filtered and broadened sweeps).
- **Bundled mermaid (path correction):** lives at `daemon/services/live_views/assets/mermaid/mermaid.min.js` (NOT frontend/) — 2 coincidental minified-JS constant tokens (`"cu"`, `"Cu"` on line 609), zero semantic hits. Sanctioned.
- **107 hits outside the 5 dirs** — all sanctioned (CHANGELOG historical block ×12; `.agents/tester/` ×49; `.agents/shared/planning/` ×19; docs/investigations ×1; the historical stub itself ×13) or ratifiable-replacement-prose (see below).
- **Ratification (dispatcher adjudication):** 3 doc locations outside the task's enumerated sanctioned set — `docs/mcps-client-remote-hands.md` (11), `docs/mcps-gateway.md` (1, mermaid diagram node for the wrapper-side cu server), `docs/runbooks/resource-cost-control.md` (1, "the computer-use builtin it also covered was removed 2026-10-24"). All are markdown prose describing the **replacement** architecture / removal history in the very docs this gate's doc-truth section audits (§5); zero live code. **Ratified as sanctioned-by-design** — the replacement-surface docs necessarily reference the superseded cu server.

**(g) Import sanity — PASS.** `import daemon.api, daemon.manager, daemon.routers, daemon.routers.mcp_servers` → `import-chain OK`, EXIT=0, no traceback.

---

## 4. MOCK QUALITY (ported `test_mcp_builtin_availability_gate.py`) — **SOUND WITH DEFECTS (non-blocking)** (worker d73c4c65)

**Stub fidelity: HIGH.** `_UnavailableStubBuiltin` subclasses the REAL `BuiltinServerDefinition` ABC (not a hand-rolled fake), implements all abstract members, and — the critical choice — overrides `is_available()` as a **sync `@classmethod` returning False**, exactly matching `base.py:126`. Had it been async, `bool(coroutine) → True` and the gate-skip test would fail loudly. The fixture registers into the **real registry singleton** via `get_registry().register()/unregister()` — the same seam production reads (`mcp_service.py:1118-1120`). No mock-past-the-seam in the fixture wiring.

**Four-edge coverage (mission-required):**

| Edge | Verdict | Location |
|---|---|---|
| gate exception isolation | **PARTIAL** — loop-site net COVERED; in-helper net UNPINNED (D1) | `test_gate_exception_isolates_to_one_server` (:764) — asserts real isolation (survivor reaches session cache, failing server absent, WARNING names the server) |
| non-builtin bypass | COVERED | `test_gate_bypassed_for_non_builtin_server` (:633) + `test_mcp_service.py:1381` (eager lane) |
| healthy-builtin bypass | COVERED | `test_gate_bypassed_for_healthy_builtin` (:686) — uses the REAL plane definition with genuine env → real `is_available()` True + `test_mcp_service.py:1337` |
| unavailable-builtin skip | COVERED | `test_gate_skips_preload_for_unavailable_builtin` (:541, added by c5b8d42c1) — asserts all 4 S1 consequences; schema mock armed to raise (gate leak = loud failure) |

Note: `test_builtin_mcp_servers.py::TestBootstrapSkipsUnavailable` ×2 are bootstrap-layer tests whose bodies never execute (the 21-error family) — correctly NOT counted toward edge coverage.

**Port fidelity:** 10 generic tests ported (6 verbatim, 3 near-verbatim, 1 **strengthened**: the skip test's `assert_not_awaited` + exact-one-log + session-cache assertion vs the old weaker `assert_not_called`/`>=1`). 40 cu-bound tests dropped (correct — subject deleted). One generic-but-cu-named loss: `test_kill_switch_on_control_cu_tools_loaded`'s full happy-path assertions (session-cache `cache`+`lock` sub-keys + surfaced lazy tool) — see D3.

**Bug-class audit:** all 12 patch/mock targets verified live (exist + actually called by the production path); async/sync fidelity correct on every mock (`get_schemas_for_server` awaited→AsyncMock; `create_lazy_mcp_tools` sync→MagicMock; `is_available` sync→sync classmethod; `_fake_gate` plain function, not AsyncMock); zero tautological asserts (constant pins reference live production symbols); mutation analysis: 4/4 gate tests fail loudly if the gate is deleted/inverted — except D1 below.

**Defects (all non-blocking; current production code is correct on every path):**
- **D1 (MEDIUM, pre-existing — inherited verbatim from the deleted original):** `patch.object(McpService, "_is_builtin_server_available", _fake_gate)` mocks past the seam — the in-helper exception net (`mcp_service.py:1127-1146`: `is_available()` raising → caught in-helper → treated as unavailable) is unpinned anywhere in the repo (deleting it leaves the suite green). The port preserved the pattern instead of fixing it; the class docstring over-claims.
- **D2 (MEDIUM, pre-existing since 2026-09-20, NOT this PR):** the sibling `eager_warm_schemas` gate has NO exception isolation (a raising `is_available()` aborts the whole eager warm — fail-CLOSED asymmetry) and zero coverage for a raising definition.
- **D3 (LOW-MED, port-time narrowing):** healthy-builtin happy-path assertions dropped (session-cache `cache`/`lock` sub-keys + surfaced tool via `get_mcp_tools`).
- **D4 (LOW, port-time narrowing):** skip-log assertion dropped the server-name pin (matches only "skipping unavailable builtin").
- **D5 (LOW, inherited):** the one-shot log flag never exercised with two unavailable servers.
- **D6/D7 (INFO):** stub doesn't override `get_base_config` (harmless — preload never calls it); registry-fixture scope asymmetry vs `test_builtin_mcp_servers.py` (both correct for scope).

**Recommended follow-up (not a gate condition):** add a `_RaisingStubBuiltin` exercising the real helper's in-helper net for both gates (fixes D1+D2); restore D3/D4 assertions.

---

## 5. DOC-TRUTH VERIFICATION — **named objects ALL TRUE; 1 adjacent defect + 1 branch-introduced stale comment** (worker 07fa2e72)

Verdict per named object (78 individual assertions checked):
- `docs/mcp-computer-use-server.md` (stub) — **ALL TRUE (17/17)**: all 4 named dead env vars have zero live readers (`COMPUTER_USE_MCP_ENABLED`, `MCP_DISABLE_BUILT_IN_COMPUTER_USE`, `CU_NPX_PATH`, `COMPUTER_USE_PROFILE` — only a daemon comment mentions one, no code); gaia link resolves; all pointers exist; the 440-line history reference (`git show 0774f3fe1:…`) exact; all six CU_* constants + the `@zavora-ai/computer-use-mcp@7.6.1` pin verified in history; orphan-sweep re-deactivation claim verified at `manager.py:2722-2760`. (DB row state = unverifiable-by-construction, no DB access permitted.)
- `CHANGELOG.md` `### Removed` entry — **TRUE on every tree-checkable claim (15/15)**: 296-line file deleted, registry unregistered, 16→15, `{"plane_"}`, CATEGORY_MODULES row dropped, ari/leader metas, D7 guard + single call site, dead env vars, docs stubbed, 1726-line test deleted, generic half ported (all 4 named behaviour classes present), **nine pin files** (5 exact-set + 4 len == 15 — verified 9 distinct files), tls_harness tuple. Loose-but-not-false: "S1 gate untouched" = code untouched, comments edited. DB note unverifiable-by-construction.
- `docs/mcps-gateway.md` count fix — **ALL TRUE**: "one of the opt-in-only categories" is correct and strictly more accurate than the old positional "15th entry of 15" (unordered frozenset); zero stale counts remain; all other numeric counts verified (4 tools, 3 read-only GETs, 9+5 error codes, 3 presence states); the doc-pin suite (`test_mcps_gateway_docs.py`) still pins the claims and passes 55/55.
- `docs/mcps-client-remote-hands.md` status note — **the added note's claims ALL TRUE** (removal, shipped replacement incl. all gateway modules + mcp_rpc tools, stub pointer, catalog-verbatim surfacing at `service.py:907-933`, session-deadline precedents in config, `CU_TOOL_CALL_TIMEOUT_SECONDS` dead). **BUT the file as a whole is DEFECTIVE (D-2, below).**
- `docs/runbooks/resource-cost-control.md` idle-reap fix — **ALL TRUE**: `MCP_POOL_IDLE_REAP_SECONDS` default 1800 (`config.py:2787`), `0` disables (`warmup_pool.py:616`), in-use connections never reaped (structural), the cu builtin was genuinely covered pre-removal (generic stdio reaper), removed 2026-10-24.
- Tester docs: `QUARANTINE.md` edit **ALL TRUE** (retired pack rows, both quarantined test files still present, re-run invocation correct, pre-existing row-splitting untouched). `PACKS.md` RETIRED note TRUE-minus-D-3 (8 rows deleted, zero dangling references — `cu_new_suite_unit_test`'s single remaining hit is inside the note itself; zero live `^| cu_` rows; deleted test file not referenced as runnable). `MOCK_TESTS.md` RETIRED note TRUE-minus-D-4 (both entries retired non-runnable; historical RESULTS file exists; LESSONS row exists).

**Defects:**
- **D-1 (🟠 branch-introduced, code comment):** `daemon/tools/_tool_registry.py:320` still says "count assertion (**16 today**)" — FALSE post-removal (15). Sits in a prose region that already carried pre-existing wrong counts (`:301` "EIGHT sites" → actually 9; `:319` "three" → actually four; `instance.py:6711` "five-pin" → actually nine) under an explicit "DERIVE THE PIN SET BY GREPPING THE TREE — do NOT trust this prose" disclaimer. **Fix: one-line update to 15** (optionally the sibling counts).
- **D-2 (🟠 branch-adjacent, doc):** `docs/mcps-client-remote-hands.md` — the new true STATUS UPDATE sits beneath a pre-existing banner ("**Nothing in this document exists in the codebase today**") and Status row ("**Proposed / not implemented**") that were already false at base (the gateway shipped in v0.27.5), plus 3 body claims the branch itself falsified (`:23` "cu builtin as built today", `:201` "the exact-pin match pattern the probe uses today" — the D7 guard this branch deleted, `:375` "`computer_use.py` — single source of truth today"). The file is now self-contradictory. **Fix: update banner + Status row to shipped state and annotate the three "today" lines.**
- **D-3 (🟢):** PACKS.md retirement note overstates ("rows targeted the cu surface, which is gone with it" — 5 of 8 rows invoked still-present, non-cu-specific files; the sibling QUARANTINE note says so correctly). Reword.
- **D-4 (🟢):** MOCK_TESTS.md note references "`EVIDENCE/` rows" — no cu EVIDENCE row exists (only LESSONS). Drop or name actual artifacts.
- **D-5 (🟢):** `daemon/services/mcp_service.py:754` comment still names `COMPUTER_USE_MCP_ENABLED=0` as the S1 example (comment-only; the var is dead). Optional cleanup.

---

## 6. WEB FRONTEND — **zero files, no web automation applicable**

`git diff --name-only d4ecdbd3d..HEAD -- frontend/ | wc -l` → `0`. The cu builtin had no FE surface (its FE/UI replacement is the remote-hands gateway, untouched). Invariant (f) frontend grep: **0 hits**. No web automation test is applicable.

---

## 7. WORKTREE DIRT ADJUDICATION (for the giter at finalize)

Two tracked files dirty at gate end, both test-execution side effects, **neither part of the branch's 29-file diff**:
1. `.agents/shared/planning/mcps-gateway-interop/p0-gate-evidence.md` (+182): +91 from a pre-gate 06:03Z p0-gate run (developer/giter residue) + +91 from this gate's gateway pack (`p0_gate.py` documented append-only harness behavior; records `## Attempt 1 — PASS`, all 40 gate assertions green incl. `plane-mcp-kill-switch-effective`). **Giter's call** — it is branch-relevant append-only gate evidence; keep or restore.
2. `.agents/shared/planning/designer-agent/install-audit.jsonl` (+2): this gate's builtin2 pack — `test_mcp_warmup_pool.py::TestKMSEnvResolution` calls the real `kms_lite.kms_request("opendesign", …)` twice (cwd-relative audit path); secret_ref-only records, in-memory KMS-Lite store, no plaintext. **Recommend restore-to-HEAD at finalize** (test pollution, not branch content).

All other pack runs left zero delta. HEAD `c5b8d42c1` never moved (verified before/after every pack). No push, no commit by the gate.

---

## 8. GAPS / FOLLOW-UPS (non-blocking)

- Mock-quality D1/D2 (unpinned in-helper exception net; eager-warm gate isolation) — pre-existing coverage holes; recommended `_RaisingStubBuiltin` follow-up.
- Mock-quality D3/D4/D5 (port-time assertion narrowing) — restore before PR close if convenient.
- Doc-truth D-1/D-2 (🟠) — see §5; D-3/D-4/D-5 (🟢).
- **Unledgered pre-existing failures:** the 3 docsmeta nodes (phase8 changelog ×1 + agents_api ×2) are A/B-proven pre-existing but are NOT in QUARANTINE.md (the other 44 red nodes are all ledgered standing families). Recommend ledgering them in a follow-up QUARANTINE.md pass (the gate's file-write constraint allowed only this evidence file). The phase8 gate is also rot-prone by design (inspects only the first of N `[Unreleased]` sections) — test-debt candidate.

---

## 9. FINAL VERDICT — ✅ SHIP

**The cu builtin removal is complete, correct, and breaks NOTHING for the surviving surfaces.**

| Gate obligation | Result |
|---|---|
| §1 Full affected families (10 packs, 55 test files, ~1576 executed) | ✅ 1529 passed / 26 failed / 21 errors / 0 timeouts — every red node in a documented standing family or A/B-adjudicated (below) |
| §2 A/B base proof (all 47 red nodes re-run at base d4ecdbd3d) | ✅ **47/47 PRE-EXISTING, set-difference ∅ both directions, zero branch-caused failures, zero base-only failures** |
| Developer's "21 pre-existing QUARANTINE errors" claim | ✅ **VERIFIED EXACTLY** — branch 21 == base 21 (node-id-for-node-id, class mix 5+6+2+4+4, identical `service_tool` signature @ manager.py:997 both refs); none are NEW |
| §3 Invariants a–f + import sanity | ✅ 7/7 PASS (15-member set exact, `{"plane_"}`, registry = webfetch/context7/plane, CATEGORY_MODULES 54 no-cu, metas clean, 0 unsanctioned residuals, import chain OK) |
| §4 Mock quality (TrueAuto rule) | ✅ SOUND — real-ABC subclass stub, real-registry-seam fixture, correct sync/async fidelity on every mock, all 4 mission edges covered; defects D1–D7 all non-blocking (pre-existing or port-time narrowing; current production code correct on every path) |
| §5 Doc truth (5 docs + 3 tester docs, 78 assertions) | ✅ named objects ALL TRUE; adjacent defects D-1/D-2 (🟠, below) + D-3/D-4/D-5 (🟢) |
| §6 Frontend | ✅ zero files in diff → no web automation applicable (stated explicitly) |

**Blockers: NONE.** Strongly-recommended pre-merge fixes (🟠 important, both ≤15-line prose edits, neither functional):
1. **D-1** — `daemon/tools/_tool_registry.py:320`: comment still says "count assertion (**16 today**)" — false post-removal (15). Branch-introduced falsification in a region already carrying pre-existing wrong counts under an explicit "do NOT trust this prose" disclaimer. One-line fix (optionally also `:301`/`:319` + `instance.py:6711`).
2. **D-2** — `docs/mcps-client-remote-hands.md`: the new (true) STATUS UPDATE sits beneath a pre-existing-false banner ("Nothing in this document exists in the codebase today") + Status row ("Proposed / not implemented"), plus 3 body claims this branch itself falsified (`:23` "cu builtin as built today", `:201` "the probe uses today" the deleted D7 guard, `:375` "`computer_use.py` — single source of truth today"). Update banner + Status row + annotate the three "today" lines.

**Worktree handoff to giter (finalize):** 2 dirty tracked files (both test-execution side effects, neither in the branch's 29-file diff — §7: `p0-gate-evidence.md` +182 [giter's call; branch-relevant append-only gate evidence] and `install-audit.jsonl` +2 [recommend restore-to-HEAD]); 1 untracked file = this evidence file (deliverable; commit per finalize flow). HEAD `c5b8d42c1` never moved through the entire gate. No push, no commits, no daemon ops, no DB access, main checkout untouched, no /tmp residue.

---

*Evidence file maintained by the Tester (test leader). FINALIZED 2026-10-24 — all sections complete; verdict §9.*
