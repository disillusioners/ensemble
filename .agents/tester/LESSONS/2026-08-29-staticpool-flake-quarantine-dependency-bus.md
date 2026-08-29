# StaticPool Session-Refresh Flake — Quarantined (2026-08-29)

## Root cause
`tests/test_dependency_bus.py::TestGenerationCounterBump::test_per_parent_lock_serializes_db_insert` fails intermittently with `sqlalchemy.exc.InvalidRequestError: Could not refresh instance '<DependencyWatcher>'` from `daemon/repositories/dependency_bus/repository.py:132` (`session.refresh(watcher)` inside `insert()`), reached via `asyncio.to_thread(self._repo.insert, watcher)` (`dependency_bus.py:523`).

The fixture uses `sqlite:///:memory:` + `StaticPool` + `check_same_thread=False`; the test drives 4 concurrent `bus.watch()` calls via `asyncio.gather` → 4 OS threads share ONE connection. SQLAlchemy Session identity-map is not thread-safe: one thread's commit between another's refresh invalidates the instance handle. Production never sees this shape (QueuePool per-thread checkout).

## Evidence
- Gate completion_regression pack: run 1 = 1F, run 2 = 97P/0F (same commit ba39a40e)
- Retry budget (isolated single-test invocations): 2P then 1F with BYTE-IDENTICAL signature → CONFIRMED FLAKY (3P/2F across 5 attempts total)
- Branch-implication ruled out: `insert()` path untouched by b4dbfda2..ba39a40e; the branch's transition_state guard is a sibling method; test failure mode (session.refresh) ≠ the guard's failure mode (assert count)

## Disposition
- QUARANTINE.md row (2026-08-29); pack deselect in completion_regression_test.sh committed as `2d5f8a11` (96P/37S/1-des re-verified)
- Permanent fix candidates (owner: dependency_bus area): NullPool/QueuePool fixture, or per-parent `asyncio.Lock` around `session.refresh` in `repository.insert`

## Lesson
StaticPool + `asyncio.to_thread` fan-out = known flake recipe (2nd occurrence family-wide; conftest at tests/job_queue documents the antipattern). New concurrency tests should use file-backed SQLite per the observer-diagnostics file (NamedTemporaryFile) or tmp_path pattern. When a pack flakes once: complete the 3× retry budget BEFORE classifying — this one reproduced byte-identically in isolation, ruling out pack-pollution.
