# Lessons — checkpoint-conn-resilience round 1 (2026-10-10)

## 1. Mock-lane inversion: a conftest mock can certify the WRONG semantics (K3 family)
The delivered suite's langgraph conftest mock replaced `langgraph.checkpoint.base` etc. during pytest, so proxy MRO behavior (`get_next_version`, `alist`) was certified in MOCK-world, not against real langgraph 1.0.9 — while the two tests that would have caught it were env-skipped (`langgraph.types` absent in 1.0.9). **Rule: for any wrapper/proxy that subclasses a third-party base, at least one certification lane must import the REAL dependency (standalone python script is sufficient and cheap — no pytest, no conftest).** The B2 wave's `evict_langgraph_mocks()` round-trip is the in-pytest escape hatch when a standalone lane is impractical.

## 2. False-green skip family: sync guard called from async fixture (H1)
`require_postgres()` (docstring: "safe to call from sync contexts") was called INSIDE an async pytest fixture → `asyncio.run()` RuntimeError → helper's bare `except Exception` → `pytest.skip("PostgreSQL not available")`. The pack showed PASS 7P/2S while BOTH fix-specific tests never ran. **Rule: treat any skip of a fix-specific test as a gate failure until the skip reason is disproven at runtime (live connectivity probe on the same cluster the siblings passed on).** Skip-reason text is a claim, not evidence.

## 3. Attack-vector mismatch: readiness degrade needs an OUTAGE, not conn churn (D3)
`pg_terminate_backend` churn (even sustained 0.3s-interval for 75s) never flipped `checkpoint_saver:false` — `AsyncConnectionPool(check=check_connection)` self-heals faster than the 10s readiness refresh tick, and the probe fails closed only on STRUCTURAL `pool.check()` failure (timeout/exception). **Rule: to exercise a degrade-not-restart readiness component, stop the backing service for > 2× refresh interval (outage-class), then restart for the recovery flip.** Conn-churn only proves pool resilience (which is the fix working as designed).

## 4. Ambient env = PROD on this host
The tester-side shell carries `POSTGRES_HOST=10.44.0.2 / POSTGRES_DB=ensemble_prod`. Every pack dispatched from this lane MUST carry `env -u POSTGRES_HOST -u POSTGRES_PORT -u POSTGRES_DB -u POSTGRES_USER -u POSTGRES_PASSWORD -u DATABASE_URL -u POSTGRES_URL`. Note the split namespace: daemon code reads `POSTGRES_*`; `tests/postgres/` + helpers read `PG_TEST_*` — fence BOTH, set only the throwaway target.

## 5. Throwaway PG recipe (host has server binaries off-PATH)
`/usr/lib/postgresql/16/bin/{initdb,pg_ctl,createdb}`; requires explicit `-c unix_socket_directories=/tmp/<sock>` (/var/run/postgresql not user-writable); port 15432/15433 free; trust auth for a disposable user; per-wave spin-up + `pg_ctl stop -m fast` + `rm -rf` — zero shared state, zero residue (proven across B2/A3/C waves).
