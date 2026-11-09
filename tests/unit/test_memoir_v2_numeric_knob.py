"""Memoir Store v2 — the three-class numeric knob governance (AC4).

Phase 2 of the Memoir Store v2 lane (plan:
``.agents/shared/planning/ari-memoir-store/v2-phase2-plan.md`` AC4;
pack ``test/packs/memoir_v2_numeric_knob_test.sh``).

The truth table (§3-A4 / Register #12 / V2-16):

| Class | Unset | Garbage / out-of-range |
|---|---|---|
| Enabled flags | ON + one WARNING naming the var | fail-CLOSED OFF + WARNING naming the value |
| Budget bounds | constitutional default + an AGGREGATE WARNING naming every unset budget knob | CLAMP to default + WARNING naming value AND default. NEVER zero |
| Cadence tunables | default, SILENT | default + WARNING |

THE LOAD-BEARING PROPERTY: **the daemon NEVER aborts**. A bare
``Field(gt=...)`` on a numeric raises ``ValidationError`` on an
unparseable env var, and the unguarded ``load_config`` then takes the
daemon DOWN (the house anomaly ``33028b27``). Every row below is
proven to resolve WITHOUT a ``ValidationError``.
"""

from __future__ import annotations

import logging

import pytest

KNOB_LOGGER = "daemon.tools.memoir_knob"

from daemon.config import (
    MEMOIR_REFLECTION_ENABLED_ENV_VAR,
    NUMERIC_CLASS_BUDGET,
    NUMERIC_CLASS_CADENCE,
    NUMERIC_CLASS_ENABLED,
    MemoirReflectionConfig,
    _classify_numeric,
    _reset_v2_knob_warnings,
)

pytestmark = pytest.mark.filterwarnings("ignore::UserWarning")

_ALL_KNOBS = (
    "ENSEMBLE_MEMOIR_REFLECTION_ENABLED",
    "ENSEMBLE_MEMOIR_REFLECTION_MAX_SYNTHESES_PER_PASS",
    "ENSEMBLE_MEMOIR_REFLECTION_MAX_CHARS_PER_SYNTHESIS",
    "ENSEMBLE_MEMOIR_REFLECTION_MAX_LLM_CALLS_PER_PASS",
    "ENSEMBLE_MEMOIR_REFLECTION_CONSTELLATION_MAX_LLM_CALLS_PER_PASS",
    "ENSEMBLE_MEMOIR_REFLECTION_TRIGGER_SUM",
    "ENSEMBLE_MEMOIR_REFLECTION_NIGHTLY_CAP",
    "ENSEMBLE_MEMOIR_REFLECTION_TICK_BUDGET_S",
    "ENSEMBLE_MEMOIR_REFLECTION_CALL_TIMEOUT_S",
    "ENSEMBLE_MEMOIR_REFLECTION_TICK_INTERVAL_S",
    "ENSEMBLE_MEMOIR_REFLECTION_DEBOUNCE_BASE_S",
    "ENSEMBLE_MEMOIR_REFLECTION_DEBOUNCE_CEILING_S",
    "ENSEMBLE_MEMOIR_REFLECTION_FORCE_FIRE_S",
    "ENSEMBLE_MEMOIR_REFLECTION_SCAN_WINDOW_DAYS",
)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for name in _ALL_KNOBS:
        monkeypatch.delenv(name, raising=False)
    from daemon.tools.memoir_knob import reset_boot_log_state

    _reset_v2_knob_warnings()
    reset_boot_log_state()
    yield
    _reset_v2_knob_warnings()
    reset_boot_log_state()


# ── the enabled-flag class ───────────────────────────────────────────────


def test_the_unset_kill_switch_resolves_on_with_a_warning(caplog):
    """AC4: unset ⇒ True + a WARNING naming the variable.

    THE WARNING IS EMITTED BY THE KNOB MODULE (the house precedent: the
    knob module owns the operator-facing log lines; the model is the
    CLASSIFIER, mechanism N-2). The model itself is SILENT — otherwise
    the warning would fire on EVERY ``Config()`` construction.
    """
    from daemon.tools.memoir_knob import (
        reset_boot_log_state,
        resolve_memoir_reflection_knob,
    )

    reset_boot_log_state()
    with caplog.at_level(logging.WARNING, logger=KNOB_LOGGER):
        state = resolve_memoir_reflection_knob()
    assert state.enabled is True
    text = "\n".join(record.getMessage() for record in caplog.records)
    assert MEMOIR_REFLECTION_ENABLED_ENV_VAR in text
    assert "unset" in text


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("true", True),
        ("TRUE", True),
        ("1", True),
        ("yes", True),
        ("on", True),
        ("t", True),
        ("y", True),
        ("false", False),
        ("FALSE", False),
        ("0", False),
        ("no", False),
        ("off", False),
        ("f", False),
        ("n", False),
    ],
)
def test_the_canonical_spellings_resolve(monkeypatch, raw, expected):
    monkeypatch.setenv(MEMOIR_REFLECTION_ENABLED_ENV_VAR, raw)
    assert MemoirReflectionConfig().enabled is expected


def test_a_garbage_kill_switch_fails_closed_with_a_warning(monkeypatch, caplog):
    """AC4: garbage ⇒ False (fail closed) + a WARNING naming the value."""
    from daemon.tools.memoir_knob import (
        reset_boot_log_state,
        resolve_memoir_reflection_knob,
    )

    monkeypatch.setenv(MEMOIR_REFLECTION_ENABLED_ENV_VAR, "maybe")
    reset_boot_log_state()
    with caplog.at_level(logging.WARNING, logger=KNOB_LOGGER):
        state = resolve_memoir_reflection_knob()
    assert state.enabled is False
    text = "\n".join(record.getMessage() for record in caplog.records)
    assert "maybe" in text


def test_an_empty_kill_switch_is_the_unset_case(monkeypatch, caplog):
    monkeypatch.setenv(MEMOIR_REFLECTION_ENABLED_ENV_VAR, "")
    config = MemoirReflectionConfig()
    assert config.enabled is True


def test_no_knob_value_ever_raises_a_validation_error(monkeypatch):
    """THE LOAD-BEARING PROPERTY: the daemon never aborts."""
    for raw in ("maybe", "", " ", "1.5", "[]", "{}", "null"):
        monkeypatch.setenv(MEMOIR_REFLECTION_ENABLED_ENV_VAR, raw)
        config = MemoirReflectionConfig()  # MUST NOT RAISE
        assert isinstance(config.enabled, bool)


# ── the budget-bound class ───────────────────────────────────────────────


def test_the_unset_budget_knobs_are_named_in_one_aggregate_warning(caplog):
    """§3-A4: ONE aggregate warning names EVERY unset budget knob.

    The warning is emitted by the KNOB MODULE (the house precedent); the
    model only RECORDS which knobs are unset.
    """
    from daemon.tools.memoir_knob import (
        reset_boot_log_state,
        resolve_memoir_reflection_knob,
    )

    reset_boot_log_state()
    with caplog.at_level(logging.WARNING, logger=KNOB_LOGGER):
        resolve_memoir_reflection_knob()
    text = "\n".join(record.getMessage() for record in caplog.records)
    for name in (
        "ENSEMBLE_MEMOIR_REFLECTION_MAX_SYNTHESES_PER_PASS",
        "ENSEMBLE_MEMOIR_REFLECTION_MAX_CHARS_PER_SYNTHESIS",
        "ENSEMBLE_MEMOIR_REFLECTION_MAX_LLM_CALLS_PER_PASS",
        "ENSEMBLE_MEMOIR_REFLECTION_TRIGGER_SUM",
        "ENSEMBLE_MEMOIR_REFLECTION_NIGHTLY_CAP",
    ):
        assert name in text


@pytest.mark.parametrize(
    "name,field",
    [
        (
            "ENSEMBLE_MEMOIR_REFLECTION_MAX_SYNTHESES_PER_PASS",
            "max_syntheses_per_pass",
        ),
        (
            "ENSEMBLE_MEMOIR_REFLECTION_MAX_CHARS_PER_SYNTHESIS",
            "max_chars_per_synthesis",
        ),
        (
            "ENSEMBLE_MEMOIR_REFLECTION_MAX_LLM_CALLS_PER_PASS",
            "max_llm_calls_per_pass",
        ),
        ("ENSEMBLE_MEMOIR_REFLECTION_TRIGGER_SUM", "trigger_sum"),
        ("ENSEMBLE_MEMOIR_REFLECTION_NIGHTLY_CAP", "nightly_cap"),
    ],
)
def test_a_garbage_budget_knob_clamps_to_the_default(monkeypatch, caplog, name, field):
    """AC4: garbage ⇒ clamp to the default + a WARNING. NEVER zero."""
    default = MemoirReflectionConfig.model_fields[field].default
    monkeypatch.setenv(name, "not-a-number")
    with caplog.at_level(logging.WARNING, logger="daemon.config"):
        config = MemoirReflectionConfig()
    assert getattr(config, field) == default
    assert getattr(config, field) != 0
    text = "\n".join(record.getMessage() for record in caplog.records)
    assert name in text


def test_a_zero_budget_knob_is_clamped_never_zero(monkeypatch, caplog):
    """A zero cap would masquerade as a kill-switch — it is forbidden."""
    monkeypatch.setenv("ENSEMBLE_MEMOIR_REFLECTION_MAX_SYNTHESES_PER_PASS", "0")
    with caplog.at_level(logging.WARNING, logger="daemon.config"):
        config = MemoirReflectionConfig()
    assert config.max_syntheses_per_pass == 1
    assert config.max_syntheses_per_pass != 0


def test_a_valid_budget_knob_is_honoured(monkeypatch):
    monkeypatch.setenv("ENSEMBLE_MEMOIR_REFLECTION_MAX_SYNTHESES_PER_PASS", "3")
    assert MemoirReflectionConfig().max_syntheses_per_pass == 3


def test_no_budget_knob_ever_raises_a_validation_error(monkeypatch):
    for raw in ("not-a-number", "", " ", "2.5", "-1", "1e999"):
        monkeypatch.setenv("ENSEMBLE_MEMOIR_REFLECTION_MAX_SYNTHESES_PER_PASS", raw)
        config = MemoirReflectionConfig()  # MUST NOT RAISE
        assert config.max_syntheses_per_pass >= 1


# ── the cadence-tunable class ────────────────────────────────────────────


def test_the_unset_cadence_knobs_are_silent(caplog):
    """§3-A4: a cadence knob is TUNING, not governance — it is silent."""
    with caplog.at_level(logging.WARNING, logger="daemon.config"):
        MemoirReflectionConfig()
    text = "\n".join(record.getMessage() for record in caplog.records)
    assert "ENSEMBLE_MEMOIR_REFLECTION_TICK_INTERVAL_S" not in text
    assert "ENSEMBLE_MEMOIR_REFLECTION_DEBOUNCE_BASE_S" not in text


def test_a_garbage_cadence_knob_falls_back_to_the_default(monkeypatch, caplog):
    monkeypatch.setenv("ENSEMBLE_MEMOIR_REFLECTION_TICK_INTERVAL_S", "soon")
    with caplog.at_level(logging.WARNING, logger="daemon.config"):
        config = MemoirReflectionConfig()
    assert config.tick_interval_s == 30
    text = "\n".join(record.getMessage() for record in caplog.records)
    assert "ENSEMBLE_MEMOIR_REFLECTION_TICK_INTERVAL_S" in text


def test_no_cadence_knob_ever_raises_a_validation_error(monkeypatch):
    for raw in ("soon", "", " ", "2.5", "-1"):
        monkeypatch.setenv("ENSEMBLE_MEMOIR_REFLECTION_TICK_INTERVAL_S", raw)
        config = MemoirReflectionConfig()  # MUST NOT RAISE
        assert config.tick_interval_s >= 1


# ── the shared classifier itself ─────────────────────────────────────────


def test_the_classifier_never_raises():
    """``_classify_numeric`` is the ONE shared truth-table reader."""
    for raw in (None, "", "x", [], {}, object(), 1.5, True, -1, 0):
        value = _classify_numeric(
            raw,
            default=5,
            numeric_class=NUMERIC_CLASS_BUDGET,
            name="TEST_KNOB",
            minimum=1,
        )
        assert value >= 1


def test_the_classifier_keeps_an_integer_field_an_integer():
    value = _classify_numeric(
        "3", default=5, numeric_class=NUMERIC_CLASS_BUDGET, name="TEST", minimum=1
    )
    assert value == 3
    assert isinstance(value, int)


def test_the_classifier_treats_a_fractional_value_as_garbage():
    value = _classify_numeric(
        "2.5", default=5, numeric_class=NUMERIC_CLASS_BUDGET, name="TEST", minimum=1
    )
    assert value == 5


def test_the_classifier_clamps_out_of_range_values():
    assert (
        _classify_numeric(
            99, default=5, numeric_class=NUMERIC_CLASS_CADENCE, name="T", maximum=10
        )
        == 10
    )
    assert (
        _classify_numeric(
            -5, default=5, numeric_class=NUMERIC_CLASS_CADENCE, name="T", minimum=1
        )
        == 1
    )


def test_the_three_classes_are_distinct_constants():
    assert NUMERIC_CLASS_ENABLED == "enabled"
    assert NUMERIC_CLASS_BUDGET == "budget"
    assert NUMERIC_CLASS_CADENCE == "cadence"


# ── the boot never aborts ────────────────────────────────────────────────


def test_the_full_config_builds_with_every_knob_garbage(monkeypatch):
    """The whole daemon config builds with EVERY knob set to garbage."""
    from daemon.config import Config

    for name in _ALL_KNOBS:
        monkeypatch.setenv(name, "garbage-value")
    config = Config()  # MUST NOT RAISE
    assert config.memoir_reflection.enabled is False
    assert config.memoir_reflection.max_syntheses_per_pass == 5
    assert config.memoir_reflection.tick_interval_s == 30
