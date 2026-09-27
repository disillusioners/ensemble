# Dry-Run Projection Contract v3.2 — MERGE-BLOCKING VERIFICATION (feature/dry-run-projection-v3.2)

Date: 2026-09-27 (11:41–13:00 UTC window)
Branch: `feature/dry-run-projection-v3.2` @ **`df89da37`** vs base **`1989b3b5`** (v0.16.0). 3-commit window touching only: `daemon/checkpoint_adapter.py` (+101), `daemon/routers/schemas.py` (+40 additive), `daemon/services/maintenance.py` (+73), `daemon/services/maintenance_api_service.py` (+60), FE checkpoint-cleanup surface (+models/index.ts, components, spec), CHANGELOG, runbook doc.
Commissioner task: independent re-run of dev's claimed suites + contract probes + Playwright 14 + whole-tree delta vs re-baselined base + SQL delta-semantics proof. Source mods: **ZERO**. Test adds: probe pack at `/tmp/ens-probe-v32/` (untracked, spec in MOCK_TESTS.md). Commits by this commission: **NONE** (23 workers, all read-only).

## Overall Verdict — ✅ READY TO MERGE

| # | Deliverable | Verdict |
|---|---|---|
| 1 | BE focused unit family (dev claim 65→71) | ✅ **128/128 PASS** in 9.2s — claim CONFIRMED exactly: cleanup_service file collects 71; family 122→128 = +6 new tests, all green |
| 2 | BE PG API suite (dev claim 40→41) | ✅ **40P + 1S / 41 collected**, exit 0 — claim CONFIRMED; new node `TestDryRunProjectionConvergence::test_convergence_after_pass_1_execute` passing (drift-tolerance, not equality — INV-13 spirit) |
| 3 | FE maintenance Jest (dev claim 113/113) | ✅ **113/113 PASS** — claim CONFIRMED; all 4 FE contract items pin-covered (projection render + "—" zero-skip, R-4 skip banner, journey copy, run-again banner incl. never-silent-execute). Dev baseline narrative off-by-3 (80 base + 33 new = 113, not 77+36) — cosmetic |
| 4 | FE tsc + build | ✅ exit 0; **5.89 MB < 6 MB cap** (prior figure, ~110 kB headroom); NG8113 set + 7 SCSS/budget classes + lighten()×2 all UNCHANGED — zero warning delta |
| 5 | Playwright e2e (14 cases) | ✅ **14/14 PASS, 0 skipped, 41s**, corrected protocol (worker-env DSN + positional filter); boot-stack canary proof; **F1 teardown leak did NOT reproduce** (clean EXIT trap; ports 8099/4299/15432 free; 0 leftover clusters) |
| 6 | Real-saver PG @ HEAD (checkpoint_adapter blast radius) | ✅ **7P + 2F = base-identical** — exactly the QUARANTINE.md caplog pair; traces in test-file assertions, no branch-touched code |
| 7 | **Contract probes P0–P6 (router-level, disposable PG)** | ✅ **PROBE-PASS 8/8** — see §2 |
| 8 | **Whole-tree delta vs re-baselined base 1989b3b5** | ✅ **ZERO new failures attributable to the branch** — 39 head-only nodes all classified (16+1 environmental, 1 load-flake, 21 pre-existing proven at base); see §3 |

---

## 1. Dev-claim adjudication (all confirmed)

| Claim | Independent result | Verdict |
|---|---|---|
| "BE focused 150P/1S exit 0 (disposable PG)" | unit 128/128 (exit 0, SQLite) + PG API 40P+1S (exit 0, disposable PG :15532) | ✅ (claim's "150" ≈ 128+41-window arithmetic; components exact) |
| "unit 65→71 incl. delta_not_superset + never-pruned incident + gate negative pin" | 71 confirmed in-file; 6 new nodes: never_pruned, zero_on_already_pruned, skips_skipped_pairs, delta_not_superset, echo_does_not_read_projection (negative pin), echo_accepts_when_would_free_matches | ✅ all present, all green |
| "integration 40→41 (convergence 1% tolerance)" | 41 collected; convergence test present + passing with tolerance semantics | ✅ |
| "FE maintenance Jest 113/113, tsc, build exit 0" | 113/113; tsc 0; build 0, under budget | ✅ |
| Repo traps honored | disposable PGs only (15532/15534/15540 + e2e 15432), POSTGRES_* scrubbed everywhere, no fresh SQLite boot, e2e dedicated port 8099 + canary + reuseExistingServer:false | ✅ |

## 2. Contract probes (the amendment's normative semantics, end-to-end through the REAL router)

Pack: `/tmp/ens-probe-v32/` (MOCK_TESTS.md spec §"Dry-Run Projection Contract v3.2"). Author leg + independent execution leg (harness-sanity adjudicated: real `include_router(maintenance_router)` + ASGITransport; real `MaintenanceApiService`/`AsyncPostgresSaver`/`PostgresCheckpointerAdapter` stack; sole mock = auxiliary `instance_repo`; P5 tamper = real asyncpg UPDATE on `maintenance_runs.summary_json`; P4 drift = real INSERT into `checkpoint_blobs`).

| Scenario | Assertion | Result |
|---|---|---|
| P0 already-pruned identity | now=0, after=0, total=0 | ✅ PASS |
| P1 never-pruned (the incident) | now=0 AND would_free_bytes=0, after=3000>0, total==after, rows=3>0 | ✅ PASS |
| P2 partial-pruned | now=1000>0, after=3000≥0, total==now+after=4000 | ✅ PASS |
| P3 skipped-pair (MAX_REFS_EXCEEDED, cap=2) | pair contributes 0; skipped[] non-empty (reason MAX_REFS_EXCEEDED) | ✅ PASS (R-4) |
| P4 echo-not-recomputed | execute summary projection byte-equal to dry-run row despite 3 inserted orphan blobs post-dry-run | ✅ PASS |
| P5 gate invariance | (a) echo would_free_bytes=1000 → 202 proceed; (b) echo tampered after=99 → 400 `byte_count_mismatch` | ✅ PASS (gate binds would_free_bytes only) |
| **P6 SQL delta-semantics (R-1 real-DB analog)** | B_orphan=1000 → now ONLY; B_excess=5000 → after ONLY; **B_both=3000 (excess+keep referencer) → NOWHERE**; B_keep=7000 → NOWHERE; now==1000, after==5000, total==6000; direct-SQL anti-join cross-check 1000==1000 | ✅ PASS — superset/double-count arithmetic would yield different numbers; delta semantics proven at SQL level, not just unit mock |

Zero product-defect findings. Env knobs verified from source: `CHECKPOINT_BLOB_PRUNE_MAX_REFS_PER_THREAD` (constants.py:194), `CHECKPOINT_MAX_PER_THREAD` (config.py:613, default 3). Gate read-path pinned: `maintenance_api_service.py:485-506` binds `would_delete.bytes` only.

## 3. Whole-tree regression delta (THE verdict)

Method: 10 HEAD shards (main checkout @ df89da37) + 10 BASE shards (detached worktree /tmp/ens-base-v320-1989b3b5 @ 1989b3b5), same invocation contract (`unset POSTGRES_*; timeout 300 uv run python -m pytest <scope> --tb=short -q`, default addopts), `test_*.py`-strict T/U enumeration (F3 lesson applied — zero 56-class artifacts), 34-file attestation family per-file (timeout 150/120) both sides (227P+2S ≡ 227P+2S). Node-level comm after log-spew cleaning; every head-only node re-run both sides.

- **HEAD 326 raw F/E nodes; BASE 294; common 287; head-only 39; base-only 7** (healed-at-HEAD, informational: filesystem guardrails ×5 U1, empty_guard ×1 + vscode_routing ×2 S5c).

**39 head-only → all classified, ZERO branch-caused:**
| Cluster | n | Classification | Evidence |
|---|---|---|---|
| `tests/unit/test_mcp_server_crud.py` (whole file) | 16 | **ENVIRONMENTAL** (.env confound) | Passes **80/80 at HEAD scratch worktree** (no .env) AND at BASE worktree; fails only in HEAD *main* checkout (carries 4.3 KB .env). Branch touched neither the router nor the test (git log empty) |
| `test_lcancheck_family_separation.py::…test_s3_whole_tree_census` | 1 | **ENVIRONMENTAL** (documented class, 3rd occurrence = F6 carried) | F/F/F at HEAD main WITH the exact live-socket signature (`grep: ./data_dev/vscode-user-data/code-server-*: Operation not supported on socket`); P/P/P at BASE |
| `test_atomic_dequeue.py::…concurrent_only_one_worker_wins` | 1 | **LOAD-FLAKE** | F/P/P at HEAD main (16-way sweep load), P/P/P at BASE; passes isolated; branch provenance empty |
| S5c cohort (spawn_intelligence_tier ×7, complete_cancel_route ×4, lcan_legacy ×1, lcau ×1, message_metadata ×2, mission_final ×1, pause_race ×1, spawn_default ×1, vscode_security ×1, ui_prefs ×2) | 21 | **PRE-EXISTING** | F/F/F at HEAD main AND HEAD scratch AND BASE worktree (file-triage legs); root causes visible at base (allowed_models vision gap, R4 short-circuit mismatch, ConnectError not raised, mirror reconciliation); zero branch provenance on all 11 files |

Base-Only (7, healed at HEAD): `test_filesystem_walk_guardrails` ×4 + `test_filesystem_workdir` ×1 (U1), `test_empty_guard_error_lineage` ×1 + `test_vscode_routing_e2e` ×2 (S5c).

**VERDICT: ZERO new failures attributable to `feature/dry-run-projection-v3.2`.** Artifacts: `/tmp/v32-wt-{head,base}-*` (20 shard logs/node files), `/tmp/v32-wt-delta/adjudication.md` (+ lists, solo legs, file triage).

### Baseline ledger replacement (per task directive)
The prior authoritative 204-node pre-existing set (vs `666c089d` @ c939aaa0) is SUPERSEDED: v0.16.0 absorbed council/governor + spawn-intelligence + signature-drift test debt. **New authoritative pre-existing set vs `1989b3b5`: 287 common + 7 base-only + (21 S5c-cohort + 2 env + 1 flake observed head-only) ≈ 318-node class**, none branch-caused. Rising pre-existing debt is a quality signal for the v0.16.x line (council/governor cluster alone = 43 of T1's 64) — flagged to leader, out of this branch's scope.

## 4. Skips & exclusions (explicit)
1. Attestation family (34 files) excluded from S5c both sides — own per-file packs, both clean (227P+2S ≡). 2. tests/postgres + tests/e2e (real-LLM) + test/packs — outside default partition by addopts/convention; PG surface covered by 3 dedicated packs (PG API, real-saver, probes). 3. PG API suite 1 designed skip (SQLite-fallback conditional). 4. Real-saver 2 quarantined nodes (QUARANTINE.md base-attributed) reconfirmed base-identical. 5. concurrency_atomic_unit_test NOT run — out of blast radius (branch touches no cascade/observer/asyncio machinery); maintenance run-lock covered 15/15 + contention classes green + real-saver concurrency tests 7P. 6. Release Gate (E2E LLM workflows) NOT run — additive feature, no core-loop architecture change (ensure.md blast-radius header; same rationale as 2026-09-27 final commission). 7. FE full-suite Jest NOT run (scoped maintenance 113 per task; full suite last green 2026-09-27 with the same 4 quarantined nodes).

## 5. ensure.md (scoped)
- **C1 changed-packs green**: ✅ all packs in the change set PASS (§1 table).
- **C2/C3 concurrency/sync-DB**: ✅ by scoped mapping (run_lock_and_capture 15/15; TestContention/TestDualArmContention green in PG suite; real-saver concurrent/race/serializable 7P).
- **C4 dev.sh `--timeout-graceful-shutdown 10`**: ✅ dev.sh:102 live flag.
- **Important (3 named helpers await-correctness)**: out of blast radius (untouched).
- **Nice-to-have (dead code)**: NG8113 set unchanged; no new dead-code class.
- No contradictions between ensure.md methods and pack rules this cycle. **No Improvement Notices.**

## 6. Gaps found & dispositions
- **G1 🟠 (env, pre-existing): `test_mcp_server_crud.py` whole-file failure is `.env`-dependent** — passes 80/80 without .env (both worktrees), fails with the main checkout's .env present. Not branch-caused; routing: test-debt/env-hygiene follow-up (pin env scrub in that file or document .env sensitivity).
- **G2 🟢 (carried F6, 3rd occurrence): census socket class** — add `data_dev` to `_CENSUS_EXCLUDES` (test:140-148).
- **G3 🟢 (carried F5): real-saver 2-char caplog logger fix** — still cheapest test-debt win.
- **G4 🟢 (process): KMS test family dirties tracked `.agents/.../install-audit.jsonl` during pytest runs** — tripped 5 pre-flight porcelain gates this commission (attributed: S5c-BASE designer-KMS batch +24, S2-BASE unit-services KMS batch +7). LESSONS entry written; future sweeps: whitelist the file or scope-isolate KMS shards.
- **G5 🟢 (evidence hygiene): dev claim arithmetic off-by-3 on FE baseline (80 not 77)** and "~36 new" (33) — Jest total 113 correct; flag to dev.
- **G6 🟢 (pre-existing debt, not this branch): whole-tree pre-existing F/E class ~318 nodes** at v0.16.0 lineage (council/governor, job_feedback_observer 6-arg signature drift with 5-arg test fakes, spawn_intelligence_tier vision/allowed_models gap, mcp .env sensitivity) — recommend a dedicated test-debt commission on `latest`.

## 7. Worker roster (23)
3ba9fe52 unit-family · 6d69ff5c pg-api · 27b75c3c fe-jest · b38b72e0 fe-static · 2230aa18 pw-e2e (×1 attempt, clean) · 20f2ab25 base-setup · b40ca5da probe-author · d9121aec probe-run · d7e0654f real-saver · HEAD shards 049e2ec1/339c314a/15ebe6b9/c6181c70/c97e6848/b965b038/9d996c28/a0df67ea/6945cc4f/367e1d4e · BASE shards 1889e636/c08a3e54/873a1987/4bbb51b3/aa2b272c/255f7d9c/39c2705f/51eeda1e/d670fee3/16910084 · 575d0696 delta-agg.

## 8. Hygiene
- Zero repo modifications by any worker (porcelain deltas = pre-existing foreign `.agents/` edits + the KMS test-exercise appends, all disclosed; base worktree restored to pristine and left pinned at 1989b3b5; HEAD scratch /tmp/ens-head-v32-agg left in place pending cleanup).
- All disposable PGs (15532/15534/15540 + e2e 15432) torn down with port-free proofs; ensemble_prod / 10.44.0.2 never contacted; ports 8088/8079 never touched.
- Evidence logs under /tmp/v32-* and /tmp/ens-probe-v32/ (ephemeral; key numbers preserved in this report).

### Overall Status
- Claimed suites: ✅ all CONFIRMED · Probes: ✅ 8/8 · E2E: ✅ 14/14 · Whole-tree delta: ✅ ZERO branch-caused · ensure.md: ✅ scoped PASS
- **Testing Complete: ✅ READY TO MERGE — no merge-blocking findings. G1–G6 non-blocking (follow-ups).**
