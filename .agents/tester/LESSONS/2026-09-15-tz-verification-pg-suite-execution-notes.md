# TZ-Fix Verification — PG Suite & Env Execution Traps (2026-09-15)

From the final gate for `feature/fix-job-queue-timestamps-tz` @ `6e5a7cb8`.
Full report: `../RESULTS/2026-09-15-job-timestamps-tz-verification.md`.

## 1. tests/postgres/ reads `PG_TEST_*`, NOT `ENSEMBLE_TEST_PG_URL`
`tests/postgres/conftest.py` builds its DSN from `PG_TEST_HOST/PORT/DB/USER/PASSWORD`. Older convention notes (and at least one PACKS.md precedent description) say `ENSEMBLE_TEST_PG_URL` — that var is IGNORED by this conftest. Safe pattern: set BOTH families pointing at the same disposable cluster. (2026-09-10 critical-notes gate had already corrected this; the lesson had not propagated.)

## 2. `-n auto` on tests/postgres/ is a FALSE-PASS
`tests/postgres/conftest.py:101-105` xdist-skip guard marks EVERY PG test `skip` when `PYTEST_XDIST_WORKER` is set → `-n auto` yields `308 skipped in 4.08s` (looks green, proves nothing). Correct invocation: serial with `--override-ini="addopts=" -m postgres`. Any future PG-shard sweep must collect-proof (`--collect-only -q | tail -1`) AND check the skip count — a suspiciously high skip count with tiny wall time is this trap.

## 3. Dispatcher-injected `POSTGRES_*` persists across worker bash subshells
The ensemble daemon process env carries prod `POSTGRES_*`; `unset` in one bash invocation does not persist to the next. Scrub-verify-repoint pattern per batch: `unset …; env | grep -Ei 'postgres|persistence_db_path|data_dir'` (must be empty of CONNECTION vars) → immediately export disposable values → verify again. Redirecting to disposable is the robust fallback when unset cannot persist.

## 4. PG `timestamp::text` render-normalization for byte-compares
`SELECT ts_col::text` drops trailing fractional zeros (`.977630` → `.97763`) — same instant, different bytes. Byte-comparing DB naive digits against API strings needs render-normalization; byte-comparing VARCHAR (TEXT-column) values against API strings is safe verbatim.

## 5. Seeding legacy +07-digit rows: bind AWARE values through a +07 session
Binding a naive datetime/string round-trips to UTC digits (the engine/ORM normalizes). To reproduce legacy skewed rows (the DC-A defect shape), bind an AWARE datetime through a session whose TZ is the +07 server default (or plain `create_engine` without the fix's connect_args) — exactly the original bug mechanism.

## 6. The jobs HTTP API has no `?since=` parameter
Verified across `daemon/routers/{jobs_crud,jobs_management,jobs_streaming,work,missions}` and `JobRepository.list`. The only since-filter surface in this codebase is the missions tool: `daemon/tools/missions.py::_parse_since` → `coerce_to_aware_utc`. Future since-filter tests must target that seam.
