# Phase 7 — Verification, Safety Gates, Docs & Staged Release

**File:** `v2-phase7-plan.md`  
**Depends on:** Phases 1–6 (`v2-phase1-plan.md` through `v2-phase6-plan.md`).  
**Flags / Decisions consumed:** All v2 decisions, W8 blast radius proof, R10/R11 hazards, V2-12, V2-13 (`v4-federated-sketch.md`).

---

## 1. Objective

Deliver end-to-end integration verification, safety gate enforcement, documentation parity, and release packaging:
1. Execute the comprehensive cross-driver test battery: fresh SQLite boot verifying clean migration discovery and execution + real PostgreSQL boot validating `create_all`, `_ensure_postgres_columns` idempotence, and index utilization.
2. Execute the **W8 Ensure Blast Radius Test Induction** proving inductively on a scratch database that a memoir ensure DDL failure cleanly aborts boot and rolls back the shared transaction without partial corruption.
3. Execute end-to-end boot smoke with the full worker census: verifying that all three workers (`MemoirEmbeddingWorker`, `MemoirReflectionWorker`, `MemoirPoignancyWorker`) spawn correctly and respond to their individual kill-switches.
4. Enforce strict tester-lane hazard fences:
   - Scrub `POSTGRES_*` environment variables per-variable (never `env -u POSTGRES_*` globs, Critical Note occ#8).
   - Explicitly pin `ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0` in all boot smoke scripts.
5. Verify that sibling deliverable `v4-federated-sketch.md` is present on disk and committed together with this planning suite.
6. Verify documentation and metrics: regenerate `.agents/shared/metrics/tool-categories.json`, confirm `agents/ari/tools_note.md` re-stamping, and assert that tool category ordinals are dynamically grep-derived.
7. Package the staged release via `scripts/upgrade/stage.sh` into `releases/v0.29.X/` (coordinated as `v0.29.8+` via Shared Meta KV census), verify the 4-way SHA-256 manifest match, cut the git tag, and hand off to the Emperor (**NO live promote**).

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

---

## 3. Domain Detail & Verification Checklist

### 3.1 Cross-Driver Test Matrix
- **SQLite Engine:**
  - Throwaway in-memory and file-based SQLite tests.
  - Verifies that `20261109_..._create_memoir_constellation_tables.sql` parses and executes cleanly.
  - Verifies that all string columns are `TEXT` and dynamic JSON typing works without errors.
- **PostgreSQL Engine (House PG 16.15):**
  - Verifies that `create_all` generates `memoir_star`, `memoir_star_alias`, `memoir_line`.
  - Verifies that `_ensure_postgres_columns` adds `poignancy_status` to `memoir_episodes` idempotently.
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

### 3.3 Full Worker Census in Boot Smoke
Boot smoke starts the daemon under `ENSEMBLE_SELF_ENV=sandbox` and asserts:
- Worker 1: `MemoirEmbeddingWorker` running, log line `[MemoirEmbed]`.
- Worker 2: `MemoirReflectionWorker` running, log line `[MemoirReflection]`.
- Worker 3: `MemoirPoignancyWorker` running, log line `[MemoirPoignancy]`.
- Toggling any individual kill-switch to `"false"` parks the target worker cleanly while leaving the other two active.

---

## 4. Tasks

| ID | Task | Outcome (testable) | Touchpoints | Depends on | Risks carried | AC |
|----|------|--------------------|-------------|------------|---------------|----|
| **T7.1** | Execute complete unit test suite across constellation, syntheses, poignancy, JEV, keepsake proposals, and wake refresh. | 100% test pass rate across all new v2 modules; zero regressions in v1 tests. | `tests/unit/test_memoir_*.py` | P1–P6 | — | AC1 |
| **T7.2** | Execute cross-driver integration tests on fresh SQLite and real PostgreSQL. | Schema, models, and queries verified identical across both database engines. | `tests/postgres/test_memoir_v2_pg.py` | T7.1 | R1, R10 | AC2 |
| **T7.3** | Execute W8 ensure blast radius test induction on a scratch PostgreSQL instance. | Proves single-transaction rollback and diagnostic output on simulated ensure DDL failure. | `tests/postgres/test_memoir_v2_ensure_blast_radius.py` | T7.2 | R1 | AC3 |
| **T7.4** | Execute end-to-end boot smoke with 3-worker census under `ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0`. | All 3 background workers spawn; Structured tick lines observed; Kill-switches park/resume independently. | `test/packs/memoir_v2_boot_smoke.sh` | T7.3 | R11 | AC4 |
| **T7.5** | Update metrics via `checkpoint_metrics.py`, verify `agents/ari/tools_note.md` re-stamp, and verify `v4-federated-sketch.md` on disk. | `.agents/shared/metrics/tool-categories.json` updated; `tools_note.md` accurate; sibling sketch committed. | `agents/ari/tools_note.md`, `.agents/shared/metrics/tool-categories.json`, `v4-federated-sketch.md` | T7.4 | R7 | AC5 |
| **T7.6** | Execute `stage.sh` for release v0.29.8+ (coordinated via Shared Meta KV census); verify 4-way SHA-256 match; cut git tag. | `releases/v0.29.X/` created; manifest valid; tag cut and pushed; zero live promotion performed. | `scripts/upgrade/stage.sh` | T7.5 | R12 | AC6 |

---

## 5. Acceptance Criteria

| ID | Criterion |
|---|---|
| **AC1** | Cross-suite unit pass: All v1 and v2 unit tests green; refusal vocabulary remains exactly 19 codes; `MEMOIR_TOOL_NAMES` holds exactly 6 tools. |
| **AC2** | Cross-driver parity: SQLite migration executes with zero warnings; PostgreSQL boot materializes all tables, columns, and indexes; dual recall queries execute identically on both engines. |
| **AC3** | W8 Ensure Blast Radius: Malformed ensure statement in scratch PG boot fails loud, names the invalid statement, rolls back the entire transaction, and leaves zero partial schema artifacts. |
| **AC4** | Boot smoke worker census: Daemon boots in sandbox; all 3 worker threads log their respective tick lines; toggling `ENSEMBLE_MEMOIR_REFLECTION_ENABLED="false"` parks reflection worker while embedding and poignancy workers continue. |
| **AC5** | Sibling deliverable & documentation parity: `v4-federated-sketch.md` verified on disk; `tool-categories.json` matches code; `test_frozen_tool_name_discovery.py` passes; zero hardcoded category numbers exist in test files. |
| **AC6** | Staged release integrity: `scripts/upgrade/stage.sh` succeeds; release directory matches manifest SHA-256; annotated git tag `v0.29.X` created; `current` symlink remains untouched on live system. |

---

## 6. Test Packs

- `test/packs/memoir_v2_full_unit_pack.sh`: Complete unit test runner across all v2 modules.
- `test/packs/memoir_v2_real_pg_pack.sh`: Real PostgreSQL integration test suite.
- `test/packs/memoir_v2_ensure_induction_pack.sh`: W8 ensure single-transaction blast radius test induction.
- `test/packs/memoir_v2_boot_smoke_pack.sh`: End-to-end daemon boot smoke with 3-worker census and kill-switch verification.

---

## 7. Flag Consumption

- All v2 configuration flags and kill-switches.
- **V2-D 7.1:** W8 Ensure single-transaction blast radius induction.
- **V2-D 7.2:** Tester-lane hazard mitigation (per-variable PG scrub + subprocess sweep pin).
- **V2-D 7.3:** Grep-derived category ordinals at ceremony time.
- **V2-D 7.4:** Dynamic version slot coordination.
- **V2-D 7.6:** Sibling deliverable `v4-federated-sketch.md` inclusion.
