# int4-Overflow Gate Lessons (2026-09-28)

Gate: `fix/checkpoint-cleanup-int4-overflow` @ 0386c344 closing verification. Full evidence in `RESULTS/2026-09-28-checkpoint-cleanup-int4-overflow-verification.md`.

## 1. Worker PATH prerequisites on this host (recurring friction, now documented)

Worker shells do NOT inherit: `uv` (`/home/nea/.local/bin/uv` 0.12.5) or PG toolchain (`/usr/lib/postgresql/16/bin` — initdb/pg_ctl/psql). Two workers hit this independently this gate. Fix pattern for any pack needing them:
```
PATH="/home/nea/.local/bin:/usr/lib/postgresql/16/bin:$PATH"
```
`.venv/bin/pytest` works without PATH surgery — prefer `scripts/run_tests_scrubbed.sh` (execs `.venv/bin/pytest` directly) for pytest packs.

## 2. `scripts/run_tests_scrubbed.sh` is now the standard wrapper for DB-adjacent packs

New in this change set and functionally proven across 6 packs under ambient LIVE `POSTGRES_*` (ensemble_prod @ 10.44.0.2): family-wide `POSTGRES_*` scrub (catches novel spellings), enumerated libpq set, names-only echo-verify, exit-78 on leak (never fired), preserves `PG_TEST_*`, execs `.venv/bin/pytest`. Use it instead of hand-rolled `env -u` chains wherever a pack might touch DB config. It does NOT provision a PG server — throwaway initdb clusters on 15xxx ports remain the worker's job.

## 3. Playwright CLI: `--project` + positional filter conflict

`npx playwright test --config playwright.maintenance.config.ts --project maintenance maintenance-checkpoint-cleanup` — the CLI interprets the second positional as an ADDITIONAL project-name filter, not a file glob (silently matches nothing). With a single-project config, drop `--project` and let the sole positional resolve as the file glob. Prior PACKS.md invocations showing `--project maintenance` + positional worked only because of argument ordering specifics; the robust form is no `--project` when there's exactly one project.

## 4. Characterize diffs from `git diff`, never from commit prose or briefs

The commission brief and the first inspection pass both described the `20260927_000001` migration edit as "doc-only comment change"; the actual diff changes the SQL type token `INTEGER → BIGINT` in the canonical CREATE TABLE plus the comment. The mischaracterization was materially harmless here (checksum analysis proved the edit inert for all existing environments), but the gate's safety disposition depended on checking the real diff. Rule restated: every prod-diff claim entering a verification gate is re-derived from `git diff <base>..HEAD` by a worker, not trusted from prose.

## 5. Boot-order fidelity trick worth reusing (from the W1 test)

`test_ensure_postgres_columns_widens_existing_int4_column` exercises the REAL production ensure path without a full manager construction: `InstanceManager.__new__(InstanceManager)` + `_engine` + `_ensemble_config = SimpleNamespace(is_postgres=True)` → `_ensure_postgres_columns()`. Legacy int4 shape fabricated by raw DDL BEFORE `create_all` (checkfirst skips the pre-existing table). This is the cheapest known shape for behavioral pins of boot-time schema-ensure logic against disposable PG.

## 6. Minor observations (non-blocking, for review radar)

- `maintenance.py:1056/:1147` — "not enough values to unpack (expected 3, got 0)" ERROR logs under passing negative-path mock tests (mock-shape artifact; graceful degradation if a real backend ever returns empty rows). Companion-family pack.
- Endpoint truth: `/api/maintenance/checkpoint-cleanup/execute` returns **202 Accepted** (async pickup + `/runs/{run_id}` polling), not 200 — briefs/tests should expect 202.
- jsonb-migration suite probes `localhost:5432/ensemble_test` as user `nea` → env-skips on this host (local cluster ≠ live host 10.44.0.2; harmless, but it means those 5 nodes provide no signal here).
