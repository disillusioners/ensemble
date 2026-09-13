# Merge-Gate Verification — Feature #1 "spawn-time intelligence override"

- **Date**: 2026-09-14 (gate executed 2026-09-13 21:16–21:32 UTC)
- **Worktree**: `/Users/nguyenminhkha/All/Code/opensource-projects/agents-ensemble-sio`
- **Branch / commit under test**: `feature/spawn-intelligence-override` @ `ca86f493` (base `a904374e`, 7 commits)
- **Method**: verify-against-real-behavior; 5 parallel worker instances (census / tier-behavior / schema-doc / nudge / boot-smoke) + base-attribution follow-up; read-only gate (tree byte-stable at ca86f493 throughout)
- **Workers**: 345cdd2f (census+attribution), 116d2648 (tier 2-4), b22cef1d (schema 5), a641c8ae (nudge 6), 04f1c875 (boot 7)

## Tree pins (timestamped)

| Pin | Time (UTC) | HEAD | Status |
|---|---|---|---|
| START (all 5 workers, first action each) | 21:16:48 – 21:16:53 | ca86f493, 7 commits, clean | ✓ |
| END (final, post base-attribution teardown) | 21:32:19 | ca86f493, clean | ✓ |

Tree byte-identical to ca86f493 across the entire gate. All ad-hoc artifacts lived in /tmp or as deleted untracked test files; every worker's END pin shows empty `git status --porcelain`.

## Overall verdict

# **DO-NOT-SHIP (as-is)** — blocked solely by **Defect D-1** (Item 7b: boot WARNING double-emit). Items 1, 2, 3, 4, 5, 6 all GREEN. **SHIP immediately after D-1 fix + targeted re-verification** (remediation path below; fix is quick-fix-eligible, ~6 lines, in-file precedent).

---

## Item 1 — FULL-SUITE CENSUS: ✅ PASS (0 genuinely new reds)

- Command: `uv run python -m pytest tests/unit -q -rEf` (worktree root; via background proc, no bash-cap truncation)
- Runtime 343.20s; exit 1 (expected, pre-existing reds)
- Summary verbatim: `109 failed, 10905 passed, 57 skipped, 327 warnings, 23 errors in 343.20s`
- Problem records: 109 FAILED + 23 ERROR = 132

### Census diff table

| Family | Expected | Observed | Disposition |
|---|---:|---:|---|
| Watchover / default_streaming | ~47 | 47 | pre-existing ✓ |
| MCP-bootstrap (ERRORs) | 23 | 23 | pre-existing ✓ |
| find_near | ~13 | 13 | pre-existing ✓ |
| AccessMemoryArchive | ~5 | 5 | pre-existing ✓ |
| RAG ordering flake | 0-1 | 0 | absent this run ✓ |
| b1 hardcoded-path | 1 | 1 | pre-existing ✓ |
| **Unlisted names (strict-rule NEW)** | 0 | **43** | **resolved by base attribution → below** |

### Base attribution (decisive closure) — 43 disputed names vs merge-base a904374e

Method: disposable detached worktree `git worktree add --detach /tmp/sio-base-wt a904374e`, own `uv sync`, provenance proof `daemon.__file__ → /private/tmp/sio-base-wt/daemon/__init__.py`, targeted 21-file run at base (38F/583P, 8.22s) vs identical targeted run at feature (38F/583P, 7.02s) — **fail lists byte-identical**. 3×-solo determinism ladder for names green at base.

| Disposition | Count | Names |
|---|---:|---|
| RED@base (pre-existing baseline drift) | 38 | job_queue_proxy_phase1 ×7, coder_developer_migration ×5, paused_auto_resume_fallback ×5 (Mock→await trap), job_processor_status_guard ×4, devops/wanderer/project-manager/coder prompt-composition ×7, llm_allowed_models_precedence ×2, terminal_reason_mirror_set ×1 (orphan_retired/pattern_f1_orphan literals), phase4_manager_decomposition ×1 (cascade_to_root kwarg pin drift), api_router_extraction ×1 (api.py 2339 lines vs 1600 pin), maintenancer_kb_coverage ×1 (EXPECTED_RELEASE_TAG v0.12.4 vs pyproject v0.12.8), models_split ×1 (LivezResponse in `__all__`), validate_agent_id_compat ×1, vision ×1 |
| Census-isolation flakes (PASS solo 3/3 at BOTH base and feature; red only in full-suite ordering) | 5 | rag/test_config auto_test_rag_skips_when_host_not_set; infra_tools list_filter_by_type; mcp_tool_timeout tool_node_handles_timeout; frozen_tool_name_discovery project_manager_frozen_mode; tool_config_validation_boot source_mode project_manager |
| **Genuinely NEW (red@feature 3/3 AND green@base)** | **0** | — |

**Verdict: PASS-with-amendment.** The dispatch family list was incomplete; the true baseline carries the additional families enumerated above (all pre-existing at base, none touch this feature's four files: `daemon/config.py`, `daemon/services/instance_lifecycle.py`, `daemon/tools/instance.py`, `tests/unit/test_spawn_intelligence_tier.py`).

### Moved file green: ✅ PASS

`tests/unit/test_spawn_intelligence_tier.py` — absent from FAILED and ERROR lists, 19 tests collected, solo run `timeout 300`: **19 passed in 0.12s, exit 0**.

---

## Items 2-4 — TIER PATH / LOUD FAILURE / DEFAULT-UNCHANGED: ✅ PASS (10/10)

Real tool invocation via `create_instance_tools` (ad-hoc `tests/integration/test_gate_sio_tmp_tier.py`, 10 tests, 0.83s, deleted after run). Plan dir `.agents/shared/planning/spawn-intelligence-override/` absent from worktree — observables derived from source + existing test docstrings (informational gap, not a branch defect).

| Check | Result | Key evidence |
|---|---|---|
| 2a tier='high' spawn | ✅ | Return carries `model='agentic' (model_tier='high')`; legacy fallback notice absent |
| 2b persisted override | ✅ | `instance_metadata.model_override == 'agentic'` via real `InstanceLifecycleService.spawn_instance` capture |
| 2c LLM construction receives it | ✅ | `manager.spawn_instance` kwargs: `model='agentic'` (tier-resolved) → `_build_llm_config(override_model=…)` at instance_lifecycle.py:1826 |
| 2d both params | ✅ | Tier wins; verbatim `[NOTE] model='coding' superseded by model_tier='high' (using agentic)`; persisted 'agentic' |
| 2e invalid legacy model | ✅ | `[NOTE] model='definitely-not-a-model' superseded…`; suppressor gate at tools/instance.py:2040 held; no fallback notice |
| 3a boot WARNING single-per-call | ✅ | In-process: exactly 1 record, verbatim `[Config] spawn_intelligence_tier_high_model resolves to 'tier-fake-model', which is NOT in allowed_models ['agentic', 'coding', 'coding2']; model_tier='high' spawns will raise until the env is re-pointed.`; no raise |
| 3b per-spawn ValueError (B2) | ✅ | `pytest.raises(ValueError)` propagates from TOOL layer; verbatim 3-line architect text (resolution + env var + allowed list + env hint + legacy-recovery hint); `manager.spawn_instance.called == False`; resolver block placed BEFORE `try:` at tools/instance.py:1937-1966 |
| 4a default→pool | ✅ | `llm_load_balance_selected` fires, resolved_source=llm_models; `_resolve_intelligence_tier` spy = 0 calls; persisted == pool pick |
| 4b legacy model='agentic' | ✅ | Silent-fallback semantics unchanged: no visibility line, no [NOTE], persisted 'agentic' |
| 4c D7 zero leakage | ✅ | Observed kwargs `{agent_id, instance_id, parent_id, project_id, instance_name, model, version_tag}` — all in allow-list; `model_tier` absent; model='agentic' (tier-resolved, D12 supersede) |

Corroboration: 3a (one record **per `load_config()` call**) + Item 7b (two records **per boot**) together pin D-1's root cause — see below.

---

## Item 5 — SCHEMA/DOC SURFACE + PIN SUITE: ✅ PASS

- **Suite**: all 5 files green — `tests/unit/test_spawn_intelligence_tier.py` (19) + `tests/integration/test_spawn_intelligence_tier.py` (11) + `tests/integration/test_spawn_default_unchanged.py` (7) + `tests/unit/test_spawn_instance_input.py` (2) + `tests/unit/test_append_allowed_models.py` (3) = **42 passed, 1.26s, exit 0**. (Bookkeeping note: dispatch labeled this the "72-pin suite"; actual count is 42 across the 5 named files — all located, none missing.)
- **5a enum**: `model_tier: Optional[Literal['high']]` → JSON schema `anyOf:[{const:'high',type:'string'},{type:'null'}]` — exactly `["high"]` ✓
- **5b rejects "low"**: `ValidationError` `loc=('model_tier',)`, `literal_error`, `Input should be 'high'` ✓
- **5c M8 parity**: SpawnInstanceInput description vs @tool docstring — **EQUIVALENT** (both: value 'high'; ValueError if resolved model not in allowed_models; supersedes legacy `model=`; empty allowed_models = pass-through). Tool docstring richer (D2/D12/A5 citations); no drift.
- **5d docstring truth**: `_build_long_tool_notice` docstring (long_tool_nudge.py:801-811) claims locked 5-section structure incl. rec-4 model_tier re-spawn; code emits exactly recs 1-4 with rec-4 mentioning `model_tier='high'` — **MATCH**.
- **5e D7 coverage**: 21 kwarg/leakage assertion sites across the suite incl. `tests/integration/test_spawn_intelligence_tier.py:122-125` (`"model_tier" not in manager.spawn_instance.call_args.kwargs`) — all green.

---

## Item 6 — NUDGE INTEGRATION (cross-feature): ✅ PASS

- **Family**: 14 test files located (2 named + 12 additional `*long_tool_nudge*`); one-pack run: **137 passed, exit 0** (3.33s; re-run 137/137 — stable).
- **e2e pin zone** `tests/integration/test_long_tool_nudge_e2e.py:216-221`:
  - :219 `assert "model_tier" in content` (rec 4 names the new opt-in param)
  - :220 `assert content.count("Re-spawn with high intelligence") == 1`
- **Real-builder content checks** (temp test, deleted): model_tier rec present ✓; exact count 1 ✓; section structure intact (header / why-it-matters / recs 1-3 / rec-4 NEW / advisory-only footer; recs 1-3 correctly contain NO pause/resume advice) ✓; rec-4 seam at `daemon/services/long_tool_nudge.py:843-846`, caller seam :1126 ✓.
- **Feature #2 non-regression**: U2b wedge replay, kill-switch (`LONG_TOOL_NUDGE_ENABLED`, config file ×5, observability OFF-path), AD-9a belt (force-clear/rearm/warn, TTL boundary, orphan close ×4, fired-hygiene ×2) — all listed pins GREEN.

---

## Item 7 — BOOT SMOKE (disposable-PG): 7a ✅ / **7b ❌** / 7c ✅ / teardown ✅

Disposable PG 14.22 @15432 (db `ensemble_gate`), uvicorn direct boot (no dev.sh) @18080-18082, per-boot `Creating PostgreSQL engine: 127.0.0.1:15432/ensemble_gate` discriminator present in all 3 logs.

| Scenario | Boot | Health | `[Config] spawn_intelligence_tier_high_model resolves to` count | Verdict |
|---|---|---|---|---|
| 7a default env | ✓ startup complete | 200 (<2s) | **0** | ✅ PASS |
| 7b `SPAWN_INTELLIGENCE_TIER_HIGH_MODEL=gpt-nonexistent-fake` | ✓ startup complete, no traceback | 200 (<2s) | **2 — expected exactly 1** | ❌ **FAIL (D-1)** |
| 7c `SPAWN_INTELLIGENCE_TIER_HIGH_MODEL=""` | ✓ startup complete | 200 (2s) | **0** | ✅ PASS |

- 7c unset-semantics proof (in-process): `_clean_env_value("") → None` → `_resolve_intelligence_tier_high_model` returns default `'agentic'` (config.py:2383-2401, default at :112); `load_config()` installs `'agentic'`.
- Teardown: all 3 uvicorn procs SIGTERM'd by verified PID, `pg_ctl stop`, /tmp artifacts removed, ports 15432/18080-18082 verified free; 8088/8079 never touched.

---

## Defects

### D-1 🟠 IMPORTANT (gate-blocking; violates pinned check "exactly one WARNING record")

- **What**: With non-allowed `SPAWN_INTELLIGENCE_TIER_HIGH_MODEL`, the boot WARNING fires **twice per process** (identical lines, log lines 6 & 26 of the 7b boot log).
- **Where**: `daemon/config.py:3258` — WARNING emit site has **no module-level emit-once guard**.
- **Root cause**: `load_config()` runs twice on the boot path — `daemon/api.py:245` (lifespan startup) AND `daemon/services/attestation_resolver.py:513` (judge-model boot-log resolution). Each call re-emits. In-file precedent that guards correctly: `warn_deprecated_allowed_models_env` at `daemon/config.py:2309` uses module flag `_allowed_models_deprecation_warned`. The unit pin `tests/unit/test_spawn_intelligence_tier.py:361` (`len(tier_records) == 1`) only invokes `load_config()` once, so it cannot catch the double-emit.
- **Risk / blast radius**: No functional impact (daemon boots, no raise, per-spawn ValueError unaffected, warning text verbatim-correct). Operational hazard: this project's ops runbooks grep-count WARNING lines as health signals; a permanent 2× count breaks "exactly N" style checks and log-noise budgets.
- **Fix candidate**: wrap the condition at `daemon/config.py:3254-3264` with a module-level guard mirroring `_allowed_models_deprecation_warned`; extend the pin to call `load_config()` twice and assert one record. Quick-fix-eligible (<20 lines, single file, obvious root cause, in-file precedent).
- **Recommended re-verification after fix** (~10 min): boot-smoke 7b re-run (exactly 1 record) + 42-pin suite + `tests/unit/test_spawn_intelligence_tier.py` solo. Census unaffected (config.py change is import-time only, but a full re-census at the new HEAD is the conservative option).

### Minor / informational (non-blocking, pre-existing at base)

| Sev | Location | Note |
|---|---|---|
| 🟢 | tests/unit/test_paused_auto_resume_fallback.py | plain `MagicMock` on awaited `command_dispatcher.dispatch` — known AsyncMock-migration trap (blueprint-documented), pre-existing at base |
| 🟢 | tests/unit/test_maintenancer_kb_coverage.py:259 | `EXPECTED_RELEASE_TAG=v0.12.4` stale vs pyproject v0.12.8 — release-bump pin lag (also flagged in prior gate notes) |
| 🟢 | tests/unit/test_terminal_reason_mirror_set_regression.py:99 | flags `orphan_retired`/`pattern_f1_orphan` literals from earlier Pattern-f work — mirror-set pin drift |
| 🟢 | tests/unit/test_models_split.py:176 | `LivezResponse` in `daemon.models.__all__` not in expected_names — earlier livez feature drift |
| 🟢 | tests/unit/test_api_router_extraction.py:778 | api.py 2339 lines vs 1600 threshold — file growth post-refactor |
| 🟢 | tests/unit/test_phase4_manager_decomposition.py:834 | facade assertion missing `cascade_to_root=True` kwarg — earlier facade-work pin drift |
| 🟢 | 5 census-isolation flakes (Item 1 table) | pass 3/3 solo at base AND feature; recommend baseline-side triage/quarantine in a follow-up maintenance pass (not quarantine during this gate — they are base-owned, and the gate commit is path-scoped to this file) |

---

## Scope & artifacts

- Full 7-item plan executed as specified — no scope reduction. All verification was read-only against ca86f493; the only tree mutation is this RESULTS file (path-scoped commit on the feature branch).
- Disposable artifacts (all removed): /tmp/sio-base-wt (detached base worktree, removed from registry), /tmp PG cluster + DATA_DIRs + boot logs, ad-hoc temp test files (deleted, END pins prove clean `git status --porcelain`).

## Verdict summary

| Item | Verdict |
|---|---|
| 1 census | ✅ PASS (0 new reds; moved file 19/19) |
| 2 tier path e2e | ✅ PASS |
| 3 loud failure (B2) | ✅ PASS |
| 4 default-unchanged | ✅ PASS |
| 5 schema/doc + suite | ✅ PASS (42/42) |
| 6 nudge integration | ✅ PASS (137/137) |
| 7 boot smoke | ❌ 7b FAIL (D-1 double WARNING); 7a/7c/teardown PASS |
| **Overall** | **DO-NOT-SHIP as-is — single blocker D-1; SHIP after ~6-line guard fix + targeted re-verify** |
