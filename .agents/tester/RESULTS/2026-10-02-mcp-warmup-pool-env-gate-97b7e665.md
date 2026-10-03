# Test Report: MCP warmup-pool env starvation fix — INDEPENDENT GATE
Date: 2026-10-02
Branch: `fix/mcp-warmup-pool-env` @ 97b7e665 (base 24705dc4 = latest), repo /home/nea/ensemble-src
Gate commissioner: leader (independent verification layer; developer's own review 0-critical is NOT counted as evidence here)
Worker instances: gate-mcp-audit d0926011 · gate-mcp-basefail 9510fa5c · gate-mcp-seam c97ee5a0 · gate-mcp-newtests 4da07f6b · gate-mcp-probe a3937807 · g2-warmup-suite 74a3aa90 · g2-mcpsetenv-pack bdd3dca8 · g2-mcp-a1 563b9191 · g2-mcp-a2 e35ae460 · g2-mcp-b1 916c149f · g2-mcp-b2 ff84da53 · g2-mcp-c e3ad4699 · g2-mcp-d d1e4ac0c · g2-mcp-e-integration 8d32c93e · g2-kms-family 09f75e8e · g2-kms-lane1-pack d0359309 · g2-frozen-pack 2b7973e4 · g2-tools-suite 6dcd2530 · g2-concurrency d5597b24 · g2-basecmp-builtin 16e8df82 · g2-toolsdir-inventory 03d20057 · g2-shard-A 13c6b8a9 · g2-shard-B 836b4c36 · g2-shard-C 4e2912fe · g2-shard-D 5a59d465 · g2-basecmp-shardA ed26cf78 · g2-basecmp-shardD 2198001b · g2-basecmp-shardC 4778a772 (in flight at write time)

## Verdict
**READY for merge into latest** — see §7. (All reds/errors base-attributed pre-existing; attribution legs completed for every family incl. shard-C trio — §5.3.)

## 1. Scope Decision
Scoped gate on the fix's 4-file diff + full MCP/KMS/tools regression surface. No full-suite run (not an architecture change). No daemon boot anywhere in this gate (env-poison family guard; fixture/unit-integration preferred per commission). Release Gate NOT triggered (scoped defect fix; lane analysis §6).

## 2. Change-set audit (gate-mcp-audit)
- HEAD = 97b7e665f3a051be504926292bb0a7c3459082bd on fix/mcp-warmup-pool-env; base 24705dc4 is ancestor; exactly 1 commit on base.
- Changed set = EXACTLY the 4 claimed files (+1005/−7): daemon/mcp/warmup_pool.py (+164), daemon/manager.py (+48/−7), daemon/tools/infra.py (+118), tests/unit/test_mcp_warmup_pool_env_refresh.py (+682). **kms_resolver.py empty diff PROVEN** (`git diff 24705dc4..97b7e665 -- '*kms_resolver*'` → empty).
- Hunk map matches claims: `update_server_env` @ warmup_pool.py:316, `build_pooled_stdio_config` @ :707 (row-wins overlay, str-filter :786); manager.py:74 import (same-line replacement, NO line shift), :2276-2325 rationale+routing+try/except fallback (:2301-2325); infra.py `_refresh_pool_env` @ :1431 (getattr-guard chain, fail-open + WARNING), wired at :1153 (LANE-2 env-ref), :1222 (LANE-1 handle), :1657 (mcp_set_env; re-reads row FRESH).
- Zero debug residue (no print/breakpoint/set_trace/commented-out code). Cosmetic notes: (i) dead `pass` branch in build_pooled_stdio_config (transport Literal makes it unreachable); (ii) fail-open stub-tolerance (`hasattr get_mcp_server`) in infra row reads; (iii) non-stdio builtin definitions would now log a spurious WARNING via the except path (no non-stdio builtin exists today).

## 3. Base-FAIL proof (gate-mcp-basefail) — CONFIRMED
- Isolated worktree @ 24705dc4, new test file transplanted via `git checkout 97b7e665 -- <file>`; import-location proof: `daemon.__file__` → worktree path (editable .pth did NOT hijack; corroborated by traceback paths + `daemon.mcp.warmup_pool.__file__`).
- Symbols: absent on base (grep exit 1), present on fix (:316, :707).
- Base run: **collection ImportError** — `cannot import name 'build_pooled_stdio_config'` at test module line 64 → suite interrupted, all 17 tests blocked = genuine 17/17 fail on base.
- API-level probe on base: `hasattr pool.update_server_env` → False; live `AttributeError: 'McpWarmupPool' object has no attribute 'update_server_env'`.
- Discrepancy note: dev-reported "AttributeError at ~8 call sites" per-test shape is NOT reproducible on pure base — the module-level ImportError masks it (dev likely observed a partially-patched tree). Fix is unaffected; narrative accuracy flag only.

## 4. Seam reality + coverage (gate-mcp-seam, gate-mcp-probe)
conftest.py:53-119 wholesale-mocks `mcp.*` SDK — real-transport impossible in-process; new suite uses NO subprocess harness (precedent exists: test_mcp_quote_sanitization.py:453-462).

Classification of 17 tests: **11 REAL-SEAM, 5 PARTIAL-REAL-SEAM** (real tool fn → real SQLModel repo on real SQLite → real `_refresh_pool_env`; pool is `MagicMock(spec=McpWarmupPool)` at the final boundary), **0 MOCKED-SEAM, 0 tautologies, 0 SUBPROCESS**.

Claims:
- **(a) boot row-wins merge incl. __KMS_ENV__ — PROVEN.** Tests #6/#7/#9/#16 exercise real `build_pooled_stdio_config` (overlay last, row wins, marker byte-preserved) + real `pool.register_server`. Gap (code-reading only): no test invokes `InstanceManager._init_warmup_pool` (filter chain is_builtin_disabled/is_available/is_active).
- **(b) 3 env-write lanes reach the pool — PROVEN BY COMPOSITION.** Tests #10/#13/#14 run real tools end-to-end to `_refresh_pool_env` (real validation, real repo, real marker writes; plaintext absent asserted); tests #1-#5 prove the real pool method given those args; **probe 4 closed the mocked boundary**: real `_refresh_pool_env` → stub manager attr-chain → REAL McpWarmupPool — `manager._mcp_service._warmup_pool is pool` verified, `__KMS_ENV__` marker overlaid on definition defaults for `od`, sibling `context7` untouched. Graceful-degradation branches (#11/#12) real-seam.
- **(c) unrelated servers unaffected — PROVEN.** Test #15: three real registered servers, byte-identical sibling envs after update. Probes 3/4 corroborate (plane/context7 untouched).
- **(d) malformed row → definition-only, no boot crash — PROVEN BY INDEPENDENT PROBE** (suite gap: no test exercises it — follow-up pinning test recommended). Probe 1: list-env/non-string/None shapes silently filtered by helper (:786 guards), row-config-access raising propagates; no uncaught crash. Probe 2: manager.py:2302-2321 try/except → WARNING + `definition.build_config({})` fallback → boot continues; behavioral fallback produced a valid config. Observation: fallback re-calls `definition.build_config({})`, so a *definition-side* explosion would re-raise — theoretical (static definitions; row shapes cannot reach it post-filter), outside claim (d) scope.
- **Drain-not-kill: PROVEN by probe 3** (suite gap noted): real pool, injected FakeConn — same object post-update, terminate/close/kill never called, config atomically replaced, sibling byte-identical.
- **Spawn seam:** proven to the SDK-constructor boundary (test #4 pins env kwargs reach `StdioServerParameters` call); actual child-process env is SDK-owned (third-party). Acceptable boundary; subprocess-harness test optional hardening.

## 5. Regression surface — MY numbers (all packs single-invocation, dual-layer timeout 300/240, no -x)

| # | Pack | Result | Attribution |
|---|------|--------|-------------|
| 1 | new pinning suite (17) | ✅ 17/17, 13s | — |
| 2 | test_mcp_warmup_pool.py (69) | ✅ 69/69, 61.5s | dev claim matched |
| 3 | registered mcp_set_env_unit_test.sh (44) | ✅ 44/44, 40.6s | dev claim matched |
| 4 | registered kms_lane1_regression (151) | ✅ 151/151, 25.7s | header claim matched |
| 5 | registered concurrency_atomic (98P/0F/74S) | ✅ baseline EXACT, 58.9s | ensure.md Core #2/#3 |
| 6 | registered frozen_tool_name_discovery (7) | ✅ 7/7, 1.2s | drift rule for daemon/tools changes |
| 7 | MCP A1: service+resilience+kb_server (191) | ✅ 191/191, 8s | — |
| 8 | MCP A2: config+filter+lazy+connmgr+kb ctx/int (114) | ✅ 114/114, 7s | — |
| 9 | MCP B1: server_crud+test_connection (152) | ✅ 152/152, 6.3s | stale-db trap did not fire |
| 10 | MCP B2: plane+builtin (154) | 137P + **17 setup ERRORs**, 8.75s | **base-attributed PRE-EXISTING** (see 5.1) |
| 11 | MCP C: quote+stdio_timeout+stdio_wrapper (70) | ✅ 70/70, 9.1s | — |
| 12 | MCP D: tool_timeout+cold_load_race+4 more (58) | ✅ 57P/1S, 5.2s | 3 known cold_load reds did NOT surface |
| 13 | MCP E: integration/test_mcp_lifecycle (13) | ✅ 13/13, 1.1s | safety-gated: fixtures self-contained (zero DB/env grep hits) |
| 14 | KMS family 6 files (139) | ✅ 139/139, 28.2s | superset of dev's 161 grouping |
| 15 | registered tools_suite_unit_test.sh | ⚠️ TIMEOUT @ internal 110s cap; ~761 ran, 0F | chronic pack-scope defect (see 5.4); superseded by shards |
| 16 | tools shard A: prompt_section (1377) | 1374P + **3F**, 8.1s | **base-attributed PRE-EXISTING** (5.2) |
| 17 | tools shard B: 9 files (685) | ✅ 685/685, 89s | — |
| 18 | tools shard C: 16 files (759−4 deselected) | 752P + **3F**, 80s | [PATCHED ON SHARDC RETURN] (5.3) |
| 19 | tools shard D: 28 files (386) | 379P/5S + **2F**, 57.3s | **base-attributed PRE-EXISTING** (5.3b) |

**TOTALS (my runs): 4312 passed / 80 skipped / 8 failed / 17 errors.** 5 of 8 reds + 17 errors PROVEN pre-existing on base; 3 reds (shard-C trio) [PATCHED ON SHARDC RETURN]. tools-dir coverage via shards + 3 already-green files = full 3284-collected dir (57 files, disjointness-proven).

### 5.1 builtin 17 errors — PRE-EXISTING (basecmp-builtin)
Fix branch AND base 24705dc4: identical 69P/17E; same fixture (test:368 `instance_manager_with_repo`), same missing attribute (`service_tool`), same producing line (manager.py:746 — import hunk was same-line replacement, zero shift). Test-infra mock-config gap, orthogonal to fix. Mission note "compare against base, do NOT chase" satisfied with hard evidence. (Mission's "17 collection errors" wording was imprecise — they are SETUP fixture errors; collection is clean.)

### 5.2 shard-A 3 reds — PRE-EXISTING (basecmp-shardA)
Base reproduces 3F/1374P, byte-identical node IDs (`workflow.md11`, `memory.md7`, `workflow.md17`) + assertion text; `git diff base..fix -- agents/` EMPTY; test is a pure repo-content lint. Pre-existing agent-prompt content debt.

### 5.3 shard-C trio + shard-D pair — ALL PRE-EXISTING
- shard-D 2 reds (`test_watch_job_mission_terminal.py` :203 AsyncMock-vs-int leak; :315 ungated watcher claim) — **PRE-EXISTING** (basecmp-shardD): base reproduces 2F/12P byte-identical payloads; KB "watch×2" quarantine family from v0.16.0 gate, now formally attributed.
- shard-C 3 reds — **PRE-EXISTING** (basecmp-shardC, leg re-dispatched 2026-10-03 after original dispatch lost in orchestration wedge; worker 4778a772): base run of knowledge+archive files = 7F/144P, containing byte-identical signatures for all three: knowledge×2 `TestExploreCallerModelOverrides` (`assert None == 'gpt-4o'` / `assert None == 'reasoning'` — the KB-documented stale-vs-1a40bc56 family since v0.16.0) + archive×1 `test_access_archive_path_traversal_rejected` (`'not found' in 'access denied'`). The 4 EXTRA base failures are the rest of the archive×5 family (all "Access denied" test-debt) — exactly the set the registered pack's 5 deselects quarantine; they surfaced on base only because the attribution leg ran without deselects. Consistency check: fix-branch shard-C 3F/752P ≡ base 7F/144P minus the 4 correctly-deselected archive nodes — zero divergence.
- Attribution-leg dispatch note: the original g2-basecmp-shardC dispatch was never delivered (orchestration wedge; worker idle, initiative null) — recovered and completed 2026-10-03.

### 5.4 Registered-pack architecture defect FOUND (not fixed during gate)
`tools_suite_unit_test.sh` self-caps at 110s internal while its scope is the whole tests/unit/tools/ dir = **3284 collected tests** (observed pace 6.9 t/s → ~8 min needed). Chronic — historical "3138P" baselines could not have fit this cap either. **Test Architecture Fix commission owed** (split/rescope the registered pack; my 4 ad-hoc shards + 3 already-green files provided equivalent coverage this gate). CORRECTION (2026-10-03, basecmp-shardC grep): the registered pack's quarantine deselect names are CORRECT (all three pack scripts in test/packs/ carry the full `test_access_archive_path_traversal_rejected` since 68823b49, present on base and fix) — the misspelled name that let the quarantined node run in ad-hoc shard C was a transcription error in MY dispatch chain (inventory worker's "verbatim" list → shard dispatch), not a pack defect. Earlier "fix the deselect name" follow-up RETRACTED; docs corrected.

## 6. Execution-lane judgment (project rule: lane intersection, not file diff)
- Job/task/queue execution-lane sites (task_processor.py:267, message_job_handler.py:129, claim_pending_task task_processor.py:464): **zero intersection** with the diff (manager.py changes are :74 import + boot-time _init_warmup_pool; infra.py changes are env-write tool lanes).
- The MCP tool-call lane IS the fix's surface and is covered at the real seam (unit + probe) to the SDK-constructor boundary.
- Boot probe: NOT run — boot path covered by real-seam tests + probe (structural AND behavioral); ensure.md Core requires none for scoped changes; env-poison family makes fixture/unit-integration the preferred lane (mission concurs). **Zero daemon boots, zero DB contact, ENSEMBLE_SELF_ENV never set, port 8088 untouched throughout the gate.**
- e2e (tests/e2e/test_mcp_kb_e2e.py, 7 tests): excluded — requires live daemon; not warranted by lane intersection for a scoped fix. The definitive live validation of the ORIGINAL symptom (od_generate_design BYOK smoke) is post-promote ODSP Stage-3 follow-up by design (pinned defect note: fix is LIVE only after next promote) — recommend running it on the first dev-DB boot of the promoted build.

## 7. Verdict & conditions
**READY for merge into latest.**
- Base-FAIL: proven (collection ImportError + API-level AttributeError on pure base).
- Seam: no tautologies; real-seam to pool boundary; mocked boundary closed by independent real-object probe.
- Claims (a)(b)(c): PROVEN; (d): PROVEN by probe (suite gap → follow-up pinning test recommended, non-blocking).
- Regression: 0 fix-caused reds across 4312 passing tests incl. full tools-dir coverage, MCP family 838/845 collected (7 e2e excluded with reason), KMS 290/290 (139+151).
- ensure.md Core: #1 ✅ (changed packs all PASS) · #2/#3 ✅ (concurrency baseline exact) · #4 ✅ (dev.sh:102 flag present). Release Gate: not triggered.

Follow-ups (non-blocking, routed):
1. Pinning tests owed: manager `_init_warmup_pool` filter-chain + malformed-row fallback + drain-not-kill (all probe-verified behaviors).
2. Test Architecture Fix: registered tools_suite pack scope vs self-cap; fix the quarantine deselect typo wherever it lives.
3. Test-debt: builtin-17 mock-config gap (add `services.service_tool` to spec), prompt-lint ×3 bare tokens, knowledge×2 stale family, watch×2.
4. Dev-report hygiene: "518 test_mcp_*" undercount (real 671 strict/889 all-in); base-FAIL narrative described a masked shape.

## Env-safety attestation
No daemon boot, no DB connection, no ENSEMBLE_SELF_ENV mutation, no process kills, port 8088 untouched, all worktrees removed and verified, main checkout left at 97b7e665 untouched (only pre-existing/benign `install-audit.jsonl` KMS-writer dirt from test runs — never cleaned, never committed).
