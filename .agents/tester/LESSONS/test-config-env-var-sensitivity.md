# test_config.py Environment Variable Sensitivity

## Issue
`tests/test_config.py::TestQueueConfig::test_queue_config_defaults` fails when `QUEUE_DISCARD_ON_STARTUP=true` is set in the environment.

## Root Cause
Pydantic's `QueueConfig` reads env vars automatically via `QUEUE_*` prefix. The test asserts `config.discard_on_startup is False` but the env var overrides the default.

## Impact
- Pre-existing issue, not related to any feature branch
- Fails only when `QUEUE_DISCARD_ON_STARTUP` env var is set in the shell environment
- Not a flaky test — consistently fails in environments with that env var

## Recommendation
Test should either:
1. Use `@pytest.fixture` to temporarily unset the env var
2. Use `monkeypatch.delenv("QUEUE_DISCARD_ON_STARTUP", raising=False)` 
3. Or test should set the env var explicitly before creating config
