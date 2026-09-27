# Maintenance Console Phase-1 Backend — Independent Verification

Date: 2026-09-27
Branch: `feature/maintenance-console` @ `3fb798d1` (base `666c089d`) — plus two TEST-ONLY commits added during this commission on top: `9dbaab90` (audit-gap G1 origin-guard route cells) and `691fb557` (smoke-script POSTGRES_URL scrub). Source under `daemon/` identical to `3fb798d1` throughout.
Commissioner task: authoritative whole-tree run + independent delta verification, Contract-v3 semantic matrix, PG integration, functional probes, live smoke. Source mods forbidden; test adds allowed for genuine gaps (2 made, both committed).

## Overall Verdict

| Deliverable | Verdict |
|---|---|
| Whole-tree default partition @ HEAD (authoritative, dev had skipped) | ✅ RUN COMPLETE — 22,995 collected across 9 shards, 204F+23E inventoried |
| **New-failure delta vs base `666c089d` (the critical deliverable)** | ✅ **ZERO branch-caused new failures** — 201/204 base-identical; 3 head-only nodes adjudicated: 1 environmental (worktree socket), 2 load-flakes (pass 3×/3× solo at both sides). Dev's delta claim CONFIRMED. |
| Contract matrix (66 planned cases) | ✅ 66/66 implemented, all 9 hot-spots carry real contract semantics; suite runs green (unit 69/69, PG 33+1 designed skip) |
| PG integration | ✅ maintenance API suite 33P+1 designed skip; real-saver 7P/2F **BASE-IDENTICAL** (quarantined pair reconfirmed) |
| Functional probes (INV-1/2/9, gate order, byte-echo, TTL) | ✅ 6/6 PASS |
| Live smoke (12-check) | ✅ 12/12 PASS (origin 403, kill-switch 4×503, availability `kill_switched`, boot sweep healed 1→0) |
| **Testing Complete** | ✅ **READY** (with 4 documented LOW-severity test-debt follow-ups, none blocking) |

---

## 1. Whole-tree default partition (HEAD) — shard table

Invocation contract per shard: `unset POSTGRES_HOST POSTGRES_PORT POSTGRES_DB POSTGRES_USER POSTGRES_PASSWORD POSTGRES_URL; timeout 300 uv run python -m pytest <shard> --tb=short -q` (default addopts `-m 'not integration and not postgres'`; per-test `timeout=30 thread` per pyproject).

| Shard | Scope | Collected | F | E | S | Wall | Exit | Result |
|---|---|---:|---:|---:|---:|---|---|---|
| S1 | tests/unit/{tools,sources,checkpoint_adapter,config,graph,models,job_state,job_queue,persistence,repositories,rag} | 3536 | 7 | 0 | 5 | 73s | 1 | FAIL (expected baseline) |
| S2 | tests/unit/{services,routers} | 2452 | 13 | 0 | 0 | 79s | 1 | FAIL (expected baseline) |
| S5c | tests/{integration,api,opencode,performance} MINUS 34 attestation files | 1536 | 14 | 0 | 1 | 269s | 1 | FAIL (expected baseline) |
| S6 | tests/{job_queue,message_queue_redesign,repositories,services,tools} | 3686 | 30 | 0 | 65 | 139s | 1 | FAIL (expected baseline) |
| U1 | tests/unit top-level files 1–110/328 | 2416 | 25 | 21 | 2 | 43s | 1 | FAIL (expected baseline) |
| U2 | tests/unit top-level files 111–220 | 2877 | 17 | 0 | 18 | 198s | 1 | FAIL (expected baseline) |
| U3 | tests/unit top-level files 221–328 | 2209 | 5 | 2 | 33 | 42s | 1 | FAIL (expected baseline) |
| T1 | tests top-level files 1–71/142 | 1966 | 24 | 0 | 78 | 47s | 1 | FAIL (expected baseline) |
| T2 | tests top-level files 72–142 + {manager,migration,property,static} | 2317 | 46 | 0 | 49 | 88s | 1 | FAIL (expected baseline) |
| **Σ** | | **22,995** | **181** | **23** | 251 | | | |

Chunk-boundary integrity: U1 ends `test_instance_list_order_repository.py` / U2 starts `test_instance_mapping_upsert.py`; U2 ends `test_question_deferred_pause_callback.py` / U3 starts `test_question_deferred_pause_edge_cases.py`; T1 ends `test_message_job_bridge.py` / T2 starts `test_message_job_serialization.py` — no gaps/overlaps.

**Branch's own surface in the shards: ZERO failures.** `test_maintenance_checkpoint_cleanup_service.py` (39), `test_maintenance_run_lock_and_capture.py` (15), `test_checkpoint_prune_destructive_override.py` (11), wiring pin (4), `test_maintenance_prune_direct_anti_join.py` (24), `test_vscode_proxy.py` (70/70 in S5c), `tests/test_maintenance.py` (77/77 — settled by direct run; name-grep against `-q` logs is a false negative for passing tests).

**HEADLINE for the branch:** S2 grep-verified NO failures in any `test_maintenance*` / `test_checkpoint_prune_destructive_override` node.

### 1a. Attestation family (34 files) — shard-level uninventoried, per-file parity CLEAN

S5 take-1 wedged in `test_attestation_bound_escalation.py` (real-LLM judge calls 10–14s each; 30s thread-method timeout killed pytest pre-summary). Take-2 (single file excluded) wedged in a sibling. TTQA step (S5c) excluded the whole family; a dedicated parity worker then ran every file per-file at HEAD (`--override-ini='timeout=120'`): **34/34 PARITY-PASS** — 33 files 195 tests passed, 1 file (`stage3_zoo_test`) 2 skipped identically at base (branch-drift pin, measured not assumed). Base-diff over the family = empty. **The wedge is a whole-shard contention phenomenon, not a per-file defect** — the family contributes zero new failures to the delta. Implication recorded in LESSONS: the default 30s per-test budget + whole-dir shard shape is architecturally incompatible with this family; run it as its own pack (or per-file) in future sweeps.

### 1b. Count reconciliation vs dev's "~570"

My authoritative default-partition count: **204 unique failing nodes (181F+23E) over 71 files**. The dev's "~570 pre-existing" figure is NOT reproduced under the sanctioned default partition (addopts exclude integration/postgres). Plausible sources of the difference: a run including `tests/postgres` (322 PG-gated tests fail loudly without PG) or marker-overridden invocations. **The delta — not the absolute count — is the deliverable, and it is clean (§2).** Flagged to the leader: dev evidence should state the partition when quoting counts.

## 2. New-failure delta vs base `666c089d` — THE VERDICT

Method: union of HEAD failing files (71) re-run at base in a detached worktree (`/tmp/ens-base-mc-delta-666c089d`, 4 chunks, all clean exits, ~82s total: 181F+23E+1467P), node-level diff.

| Classification | Count | Nodes |
|---|---:|---|
| PRE-EXISTING (fail both sides, same node) | **201** | see `delta/pre-existing.txt` |
| NEW-FAILURE at HEAD (raw diff) | 3 | adjudicated below → **0 branch-caused** |
| Healed at HEAD (base-only) | 3 | informational: `test_ab_resolution_threshold_met`, `test_invalid_json_in_meta_json_falls_back_to_legacy`, `test_complete_pipeline_with_real_agents` — files unchanged base→HEAD; healings from branch source changes (benign direction) |
| NEW-TEST-FAILURE / FILE-NEW | 0 | — |

### The 3 raw head-only nodes — 3×/3× retry-budget adjudication (both sides)

| Node | HEAD 3× | Base 3× | Final classification |
|---|---|---|---|
| `tests/integration/test_lcancheck_family_separation.py::TestS3WholeTreeCensus::test_s3_whole_tree_census` | F/F/F (~10.5s) | P/P/P | **ENVIRONMENTAL** — grep census walks the worktree; a live code-server Unix socket at `data_dev/vscode-user-data/code-server-*` (created by local testing the same morning) makes grep rc=2 → RuntimeError at test:1014. `_CENSUS_EXCLUDES` (test:140-148) lacks `data_dev`. Base worktree was fresh → passed. NOT branch-caused. Follow-up (report-only, out of branch scope): add `"data_dev"` to `_CENSUS_EXCLUDES`. |
| `tests/services/test_skill_evolution_service.py::TestCheckABTestResolution::test_ab_resolution_force_resolve` | P/P/P (0.13s) | P/P/P | **LOAD-FLAKE** — failed once in S6 shard under 12-way parallel pytest contention; passes 3× solo at both sides + in base chunk context. Files byte-identical base→HEAD. Not quarantined (insufficient repeat evidence); watch-listed in LESSONS. |
| `tests/unit/test_mcp_tool_timeout.py::TestToolNodeIntegration::test_tool_node_handles_timeout` | P/P/P (0.33s) | P/P/P | **LOAD-FLAKE** — same class (timing-sensitive test under heavy parallel load). |

**Delta verdict: ZERO new failures attributable to the maintenance-console branch.** The dev's triage claim ("ZERO new ones vs base") is independently CONFIRMED. The dev's enumerated triage items (3 stale pins / 2 httpx / 1 smoke-residue) cannot be counterfactually verified from current tree state (all 6 branch-modified test files are green at BOTH base and HEAD — their fixes predate the tested snapshot); no residual evidence of any missed branch-only failure remains.

## 3. Contract matrix (66 cases) — semantics, not just green

Planned→actual map: plan §4.3 file exists as planned (`test_maintenance_checkpoint_cleanup_api.py`); the commissioner-context name `test_maintenance_api_integration.py` does not exist (superseded naming — actual inventory in `RESULTS/assets/.../` recon). 66/66 planned cases implemented (+7 bonus tests); case 53 correctly retired→64; splits: 18→two, 59→matrix+match, 64→render+fallback.

| Hot-spot (task-quoted) | Case(s) | Verdict |
|---|---|---|
| Fresh-dry-run TTL expiry | 24/42 | ✅ OK — real TEXT-ISO age seeding (400s), asserts `age_seconds > 300` AND `max_age_seconds == 300`; integration twin asserts freshness window ±2s + `+00:00` |
| Byte-echo refusal (execute > confirmed echo caught) | 25/57 | ✅ OK — 25: stored-row value in body `{expected, stored}`; 57: **exceeds contract** — excess-row blobs asserted to SURVIVE (`orphans_after["cnt"] > 0`) + byte-count equality + checkpoint_count==3; three independent assertions all fail under D-first silent over-deletion |
| Origin matrix incl. `Origin: null` + evil.example | 59 | ✅ OK — `Origin: null` explicitly parametrized → 403 in BOTH unit (9 cells) and integration (7 cells) halves; parser 403s null before same-origin/trusted rules; bonus port-aware regression cell |
| Kill-switch 503×4 | 36/60 | ✅ OK — all 4 endpoints 503 `maintenance_disabled` + availability 200 `kill_switched` + boot-read pin (env flip post-import still 503); ordering origin→kill-switch→service gates pinned (case 28) |
| 409 adoption via details.run_id | 11a/26/63 | ✅ OK — holder's run_id/started_at nested under `details`; NO row for refused caller (`list_all()` count asserted); `idempotency_key` proven absent from schema AND body |
| Interrupted boot sweep | 35/61 | ✅ OK — unconditional CAS (no age gate), terminal rows untouched, exactly 1 sweep log line, `/status.in_flight is None` |
| Concurrent double-insert one-row (both drivers) | 64 | ✅ OK — PG: real concurrent (2 engines + gather) `[False, True]` one winner; SQLite: index renders + second insert refused, row-count==1; fallback test loudly skips when render works |
| MAINTENANCE_TRUSTED_ORIGINS CSV edges | fold-in (c) | ✅ OK — 8 cells: empty→no extras, whitespace-only, multi-entry, empty-between-commas, malformed inert, case-fold, near-miss host 403; parser strip+lower+drop-empties with cache reset hook |
| MANUAL-ONLY pin bites if auto tries | INV-1/INV-9 | ✅ OK — `TestManualOnlyEntryPoint::test_auto_execute_never_calls_manual_entry_point` = AST walk of `CheckpointCleanupJob.execute` asserting no call named `run_checkpoint_prunes`; reinforced by 9a behavioral pin (E before D) + case-47 source pins + singleton/kwargs AST pins |

Runtime confirmations: unit pack `timeout 300 uv run python -m pytest <4 files> --tb=short -q -rA` → exit 0, **69/69 in 4.5s** (wiring pin 4/4 incl. both MANUAL-ONLY nodes). PG pack (below) → all matrix cells PASSED.

### Gaps found (audit) + dispositions
- **G1 (MED — FIXED)**: origin guard never tested with untrusted Origin on `/dry-run` + `/runs/{id}` (silent route-wiring seam). **Fixed:** +4 parametrized cells (evil.example + null on both routes), suite 33→37 passed, commit `9dbaab90`.
- **G2 (LOW-MED — open, test-debt)**: no HTTP-level replay of `dry_run_stale` (24) or `backend_unsupported` 503-over-execute (27); unit service-level both OK.
- **G3 (LOW — open)**: AM-6 "ONE INFO log with requester forensics" on manual 409 implemented but unpinned.
- **G3b (LOW — open)**: kill-switch boot INFO line only covered by the smoke script, no automated pin.
- Non-gap notes: SQLite leg of case 64 is sequential (row-count==1 still asserted); near-miss malformed-origin literal-match documented as fail-closed.

## 4. PG integration (disposable PG only; `ensemble_prod` never contacted)

| Pack | Command (essentials) | Exit | Result |
|---|---|---|---|
| maintenance API suite | disposable PG :15432 (`initdb -A trust`); `PG_TEST_*` env; `timeout 300 uv run python -m pytest tests/integration/test_maintenance_checkpoint_cleanup_api.py --override-ini="addopts=" -m "integration and postgres" --tb=short -q -rA` | 0 | ✅ **33 passed + 1 designed skip in 9.9s** — skip = case-64 SQLite-fallback conditional (in-test `pytest.skip` gated on the partial index rendering), NOT an env dodge; matches dev evidence exactly |
| real-saver HEAD leg | PG :15434, `--override-ini='addopts=' --override-ini='timeout=120'` | 1 | ⚠️ 7P/2F — failures EXACTLY the QUARANTINE.md caplog pair |
| real-saver BASE leg | detached worktree @666c089d, PG :15435, identical invocation | 1 | **BASE-IDENTICAL 7P/2F, same node ids** → pre-existing test-debt reconfirmed (2-char logger-name fix still deferred); effective 7/7 relevant PASS |

All PG teardowns verified (ports freed, worktrees removed, zero leaks).

## 5. Functional probes (mock-based, /tmp scripts, real modules)

6/6 PASS: P1 manual `run_checkpoint_prunes` = E-before-D (both destructive and dry-run arms); P2 auto `execute()` = D-before-E AND never calls `run_checkpoint_prunes` (sentinel assertion); P3 `destructive=True`+env-off reaches DELETE (INV-2), `None`+env-off stays dry-run; P4 gate order via 6 multi-violation combos (backend_unsupported → confirm_required → not_found → byte_count_mismatch → run_in_flight → dry_run_stale precedence as specified); P5 byte-echo body carries DB-stored value not request; P6 TTL math (400s stale w/ age>300, 100s fresh, 1000s age≥900).

## 6. Live smoke (first execution under tester conventions)

`timeout 300 bash tests/manual/maintenance_console_smoke.sh` → exit 0, **12/12 PASS** in 12.5s: availability 200, status 200, origin refusal 403 (`origin_not_trusted`), availability exempt, localhost allowed, dry-run 200 (run_id minted), confirm gate 400, runs 404, runs-by-id 200, boot sweep (seeded phantom `running`→`interrupted`, second boot sweep 0), kill-switch 4×503 `maintenance_disabled` + availability `kill_switched`. Disposable DB `ensemble_mc_smoke_*` bound (daemon log proof), no `ensemble_prod` string anywhere, cleanup trap verified (port 8199 free, DB dropped).

**Script defect found + fixed (test-only, commit `691fb557`)**: the daemon-spawn `env -u` scrub list omitted `POSTGRES_URL` — and `daemon/persistence.py:137` checks `POSTGRES_URL` FIRST. Poison-DSN verification: with a hostile `POSTGRES_URL` exported, the fixed script still bound the disposable DB (grep `ensemble_mc_smoke` =3, `ensemble_prod` =0) and all checks passed. Report-only residues: `PGPASSWORD` inheritance could silently fail the cleanup DROP; boot-2 log truncates boot-1 (evidence captured via watcher).

## 7. Skips & exclusions (explicit)

1. **34 attestation files** excluded from the S5 shard inventory (whole-shard wedge class under the default 30s per-test budget) — **fully compensated** by per-file parity at HEAD (34/34 pass/skip-identical; §1a). Not a coverage hole for the delta; IS an architecture note for future sweeps.
2. **1 designed skip** in the PG suite (case-64 SQLite-fallback conditional) — by-design loud skip, primary path covered on PG.
3. **2 quarantined real-saver tests** (QUARANTINE.md 2026-09-26, base-attributed caplog pair) — reconfirmed base-identical today; still awaiting the 2-char test-side fix (out of scope).
4. `tests/postgres` (322), `tests/e2e`, `test/packs` — outside the default partition by addopts/convention.
5. Live smoke ran on PG_TEST stack defaults (localhost:5432 test creds) per the script's own design; ensemble_prod never touched.

## 8. Code changes by this commission (test-only, both committed)

| Commit | File | Change |
|---|---|---|
| `9dbaab90` | `tests/integration/test_maintenance_checkpoint_cleanup_api.py` | +53 lines, 4 origin-guard cells on `/dry-run` + `/runs/{id}` (audit gap G1); suite 37P+1S green |
| `691fb557` | `tests/manual/maintenance_console_smoke.sh` | 1 line: `-u POSTGRES_URL` in daemon env scrub; poison-verified |

No source (`daemon/`) file was modified at any point. Pre-existing foreign worktree dirt (`.agents/approver/active.md`, `.agents/approver/maintenance-console-tracking.md`, `.agents/tidier/notes.md`) disclosed and untouched.

## 9. Follow-ups (non-blocking, ranked)

1. 🟠 `data_dev` missing from `_CENSUS_EXCLUDES` (`tests/integration/test_lcancheck_family_separation.py:140`) — any live code-server session in the worktree breaks the census test environmentally. 1-line test fix (out of branch scope — owner's call).
2. 🟢 G2/G3/G3b test-debt (HTTP replays for stale/unsupported gates; INFO-forensics pin; kill-switch boot-line pin).
3. 🟢 Attestation-family pack architecture: give it its own labeled pack (or per-file legs at `timeout=120`) in future whole-tree sweeps; document in PACKS.md when done.
4. 🟢 Real-saver 2-char caplog logger fix (QUARANTINE.md) — still the cheapest test-debt win in the tree.
5. 🟢 Smoke-script residues: PGPASSWORD-inheritance DROP failure mode; boot-log truncation (watcher workaround exists).
6. 🟢 Dev-evidence hygiene: quote the pytest partition when citing failure counts ("~570" not reproducible under default addopts).

## Artifacts

All under `.agents/tester/RESULTS/assets/2026-09-27-maintenance-console/`: `wt-head/` (9 shard logs + inventories), `delta/` (head/base inventories, verdict.txt, dev-triage), `attestation/` (35 logs), `newfail/` (18 adjudication logs), `smoke/`, `contract-unit.log`, `pg-api.log`, `realsaver-{head,base}.log`, `g1-gapfix.log`.

Worker instances (16): de499f06 recon · 3395b418 audit · a5f615d4/cff3b35f/5570dd1a/c848cc8e/5260491d/5259b893/08c1d4f4/345bf34a/8b207f50 shards S1/S2/S5/S6/U1/U2/U3/T1/T2 · 683f3b2d S5b · 8e51809d S5c · 2cd27cef attestation-parity · 45f627cf contract-unit · 685eec3e PG-A · 3c02c141 PG-B · e84346c7 probes · 66cc1205 smoke · bb6367cd G1-gapfix · 2dff36bd smoke-fix · 160c37b9 base-delta · 8500b6c5 adjudication.
