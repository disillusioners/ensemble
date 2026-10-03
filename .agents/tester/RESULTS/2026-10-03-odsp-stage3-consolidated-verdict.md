# CONSOLIDATED FINAL VERDICT — ODSP Stage 3 / MCP warmup-pool env fix independent gate
Date: 2026-10-03 (gate executed 2026-10-02 18:xx–19:24Z; recovered + gap-closed 2026-10-03 after parent job c264aa8a went prematurely terminal at 19:25:54Z — bookkeeping defect family, tester work unaffected)
Commission: independent test gate for `fix/mcp-warmup-pool-env` @ 97b7e665 (base 24705dc4 = latest), repo /home/nea/ensemble-src
Primary evidence: `RESULTS/2026-10-02-mcp-warmup-pool-env-gate-97b7e665.md` (full report, all corrections applied) · `PACKS.md` (gate commission section) · `LESSONS/2026-10-02-mcp-env-gate-baseline-traps.md` · `QUARANTINE.md` (2 new base-attributed rows)

## Field 1 — Base-FAIL proof outcome: PROVEN (independently, on pure base)
- Isolated worktree @ 24705dc4, new test file transplanted via `git checkout 97b7e665 -- <file>`; import-location proof held (`daemon.__file__` → worktree path; the main `.venv` editable `.pth` did NOT hijack; proof must run with cwd INSIDE the worktree — sys.path[0]=cwd otherwise silently resolves the main checkout).
- Pure-base run: **collection ImportError** — `cannot import name 'build_pooled_stdio_config' from 'daemon.mcp.warmup_pool'` at test module :64 → suite interrupted → all 17 pinning tests blocked = genuine 17/17 fail on base.
- API-level probe on base: `AttributeError: 'McpWarmupPool' object has no attribute 'update_server_env'` (symbol absent on base; present on fix at warmup_pool.py:316 and :707).
- Narrative discrepancy (non-blocking): dev-reported "AttributeError at ~8 call sites" per-test shape is not reproducible on pure base — the module-level ImportError masks it.
- Root-cause chain of the original live symptom (od_generate_design BYOK starvation: pool registered from definition defaults, env writes never refreshed pool config, sessions resolve pool-first) confirmed by hunk-level audit + real-object probes of the fix's counter-behavior.

## Field 2 — Seam reality: REAL, no tautologies
- 17 pinning tests / 6 classes classified: **11 REAL-SEAM, 5 PARTIAL-REAL-SEAM, 0 MOCKED-SEAM, 0 tautologies, 0 subprocess**.
- `build_pooled_stdio_config` ROW-WINS overlay: real helper exercised (overlay applied last; str-filter :786; `__KMS_ENV__` markers byte-preserved; no-row → definition defaults).
- `update_server_env` atomic replace: real pool method (config reconstructed atomically, sibling keys preserved, defensive copy proven; returns False for unregistered).
- `_refresh_pool_env` on all three env-write lanes: mcp_set_env (:1657), kms_attach LANE-1 (:1222), LANE-2 (:1153) — real tools → real SQLModel repo on real SQLite → real refresh helper, with the final pool boundary mocked in-suite and **closed by independent real-object probe**: real `_refresh_pool_env` → `manager._mcp_service._warmup_pool` → REAL McpWarmupPool; marker overlaid on definition defaults; sibling server untouched. Graceful-degradation branches (no pool / no method) real-seam.
- Honest boundary: conftest.py:53-119 mocks the whole `mcp` SDK, so proof reaches the `StdioServerParameters`-kwargs boundary, not a real child-process env (SDK-owned third-party behavior). Acceptable; subprocess-harness hardening optional.
- Drain-not-kill + malformed-row fallback: PROVEN by independent /tmp probes on real production objects (no connection terminate/close/kill; manager.py:2301-2325 try/except → WARNING → definition-only → boot continues) — suite does NOT pin these (follow-up owed).

## Field 3 — Developer claims independently confirmed
- Changed set EXACTLY 4 files (+1005/−7); **kms_resolver.py empty diff proven**; single commit on base; zero debug residue.
- 17 pinning tests / 6 classes: **17/17 PASS** on fix branch (13s), genuine base-FAIL (field 1).
- Claim (a) boot row-wins merge incl. __KMS_ENV__: **PROVEN** (real-seam tests #6/#7/#9/#16; manager filter-chain wiring verified by code-reading + probe — not test-invoked).
- Claim (b) env-write refresh reaches pool (3 lanes): **PROVEN** (composition + closing-loop probe).
- Claim (c) unrelated pooled servers unaffected on empty row: **PROVEN** behaviorally (test #15 byte-identical siblings + probes).
- Claim (d) malformed row → definition-only fallback, no boot crash: **PROVEN by probe**; suite gap — pinning test follow-up routed, non-blocking.
- Dev count reconciliation: 17/17 ✅ · warmup 69/69 ✅ · mcp_set_env 44/44 ✅ · KMS-161 ✅ (superset 139+151=290 run green) · "wider test_mcp_* 518" **undercount** (real surface 671 strict / 889 all-in; this gate executed the MCP family 838/845 collected, 7 e2e excluded with lane-reason).

## Field 4 — g2-basecmp-shardC (the lost leg): RE-RUN AND CLOSED
- Original dispatch was never delivered (orchestration wedge; worker 4778a772 idle, initiative null — leader forensics). Re-dispatched 2026-10-03 with the full task; completed cleanly.
- Purpose: attribute shard-C's 3 reds vs base — knowledge×2 (`TestExploreCallerModelOverrides` :3021/:3060) + archive×1 (`test_access_archive_path_traversal_rejected` :101).
- Result: base run (knowledge+archive files, no deselects) = 7F/144P containing **byte-identical signatures for all 3** → **ALL PRE-EXISTING**. knowledge×2 = the KB-documented stale-vs-1a40bc56 family (since v0.16.0). archive×1 = test-debt; the base run also exposed the full archive×5 "Access denied" family — exactly the set the registered pack's 5 deselects quarantine. Consistency: fix-branch 3F ≡ base 7F minus the 4 correctly-deselected archive nodes — zero divergence.
- Correction surfaced by the leg: the "quarantine deselect typo" was in MY dispatch chain (inventory worker's relayed list), NOT in the registered pack (all 3 pack scripts carry the correct name since 68823b49). RESULTS/LESSONS corrected; the "fix the deselect name" follow-up retracted. New lesson recorded: grep-verify any relayed "verbatim" list against source; assert pytest's "N deselected" equals expected.

## Field 5 — FINAL VERDICT: **READY**
`fix/mcp-warmup-pool-env` @ 97b7e665 is READY for `--no-ff` merge into latest.
- **No NEW failures vs base 24705dc4**: gate totals 4312 passed / 80 skipped / 8 failed / 17 errors across 21 packs incl. full tools-dir coverage (3284 collected via 4 disjoint shards + 3 direct packs) and the MCP/KMS family — **every red and error reproduced byte-identically on base in isolated import-proved worktrees** (4 attribution legs + base-FAIL leg). Fix-caused failures: ZERO.
- **Claims verified** (field 3) — all four behavioral claims hold; the fix's seams are real and wired as claimed (field 2).
- ensure.md Core #1-#4 PASS (scoped packs, concurrency baseline exact, dev.sh flag); Release Gate not triggered (scoped 4-file fix; job/task/queue lane intersection empty; zero daemon boots during the gate — env-poison family guard upheld).
- Non-blocking follow-ups (routed in gate RESULTS §7): pinning tests for manager wiring/fallback/drain; tools_suite registered-pack scope-vs-110s-self-cap Test Architecture Fix (chronic TIMEOUT — cannot pass as registered; interim shard coverage delivered); test-debt census now in QUARANTINE.md; **the definitive live-symptom validation (od_generate_design BYOK smoke) is post-promote by design** — run it on the first dev-DB boot of the promoted build (ENSEMBLE_SELF_ENV=dev, never ensemble_prod).
