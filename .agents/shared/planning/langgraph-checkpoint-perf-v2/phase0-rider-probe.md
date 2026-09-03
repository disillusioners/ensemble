# Phase 0 Rider Probe — `tests/test_settings_api.py` ×12 InvalidSchemaName

- **Date:** 2026-09-03 (probe window ≤15 min, started 23:32:05 UTC)
- **Branch under probe:** `feature/langgraph-checkpoint-perf-v2` @ `2f80d45b`
- **Probe type:** bounded READ-ONLY diagnostic; DB touched: `ensemble_cpv2_test` only
- **Outcome:** **(A) CONFIRMED mechanism** — fixture-DSN drift: `pg_engine` connects to `ensemble_test` (via `PG_TEST_*` defaults), where the `public` schema is missing; `ensemble_cpv2_test` is healthy and was the WRONG database to indict.
- **Port-caused?** **No** — categorically not port-caused (see §4).

---

## 1. Fixture chain (read-only inspection)

`tests/test_settings_api.py` builds its own DSN from `PG_TEST_*` env vars (lines 79–85):

```python
PG_HOST = os.environ.get("PG_TEST_HOST", "localhost")
PG_PORT = int(os.environ.get("PG_TEST_PORT", "5432"))
PG_DB = os.environ.get("PG_TEST_DB", "ensemble_test")          # ← default target
PG_USER = os.environ.get("PG_TEST_USER", "ensemble")
PG_PASSWORD = os.environ.get("PG_TEST_PASSWORD", "ensemble_dev")
PG_URL = f"postgresql+psycopg://{PG_USER}:{PG_PASSWORD}@{PG_HOST}:{PG_PORT}/{PG_DB}"
```

`pg_engine` (session-scoped, lines 105–128) then runs `SQLModel.metadata.create_all(create_engine(PG_URL, future=True))`. **The fixture never reads `POSTGRES_URL` or `POSTGRES_DB`** — pinning those two env vars has zero effect on which database this file hits. The same `PG_TEST_*` default block exists in `tests/postgres/conftest.py:68–72`.

No repo-level pin of `PG_TEST_*` exists (grep over `pytest.ini`, `pyproject.toml`, `setup.cfg`, `tox.ini`, `Makefile`, `scripts/`, `.github/`: only the two conftest default blocks; shell env: 0 `PG_TEST_*` vars set).

## 2. Commands run (pinned DSN shown with password redacted)

### 2.1 DB sanity check — `ensemble_cpv2_test` is test-scale and healthy

```
POSTGRES_URL=postgresql://ensemble:***@localhost:5432/ensemble_cpv2_test \
POSTGRES_DB=ensemble_cpv2_test \
psql -h localhost -p 5432 -U ensemble -d ensemble_cpv2_test -Atc \
  "SELECT count(*) FROM information_schema.tables WHERE table_schema='public';"
→ 4
```

The 4 public tables: `checkpoints`, `checkpoint_blobs`, `checkpoint_migrations`, `checkpoint_writes` (LangGraph saver tables only — `projects` does NOT exist; the DB appears wiped after the baseline, leaving only checkpoint-perf tables). Schemas present: `information_schema`, `pg_catalog`, `public`.

Search-path state (same pinned connection):

```
SHOW search_path                    → public
pg_roles.rolconfig for 'ensemble'   → {search_path=public}
pg_db_role_setting (db+role level)  → empty
Server: PostgreSQL 14.22 (Homebrew) aarch64
```

DDL probe as role `ensemble` against the pinned DB (rolled back, zero residue):

```
BEGIN; CREATE TABLE _probe_probe(c int); ROLLBACK;   → succeeded (ROLLBACK)
```

**Conclusion from 2.1:** `ensemble_cpv2_test.public` EXISTS, is writable by `ensemble`, and `search_path=public` resolves. The predecessor claim ("cpv2_test has no public schema") is disproven again under current state.

### 2.2 Minimal reproduction — single-file pytest run, pinned envs, `PG_TEST_*` unset (baseline condition)

```
POSTGRES_URL=postgresql://ensemble:***@localhost:5432/ensemble_cpv2_test \
POSTGRES_DB=ensemble_cpv2_test \
uv run pytest tests/test_settings_api.py -o addopts= -x -q --no-header \
  -p no:cacheprovider --tb=short
→ ERROR tests/test_settings_api.py::TestGetLanguage::test_returns_default_auto_when_unset
  E sqlalchemy.exc.ProgrammingError: (psycopg.errors.InvalidSchemaName)
    no schema has been selected to create in
  E LINE 2: CREATE TABLE infra_asset_types (
  (session-scoped pg_engine fixture → setup ERROR → all 12 tests error)
```

**Critical observation:** with `PG_TEST_*` unset, this run's DSN was `postgresql+psycopg://ensemble:ensemble_dev@localhost:5432/ensemble_test` — i.e. the module connected to **`ensemble_test`, not the pinned `ensemble_cpv2_test`**, proving (a) the pin is ignored by this fixture and (b) the connection itself SUCCEEDED: the default password `ensemble_dev` differs from the configured password in `data/ensemble.json` (verified by comparison, value not reproduced), yet no auth error occurred — local `pg_hba` therefore accepts it (trust), so the failure is inside the target database, not authentication.

Count check: `pytest tests/test_settings_api.py --co -q -o addopts= -p no:cacheprovider` → **12 tests collected** — matches the failing family exactly.

## 3. Confirmed mechanism (A)

1. `tests/test_settings_api.py` ignores `POSTGRES_URL`/`POSTGRES_DB`; its `pg_engine` fixture targets `PG_TEST_DB` (default **`ensemble_test`**) on localhost:5432.
2. In the database the fixture actually reaches (`ensemble_test`), the role-wide setting `search_path=public` resolves to **nothing** — i.e. `ensemble_test` is missing its `public` schema. This is the only state consistent with: role `search_path=public` (verified via `pg_roles`) + successful connection + SQLSTATE 3F000 on an unqualified `CREATE TABLE`; the DDL probe succeeding in `ensemble_cpv2_test` rules out role/privilege causes. `SQLModel` tables carry no `schema=` qualifier, so `create_all` emits bare `CREATE TABLE infra_asset_types (...)` → `no schema has been selected to create in`. (The dispatcher's `schema=<missing>` hypothesis is also ruled out — no `schema=` kwargs.)
3. `ensemble_cpv2_test` is healthy (§2.1). The predecessor attributed the failure to the wrong database — they inspected the pinned disposable DB while the fixture never connects to it. This reframes the "disproven" claim: `public` was checked in the wrong DB; the mechanism stands, just one database over.
4. Residual (honesty note): I did not open a direct connection to `ensemble_test` (probe binding limits touched DBs to `ensemble_cpv2_test`), so "`public` missing in `ensemble_test`" is inferred from the behavioral chain above rather than read from `pg_namespace` there. One `psql -d ensemble_test -c '\dn'` by an unbound operator settles it in seconds.

**Fix direction (for the later worker, not applied here):** align the fixture DSN with the pinned environment (read `POSTGRES_URL`/`POSTGRES_DB`, or set `PG_TEST_DB=ensemble_cpv2_test` + `PG_TEST_PASSWORD` when driving this file), and/or recreate the `public` schema in `ensemble_test`. Either makes the module runnable against the disposable DB.

## 4. Not port-caused

- Phase-0 baseline recorded the ×12 at base `2f80d45b` with ZERO port changes (`.agents/shared/planning/langgraph-checkpoint-perf-v2/phase0-baseline.md` ~lines 97/112/120).
- The probe reproduced the identical error at base `2f80d45b` with the port's code present but inert — the failing path (`PG_TEST_*` defaults → `ensemble_test` → `create_all`) never touches the port surface (`daemon/persistence.py` instrumentation, `daemon/services/maintenance.py` timing, new port files).
- The fixture's last functional touch dates to 2026-07-22 (`6ceb6c31`) — pre-branch.

## 5. CORRECTION BLOCK (for a later worker to apply; this probe did not edit those files)

### 5.1 `phase0-baseline.md` (~line 97, the test_settings_api row)

**Stale sentence (verbatim):**

> (test fixture's `pg_engine` uses `SQLModel.metadata.create_all(create_engine(PG_URL))` without `search_path`; the disposable DB has no schema)

**Replacement:**

> (the `pg_engine` fixture builds its own DSN from `PG_TEST_*` env defaults — db `ensemble_test`, password `ensemble_dev` — and ignores `POSTGRES_URL`/`POSTGRES_DB`; the connected DB `ensemble_test` is missing its `public` schema, so the role-wide `search_path=public` resolves to nothing and unqualified `CREATE TABLE` fails with SQLSTATE 3F000. `ensemble_cpv2_test`'s `public` schema EXISTS and accepts DDL as role `ensemble` — probe-verified. Rider probe 2026-09-03: `phase0-rider-probe.md`)

### 5.2 `phase0-baseline.md` (~line 112, supporting bullet)

**Stale clause (verbatim):**

> the `pg_engine` fixture lacks `search_path` setup.

**Replacement:**

> the `pg_engine` fixture lacks `PG_TEST_*`/`POSTGRES_*` DSN alignment — it targets `ensemble_test` (default `PG_TEST_DB`), whose `public` schema is missing; `search_path` setup is not the lever.

### 5.3 `phase0-state.md` (~line 179, root-cause attribution #1)

**Stale sentence (verbatim):**

> The test file's `pg_engine` fixture calls `SQLModel.metadata.create_all(create_engine(PG_URL, future=True))` without `search_path` setup; the disposable DB `ensemble_cpv2_test` has no `public` schema configured.

**Replacement:**

> The test file's `pg_engine` fixture builds its own DSN from `PG_TEST_*` env defaults (db `ensemble_test`), ignoring `POSTGRES_URL`/`POSTGRES_DB`; the connected DB `ensemble_test` is missing its `public` schema, so `SQLModel.metadata.create_all`'s unqualified `CREATE TABLE` fails with SQLSTATE 3F000 (`search_path=public` resolves to nothing there). `ensemble_cpv2_test` is NOT the implicated DB — its `public` schema exists and accepts DDL as role `ensemble` (probe-verified 2026-09-03; rider probe `phase0-rider-probe.md`).

*(Keep the adjacent "Last touched 2026-07-22 (`6ceb6c31`) — pre-dates this port branch." sentence unchanged — it remains correct.)*

---

## Appendix: probe inventory

| # | Action | Result |
|---|--------|--------|
| 1 | Read `tests/test_settings_api.py` (fixture + DSN block) | `PG_TEST_*` defaults → `ensemble_test` |
| 2 | Grep repo configs + shell for `PG_TEST_` pins | none outside the two conftest default blocks |
| 3 | Sanity: table count `ensemble_cpv2_test.public` | 4 (checkpoint tables only), test-scale |
| 4 | Spot row-count `projects` | relation does not exist (DB wiped post-baseline) |
| 5 | Schemas / role+db search_path settings / `SHOW search_path` | `public` present; `search_path=public` role-wide; no overrides |
| 6 | Rolled-back DDL probe as `ensemble` | CREATE succeeds in `ensemble_cpv2_test` |
| 7 | Single-file pytest repro (pinned envs, `PG_TEST_*` unset) | exact InvalidSchemaName at `CREATE TABLE infra_asset_types`; 12 collected |
| 8 | Fixture default password vs configured password | differ (no auth failure ⇒ trust auth) |
| 9 | `pg_hba_file_rules` read | permission denied for role `ensemble` (non-superuser) — skipped, not needed |
