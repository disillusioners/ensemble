# Phase 7 — Verification, Safety Gates, Docs & Staged Release

**File:** `v2-phase7-plan.md`  
**Depends on:** Phases 1–6 (`v2-phase1-plan.md` through `v2-phase6-plan.md`).  
**Flags / Decisions consumed:** All v2 decisions, W8 blast radius proof, R10/R11 hazards, V2-12, V2-13 (`v4-federated-sketch.md`), §3-A7 mutation matrix, SF8 interpreter pin, Suggestion #7, Suggestion #9.

---

## 1. Objective

Deliver end-to-end integration verification, safety gate enforcement, documentation parity, and release packaging:
1. Execute the comprehensive cross-driver test battery: fresh SQLite boot verifying clean migration discovery and execution + real PostgreSQL boot validating `create_all`, `_ensure_postgres_columns` idempotence, and index utilization. Update ensure census pins: `test_memoir_retrieval_pg.py` stays 2; `test_memoir_pg_corpus.py` flips 2→4 (Register #8).
2. Execute the **W8 Ensure Blast Radius Test Induction** proving inductively on a scratch database that a memoir ensure DDL failure cleanly aborts boot and rolls back the shared transaction without partial corruption.
3. Execute the **7 Mutation-Verified Safety Pins (§3-A7)** across kill-switches, budget clamps, 19-code freeze, notification/proposal-ledger once-everness dedup, supersede chains, legacy seed cutover with 'T'-literal mutation leg, and record-column purity.
4. Execute end-to-end boot smoke with the **3-Worker Boot Census (§3-A7)** (Optional #5): verifying that `MemoirEmbeddingWorker`, `MemoirReflectionWorker`, and `MemoirPoignancyWorker` spawn on their own `app.state` tasks with dedicated executors, respond independently to kill-switches within 1 tick, and start cleanly with unset knobs (unset⇒ON visibility).
5. **Boot Smoke Isolation & Bootstrap (SF8 / Suggestion #7):** Boot smoke must execute using the WORKTREE VENV INTERPRETER (`/home/nea/ensemble-src-wt-memoir-v2/.venv/bin/python -m daemon`), preceded by a `uv sync` bootstrap step to materialize the worktree environment. NEVER invoke a frozen binary executable in `releases/`. (The binary's path derivation resolves the LIVE install root; its 90s `gc_pending_actions` sweep could prune a live ceremony's nonce). Include a ceremony-coordination caveat line.
6. Enforce strict tester-lane hazard fences:
   - Scrub `POSTGRES_*` environment variables per-variable (never `env -u POSTGRES_*` globs, Critical Note occ#8).
   - Explicitly pin `ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0` in all boot smoke scripts.
7. Verify that sibling deliverable `v4-federated-sketch.md` is present on disk and committed together with this planning suite.
8. Verify documentation and metrics: regenerate `.agents/shared/metrics/tool-categories.json`, confirm `agents/ari/tools_note.md` re-stamping, and assert that tool category ordinals are dynamically grep-derived.
9. Package the staged release via `scripts/upgrade/stage.sh` into `releases/v0.29.X/` (coordinated as `v0.29.8+` via Shared Meta KV census), verify the 4-way SHA-256 manifest match, cut the git tag, and hand off to the Emperor (**NO live promote**).

---

## 2. Decisions

| ID | Decision | Rationale (verified anchors) | Rejected Alternatives |
|----|----------|------------------------------|-----------------------|
| **V2-D 7.1** | W8 Ensure Blast Radius Test Induction: Test failure blast radius inductively on a scratch PG instance with a deliberately invalid ensure statement (`ALTER TABLE memoir_episodes ADD COLUMN IF NOT EXISTS poignancy_status no_such_type;`). | Proves the single-transaction rollback property without relying on prose claims. Confirms that failed DDL names the offending statement and halts boot cleanly. | Claiming containment in documentation without inductive test proof. |
| **V2-D 7.2** | Strict tester-lane hazard fences: (1) Scrub `POSTGRES_*` per-variable (`env -u POSTGRES_DB -u POSTGRES_USER ...`); (2) Explicitly export `ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0`. | Standing 🔴 critical notes: `env -u POSTGRES_*` is a silent no-op in bash; frozen-binary boot sweep kills live systemd processes if not explicitly disabled. | Using wildcard unsets; OR omitting subprocess sweep guards. |
| **V2-D 7.3** | Category ordinals grep-derived at ceremony time: Zero test files or release notes hardcode the memoir tool category ordinal. | The memoir category ordinal drifts as sibling lanes merge (was #18 in prose, #19 in registry, #20 after prompt-reload). Dynamic grep derivation eliminates drift defects. | Hardcoding category ordinals. |
| **V2-D 7.4** | Version slot coordinated via Shared Meta KV census: Coordinate slot dynamically at stage time; parallel views-fix lane claims `v0.29.7`, so Memoir v2 expects `v0.29.8+`. | Prevents version collision when staging concurrently with parallel active feature lanes. | Hardcoding a fixed version tag before staging. |
| **V2-D 7.5** | Staging only, NO live promote: Stage release via `scripts/upgrade/stage.sh` and cut annotated git tag. Promotion belongs exclusively to the Emperor's 3-factor ceremony. | Constitutional upgrade protocol: Ari and implementing workers cannot promote live software autonomously. Promotion requires user attestation. | Running `promote.sh live` autonomously. |
| **V2-D 7.6** | Sibling deliverable `v4-federated-sketch.md` committed together: Sibling deliverable is present on disk and verified before staging. | R3 rolling doctrine discharge: v4 design pressure is recorded and committed as part of the v2 milestone. | Committing v4 sketch in a separate detached PR. |
| **V2-D 7.7** | Ensure census pin update asymmetry (§3-A5 / Register #8): `test_memoir_retrieval_pg.py:98-104` stays at 2 (narrow filter on `tsv`/`GIN`); `test_memoir_pg_corpus.py:115-123` flips 2→4 (broad filter on `memoir_episodes` matches both new ensure entries). | Test filters are asymmetric; updating both to 3 or 4 blindly is a defect. | Arbitrarily editing both pins to 3. |
| **V2-D 7.8** | Boot-smoke interpreter pin to worktree venv & uv sync bootstrap (SF8 / Suggestion #7): Execute boot smoke strictly via `/home/nea/ensemble-src-wt-memoir-v2/.venv/bin/python -m daemon`, with a prior `uv sync` bootstrap, NEVER a frozen binary. | A frozen binary resolves `<INSTALL_DIR>/releases`, binding the live promote journal where its 90s `gc_pending_actions` sweep can prune a live ceremony nonce. | Executing against frozen binary in releases directory. |

---

## 3. Domain Detail & Verification Checklist

### 3.1 Cross-Driver Test Matrix
- **SQLite Engine:**
  - Throwaway in-memory and file-based SQLite tests.
  - Verifies that `20261109_..._create_memoir_v2_tables.sql` parses and executes cleanly with plain `ADD COLUMN` statements.
  - Verifies that all string columns are `TEXT` and dynamic JSON typing works without errors.
- **PostgreSQL Engine (House PG 16.15):**
  - Verifies that `create_all` generates `memoir_star`, `memoir_star_alias`, `memoir_line`, `memoir_reflection_state`, `memoir_proposal_ledger`, `memoir_jev_ledger`.
  - Verifies that `_ensure_postgres_columns` adds the four columns (with `TIMESTAMPTZ` typing for `reflected_at`) and executes the legacy seed idempotently.
  - Verifies ensure census pins: `test_memoir_retrieval_pg.py` == 2, `test_memoir_pg_corpus.py` == 4.
  - Verifies GIN and B-Tree index utilization with `EXPLAIN`.
  - Verifies CHECK constraints reject out-of-bounds `star_type` and negative `heat`.

### 3.2 Inductive Ensure Blast Radius Proof (W8 Pattern)
1. Launch scratch PG instance.
2. In a scratch copy of `manager.py`, inject a malformed DDL statement into the memoir ensure block.
3. Boot the daemon.
4. **Assert:**
   - Daemon boot halts with non-zero exit code.
   - Logs clearly name the failing memoir statement.
   - Database inspection proves ZERO partial columns or tables committed from that transaction.
   - Prior inherited tables remain untouched and consistent.

### 3.3 The 7 Mutation-Verified Safety Pins (§3-A7 / Suggestion #9)
The mutation driver (`test/packs/memoir_mutation_matrix_test.py` + `mutation-matrix-digest.json`) asserts:
1. **Kill-switch truth tables ×3 bools:** Parametrised matrix over reflection, poignancy, wake-refresh; deleting the garbage arm flips RED.
2. **Budget clamps:** Mock LLM returning 6 syntheses results in exactly 5 stored rows; raising cap to 6 in source flips RED. Same for 1000-char reject and call-cap dispatch increment.
3. **19-code freeze:** Appending a 20th code to `MEMOIR_CODES` flips the import assertion RED.
4. **Notification / proposal-ledger once-everness dedup (W2 / Suggestion #9):** Removing the date comparison in notification ledger emits 2 notifications -> RED. Removing the proposal-ledger once-ever-per-episode UNIQUE constraint (`uq_memoir_proposal_agent_episode`) allows re-proposing an episode -> RED. Removing refresh's independence from `memoir_briefing_injected` -> RED.
5. **Append-only / supersede chains:** Emitting `REFINE` without `supersedes` back-pointer -> RED; emitting synthesis with empty `source_episode_ids` -> RED.
6. **Backfill seed cutover & 'T'-literal mutation leg (SF11 / Suggestion #9):** Widening legacy seed predicate to bare-NULL marks post-cutover rows legacy -> RED. Swapping the space-separated cutover literal `'2026-11-09 00:00:00'` to the ISO-8601 `'T'` literal `'2026-11-09T00:00:00Z'` in SQLite seed causes the cutover fixture row to be incorrectly marked legacy (the space < T lexicographic trap) -> RED.
7. **Record-column purity vs poignancy:** Making `record_poignancy_score` write to `importance` trips `RECORD_COLUMNS` purity pin -> RED (`RECORD_COLUMNS` remains write-frozen); `MEMOIR_INSERT_COLUMNS` pin asserts the updated byte-exact insert list -> amending statement to match passes GREEN.

### 3.4 3-Worker Boot Census & Interpreter Isolation (SF8 / Suggestion #7)
Boot smoke starts the daemon under `ENSEMBLE_SELF_ENV=sandbox` using `/home/nea/ensemble-src-wt-memoir-v2/.venv/bin/python -m daemon`:
- **Bootstrap Step (Suggestion #7):** Execute `uv sync` in `/home/nea/ensemble-src-wt-memoir-v2` prior to boot smoke to ensure worktree venv dependencies are fully materialized.
- **POK Auth Scrub (ND-11):** Boot smoke environment explicitly scrubs `ENSEMBLE_AUTH_ISSUER`, `ENSEMBLE_AUTH_DISCOVERY_URL`, and `ENSEMBLE_AUTH_EXTERNAL_REDIRECT_BASE` per project POK (`auth-lane-scrub-ensemble-auth-env`).
- **Isolation Pin:** Invocation uses worktree venv python directly. NEVER invoke frozen binary from `releases/`.
- **Ceremony-coordination caveat:** Live promote ceremonies running concurrently are protected from nonce eviction by bypassing frozen binary path resolution.
- Worker 1: `MemoirEmbeddingWorker` running on `app.state.memoir_embedding_worker_task`, thread prefix `memoir-embed`.
- Worker 2: `MemoirReflectionWorker` running on `app.state.memoir_reflection_worker_task`, thread prefix `memoir-reflect`.
- Worker 3: `MemoirPoignancyWorker` running on `app.state.memoir_poignancy_worker_task`, thread prefix `memoir-poignancy`.
- Unset⇒ON visibility: With all three knobs unset, all three workers spawn and log start lines.
- Toggling any individual kill-switch to `"false"` parks the target worker cleanly while leaving the other two active.

---

## 4. Tasks

| ID | Task | Outcome (testable) | Touchpoints | Depends on | Risks carried | AC |
|----|------|--------------------|-------------|------------|---------------|----|
| **T7.1** | Execute complete unit test suite across constellation, syntheses, poignancy, JEV, keepsake proposals, and wake refresh. | 100% test pass rate across all new v2 modules; zero regressions in v1 tests. | `tests/unit/test_memoir_*.py` | P1–P6 | — | AC1 |
| **T7.2** | Execute cross-driver integration tests on fresh SQLite and real PostgreSQL; verify ensure census pins (retrieval_pg=2, pg_corpus=4). | Schema, models, and queries verified identical across both database engines; census pins pass. | `tests/postgres/test_memoir_v2_pg.py` | T7.1 | R1, R10 | AC2 |
| **T7.3** | Execute W8 ensure blast radius test induction on a scratch PostgreSQL instance. | Proves single-transaction rollback and diagnostic output on simulated ensure DDL failure. | `tests/postgres/test_memoir_v2_ensure_blast_radius.py` | T7.2 | R1 | AC3 |
| **T7.4** | Execute the 7 mutation-verified safety pins via `test/packs/memoir_mutation_matrix_test.py` (incl. proposal-ledger once-everness and 'T'-literal legs). | All 7 mutation legs flip RED on intentional defect, restore cleanly to GREEN; digest generated. | `test/packs/memoir_mutation_matrix_test.py` | T7.3 | — | AC4 |
| **T7.5** | Execute end-to-end boot smoke with 3-worker census under `ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0` using worktree venv interpreter (`.venv/bin/python -m daemon`) with prior `uv sync` bootstrap (SF8 / Suggestion #7). | All 3 background workers spawn; dedicated thread prefixes verified; live ceremony nonce protected from path-binding gc ticks; kill-switches park/resume independently. | `test/packs/memoir_v2_boot_smoke.sh` | T7.4 | R11 | AC5 |
| **T7.6** | Update metrics via `checkpoint_metrics.py`, verify `agents/ari/tools_note.md` re-stamp, verify `v4-federated-sketch.md` on disk, and execute `stage.sh` for v0.29.8+. | `releases/v0.29.X/` created; manifest valid; tag cut and pushed; zero live promotion performed. | `scripts/upgrade/stage.sh`, `agents/ari/tools_note.md` | T7.5 | R12 | AC6 |

---

## 5. Acceptance Criteria

| ID | Criterion |
|---|---|
| **AC1** | Cross-suite unit pass: All v1 and v2 unit tests green; refusal vocabulary remains exactly 19 codes; `MEMOIR_TOOL_NAMES` holds exactly 6 tools. |
| **AC2** | Cross-driver parity & census pins: SQLite migration executes cleanly with plain `ADD COLUMN`; PostgreSQL boot materializes all tables, columns, and indexes; ensure census pin `test_memoir_retrieval_pg.py` == 2, `test_memoir_pg_corpus.py` == 4. |
| **AC3** | W8 Ensure Blast Radius: Malformed ensure statement in scratch PG boot fails loud, names the invalid statement, rolls back the entire transaction, and leaves zero partial schema artifacts. |
| **AC4** | The 7 Mutation Pins (§3-A7 / Suggestion #9): All 7 mutation legs proven (kill-switch truth tables, budget clamps, 19-code freeze, notification/proposal-ledger once-everness dedup, supersede chains, legacy seed cutover with 'T'-literal mutation leg, and record-column purity vs poignancy). |
| **AC5** | Boot smoke 3-worker census & venv isolation (SF8 / Suggestion #7): Daemon boots in sandbox using worktree venv python (never frozen binary) after `uv sync` bootstrap; all 3 worker threads log their respective tick lines on own executors (`memoir-embed`, `memoir-reflect`, `memoir-poignancy`); live ceremony nonces preserved; toggling `ENSEMBLE_MEMOIR_REFLECTION_ENABLED="false"` parks reflection worker while others continue. |
| **AC6** | Staged release integrity: `scripts/upgrade/stage.sh` succeeds; release directory matches manifest SHA-256; annotated git tag `v0.29.X` created; `v4-federated-sketch.md` included in release commit; `current` symlink remains untouched on live system. |

---

## 6. Test Packs

- `test/packs/memoir_v2_full_unit_pack.sh`: Complete unit test runner across all v2 modules.
- `test/packs/memoir_v2_real_pg_pack.sh`: Real PostgreSQL integration test suite with asymmetric census pin assertions.
- `test/packs/memoir_v2_ensure_induction_pack.sh`: W8 ensure single-transaction blast radius test induction.
- `test/packs/memoir_v2_mutation_matrix_test.sh`: 7-leg mutation verification runner (incl. proposal once-everness and 'T'-literal legs).
- `test/packs/memoir_v2_boot_smoke_pack.sh`: End-to-end daemon boot smoke with 3-worker census, venv isolation, and kill-switch verification.

---

## 7. Flag Consumption

- All v2 configuration flags and kill-switches.
- **V2-D 7.1:** W8 Ensure single-transaction blast radius induction.
- **V2-D 7.2:** Tester-lane hazard mitigation (per-variable PG scrub + subprocess sweep pin).
- **V2-D 7.3:** Grep-derived category ordinals at ceremony time.
- **V2-D 7.4:** Dynamic version slot coordination.
- **V2-D 7.6:** Sibling deliverable `v4-federated-sketch.md` inclusion.
- **V2-D 7.7 / §3-A5:** Asymmetric ensure census pin reconciliation (retrieval_pg=2, pg_corpus=4).
- **V2-D 7.8 / SF8:** Worktree venv interpreter pin and `uv sync` bootstrap for boot-smoke isolation.
- **Suggestion #9:** Proposal-ledger once-everness and 'T'-literal mutation pin vocabulary.
