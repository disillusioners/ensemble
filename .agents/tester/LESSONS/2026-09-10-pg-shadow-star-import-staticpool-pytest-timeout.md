# PG Shadow Gotchas: star-import underscore fixtures, NullPool×to_thread deadlock, pytest-timeout vs addopts, pack sizing (2026-09-10 answer-gate gate)

Four verifier-infra lessons from the answer-gate-resume pre-merge gate (`feature/fix-answer-gate-resume` @ `c6348df0`). None touched code under test; cost: 2 extra pack runs + 1 architecture-fix worker.

## 1. `import *` silently drops underscore fixtures — shadows need explicit imports

**Shape:** the PG engine-shadow pattern star-imports the original test module (`from tests.unit.test_pause_resume_root import *`) to reuse its classes + fixtures. Python's `import *` skips underscore-prefixed names by spec — `_wire_bus_mock` (a real `@pytest.fixture` at test_pause_resume_root.py:109) never landed in the shadow namespace → `fixture '_wire_bus_mock' not found` ERROR on the single test that requested it.

**Fix (durable recipe):** in every shadow, add explicit imports for underscore fixtures: `from tests.unit.test_pause_resume_root import _wire_bus_mock  # noqa: F401`. Sweep the origin module for `^def _\w+` decorated with `@pytest.fixture` and import each.

**Detection trap:** the error was invisible in the combined 49-test pack because the 280s watchdog killed pytest before a summary; `-rfE` + per-pack splitting surfaced it.

## 2. `NullPool` × `asyncio.to_thread(<session-bound-method>)` = cross-thread deadlock on PG (the dangerous one)

**Shape:** LESSONS 2026-09-10 §1 prescribes `NullPool` for search_path-optioned shadow engines. But tests that create a session in the main thread and ship its bound method to a worker thread (`await asyncio.to_thread(self._instance_repository.get, instance_id)` — e.g. TestReviveTerminalInstancePersists / TestTerminalParentRevival / TestReconcileSubshapeARouterThreadingRegression in test_resume_router_deferred_recovery.py) deadlock under NullPool: test2's `to_thread` blocks forever against test1's still-draining default-executor threads. Verified by minimal 2-test repro (first PASS, second HANGS indefinitely).

**Failure mode:** presents as a pack TIMEOUT, easily mis-diagnosed as "per-test schema-cycle throughput". (This gate initially blamed create_all cost; root cause was the hang.)

**Fix (durable recipe):** for shadows whose tests use `asyncio.to_thread` with session-bound methods, switch the shadow engine to `StaticPool` (single shared connection; still fine with libpq `search_path` options — the URL options ride every checkout of the same connection). NullPool remains correct for shadows without cross-thread session usage.

**Discriminator:** a PG shadow pack where progress stalls mid-run with no failure output = suspect this deadlock, not slowness.

## 3. `--override-ini="addopts="` does NOT neutralize pytest-timeout

**Shape:** packs that clear `addopts` (to escape the default `-m` filter) assumed pytest-timeout was neutralized too. It is not — pytest-timeout reads the `timeout` ini option directly (pyproject: `timeout = 30`, `timeout_method = "thread"`). A 30s per-test timer fired mid-teardown and SIGKILLed pytest, swallowing all per-node detail.

**Fix (durable recipe):** add `--timeout=0` to pack pytest invocations where the pack's own dual-layer timeouts govern. Thread-method timers can't interrupt a hung teardown anyway — the pack watchdog is the real cap.

## 4. PG shadow pack sizing: split at ≤ ~30 tests

Per-test `DROP SCHEMA CASCADE` + `CREATE SCHEMA` + `SQLModel.metadata.create_all` on a real PG server costs seconds per test (many CREATE TABLE round-trips). A 49-test combined shadow pack exceeded any sane single budget; the split (21 + 28) runs in 7.06s + 3.18s respectively (most of the original slowness was lesson-2 hangs, but the schema-cycle cost is real). Keep PG shadow packs scoped ≤ ~30 tests; split by shadow module, each pack owning its own disposable DB + lifecycle traps.

## 5. Minor: verify `ensemble_prod`-untouched by OBSERVING NO CONNECTION, not by connecting

One cleanup verification ran a read-only catalog SELECT against `ensemble_prod` (`pg_tables` count) as "proof of untouched". Harmless but it violates the no-connection norm and normalizes prod touchpoints. Correct evidence: the pack's URL/name abort guards + listing only disposable/admin DBs in every command.
