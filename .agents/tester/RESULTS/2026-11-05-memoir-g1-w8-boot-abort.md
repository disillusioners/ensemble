# G1 — W8 frozen-binary boot-abort induction (Memoir Store v1, Phase C)

**Gate:** G1 of 5 (gate worker 1). **Verdict: PASS-WITH-NOTES** — all legs hold
against the binding W8.2 observations; two disclosed caveats (below).
**Date:** 2026-11-05 (local) / 2026-11-06T01:0xZ.
**Worktree:** `/home/nea/ensemble-src-wt-memoir-store`, branch `feature/ari-memoir-store`,
HEAD `f054a08bc55cc64360ba15eb010ef7ef350cad8b` (re-verified on arrival; unchanged throughout).
**Interpreter:** worktree `.venv` 3.14.7, pytest 9.0.2; `import daemon` resolves to the
worktree (`/home/nea/ensemble-src-wt-memoir-store/daemon/__init__.py`, re-proven).
**Binding spec read in full first:** `architecture-recommendation.md` §12.4 (W8.1–W8.5) +
§12.5 R10, `decisions.md` §4 row 17, `phase1-plan.md` AC3, `phase7-plan.md` D7.3/D7.6.

---

## 1. Binaries (PyInstaller 6.19.0, `uv run python -m PyInstaller ensemble.spec`, CWD = worktree root)

| Artifact | sha256 | Notes |
|---|---|---|
| `ensemble-prod.induction` | `c089ea551b903bfcfcac683e8ba14b3fc4bd8edf98ebc34db82905bde697bdf1` | ONE plant: memoir `tsv` statement replaced with `no_such_imm_function(title)` |
| `ensemble-prod.clean` (canonical) | `8a8c227e90166bd0874a75da95b430e012925fd40a471d6ee34b3705d3b93f79` | **`dist/ensemble-prod` left in place** — built AFTER source restore; G3 may copy it |
| `ensemble-prod.tryexcept` (mutant) | `dc3f719bdbb00baef4718e3f32526c04ab51ed23fb59b5101151e0f7ec8a9921` | BOTH plants (invalid `tsv` + per-statement `try/except`) — see §5 |

Build guardrail: `uv.lock` sha256 `092941b548c0…` **unchanged** before/after every build.
`daemon/manager.py` restored byte-identical between every build
(`627ddcbb00329ea44bb4b68aa1c66c07a3338936278aa380002cec4f84a9d38d` == `git show
HEAD:daemon/manager.py`); `dist/` + `build/` are gitignored (porcelain stayed at exactly the
two new untracked artifacts before the commit).

Process disclosure: an intermediate binary (`b230d681…`) carrying the `try/except` plant
**alone** was built first and discarded — with valid DDL nothing fails, so its C3 leg showed
`tsv` PRESENT. The behavioral mutant must combine both plants; the rebuild `dc3f719b…` does.

---

## 2. Leg A — INDUCTION (frozen-binary COPY, scratch PG 16.15, scratch root)

Launch fences: binary COPY at `<scratch>/root-induction/bin/ensemble-prod` (never in place),
payload `agents/` + `plugins/` beside it, scratch `config.yaml`, fake `HOME`,
`ENSEMBLE_SELF_ENV=sandbox`, **`ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0` pinned explicitly**,
`ENSEMBLE_ORPHAN_F1_ENABLED=0`, `ENSEMBLE_MEMOIR_ENABLED=false`, dead-port embedding
(`EMBEDDING_BASE_URL=http://127.0.0.1:9/v1`), per-variable scrub of ambient `POSTGRES_*`,
`PORT`, `HOST`, `ENSEMBLE_SELF_ENV`, `ENSEMBLE_UPGRADE_LIVE`,
`ENSEMBLE_UPGRADE_SCRIPTS_DIR`, `RAG_IS_REQUIRED`, `ENSEMBLE_MISSION_RECORDS`,
`ENSEMBLE_JOB_SYSTEM_JOB_RETRY_SCHEDULER_ENABLED`,
`ENSEMBLE_AUTO_CONTINUE_RUNNING_ON_RESTART`, `ENSEMBLE_BASH_TRACKING_ID`, `EMBEDDING_*`,
`DATA_DIR` (never the glob `env -u POSTGRES_*`). Pre-flight `SELECT current_database()` ==
`memoir_w8_scratch` asserted before every write-bearing step and each boot.

| # | Assertion | Observed |
|---|---|---|
| A1 | boot FAILS, no listener | exit **rc=3**; `127.0.0.1:17350` never served `/livez` |
| A2 | the offending memoir statement is NAMED | log carries `[SQL: ALTER TABLE memoir_episodes ADD COLUMN IF NOT EXISTS tsv tsvector GENERATED ALWAYS AS (no_such_imm_function(title)) STORED]` + `Application startup failed. Exiting.`; traceback pins `daemon/manager.py:1012 __init__ → :9651 _ensure_postgres_columns` |
| A3 | no partial memoir ENSURE schema | `tsv` column count **0**, `idx_memoir_episodes_tsv` count **0** (transaction rolled back). The four memoir TABLES are present (count 4) — see caveat §6.1 |
| A4 | inherited sentinels ABSENT (collateral rollback) | `instance_execution_leases` → `to_regclass` NULL; `uq_projects_name` index count **0** |
| A5 | live fence holds | see §4 |

Sentinel choice (justified):
* **Primary — inherited `CREATE TABLE IF NOT EXISTS instance_execution_leases`** (shared list
  entry ~L8406): it is the one inherited statement whose object has **no ORM model**
  (`SQLModel.metadata.tables` does not contain it), so `create_all` never creates it and the
  statement is **pending on every fresh boot**. Asserted pending before the boot
  (`to_regclass IS NULL`), absent after the aborted boot, present after the clean boot.
* **Secondary — pre-shaped `projects` table** (the literal "pre-shape so it is PENDING"
  requirement): created from the model DDL **minus** `uq_projects_name`, so the inherited
  `CREATE UNIQUE INDEX IF NOT EXISTS uq_projects_name` fires; asserted `pg_class` count 0
  before, 0 after induction, 1 after clean.

---

## 3. Leg B — CLEAN / NON-REGRESSION (two-sided, same scratch setup)

| # | Assertion | Observed |
|---|---|---|
| B1 | clean boot succeeds | `/livez` 200 on `127.0.0.1:17350`; log `Starting Ensemble v0.28.11`, `Uvicorn running on http://127.0.0.1:17350` |
| B2 | memoir tables exist | 4/4 (`memoir_episodes`, `memoir_persona`, `memoir_persona_versions`, `memoir_syntheses`) |
| B3 | `tsv` generated column | `information_schema`: `tsvector` / `is_generated=ALWAYS` |
| B4 | GIN index | `idx_memoir_episodes_tsv` via `USING gin` |
| B5 | populates on INSERT | inserted row; `tsv @@ plainto_tsquery('english','memoir')` → 1 |
| B6 | inherited sentinels COMMITTED (non-vacuity control) | `instance_execution_leases` present; `uq_projects_name` count 1 |
| B7 | second boot on the same DB | `/livez` 200 (ensure idempotent, no error) |
| B8 | second boot is a no-op | tables **91==91**, indexes **571==571** |

---

## 4. Live fence (read-only evidence only)

`state.json` sha256 `520fce7f10fd4c1f0c2945f763d57af4e1e88a0cc417922dbe5512ed1359e0e6`
**PRE == MID == POST**; `current -> releases/v0.28.12` unchanged; `:9797/livez` **200/200/200**
(monotone). No write anywhere under `/home/nea/agents-ensemble/**`; no stage/promote/rollback
script run; nothing on 9797/8079/8088 touched; the shared main checkout untouched.

---

## 5. Leg C — STRUCTURAL PINS + mutation matrix

New pin file: `tests/unit/test_memoir_ensure_structure.py` (3 tests, AST-based extraction of
the `statements = [...]` list + execute loop inside `_ensure_postgres_columns`).

Baseline: **3 passed**.

| Mutation (transient, restored byte-identical) | Expected | Observed |
|---|---|---|
| M-i wrap `conn.execute(text(stmt))` in `try/except` | pin 2 RED | **RED** — `test_the_statement_loop_has_no_per_statement_try_except` (1 failed) |
| M-ii open a second `with self._engine.begin()` around the loop | pin 1 RED | **RED** — `test_the_shared_ensure_block_uses_exactly_one_transaction` (1 failed) |
| M-iii remove the two memoir statements from the list | pin 3 RED | **RED** — `test_the_memoir_tsv_and_gin_statements_ride_the_shared_list` (1 failed) |

After each leg: source restored, sha == clean, final baseline **3 passed**.

**Behavioral half of M-i (boot-level, own planted build `dc3f719b…`):** the combined mutant
(invalid `tsv` + `try/except`) **BOOTS**: `/livez` 200 — i.e. W8.2-1 flips RED exactly as
AC3 predicts. Its DB shows `tsv` **absent** and `instance_execution_leases` **absent**:
PostgreSQL aborts the whole transaction on the first error, every later `execute` raises
`InFailedSqlTransaction` (swallowed), and `COMMIT` on an aborted transaction behaves as
`ROLLBACK` — so the try/except turns a loud abort into a **silently fully-unapplied ensure**
(the daemon boots; the first lexical query would crash later — the house docstring's own
rationale for fail-loud). See caveat §6.2.

---

## 6. Disclosures (caveats)

**6.1 — A-3 literal wording vs the create_all commit boundary.** The brief's parenthetical
("all 4 memoir tables absent") is not attainable in the frozen binary: `create_all`
(`daemon/manager.py:990`) commits in its own transaction **before** the shared ensure runs
(`:1012`), so the four TABLES survive the ensure rollback (observed count 4). The binding
W8.2-3 wording is precise — "`tsv` is absent after the failed boot (the transaction rolled
back)" — and that is exactly what was observed (tsv + GIN absent, sentinel absent). The
blast radius proven is the **ensure transaction's** collateral rollback; the table-creation
step is outside it. **Any DIFFERENT outcome would be RED — this is not one.**

**6.2 — mutant semantics are stronger than "half-applied".** The mutant boots with the
**entire** ensure rolled back, not a partial application. Disclosure only; it strengthens the
invariant's justification.

**6.3 — banner version.** Scratch binaries report `Starting Ensemble v0.28.11` (the worktree's
`__version__`), expected for a lane build on the current base.

**6.4 — W8.5 verbatim intent (recorded):** the observed blast radius — **whole-boot abort +
collateral rollback of the shared ensure transaction** — is the **ADOPTED POSTURE** per
`decisions.md` §4 row 17 (Q6 no-containment), **not a defect**. The kill-switch does not
protect the boot; the rollback is fix-loss only and every inherited statement is idempotent.

---

## 7. Pack + invocation

* Pack: `test/packs/memoir_w8_boot_abort_test.sh` (registered in `.agents/tester/PACKS.md`).
* Invocation used: `MEMOIR_W8_BINS_DIR=/tmp/g1-w8/bins timeout 900 bash test/packs/memoir_w8_boot_abort_test.sh`
  (`MEMOIR_W8_KEEP_SCRATCH=1` used once to archive raw logs).
* The pack self-provisions the scratch PG16 cluster + DB, the three payload roots, the
  pre-shaped sentinels, the fences, and the teardown; it requires the three binaries (a
  missing binary fails loud — the boot legs are never silently skipped).
* **Actual runtime: ~81 s** (01:00:56 → 01:02:17 for the full 22-assertion run incl. 4 boots).
  The 900 s registration is conservative.
* Raw evidence: `.agents/tester/RESULTS/raw/2026-11-05-memoir-g1-w8/` (logs + `MANIFEST.sha256`).

## 8. Final verdict

**PASS-WITH-NOTES.** A1–A5, B1–B8, C (pins ×3 mutations), and the boot-level mutant all hold;
the "same/different outcome" rule is satisfied in both directions. Notes: §6.1 (A-3 brief
parenthetical vs create_all boundary — W8.2-3 satisfied exactly) and §6.2 (mutant rolls back
the whole ensure, not half).
