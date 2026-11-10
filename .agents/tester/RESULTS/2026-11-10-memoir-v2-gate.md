# Memoir Store v2 ("The Reflecting Mind") — FORMAL TESTER GATE

- **Date:** 2026-11-10
- **Project:** agents-ensemble
- **Worktree (real checkout):** `/home/nea/ensemble-src-wt-memoir-v2`
- **Branch:** `feature/memoir-v2-reflecting-mind`
- **Gate-open HEAD:** `e83b69ebe` · **Gate-authored test-only commits (5):** `0719ed5e6`, `e92abdcd3`, `883ce7535`, `a8760fcc7`, `a38e5e33b`
- **Base:** `f49006b92` (`merge-base --is-ancestor` OK)
- **Lane commits:** **40** (dispatch said 28 — discrepancy recorded)
- **Change set:** 98 files, +33,028 / −126
- **Dirty tree:** exactly 2 FOREIGN entries (`.agents/approver/active.md`, `.agents/approver/memoir-v2-reflecting-mind-tracking.md`) — approver lane, **not this lane's**
- **Verdict:** see §11

---

## 0. Executive summary

| Obligation | Verdict |
|---|---|
| 1. REAL-PG fenced legs (phase-1/4/5/6) | **PASS** — 47 nodes across 3 registered PG packs + 1 ad-hoc leg; 10/10 mutations → detectors; 0 vacuous |
| 2. REAL boot smoke + W8 ensure blast-radius | **PASS** — real boot, 3-worker census, fresh PG, kill-switch park; W8 abort proven (exit 3), zero partial DDL, control leg discriminates |
| 3. Lane packs + mutation-verified safety pins | **PASS** — 26/26 packs green, every `EXPECTED_PASSED_N` matched; **9/9 pins mutation-proven detectors** |
| 4. Plan→code traceability matrix | **PASS (with 6 NO-DETECTOR ACs)** — 45 ACs + 11 tasks classified; 39 PINNED, 13 with receipts, **6 no detector** |
| 5. PACK registration | **PASS** — 26 rows registered with scope + in-script pin; plan-vs-tree gap clause included |
| 6. Attribution discipline | **PASS** — all 5 pre-existing buckets verified; **3 NET-NEW found and fixed by the gate** |
| 7. Live-LLM feasibility | **PASS (NOT-RUN as a pin)** — `FEASIBLE BUT NOT GATE-SUITABLE`; 3-request live smoke executed; 9→8 non-determinism at temp 0.0 |
| 8. Environment fences | **PASS** — per-variable scrub everywhere, no Docker, no live DB contact, no live-install writes, install-audit sha256 unchanged across ~300 pytest invocations |
| `ensure.md` Core | **PASS with 1 quarantined pre-existing pack** |
| `ensure.md` Release-Gate | **PASS** — Tier-2 whole-tree adjudicated; 1 of 4 E2E scenarios executed green on isolated scratch PG |

**Net-new regressions: 3 found, 3 fixed, 0 open.** (Each was a *stale census/sanction pin*, not product behaviour.)

---

## 1. Environment / fences (established, then used)

| Fact | Value |
|---|---|
| Interpreter | worktree `.venv` → Python **3.14.7**, pytest 9.0.2, psycopg 3.3.4, sqlalchemy 2.0.48 |
| Ambient `POSTGRES_*` | **LIVE `ensemble_prod` @ 10.44.0.2** — never contacted; scrubbed per-variable on every command |
| Ambient `ENSEMBLE_AUTH_*` | **7 vars present** — whole-family scrub is mandatory (see §8 harness defect) |
| Worktree `.env` | `ENSEMBLE_SELF_ENV=dev`, `POSTGRES_DB=ensemble_dev` @127.0.0.1:5432 (gitignored) |
| Native PG | 16.15 at `/usr/lib/postgresql/16/bin`; system cluster on 5432 |
| Docker | absent / banned |
| Headroom | 75 G disk, ~9 GB RAM, 16 cores (OOM risk on naive xdist farms — no xdist used anywhere) |
| Live daemons | live v0.29.7 on 9797/9798; demo on 7979 — untouched |
| Port hazard | daemon **DEFAULT port is 8088** (the self-system) — every boot pinned `PORT` explicitly |
| Tracked-file hazard | `install-audit.jsonl` sha256 `881e133a…` — **unchanged before/after every run** |
| Frozen binary | none in worktree; all boots via `.venv/bin/python -m daemon` / `uvicorn` |

**Disposable-cluster recipe (proven, ~1.5 s):** `initdb -A trust --encoding=UTF8 --no-sync --locale=C`; `pg_ctl … -o "-p <port> -k $SCRATCH -c listen_addresses=127.0.0.1 -c fsync=off -c full_page_writes=off" -w start`. Timings: initdb 1.1 s · start 0.14 s · createdb 0.23 s · first TCP connect 9 ms.

**PG DSN contract:** `PG_TEST_HOST/PORT/DB/USER/PASSWORD`, URL `postgresql+psycopg://…`. `tests/postgres/conftest.py` carries a **real scratch-DB fence** — `check_scratch_db_fence()` (session-scoped autouse) with an exact-name allowlist, forbidden set `{ensemble_prod, ensemble_dev, ensemble_prod_test}`, and an ownership check. Structural guard against this host's ambient live DSN; observed green on every PG run.

**Editable-install trap (load-bearing, caught independently 3×):** the worktree `.venv` carries `_editable_impl_ensemble.pth` → the worktree, so a base-leg run inside that venv silently imports **HEAD**. Every base leg therefore used `sys.path.insert(0, <base>)` + `os.chdir` + a hard-fail `IDENTITY: <daemon.__file__>` assertion.

---

## 2. Obligation 1 — REAL-PG, fenced

### 2a. The three registered PG packs (own disposable cluster, serial)

| Pack | Result | Passed/Failed/Collected | Expected | Wall |
|---|---|---|---|---|
| `memoir_v2_schema_pg` | **PASS after quick fix** (was 14/15) | 15 / 0 / 15 | 15 | 6.5 s |
| `memoir_v2_poignancy_pg` (phase-4, first formal fenced execution) | **PASS** | 15 / 0 / 15 | 15 | 4.2 s |
| `memoir_v2_reflection_pg` | **PASS** | 17 / 0 / 17 | 17 | 5.0 s |

**NEW RED #1 (found here, fixed):** `tests/postgres/test_memoir_v2_schema_pg.py:362 test_the_shipped_seed_literal_is_space_separated` asserted the exact no-offset literal while the shipped seed is `'2026-11-09 00:00:00+00'`. `implementation-notes §17.5.3` states the two cutover literals are **intentionally distinct** (manager.py `+00` vs SQLite none — "Do not unify them"); V2-33/SF11 only forbids the ISO-8601 `T` form. The pin over-specified → test-side fix ('space-separated, no `T`, optional `+00`'), commit `0719ed5e6`, re-run 15/15.

### 2b. Ad-hoc fenced leg — phase-5 + phase-6 (26/26 nodes, 10/10 mutation detectors, 0 vacuous)

Schema path: the REAL path with no daemon boot — import-sweep → `SQLModel.metadata.create_all` → the REAL `InstanceManager._ensure_postgres_columns()` on an `InstanceManager.__new__` shell (AST-verified to read only `_engine`, `_ensemble_config`, `_migrate_overloaded_image_refs_rows`).

| Node | Result |
|---|---|
| A / A2 `memoir_jev_ledger` exists + types + roundtrip | PASS |
| B / B2 `memoir_persona_versions` declared exactly once; seed idempotent (`approved`/`seed`) | PASS |
| C1/C2/C3 `uq_memoir_proposal_agent_episode` catalog (agent_id, episode_id); duplicate rejected; different episode accepted | PASS |
| D `notified_at` = `timestamp with time zone` | PASS |
| E1/E2/E3 UTC day-window count 2; **status-independent** (3 rotations); **REAL** `count_notified_today` = 2 | PASS |
| F1/F2 **REAL** `scan_candidates`: ledgered excluded, unledgered eligible included, `skipped_ineligible=1`; wire SQL captured via `before_cursor_execute` | PASS |
| G1/G2 four tokens accepted; `'delivered'` rejected by `ck_memoir_proposal_ledger_notification_status` | PASS |
| STEP3b migration runner NO-OP on PG (125 files discovered, 0 applied) | PASS |
| STEP3c ensure idempotent re-run | PASS |
| STEP4 pre-v2 evolution: `create_all` creates NEW tables but not new columns; ensure then adds `tsv/poignancy/poignancy_attempts/reflected_at/poignancy_status` | PASS |

**FINDING (release-notes class): the UTC day-window predicate is session-TZ conditional.** `date_trunc('day', now() at time zone 'UTC')` is `timestamp WITHOUT time zone`; compared to a `timestamptz` column, PG coerces the boundary into the **session** zone. Measured `TimeZone=UTC → 2`, `America/New_York → 1`. The service path (Python tz-aware bounds) returns 2 in both. **Production is safe only because the manager engine pins `-c timezone=UTC` (`daemon/repositories/factory.py:259,299`).** Any future engine built without that pin silently breaks the 1/day anti-spam gate.

---

## 3. Obligation 2 — REAL boot smoke + W8 induction

### 3a. Boot smoke (scratch PG 16, fresh DB, `uv sync --frozen` exit 0 BEFORE boot)

| Assertion | Result | Evidence |
|---|---|---|
| A1 health 200 | PASS | `{"status":"healthy","version":"0.29.6","current_database":"postgres","postgres_env_available":true}` |
| A2 **3-worker census** | PASS (with disclosure) | start lines `[MemoirEmbed] … dims=1536`, `[MemoirReflect] … thread=memoir-reflect`, `[MemoirPoignancy] … thread=memoir-poignancy`; live threads `memoir-embed_0`, `memoir-poignanc`. **`memoir-reflect` OS thread is LAZY** (ThreadPoolExecutor spawns on first submit; fresh/idle DB at 13:43 UTC had none) ⇒ executor-task + log evidence used. Disclosed, not papered over. |
| A3 fresh-PG schema | PASS | 10 memoir tables (6 v2 + `memoir_persona`, `memoir_persona_versions`, `memoir_syntheses`, `memoir_episodes`); episode table carries all 4 new columns |
| A4 stability | PASS | peak RSS 323 MB, graceful SIGTERM, no OOM |
| B1 reflection OFF | PASS | no-spawn (`explicit_off`), other two started, healthy |
| B2 global OFF | PASS | embedding no-spawn, reflection+poignancy started |
| B3 poignancy OFF | PASS | task created then **parks within one tick** — documented kill-switch half-liveness asymmetry |

Knob→worker map: `ENSEMBLE_MEMOIR_ENABLED`→embedding (boot-gated) · `ENSEMBLE_MEMOIR_REFLECTION_ENABLED`→reflection (boot-gated) · `ENSEMBLE_MEMOIR_POIGNANCY_ENABLED`→poignancy (created always, parks per tick).

### 3b. W8 ensure blast-radius induction (scratch clone, malformed DDL)

| Assertion | Result | Evidence |
|---|---|---|
| W8-1 boot halts | PASS | **exit 3** (uvicorn startup-failure; distinct from 75/78/1/124), fast clean exit |
| W8-2 log names the memoir statement | PASS | `psycopg.errors.UndefinedObject: type "no_such_type" does not exist` + `manager.py:9834 _ensure_postgres_columns` + `[SQL: ALTER TABLE memoir_episodes …]` |
| W8-3 zero partial DDL | PASS (a, c) | `diff cols_before cols_after` → identical (1083 lines); `poignancy_status` not added |
| W8-4 inherited state untouched | PASS | sentinel row `1 \| before-boot` intact |
| W8-5 order-independent, one transaction | PASS | `manager.py:9828-9830` one `engine.begin()` + bare loop + zero try/except; **all later non-memoir ensure lines absent** from the abort log (drift_events, resource_monitor, checkpoint-trio) |
| control leg | PASS | revert → `Application startup complete`, 10 memoir tables |

**FINDING — W8 predicate (3b) is unreachable as written.** `SQLModel.metadata.create_all` commits the v2 tables in its **own transaction before** the ensure block, so "none of the six v2 tables exist after an ensure abort" can never hold. The gate proved the stronger equivalent — *the aborting boot added nothing vs the pre-boot inventory* — and discloses the substitution. Phase-7 §3.2 should be re-specified.

**FINDING — `ADD COLUMN IF NOT EXISTS` skips without type validation when the column exists.** On a genuinely fresh DB the injected malformed DDL is a no-op NOTICE, so the induction *requires* a pre-v2-shaped DB (`create-leg boot → DROP COLUMN → inject`). Recipe recorded in the KB so the next gate does not re-derive it.

---

## 4. Obligation 3 — lane packs + mutation-verified pins

### 4a. All 26 v2 packs green, every pin matched

| Pack | Nodes | Pin | Pack | Nodes | Pin |
|---|---|---|---|---|---|
| schema_unit | 28 | 28 | poignancy_service | 56 | 56 |
| constellation_repo | 23 | 23 | poignancy_worker | 26 | 26 |
| syntheses_repo | 23 | 23 | poignancy_lifecycle | 10 | 10 |
| star_description | 4 | 4 | poignancy_retrieval | 10 | 10 |
| write_purity_unit | 15 | 15 | jev_classifier | 26 | 26 |
| reflection_service | 43 | 43 (re-pin 32→43) | persona_composition | **13** | **13** (was 11 — see NEW RED #2) |
| reflection_worker | 17 | 17 | persona_proposal | 5 | 5 |
| dual_recall | 16 | 16 (re-pin 15→16) | persona_router | 15 | 15 |
| numeric_knob | 42 | 42 (re-pin 36→42) | keepsake_proposals | 43 | 43 |
| constellation_service | 17 | 17 | proposal_notification | 16 | 16 |
| about_tool | 29 | 29 | wake_refresh | 40 | 40 |
| tool_registration | 26 | 26 | schema_pg / poignancy_pg / reflection_pg | 15/15/17 | 15/15/17 |

Phase-3 orphan-class residual **closed by evidence**: all six load-bearing classes (`TestThePerPassCallBudget`, `TestTheHeatHonesty`, `TestThePerPurposeBudget`, `TestTheDurablePassIdentity`, `TestTheBudgetRefusal`, `TestTheSeedSurvivesAParseFailure`) proven **collected in the selector**.

**NEW RED #2 (found here, fixed):** `memoir_v2_persona_composition_test` selected 4 classes / pinned 11, but tidier batch B2 (`ae07a48b7`) added `TestCompositionBudgetChargedAtCallSite` + `TestParkLoopIsolation` **without registering them or moving the pin** → `11/13 collected (2 deselected)`: two legs invisible to the pack, violating the lane's own §14 rule. Fix: selector + pin → **13**, commit `e92abdcd3`. Both newly-selected legs pass.

### 4b. Nine safety pins, all mutation-proven (scratch clones, byte-identical restores)

| Pin | Mutation | RED evidence | Verdict |
|---|---|---|---|
| lane-OFF byte-identity | `_match_syntheses` returns `[]` instead of `None` | **4 nodes** (`assert 'syntheses=' not in …`) | DETECTOR (first executed receipt — ledger had none) |
| display-freeze | rank by raw `importance` / project the score | **1 + 4 nodes** (`assert 'importance=2' in '…importance=9…'`, `assert 9 == 2`, byte-identity leg) | DETECTOR (first executed receipt) |
| write-purity allowed-caller | score UPDATE writes `importance` / swap insert-list order | **1 + 2 nodes** (`RECORD_COLUMNS` freeze; byte-exact 22-column list) | DETECTOR (first executed receipt) |
| router 403 **both** endpoints + census | approve handler, reject handler, and a fictional 4th route | approve `assert 404 == 403` · reject `assert 404 == 403` · census `assert 4 == 3` | DETECTOR (all three) |
| per-pass call budgets | re-mint budget per chunk; heat gate `if True` | `assert 3 <= 2` · `assert 1.0 == 0.0` (+bonus deleted-episode leg) | DETECTOR |
| once-ever skip — plan recipe (d) | disable read-side pre-check | **0 RED** (43 passed) | **NON-DISCRIMINATING — confirmed; must NOT be cited as the receipt** |
| once-ever skip — (d)′ | drop query-side `notin_`, keep read-side skip | **exactly 7 RED** | DETECTOR |
| [PROPOSALS] preserved-bytes | swap trim-rung order | `assert 5 == (5 - 1)` | DETECTOR |
| notify CAS | drop `notification_status == pending` from the CAS WHERE | "delivered 2 times" / `assert 2 == 1` | DETECTOR |
| bridge sanitization | `_proposal_one_line` → identity | **2 nodes** (newline + forged `[SYSTEM CONTEXT: Memoir]` header survive) | DETECTOR |

---

## 5. Obligation 4 — traceability matrix

| Phase | ACs | PINNED | MUTATION-PROVEN | **NO DETECTOR** |
|---|---|---|---|---|
| 1 schema substrate | 6 | 6 | 0 | 0 |
| 2 the dream | 7 | 6 | 0 | **1 (AC6)** |
| 3 constellation / `memoir_about` | 7 | 7 | 4 | 0 |
| 4 poignancy worker | 7 | 7 | 0 | 0 |
| 5 persona gate (JEV) | 6 | 6 | 5 | 0 |
| 6 keepsake / wake refresh | 6 | 6 | 4 | 0 |
| 7 verification & release | 6 | **1** | 0 | **5 (AC1, AC3, AC4, AC5, AC6)** |
| **TOTAL** | **45** | **39** | **13** | **6** |

**ACs with NO detector:** P2 AC6 (foreign-belief convention is docstring-only) · **T7.1** (no full-unit pack) · **T7.3** (no v2 ensure blast-radius detector) · **T7.4** (no v2 mutation driver — *highest-value gap*: all v2 detector-ness rested on hand-run prose receipts until this gate executed 9 ad-hoc) · **T7.5** (no 3-worker boot census in tree) · **T7.6** (no stage/tag/manifest detector, plan-acknowledged). Plus task **T3.5** — `agents/ari/tools_note.md` re-stamp verified **NOT applied** (`grep -c memoir_about` = 0; versioned-prompt-artifact lane, human-session-only).

**Missing phase-7 artifacts** (names exist only inside `v2-phase7-plan.md`): `tests/postgres/test_memoir_v2_pg.py`, `tests/postgres/test_memoir_v2_ensure_blast_radius.py`, `memoir_v2_full_unit_pack.sh`, `memoir_v2_real_pg_pack.sh`, `memoir_v2_ensure_induction_pack.sh`, `memoir_v2_mutation_matrix_test.sh`, `memoir_v2_boot_smoke{,_pack}.sh`. The shipped "equivalents" are **v1** and do not satisfy the v2 ACs: v1 W8 induces `no_such_imm_function` on v1 `tsv`; `memoir_mutation_matrix_test.py` is the v1 seven legs; `memoir_boot_smoke_test.sh` boots a **frozen binary with a 1-worker census** — the opposite of AC5.

**Predicate mismatches:** PG cutover literal (plan `'2026-11-09 00:00:00'` vs shipped `+00`, §17.5.3 deliberate; the PG pin is now a regex so the plan literal is not enforced on PG) · P3 AC2 "capped at 2" vs operative **per-purpose cap of 1** (pool 2, Tier-2 ceiling 1) · P2 AC1 "sum 26" vs fixture 27 (leader briefing error, code right — KV `memoir-v2.loop-rules` §5) · P6 AC2 label `[PROPOSALS]` vs shipped header `### Proposals for Keepsake Review`.

**Assertions weaker than the AC wording (9):** "byte-identical" excludes `last_accessed_at` · "zero hardcoded ordinals" greps only `categories.index("memoir")` (and `test_memoir_registration.py:148` still hardcodes `== 20`) · "resolves when Ari spawns" = factory construction only · recall never exercised under the parked worker · P6 AC4 payload = substring checks, no token bound · P1 AC2's `test_the_record_columns_family_is_untouched` declares its own local set (near-tautological; the real bite is `test_memoir_write_purity_unit.py:331`) · `assert caller.constellation_calls <= 2` (inequality) · "exactly 5 legacy" asserts the remainder · `create_draft` caller pin is **file**-set equality (a new caller inside an allowed file is invisible).

---

## 6. Obligation 6 — attribution + Tier-2 (ensure.md Release-Gate)

Method: 5 deterministic shards (`find … | sort` → 1,684 files; `awk 'NR%5==N'`), repo addopts preserved, `tests/e2e/**` and `tests/packs/g7_unique_index_smoke_test.py` excluded as pre-registered hazards. **Every shard exceeded the 300 s cap** (337 files ≈ 6.5k nodes ≈ 26 min) and was bisected by the worker with the cap never raised.

### Adjudication totals (all shards, base = `f49006b92`, per-node A/B with interpreter-identity proofs)

| Bucket | Count |
|---|---|
| PRE-EXISTING (red at base) | ≈ 560 unique nodes |
| **NET-NEW (green at base, red at HEAD)** | **3** (all stale pins; all fixed — §9) |
| IMPROVED (red at base, green at HEAD) | **3** (`test_lcan_legacy_checkpoint` drift pin s1–s3) |
| Order/load-sensitive on BOTH legs | 13 (`test_initiative_message` 6, `test_instance_search_api` 7) |
| Genuinely flaky | 1 (`test_tier1_sibling_owned_row_skipped`: 3/5 red at base, 1/3 at HEAD) |

**Attested pre-existing buckets confirmed:** `test_configure_builtin_idempotency.py` 15F/2P (shard 2) · `test_memoir_inner_soul_redirect_test*` — **no such pytest node exists**; the real file `tests/unit/tools/test_inner_soul_redirect.py` ran **GREEN** (the pack of that name exists; the foreign-dirt red described in the KV did not surface in the Tier-2 corpus) · `.agents/approver/**` WIP never implicated.

**Three re-pinned pack pins verified:** `dual_recall 16`, `numeric_knob 42`, `reflection_service 43` — all MATCH.

### ⚠ Maintenance findings (mine, not the lane's)
1. **Sharding was sized wrong.** `awk 'NR%5'` over *files* yields 337 files ≈ 6.5k nodes ≈ 26 min ≈ **5× the cap**. Node-count sharding (~150–200 nodes ⇒ ~35 shards) is the correct shape for this repo; `tests/test_memory_integration.py` and the job_queue cluster are expensive per file.
2. **A capped 300 s `-q` run is silent-green.** It writes ~844 bytes and **zero `FAILED` lines** (tracebacks buffered to session end). A capped log must never be read as "no reds".
3. **Base-leg `.venv` asymmetry is a false-IMPROVED trap.** `git clone --shared` has no `.venv`, and `test_sync_runner.py` shells out to `.venv/bin/python` → base-only ERRORs that look like improvements. Symlinking the worktree `.venv` into the base clone reclassified 2 nodes to PRE-EXISTING.
4. **The route-census invariant is asserted in FOUR places** (§9) — a mount change must update all four; the lane updated two.
5. **Canon defect:** the tester skill `whole-tree-base-proven-diff.md:46-61` omits the `ENSEMBLE_AUTH_*` family and `PACKS.md:511` renders the fence as a glob that is a **silent no-op** → shard 1 manufactured 111 phantom reds (§8).

---

## 7. Obligation 7 — live-LLM

**`FEASIBLE BUT NOT GATE-SUITABLE`.** Production path drivable without booting the daemon (`load_config()` → `_reflection_llm_config` / `_poignancy_llm_config` over `_memoir_lane_llm_config`, `api.py:482/514/537`); `base_url=https://llm.ensem.dev/v1`, **`base_url_backup=https://llm.daoduc.org/v1` (failover configured; `_RawFailoverShim.is_configured == True`)**, `model=agentic`.

`max_retries=0` CONFIRMED in **all three** lanes (`memoir_reflection_service.py:1364`, `memoir_poignancy_service.py:696`, `memoir_persona_gate.py:1109`) — 1 HTTP attempt per success observed.

| # | Call | Result | Latency | finish | usage p/c |
|---|---|---|---|---|---|
| 1 | poignancy | `'9'` → **9** | 5,520 ms | stop | 141/303 |
| 2 | reflection synthesis | envelope parsed, 2 candidates, required keys | 8,452 ms | stop | 266/689 |
| 3 | poignancy re-run, identical prompt | `'8'` → **8** | 8,347 ms | stop | 141/352 |

**Determinism: 9 → 8 on a byte-identical prompt at coded `temperature=0.0`** (303/352 completion tokens for a one-character answer = reasoning tokens). ⇒ an exact-value live pin would flake; a future live leg may assert **shape only**.

**Residual risk:** failover swap unproven (never forced) · real 401/403 classification never seen live · real token ceilings vs the 45 s cap unproven at scale · `finish_reason` variety mock-only.

---

## 8. Harness defect found and root-caused (mine)

Shard 1 (and the sibling census) inherited 7 ambient `ENSEMBLE_AUTH_*` vars because my dispatch named only `POSTGRES_*`/`PG*`. Control vs scoped re-run:

| Leg | Reds |
|---|---|
| CONTROL (PG scrub only) | **112** (84F + 28E) |
| HEAD, whole-family scrub | **1** |
| BASE, whole-family scrub | **1** (identical) |

**111 of 112 vanished on the fence alone; 0 NET-NEW.** Survivor: `test_vscode_security_integration.py::TestC1PathTraversal::test_c1_valid_repo_folder_not_blocked` (`DID NOT RAISE httpx.ConnectError`) — red identically at base.

Root cause is in **our own canon**, not a worker: `whole-tree-base-proven-diff.md:46-61` omits the auth family; `PACKS.md:511` prints `env -u POSTGRES_*/PG*/ENSEMBLE_*` — a glob `env` does not expand (proven: leaves all 7 vars) *and* falsely signals auth coverage. **Fixed in this gate's registration commit.**

---

## 9. NET-NEW regressions — 3 found, 3 fixed

| # | Node | Root cause | Fix | Proof |
|---|---|---|---|---|
| 1 | `tests/postgres/test_memoir_v2_schema_pg.py:362` cutover-literal pin | pin demanded the exact no-offset literal; §17.5.3 makes `+00` deliberate | `0719ed5e6` — assert space-separated, no `T`, optional `+00` | 15/15 green |
| 2 | `memoir_v2_persona_composition_test` selector/pin | tidier B2 added 2 classes without registering/re-pinning (lane rule §14 violation) | `e92abdcd3` — selector + pin 11→13 | 13/13 green; both new legs pass |
| 3 | `tests/unit/test_post_restart_arm_notify_no_parallel.py` (`PRE_FEATURE_API_ROUTER_LIST`) | phase-5 mount grew `include_router` 38→39; the SIBLING census was re-pinned, this second copy was not | `883ce7535` — add `memoir_router` at true index 34 with the (a)/(b)/(c) rationale | 8/8 + sibling 7/7; red-at-HEAD proof executed (restore → `1 failed`, index-34 mismatch; base 38/38 green) |
| 4 | `tests/unit/repositories/test_source_gc_repository.py::TestP11BootNoBehaviorChange::test_post_merge_span_exactly_sanctioned_hunk` | memoir's +82 lines to `daemon/manager.py` fall outside the decision-lane **marker-keyed** sanction (memoir hunks contain `decision` 0×; `memoir` is per-hunk-complete 11/6) | `a38e5e33b` — memoir-keyed sanction mirroring precedent `c81dd0dea` (**materialized**: that commit lives only on `origin/latest`; this branch forked before it) | committed plant → RED `offending hunks=[['+# unrelated drift']]`; revert → 4/4 green; blanket check on other paths intact. **The pin reads COMMITTED state — a working-tree plant is invisible**, so the bite-proof required a committed mutation |
| 5 | `tests/unit/metrics/test_metrics_query_scope_cage.py::TestThreeWayCage` (4 nodes) | **third** copy of the census triple left at 113/38/34 while the lane updated the siblings to 116/39/35 | `a8760fcc7` — 3-literal re-pin (+ docstring/comment) | four-copy consistency (7/7/5/8 passed); **each literal perturbed separately** reddens its own assert + the composite form; restored byte-exactly |

Counts read true: **3 net-new found by the shards** (rows 1, 3-family [rows 3+5 are the same mount's stale pins in two files], 4) — all stale pins, none a product regression; **5 test-side fixes total** by the gate.

---

## 10. Quarantine (tester-owned)

`concurrency_atomic_unit_test` is **5F/94P/74S** — the 5 are `tests/test_instance_metadata_atomic.py` metadata/title setters (`DetachedInstanceError`), **proven red at base** via a `git archive` A/B leg. Per `ensure.md:7` they are quarantined (deterministic, not flaky) with an explicit "the pack is NOT marked PASS" clause and an un-quarantine condition. Separately, `test_tier1_sibling_owned_row_skipped` is quarantined as **genuinely flaky**.

---

## 11. VERDICT

**GO for staging**, conditional on nothing further: every executed obligation is green; all 26 packs pass at their pins; all 9 safety pins are mutation-proven detectors; the real boot, the 3-worker census, the W8 blast-radius proof and the phase-5/6 PG legs all pass on isolated scratch infrastructure; `ensure.md` Core passes with one documented pre-existing quarantine; the Release-Gate E2E cascade passes on isolated scratch PG.

**Net-new regressions at gate open: 3. Open at gate close: 0.**

**Must travel with the stage (disclosed, not blocking):**
1. **6 ACs have no detector** — T7.1, T7.3, T7.4 (**highest value: no v2 mutation driver**), T7.5, T7.6 + P2 AC6. The five phase-7 packs and `test_memoir_v2_ensure_blast_radius.py` **do not exist**; the shipped equivalents are v1 and one of them boots a frozen binary with a 1-worker census.
2. **Two spec defects to fix in the plan:** W8 predicate (3b) unreachable as written (create_all commits first) · the day-window predicate is session-TZ conditional and safe only because the engine pins `timezone=UTC`.
3. **T3.5 `agents/ari/tools_note.md` re-stamp not applied** (versioned-prompt-artifact lane; human session required).
4. **No live-LLM leg**; `FEASIBLE BUT NOT GATE-SUITABLE` with the 5 named residual risks.
5. **`ensure.md`/skill canon defects fixed here:** the auth-family fence omission + the glob no-op.
6. **E2E coverage is one scenario of four** (cascade green; pause→resume, terminate→revive, auth-ENABLED paths not run).
