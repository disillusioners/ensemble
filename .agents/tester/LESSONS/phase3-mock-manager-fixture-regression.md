# Phase 3 Lesson: Mock Manager Fixture Regression Pattern

## Issue
When adding new properties to `InstanceManager` (like `is_write_paused`), all existing test fixtures that use `Mock()` for the manager will get truthy auto-generated attributes, causing unexpected behavior.

## Pattern
```python
# In source code (routers):
if manager.is_write_paused:
    raise HTTPException(503, "Writes are paused")

# In test fixtures:
mock_manager = Mock()  # mock_manager.is_write_paused is TRUTHY (Mock object)
```

## Impact
232 test failures from a single new property. All return 503 instead of expected status codes.

## Fix Pattern
When adding new properties/methods to InstanceManager:
1. Search all test fixtures that create Mock managers
2. Add explicit `mock_manager.new_property = False/None` (appropriate default)
3. Also check `app.state.manager` is set (not just on dependency injection)

## Prevention
Consider creating a `create_mock_manager()` fixture that always returns a properly configured mock with all current InstanceManager attributes set to safe defaults.
