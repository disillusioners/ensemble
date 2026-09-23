# fs-tool-guardrails — Full-Regression Verification Gate (2026-09-23)

**Verdict: ✅ PASS — 0 branch-caused regressions. Merge-ready from testing.**

- **Branch:** `feature/fs-tool-guardrails` @ `948c0f06` (4 commits `a0493ad1`/`bbc85030`/`9223914a`/`948c0f06`; base `3c09c6ae` ancestor-verified, rc=0)
- **Change:** `daemon/tools/filesystem.py` +737/−~89 (bounded walks: exclusions, depth 10, 10k file cap, 1.5MB skip, 20s deadline, absolute-root refusal, truncation notices, `.agents` allowlist) + 2 unit test files (+1133/+25)
- **Method:** 16 pack executions (15 sweep + 1 family ad-hoc) + 6 base A/B legs + 3 solo tiebreaks; **29 worker dispatches, 0 re-dispatches, all first-try**; every run drift-checked (`branch@HEAD` echoed), env-scrubbed (`env -u` POSTGRES_*/DATABASE_URL — ensemble_prod NEVER touched), dual-layer timeout (outer 300 + script-internal 280/own budget)
- **Worktree:** clean at every drift check (one foreign mid-run edit `.agents/tidier/notes.md` appeared — outside test surface, disclosed, untouched)

## 1. Scoped claim beaten — family pack

`fs_guardrails_family_unit_test` (ad-hoc; 4 files: walk_guardrails, absolute_path, grep_include_recursive_regression, workdir):
**123 passed / 0 failed / 0 errors / 0 skipped / 0 deselected** — exact match to the claim (parametrize expansion reconciled: 113 defs + 2×6 parametrized). 0.46s. Worker a2b99cac.

## 2. Full regression sweep (15 packs, 22,092 passed total)

| # | Pack | Result | Counts (P/F/E/S) | Time | Red adjudication |
|---|------|--------|------------------|------|------------------|
| 1 | regression_unit_loose_a_d | FAIL-by-baseline | 1800/10/21/2 | 18.7s | 31/31 pre-existing (leg 1) — coder/devops prompt ×9, api.py size, 21E MCP mock-config baseline |
| 2 | regression_unit_loose_e_l | FAIL-by-baseline | 1474/22/0/0 | 59.7s | 22/22 pre-existing (leg 1) — find_near ×13, job_processor ×4, coding2 ×2, gaia ×3 |
| 3 | regression_unit_loose_m_r | FAIL-by-baseline | 2324/10/0/40 | 65.9s | 10/10 pre-existing (leg 1) — release-tag pin, LivezResponse, pause-kwarg ×6, PM prompts ×2 |
| 4 | regression_unit_loose_s_z | FAIL-by-baseline | 1537/5/2/11 | 20.6s | 7/7 pre-existing (leg 1) — terminal_reason, vision, get_registry, wanderer ×2, webfetch ×2 |
| 5 | regression_unit_services | FAIL-by-baseline | 1905/8/0/0 | 18.4s | 7 proxy_phase1 (documented family, count-identical) + 1 b1_wc env-defect (leg 2) |
| 6 | regression_unit_smaller_subdirs_routers | FAIL-by-baseline | 827/3/0/0 | 17.7s | 3/3 pre-existing (leg 2) — originator_instance_id kwarg rot |
| 7 | regression_unit_tools | **PASS** | 2835/0/0/6 | 25.0s | — incl. **test_knowledge_tools.py 120/120 green** (referencer) |
| 8 | regression_top_level_a_h | FAIL-by-baseline | 1058/24/2/53 | 65.0s | 25/26 pre-existing (leg 2) + 1 jsonb FLAKE (3/3 solo PASS) |
| 9 | regression_top_level_i_q | FAIL-by-baseline | 2420/59/0/73 | 28.6s | 58/59 pre-existing (leg 3) + 1 skillab FLAKE (solo 2/2+base) |
| 10 | regression_top_level_r_z_misc | FAIL-by-baseline | 2277/13/0/34 | 22.0s | 13/13 pre-existing (leg 3) — migration-20260714 ×10, skill-evo ×2, spawn_team ×1 |
| 11 | regression_chat_source_integration | **PASS** | 58/0/0/1 | 25.8s | — |
| 12 | concurrency_atomic (ensure #2/#3) | **PASS** | 98/0/0/74 | 10.0s | — |
| 13 | regression_opencode_e2e | FAIL-by-baseline | 516/4/0/1 | 23.0s | 4/4 pre-existing (leg 3) — e2e pause/resume terminal-state family |
| 14 | regression_job_queue | FAIL-by-baseline | 1856/12(+1 flake)/0/38 | 26.9s | 12 pre-existing (legs 4+5: JSON trio = v0.13.10-documented; census ×2; known-7) + 1 council FLAKE |
| 15 | regression_integration | FAIL-by-baseline | 984/34/23/4 | 264.7s | 30/34 pre-existing (leg 6b) + 4 FLAKES (solo tiebreak 8/8 PASS); 23E = documented PG-role/schema env convention |

**Red ledger:** 205 F + 46 E observations → **198 pre-existing + 7 flakes + 0 branch-caused**; 23 E environmental (PG perms). Base A/B coverage: legs 1–6b, 100% of non-documented-family reds empirically run at `3c09c6ae`; proxy_phase1 ×7 carried from documented count-identical family. Inverse-direction: 1 branch-**fixed** node (`test_memory_integration::test_concurrent_writes_no_corruption` FAIL@base → PASS@branch).

## 3. Incident-shape verification — 19/19 PASS (the OOM repro, bounded)

Synthetic tree: 387 dirs / 4,216 files / 9.3MB in `data/fs-incident-20260923` (gitignored; removed after):
- **grep_files** `NEEDLE_FSI2026`: **1,202 matches in 138.5 ms, ΔRSS +1.88 MB** (vs 16 GB incident); 0/1000 node_modules, 0/200 .venv, 0/10 hidden leaked; `.agents/` allowlist 2/2 found; 3×3MB oversize skipped **with notice**; depth-15 excluded
- **glob_files**: 3,005 bounded entries in 104.8 ms; same exclusions; depth notice
- **Refusals (both tools, both layers)**: bare-absolute-no-workdir → incident-referencing error; outside-workdir → "outside workspace boundary"
- Notices verbatim: `⚠ Search incomplete: depth cap reached (10 levels); 3 oversized file(s) skipped (>1.4MB). Results may be incomplete — narrow the root or raise pattern specificity.` (cosmetic: 1.4MB = rounded display of the 1.5MB constant)
- Script `/tmp/fs_incident_shape_20260923.py`; log `/tmp/fs_incident_shape_20260923.log`; cleanup verified

## 4. Edge/mocking audit (static, discovery worker)

- **Resolver fidelity:** dominant pattern = public `@tool.invoke` (36 call sites); internal-helper tests isolated in dedicated classes — mocks do NOT bypass `_resolve_search_root`
- **Timeout determinism:** zero `sleep()` in either test file; fake-monotonic choreographies for walk/stat/read phases
- **Two-layer refusal:** tool layer + resolver layer both pinned (incl. W-E unconditional non-temp pins)
- **Bound pins:** 10k cap + 1.5MB skip constant-equality pins; depth-10 & 20s via monkeypatched-value behavior pins (no constant pin — minor gap, non-blocking)
- Referencer inventory: 16 files, all in default selection (mission-named `test_dynamic_toolset_expansion.py` is NOT a literal referencer — covered by partitions anyway)

## 5. ensure.md (Core, blast-radius scoped)

- **#1 No regressions in changed packs: PASS** (family 123/123; all sweep reds pre-existing/flake)
- **#2/#3 Deadlock+async-DB discipline: PASS** (concurrency_atomic 98P/0F)
- **#4 dev.sh `--timeout-graceful-shutdown 10`: PASS** (dev.sh:102, grep-verified)
- **Release Gate: NOT triggered** — single-module tool change, no cross-module architecture. postgres-marked suite (338) scoped out (needs disposable PG; zero DB code in diff — can run on request). no_xdist-marked tests skip under xdist packs per repo convention.
- No ensure.md contradictions found (all validations were already pack-mapped).

## 6. Quarantine additions (this gate)

7 flake nodes → 2 consolidated QUARANTINE.md rows (jsonb/skillab/council + 4 integration contention flakes). All retry-budget-met, solo-decisive, zero branch-caused. Rising quarantine count = test-infrastructure debt (shared SQLite/PG across xdist workers) — flagged as quality risk; durable fix = per-worker DB isolation for affected files.

## 7. Artifacts

- Base worktrees (left in place): `/tmp/ens-fsg-base`, `/tmp/ens-fsg-base-2` (detached @ 3c09c6ae)
- Logs: `/tmp/iq_pack_run.log`, `/tmp/jq_pack_run.log{,2}`, `/tmp/integration_pack_run.log`, `/tmp/fsg_base{,2,3,4,5,6,6b}_run.log`, `/tmp/fsg_cand_tb{1,2}.log`, `/tmp/fs_incident_shape_20260923.log`
- Worktree hygiene: main tree on `feature/fs-tool-guardrails` @ 948c0f06 throughout; zero commits made by this gate (report-only arc)
