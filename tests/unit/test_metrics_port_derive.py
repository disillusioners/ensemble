"""Unit tests for rider (d): derived metrics port (component B, pin (ii)).

Fix-round 2 / N8: the derive moved into ``daemon/services/metrics_listener.py``
(single source of truth) and gained an explicit edge policy:
``main+1 > 65535``, ``main_port < 1`` and invalid env values are
CLAMP-BY-DISABLE (None + loud ERROR) — never a silent bad bind.
"""

from pathlib import Path

from daemon.services.metrics_listener import derive_metrics_port

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_derive_scratch_main_plus_one():
    assert derive_metrics_port(8099) == 8100


def test_derive_live_main_unchanged():
    assert derive_metrics_port(9797) == 9798


def test_derive_env_escape_hatch():
    assert derive_metrics_port(8099, env_value="19999") == 19999


def test_derive_env_empty_falls_back():
    assert derive_metrics_port(8099, env_value="") == 8100
    assert derive_metrics_port(8099, env_value="   ") == 8100


def test_derive_env_invalid_disables(caplog):
    with caplog.at_level("ERROR", logger="daemon.services.metrics_listener"):
        assert derive_metrics_port(8099, env_value="not-a-port") is None
        assert derive_metrics_port(8099, env_value="70000") is None
    assert "DISABLED" in caplog.text


def test_derive_overflow_at_65535_disables_loudly(caplog):
    """N8: the pre-fix ``int(port)+1`` overflow."""
    with caplog.at_level("ERROR", logger="daemon.services.metrics_listener"):
        assert derive_metrics_port(65535) is None
    assert "exceeds 65535" in caplog.text


def test_derive_ephemeral_main_port_disables(caplog):
    with caplog.at_level("ERROR", logger="daemon.services.metrics_listener"):
        assert derive_metrics_port(0) is None
    assert "ephemeral" in caplog.text


def test_api_source_uses_the_single_derive_helper():
    src = (REPO_ROOT / "daemon" / "api.py").read_text(encoding="utf-8")
    assert "derive_metrics_port(" in src
    assert 'os.environ.get("LIMITS_METRICS_LISTENER_PORT")' in src
    # The retired managed-key fallback must not be the operative expression.
    assert "getattr(config.limits, \"metrics_listener_port\"" not in src
    assert "getattr(config.limits, 'metrics_listener_port'" not in src


def test_managed_key_retired_in_settings_service():
    src = (REPO_ROOT / "daemon" / "services" / "settings_service.py").read_text(encoding="utf-8")
    assert '"limits.metrics_listener_port": ManagedKeySpec' not in src
