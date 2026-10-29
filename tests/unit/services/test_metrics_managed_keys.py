"""P0 T5 — the SEVEN metrics managed keys (plan §5 / catalog §0.8).

The registry is the ONE truth and it must mirror the backing pydantic
Field EXACTLY: the default, the bounds and the value type. The bounds
are validated at WRITE time (the registry's validator is the one
validation truth — Ruling 1).

The restart column is the plan §5 table verbatim:

* ``limits.metrics_enabled`` — no restart (per-request);
* ``limits.metrics_listener_port`` — restart;
* ``limits.metrics_scrape_interval_hint_s`` — no restart (advisory);
* ``limits.metrics_census_interval_s`` — restart;
* ``limits.metrics_sampler_interval_s`` — restart;
* ``limits.metrics_textfile_dir`` — restart;
* ``limits.metrics_cardinality_cap`` — no restart (hard cap).
"""

from __future__ import annotations

import pytest

from daemon.config import LimitsConfig
from daemon.services.settings_service import (
    MANAGED_KEYS,
    SettingsValidationError,
    parse_and_validate,
)

METRICS_KEYS = (
    "limits.metrics_enabled",
    "limits.metrics_listener_port",
    "limits.metrics_scrape_interval_hint_s",
    "limits.metrics_census_interval_s",
    "limits.metrics_sampler_interval_s",
    "limits.metrics_textfile_dir",
    "limits.metrics_cardinality_cap",
)

#: The plan §5 restart column.
RESTART_COLUMN = {
    "limits.metrics_enabled": False,
    "limits.metrics_listener_port": True,
    "limits.metrics_scrape_interval_hint_s": False,
    "limits.metrics_census_interval_s": True,
    "limits.metrics_sampler_interval_s": True,
    "limits.metrics_textfile_dir": True,
    "limits.metrics_cardinality_cap": False,
}


@pytest.mark.parametrize("key", METRICS_KEYS)
def test_the_key_is_registered(key):
    assert key in MANAGED_KEYS


@pytest.mark.parametrize("key", METRICS_KEYS)
def test_the_registry_default_mirrors_the_pydantic_field(key):
    """ONE truth: the registry default == the pydantic Field default."""
    spec = MANAGED_KEYS[key]
    field_name = key.split(".", 1)[1]
    field = LimitsConfig.model_fields[field_name]
    assert spec.default == field.default


@pytest.mark.parametrize("key", METRICS_KEYS)
def test_the_registry_bounds_mirror_the_pydantic_field(key):
    spec = MANAGED_KEYS[key]
    field_name = key.split(".", 1)[1]
    metadata = LimitsConfig.model_fields[field_name].metadata
    ge = next((m.ge for m in metadata if hasattr(m, "ge")), None)
    le = next((m.le for m in metadata if hasattr(m, "le")), None)
    assert spec.ge == ge
    assert spec.le == le


@pytest.mark.parametrize("key", METRICS_KEYS)
def test_the_registry_value_type_matches_the_field_type(key):
    spec = MANAGED_KEYS[key]
    field_name = key.split(".", 1)[1]
    annotation = LimitsConfig.model_fields[field_name].annotation
    if annotation is bool:
        expected = "bool"
    elif annotation is int:
        expected = "int"
    elif annotation is str:
        expected = "str"
    elif annotation is float:
        expected = "float"
    else:  # pragma: no cover — a new type must be registered
        pytest.fail(f"unhandled annotation {annotation!r}")
    assert spec.value_type == expected


@pytest.mark.parametrize("key", METRICS_KEYS)
def test_the_restart_column_matches_the_plan(key):
    assert MANAGED_KEYS[key].requires_restart is RESTART_COLUMN[key]


def test_the_env_var_mirrors_the_limits_env_prefix():
    for key in METRICS_KEYS:
        spec = MANAGED_KEYS[key]
        assert spec.env_var == "LIMITS_" + key.split(".", 1)[1].upper()
        assert spec.config_path == key


# ─── write-time bounds validation ─────────────────────────────────────────


@pytest.mark.parametrize(
    "key,value",
    [
        ("limits.metrics_listener_port", 1023),
        ("limits.metrics_listener_port", 65536),
        ("limits.metrics_scrape_interval_hint_s", 4),
        ("limits.metrics_scrape_interval_hint_s", 301),
        ("limits.metrics_census_interval_s", 14),
        ("limits.metrics_census_interval_s", 301),
        ("limits.metrics_sampler_interval_s", 0),
        ("limits.metrics_sampler_interval_s", 61),
        ("limits.metrics_cardinality_cap", 99),
        ("limits.metrics_cardinality_cap", 10001),
    ],
)
def test_out_of_bounds_values_are_rejected_at_write_time(key, value):
    ok, _parsed, failure_class, _detail = parse_and_validate(
        MANAGED_KEYS[key], value
    )
    assert ok is False
    assert failure_class == "validation"


@pytest.mark.parametrize(
    "key,value",
    [
        ("limits.metrics_enabled", True),
        ("limits.metrics_enabled", False),
        ("limits.metrics_listener_port", 1024),
        ("limits.metrics_listener_port", 65535),
        ("limits.metrics_listener_port", 9798),
        ("limits.metrics_scrape_interval_hint_s", 5),
        ("limits.metrics_scrape_interval_hint_s", 300),
        ("limits.metrics_census_interval_s", 15),
        ("limits.metrics_census_interval_s", 300),
        ("limits.metrics_sampler_interval_s", 1),
        ("limits.metrics_sampler_interval_s", 60),
        ("limits.metrics_textfile_dir", "/tmp/metrics"),
        ("limits.metrics_cardinality_cap", 100),
        ("limits.metrics_cardinality_cap", 10000),
        ("limits.metrics_cardinality_cap", 2000),
    ],
)
def test_in_bounds_values_are_accepted(key, value):
    ok, parsed, failure_class, detail = parse_and_validate(
        MANAGED_KEYS[key], value
    )
    assert ok is True, f"{failure_class}: {detail}"
    assert parsed == value


def test_the_census_interval_floor_is_fifteen_seconds():
    """The census cadence is NEVER faster than the resource monitor."""
    ok, _parsed, _fc, _detail = parse_and_validate(
        MANAGED_KEYS["limits.metrics_census_interval_s"], 14
    )
    assert ok is False


def test_the_cardinality_cap_is_bounded():
    ok, _parsed, _fc, _detail = parse_and_validate(
        MANAGED_KEYS["limits.metrics_cardinality_cap"], 10001
    )
    assert ok is False


def test_a_bool_key_rejects_a_non_bool():
    ok, _parsed, failure_class, _detail = parse_and_validate(
        MANAGED_KEYS["limits.metrics_enabled"], "yes-please"
    )
    assert ok is False
    assert failure_class == "parse"


def test_the_write_path_raises_on_an_out_of_bounds_value():
    """The service's write path rejects the value (422 upstream)."""
    spec = MANAGED_KEYS["limits.metrics_census_interval_s"]
    ok, _parsed, failure_class, _detail = parse_and_validate(spec, 1)
    assert ok is False
    assert failure_class == "validation"
    assert SettingsValidationError is not None
