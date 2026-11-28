# Pack results at the tip — revive-wedge re-gate (leg 01)

Date: 2026-11-28
Tip under test: `3a04a78b41344b8f3e7986d3b97672edc843d513` (branch `fix/revive-wedge-terminalization`)
Worktree: `/home/nea/ensemble-src-wt-revive-wedge`
Evidence root: `.agents/tester/RESULTS/2026-11-28-revive-wedge-regate/`

Every pack below ran at its **PACKS.md-registered invocation** (verbatim), with the `timeout 300`
command-level wrapper, the house scrubber where the row specifies it, and SERIAL execution for the PG lane.

## Component C — parent-completion seam

| Pack (row) | File | Collected | Passed | Failed | Deselected | Skipped | Wall | RESULT | Worker | Raw output |
|---|---|---|---|---|---|---|---|---|---|---|
| `revive_wedge_parent_completion_guard_unit_test` (L14) | `tests/unit/services/test_parent_completion_guard_pins.py` | 11 | 11 | 0 | 0 | 0 | 9 s (pytest 2.98 s) | **PASS** | `244445b8` | `pack-componentC-parent-completion-guard.out` |

Census matches the registration (PC-1…PC-10 + the C5/A1 advisory pin = 11 nodes).

## Component A — narrow guard + decoupling + observability + real-PG pin

| Pack (row) | File | Collected | Passed | Failed | Deselected | Skipped | Wall | RESULT | Worker | Raw output |
|---|---|---|---|---|---|---|---|---|---|---|
| `revive_wedge_child_reports_narrow_guard_pg` (L30) | `tests/postgres/test_child_reports_narrow_guard_pg.py` | 1 | 1 | 0 | 0 | 0 | 11.19 s (pytest 4.07 s) | **PASS** | `d76948c3` | `pack-A-pg-narrow-guard.out` |
| `revive_wedge_obligation_triple_discriminator_pg` (L31) | `tests/postgres/test_obligation_triple_discriminator_pg.py` | 5 | 5 | 0 | 0 | 0 | 13.53 s (pytest 7.22 s) | **PASS** | `b5eae3b8` | `pack-A-pg-obligation-triple.out` |
| `revive_wedge_report_deferred_savepoint_pg` (L32) | `tests/postgres/test_report_deferred_savepoint_pg.py` | 4 | 4 | 0 | 0 | 0 | 13 s (pytest 6.56 s) | **PASS** | `0afeb003` | `pack-A-pg-deferred-savepoint.out` |
| `revive_wedge_report_injection_prefix_boundary_unit` (L33) | `tests/repositories/test_report_injection_prefix_boundary.py` | 2 | 2 | 0 | 0 | 0 | 0.47 s | **PASS** | `7d4199dd` | `pack-A-prefix-boundary.out` |
| `revive_wedge_b4_child_report_obligation_stay_green` (L34) | `tests/unit/services/test_b4_child_report_obligation.py` | 9 | 9 | 0 | 0 | 0 | 7 s (pytest 0.53 s) | **PASS** | `81d21682` | `pack-A-staygreen-b4-child-report-obligation.out` |
| `revive_wedge_terminal_row_status_strand_stay_green` (L35) | `tests/unit/services/test_terminal_row_status_strand.py` | 16 | 16 | 0 | 0 | 0 | 11 s (pytest 5.61 s) | **PASS** | `81d21682` | `pack-A-staygreen-terminal-row-status-strand.out` |
| `revive_wedge_fp4_premature_terminal_stay_green` (L36) | `tests/job_queue/test_fp4_premature_terminal_pins.py` | 8 | 8 | 0 | 0 | 0 | 11.46 s (pytest 5.40 s) | **PASS** | `729a3fe0` | `pack-A-staygreen-fp4-premature-terminal.out` |
| `revive_wedge_round2_council_fixes_stay_green` (L37) | `tests/job_queue/test_round2_council_fixes.py` | 15 | 15 | 0 | 0 | 0 | 10.90 s (pytest 4.97 s) | **PASS** | `729a3fe0` | `pack-A-staygreen-round2-council-fixes.out` |
| `revive_wedge_job_result_summary_gate_stay_green` (L38) | `tests/job_queue/test_job_result_summary_and_gate.py` | 15 | 15 | 0 | 0 | 0 | 11.09 s (pytest 4.62 s) | **PASS** | `729a3fe0` | `pack-A-staygreen-job-result-summary-gate.out` |

## Component B — system-log search paging (ride-along)

| Pack (row) | File | Collected | Passed | Failed | Deselected | Skipped | Wall | RESULT | Worker | Raw output |
|---|---|---|---|---|---|---|---|---|---|---|
| `revive_wedge_system_log_search_paging_unit_test` (L53) | `tests/unit/tools/test_system_log_search_paging_pins.py` + `tests/test_system_log_tools.py` | 101 | 101 | 0 | 0 | 0 | 7 s (pytest 1.79 s) | **PASS** | `4dfd591b` | `pack-B-system-log-search-paging.out` |

## TOTALS

| Metric | Value |
|---|---|
| Packs run | **11 of 11 registered** (Component A 9, Component B 1, Component C 1) |
| Nodes collected | **192** |
| Nodes passed | **192** |
| Nodes failed | **0** |
| Deselected | 0 |
| Skipped | **0** |
| TIMEOUT | 0 |
| FAILED ids | **none** |

## Skip-vacuity control (a green that is really a skip)

The house scrubber scrubs `POSTGRES_*`, so a DB-gated file can report RC=0 with every node SKIPPED.
Each green was checked for that:

- stay-green group 2: `grep` for `skipif|pytestmark|POSTGRES|requires_db|DATABASE_URL` across
  `test_fp4_premature_terminal_pins.py`, `test_round2_council_fixes.py`,
  `test_job_result_summary_and_gate.py` → **0 hits** ⇒ no node could skip; all 38 executed real assertions.
- stay-green group 1: `grep -cE 'FAILED|SKIPPED|deselected|ERROR'` over both raw captures → **0** on each.
- PG legs: `-rs` was passed and **zero skip lines** were emitted; the narrow-guard pack additionally
  asserts `assert _dbname == "ensemble_test"` **inside the test**, so the run provably hit the
  throwaway scratch DB and not a silent fallback. A skip would have been reported as a leg FAILURE.

## PG lane facts (throwaway only — no live contact)

| Fact | Value |
|---|---|
| Target DB | `ensemble_test` @ 127.0.0.1:5432 (PostgreSQL 16.15), throwaway, `public` table count 0 before the legs |
| App role | `ensemble_dev_app` (from `.env` `POSTGRES_PASSWORD`, 32 chars, never printed) — granted `CONNECT` + `USAGE, CREATE` on schema `public` (**load-bearing**: `public` is owned by role `ensemble`, not `pg_database_owner`, so `create_all` would have failed without it) |
| Superuser | role `ensemble` (`rolsuper=true, rolcreatedb=true`) — used only to grant; the app role lacks `CREATEDB` |
| Prod safety | `ensemble_prod` / `ensemble_prod_test` **do not exist on this cluster**; `ensemble_dev` is in the conftest's `FORBIDDEN_DB_NAMES` and was never targeted |
| Scrub | per-variable `env -u` for all 12 names (5 × `POSTGRES_*` + `DATABASE_URL` + 6 × `PG_TEST_*`). **No `env -u POSTGRES_*` glob** (bash does not glob env names — a silent no-op) |
| Serialisation | the 3 PG packs ran strictly sequentially: `pg_engine` does `drop_all`+`create_all` per session, so concurrent runs on one DB would manufacture false reds |
| Deferred cleanup | `psql -h 127.0.0.1 -p 5432 -U ensemble -d postgres -c "DROP DATABASE IF EXISTS ensemble_test;"` (recorded, not yet executed) |

## Non-fatal noise / harness notes (not product findings)

- `langchain_core` Pydantic-V1-on-Python-3.14 `UserWarning` on import — present at base as well.
- The agent shell is `/bin/sh` (dash): `set -o pipefail` and `${PIPESTATUS[0]}` are invalid there. Three
  workers hit this (`244445b8` "Bad substitution", `4dfd591b` "Illegal option -o pipefail" — the latter
  BEFORE pytest was spawned, `0afeb003` same). In every case the pack itself was unaffected and the exit
  code was re-captured POSIX-safely (`> "$OUT" 2>&1; RC=$?`). POK already carries this fact
  (`gate-pipe-tee-exit-code-dash-trap`, `bash-tool-dash-shell`); a skill-evolution fix was dispatched to
  the skill-keeper for `test-pack-execution` (`357d3c12-997f-41d2-b544-dd9b65aa9a77`, job `53cc275c`).
- Four `WARNING daemon.services.child_reports:child_reports.py:3766 No assistant content found for
  instance root-...` lines appear inside GREEN `terminal_row_status_strand` nodes — expected log output
  from passing tests, not failures.
