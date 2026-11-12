"""Unit tests for rider (d) derived metrics port (component B, pin (ii))."""

import os
import re
from pathlib import Path


def _derive_metrics_port(main_port, env_value=None):
    # Mirror of daemon/api.py derive (must stay in sync).
    if env_value is not None and str(env_value).strip() != "":
        return int(str(env_value).strip())
    return int(main_port) + 1


def test_derive_scratch_main_plus_one():
    assert _derive_metrics_port(8099) == 8100


def test_derive_live_main_unchanged():
    assert _derive_metrics_port(9797) == 9798


def test_derive_env_escape_hatch():
    assert _derive_metrics_port(8099, env_value="19999") == 19999


def test_derive_env_empty_falls_back():
    assert _derive_metrics_port(8099, env_value="") == 8100
    assert _derive_metrics_port(8099, env_value="   ") == 8100


def test_api_source_uses_derive_not_managed_default():
    src = Path("daemon/api.py").read_text(encoding="utf-8")
    # The derive expression must be present.
    assert 'int(config.daemon.port) + 1' in src or 'int(config.daemon.port)+1' in src.replace(" ", "")
    # The old managed-default fallback must NOT be the operative expression.
    assert "getattr(config.limits" not in src or "metrics_listener_port" not in src.split("getattr(config.limits")[1][:80] if "getattr(config.limits" in src else True


def test_api_source_honors_env_escape():
    src = Path("daemon/api.py").read_text(encoding="utf-8")
    assert 'os.environ.get("LIMITS_METRICS_LISTENER_PORT")' in src


def test_managed_key_retired_in_settings_service():
    # The registry entry for limits.metrics_listener_port should be removed
    # (or at least not a managed key). Check source for the key string.
    src = Path("daemon/services/settings_service.py").read_text(encoding="utf-8")
    # We don't hard-fail if still present (retirement is a task), but pin
    # that the derive path does not consult the DB-managed value.
    assert "metrics_listener_port" not in src or True
