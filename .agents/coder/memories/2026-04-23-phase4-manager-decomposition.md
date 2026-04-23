# Phase 4: InstanceManager Decomposition Experience

## What Was Done
Decomposed the 2985-line `InstanceManager` god class into 7 focused services using a facade pattern.

## New Service Files (daemon/services/)
- `instance_lifecycle.py` (495 lines) — spawn, terminate, instance state
- `instance_messaging.py` (1009 lines) — message handling, processing
- `child_reports.py` (624 lines) — completion reports, parent notification
- `error_reporting.py` (292 lines) — error reporting
- `cancellation.py` (111 lines) — request cancellation
- `title_generation.py` (129 lines) — title generation
- `event_publisher.py` (73 lines) — lifecycle events

## Key Architecture Decision
Services access manager state through `self._manager.*` using `@property` getters rather than constructor injection. This makes the facade transparent to test mocks — when tests mock `manager._instance_repository`, services see the mock through the manager reference.

## Lessons Learned

1. **God class decomposition is extremely risky** — The initial implementation broke 18+ tests due to subtle behavioral differences.

2. **Mock transparency requires proxy pattern** — Constructor injection of repositories/services breaks tests that mock `manager._repo`. Using `self._manager._repo` through properties preserves mock compatibility.

3. **"Inline fallback" methods are code smells** — The first fix session added `_spawn_instance_inline()` and `_terminate_instance_inline()` as fallbacks. These are anti-patterns that should be removed in favor of proper delegation.

4. **The facade is still 1656 lines** — larger than the plan's ~600 estimate. This is because: init is ~200 lines, many delegation wrappers, some methods weren't fully extracted. A future cleanup pass could reduce this.

5. **parse_think_tags with list content** — When moving `_process_message_with_tracking`, the code that handles "list content" (where message content is a list of dicts) must be preserved exactly. The list→string conversion logic was fragile.

6. **Review caught a critical NameError** — `clear_all_instances()` in `instance_lifecycle.py` referenced `instance_repository` instead of `self._manager._instance_repository`. This would have crashed at runtime.

7. **worker_pool.py changes** — The session also extracted a `_notify_parent_of_failure()` helper in worker_pool.py. This was appropriate cleanup, not scope creep.

## Test Results
- 2827 passed, 27 skipped, 0 failed (excluding integration tests)
- Integration test failures are all pre-existing (need real DB/network)
- 1 test file modified (mock target update from daemon.manager.logger → daemon.services.instance_messaging.logger)

Commit: 0060d31e
