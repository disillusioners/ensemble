# Phase 2 — Rider R-10: wedge-capture store-census fix

## 1. Objective

Repair the born-broken wedge-capture store census (`pg_class.n_live_tup` — column exists only on
`pg_stat_user_tables`) so the observability instrument for exactly this incident class works on
real PG, and re-point its real-PG test pack from born-broken to populated. Isolated, independently
gateable — parallelizable with Phase 1 (disjoint files).

Design references: technical-analysis §11 R-10; decisions D-8; findings-riders §6.

## 2. Prerequisites

None (independent of Phase 1). Same fences as Phase 0 (PG pack path: `PG_TEST_*` DSN,
scratch PG16-class fixture; serial-only under xdist).

## 3. Decisions

| ID | Decision | Rationale (cite) | Rejected alternatives |
|----|----------|------------------|------------------------|
| PD-2.1 | Minimal-join fix: keep `FROM pg_class c JOIN pg_namespace n …` + `pg_total_relation_size(c.oid)` verbatim; replace `c.n_live_tup::bigint` with `s.n_live_tup::bigint` via `LEFT JOIN pg_stat_user_tables s ON s.relid = c.oid` (NULL-safe — consumer handles None, `:392-397`) | D-8; findings-riders §6 "minimal isolated fix (query shape)"; in-repo correct-query precedent `ens_db_tools.py:1223`; the existing static pins survive this shape UNCHANGED (only a pg_class-dropping rewrite would redden them) | Rewriting the census off pg_class (reddens the static pin pack, larger blast radius); filtering pg_stat_user_tables alone (loses `pg_total_relation_size(c.oid)` per-table sizing) |
| PD-2.2 | Real-PG pack re-point: `tests/postgres/test_dbpool_wedge_capture_pg.py` census expectation moves from "failure mode is not the F5 cascade" (:26-46) to "census POPULATED" (values + keys `checkpoints_rows / checkpoint_blobs_rows / checkpoint_blobs_bytes`, `:398-400`); static pin pack runs UNCHANGED and green | D-8; technical-analysis §11 test-surface row | Leaving the PG pack asserting the broken mode (vacuous after the fix) |

## 4. Tasks

| ID | Task | Outcome (testable) | Touchpoints (files) | Depends on | Risks carried | AC |
|----|------|--------------------|---------------------|------------|---------------|----|
| T2.1 | Apply the census SQL fix in `_read_store_census` (PD-2.1) | On real PG: census populated (no `UndefinedColumn`, no `failed:UndefinedColumn` status); the read-3 `InFailedSqlTransaction` cascade abort disappears; SQLite path untouched (PG-only path) | `daemon/services/wedge_capture/db_capture.py` (:374-390) | — | — | AC-2.1 |
| T2.2 | Re-point the real-PG pack expectation (PD-2.2); run the static pin pack UNCHANGED (`tests/unit/tools/test_dbpool_wedge_capture_static.py:100-137`) and confirm green — every pin (`FROM pg_class c` :102, pg_namespace join :105, schema filter :108, `pg_total_relation_size(c.oid)` :111, own-function AST :115-130, exactly-one `FROM pg_class c` + one `FROM task t` :132-137) satisfied by the LEFT-JOIN shape | Static pack green with zero edits; PG pack green with populated-census assertions; a plant of the OLD `c.n_live_tup` form goes RED in the PG pack (mutation leg recorded for Phase 3 folding) | `tests/postgres/test_dbpool_wedge_capture_pg.py`; (static pack: run-only) | T2.1 | — | AC-2.2 |
| T2.3 | Census-value sanity on the scratch PG: seeded checkpoint rows ⇒ non-zero `checkpoints_rows` / `checkpoint_blobs_rows`; NULL-safety exercised (a table absent from pg_stat_user_tables ⇒ None handled by the consumer, no crash) | Sanity evidence recorded (bounded — one scratch run) | scratch PG fixture (evidence only) | T2.1 | — | AC-2.3 |

## 5. Acceptance criteria

| ID | Criterion |
|----|-----------|
| AC-2.1 | Census populated on real PG; cascade abort gone; SQLite unaffected |
| AC-2.2 | Static pin pack green UNCHANGED; PG pack re-pointed green; old-form plant RED (mutation-verified in Phase 3's consolidated matrix) |
| AC-2.3 | Populated values sane on seeded data; NULL-safe consumer path exercised |

## 6. Verification method

Direct pack runs (explicit `tests/` paths; PG marker path via the postgres conftest; serial-only
under xdist; `tee LOG >/dev/null` + `tail` for floods; explicit `--timeout` if the PG leg needs
it). The static pack doubling as a no-edit proof is the isolation guarantee.

## 7. Risks & if-flipped contingency

> **D-8 RATIFIED @ `9657be93a` (4.35 vs 3.05)** — include-as-rider CONFIRMED. The contingency
> below is retained as the priced flip path for the architect §12 non-blocking note: an overrule
> means routing R-10 to a (currently nonexistent) owner lane and accepting the census staying
> born-broken.

- **D-8 flip (route R-10 to the wedge-capture owner lane instead of this rider):** Phase 2 drops
  entirely (T2.1-T2.3 + AC-2.1-2.3); Phase 3 loses only the R-10 mutation-leg row; Phases 4/5
  corpora shrink accordingly; the handoff report (Phase 6) routes the defect with the findings
  citation (`db_capture.py:374-390` + the db-pool PG pack's born-broken measurement). Schedule
  shortens; nothing else moves.
