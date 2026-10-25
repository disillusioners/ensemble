# Independent Release-Gate Verification — mission-records PR1 (completion-subsystem Proposal B)

- **Date:** 2026-10-25 (08:17–10:50 UTC window)
- **Verifier:** tester (Test Leader) + 12 dispatched workers: recon=5cc91d43 · pg-fresh=fe8227d3 · pg-existing=3a7298fa · mutations=ec2fc137 · g1-mergegate=c8e7f07c · g4-sqlite=55756ee1 · g5-facade=5342f1d2 · g6-e2e=b803a20a · g9s-focused=90f4c3e4 · h1-audit=00d321db · commission-repro=8b64ba39 · g7-abpool=8d11eb06
- **Branch:** `feature/mission-records-pr1` @ `0428c936eaf7421b0bf4c5e7752f1053bfb339ef` — 7 commits on base `7af1d2632` (= `origin/latest` exactly, 0 behind, ancestor-proven); diff 36 files +5,988/−38 (22 daemon source, 4 migration files, 9 test, 1 planning doc)
- **Worktree:** `agents-ensemble-wt-mission-records-pr1` · fence `.env` sha256-16 `1937ecd11688af43` (ENSEMBLE_SELF_ENV=dev, DAEMON_PORT=8091) · Python 3.13.3 · pytest 9.0.2 · pytest-randomly NOT installed
- **Pre-existing dirt (disclosed, untouched):** ` M .agents/tidier/notes.md` (foreign tidier session)
- **Mandate compliance:** NO daemon restart (none was running; ONE self-hosted worktree daemon started + cleanly stopped for G6, port-verified teardown) · NO TCC · NO force-push · NO pushes · `ensemble_prod` NEVER touched — fence-asserted `host=localhost AND port=5432 AND dbname=<scratch>` before every PG touch; ambient `POSTGRES_*`→prod vars (present in worker shells, password included) neutralized by `scripts/run_tests_scrubbed.sh` (non-PG runs) or the hand-scrubbed fence env (PG-needing runs) · port 8088 untouched throughout.

## Verdict

**GATE = FAIL — back to dev. Two branch-caused items, both root-caused with proven, cheap fixes:**

| # | Class | Item | Decisive evidence |
|---|---|---|---|
| 🔴 1 | **PRODUCTION DEFECT (commission persistence)** | `send_message` lane: the commission row — and the rung-3 mission row when minted in the same region — is written into a caller-owned session that is NEVER committed. `WriteGuardSession.__exit__` → `Session.close()` = rollback, AFTER the success log fired. Live E2E DB: `mission_commissions = 0` rows despite a logged `COMMISSION lane=send_message … commission=b61c4932…`. | PG repro with the REAL repo/service code (scratch `mission_pr1_repro`): **A** watcher lifecycle (`session=`, no commit) → `0 ROWS` (both tables); **B** control (`session=None`) → persists (1/1); **A2** fix probe (`session.commit()` in-block) → persists. Site: `daemon/tools/instance.py:1206` (session open), `:1263-1268`/`:1271-1279` (mints, `session=`), ZERO commit/flush in `:1154-1338`; `daemon/write_pause_guard.py:360-391` (close, no commit). **SQLite MASKS this class** (pysqlite: savepoint RELEASE commits when no outer BEGIN) — the green unit suite structurally cannot catch it; the dev's regression test MUST be PG-backed. |
| 🔴 2 | **TEST DEBT — 6 deterministic reds, one root cause** | `message_tap.stamp_persisted` now emits 4-tuple `upsert_batch` items `(mid, now_iso, None, mission_id)`; 6 stale test seams still unpack 3 → `ValueError: too many values to unpack (expected 3)`. Production is CORRECT — `MessageMetadataRepository.upsert_batch` was widened in-branch to `mid, ts, seq, *rest` (both shapes valid). Exact analogue of the prior train's `populate_existing` stale-mock class. | G7 solo 3/3 FAIL @ HEAD vs 3/3 PASS @ base. Sites: `tests/test_injection_graph.py:949` `:1010` `:1117` `:1198` · `tests/unit/services/test_w5_claim_order_wc_wake.py:950` · `tests/unit/test_context_messages.py:961`. Fix: widen the 6 unpacks (`*_rest`); re-run root-a/root-b + unit-services shards both legs (~2 min edit + ~7 min re-runs). |

Everything else **PASSES**: fresh-PG + existing-PG migrations, focused pack 112/112, facade guards 18/18, reviewer merge-gate 43/43, SQLite integration 16/16, mutations 5/5 categories, revive-mint 5/5, E2E terminate-revive + 3-level cascade green, live mint lanes fire (`mission_records`=5 rows in E2E). The 2 E2E vocabulary reds are **BASE-attributed** (M3 `settled` drift, commit `05618c55c`, ancestor of base; empty diffs on the test file AND job-queue routers/repos) — quarantined this session, NOT PR1.

## Per-gate verdict table (1–10)

| Gate | Verdict | Evidence (one line each; details in sections below) |
|---|---|---|
| 1 Reviewer merge-gate @ final HEAD | ✅ PASS | 43/43 green: full `tests/unit/test_chat_source_live_injection.py` (38 nodes, incl. `test_set_injection_called_enqueue_not_called` + `test_threads_echo_id_and_source`) + all 5 `TestResolveOrMintFailOpenSignatureDriftPin` nodes (the NEW H1 wrapper pin — lives in `test_mission_record_service.py`, not the chat file). H1 lineage proven (`15b67d717` ancestor of HEAD; kw-only param `:976`; forwarding `:386/:396/:423`). 6s. |
| 2 Migrations — FRESH PG | ✅ PASS | Scratch `mission_pr1_fresh`, real boot path (InstanceManager init: create_all + runner + `_ensure`): 78 tables; `mission_records` 15 cols / `mission_commissions` 11 cols, types match models; 3 additive columns (`report_injections.mission_id` text, `.commissioner_instance_id` text, `message_metadata.mission_id` varchar(64)) all nullable, no server default; 6 indexes incl. partial UNIQUE `ux_mission_records_anchor_founding_msg` (survived to PG verbatim). **Ledger EMPTY on PG = by-design** (runner.py:837-842 early-return fires BEFORE `ensure_migrations_table`:851 → runner never enumerates `.sql` on PG; table exists only via SQLModel registration in create_all). Idempotent: runner 4× (`[]` each), create_all 3× (existence-checks only), `_ensure` 3× (137 stmts incl. the 3 mission ALTERs) — zero errors, zero diffs (78 tables / 15/11 cols / 3+3 idx / 0 ledger before==after). DB dropped, verified absent. |
| 3 Migrations — EXISTING PG | ✅ PASS | Pre-PR1 simulation: base `7af1d2632` create_all → 73 tables, mission tables + 3 columns ABSENT (information_schema), seeded 1 report_injection + 2 message_metadata rows. PR1 upgrade (boot order): tables land via **create_all** (model registration, `repositories/__init__.py:107`); 3 columns land via **`_ensure_postgres_columns`** (manager.py:8352-8354, exactly 3 mission `ADD COLUMN IF NOT EXISTS`); runner no-ops on PG (logged skip, cited). Second full pass: zero errors, no dupes (75→75 tables, colcounts 1/1/1, indexes 6→6, ledger 0→0), seeded rows byte-intact. Runner idempotency contract quoted (runner.py:684-688/703-707/728-735 = SQLite-lane belt). Base worktree removed (no --force); DB dropped. |
| 4 File-backed-SQLite integration | ✅ PASS | 16/16 via scrubbed runner: `test_d2c_settle_hold_real_db.py` 7/7 (real `fetch_pending_for_targets`; PENDING watcher holds the settle; fired watcher releases) + `test_mission_record_migrations_sqlite.py` 4/4 (apply-and-land, idempotent-reapply, partial-unique mint idempotency, rollback-safety) + `test_mission_record_revive_enqueue_integration.py` 5/5. NOTE: D2c self-documents as a legacy dependency-watchers closure riding the branch (NOT PR1 coverage; PR1 coverage = the five `test_mission_record_*` files) — gate-required anyway. Ambient prod-cred `POSTGRES_*` neutralized by the scrub wrapper (fail-closed, exit-78 class). |
| 5 Facade-forwarding guards | ✅ PASS | 18/18: pinned-tuple semantics post-rename 3/3 (`test_phase1_facade_signature_unchanged.py`) · `mission_id` kw-only + forwarded through `enqueue_message`/`enqueue_message_job`/`_process_message_with_tracking` 8/8 (`test_mission_record_facade_forwarding.py`) · work_id unit 4/4 + real-dispatch integration 3/3 (solo runs clean — the line-145 containment row covers full-shard context only, never implicated). |
| 6 Spawn-verification E2E (real LLM) | ❌ **FAIL** | terminate-after-spawn-revive **PASS** 67s · 3-level cascade **PASS** 157s · revive-mint file **5/5 PASS** · `mission_records` **5 live rows** minted through real E2E flows (user_api×4, revive×1, all `provenance=live`,`status=open`,`epoch_seq=1`) · boot backfill `dry_run=True scanned=0` (correct on empty DB) — **BUT commission-persistence defect 🔴1** (logged COMMISSION mint → 0 rows; repro-confirmed). pause-resume + happy-path FAIL = base-attributed M3 `settled` drift (quarantined; workflows actually COMPLETED — daemon log `terminal_status=completed`, DB `admission_state=done`). Daemon: self-hosted worktree uvicorn on 127.0.0.1:8091 (dev.sh:155 shape; documented deviations: port, host, no `--reload`), scratch DB `mission_pr1_e2e` (82 tables incl. both mission tables at boot), health 200 `/api/health` v0.27.7, queue-hygiene 0 pending at all 4 checkpoints, port-verified SIGTERM teardown, DB dropped. **Skip list: NONE** (all 4 E2E nodes + 5 revive-mint nodes ran). |
| 7 Base-leg full-pool A/B | ❌ **FAIL (test-side)** | 27 pool + 1 isolated + 3 attestation shards per leg, `timeout 1750`/shard, fence-satisfied env, symmetric deselects (967 marker + 39 containment row-145 + 1 `[1x10k]`), no whole-tree collection, no timeout relaxed. Set diff 13 = 417 common + 6 base-only + 7 head-only → **6 branch-caused (🔴2 above), remaining 7 = 0 branch-caused** (dequeue ×2 rows 53/55 · prompt_version ×2 row 146 · status_transitions swap standing row · `test_resources_anomalies::test_seeded_finding_full_sections` session-state flake PPP/PPF → NEW quarantine row · `test_phase1_facade_signature_unchanged::…did_not_grow` FFF@BASE **repaired by the branch** — the branch diff itself documents this pre-existing red; A/B independently confirms). Prior-train 6 nodes (`test_send_message_success` + `TestTitleGenerationTrigger`×5): GREEN 3/3 BOTH legs (fix in base, as required). `upgrade_executor` isolated shard: rc=1, 159 INTERNALERROR lines, byte-identical signature both legs. |
| 8 Mutation checks | ✅ PASS | 5/5 mandated categories RED→GREEN via 6 line-neutral edits (both H1 wrapper branches; rung-2 `if commission is not None:` ; `WHERE state='outstanding'` guard; UUID5 seed determinism — NOTE: no `ON CONFLICT` exists anywhere in the mission_record layer, the docstrings' claim is drift; mode default `dry`→`off` with 7-red blast incl. `test_default_is_dry`). All restores sha256-identical (both mutated files cross-checked against the PR1 worktree); mut worktree `git status` EMPTY, HEAD unmoved, worktree removed. **Finding:** the REAL H1 call site (`daemon/sources/registry.py:1164`, `founding_message_id=echo_id`) is UNPINNED — deleting it leaves 118/118 green. |
| 9 Full + short runs | short ✅ PASS 112/112 · full ❌ = G7 HEAD leg | Short = mission-records focused pack (8 files; node counts 43/8/4/13/5/20/16/3 exact; collection == execution; 6.67s). Full = the A/B HEAD leg per the end-of-train convention (no duplicate full run): 27,768 executed, 27,063P/375F/49E/281S, failing-set 424 — fails on 🔴2. |
| 10 Evidence integrity | ✅ this file | + PACKS.md ad-hoc pack block (F-3 closed) + QUARANTINE.md 2 new rows; committed on the branch. All scratch DBs dropped, all auxiliary worktrees removed (receipts in §Artifacts). |

## Gate 6 detail — E2E per-test + defect

| Node | Exit | Verdict | Runtime | Evidence |
|---|---|---|---|---|
| `test_pause_after_spawn_then_resume` | 1 | FAIL — **base-attributed** | 118s | `:1927` "did not reach a terminal job state … (status=settled)"; job actually completed (`admission_state=done`, `terminal_reason=completed`) |
| `test_terminate_after_spawn_then_revive` | 0 | **PASS** | 67s | terminate→revive round-trip green |
| `test_parent_child_workflow_happy_path` | 1 | FAIL — **base-attributed** | 74s | `:1429` "Expected terminal status in first SSE event … got status='settled'" |
| `test_three_level_cascade_reports` | 0 | **PASS** | 157s | reports delivered, no premature/stuck completion |
| revive-mint file (5 nodes) | 0 | **PASS 5/5** | 1.24s | fresh-episode mint, epoch chaining, mode-off no-mint, internal-agent no-mint, queue-row stamping |

**Defect mechanism (🔴1):** `daemon/tools/instance.py::_register_child_completion_watcher` `:1206` opens `WriteGuardSession(Session(manager.engine), manager.write_guard)`; `:1263` `resolve_or_mint_fail_open(..., session=session)`; `:1271` `mint_commission_fail_open(..., lane="send_message", session=session)`; the function body `:1154-1338` contains ZERO commit/flush/begin; `WriteGuardSession.__exit__` (`write_pause_guard.py:360-391`) closes → implicit ROLLBACK. `mint_commission_fail_open` wraps in `begin_nested()` — clean exit RELEASEs the savepoint INTO the still-uncommitted outer transaction, then logs success (`:1231-1238`) — the log reports a commission id that never becomes durable. The watcher row itself is unaffected (`dependency_bus` uses its own repository). **Per-lane persistence census:** send_message = LOST (PG-proven); revive = persists (caller session BUT enclosing `_prepare_enqueued_message` commits at `instance_messaging.py:2730`); instance_nudge / user_api / chat / scheduler(when armed) / job_continue / boot-backfill = repo-owned transactions, persist. **Fix direction (analysis-only, not applied):** preferred `session.commit()` in the watcher region after the commission try/except (REPRO-A2-proven; preserves the same-transaction lane contract, mirrors the revive lane), or drop `session=` at `:1267/:1277` (2-kwarg diff; abandons same-transaction semantics before PR2/3 readers). Either way: durability log post-commit + a **PG-backed** regression test.

**Repro outputs (PG, real code, verbatim):** A: `SELECT commission_id=bc558745… -> 0 ROWS`, `mission_records=0` · B: FOUND, 1/1 · A2 (commit): FOUND, 2/2. SQLite probe: row FOUND on SQLite (dialect artifact: `in_transaction=False` after SELECT → SAVEPOINT starts txn → RELEASE commits it) — **SQLite is not a valid substrate for this defect class.**

## Gate 7 detail — full-pool A/B

| | BASE `7af1d2632` | HEAD `0428c936e` |
|---|---|---|
| Census (collected) | 30,237 | 30,317 (+80 = 6 branch-new test files) |
| Executed (P+F+E+S) | 27,690 | 27,768 |
| P / F / E / S | 26,986 / 372 / 51 / 281 | 27,063 / 375 / 49 / 281 |
| Deselected | 1,007 (967 marker + 39 containment + 1 `[1x10k]`) | identical |
| Failing-ID set | 423 | 424 |
| Leg wall | 32m32s | 34m03s |

- **Set diff 13 = 417 common + 6 base-only + 7 head-only.** 6 branch-caused = 🔴2 (solo 3/3 F@HEAD, 3/3 P@BASE). Remaining 7: 4 solo-P 3× both legs (dequeue ×2 → rows 53/55; prompt_version ×2 → row 146) · status_transitions swap member (standing 2026-10-05 row) · `test_resources_anomalies::test_seeded_finding_full_sections` base-PPP/head-PPF session-state flake (NEW quarantine row) · `test_phase1_facade_signature_unchanged::…did_not_grow` FFF@BASE, node renamed+repaired at HEAD (pre-existing red the branch REPAIRS — confirmed independently).
- **Claim-checks:** prior-train 6 nodes green 3/3 both legs ✓ (one cosmetic note: `test_send_message_success` solo run2 hit the 30s per-test timeout SYMMETRICALLY on both legs — pre-existing latency flake, 2/3 pass both legs). Dev claim "4 failures pre-existing at 15b67d717": strong reading FALSIFIED at `0428c936e` — the diff contains 6 NEW deterministic HEAD reds (the exact 4-id list was not recoverable from the dispatch; any 4-subset of the 417 common reds trivially matches base). At least one pre-existing deterministic base red CONFIRMED (the facade-pin node the branch repairs).
- Branch-new/modified test files at HEAD: **0 failing nodes** (all 80 new nodes green in-leg).
- **Deviations (all documented, symmetric):** (1) directory-batch `--collect-only -q` census (per-file ≈105 min/leg didn't fit; node-id lines identical, summaries cross-checked); (2) `.venv/bin/python -m pytest` frozen a4 form (a4_preimport shim needs `-m` sys.path semantics; `run_tests_scrubbed.sh` unusable for fence legs — exit-78 on the required `POSTGRES_DB`; hand-scrubbed env IS the scrubbed posture); (3) **load-bearing fence catch:** worker shell inherits `POSTGRES_DB=ensemble_prod` + password — fence `-u`'s both before pinning `ensemble_dev_test_only`; asserted on every PG touch; (4) first BASE launch died on host bash 3.2 (`mapfile` + `set -u` empty-array) — zero tests executed, relaunched clean; (5) HEAD-leg path typo in the dispatch corrected from the claim KV. Shard bump: `unit-root` 3→4 chunks (3,400 > 3,300 cap after +80 nodes) — 27 pool shards vs prior train's 26.

## Gate 8 mutation table

| # | Site | Edit | RED nodes | Restore sha |
|---|---|---|---|---|
| a | `mission_record_service.py:1021` (plain branch) | drop `founding_message_id=` forward | `test_plain_branch_forwards_founding_message_id` + `test_omitted_kwarg_forwards_none_default` (2F/41P) | `32f9d88c…` ✅ |
| a2 | `mission_record_service.py:1011` (savepoint branch) | drop forward | `test_savepoint_branch_forwards_founding_message_id` (1F/4P) | `32f9d88c…` ✅ |
| b | `mission_record_service.py:480` (rung 2) | `if commission is not None and False:` | `test_rung2_commissioned_worker_resolves_commissioners_mission` + `test_rung1_beats_rung2` + `test_rung2_resolves_oldest_outstanding` (3F/77P) | `32f9d88c…` ✅ |
| c | `repositories/mission_record/repository.py:339` | delete `WHERE state=='outstanding'` guard | `test_commission_satisfied_only_from_outstanding` (1F/79P) | `6af89200…` ✅ |
| d | `repository.py:513` (UUID5 seed) | append `:{uuid4()}` → non-deterministic | `test_committed_nudge_to_non_terminal_target_backfills` (1F/79P) | `6af89200…` ✅ |
| e | `mission_record_service.py:219` (mode default) | `DRY`→`OFF` | `test_default_is_dry` + 6 collateral (7F/73P) | `32f9d88c…` ✅ |

Reviewer-gate nodes under mutation (a): both still PASSED — the pin is the discriminating test, as designed.

## Gaps ledger (untested + findings; report-only, nothing fixed here)

**Critical (= the two FAIL items):** fix 🔴1 (commission persistence + PG-backed regression test) and 🔴2 (6 unpack widenings) before merge.

**Important:**
1. **H1 chat-lane caller pin missing** — no test drives `SourceRegistry._handle_message` and asserts the mint call carries the echo id (deleting `registry.py:1164` stays 118/118 green). Fix shape: in `TestRunningLiveGraphInjection`, spy `daemon.sources.registry.resolve_or_mint_fail_open` (sync MagicMock — the call rides `asyncio.to_thread`) and assert `kwargs["founding_message_id"] == set_injection.call_args.kwargs["echo_id"]`; cheap textual variant: assert `"founding_message_id=echo_id"` in `inspect.getsource(SourceRegistry._handle_message)`.
2. **ON CONFLICT docstring drift ×3** (`repository.py:505`, `models.py:347`, `mission_record_service.py:692`) — no `on_conflict` exists in the layer; real idempotency = UUID5 determinism + `get_commission` pre-check; TOCTOU window between pre-check and insert (boot-time-only exposure — backfill runs once at boot, `manager.py:1050`); optional hardening: catch `IntegrityError` → no-op, mirroring the epoch path `:406`.
3. **E2E terminal-set vocabulary drift (base defect):** add `settled` to the two tests' terminal sets or assert `admission_state`/`terminal_reason` — future train; 2 nodes quarantined meanwhile.
4. **Deployment guidance:** default mode `dry` = mints WRITE (`mission_records`/`mission_commissions` rows on every mission; only the scheduler lane is dual-gated OFF). The prod flip should set `ENSEMBLE_MISSION_RECORDS=off` explicitly until PR2/3 readers land, or accept the write volume consciously.

**Nice-to-have:**
5. `founding_message_id` completeness at 4 sites (instance `send_message`, `instance_nudge`, nudge-terminal revive, scheduler[when armed]) — honest NULLs, guarded first-wins `attach_founding_message` can backfill later; not H1-class.
6. `mission_id` namespace collision (new UUID4-per-epoch vs legacy read-model `mission_id == instance_id`) — deliberate and documented in the migration header; grep with care.
7. Server-side DEFAULT divergence: PG create_all lands `NOT NULL` without server DEFAULT where the SQLite `.sql` declares `DEFAULT 1/'open'/'live'/0/'outstanding'` — inert for the ORM write path (zero raw `INSERT INTO mission_*`); matters only if PR2/3 writes raw SQL.
8. **Live-daemon PG paths under the no-restart fence** — E2E exercised a self-hosted worktree daemon + scratch PG; prod-live activation behavior remains untested by design of this gate.
9. Pre-existing boot warning (unrelated to PR1): builtin Plane MCP warmup validation error (`streamable-http` vs `McpStdioConfig`, `builtin_servers/plane.py:339`).

## Quarantine updates (this session)

1. M3 `settled` vocabulary drift ×2 (E2E pause-resume + happy-path) — base-attributed (empty diff + root-cause commit `05618c55c` ancestor of base); deterministic.
2. `test_resources_anomalies.py::test_seeded_finding_full_sections` — session-state flake (base PPP / head PPF solo 3×); new row.

## Artifacts

- `/tmp/mrpr1_gate2/` (14 logs + 4 scripts, incl. boot_path_v2 phase-tagged DDL capture) · `/tmp/mrpr1_gate3/` (pre-state, 2 upgrade passes, contract excerpts) · `/tmp/mrpr1_gate6/` (daemon.log 1297 lines, e2e_*.log, launch.sh) · `/tmp/mrpr1_gate7/` (RESULTS.md, `logs/<leg>-<shard>.{log,meta,ids}`, `set-diff.json`, `solo-results.json` 19 nodes × 3 × 2 legs, `shards/shards.json`, sha-pinned scripts) · `/tmp/mrpr1_gate8/` (21 logs + MUTATION_LEDGER.md) · `/tmp/mrpr1_commission_repro_logs/` (repro A/B/A2 transcripts) · `/tmp/gate9short_formal.log`
- **Scratch cleanup receipts:** `mission_pr1_fresh`, `mission_pr1_exist`, `mission_pr1_e2e`, `mission_pr1_repro` ALL dropped (verified absent in pg_database; 0 lingering conns); worktrees `-base`, `-mut`, `-ab-base` ALL removed (`git worktree remove`, no --force); `ensemble_dev_test_only` left as-is (pre-existing); ENSEMBLE_DATA_DIR scratches wiped; stash lists identical pre/post.

## Re-verification plan (delta pass after dev fixes; do NOT re-run the full gate)

1. 🔴1 fix: `session.commit()` at scope (A2-proven) or `session=None` at `instance.py:1267/:1277`; durability log post-commit; NEW PG-backed regression test (SQLite green proves nothing).
2. 🔴2 fix: widen the 6 unpacks (`*_rest`); re-run root-a/root-b + unit-services shards on both legs.
3. Delta verify: focused pack (112) + the two re-run shards + `mrpr1_e2e_spawn` send-message commission-row check (`mission_commissions ≥ 1` persisted) + the new PG regression test green.
